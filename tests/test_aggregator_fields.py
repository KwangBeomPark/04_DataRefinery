"""Unit tests for the aggregator field-pool state model (no Tkinter required)."""

import unittest

from src.aggregator_fields import (
    DEFAULT_MEASURE_FUNCTION,
    DIMENSION,
    MEASURE,
    MEASURE_FUNCTIONS,
    ROWS,
    VALUES,
    AggregatorFieldState,
)
from src.data_aggregator import ColumnGroupRule, DerivedFormulaRule, FilterCondition

COLUMNS = ["월", "국가", "거래선", "디비전", "수량", "매출", "비용1", "비용2", "비용3"]
DIMENSIONS = ["월", "국가", "거래선", "디비전"]
MEASURES = ["수량", "매출", "비용1", "비용2", "비용3"]
# A month column also offers its year, which sits just above its source column.
POOLED_DIMENSIONS = ["연도", "월", "국가", "거래선", "디비전"]


def make_state() -> AggregatorFieldState:
    state = AggregatorFieldState()
    state.load_schema(DIMENSIONS, MEASURES, month_column="월", columns=COLUMNS)
    return state


class TestFieldPools(unittest.TestCase):
    def test_load_schema_fills_both_pools(self):
        state = make_state()
        self.assertEqual([f.name for f in state.dimension_pool], POOLED_DIMENSIONS)
        self.assertEqual([f.name for f in state.measure_pool], MEASURES)
        self.assertTrue(state.field("월").is_month)

    def test_a_month_column_offers_a_year_field(self):
        state = make_state()
        self.assertTrue(state.is_year("연도"))
        self.assertFalse(state.is_derived("연도"))
        self.assertTrue(state.can_place_group_key("연도"))
        self.assertFalse(state.can_place_value("연도"))

    def test_without_a_month_column_there_is_no_year_field(self):
        state = AggregatorFieldState()
        state.load_schema(["국가"], ["매출"], month_column=None, columns=["국가", "매출"])
        self.assertEqual([f.name for f in state.dimension_pool], ["국가"])

    def test_uses_year_follows_the_placement(self):
        state = make_state()
        self.assertFalse(state.uses_year())
        state.place_group_key("연도")
        self.assertTrue(state.uses_year())
        state.unplace("연도")
        self.assertFalse(state.uses_year())

    def test_placing_a_row_removes_it_from_the_source_pool(self):
        state = make_state()
        self.assertTrue(state.place_group_key("디비전"))

        self.assertEqual(state.group_keys, ["디비전"])
        self.assertNotIn("디비전", [f.name for f in state.dimension_pool])

    def test_unplacing_restores_the_original_pool_position(self):
        state = make_state()
        state.place_group_key("국가")
        state.unplace("국가")

        # Back between 월 and 거래선, not appended at the end.
        self.assertEqual([f.name for f in state.dimension_pool], POOLED_DIMENSIONS)

    def test_a_field_cannot_sit_in_two_areas(self):
        state = make_state()
        state.place_group_key("디비전")

        self.assertFalse(state.place_value("디비전"))
        self.assertEqual(state.values, [])

    def test_reclassify_moves_a_pooled_column_between_pools(self):
        state = make_state()
        self.assertTrue(state.reclassify("거래선", MEASURE))

        self.assertNotIn("거래선", [f.name for f in state.dimension_pool])
        self.assertIn("거래선", [f.name for f in state.measure_pool])
        self.assertIn("거래선", state.raw_measure_names())

    def test_reclassify_refuses_a_placed_column(self):
        state = make_state()
        state.place_group_key("디비전")

        self.assertFalse(state.reclassify("디비전", MEASURE))

    def test_only_raw_source_columns_can_change_pool(self):
        """The year and a constant are dimensions by nature; a rule is a measure."""
        state = make_state()
        state.add_constant_column("YYYY", "2026")
        state.add_column_group(ColumnGroupRule("총비용", ["비용1"]))

        self.assertFalse(state.reclassify("연도", MEASURE))
        self.assertFalse(state.reclassify("YYYY", MEASURE))
        self.assertFalse(state.reclassify("총비용", DIMENSION))
        self.assertEqual([f.name for f in state.measure_pool], MEASURES + ["총비용"])
        self.assertFalse(state.can_place_value("연도"))

    def test_can_drop_on_pool_allows_going_home_or_reclassifying_a_raw_column(self):
        state = make_state()
        state.add_constant_column("YYYY", "2026")
        state.add_column_group(ColumnGroupRule("총비용", ["비용1"]))
        state.place_value("총비용")

        self.assertTrue(state.can_drop_on_pool("국가", MEASURE))  # raw: reclassify
        self.assertTrue(state.can_drop_on_pool("총비용", MEASURE))  # placed rule going home
        self.assertTrue(state.can_drop_on_pool("YYYY", DIMENSION))  # its own pool
        self.assertFalse(state.can_drop_on_pool("총비용", DIMENSION))
        self.assertFalse(state.can_drop_on_pool("연도", MEASURE))
        self.assertFalse(state.can_drop_on_pool("YYYY", MEASURE))
        self.assertFalse(state.can_drop_on_pool("없는컬럼", MEASURE))

    def test_reorder_changes_value_order(self):
        state = make_state()
        state.place_value("수량")
        state.place_value("매출")

        self.assertTrue(state.reorder(VALUES, 1, 0))
        self.assertEqual(state.values, ["매출", "수량"])

    def test_reorder_rejects_out_of_range(self):
        state = make_state()
        state.place_group_key("디비전")
        self.assertFalse(state.reorder(ROWS, 5, 0))


