"""Typed contracts for an isolated, single-fact mart."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Dimension:
    name: str
    key_columns: tuple[str, ...]
    attributes: tuple[str, ...] = ()
    authority_column: str | None = None


@dataclass(frozen=True)
class Measure:
    name: str
    column: str
    precision: int = 18
    scale: int = 2
    unit: str = ""
    aggregation: str = "sum"
    kind: str = "value"


@dataclass(frozen=True)
class MartModel:
    name: str
    fact_table: str
    dimensions: tuple[Dimension, ...]
    measures: tuple[Measure, ...]
    period_column: str
    partition_columns: tuple[str, ...] = ()
    row_key_columns: tuple[str, ...] = ()
    excluded_columns: tuple[str, ...] = ()
    version: str = "1"
    source_delimiter: str = ","
    source_encoding: str = "utf-8-sig"


@dataclass(frozen=True)
class ExportDefinition:
    name: str
    columns: tuple[str, ...]
    format: str = "wide"
    cost_measures: tuple[str, ...] = ()
    group_by: tuple[str, ...] = ()
    filters: tuple[tuple[str, str], ...] = ()
    period_start: str | None = None
    period_end: str | None = None


def validate_column_roles(model):
    """Reject numeric/text role collisions without forbidding shared dimensions."""
    non_numeric = {model.period_column, *model.partition_columns, *model.row_key_columns}
    for dimension in model.dimensions:
        non_numeric.update(dimension.key_columns)
        non_numeric.update(dimension.attributes)
        if dimension.authority_column:
            non_numeric.add(dimension.authority_column)
    measures = [measure.column.casefold() for measure in model.measures]
    text_columns = {column.casefold() for column in non_numeric}
    excluded = {column.casefold() for column in model.excluded_columns}
    if len(measures) != len(set(measures)):
        raise ValueError('A source column cannot be used by multiple measures')
    if set(measures) & text_columns:
        raise ValueError('A measure source column cannot also be a key, attribute, period or authority')
    if excluded & (text_columns | set(measures)):
        raise ValueError('An excluded column cannot also have an active role')


def model_from_dict(data):
    """Restore a model from its persisted JSON-compatible representation."""
    return MartModel(
        name=data['name'], fact_table=data['fact_table'],
        dimensions=tuple(Dimension(d['name'], tuple(d['key_columns']), tuple(d.get('attributes', ())), d.get('authority_column')) for d in data['dimensions']),
        measures=tuple(Measure(**m) for m in data['measures']),
        period_column=data['period_column'], partition_columns=tuple(data.get('partition_columns', ())),
        row_key_columns=tuple(data.get('row_key_columns', ())), excluded_columns=tuple(data.get('excluded_columns', ())),
        version=data.get('version', '1'),
        source_delimiter=data.get('source_delimiter', ','),
        source_encoding=data.get('source_encoding', 'utf-8-sig'))



def export_from_dict(data):
    """Restore an export definition from its JSON-compatible form."""
    return ExportDefinition(name=data['name'], columns=tuple(data['columns']), format=data.get('format', 'wide'),
                            cost_measures=tuple(data.get('cost_measures', ())), group_by=tuple(data.get('group_by', ())),
                            filters=tuple(tuple(item) for item in data.get('filters', ())),
                            period_start=data.get('period_start'), period_end=data.get('period_end'))
