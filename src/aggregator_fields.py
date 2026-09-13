"""Tkinter-free state model for the Data Aggregator field pools and pivot areas.

The aggregator screen behaves like a pivot field list: every column starts in a
source *pool* (dimensions or measures) and moves into a pivot *area* (row groups
or values).  A field is never in a pool and an area at the same time, which is
what lets the UI show a column disappearing from the left panel once it is used.

Rules (column groups and derived formulas) create *derived* fields.  They join
the measure pool like any other field, and only reach the output file once they
are placed into the values area.

An area only accepts the pool it matches: row groups take raw dimension
columns, values take measure columns (source measures and rule-created ones).
A column the schema read as the wrong kind is moved with `reclassify()` first;
placement never reclassifies on its own.

Every change is reversible: the caller records the configuration with
`snapshot()` just before a mutating action and `undo()` puts it back.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple, Union

from src.data_aggregator import (
    AGGREGATION_FUNCTIONS,
    ColumnGroupRule,
    DerivedFormulaRule,
    FilterCondition,
)

# Home pools
DIMENSION = "dimension"
MEASURE = "measure"

# Field kinds
RAW = "raw"
GROUP_RULE = "group"
FORMULA_RULE = "formula"
YEAR = "year"  # the year read out of the month column; a dimension, not a rule
CONSTANT = "constant"  # a literal the user types, stamped on every output row

DEFAULT_ANNUAL_COLUMN = "연도"

# How a value column is aggregated. Declared here rather than imported from the
# engine so this model keeps its own vocabulary; the engine reads the same names.
# Re-exported from the engine on purpose: the engine silently falls back to sum
# for a name it does not know, so a second list here could drift and the UI would
# offer a choice that quietly does nothing.
MEASURE_FUNCTIONS: Tuple[str, ...] = tuple(AGGREGATION_FUNCTIONS)
DEFAULT_MEASURE_FUNCTION = "sum"

# Pivot areas
ROWS = "rows"
VALUES = "values"
FILTERS = "filters"

# Derived fields and preset-only columns sort after every real file column.
_DERIVED_ORDER_BASE = 1_000_000
_UNKNOWN_ORDER_BASE = 900_000

# Undo depth. Deep enough for a stretch of mis-clicks, shallow enough that a
# long session never grows without limit.
_UNDO_LIMIT = 20

# Everything that describes the current configuration. The undo stack itself is
# deliberately missing: snapshotting it would nest a copy inside every copy.
_SNAPSHOT_ATTRS: Tuple[str, ...] = (
    "dimension_pool",
    "measure_pool",
    "group_keys",
    "values",
    "filters",
    "column_groups",
    "derived_formulas",
    "month_column",
    "annual_column_name",
    "constant_columns",
    "measure_functions",
    "_fields",
    "_home",
    "_derived_seq",
    "_unknown_seq",
)

Rule = Union[ColumnGroupRule, DerivedFormulaRule]


@dataclass(frozen=True)
class PoolField:
    """One selectable field, either a source column or a rule-created column."""

    name: str
    kind: str = RAW
    order: int = 0
    is_month: bool = False

    @property
    def is_derived(self) -> bool:
        """True only for value-producing rules. The year and constant fields are
        dimensions, so they stay placeable as row keys."""
        return self.kind in (GROUP_RULE, FORMULA_RULE)


def rule_dependencies(rule: Rule) -> List[str]:
    """Return the column names a rule reads from."""
    if isinstance(rule, ColumnGroupRule):
        return list(rule.source_columns)
    return [rule.numerator_column, rule.denominator_column]


class AggregatorFieldState:
    """Single source of truth for what the aggregator screen currently shows."""

    def __init__(self) -> None:
        self.reset()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def reset(self) -> None:
        self.dimension_pool: List[PoolField] = []
        self.measure_pool: List[PoolField] = []
        self.group_keys: List[str] = []
        self.values: List[str] = []
        self.filters: List[FilterCondition] = []
        self.column_groups: List[ColumnGroupRule] = []
        self.derived_formulas: List[DerivedFormulaRule] = []
        self.month_column: Optional[str] = None
        self.annual_column_name: str = DEFAULT_ANNUAL_COLUMN
        self.constant_columns: Dict[str, str] = {}
        self.measure_functions: Dict[str, str] = {}
        self._fields: Dict[str, PoolField] = {}
        self._home: Dict[str, str] = {}
        self._derived_seq = 0
        self._unknown_seq = 0
        # A new source file is a new session: nothing before it may be undone into.
        self._undo_stack: List[Dict[str, object]] = []

    def load_schema(
        self,
        dimensions: Sequence[str],
        measures: Sequence[str],
        month_column: Optional[str] = None,
        columns: Optional[Sequence[str]] = None,
    ) -> None:
        """Reset the model and repopulate both pools from a dataset schema."""
        self.reset()
        self.month_column = month_column

        # Original file column order is the most intuitive "put it back" position.
        order_of = {name: idx for idx, name in enumerate(columns or [])}

        def _order(name: str, fallback: int) -> int:
            return order_of.get(name, len(order_of) + fallback)

        for idx, name in enumerate(dimensions):
            self._register(
                PoolField(name=name, kind=RAW, order=_order(name, idx), is_month=(name == month_column)),
                DIMENSION,
            )
        for idx, name in enumerate(measures):
            if name in self._fields:
                continue  # already claimed as a dimension; a column has one home
            self._register(PoolField(name=name, kind=RAW, order=_order(name, idx)), MEASURE)

        # A YYYYMM column also offers its year. Making it a field the user drags
        # replaces the old global "roll up to years" switch, which silently added
        # a column nobody asked for.
        if month_column and self.annual_column_name not in self._fields:
            self._register(
                PoolField(
                    name=self.annual_column_name,
                    kind=YEAR,
                    order=_order(month_column, 0) - 1,  # sits just above its source
                ),
                DIMENSION,
            )

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------
    def snapshot(self) -> None:
        """Record the current configuration so the next change can be undone."""
        captured = self._capture()
        if self._undo_stack and self._undo_stack[-1] == captured:
            return  # nothing changed since the last snapshot; one entry is enough
        self._undo_stack.append(captured)
        if len(self._undo_stack) > _UNDO_LIMIT:
            del self._undo_stack[0]

    def undo(self) -> bool:
        """Restore the configuration before the last snapshot. False if nothing to undo."""
        if not self._undo_stack:
            return False
        for name, value in self._undo_stack.pop().items():
            setattr(self, name, value)
        return True

    def can_undo(self) -> bool:
        """True while at least one recorded configuration is waiting."""
        return bool(self._undo_stack)

    def clear_undo(self) -> None:
        """Forget the recorded history without touching the configuration."""
        self._undo_stack = []

    def _capture(self) -> Dict[str, object]:
        """Deep-copy the configuration. It holds only plain containers and frozen
        dataclasses, so the copy shares nothing mutable with the live state."""
        return {name: deepcopy(getattr(self, name)) for name in _SNAPSHOT_ATTRS}

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------
    def field(self, name: str) -> Optional[PoolField]:
        return self._fields.get(name)

    def is_derived(self, name: str) -> bool:
        entry = self._fields.get(name)
        return bool(entry and entry.is_derived)

    def is_known(self, name: str) -> bool:
        return name in self._fields

    def home_of(self, name: str) -> Optional[str]:
        """Return the pool a field lives in: `DIMENSION`, `MEASURE` or None."""
        return self._home.get(name)

    def rule_for(self, name: str) -> Optional[Rule]:
        for rule in self.column_groups:
            if rule.new_column == name:
                return rule
        for rule in self.derived_formulas:
            if rule.new_column == name:
                return rule
        return None

    def raw_measure_names(self) -> List[str]:
        """Every numeric source column, whether pooled or already placed."""
        return [
            name
            for name, entry in self._fields.items()
            if entry.kind == RAW and self._home.get(name) == MEASURE
        ]

    def formula_candidate_names(self) -> List[str]:
        """Columns a derived formula may reference: source measures plus earlier rules."""
        names = self.raw_measure_names()
        names.extend(rule.new_column for rule in self.column_groups)
        names.extend(rule.new_column for rule in self.derived_formulas)
        return list(dict.fromkeys(names))

    # ------------------------------------------------------------------
    # Pool <-> area movement
    # ------------------------------------------------------------------
    def can_place_group_key(self, name: str) -> bool:
        """True when the field is allowed in the row-group area.

        Only raw columns whose home is the dimension pool are row keys.  A
        rule-created column has no raw values to group by, and a measure has to
        be moved to the dimension pool with `reclassify()` first.  Placement of
        an item already sitting in an area is not considered here, so the UI can
        reuse this as a drop-target test while reordering.
        """
        entry = self._fields.get(name)
        return bool(entry and not entry.is_derived and self._home.get(name) == DIMENSION)

    def can_place_value(self, name: str) -> bool:
        """True when the field is allowed in the values area.

        Everything whose home is the measure pool qualifies: source measures
        plus the columns created by group and formula rules, which register
        there too.  Raw dimensions do not.
        """
        return self._home.get(name) == MEASURE

    def place_group_key(self, name: str, index: Optional[int] = None) -> bool:
        """Move a raw dimension column into the row-group area."""
        if not self.can_place_group_key(name):
            return False
        if name in self.group_keys or name in self.values:
            return False
        self._remove_from_pool(name)
        self._insert_at(self.group_keys, name, index)
        return True

    def place_value(self, name: str, index: Optional[int] = None) -> bool:
        """Move a measure column, source or rule-created, into the values area."""
        if not self.can_place_value(name):
            return False
        if name in self.values or name in self.group_keys:
            return False
        self._remove_from_pool(name)
        self._insert_at(self.values, name, index)
        return True

    def unplace(self, name: str) -> bool:
        """Return a placed field to its home pool."""
        if name in self.group_keys:
            self.group_keys.remove(name)
        elif name in self.values:
            self.values.remove(name)
        else:
            return False
        entry = self._fields.get(name)
        if entry is not None:
            self._insert_into_pool(entry)
        return True

    def can_drop_on_pool(self, name: str, target_home: str) -> bool:
        """True when a pool may take the field.

        Either the field is going home (back from an area, or a no-op drop on
        its own pool) or it is a raw source column, which may change home.
        The year, constant and rule fields belong to one pool by nature.
        """
        entry = self._fields.get(name)
        if entry is None or target_home not in (DIMENSION, MEASURE):
            return False
        return self._home.get(name) == target_home or entry.kind == RAW

    def reclassify(self, name: str, target_home: str) -> bool:
        """Switch a pooled raw source column between the dimension and measure pools."""
        entry = self._fields.get(name)
        if entry is None or entry.kind != RAW:
            return False
        if target_home not in (DIMENSION, MEASURE) or self._home.get(name) == target_home:
            return False
        if name in self.group_keys or name in self.values:
            return False  # only pooled fields can change home
        self._remove_from_pool(name)
        self._home[name] = target_home
        if target_home == DIMENSION:
            self.measure_functions.pop(name, None)  # a dimension aggregates nothing
        self._insert_into_pool(entry)
        return True

    def reorder(self, area: str, from_index: int, to_index: int) -> bool:
        """Move an item within an area, which also changes output column order."""
        target = self._area_list(area)
        if target is None or not (0 <= from_index < len(target)):
            return False
        to_index = max(0, min(to_index, len(target) - 1))
        if from_index == to_index:
            return False
        target.insert(to_index, target.pop(from_index))
        return True

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------
    def add_column_group(self, rule: ColumnGroupRule) -> bool:
        if self.is_known(rule.new_column):
            return False
        self.column_groups.append(rule)
        self._register_derived(rule.new_column, GROUP_RULE)
        return True

    def add_derived_formula(self, rule: DerivedFormulaRule) -> bool:
        if self.is_known(rule.new_column):
            return False
        self.derived_formulas.append(rule)
        self._register_derived(rule.new_column, FORMULA_RULE)
        return True

    def dependents_of(self, name: str) -> List[str]:
        """Rule columns that would break if `name` disappeared, transitively."""
        found: List[str] = []
        frontier = [name]
        while frontier:
            current = frontier.pop()
            for rule in list(self.column_groups) + list(self.derived_formulas):
                if rule.new_column in found or rule.new_column == name:
                    continue
                if current in rule_dependencies(rule):
                    found.append(rule.new_column)
                    frontier.append(rule.new_column)
        return found

    def remove_derived(self, name: str) -> List[str]:
        """Delete a rule and every rule that depends on it. Returns removed names."""
        if not self.is_derived(name):
            return []
        removed = [name] + self.dependents_of(name)
        for target in removed:
            self.column_groups = [r for r in self.column_groups if r.new_column != target]
            self.derived_formulas = [r for r in self.derived_formulas if r.new_column != target]
            self._forget(target)
        return removed

    # ------------------------------------------------------------------
    # Constant and year columns
    # ------------------------------------------------------------------
    def add_constant_column(self, name: str, value: str) -> bool:
        """Register a literal column, e.g. YYYY = 2026, stamped on every row."""
        name = name.strip()
        if not name or self.is_known(name):
            return False
        self.constant_columns[name] = value
        self._unknown_seq += 1
        self._register(
            PoolField(name=name, kind=CONSTANT, order=_UNKNOWN_ORDER_BASE + self._unknown_seq),
            DIMENSION,
        )
        return True

    def remove_constant_column(self, name: str) -> bool:
        if name not in self.constant_columns:
            return False
        del self.constant_columns[name]
        self._forget(name)
        return True

    def is_constant(self, name: str) -> bool:
        entry = self._fields.get(name)
        return bool(entry and entry.kind == CONSTANT)

    def is_year(self, name: str) -> bool:
        entry = self._fields.get(name)
        return bool(entry and entry.kind == YEAR)

    def uses_year(self) -> bool:
        """True when the year field is placed, which is what turns roll-up on."""
        return any(self.is_year(name) for name in self.group_keys)

    def constant_columns_for_spec(self) -> Dict[str, str]:
        """Only the literals actually placed in the row area reach the output."""
        return {name: self.constant_columns[name] for name in self.group_keys if name in self.constant_columns}

    # ------------------------------------------------------------------
    # Aggregation function per value column
    # ------------------------------------------------------------------
    def set_measure_function(self, name: str, function: str) -> bool:
        """Choose how a value column is aggregated.

        False when that is not allowed, or when the choice is already in effect:
        nothing changes then, so the caller records no undo step for it.

        Only a placeable measure carries a function: a raw dimension has nothing
        to aggregate, and a rule column is computed from the grouped totals
        instead of being aggregated itself.
        """
        if function not in MEASURE_FUNCTIONS:
            return False
        if not self.can_place_value(name) or self.is_derived(name):
            return False
        if self.measure_function(name) == function:
            return False
        if function == DEFAULT_MEASURE_FUNCTION:
            # Absent already means "sum"; storing it would be a second spelling.
            self.measure_functions.pop(name, None)
        else:
            self.measure_functions[name] = function
        return True

    def measure_function(self, name: str) -> str:
        """The function chosen for a column, `"sum"` while nothing is chosen."""
        return self.measure_functions.get(name, DEFAULT_MEASURE_FUNCTION)

    def measure_functions_for_spec(self) -> Dict[str, str]:
        """Non-default choices of the columns actually placed in the values area.

        A default configuration yields an empty dict, which the engine reads as
        "sum everything", exactly as it behaved before functions existed.
        """
        return {
            name: self.measure_functions[name]
            for name in self.values
            if self.measure_functions.get(name, DEFAULT_MEASURE_FUNCTION) != DEFAULT_MEASURE_FUNCTION
        }

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------
    def add_filter(self, condition: FilterCondition) -> None:
        self.filters.append(condition)

    def remove_filter(self, index: int) -> bool:
        if 0 <= index < len(self.filters):
            del self.filters[index]
            return True
        return False

    # ------------------------------------------------------------------
    # Spec / preset conversion
    # ------------------------------------------------------------------
    def measure_sums(self) -> List[str]:
        """Placed values that are plain source columns."""
        return [name for name in self.values if not self.is_derived(name)]

    def rules_for_spec(self) -> Tuple[List[ColumnGroupRule], List[DerivedFormulaRule]]:
        """Rules actually needed to produce the current values, with output flags set.

        A rule that is only referenced by another rule is kept as an intermediate
        calculation (`output=False`) so it never reaches the result file.
        """
        needed = self._required_rule_names()
        groups = [
            replace(rule, output=rule.new_column in self.values)
            for rule in self.column_groups
            if rule.new_column in needed
        ]
        formulas = [
            replace(rule, output=rule.new_column in self.values)
            for rule in self.derived_formulas
            if rule.new_column in needed
        ]
        return groups, formulas

    def all_rules_with_output(self) -> Tuple[List[ColumnGroupRule], List[DerivedFormulaRule]]:
        """Every rule with its output flag, including ones not placed yet (for presets)."""
        groups = [replace(r, output=r.new_column in self.values) for r in self.column_groups]
        formulas = [replace(r, output=r.new_column in self.values) for r in self.derived_formulas]
        return groups, formulas

    def apply_configuration(
        self,
        *,
        group_keys: Sequence[str],
        measure_sums: Sequence[str],
        column_groups: Sequence[ColumnGroupRule],
        derived_formulas: Sequence[DerivedFormulaRule],
        filters: Sequence[FilterCondition],
        value_order: Optional[Sequence[str]] = None,
        constant_columns: Optional[Dict[str, str]] = None,
        measure_functions: Optional[Dict[str, str]] = None,
    ) -> None:
        """Replace every placement and rule, keeping the loaded schema pools intact."""
        # Applying a preset is one user action, so it snapshots itself: a caller
        # that already snapshotted adds no duplicate, and one load stays one undo.
        self.snapshot()
        for name in list(self.group_keys) + list(self.values):
            self.unplace(name)
        for entry in [f for f in self.measure_pool if f.is_derived]:
            self.remove_derived(entry.name)
        for name in list(self.constant_columns):
            self.remove_constant_column(name)
        self.column_groups = []
        self.derived_formulas = []
        self.filters = list(filters)
        self.measure_functions = {}  # a leftover choice must not outlive its preset

        for name, value in (constant_columns or {}).items():
            self.add_constant_column(name, value)

        # A hand-edited preset can name a row key after a column a rule creates.
        # Registering it as a raw field first would make the rule fail to register
        # and disappear without a word, so the rule keeps the name.
        rule_names = {rule.new_column for rule in column_groups}
        rule_names.update(rule.new_column for rule in derived_formulas)

        for name in group_keys:
            if name in rule_names:
                continue  # a calculated column can never be a row key
            self._ensure_home(name, DIMENSION)
            self.place_group_key(name)

        for rule in column_groups:
            self.add_column_group(rule)
        for rule in derived_formulas:
            self.add_derived_formula(rule)

        placements = list(measure_sums)
        placements.extend(r.new_column for r in column_groups if r.output)
        placements.extend(r.new_column for r in derived_formulas if r.output)
        placements = list(dict.fromkeys(placements))

        if value_order:
            preferred = [name for name in value_order if name in placements]
            placements = preferred + [name for name in placements if name not in preferred]

        for name in placements:
            self._ensure_home(name, MEASURE)
            self.place_value(name)

        # After placement, so every column already has its measure home. A saved
        # choice that no longer applies is dropped instead of replayed.
        for name, function in (measure_functions or {}).items():
            self.set_measure_function(name, function)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _area_list(self, area: str) -> Optional[List]:
        return {ROWS: self.group_keys, VALUES: self.values, FILTERS: self.filters}.get(area)

    def _pool_for(self, name: str) -> List[PoolField]:
        return self.dimension_pool if self._home.get(name) == DIMENSION else self.measure_pool

    def _register(self, entry: PoolField, home: str) -> None:
        self._fields[entry.name] = entry
        self._home[entry.name] = home
        self._insert_into_pool(entry)

    def _register_derived(self, name: str, kind: str) -> None:
        self._derived_seq += 1
        entry = PoolField(name=name, kind=kind, order=_DERIVED_ORDER_BASE + self._derived_seq)
        self._register(entry, MEASURE)

    def _ensure_home(self, name: str, home: str) -> None:
        """Make a preset column exist in the pool its saved area requires.

        A preset records the area a column was used in, and that beats the
        guess this file's schema made: a column saved as a row key but detected
        as a measure here is reclassified instead of silently dropped from the
        replayed configuration.  Unknown columns are registered on the spot.
        """
        if name not in self._fields:
            self._unknown_seq += 1
            self._register(
                PoolField(name=name, kind=RAW, order=_UNKNOWN_ORDER_BASE + self._unknown_seq), home
            )
            return
        if self._home.get(name) != home:
            self.reclassify(name, home)

    def _insert_into_pool(self, entry: PoolField) -> None:
        pool = self._pool_for(entry.name)
        if any(existing.name == entry.name for existing in pool):
            return
        position = len(pool)
        for idx, existing in enumerate(pool):
            if existing.order > entry.order:
                position = idx
                break
        pool.insert(position, entry)

    def _remove_from_pool(self, name: str) -> None:
        for pool in (self.dimension_pool, self.measure_pool):
            for idx, entry in enumerate(pool):
                if entry.name == name:
                    del pool[idx]
                    return

    def _forget(self, name: str) -> None:
        self._remove_from_pool(name)
        if name in self.group_keys:
            self.group_keys.remove(name)
        if name in self.values:
            self.values.remove(name)
        self._fields.pop(name, None)
        self._home.pop(name, None)
        self.measure_functions.pop(name, None)

    @staticmethod
    def _insert_at(target: List[str], name: str, index: Optional[int]) -> None:
        if index is None or index >= len(target):
            target.append(name)
        else:
            target.insert(max(0, index), name)

    def _required_rule_names(self) -> set:
        by_name: Dict[str, Rule] = {r.new_column: r for r in self.column_groups}
        by_name.update({r.new_column: r for r in self.derived_formulas})

        needed: set = set()
        frontier = [name for name in self.values if name in by_name]
        while frontier:
            current = frontier.pop()
            if current in needed:
                continue
            needed.add(current)
            for dependency in rule_dependencies(by_name[current]):
                if dependency in by_name and dependency not in needed:
                    frontier.append(dependency)
        return needed