class TestPlacementRules(unittest.TestCase):
    """An area only takes the pool that matches it; reclassify is the way around."""

    def test_measure_is_rejected_from_the_row_group_area(self):
        state = make_state()

        self.assertFalse(state.place_group_key("매출"))
        self.assertEqual(state.group_keys, [])
        self.assertIn("매출", [f.name for f in state.measure_pool])

    def test_dimension_is_rejected_from_the_values_area(self):
        state = make_state()

        self.assertFalse(state.place_value("디비전"))
        self.assertEqual(state.values, [])
        self.assertIn("디비전", [f.name for f in state.dimension_pool])

    def test_derived_column_is_values_only(self):
        state = make_state()
        state.add_column_group(ColumnGroupRule("총비용", ["비용1", "비용2"]))

        self.assertFalse(state.place_group_key("총비용"))
        self.assertTrue(state.place_value("총비용"))
        self.assertEqual(state.group_keys, [])
        self.assertEqual(state.values, ["총비용"])

    def test_unknown_column_is_rejected_by_both_areas(self):
        state = make_state()

        self.assertFalse(state.place_group_key("없는컬럼"))
        self.assertFalse(state.place_value("없는컬럼"))

    def test_reclassify_then_place_is_the_escape_hatch_for_a_measure(self):
        state = make_state()
        self.assertFalse(state.place_group_key("수량"))

        self.assertTrue(state.reclassify("수량", DIMENSION))
        self.assertTrue(state.place_group_key("수량"))
        self.assertEqual(state.group_keys, ["수량"])

    def test_reclassify_then_place_is_the_escape_hatch_for_a_dimension(self):
        state = make_state()
        self.assertFalse(state.place_value("거래선"))

        self.assertTrue(state.reclassify("거래선", MEASURE))
        self.assertTrue(state.place_value("거래선"))
        self.assertEqual(state.values, ["거래선"])

    def test_can_place_predicates_follow_the_home_pool(self):
        state = make_state()
        state.add_column_group(ColumnGroupRule("총비용", ["비용1"]))

        self.assertTrue(state.can_place_group_key("디비전"))
        self.assertFalse(state.can_place_group_key("매출"))
        self.assertFalse(state.can_place_group_key("총비용"))

        self.assertTrue(state.can_place_value("매출"))
        self.assertTrue(state.can_place_value("총비용"))
        self.assertFalse(state.can_place_value("디비전"))

        self.assertEqual(state.home_of("디비전"), DIMENSION)
        self.assertEqual(state.home_of("총비용"), MEASURE)
        self.assertIsNone(state.home_of("없는컬럼"))

    def test_an_already_placed_field_still_matches_its_own_area(self):
        # The UI reuses can_place_* as a drop-target test, so dragging a placed
        # item around inside its own area must stay allowed.
        state = make_state()
        state.place_group_key("디비전")

        self.assertTrue(state.can_place_group_key("디비전"))
        self.assertFalse(state.place_group_key("디비전"))  # but placing it twice is not


