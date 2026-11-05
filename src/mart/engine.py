"""Single-fact snapshot loading with workspace-owned transactional state."""
import csv
import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

EXPORT_SEMANTICS_VERSION = 2


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _ident(value):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ValueError('Invalid model identifier')
    return '"' + value.replace('"', '""') + '"'


def _model(model):
    d = asdict(model) if hasattr(model, '__dataclass_fields__') else dict(model)
    # Normalize defaults for backward compatibility with existing saved databases
    if d.get('source_delimiter') == ',':
        d.pop('source_delimiter', None)
    if d.get('source_encoding') == 'utf-8-sig':
        d.pop('source_encoding', None)
    return _json(d)


def _normalize_model_dict(d: dict) -> dict:
    """Normalize model dictionary for semantic schema comparison across versions."""
    res = dict(d)
    for k in ('source_delimiter', 'source_encoding'):
        res.pop(k, None)
    return res


def validate_mart_model(model):
    """Public validator for mart model integrity, returning the set of required column names."""
    from .model import validate_column_roles
    validate_column_roles(model)
    _ident(model.fact_table)
    if not model.row_key_columns or not model.dimensions or not model.measures:
        raise ValueError('Dimension, measure and business row key are required')
    names = [d.name for d in model.dimensions]
    measures = [m.name for m in model.measures]
    if len({n.casefold() for n in names}) != len(names) or len({n.casefold() for n in measures}) != len(measures):
        raise ValueError('Duplicate model name')
    required = {model.period_column, *model.partition_columns, *model.row_key_columns, *model.excluded_columns}
    for dim in model.dimensions:
        _ident(dim.name)
        if not dim.key_columns:
            raise ValueError('Dimensions require keys')
        if len(set(dim.key_columns + dim.attributes)) != len(dim.key_columns + dim.attributes) or 'sk' in dim.key_columns + dim.attributes:
            raise ValueError('Duplicate or reserved dimension column')
        required.update(dim.key_columns)
        required.update(dim.attributes)
        if dim.authority_column:
            required.add(dim.authority_column)
        for column in (*dim.key_columns, *dim.attributes):
            _ident(column)
    for measure in model.measures:
        _ident(measure.name)
        if measure.aggregation != 'sum' or measure.kind not in ('value', 'cost', 'quantity') or not 1 <= measure.precision <= 38 or not 0 <= measure.scale <= measure.precision:
            raise ValueError('Unsupported measure definition')
        required.add(measure.column)
    for column in required:
        _ident(column)
    if len({c.casefold() for c in required}) != len(required):
        raise ValueError('Column names differ only by case')
    stage_reserved = {'row_key', 'payload'} | {('_nk_' + d.name).casefold() for d in model.dimensions} | {('_attr_' + d.name).casefold() for d in model.dimensions}
    if any(c.casefold() in stage_reserved for c in required):
        raise ValueError('Source column conflicts with an internal staging column')
    reserved = {'scope', 'row_key', 'payload'} | {('dim_' + d.name + '_sk').casefold() for d in model.dimensions}
    if any(m.name.casefold() in reserved for m in model.measures):
        raise ValueError('Reserved fact column name')
    return required