class TestMeasureFunctionNames(unittest.TestCase):
    def test_the_state_model_offers_exactly_what_the_engine_understands(self):
        """A name the engine does not know is silently treated as sum, so the two
        lists must not be allowed to drift apart."""
        from src.aggregator_fields import MEASURE_FUNCTIONS
        from src.data_aggregator import AGGREGATION_FUNCTIONS

        self.assertEqual(tuple(MEASURE_FUNCTIONS), tuple(AGGREGATION_FUNCTIONS))


class TestConstantColumns(unittest.TestCase):
    def test_a_literal_joins_the_dimension_pool(self):
        state = make_state()
        self.assertTrue(state.add_constant_column("YYYY", "2026"))

        self.assertEqual([f.name for f in state.dimension_pool][-1], "YYYY")
        self.assertTrue(state.is_constant("YYYY"))
        self.assertEqual(state.constant_columns["YYYY"], "2026")
        self.assertTrue(state.can_place_group_key("YYYY"))
        self.assertFalse(state.can_place_value("YYYY"))

    def test_a_name_already_in_the_file_is_rejected(self):
        state = make_state()
        self.assertFalse(state.add_constant_column("디비전", "x"))
        self.assertFalse(state.add_constant_column("  ", "x"))
        self.assertEqual(state.constant_columns, {})

    def test_only_placed_literals_reach_the_spec(self):
        state = make_state()
        state.add_constant_column("YYYY", "2026")
        state.add_constant_column("출처", "ERP")

        state.place_group_key("YYYY")

        self.assertEqual(state.constant_columns_for_spec(), {"YYYY": "2026"})

    def test_removing_a_literal_takes_it_out_of_the_row_area_too(self):
        state = make_state()
        state.add_constant_column("YYYY", "2026")
        state.place_group_key("YYYY")

        self.assertTrue(state.remove_constant_column("YYYY"))

        self.assertEqual(state.group_keys, [])
        self.assertNotIn("YYYY", [f.name for f in state.dimension_pool])
        self.assertFalse(state.is_known("YYYY"))
        self.assertFalse(state.remove_constant_column("YYYY"))

    def test_apply_configuration_restores_literals(self):
        state = make_state()
        state.apply_configuration(
            group_keys=["YYYY", "디비전"],
            measure_sums=["매출"],
            column_groups=[],
            derived_formulas=[],
            filters=[],
            constant_columns={"YYYY": "2026"},
        )

        self.assertEqual(state.group_keys, ["YYYY", "디비전"])
        self.assertEqual(state.constant_columns_for_spec(), {"YYYY": "2026"})

    def test_apply_configuration_drops_literals_from_the_previous_setup(self):
        state = make_state()
        state.add_constant_column("YYYY", "2026")

        state.apply_configuration(
            group_keys=["디비전"], measure_sums=[], column_groups=[],
            derived_formulas=[], filters=[],
        )

        self.assertEqual(state.constant_columns, {})
        self.assertFalse(state.is_known("YYYY"))


class TestRules(unittest.TestCase):
    def test_column_group_joins_the_measure_pool(self):
        state = make_state()
        rule = ColumnGroupRule(new_column="총비용", source_columns=["비용1", "비용2", "비용3"])
        self.assertTrue(state.add_column_group(rule))

        pool = [f.name for f in state.measure_pool]
        self.assertEqual(pool[-1], "총비용")  # appended at the bottom
        self.assertTrue(state.is_derived("총비용"))
        self.assertIs(state.rule_for("총비용"), rule)

    def test_duplicate_rule_name_is_rejected(self):
        state = make_state()
        state.add_column_group(ColumnGroupRule("총비용", ["비용1"]))

        self.assertFalse(state.add_column_group(ColumnGroupRule("총비용", ["비용2"])))
        self.assertFalse(state.add_derived_formula(DerivedFormulaRule("매출", "매출", "수량")))
        self.assertEqual(len(state.column_groups), 1)

    def test_removing_a_rule_cascades_to_dependents(self):
        state = make_state()
        state.add_column_group(ColumnGroupRule("총비용", ["비용1", "비용2"]))
        state.add_derived_formula(DerivedFormulaRule("비용율", "총비용", "매출"))
        state.place_value("비용율")

        self.assertEqual(state.dependents_of("총비용"), ["비용율"])
        removed = state.remove_derived("총비용")

        self.assertEqual(sorted(removed), sorted(["총비용", "비용율"]))
        self.assertEqual(state.column_groups, [])
        self.assertEqual(state.derived_formulas, [])
        self.assertEqual(state.values, [])

    def test_rules_only_reach_the_output_when_placed(self):
        state = make_state()
        state.add_column_group(ColumnGroupRule("총비용", ["비용1", "비용2"]))
        state.place_value("매출")

        groups, formulas = state.rules_for_spec()
        self.assertEqual(groups, [])  # created but never placed: not needed at all
        self.assertEqual(formulas, [])
        self.assertEqual(state.measure_sums(), ["매출"])

    def test_intermediate_rule_is_computed_but_not_output(self):
        state = make_state()
        state.add_column_group(ColumnGroupRule("총비용", ["비용1", "비용2"]))
        state.add_derived_formula(DerivedFormulaRule("비용율", "총비용", "매출"))
        state.place_value("비용율")

        groups, formulas = state.rules_for_spec()
        self.assertEqual([g.new_column for g in groups], ["총비용"])
        self.assertFalse(groups[0].output)  # needed for the ratio, kept out of the file
        self.assertTrue(formulas[0].output)

    def test_all_rules_with_output_keeps_unplaced_rules_for_presets(self):
        state = make_state()
        state.add_column_group(ColumnGroupRule("총비용", ["비용1"]))
        groups, _formulas = state.all_rules_with_output()

        self.assertEqual([g.new_column for g in groups], ["총비용"])
        self.assertFalse(groups[0].output)