def _period_kind(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?', value):
        raise ValueError('Period must be an ISO month or date')
    try:
        date.fromisoformat(value + '-01' if len(value) == 7 else value)
    except ValueError as exc:
        raise ValueError('Invalid period date') from exc
    return len(value)


def _period_granularity(db, period_column):
    try:
        kinds = {_period_kind(json.loads(scope)[period_column]) for (scope,) in db.execute('SELECT scope FROM mart_scopes').fetchall()}
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('Stored period is missing or invalid') from exc
    if len(kinds) > 1:
        raise ValueError('Stored period granularity is inconsistent')
    return next(iter(kinds), None)


def _validate_period_bounds(db, period_column, start, end):
    kind = _period_granularity(db, period_column)
    for bound in (start, end):
        if bound is not None and _period_kind(bound) != kind:
            raise ValueError('Period bound must match stored period granularity')
    if start is not None and end is not None and start > end:
        raise ValueError('Invalid period range')


class MartEngine:
    def __init__(self, workspace, preflight=None):
        self.workspace = workspace
        self.preflight = preflight

    def _capacity(self, operation, size, **context):
        # Legacy three-argument callbacks remain supported for existing callers.
        if getattr(self.preflight, 'accepts_context', False):
            return self.preflight(operation, self.workspace, size, **context)
        return self.preflight(operation, self.workspace, size)

    def _remaining(self, path):
        check = getattr(self.preflight, 'check_remaining', None)
        if check:
            check(path)

    def _copy_checked(self, inp, out, destination):
        while chunk := inp.read(1024 * 1024):
            self._remaining(destination)
            out.write(chunk)

    def _configure_resources(self, db):
        if hasattr(self.preflight, 'memory_limit'):
            temp = self.workspace.root / 'staging' / 'duckdb-temp'
            temp.mkdir(parents=True, exist_ok=True)
            db.execute('SET memory_limit = ?', [self.preflight.memory_limit])
            db.execute('SET temp_directory = ?', [str(temp)])
            db.execute('SET max_temp_directory_size = ?', [self.preflight.temp_limit])

    def _connect(self, create=False):
        import duckdb
        marker = self.workspace.validate()
        exists = self.workspace.database.exists()
        if not exists and not create:
            raise FileNotFoundError('Mart database does not exist')
        db = duckdb.connect(str(self.workspace.database), read_only=not create)
        try:
            self._configure_resources(db)
        except Exception:
            db.close()
            raise
        if exists:
            try:
                identity = db.execute("SELECT value FROM mart_meta WHERE key='workspace_id'").fetchone()
            except duckdb.Error:
                db.close()
                raise ValueError('Database is not a mart workspace') from None
            if not identity or identity[0] != marker['id']:
                db.close()
                raise ValueError('Workspace identity mismatch')
        else:
            db.execute('BEGIN')
            db.execute('CREATE TABLE mart_meta(key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL)')
            db.execute('INSERT INTO mart_meta VALUES (?, ?), (?, ?)', ['workspace_id', marker['id'], 'revision', '0'])
            db.execute('CREATE TABLE mart_dimensions(dimension VARCHAR, natural_key VARCHAR, sk BIGINT, attributes VARCHAR, authority VARCHAR, PRIMARY KEY(dimension,natural_key), UNIQUE(dimension,sk))')
            db.execute('CREATE TABLE mart_scopes(scope VARCHAR PRIMARY KEY, fingerprint VARCHAR, row_count BIGINT, controls VARCHAR, contributors VARCHAR)')
            db.execute('CREATE TABLE mart_history(revision BIGINT PRIMARY KEY, scope VARCHAR, fingerprint VARCHAR, row_count BIGINT, controls VARCHAR, input_hashes VARCHAR)')
            db.execute('CREATE TABLE mart_inputs(path VARCHAR PRIMARY KEY)')
            db.execute('CREATE TABLE mart_publications(path VARCHAR PRIMARY KEY, sha256 VARCHAR)')
            db.execute('CREATE TABLE mart_audit_rows(revision BIGINT, scope VARCHAR, row_key VARCHAR, payload VARCHAR)')
            db.execute('CREATE TABLE mart_audit_batches(revision BIGINT PRIMARY KEY, scope VARCHAR, row_count BIGINT, sha256 VARCHAR, controls VARCHAR)')
            db.execute('CREATE TABLE mart_audit_dimensions(revision BIGINT, dimension VARCHAR, natural_key VARCHAR, sk BIGINT, attributes VARCHAR, authority VARCHAR)')
            db.execute('COMMIT')
        return db

    def _validate_model(self, model):
        return validate_mart_model(model)

    def load_snapshot(self, model, files, scope, expected_files, complete=False, allow_empty=False):
        """Load a complete declared scope from verified, immutable local copies."""
        if self.preflight is None:
            raise ValueError('Verified capacity preflight is required')
        if not complete:
            raise ValueError('Explicit completion declaration required')
        paths = [Path(path) for path in files]
        if len({p.name for p in paths}) != len(paths) or sorted(p.name for p in paths) != sorted(expected_files):
            raise ValueError('Input file set differs from completion declaration')
        scope_columns = (model.period_column,) + tuple(model.partition_columns)
        if set(scope) != set(scope_columns) or any(scope[k] is None or str(scope[k]) == '' for k in scope_columns):
            raise ValueError('Scope must specify period and every partition')
        period_kind = _period_kind(scope[model.period_column])
        required = self._validate_model(model)
        model_text = _model(model)
        scope_text = _json(scope)
        with self.workspace.lock():
            size = sum(p.stat().st_size for p in paths)
            decision = self._capacity('load', size, files=tuple(paths), model=model, scope=dict(scope))
            if not isinstance(decision, dict) or decision.get('allowed') is not True:
                raise ValueError('Capacity preflight did not permit writing: ' + str(decision.get('reason', 'unknown') if isinstance(decision, dict) else 'unknown'))
            staging = self.workspace.root / 'staging'
            staging.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(dir=staging, prefix='load-') as tmp:
                copies = []
                hashes = []
                for index, path in enumerate(paths):
                    before = _hash_file(path)
                    if decision.get('input_hashes') is not None and before != decision['input_hashes'][index]:
                        raise ValueError('Input changed after capacity inspection')
                    copy = Path(tmp) / (str(index) + '.csv')
                    digest = hashlib.sha256()
                    with path.open('rb') as inp, copy.open('xb') as out:
                        while chunk := inp.read(1024 * 1024):
                            self._remaining(copy)
                            out.write(chunk)
                            digest.update(chunk)
                    if before != digest.hexdigest() or before != _hash_file(path):
                        raise ValueError('Input changed during copy')
                    copies.append(copy)
                    hashes.append(before)
                fingerprint = hashlib.sha256(_json([model_text, sorted(hashes)]).encode()).hexdigest()
                archive = self.workspace.root / 'inputs'
                archive.mkdir(exist_ok=True)
                for copy, digest in zip(copies, hashes):
                    retained = archive / (digest + '.csv')
                    if retained.exists():
                        if _hash_file(retained) != digest:
                            raise ValueError('Retained input integrity failure')
                    else:
                        os.replace(copy, retained)
                copies = [archive / (digest + '.csv') for digest in hashes]
                db = self._connect(create=True)
                try:
                    db.execute('BEGIN')
                    saved = db.execute("SELECT value FROM mart_meta WHERE key='model'").fetchone()
                    if saved:
                        saved_dict = _normalize_model_dict(json.loads(saved[0]))
                        curr_dict = _normalize_model_dict(json.loads(model_text))
                        if saved_dict != curr_dict:
                            raise ValueError('Model changes require a new workspace')
                    existing_kind = _period_granularity(db, model.period_column)
                    if existing_kind is not None and period_kind != existing_kind:
                        raise ValueError('Period granularity must remain consistent')
                    previous = db.execute('SELECT fingerprint,contributors FROM mart_scopes WHERE scope=?', [scope_text]).fetchone()
                    contributors = sorted(p.name for p in paths)
                    if previous:
                        old_contributors = json.loads(previous[1])
                        if (len(old_contributors) > 1 or len(contributors) > 1) and old_contributors != contributors:
                            raise ValueError('Existing multi-file contribution set changed')
                    if previous and previous[0] == fingerprint:
                        revision = int(db.execute("SELECT value FROM mart_meta WHERE key='revision'").fetchone()[0])
                        for source in paths:
                            db.execute('INSERT INTO mart_inputs VALUES (?) ON CONFLICT DO NOTHING', [str(source.resolve())])
                        if any(_hash_file(path) != digest for path, digest in zip(paths, hashes)) or any(_hash_file(path) != digest for path, digest in zip(copies, hashes)):
                            raise ValueError('Input changed before commit')
                        self._remaining(self.workspace.root)
                        db.execute('COMMIT')
                        return {'revision': revision, 'changed': False}
                    if not saved:
                        self._create_model(db, model, model_text)
                    columns = sorted(required)
                    fields = ','.join(_ident(c) + ' VARCHAR' for c in columns)
                    extra = ','.join(_ident('_nk_' + d.name) + ' VARCHAR,' + _ident('_attr_' + d.name) + ' VARCHAR' for d in model.dimensions)
                    db.execute('CREATE TEMP TABLE stage(row_key VARCHAR PRIMARY KEY,payload VARCHAR,' + fields + ',' + extra + ')')
                    batch = []
                    count = 0
                    for copy in copies:
                        with copy.open(encoding=getattr(model, 'source_encoding', 'utf-8-sig') or 'utf-8-sig', newline='') as source:
                            reader = csv.DictReader(source, delimiter=getattr(model, 'source_delimiter', ',') or ',', strict=True)
                            if reader.fieldnames is None or len(set(reader.fieldnames)) != len(reader.fieldnames) or set(reader.fieldnames) != required:
                                raise ValueError('Input schema differs from declared roles')
                            for row in reader:
                                self._validate_row(row, model, scope, scope_columns)
                                key = _json([row[k] for k in model.row_key_columns])
                                extras = []
                                for dim in model.dimensions:
                                    extras.extend((_json([row[k] for k in dim.key_columns]), _json({k: row[k] for k in dim.attributes})))
                                payload = {k: row[k] for k in set(scope_columns) | set(model.row_key_columns)}
                                batch.append((key, _json(payload), *(row[c] for c in columns), *extras))
                                count += 1
                                if len(batch) >= 1000:
                                    self._remaining(self.workspace.root)
                                    self._insert_stage(db, batch)
                                    batch.clear()
                    if batch:
                        self._insert_stage(db, batch)
                    if count == 0 and not allow_empty:
                        raise ValueError('Empty snapshot requires explicit declaration')
                    revision = int(db.execute("SELECT value FROM mart_meta WHERE key='revision'").fetchone()[0]) + 1
                    self._remaining(self.workspace.root)
                    fact = _ident('fact_' + model.fact_table)
                    self._archive_scope(db, model, scope_text, revision)
                    self._merge_dimensions(db, model, revision)
                    db.execute('DELETE FROM ' + fact + ' WHERE scope=?', [scope_text])
                    joins = []
                    for index, dim in enumerate(model.dimensions):
                        joins.append(' JOIN mart_dimensions d' + str(index) + ' ON d' + str(index) + '.dimension=? AND d' + str(index) + '.natural_key=s.' + _ident('_nk_' + dim.name))
                    keys = ['d' + str(i) + '.sk' for i in range(len(model.dimensions))]
                    values = ['CAST(NULLIF(s.' + _ident(m.column) + ", '') AS DECIMAL(" + str(m.precision) + ',' + str(m.scale) + '))' for m in model.measures]
                    db.execute('INSERT INTO ' + fact + ' SELECT ?,s.row_key,s.payload,' + ','.join(keys + values) + ' FROM stage s' + ''.join(joins), [scope_text, *(d.name for d in model.dimensions)])
                    controls = self._scope_controls(db, model, scope_text)
                    expected = self._stage_controls(db, model)
                    if controls != expected:
                        raise ValueError('Source and loaded controls differ')
                    db.execute('DELETE FROM mart_scopes WHERE scope=?', [scope_text])
                    db.execute('INSERT INTO mart_scopes VALUES (?,?,?,?,?)', [scope_text, fingerprint, count, _json(controls), _json(contributors)])
                    db.execute('INSERT INTO mart_history VALUES (?,?,?,?,?,?)', [revision, scope_text, fingerprint, count, _json(controls), _json(sorted(hashes))])
                    for source in paths:
                        db.execute('INSERT INTO mart_inputs VALUES (?) ON CONFLICT DO NOTHING', [str(source.resolve())])
                    db.execute("UPDATE mart_meta SET value=? WHERE key='revision'", [str(revision)])
                    if any(_hash_file(path) != digest for path, digest in zip(paths, hashes)) or any(_hash_file(path) != digest for path, digest in zip(copies, hashes)):
                        raise ValueError('Input changed before commit')
                    self._remaining(self.workspace.root)
                    db.execute('COMMIT')
                    return {'revision': revision, 'changed': True, 'rows': count, 'controls': controls}
                except Exception:
                    db.execute('ROLLBACK')
                    raise
                finally:
                    db.close()

    @staticmethod
    def _insert_stage(db, batch):
        import duckdb
        try:
            import pyarrow as pa
            columns = [pa.array(values, type=pa.string()) for values in zip(*batch)]
            table = pa.Table.from_arrays(columns, names=['c' + str(i) for i in range(len(columns))])
            db.register('_mart_stage_batch', table)
            try:
                db.execute('INSERT INTO stage SELECT * FROM _mart_stage_batch')
            finally:
                db.unregister('_mart_stage_batch')
        except duckdb.ConstraintException as exc:
            raise ValueError('Duplicate business row key or invalid staged row') from exc

    @staticmethod
    def _validate_row(row, model, scope, scope_columns):
        if None in row or any(v is None for v in row.values()):
            raise ValueError('Malformed CSV row')
        if any(row[k] != str(scope[k]) for k in scope_columns):
            raise ValueError('Input row outside declared scope')
        for key in set(model.row_key_columns).union(*(set(d.key_columns) for d in model.dimensions)):
            if not row[key].strip():
                raise ValueError('Required key missing')
        for dim in model.dimensions:
            if dim.authority_column:
                value = row[dim.authority_column]
                if not re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?', value):
                    raise ValueError('Attribute authority must use ISO month or date')
                try:
                    date.fromisoformat(value + '-01' if len(value) == 7 else value)
                except ValueError as exc:
                    raise ValueError('Invalid authority date') from exc
        for measure in model.measures:
            value = row[measure.column]
            if value == '':
                continue
            try:
                number = Decimal(value)
                if not number.is_finite() or number.as_tuple().exponent < -measure.scale or number.copy_abs() >= Decimal(10) ** (measure.precision - measure.scale):
                    raise ValueError('Decimal exceeds declared precision or scale')
            except InvalidOperation as exc:
                raise ValueError('Invalid decimal') from exc

    @staticmethod
    def _create_model(db, model, model_text):
        db.execute("INSERT INTO mart_meta VALUES ('model', ?)", [model_text])
        for dim in model.dimensions:
            cols = ','.join(_ident(c) + ' VARCHAR' for c in (*dim.key_columns, *dim.attributes))
            db.execute('CREATE TABLE ' + _ident('dim_' + dim.name) + ' (sk BIGINT PRIMARY KEY,' + cols + ')')
        fields = [_ident('dim_' + d.name + '_sk') + ' BIGINT NOT NULL REFERENCES ' + _ident('dim_' + d.name) + '(sk)' for d in model.dimensions]
        fields += [_ident(m.name) + f' DECIMAL({m.precision},{m.scale})' for m in model.measures]
        db.execute('CREATE TABLE ' + _ident('fact_' + model.fact_table) + ' (scope VARCHAR NOT NULL,row_key VARCHAR NOT NULL,payload VARCHAR NOT NULL,' + ','.join(fields) + ', PRIMARY KEY(scope,row_key))')

    @staticmethod
    def _merge_dimensions(db, model, revision):
        for dim in model.dimensions:
            nk = _ident('_nk_' + dim.name)
            attr = _ident('_attr_' + dim.name)
            authority = _ident(dim.authority_column) if dim.authority_column else "''"
            if dim.authority_column:
                lengths = db.execute('SELECT DISTINCT LENGTH(' + authority + ') FROM stage').fetchall()
                old_lengths = db.execute('SELECT DISTINCT LENGTH(authority) FROM mart_dimensions WHERE dimension=?', [dim.name]).fetchall()
                if len({v[0] for v in lengths + old_lengths}) > 1:
                    raise ValueError('Authority granularity must remain consistent')
            conflict = db.execute('SELECT 1 FROM stage GROUP BY ' + nk + ',' + authority + ' HAVING COUNT(DISTINCT ' + attr + ')>1 LIMIT 1').fetchone()
            if conflict:
                raise ValueError('Conflicting attributes at same authority')
            db.execute('CREATE OR REPLACE TEMP TABLE dim_candidates AS SELECT DISTINCT ' + nk + ' AS nk,' + attr + ' AS attrs,' + authority + ' AS authority FROM stage QUALIFY ROW_NUMBER() OVER (PARTITION BY ' + nk + ' ORDER BY ' + authority + ' DESC)=1')
            conflict = db.execute('SELECT 1 FROM dim_candidates c JOIN mart_dimensions d ON d.dimension=? AND d.natural_key=c.nk WHERE d.authority=c.authority AND d.attributes<>c.attrs LIMIT 1', [dim.name]).fetchone()
            if conflict:
                raise ValueError('Conflicting attributes at same authority')
            db.execute('INSERT INTO mart_audit_dimensions SELECT ?,d.* FROM mart_dimensions d JOIN dim_candidates c ON d.dimension=? AND d.natural_key=c.nk WHERE c.authority>d.authority', [revision, dim.name])
            db.execute('UPDATE mart_dimensions SET attributes=c.attrs,authority=c.authority FROM dim_candidates c WHERE dimension=? AND natural_key=c.nk AND c.authority>mart_dimensions.authority', [dim.name])
            max_sk = db.execute('SELECT COALESCE(MAX(sk),0) FROM mart_dimensions WHERE dimension=?', [dim.name]).fetchone()[0]
            db.execute('INSERT INTO mart_dimensions SELECT ?,c.nk,?+ROW_NUMBER() OVER (ORDER BY c.nk),c.attrs,c.authority FROM dim_candidates c LEFT JOIN mart_dimensions d ON d.dimension=? AND d.natural_key=c.nk WHERE d.sk IS NULL', [dim.name, max_sk, dim.name])
            table = _ident('dim_' + dim.name)
            # Update only touched members and insert new ones without renumbering.
            if dim.attributes:
                assignments = ','.join(_ident(k) + "=json_extract_string(d.attributes, '$." + json.dumps(k, ensure_ascii=False).replace("'", "''") + "')" for k in dim.attributes)
                db.execute('UPDATE ' + table + ' SET ' + assignments + ' FROM mart_dimensions d JOIN dim_candidates c ON d.natural_key=c.nk WHERE d.dimension=? AND ' + table + '.sk=d.sk', [dim.name])
            values = ["json_extract_string(d.natural_key, '$[" + str(i) + "]')" for i in range(len(dim.key_columns))]
            values += ["json_extract_string(d.attributes, '$." + json.dumps(k, ensure_ascii=False).replace("'", "''") + "')" for k in dim.attributes]
            db.execute('INSERT INTO ' + table + ' SELECT d.sk,' + ','.join(values) + ' FROM mart_dimensions d JOIN dim_candidates c ON d.natural_key=c.nk LEFT JOIN ' + table + ' t ON t.sk=d.sk WHERE d.dimension=? AND t.sk IS NULL', [dim.name])

    @staticmethod
    def _stage_controls(db, model):
        expressions = ['COUNT(*)'] + ['SUM(CAST(NULLIF(' + _ident(m.column) + ", '') AS DECIMAL(" + str(m.precision) + ',' + str(m.scale) + ')))' for m in model.measures]
        result = db.execute('SELECT ' + ','.join(expressions) + ' FROM stage').fetchone()
        return {'rows': result[0], 'sums': {m.name: str(value) if value is not None else None for m, value in zip(model.measures, result[1:])}}

    def _archive_scope(self, db, model, scope, revision):
        """Preserve and verify typed pre-change facts before replacing the scope."""
        db.execute('CREATE TABLE IF NOT EXISTS mart_audit_batches(revision BIGINT PRIMARY KEY, scope VARCHAR, row_count BIGINT, sha256 VARCHAR, controls VARCHAR)')
        fact = _ident('fact_' + model.fact_table)
        def literal(value):
            return "'" + value.replace("'", "''") + "'"
        joins = []
        dimensions = []
        for i, dim in enumerate(model.dimensions):
            alias = 'audit_dim_' + str(i)
            joins.append(' JOIN mart_dimensions ' + alias + ' ON ' + alias + '.dimension=' + literal(dim.name) + ' AND ' + alias + '.sk=f.' + _ident('dim_' + dim.name + '_sk'))
            dimensions.extend([literal(dim.name), "json_object('sk'," + alias + '.sk,\'natural_key\',json(' + alias + '.natural_key))'])
        measures = []
        for measure in model.measures:
            measures.extend([literal(measure.name), 'CAST(f.' + _ident(measure.name) + ' AS VARCHAR)'])
        payload = "json_object('version',2,'row',json(f.payload),'dimensions',json_object(" + ','.join(dimensions) + "),'measures',json_object(" + ','.join(measures) + '))'
        source = ' FROM ' + fact + ' f' + ''.join(joins) + ' WHERE f.scope=?'
        expected = self._scope_controls(db, model, scope)

        def digest_query(sql, args, *, verify_controls=False):
            cursor = db.execute(sql, args)
            digest = hashlib.sha256()
            count = 0
            totals = {m.name: None for m in model.measures}
            with localcontext() as context:
                context.prec = 100
                while batch := cursor.fetchmany(1000):
                    self._remaining(self.workspace.root)
                    for key, raw in batch:
                        value = json.loads(raw)
                        encoded = _json([key, value]).encode('utf-8')
                        digest.update(len(encoded).to_bytes(8, 'big'))
                        digest.update(encoded)
                        count += 1
                        if verify_controls:
                            for name, number in value['measures'].items():
                                if number is not None:
                                    totals[name] = (totals[name] or Decimal(0)) + Decimal(number)
            return count, digest.hexdigest(), totals

        source_sql = 'SELECT f.row_key,' + payload + source + ' ORDER BY f.row_key'
        source_count, source_hash, _ = digest_query(source_sql, [scope])
        if source_count != expected['rows']:
            raise ValueError('Audit source dimension or row count mismatch')
        db.execute('INSERT INTO mart_audit_rows SELECT ?,f.scope,f.row_key,' + payload + source, [revision, scope])
        count, copied_hash, totals = digest_query('SELECT row_key,payload FROM mart_audit_rows WHERE revision=? AND scope=? ORDER BY row_key', [revision, scope], verify_controls=True)
        if count != source_count or copied_hash != source_hash:
            raise ValueError('Audit copy integrity failure')
        for name, actual in expected['sums'].items():
            if (Decimal(actual) if actual is not None else None) != totals[name]:
                raise ValueError('Audit copy controls differ')
        db.execute('INSERT INTO mart_audit_batches VALUES (?,?,?,?,?)', [revision, scope, count, copied_hash, _json(expected)])

    @staticmethod
    def _scope_controls(db, model, scope_text):
        expressions = ['COUNT(*)'] + ['SUM(' + _ident(m.name) + ')' for m in model.measures]
        result = db.execute('SELECT ' + ','.join(expressions) + ' FROM ' + _ident('fact_' + model.fact_table) + ' WHERE scope=?', [scope_text]).fetchone()
        return {'rows': result[0], 'sums': {m.name: str(value) if value is not None else None for m, value in zip(model.measures, result[1:])}}

    def catalog(self):
        with self.workspace.lock():
            db = self._connect()
            try:
                model = db.execute("SELECT value FROM mart_meta WHERE key='model'").fetchone()
                if model:
                    _period_granularity(db, json.loads(model[0])['period_column'])
                revision = int(db.execute("SELECT value FROM mart_meta WHERE key='revision'").fetchone()[0])
                scopes = db.execute('SELECT scope,row_count FROM mart_scopes ORDER BY scope').fetchall()
                columns = db.execute("SELECT table_name,column_name,data_type FROM information_schema.columns WHERE table_schema='main' ORDER BY table_name,ordinal_position").fetchall()
                constraints = db.execute("SELECT table_name,constraint_type,constraint_column_names,referenced_table,referenced_column_names FROM duckdb_constraints() WHERE schema_name='main' AND constraint_type IN ('PRIMARY KEY','FOREIGN KEY')").fetchall()
                return {'revision': revision, 'model': json.loads(model[0]) if model else None, 'scopes': [{'scope': json.loads(s), 'rows': n} for s, n in scopes], 'columns': columns, 'constraints': constraints}
            finally:
                db.close()

    @staticmethod
    def _rows_query(db, model, scope=None, limit=None, offset=0):
        from .model import model_from_dict, validate_column_roles
        validate_column_roles(model_from_dict(model))
        _period_granularity(db, model['period_column'])
        fields = ['f.payload']
        fact_columns = {model['period_column'], *model['partition_columns'], *model['row_key_columns']}
        joins = []
        output = []
        for index, dim in enumerate(model['dimensions']):
            alias = 'd' + str(index)
            joins.append(' JOIN ' + _ident('dim_' + dim['name']) + ' ' + alias + ' ON ' + alias + '.sk=f.' + _ident('dim_' + dim['name'] + '_sk'))
        assigned = set(fact_columns)
        for role in ('key_columns', 'attributes'):
            for index, dim in enumerate(model['dimensions']):
                for column in dim[role]:
                    if column not in assigned:
                        fields.append('d' + str(index) + '.' + _ident(column))
                        output.append(column)
                        assigned.add(column)
        for measure in model['measures']:
            fields.append('f.' + _ident(measure['name']))
            output.append(measure['column'])
        sql = 'SELECT ' + ','.join(fields) + ' FROM ' + _ident('fact_' + model['fact_table']) + ' f' + ''.join(joins)
        args = []
        if scope is not None:
            sql += ' WHERE f.scope=?'
            args.append(_json(scope))
        sql += ' ORDER BY f.scope,f.row_key'
        if limit is not None:
            sql += ' LIMIT ? OFFSET ?'
            args.extend((limit, offset))
        return db.execute(sql, args), output

    @staticmethod
    def _row_dict(record, columns):
        row = json.loads(record[0])
        fact_columns = set(row)
        for column, value in zip(columns, record[1:]):
            if column not in fact_columns:
                row[column] = str(value) if isinstance(value, Decimal) else value
        return row

    def sample(self, limit=100, offset=0):
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError('Invalid page')
        with self.workspace.lock():
            db = self._connect()
            try:
                saved = db.execute("SELECT value FROM mart_meta WHERE key='model'").fetchone()
                if not saved:
                    raise ValueError('No model has been loaded into this workspace')
                model = json.loads(saved[0])
                revision = int(db.execute("SELECT value FROM mart_meta WHERE key='revision'").fetchone()[0])
                cursor, columns = self._rows_query(db, model, limit=limit, offset=offset)
                return {'revision': revision, 'rows': [self._row_dict(row, columns) for row in cursor.fetchall()], 'offset': offset}
            finally:
                db.close()

    def reconcile(self):
        from .model import model_from_dict
        with self.workspace.lock():
            db = self._connect()
            try:
                saved = db.execute("SELECT value FROM mart_meta WHERE key='model'").fetchone()
                if not saved:
                    raise ValueError('No model has been loaded into this workspace')
                model = model_from_dict(json.loads(saved[0]))
                from .model import validate_column_roles
                validate_column_roles(model)
                _period_granularity(db, model.period_column)
                revision = int(db.execute("SELECT value FROM mart_meta WHERE key='revision'").fetchone()[0])
                scopes = db.execute('SELECT scope,controls FROM mart_scopes ORDER BY scope').fetchall()
                result = []
                for scope, expected in scopes:
                    actual = self._scope_controls(db, model, scope)
                    expected = json.loads(expected)
                    result.append({'scope': json.loads(scope), 'expected': expected, 'actual': actual, 'matches': expected == actual})
                return {'revision': revision, 'scopes': result, 'matches': all(item['matches'] for item in result)}
            finally:
                db.close()

    @staticmethod
    def _export_query(db, model, definition, scope):
        from .model import model_from_dict, validate_column_roles
        validate_column_roles(model_from_dict(model))
        _validate_period_bounds(db, model['period_column'], definition.period_start, definition.period_end)
        expressions = {}
        for column in (model['period_column'], *model['partition_columns'], *model['row_key_columns']):
            path = '$.' + json.dumps(column, ensure_ascii=False)
            expressions[column] = "json_extract_string(f.payload, '" + path.replace("'", "''") + "')"
        joins = []
        for index, dim in enumerate(model['dimensions']):
            alias = 'd' + str(index)
            joins.append(' JOIN ' + _ident('dim_' + dim['name']) + ' ' + alias + ' ON ' + alias + '.sk=f.' + _ident('dim_' + dim['name'] + '_sk'))
        for role in ('key_columns', 'attributes'):
            for index, dim in enumerate(model['dimensions']):
                for column in dim[role]:
                    if column not in expressions:
                        expressions[column] = 'd' + str(index) + '.' + _ident(column)
        for measure in model['measures']:
            expressions[measure['column']] = 'f.' + _ident(measure['name'])
        requested = set(definition.columns) | {c for c, _ in definition.filters} | {model['period_column']}
        if not requested <= expressions.keys():
            raise ValueError('Unknown export field or filter')
        inner = ','.join(expr + ' AS ' + _ident(column) for column, expr in expressions.items())
        sql = ' FROM (SELECT ' + inner + ' FROM ' + _ident('fact_' + model['fact_table']) + ' f' + ''.join(joins)
        args = []
        if scope is not None:
            sql += ' WHERE f.scope=?'
            args.append(_json(scope))
        sql += ') source'
        conditions = []
        for column, value in definition.filters:
            conditions.append(_ident(column) + '=?')
            args.append(value)
        for value, operator in ((definition.period_start, '>='), (definition.period_end, '<=')):
            if value is not None:
                if not re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?', value):
                    raise ValueError('Period bounds must be ISO month or date')
                conditions.append(_ident(model['period_column']) + operator + '?')
                args.append(value)
        if definition.period_start and definition.period_end and (len(definition.period_start) != len(definition.period_end) or definition.period_start > definition.period_end):
            raise ValueError('Invalid period range')
        if conditions:
            sql += ' WHERE ' + ' AND '.join(conditions)
        measure_columns = {m['column'] for m in model['measures']}
        if definition.group_by:
            if definition.format != 'wide' or not set(definition.group_by) <= set(definition.columns) or set(definition.group_by) & measure_columns:
                raise ValueError('Invalid grouping')
            if set(definition.columns) - set(definition.group_by) - measure_columns:
                raise ValueError('Ungrouped dimension column')
            selected = [('CASE WHEN COUNT(' + _ident(c) + ')=COUNT(*) THEN SUM(' + _ident(c) + ') ELSE NULL END') if c in measure_columns else _ident(c) for c in definition.columns]
            sql = 'SELECT ' + ','.join(selected) + sql + ' GROUP BY ' + ','.join(map(_ident, definition.group_by))
        else:
            # Long export also reads each requested cost measure.
            cols = list(definition.columns)
            if definition.format == 'long':
                lookup = {m['name']: m['column'] for m in model['measures']}
                cols += [lookup[n] for n in definition.cost_measures]
            sql = 'SELECT ' + ','.join(map(_ident, cols)) + sql
        return db.execute(sql, args)

    def create_export(self, definition, scope=None):
        """Create an immutable local CSV candidate tied to model and revision."""
        import uuid
        if self.preflight is None:
            raise ValueError('Verified capacity preflight is required')
        if definition.format not in ('wide', 'long'):
            raise ValueError('Unsupported export format')
        with self.workspace.lock():
            db = self._connect()
            try:
                model = json.loads(db.execute("SELECT value FROM mart_meta WHERE key='model'").fetchone()[0])
                from .model import model_from_dict, validate_column_roles
                validate_column_roles(model_from_dict(model))
                _period_granularity(db, model['period_column'])
                revision = int(db.execute("SELECT value FROM mart_meta WHERE key='revision'").fetchone()[0])
                decision = self._capacity('export', self.workspace.database.stat().st_size, definition=definition, scope=scope, model=model, destination=self.workspace.root / 'exports', db=db)
                if not isinstance(decision, dict) or decision.get('allowed') is not True:
                    raise ValueError('Capacity preflight did not permit writing: ' + str(decision.get('reason', 'unknown') if isinstance(decision, dict) else 'unknown'))
                known = {model['period_column'], *model['partition_columns'], *model['row_key_columns']}
                for dim in model['dimensions']:
                    known.update(dim['key_columns'])
                    known.update(dim['attributes'])
                measures = {m['name']: m for m in model['measures']}
                measure_columns = {m['column'] for m in model['measures']}
                if not definition.columns or len(set(definition.columns)) != len(definition.columns) or not set(definition.columns) <= known | measure_columns:
                    raise ValueError('Unknown or duplicate export columns')
                if definition.format == 'long':
                    if set(definition.columns) & measure_columns or not definition.cost_measures or not set(definition.cost_measures) <= set(measures):
                        raise ValueError('Long export requires dimensions and declared cost measures')
                    if any(measures[n]['kind'] != 'cost' for n in definition.cost_measures):
                        raise ValueError('Long export requires measures explicitly classified as cost')
                    if any('qty' in name.lower() or 'quantity' in name.lower() for name in (*definition.columns, *definition.cost_measures, *(measures[n]['column'] for n in definition.cost_measures), *(measures[n]['unit'] for n in definition.cost_measures))):
                        raise ValueError('Long cost export cannot contain quantity')
                candidate = uuid.uuid4().hex
                folder = self.workspace.root / 'exports' / candidate
                folder.mkdir(parents=True, exist_ok=False)
                path = folder / 'data.csv'
                cursor = self._export_query(db, model, definition, scope)
                count = 0
                with path.open('x', encoding='utf-8-sig', newline='') as output:
                    writer = csv.writer(output)
                    writer.writerow([*definition.columns, 'measure', 'value'] if definition.format == 'long' else definition.columns)
                    while rows := cursor.fetchmany(1000):
                        self._remaining(path)
                        for record in rows:
                            if definition.format == 'wide':
                                writer.writerow(record)
                                count += 1
                            else:
                                values = record[len(definition.columns):]
                                for name, value in zip(definition.cost_measures, values):
                                    writer.writerow([*record[:len(definition.columns)], name, value])
                                    count += 1
                manifest = {'candidate': candidate, 'export_semantics_version': EXPORT_SEMANTICS_VERSION, 'workspace_id': self.workspace.validate()['id'], 'revision': revision, 'model_sha256': hashlib.sha256(_json(model).encode()).hexdigest(), 'definition': asdict(definition), 'scope': scope, 'sha256': _hash_file(path), 'rows': count}
                with (folder / 'manifest.json').open('x', encoding='utf-8') as output:
                    json.dump(manifest, output, ensure_ascii=False, sort_keys=True)
                return manifest
            finally:
                db.close()

    def inspect_export(self, candidate):
        if not re.fullmatch(r'[0-9a-f]{32}', candidate):
            raise ValueError('Invalid candidate ID')
        folder = self.workspace.root / 'exports' / candidate
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('candidate') != candidate or _hash_file(folder / 'data.csv') != manifest.get('sha256'):
            raise ValueError('Export candidate integrity failure')
        if manifest.get('workspace_id') != self.workspace.validate()['id']:
            raise ValueError('Export workspace generation changed')
        return manifest

    @staticmethod
    def _validate_export_contract(db, manifest):
        from .model import model_from_dict, validate_column_roles
        version = manifest.get('export_semantics_version')
        if type(version) is not int or version != EXPORT_SEMANTICS_VERSION:
            raise ValueError('Output semantics changed; recreate and approve the candidate')
        model = json.loads(db.execute("SELECT value FROM mart_meta WHERE key='model'").fetchone()[0])
        validate_column_roles(model_from_dict(model))
        if hashlib.sha256(_json(model).encode()).hexdigest() != manifest.get('model_sha256'):
            raise ValueError('Model changed')
        definition = manifest['definition']
        _validate_period_bounds(db, model['period_column'], definition.get('period_start'), definition.get('period_end'))
        return model

    def approve_export(self, candidate, reviewer):
        if not reviewer.strip():
            raise ValueError('Reviewer required')
        reconciliation = self.reconcile()
        if not reconciliation['matches']:
            raise ValueError('Reconciliation failed')
        with self.workspace.lock():
            manifest = self.inspect_export(candidate)
            db = self._connect()
            try:
                self._validate_export_contract(db, manifest)
                revision = int(db.execute("SELECT value FROM mart_meta WHERE key='revision'").fetchone()[0])
                if revision != manifest['revision'] or revision != reconciliation['revision']:
                    raise ValueError('Export revision is stale')
                approval = {'reviewer': reviewer, 'revision': revision, 'manifest_sha256': _hash_file(self.workspace.root / 'exports' / candidate / 'manifest.json'), 'sha256': manifest['sha256']}
                path = self.workspace.root / 'exports' / candidate / 'approval.json'
                with path.open('x', encoding='utf-8') as output:
                    json.dump(approval, output, ensure_ascii=False, sort_keys=True)
                return approval
            finally:
                db.close()

    def with_approved_export(self, candidate, callback):
        """Validate a candidate and invoke a read-only callback under the workspace lock."""
        from .model import model_from_dict, validate_column_roles
        with self.workspace.lock():
            manifest = self.inspect_export(candidate)
            folder = self.workspace.root / 'exports' / candidate
            approval = json.loads((folder / 'approval.json').read_text(encoding='utf-8'))
            if approval.get('sha256') != manifest['sha256'] or approval.get('manifest_sha256') != _hash_file(folder / 'manifest.json'):
                raise ValueError('Approval does not match candidate')
            db = self._connect()
            try:
                self._validate_export_contract(db, manifest)
                revision = int(db.execute("SELECT value FROM mart_meta WHERE key='revision'").fetchone()[0])
                model = json.loads(db.execute("SELECT value FROM mart_meta WHERE key='model'").fetchone()[0])
                validate_column_roles(model_from_dict(model))
                _period_granularity(db, model['period_column'])
                if revision != manifest['revision'] or revision != approval.get('revision'):
                    raise ValueError('Approval is stale')
                if hashlib.sha256(_json(model).encode()).hexdigest() != manifest['model_sha256']:
                    raise ValueError('Model changed')
                return callback(manifest, model)
            finally:
                db.close()

    def publish_sharepoint(self, candidate, publisher, filename):
        """Validate an approved immutable candidate under the workspace lock before any network call."""
        from .model import model_from_dict, validate_column_roles
        with self.workspace.lock():
            marker = self.workspace.validate()
            manifest = self.inspect_export(candidate)
            folder = self.workspace.root / 'exports' / candidate
            approval = json.loads((folder / 'approval.json').read_text(encoding='utf-8'))
            if approval.get('sha256') != manifest['sha256'] or approval.get('manifest_sha256') != _hash_file(folder / 'manifest.json'):
                raise ValueError('Approval does not match candidate')
            db = self._connect()
            try:
                self._validate_export_contract(db, manifest)
                revision = int(db.execute("SELECT value FROM mart_meta WHERE key='revision'").fetchone()[0])
                model = json.loads(db.execute("SELECT value FROM mart_meta WHERE key='model'").fetchone()[0])
                validate_column_roles(model_from_dict(model))
                _period_granularity(db, model['period_column'])
                if revision != manifest['revision'] or revision != approval.get('revision'):
                    raise ValueError('Approval is stale')
                if hashlib.sha256(_json(model).encode()).hexdigest() != manifest['model_sha256']:
                    raise ValueError('Model changed')
                if (folder / 'data.csv').stat().st_size > 250 * 1024 * 1024:
                    from .publication import PublicationResult
                    return PublicationResult('failed', '250 MiB upload limit exceeded; no upload attempted')
                data = (folder / 'data.csv').read_bytes()
                if hashlib.sha256(data).hexdigest() != manifest['sha256']:
                    raise ValueError('Export candidate integrity failure')
                if self.workspace.validate()['id'] != marker['id']:
                    raise ValueError('Workspace generation changed')
                target = publisher.resolve()
                observed = publisher.observe_target(target, filename)
                return publisher.publish(target, filename, data, expected_etag=observed.get('etag'), expected_absent=observed.get('absent', False))
            finally:
                db.close()

    def publish_local(self, candidate, destination):
        from .model import model_from_dict, validate_column_roles
        if self.preflight is None:
            raise ValueError('Verified capacity preflight is required')
        destination = Path(destination).resolve()
        if destination == self.workspace.root or self.workspace.root in destination.parents:
            raise ValueError('Destination must be outside the workspace')
        with self.workspace.lock():
            manifest = self.inspect_export(candidate)
            folder = self.workspace.root / 'exports' / candidate
            approval = json.loads((folder / 'approval.json').read_text(encoding='utf-8'))
            if approval.get('sha256') != manifest['sha256'] or approval.get('manifest_sha256') != _hash_file(folder / 'manifest.json'):
                raise ValueError('Approval does not match candidate')
            if not self.workspace.database.is_file():
                raise FileNotFoundError('Mart database does not exist')
            db = self._connect(create=True)
            try:
                self._validate_export_contract(db, manifest)
                revision = int(db.execute("SELECT value FROM mart_meta WHERE key='revision'").fetchone()[0])
                model = json.loads(db.execute("SELECT value FROM mart_meta WHERE key='model'").fetchone()[0])
                validate_column_roles(model_from_dict(model))
                _period_granularity(db, model['period_column'])
                if revision != manifest['revision'] or revision != approval.get('revision'):
                    raise ValueError('Approval is stale')
                for (source_path,) in db.execute('SELECT path FROM mart_inputs').fetchall():
                    original = Path(source_path)
                    if destination == original or (destination.exists() and original.exists() and os.path.samefile(destination, original)):
                        raise ValueError('Destination is an input file')
                destination_exists = destination.exists()
                if destination_exists:
                    prior = db.execute('SELECT sha256 FROM mart_publications WHERE path=?', [str(destination)]).fetchone()
                    if not prior or _hash_file(destination) != prior[0]:
                        raise ValueError('Existing destination is not the last verified publication')
                decision = self._capacity('publish', (folder / 'data.csv').stat().st_size, destination=destination)
                if not isinstance(decision, dict) or decision.get('allowed') is not True:
                    raise ValueError('Capacity preflight did not permit writing: ' + str(decision.get('reason', 'unknown') if isinstance(decision, dict) else 'unknown'))
                destination.parent.mkdir(parents=True, exist_ok=True)
                fd, name = tempfile.mkstemp(prefix='.mart-', suffix='.tmp', dir=destination.parent)
                try:
                    with os.fdopen(fd, 'wb') as out, (folder / 'data.csv').open('rb') as source:
                        self._copy_checked(source, out, destination)
                        out.flush()
                        os.fsync(out.fileno())
                    if _hash_file(name) != manifest['sha256']:
                        raise ValueError('Published copy failed integrity check')
                    if destination_exists:
                        os.replace(name, destination)
                    else:
                        os.link(name, destination)
                    db.execute('INSERT INTO mart_publications VALUES (?,?) ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256', [str(destination), manifest['sha256']])
                finally:
                    if os.path.exists(name):
                        os.unlink(name)
                return {'destination': str(destination), 'revision': revision, 'sha256': manifest['sha256']}
            finally:
                db.close()

    def backup(self, destination):
        """Checkpoint, close, copy and read-verify an independent backup."""
        if self.preflight is None:
            raise ValueError('Verified capacity preflight is required')
        destination = Path(destination).resolve()
        with self.workspace.lock():
            if destination.exists() and any(destination.iterdir()):
                raise ValueError('Backup destination must be empty')
            decision = self._capacity('backup', self.workspace.database.stat().st_size, destination=destination)
            if not isinstance(decision, dict) or decision.get('allowed') is not True:
                raise ValueError('Capacity preflight did not permit writing: ' + str(decision.get('reason', 'unknown') if isinstance(decision, dict) else 'unknown'))
            db = self._connect(create=True)
            try:
                revision = int(db.execute("SELECT value FROM mart_meta WHERE key='revision'").fetchone()[0])
                db.execute('CHECKPOINT')
            finally:
                db.close()
            decision = self._capacity('backup', self.workspace.database.stat().st_size, destination=destination)
            if not isinstance(decision, dict) or decision.get('allowed') is not True:
                raise ValueError('Capacity preflight after checkpoint did not permit writing: ' + str(decision.get('reason', 'unknown') if isinstance(decision, dict) else 'unknown'))
            destination.mkdir(parents=True, exist_ok=True)
            copy = destination / 'mart.duckdb'
            with self.workspace.database.open('rb') as source, copy.open('xb') as target:
                self._copy_checked(source, target, destination)
                target.flush()
                os.fsync(target.fileno())
            digest = _hash_file(copy)
            import duckdb
            check = duckdb.connect(str(copy), read_only=True)
            try:
                self._configure_resources(check)
                identity = check.execute("SELECT value FROM mart_meta WHERE key='workspace_id'").fetchone()[0]
                copied_revision = int(check.execute("SELECT value FROM mart_meta WHERE key='revision'").fetchone()[0])
                from .model import model_from_dict
                saved_model = check.execute("SELECT value FROM mart_meta WHERE key='model'").fetchone()
                if saved_model:
                    backup_model = model_from_dict(json.loads(saved_model[0]))
                    scopes = check.execute('SELECT scope,controls FROM mart_scopes').fetchall()
                    for saved_scope, expected in scopes:
                        if self._scope_controls(check, backup_model, saved_scope) != json.loads(expected):
                            raise ValueError('Backup reconciliation failed')
            finally:
                check.close()
            if copied_revision != revision or identity != self.workspace.validate()['id']:
                raise ValueError('Backup verification failed')
            manifest = {'revision': revision, 'sha256': digest, 'workspace_id': identity, 'later_data': 'requires review', 'retained_inputs': 'not included; preserve workspace inputs separately'}
            with (destination / 'manifest.json').open('x', encoding='utf-8') as out:
                json.dump(manifest, out, sort_keys=True)
            return manifest

    @classmethod
    def restore_backup(cls, backup, destination, preflight=None):
        """Restore to a new workspace identity; previous approvals are not restored."""

        from .storage import WorkspaceStore
        backup = Path(backup).resolve()
        manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))
        source = backup / 'mart.duckdb'
        if _hash_file(source) != manifest['sha256']:
            raise ValueError('Backup hash mismatch')
        store = WorkspaceStore(destination)
        if preflight is None:
            raise ValueError('Verified capacity preflight is required')
        decision = (preflight('restore', store, source.stat().st_size, destination=store.root)
                    if getattr(preflight, 'accepts_context', False)
                    else preflight('restore', store, source.stat().st_size))
        if not isinstance(decision, dict) or decision.get('allowed') is not True:
            raise ValueError('Capacity preflight did not permit writing')
        store.create()
        with store.lock():
            with source.open('rb') as inp, store.database.open('xb') as out:
                cls(store, preflight)._copy_checked(inp, out, store.root)
                out.flush()
                os.fsync(out.fileno())
            if _hash_file(store.database) != manifest['sha256']:
                raise ValueError('Restore copy failed integrity check')
            import duckdb
            db = duckdb.connect(str(store.database))
            try:
                cls(store, preflight)._configure_resources(db)
                actual_id = db.execute("SELECT value FROM mart_meta WHERE key='workspace_id'").fetchone()[0]
                revision = int(db.execute("SELECT value FROM mart_meta WHERE key='revision'").fetchone()[0])
                if actual_id != manifest['workspace_id'] or revision != manifest['revision']:
                    raise ValueError('Backup identity or revision mismatch')
                db.execute("UPDATE mart_meta SET value=? WHERE key='workspace_id'", [store.validate()['id']])
                db.execute('CHECKPOINT')
            finally:
                db.close()
        engine = cls(store, preflight=preflight)
        verified = engine.reconcile()
        if not verified['matches']:
            raise ValueError('Restored data controls differ')
        return {'workspace': str(store.root), 'revision': revision, 'later_data': 'requires review', 'approvals': 'not restored'}