class TestConfiguration(unittest.TestCase):
    def test_apply_configuration_round_trips_a_preset(self):
        state = make_state()
        state.apply_configuration(
            group_keys=["디비전"],
            measure_sums=["매출"],
            column_groups=[ColumnGroupRule("총비용", ["비용1", "비용2"], output=True)],
            derived_formulas=[],
            filters=[FilterCondition("국가", "==", "KR")],
            value_order=["총비용", "매출"],
        )

        self.assertEqual(state.group_keys, ["디비전"])
        self.assertEqual(state.values, ["총비용", "매출"])
        self.assertEqual(state.measure_sums(), ["매출"])
        self.assertEqual(len(state.filters), 1)
        self.assertNotIn("디비전", [f.name for f in state.dimension_pool])

    def test_apply_configuration_clears_the_previous_setup(self):
        state = make_state()
        state.place_group_key("국가")
        state.place_value("수량")
        state.add_column_group(ColumnGroupRule("총비용", ["비용1"]))

        state.apply_configuration(
            group_keys=["디비전"],
            measure_sums=["매출"],
            column_groups=[],
            derived_formulas=[],
            filters=[],
        )

        self.assertEqual(state.group_keys, ["디비전"])
        self.assertEqual(state.values, ["매출"])
        self.assertEqual(state.column_groups, [])
        self.assertIn("국가", [f.name for f in state.dimension_pool])
        self.assertNotIn("총비용", [f.name for f in state.measure_pool])

    def test_preset_columns_missing_from_the_schema_are_still_shown(self):
        state = make_state()
        state.apply_configuration(
            group_keys=["없는컬럼"],
            measure_sums=[],
            column_groups=[],
            derived_formulas=[],
            filters=[],
        )

        self.assertEqual(state.group_keys, ["없는컬럼"])
        self.assertTrue(state.is_known("없는컬럼"))

    def test_preset_keeps_a_row_key_the_schema_read_as_a_measure(self):
        state = AggregatorFieldState()
        state.load_schema(
            ["월"], ["모델코드", "매출"], month_column="월", columns=["월", "모델코드", "매출"]
        )

        state.apply_configuration(
            group_keys=["모델코드"],
            measure_sums=["매출"],
            column_groups=[],
            derived_formulas=[],
            filters=[],
        )

        self.assertEqual(state.group_keys, ["모델코드"])
        self.assertEqual(state.home_of("모델코드"), DIMENSION)
        self.assertNotIn("모델코드", [f.name for f in state.measure_pool])

    def test_preset_keeps_a_value_the_schema_read_as_a_dimension(self):
        state = make_state()

        state.apply_configuration(
            group_keys=["디비전"],
            measure_sums=["거래선"],
            column_groups=[],
            derived_formulas=[],
            filters=[],
        )

        self.assertEqual(state.values, ["거래선"])
        self.assertEqual(state.home_of("거래선"), MEASURE)

    def test_a_later_preset_moves_the_column_back(self):
        state = make_state()
        state.apply_configuration(
            group_keys=["수량"],
            measure_sums=["매출"],
            column_groups=[],
            derived_formulas=[],
            filters=[],
        )
        self.assertEqual(state.home_of("수량"), DIMENSION)

        state.apply_configuration(
            group_keys=["디비전"],
            measure_sums=["수량"],
            column_groups=[],
            derived_formulas=[],
            filters=[],
        )

        self.assertEqual(state.group_keys, ["디비전"])
        self.assertEqual(state.values, ["수량"])
        self.assertEqual(state.home_of("수량"), MEASURE)

    def test_a_row_key_named_after_a_rule_column_does_not_swallow_the_rule(self):
        """A hand-edited preset must not make a rule vanish silently."""
        state = make_state()
        state.apply_configuration(
            group_keys=["디비전", "총비용"],  # 총비용 is also produced by the rule below
            measure_sums=[],
            column_groups=[ColumnGroupRule("총비용", ["비용1", "비용2"], output=True)],
            derived_formulas=[],
            filters=[],
        )

        self.assertEqual([r.new_column for r in state.column_groups], ["총비용"])
        self.assertTrue(state.is_derived("총비용"))
        self.assertEqual(state.group_keys, ["디비전"])
        self.assertEqual(state.values, ["총비용"])

    def test_filters_add_and_remove(self):
        state = make_state()
        state.add_filter(FilterCondition("디비전", "==", "Mobile"))
        self.assertEqual(len(state.filters), 1)

        self.assertTrue(state.remove_filter(0))
        self.assertEqual(state.filters, [])
        self.assertFalse(state.remove_filter(0))


class TestUndo(unittest.TestCase):
    """The caller snapshots just before a mutating action; undo puts it back."""

    def test_undo_restores_a_placement(self):
        state = make_state()
        state.snapshot()
        state.place_group_key("디비전")

        self.assertTrue(state.can_undo())
        self.assertTrue(state.undo())
        self.assertEqual(state.group_keys, [])
        self.assertIn("디비전", [f.name for f in state.dimension_pool])
        self.assertFalse(state.can_undo())

    def test_undo_restores_an_unplaced_field(self):
        state = make_state()
        state.place_value("매출")
        state.snapshot()
        state.unplace("매출")

        self.assertTrue(state.undo())
        self.assertEqual(state.values, ["매출"])
        self.assertNotIn("매출", [f.name for f in state.measure_pool])

    def test_undo_restores_pool_order_not_just_membership(self):
        state = make_state()
        state.add_column_group(ColumnGroupRule("총비용", ["비용1", "비용2"]))
        state.snapshot()
        state.place_group_key("국가")
        state.place_value("수량")
        state.place_value("총비용")

        self.assertTrue(state.undo())

        # 국가 goes back between 월 and 거래선, not appended at the end.
        self.assertEqual([f.name for f in state.dimension_pool], POOLED_DIMENSIONS)
        self.assertEqual([f.name for f in state.measure_pool], MEASURES + ["총비용"])

    def test_undo_restores_a_reorder(self):
        state = make_state()
        state.place_value("수량")
        state.place_value("매출")
        state.snapshot()
        state.reorder(VALUES, 1, 0)

        self.assertEqual(state.values, ["매출", "수량"])
        self.assertTrue(state.undo())
        self.assertEqual(state.values, ["수량", "매출"])

    def test_undo_removes_an_added_rule(self):
        state = make_state()
        state.snapshot()
        state.add_column_group(ColumnGroupRule("총비용", ["비용1", "비용2"]))

        self.assertTrue(state.undo())
        self.assertEqual(state.column_groups, [])
        self.assertFalse(state.is_known("총비용"))
        self.assertEqual([f.name for f in state.measure_pool], MEASURES)

    def test_undo_brings_back_a_cascaded_rule_deletion(self):
        state = make_state()
        state.add_column_group(ColumnGroupRule("총비용", ["비용1", "비용2"]))
        state.add_derived_formula(DerivedFormulaRule("비용율", "총비용", "매출"))
        state.place_value("비용율")
        state.snapshot()

        removed = state.remove_derived("총비용")
        self.assertEqual(sorted(removed), sorted(["총비용", "비용율"]))

        self.assertTrue(state.undo())
        self.assertEqual([r.new_column for r in state.column_groups], ["총비용"])
        self.assertEqual([r.new_column for r in state.derived_formulas], ["비용율"])
        self.assertEqual(state.values, ["비용율"])
        self.assertEqual([f.name for f in state.measure_pool], MEASURES + ["총비용"])

    def test_undo_restores_filters(self):
        state = make_state()
        state.add_filter(FilterCondition("국가", "==", "KR"))
        state.snapshot()
        state.add_filter(FilterCondition("디비전", "==", "Mobile"))

        self.assertTrue(state.undo())
        self.assertEqual([f.column for f in state.filters], ["국가"])

        state.snapshot()
        state.remove_filter(0)
        self.assertTrue(state.undo())
        self.assertEqual([f.column for f in state.filters], ["국가"])

    def test_undo_restores_a_constant_column(self):
        state = make_state()
        state.snapshot()
        state.add_constant_column("YYYY", "2026")

        self.assertTrue(state.undo())
        self.assertEqual(state.constant_columns, {})
        self.assertFalse(state.is_known("YYYY"))
        self.assertEqual([f.name for f in state.dimension_pool], POOLED_DIMENSIONS)

    def test_undo_restores_a_removed_constant_column(self):
        state = make_state()
        state.add_constant_column("YYYY", "2026")
        state.place_group_key("YYYY")
        state.snapshot()
        state.remove_constant_column("YYYY")

        self.assertTrue(state.undo())
        self.assertEqual(state.group_keys, ["YYYY"])
        self.assertEqual(state.constant_columns, {"YYYY": "2026"})

    def test_undo_restores_a_measure_function(self):
        state = make_state()
        state.place_value("매출")
        state.set_measure_function("매출", "mean")
        state.snapshot()
        state.set_measure_function("매출", "count")

        self.assertTrue(state.undo())
        self.assertEqual(state.measure_function("매출"), "mean")

    def test_undo_on_an_empty_stack_returns_false_and_changes_nothing(self):
        state = make_state()
        state.place_group_key("디비전")
        pool = [f.name for f in state.dimension_pool]

        self.assertFalse(state.can_undo())
        self.assertFalse(state.undo())
        self.assertEqual(state.group_keys, ["디비전"])
        self.assertEqual([f.name for f in state.dimension_pool], pool)

    def test_an_unchanged_snapshot_does_not_pile_up(self):
        state = make_state()
        state.place_value("매출")
        state.snapshot()
        state.snapshot()
        state.snapshot()
        state.place_value("수량")

        self.assertTrue(state.undo())
        self.assertEqual(state.values, ["매출"])
        self.assertFalse(state.can_undo())

    def test_the_stack_drops_the_oldest_entry_beyond_its_cap(self):
        state = make_state()
        state.add_constant_column("C0", "0")
        for index in range(1, 26):
            state.snapshot()
            state.add_constant_column("C%d" % index, str(index))

        steps = 0
        while state.undo():
            steps += 1

        self.assertEqual(steps, 20)  # the cap, not the 25 changes
        self.assertFalse(state.can_undo())
        # The five oldest steps fell off the bottom, so their columns stay.
        self.assertEqual(sorted(state.constant_columns), ["C%d" % i for i in range(6)])

    def test_load_schema_clears_the_stack(self):
        state = make_state()
        state.snapshot()
        state.place_group_key("디비전")
        self.assertTrue(state.can_undo())

        state.load_schema(["국가"], ["매출"], month_column=None, columns=["국가", "매출"])

        self.assertFalse(state.can_undo())
        self.assertFalse(state.undo())
        self.assertEqual([f.name for f in state.dimension_pool], ["국가"])

    def test_reset_clears_the_stack(self):
        state = make_state()
        state.snapshot()
        state.place_group_key("디비전")

        state.reset()

        self.assertFalse(state.can_undo())
        self.assertEqual(state.group_keys, [])

    def test_clear_undo_keeps_the_configuration(self):
        state = make_state()
        state.snapshot()
        state.place_group_key("디비전")

        state.clear_undo()

        self.assertFalse(state.can_undo())
        self.assertEqual(state.group_keys, ["디비전"])

    def test_a_preset_application_undoes_as_one_step(self):
        state = make_state()
        state.place_group_key("국가")
        state.place_value("매출")
        state.snapshot()  # the UI snapshots before the action, as with every other

        state.apply_configuration(
            group_keys=["디비전"],
            measure_sums=["수량"],
            column_groups=[ColumnGroupRule("총비용", ["비용1", "비용2"], output=True)],
            derived_formulas=[],
            filters=[FilterCondition("국가", "==", "KR")],
        )

        self.assertTrue(state.undo())
        self.assertEqual(state.group_keys, ["국가"])
        self.assertEqual(state.values, ["매출"])
        self.assertEqual(state.column_groups, [])
        self.assertEqual(state.filters, [])
        self.assertFalse(state.can_undo())  # the caller's snapshot was not duplicated

    def test_a_preset_is_undoable_even_without_a_caller_snapshot(self):
        state = make_state()
        state.place_value("매출")

        state.apply_configuration(
            group_keys=["디비전"], measure_sums=["수량"], column_groups=[],
            derived_formulas=[], filters=[],
        )

        self.assertTrue(state.can_undo())
        self.assertTrue(state.undo())
        self.assertEqual(state.values, ["매출"])
        self.assertEqual(state.group_keys, [])

    def test_a_snapshot_does_not_alias_the_live_configuration(self):
        state = make_state()
        state.add_column_group(ColumnGroupRule("총비용", ["비용1"]))
        state.snapshot()
        state.column_groups[0].source_columns.append("비용2")  # mutable list in a frozen rule

        self.assertTrue(state.undo())
        self.assertEqual(state.column_groups[0].source_columns, ["비용1"])


class TestMeasureFunctions(unittest.TestCase):
    """Each value column carries how it is aggregated; absent means "sum"."""

    def test_the_engine_vocabulary_is_fixed(self):
        self.assertEqual(MEASURE_FUNCTIONS, ("sum", "mean", "count", "min", "max"))
        self.assertEqual(DEFAULT_MEASURE_FUNCTION, "sum")

    def test_an_unset_column_sums(self):
        state = make_state()
        self.assertEqual(state.measure_function("매출"), "sum")
        self.assertEqual(state.measure_function("없는컬럼"), "sum")

    def test_a_measure_takes_every_known_function(self):
        state = make_state()
        state.set_measure_function("매출", MEASURE_FUNCTIONS[-1])  # so the first choice below is a change
        for function in MEASURE_FUNCTIONS:
            self.assertTrue(state.set_measure_function("매출", function))
            self.assertEqual(state.measure_function("매출"), function)

    def test_choosing_the_function_already_in_effect_changes_nothing(self):
        """The UI snapshots before every choice; a no-op must say so or Ctrl+Z gets a dead step."""
        state = make_state()

        self.assertFalse(state.set_measure_function("매출", "sum"))  # sum is already the default
        state.set_measure_function("매출", "mean")
        self.assertFalse(state.set_measure_function("매출", "mean"))
        self.assertEqual(state.measure_function("매출"), "mean")

    def test_an_unknown_function_is_rejected(self):
        state = make_state()
        state.set_measure_function("매출", "mean")

        self.assertFalse(state.set_measure_function("매출", "median"))
        self.assertFalse(state.set_measure_function("매출", "SUM"))
        self.assertFalse(state.set_measure_function("매출", ""))
        self.assertEqual(state.measure_function("매출"), "mean")

    def test_a_dimension_has_nothing_to_aggregate(self):
        state = make_state()
        state.add_constant_column("YYYY", "2026")

        self.assertFalse(state.set_measure_function("디비전", "mean"))
        self.assertFalse(state.set_measure_function("연도", "count"))
        self.assertFalse(state.set_measure_function("YYYY", "count"))
        self.assertFalse(state.set_measure_function("없는컬럼", "mean"))
        self.assertEqual(state.measure_functions, {})

    def test_a_rule_column_is_computed_not_aggregated(self):
        state = make_state()
        state.add_column_group(ColumnGroupRule("총비용", ["비용1", "비용2"]))
        state.add_derived_formula(DerivedFormulaRule("비용율", "총비용", "매출"))
        state.place_value("총비용")
        state.place_value("비용율")

        self.assertFalse(state.set_measure_function("총비용", "mean"))
        self.assertFalse(state.set_measure_function("비용율", "max"))
        self.assertEqual(state.measure_functions_for_spec(), {})

    def test_a_dimension_turned_measure_may_be_counted(self):
        state = make_state()
        state.reclassify("거래선", MEASURE)
        state.place_value("거래선")

        self.assertTrue(state.set_measure_function("거래선", "count"))
        self.assertEqual(state.measure_functions_for_spec(), {"거래선": "count"})

    def test_the_spec_omits_defaults_and_unplaced_columns(self):
        state = make_state()
        state.place_value("매출")
        state.place_value("수량")
        state.set_measure_function("수량", "mean")
        state.set_measure_function("비용1", "max")  # chosen but never placed

        self.assertEqual(state.measure_functions_for_spec(), {"수량": "mean"})

    def test_a_default_configuration_produces_an_empty_dict(self):
        state = make_state()
        state.place_value("매출")
        state.place_value("수량")

        self.assertEqual(state.measure_functions_for_spec(), {})

    def test_choosing_sum_again_clears_the_entry(self):
        state = make_state()
        state.place_value("매출")
        state.set_measure_function("매출", "count")

        self.assertTrue(state.set_measure_function("매출", "sum"))
        self.assertEqual(state.measure_function("매출"), "sum")
        self.assertEqual(state.measure_functions, {})

    def test_the_choice_survives_moving_the_field_out_and_back(self):
        state = make_state()
        state.place_value("매출")
        state.set_measure_function("매출", "mean")

        self.assertTrue(state.unplace("매출"))
        self.assertEqual(state.measure_function("매출"), "mean")
        self.assertEqual(state.measure_functions_for_spec(), {})  # not placed right now

        self.assertTrue(state.place_value("매출"))
        self.assertEqual(state.measure_functions_for_spec(), {"매출": "mean"})

    def test_reclassifying_to_a_dimension_drops_the_choice(self):
        state = make_state()
        state.set_measure_function("매출", "mean")

        self.assertTrue(state.reclassify("매출", DIMENSION))
        self.assertEqual(state.measure_function("매출"), "sum")

    def test_apply_configuration_restores_and_clears_choices(self):
        state = make_state()
        state.place_value("비용1")
        state.set_measure_function("비용1", "max")

        state.apply_configuration(
            group_keys=["디비전"],
            measure_sums=["매출", "수량"],
            column_groups=[],
            derived_formulas=[],
            filters=[],
            measure_functions={"수량": "mean"},
        )

        self.assertEqual(state.measure_function("비용1"), "sum")  # the leftover is gone
        self.assertEqual(state.measure_function("수량"), "mean")
        self.assertEqual(state.measure_functions_for_spec(), {"수량": "mean"})

    def test_apply_configuration_drops_a_choice_that_no_longer_applies(self):
        state = make_state()

        state.apply_configuration(
            group_keys=["디비전"],
            measure_sums=["매출"],
            column_groups=[ColumnGroupRule("총비용", ["비용1"], output=True)],
            derived_formulas=[],
            filters=[],
            measure_functions={"총비용": "mean", "디비전": "count", "매출": "median"},
        )

        self.assertEqual(state.measure_functions_for_spec(), {})


if __name__ == "__main__":
    unittest.main()
