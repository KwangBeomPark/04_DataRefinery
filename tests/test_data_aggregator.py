"""Unit tests for data_aggregator module."""

import os
import re
import tempfile
import threading
import unittest
import pandas as pd
from openpyxl import load_workbook

from src.data_aggregator import (
    AGGREGATION_FUNCTIONS,
    AggregationCancelledError,
    AggregationSpec,
    AggregatorError,
    ColumnGroupRule,
    ColumnNotFoundError,
    DerivedFormulaRule,
    EmptyResultError,
    FilterCondition,
    aggregate_dataset,
    default_output_path,
    estimate_result_rows,
    format_preview_rows,
    format_preview_text,
    inspect_dataset_schema,
    preview_aggregation,
)


def _data_cell_formats(worksheet, column_name: str) -> set:
    """Number formats used by the data cells (row 2 downwards) of one named column."""
    headers = [cell.value for cell in worksheet[1]]
    index = headers.index(column_name) + 1
    return {
        worksheet.cell(row=row, column=index).number_format
        for row in range(2, worksheet.max_row + 1)
    }


class TestDataAggregator(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.csv_path = os.path.join(self.temp_dir.name, "sample_financial.csv")

        # Create realistic financial ledger data with multiple years (2025 & 2026), divisions, models
        data = [
            # month, division, customer, model, sales, cost1, cost2, cost3, profit
            ["202511", "TV", "BestBuy", "OLED65", "500", "250", "50", "25", "175"],
            ["202512", "TV", "Amazon", "OLED65", "1500", "750", "150", "75", "525"],
            ["202601", "TV", "BestBuy", "OLED65", "1000", "500", "100", "50", "350"],
            ["202601", "TV", "Amazon", "OLED65", "2000", "1000", "200", "100", "700"],
            ["202601", "TV", "BestBuy", "OLED55", "800", "400", "80", "40", "280"],
            ["202602", "TV", "BestBuy", "OLED65", "1500", "750", "150", "75", "525"],
            ["202602", "Mobile", "Verizon", "GalaxyS", "3000", "1500", "300", "100", "1100"],
            ["202603", "TV", "Amazon", "OLED65", "2500", "1250", "250", "100", "900"],
            ["202603", "Mobile", "AT&T", "GalaxyS", "4000", "2000", "400", "150", "1450"],
            # Row with 0 sales for division test
            ["202604", "TV", "Costco", "OLED77", "0", "100", "20", "10", "-130"],
        ]
        columns = ["월", "디비전", "거래선", "모델", "매출", "비용1", "비용2", "비용3", "영업이익"]

        df = pd.DataFrame(data, columns=columns)
        df.to_csv(self.csv_path, index=False, encoding="utf-8-sig")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_inspect_dataset_schema(self):
        schema = inspect_dataset_schema(self.csv_path)

        self.assertIn("월", schema.columns)
        self.assertIn("디비전", schema.columns)
        self.assertIn("매출", schema.columns)
        self.assertEqual(schema.detected_month_column, "월")
        self.assertIn("월", schema.dimension_candidates)
        self.assertIn("디비전", schema.dimension_candidates)
        self.assertIn("모델", schema.dimension_candidates)
        self.assertIn("매출", schema.measure_candidates)
        self.assertIn("비용1", schema.measure_candidates)
        self.assertIn("TV", schema.sample_values["디비전"])

    def test_multi_year_rollup_and_key_exclusion(self):
        # Filter for TV, rollup month to year, exclude '거래선', aggregate model level
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전", "모델"],
            measure_sums=["매출", "영업이익"],
            filters=[FilterCondition(column="디비전", operator="==", value="TV")],
            rollup_annual=True,
            month_column="월",
            annual_column_name="연도",
            output_format="csv",
            chunksize=3,  # small chunk to test multi-chunk aggregation
        )

        out_path = aggregate_dataset(spec)
        self.assertTrue(os.path.exists(out_path))

        res_df = pd.read_csv(out_path)
        self.assertIn("연도", res_df.columns)
        self.assertIn("디비전", res_df.columns)
        self.assertIn("모델", res_df.columns)
        self.assertNotIn("거래선", res_df.columns)

        # 2025 OLED65 total sales: 500 + 1500 = 2000
        oled65_2025 = res_df[(res_df["연도"] == 2025) & (res_df["모델"] == "OLED65")].iloc[0]
        self.assertEqual(oled65_2025["매출"], 2000.0)
        self.assertEqual(oled65_2025["영업이익"], 700.0)

        # 2026 OLED65 total sales: 1000 + 2000 + 1500 + 2500 = 7000
        oled65_2026 = res_df[(res_df["연도"] == 2026) & (res_df["모델"] == "OLED65")].iloc[0]
        self.assertEqual(oled65_2026["매출"], 7000.0)
        self.assertEqual(oled65_2026["영업이익"], 2475.0)

    def test_column_groups_and_derived_formulas(self):
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전", "모델"],
            measure_sums=["매출", "영업이익"],
            column_groups=[
                ColumnGroupRule(new_column="직접비", source_columns=["비용1", "비용2"]),
            ],
            derived_formulas=[
                DerivedFormulaRule(
                    new_column="이익율(%)",
                    numerator_column="영업이익",
                    denominator_column="매출",
                ),
                DerivedFormulaRule(
                    new_column="직접비율(%)",
                    numerator_column="직접비",
                    denominator_column="매출",
                ),
            ],
            output_format="csv",
            chunksize=4,
        )

        out_path = aggregate_dataset(spec)
        res_df = pd.read_csv(out_path)

        self.assertIn("직접비", res_df.columns)
        self.assertIn("이익율(%)", res_df.columns)
        self.assertIn("직접비율(%)", res_df.columns)

        # OLED65: 직접비 = 5400 (all rows), 매출 = 9000.
        # Percent columns store the ratio (0.6), Excel renders it as 60.00%.
        oled65 = res_df[res_df["모델"] == "OLED65"].iloc[0]
        self.assertEqual(oled65["매출"], 9000.0)
        self.assertAlmostEqual(oled65["이익율(%)"], 3175.0 / 9000.0, places=6)
        self.assertAlmostEqual(oled65["직접비율(%)"], oled65["직접비"] / 9000.0, places=6)
        self.assertAlmostEqual(oled65["직접비율(%)"], 0.6, places=6)

        # OLED77 has sales 0 -> 이익율 must be 0.0, not NaN or error
        oled77 = res_df[res_df["모델"] == "OLED77"].iloc[0]
        self.assertEqual(oled77["매출"], 0.0)
        self.assertEqual(oled77["이익율(%)"], 0.0)

    def test_intermediate_rule_is_calculated_but_not_written(self):
        """A rule flagged output=False feeds a formula without reaching the file."""
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전"],
            measure_sums=["매출"],
            column_groups=[
                ColumnGroupRule(new_column="직접비", source_columns=["비용1", "비용2"], output=False),
            ],
            derived_formulas=[
                DerivedFormulaRule(
                    new_column="직접비율(%)",
                    numerator_column="직접비",
                    denominator_column="매출",
                ),
            ],
            output_format="csv",
            chunksize=4,
        )

        res_df = pd.read_csv(aggregate_dataset(spec))

        self.assertNotIn("직접비", res_df.columns)
        self.assertIn("직접비율(%)", res_df.columns)

        tv = res_df[res_df["디비전"] == "TV"].iloc[0]
        self.assertGreater(tv["직접비율(%)"], 0.0)

    def test_output_order_controls_value_columns(self):
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전"],
            measure_sums=["매출", "영업이익"],
            column_groups=[ColumnGroupRule(new_column="직접비", source_columns=["비용1", "비용2"])],
            output_order=["직접비", "영업이익", "매출"],
            output_format="csv",
            chunksize=4,
        )

        res_df = pd.read_csv(aggregate_dataset(spec))

        self.assertEqual(list(res_df.columns), ["디비전", "직접비", "영업이익", "매출"])

    def test_output_order_is_optional(self):
        """Without output_order the declaration order is preserved."""
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전"],
            measure_sums=["매출", "영업이익"],
            column_groups=[ColumnGroupRule(new_column="직접비", source_columns=["비용1", "비용2"])],
            output_format="csv",
            chunksize=4,
        )

        res_df = pd.read_csv(aggregate_dataset(spec))

        self.assertEqual(list(res_df.columns), ["디비전", "매출", "영업이익", "직접비"])

    def test_preview_honours_output_flags(self):
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전"],
            measure_sums=["매출"],
            column_groups=[
                ColumnGroupRule(new_column="직접비", source_columns=["비용1", "비용2"], output=False),
            ],
            derived_formulas=[
                DerivedFormulaRule("직접비율(%)", "직접비", "매출"),
            ],
            output_format="csv",
        )

        preview_df, _text = preview_aggregation(spec, sample_rows=100)

        self.assertNotIn("직접비", preview_df.columns)
        self.assertIn("직접비율(%)", preview_df.columns)

    def test_annual_column_may_be_listed_in_group_by_keys(self):
        """The UI puts 연도 in the row area itself, so the engine must not go
        looking for it in the source file."""
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["연도", "디비전"],
            measure_sums=["매출"],
            rollup_annual=True,
            month_column="월",
            output_format="csv",
            chunksize=4,
        )

        res_df = pd.read_csv(aggregate_dataset(spec))

        self.assertEqual(list(res_df.columns), ["연도", "디비전", "매출"])
        self.assertEqual(sorted(res_df["연도"].unique().tolist()), [2025, 2026])

    def test_annual_column_position_is_honoured(self):
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전", "연도"],
            measure_sums=["매출"],
            rollup_annual=True,
            month_column="월",
            output_format="csv",
            chunksize=4,
        )

        res_df = pd.read_csv(aggregate_dataset(spec))

        self.assertEqual(list(res_df.columns), ["디비전", "연도", "매출"])

    def test_constant_and_annual_columns_together(self):
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["YYYY", "연도", "디비전"],
            measure_sums=["매출"],
            constant_columns={"YYYY": "2026"},
            rollup_annual=True,
            month_column="월",
            output_format="csv",
            chunksize=4,
        )

        res_df = pd.read_csv(aggregate_dataset(spec))

        self.assertEqual(list(res_df.columns), ["YYYY", "연도", "디비전", "매출"])
        self.assertEqual(set(res_df["YYYY"].astype(str)), {"2026"})
        self.assertEqual(sorted(res_df["연도"].unique().tolist()), [2025, 2026])

    def test_xlsx_export(self):
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전"],
            measure_sums=["매출"],
            output_format="xlsx",
        )

        out_path = aggregate_dataset(spec)
        self.assertTrue(out_path.endswith(".xlsx"))
        self.assertTrue(os.path.exists(out_path))

        excel_df = pd.read_excel(out_path, sheet_name="Aggregated")
        self.assertEqual(len(excel_df), 2)  # TV, Mobile
        tv_sales = excel_df[excel_df["디비전"] == "TV"]["매출"].iloc[0]
        self.assertEqual(tv_sales, 9800.0)

    def test_percent_column_stores_ratio_not_scaled_value(self):
        """A percent column holds 0.2747, not 27.47 - Excel's format does the scaling."""
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전"],
            measure_sums=["매출", "영업이익"],
            derived_formulas=[
                DerivedFormulaRule("이익율(%)", "영업이익", "매출"),
            ],
            output_format="csv",
        )

        res_df = pd.read_csv(aggregate_dataset(spec))
        tv = res_df[res_df["디비전"] == "TV"].iloc[0]

        # TV: 영업이익 3325 / 매출 9800 = 0.3393...
        self.assertAlmostEqual(tv["이익율(%)"], 3325.0 / 9800.0, places=6)
        self.assertLess(tv["이익율(%)"], 1.0)

    def test_multiplier_scales_every_format_type_uniformly(self):
        """multiplier is never silently ignored: a percent rule with x100 still stores x100."""
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전"],
            measure_sums=["매출", "영업이익"],
            derived_formulas=[
                DerivedFormulaRule("이익율(%)", "영업이익", "매출", multiplier=100.0),
            ],
            output_format="csv",
        )

        res_df = pd.read_csv(aggregate_dataset(spec))
        tv = res_df[res_df["디비전"] == "TV"].iloc[0]

        self.assertAlmostEqual(tv["이익율(%)"], (3325.0 / 9800.0) * 100.0, places=4)

    def test_xlsx_number_formats_by_column_kind(self):
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전"],
            measure_sums=["매출", "영업이익"],
            column_groups=[ColumnGroupRule("직접비", ["비용1", "비용2"])],
            derived_formulas=[
                DerivedFormulaRule("이익율(%)", "영업이익", "매출"),
            ],
            output_format="xlsx",
        )

        res = aggregate_dataset(spec)
        worksheet = load_workbook(res.out_path)["Aggregated"]

        self.assertEqual(_data_cell_formats(worksheet, "이익율(%)"), {"0.00%"})
        self.assertEqual(_data_cell_formats(worksheet, "매출"), {"#,##0.##"})
        self.assertEqual(_data_cell_formats(worksheet, "영업이익"), {"#,##0.##"})
        self.assertEqual(_data_cell_formats(worksheet, "직접비"), {"#,##0.##"})
        # Group keys and the header row keep the General format
        self.assertEqual(_data_cell_formats(worksheet, "디비전"), {"General"})
        self.assertEqual({cell.number_format for cell in worksheet[1]}, {"General"})

        excel_df = pd.read_excel(res.out_path, sheet_name="Aggregated")
        tv = excel_df[excel_df["디비전"] == "TV"].iloc[0]
        self.assertAlmostEqual(tv["이익율(%)"], 3325.0 / 9800.0, places=6)

    def test_ratio_and_number_format_types(self):
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전"],
            measure_sums=["매출", "영업이익"],
            derived_formulas=[
                DerivedFormulaRule("이익배수", "영업이익", "매출", format_type="ratio"),
                DerivedFormulaRule(
                    "이익지수", "영업이익", "매출", multiplier=1000.0, format_type="number"
                ),
            ],
            output_format="xlsx",
        )

        res = aggregate_dataset(spec)
        worksheet = load_workbook(res.out_path)["Aggregated"]

        self.assertEqual(_data_cell_formats(worksheet, "이익배수"), {"0.000"})
        self.assertEqual(_data_cell_formats(worksheet, "이익지수"), {"General"})

        excel_df = pd.read_excel(res.out_path, sheet_name="Aggregated")
        tv = excel_df[excel_df["디비전"] == "TV"].iloc[0]
        self.assertAlmostEqual(tv["이익배수"], 3325.0 / 9800.0, places=6)
        self.assertAlmostEqual(tv["이익지수"], (3325.0 / 9800.0) * 1000.0, places=4)

    def test_default_output_path_shape(self):
        xlsx_path = default_output_path(self.csv_path, "xlsx")
        csv_path = default_output_path(self.csv_path, "csv")

        self.assertEqual(
            os.path.dirname(xlsx_path),
            os.path.dirname(os.path.abspath(self.csv_path)),
        )
        self.assertRegex(
            os.path.basename(xlsx_path),
            r"^sample_financial_aggregated_\d{8}_\d{6}\.xlsx$",
        )
        self.assertRegex(
            os.path.basename(csv_path),
            r"^sample_financial_aggregated_\d{8}_\d{6}\.csv$",
        )

    def test_aggregate_dataset_uses_default_output_path(self):
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전"],
            measure_sums=["매출"],
            output_format="csv",
        )

        res = aggregate_dataset(spec)

        self.assertIsNotNone(
            re.fullmatch(r"sample_financial_aggregated_\d{8}_\d{6}\.csv", os.path.basename(res.out_path))
        )

    def test_explicit_output_path_is_honoured(self):
        target = os.path.join(self.temp_dir.name, "custom_result.csv")
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전"],
            measure_sums=["매출"],
            output_format="csv",
            output_path=target,
        )

        res = aggregate_dataset(spec)

        self.assertEqual(res.out_path, os.path.abspath(target))
        self.assertTrue(os.path.exists(target))

    def test_filter_operators(self):
        # Test 'not in' with scalar, 'in' with list, and 'contains'
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["거래선"],
            measure_sums=["매출"],
            filters=[
                FilterCondition(column="거래선", operator="not in", value="BestBuy"),
                FilterCondition(column="거래선", operator="in", value=["Amazon", "Costco"]),
            ],
            output_format="csv",
        )

        out_path = aggregate_dataset(spec)
        res_df = pd.read_csv(out_path)
        customers = set(res_df["거래선"])
        self.assertNotIn("BestBuy", customers)
        self.assertIn("Amazon", customers)
        self.assertIn("Costco", customers)

    def test_polish_number_mode(self):
        polish_csv = os.path.join(self.temp_dir.name, "polish_sample.csv")
        p_data = [
            ["TV", "1 234,50"],
            ["TV", "2 000,00"],
        ]
        p_df = pd.DataFrame(p_data, columns=["디비전", "매출"])
        p_df.to_csv(polish_csv, index=False, encoding="utf-8")

        spec = AggregationSpec(
            file_path=polish_csv,
            group_by_keys=["디비전"],
            measure_sums=["매출"],
            number_mode="Polish",
            output_format="csv",
        )
        out_path = aggregate_dataset(spec)
        res_df = pd.read_csv(out_path)
        self.assertEqual(res_df["매출"].iloc[0], 3234.50)

    def test_mid_stream_cancellation(self):
        cancel_evt = threading.Event()

        def progress_cb(cur, total, msg):
            if cur >= 2:
                cancel_evt.set()

        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전"],
            measure_sums=["매출"],
            chunksize=2,  # Many chunks
        )

        with self.assertRaises(AggregationCancelledError):
            aggregate_dataset(spec, progress_callback=progress_cb, cancel_event=cancel_evt)

    def test_missing_column_error(self):
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["없는컬럼"],
            measure_sums=["매출"],
        )

        with self.assertRaises(ColumnNotFoundError):
            aggregate_dataset(spec)

    def test_preview_aggregation(self):
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전"],
            measure_sums=["매출"],
            column_groups=[ColumnGroupRule("직접비", ["비용1", "비용2"])],
            derived_formulas=[DerivedFormulaRule("이익율(%)", "영업이익", "매출")],
        )
        prev_df, prev_text = preview_aggregation(spec, sample_rows=10)
        self.assertIsInstance(prev_df, pd.DataFrame)
        self.assertIn("디비전", prev_df.columns)
        self.assertIn("직접비", prev_df.columns)
        self.assertIn("이익율(%)", prev_df.columns)
        self.assertIn("TV", prev_text)

    def test_coerced_numbers_count_tracking(self):
        corrupt_csv = os.path.join(self.temp_dir.name, "corrupt_data.csv")
        data = [
            ["TV", "1000", "500"],
            ["TV", "N/A", "200"],      # Corrupt number in sales
            ["Mobile", "2000", "오류"], # Corrupt number in cost
        ]
        df = pd.DataFrame(data, columns=["디비전", "매출", "비용1"])
        df.to_csv(corrupt_csv, index=False, encoding="utf-8")

        spec = AggregationSpec(
            file_path=corrupt_csv,
            group_by_keys=["디비전"],
            measure_sums=["매출", "비용1"],
            output_format="csv",
        )
        res = aggregate_dataset(spec)
        self.assertEqual(res.coerced_numbers_count, 2)
        res_df = pd.read_csv(res.out_path)
        tv_row = res_df[res_df["디비전"] == "TV"].iloc[0]
        self.assertEqual(tv_row["매출"], 1000.0)  # 1000 + 0
        self.assertEqual(tv_row["비용1"], 700.0)  # 500 + 200


class TestConstantColumns(unittest.TestCase):
    """Literal group columns: a fixed label stamped onto every output row."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.csv_path = os.path.join(self.temp_dir.name, "sample_financial.csv")

        data = [
            ["202601", "TV", "OLED65", "1000", "350"],
            ["202601", "TV", "OLED55", "800", "280"],
            ["202602", "TV", "OLED65", "1500", "525"],
            ["202602", "Mobile", "GalaxyS", "3000", "1100"],
            ["202603", "Mobile", "GalaxyS", "4000", "1450"],
        ]
        columns = ["월", "디비전", "모델", "매출", "영업이익"]
        pd.DataFrame(data, columns=columns).to_csv(self.csv_path, index=False, encoding="utf-8-sig")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_constant_column_stamps_literal_at_its_group_key_position(self):
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전", "YYYY", "모델"],
            measure_sums=["매출"],
            constant_columns={"YYYY": "2026"},
            output_format="csv",
            chunksize=2,  # the literal must survive the multi-chunk fold
        )

        res_df = pd.read_csv(aggregate_dataset(spec), dtype=str)

        self.assertEqual(list(res_df.columns), ["디비전", "YYYY", "모델", "매출"])
        self.assertEqual(set(res_df["YYYY"]), {"2026"})
        # A constant adds no granularity: OLED65 still folds into a single row.
        oled65 = res_df[res_df["모델"] == "OLED65"]
        self.assertEqual(len(oled65), 1)
        self.assertEqual(float(oled65.iloc[0]["매출"]), 2500.0)

    def test_constant_column_is_never_looked_up_in_the_source(self):
        """The name is absent from the CSV header, which must not be a missing column."""
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["YYYY", "디비전"],
            measure_sums=["매출"],
            constant_columns={"YYYY": "2026"},
            output_format="csv",
        )

        res_df = pd.read_csv(aggregate_dataset(spec), dtype=str)

        self.assertEqual(list(res_df.columns), ["YYYY", "디비전", "매출"])
        self.assertEqual(sorted(set(res_df["디비전"])), ["Mobile", "TV"])

    def test_constant_column_absent_from_group_by_keys_is_appended(self):
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전"],
            measure_sums=["매출"],
            constant_columns={"YYYY": "2026"},
            output_format="csv",
        )

        res_df = pd.read_csv(aggregate_dataset(spec), dtype=str)

        self.assertEqual(list(res_df.columns), ["디비전", "YYYY", "매출"])

    def test_constant_column_clashing_with_source_column_raises(self):
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전"],
            measure_sums=["매출"],
            constant_columns={"디비전": "TV"},
            output_format="csv",
        )

        with self.assertRaises(AggregatorError) as ctx:
            aggregate_dataset(spec)

        # Shadowing real data is a spec conflict, not a missing column
        self.assertNotIsInstance(ctx.exception, ColumnNotFoundError)
        self.assertIn("디비전", str(ctx.exception))

    def test_constant_column_keeps_leading_zero_code(self):
        xlsx_spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["코드", "디비전"],
            measure_sums=["매출"],
            constant_columns={"코드": "007"},
            output_format="xlsx",
        )

        worksheet = load_workbook(aggregate_dataset(xlsx_spec).out_path)["Aggregated"]
        cells = {worksheet.cell(row=row, column=1).value for row in range(2, worksheet.max_row + 1)}
        self.assertEqual(cells, {"007"})  # text, not the number 7

        csv_spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["코드", "디비전"],
            measure_sums=["매출"],
            constant_columns={"코드": "007"},
            output_format="csv",
        )

        res_df = pd.read_csv(aggregate_dataset(csv_spec), dtype=str)
        self.assertEqual(set(res_df["코드"]), {"007"})

    def test_constant_column_uses_general_format_in_xlsx(self):
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["YYYY", "디비전"],
            measure_sums=["매출", "영업이익"],
            constant_columns={"YYYY": "2026"},
            output_format="xlsx",
        )

        worksheet = load_workbook(aggregate_dataset(spec).out_path)["Aggregated"]

        self.assertEqual(_data_cell_formats(worksheet, "YYYY"), {"General"})
        self.assertEqual(_data_cell_formats(worksheet, "매출"), {"#,##0.##"})
        self.assertEqual(_data_cell_formats(worksheet, "영업이익"), {"#,##0.##"})

    def test_preview_shows_constant_column(self):
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["YYYY", "디비전"],
            measure_sums=["매출"],
            constant_columns={"YYYY": "2026"},
        )

        prev_df, prev_text = preview_aggregation(spec, sample_rows=100)

        self.assertEqual(list(prev_df.columns), ["YYYY", "디비전", "매출"])
        self.assertEqual(set(prev_df["YYYY"]), {"2026"})
        self.assertIn("2026", prev_text)
        self.assertNotIn("2,026", prev_text)  # rendered as a label, never number-formatted

    def test_constant_column_named_like_annual_column_is_rejected_under_rollup(self):
        """The annual rollup writes 연도 itself, so a constant of that name would be overwritten."""
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["연도", "디비전"],
            measure_sums=["매출"],
            constant_columns={"연도": "2026"},
            rollup_annual=True,
            month_column="월",
            output_format="csv",
        )

        with self.assertRaises(AggregatorError) as ctx:
            aggregate_dataset(spec)

        self.assertIn("연도", str(ctx.exception))

    def test_constant_column_named_like_annual_column_is_allowed_without_rollup(self):
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["연도", "디비전"],
            measure_sums=["매출"],
            constant_columns={"연도": "2026"},
            output_format="csv",
        )

        res_df = pd.read_csv(aggregate_dataset(spec), dtype=str)

        self.assertEqual(list(res_df.columns), ["연도", "디비전", "매출"])
        self.assertEqual(set(res_df["연도"]), {"2026"})

    def test_empty_constant_columns_leaves_output_unchanged(self):
        """Regression guard: the default empty dict must not disturb an existing result."""
        baseline_path = os.path.join(self.temp_dir.name, "baseline.csv")
        explicit_path = os.path.join(self.temp_dir.name, "explicit_empty.csv")
        common = dict(
            file_path=self.csv_path,
            group_by_keys=["디비전", "모델"],
            measure_sums=["매출", "영업이익"],
            column_groups=[ColumnGroupRule("이익묶음", ["영업이익"])],
            derived_formulas=[DerivedFormulaRule("이익율(%)", "영업이익", "매출")],
            rollup_annual=True,
            month_column="월",
            output_format="csv",
            chunksize=2,
        )

        aggregate_dataset(AggregationSpec(output_path=baseline_path, **common))
        aggregate_dataset(AggregationSpec(output_path=explicit_path, constant_columns={}, **common))

        with open(baseline_path, "rb") as baseline, open(explicit_path, "rb") as explicit:
            self.assertEqual(baseline.read(), explicit.read())

        res_df = pd.read_csv(baseline_path)
        self.assertEqual(
            list(res_df.columns),
            ["연도", "디비전", "모델", "매출", "영업이익", "이익묶음", "이익율(%)"],
        )


class TestAggregationFunctions(unittest.TestCase):
    """A per-column reduction folded across chunks: sum, mean, count, min, max."""

    # TV holds eight rows of 매출, Mobile two. Every expectation below is hand-computed
    # from this table, and every spec uses a chunksize that forces several chunks.
    ROWS = [
        ["202511", "TV", "OLED65", "500", "250", "175"],
        ["202512", "TV", "OLED65", "1500", "750", "525"],
        ["202601", "TV", "OLED65", "1000", "500", "350"],
        ["202601", "TV", "OLED65", "2000", "1000", "700"],
        ["202601", "TV", "OLED55", "800", "400", "280"],
        ["202602", "TV", "OLED65", "1500", "750", "525"],
        ["202602", "Mobile", "GalaxyS", "3000", "1500", "1100"],
        ["202603", "TV", "OLED65", "2500", "1250", "900"],
        ["202603", "Mobile", "GalaxyS", "4000", "2000", "1450"],
        ["202604", "TV", "OLED77", "0", "100", "-130"],
    ]
    COLUMNS = ["월", "디비전", "모델", "매출", "비용1", "영업이익"]

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.csv_path = os.path.join(self.temp_dir.name, "functions.csv")
        pd.DataFrame(self.ROWS, columns=self.COLUMNS).to_csv(
            self.csv_path, index=False, encoding="utf-8-sig"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run(self, **kwargs) -> pd.DataFrame:
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=kwargs.pop("group_by_keys", ["디비전"]),
            measure_sums=kwargs.pop("measure_sums", ["매출"]),
            output_format=kwargs.pop("output_format", "csv"),
            chunksize=kwargs.pop("chunksize", 3),  # 10 rows -> 4 chunks
            **kwargs,
        )
        return pd.read_csv(aggregate_dataset(spec))

    def test_supported_function_names(self):
        self.assertEqual(AGGREGATION_FUNCTIONS, ("sum", "mean", "count", "min", "max"))

    def test_every_function_across_several_chunks(self):
        """chunksize=3 splits the 10 rows four ways, so a wrong fold cannot hide."""
        expected = {
            "sum": {"TV": 9800.0, "Mobile": 7000.0},
            "mean": {"TV": 9800.0 / 8, "Mobile": 3500.0},  # 1225.0
            "count": {"TV": 8.0, "Mobile": 2.0},
            "min": {"TV": 0.0, "Mobile": 3000.0},
            "max": {"TV": 2500.0, "Mobile": 4000.0},
        }

        for function, per_division in expected.items():
            with self.subTest(function=function):
                res_df = self._run(measure_functions={"매출": function})
                actual = res_df.set_index("디비전")["매출"].to_dict()
                for division, value in per_division.items():
                    self.assertAlmostEqual(actual[division], value, places=6)

    def test_functions_mix_within_one_result(self):
        res_df = self._run(
            measure_sums=["매출", "비용1", "영업이익"],
            measure_functions={"비용1": "max", "영업이익": "mean"},
        )

        tv = res_df[res_df["디비전"] == "TV"].iloc[0]
        self.assertEqual(tv["매출"], 9800.0)          # untouched sum
        self.assertEqual(tv["비용1"], 1250.0)         # max of the eight TV costs
        self.assertAlmostEqual(tv["영업이익"], 3325.0 / 8, places=6)

    def test_mean_is_not_a_mean_of_chunk_means(self):
        """Uneven chunks: folding chunk means would give 55.0 where the answer is 32.5."""
        skewed_csv = os.path.join(self.temp_dir.name, "skewed.csv")
        rows = [["TV", "10"], ["TV", "10"], ["TV", "10"], ["TV", "100"]]
        pd.DataFrame(rows, columns=["디비전", "매출"]).to_csv(
            skewed_csv, index=False, encoding="utf-8"
        )

        spec = AggregationSpec(
            file_path=skewed_csv,
            group_by_keys=["디비전"],
            measure_sums=["매출"],
            measure_functions={"매출": "mean"},
            output_format="csv",
            chunksize=3,  # chunks of 3 and 1 -> mean of means would be (10 + 100) / 2
        )

        res_df = pd.read_csv(aggregate_dataset(spec))

        self.assertAlmostEqual(res_df["매출"].iloc[0], 32.5, places=9)

    def test_mean_is_stable_whatever_the_chunk_boundaries(self):
        values = [self._run(measure_functions={"매출": "mean"}, chunksize=size) for size in (1, 2, 3, 4, 7, 100)]

        for res_df in values:
            tv = res_df[res_df["디비전"] == "TV"].iloc[0]
            self.assertAlmostEqual(tv["매출"], 1225.0, places=9)

    def test_count_ignores_blank_and_unparseable_cells(self):
        sparse_csv = os.path.join(self.temp_dir.name, "sparse.csv")
        rows = [
            ["TV", "100"],
            ["TV", ""],      # blank cell
            ["TV", "N/A"],   # not a number
            ["TV", "300"],
            ["TV", "0"],     # a real zero still counts
            ["Mobile", ""],
        ]
        pd.DataFrame(rows, columns=["디비전", "매출"]).to_csv(
            sparse_csv, index=False, encoding="utf-8"
        )

        spec = AggregationSpec(
            file_path=sparse_csv,
            group_by_keys=["디비전"],
            measure_sums=["매출"],
            measure_functions={"매출": "count"},
            output_format="csv",
            chunksize=2,
        )

        res_df = pd.read_csv(aggregate_dataset(spec))
        counts = res_df.set_index("디비전")["매출"].to_dict()

        self.assertEqual(counts["TV"], 3)      # 100, 300, 0
        self.assertEqual(counts["Mobile"], 0)  # nothing usable at all

    def test_min_and_max_skip_blank_cells(self):
        sparse_csv = os.path.join(self.temp_dir.name, "sparse_minmax.csv")
        rows = [["TV", ""], ["TV", "300"], ["TV", ""], ["TV", "100"]]
        pd.DataFrame(rows, columns=["디비전", "매출"]).to_csv(
            sparse_csv, index=False, encoding="utf-8"
        )

        common = dict(
            file_path=sparse_csv,
            group_by_keys=["디비전"],
            measure_sums=["매출"],
            output_format="csv",
            chunksize=2,
        )

        low = pd.read_csv(aggregate_dataset(AggregationSpec(measure_functions={"매출": "min"}, **common)))
        high = pd.read_csv(aggregate_dataset(AggregationSpec(measure_functions={"매출": "max"}, **common)))

        self.assertEqual(low["매출"].iloc[0], 100.0)  # not 0 from a blank cell
        self.assertEqual(high["매출"].iloc[0], 300.0)

    def test_a_group_with_no_usable_cell_folds_to_zero_never_nan(self):
        """All-blank and all-unparseable groups give 0.0 under every function, one chunk or many.

        A NaN here would land in the file as an empty cell and poison the next formula."""
        unusable_csv = os.path.join(self.temp_dir.name, "unusable.csv")
        rows = [
            ["TV", "100"],
            ["TV", "abc"],
            ["Mobile", ""],      # blank only
            ["Mobile", "N/A"],   # nothing parseable
            ["Audio", ""],
            ["Audio", ""],
        ]
        pd.DataFrame(rows, columns=["디비전", "매출"]).to_csv(
            unusable_csv, index=False, encoding="utf-8"
        )
        expected_tv = {"sum": 100.0, "mean": 100.0, "count": 1.0, "min": 100.0, "max": 100.0}

        for function in AGGREGATION_FUNCTIONS:
            for chunksize in (1, 100):  # every row its own chunk, then one chunk for all
                with self.subTest(function=function, chunksize=chunksize):
                    spec = AggregationSpec(
                        file_path=unusable_csv,
                        group_by_keys=["디비전"],
                        measure_sums=["매출"],
                        measure_functions={"매출": function},
                        output_format="csv",
                        chunksize=chunksize,
                    )
                    res_df = pd.read_csv(aggregate_dataset(spec))

                    self.assertFalse(res_df["매출"].isna().any())
                    values = res_df.set_index("디비전")["매출"].to_dict()
                    self.assertEqual(values["TV"], expected_tv[function])
                    self.assertEqual(values["Mobile"], 0.0)
                    self.assertEqual(values["Audio"], 0.0)

    def test_polish_decimals_fold_the_same_as_english_ones(self):
        """Every function sees the locale-parsed number, so "1 234,56" folds as 1234.56."""
        polish_csv = os.path.join(self.temp_dir.name, "polish.csv")
        rows = [["TV", "1 234,56"], ["TV", "1.000,5"], ["TV", "-"], ["Mobile", "7,25"], ["Mobile", "0,75"]]
        pd.DataFrame(rows, columns=["디비전", "매출"]).to_csv(
            polish_csv, index=False, encoding="utf-8"
        )
        expected = {
            "sum": {"TV": 2235.06, "Mobile": 8.0},
            "mean": {"TV": 1117.53, "Mobile": 4.0},  # the dash is skipped, not counted as zero
            "count": {"TV": 2.0, "Mobile": 2.0},
            "min": {"TV": 1000.5, "Mobile": 0.75},
            "max": {"TV": 1234.56, "Mobile": 7.25},
        }

        for function, per_division in expected.items():
            with self.subTest(function=function):
                spec = AggregationSpec(
                    file_path=polish_csv,
                    group_by_keys=["디비전"],
                    measure_sums=["매출"],
                    measure_functions={"매출": function},
                    number_mode="Polish",
                    output_format="csv",
                    chunksize=2,
                )
                actual = pd.read_csv(aggregate_dataset(spec)).set_index("디비전")["매출"].to_dict()
                for division, value in per_division.items():
                    self.assertAlmostEqual(actual[division], value, places=6)

    def test_count_column_uses_whole_number_format_in_xlsx(self):
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전"],
            measure_sums=["매출", "영업이익"],
            measure_functions={"매출": "count", "영업이익": "mean"},
            output_format="xlsx",
            chunksize=3,
        )

        worksheet = load_workbook(aggregate_dataset(spec).out_path)["Aggregated"]

        self.assertEqual(_data_cell_formats(worksheet, "매출"), {"#,##0"})
        # Only count changes format; every other function keeps the measure format.
        self.assertEqual(_data_cell_formats(worksheet, "영업이익"), {"#,##0.##"})

    def test_column_group_source_is_summed_even_with_a_value_function(self):
        """비용1 is both a group source and a max value column: both answers must be right."""
        res_df = self._run(
            measure_sums=["매출", "비용1"],
            column_groups=[ColumnGroupRule("직접비", ["비용1", "영업이익"])],
            measure_functions={"비용1": "max"},
            chunksize=2,
        )

        tv = res_df[res_df["디비전"] == "TV"].iloc[0]
        self.assertEqual(tv["비용1"], 1250.0)   # the value column shows the max
        self.assertEqual(tv["직접비"], 5000.0 + 3325.0)  # the group still sums 비용1

        mobile = res_df[res_df["디비전"] == "Mobile"].iloc[0]
        self.assertEqual(mobile["비용1"], 2000.0)
        self.assertEqual(mobile["직접비"], 3500.0 + 2550.0)

    def test_derived_formula_over_a_mean_column(self):
        res_df = self._run(
            measure_sums=["매출", "영업이익"],
            measure_functions={"영업이익": "mean"},
            derived_formulas=[DerivedFormulaRule("평균이익율", "영업이익", "매출", format_type="ratio")],
            chunksize=4,
        )

        tv = res_df[res_df["디비전"] == "TV"].iloc[0]
        mean_profit = 3325.0 / 8
        self.assertAlmostEqual(tv["영업이익"], mean_profit, places=6)
        self.assertAlmostEqual(tv["평균이익율"], mean_profit / 9800.0, places=9)

    def test_derived_formula_over_a_count_column(self):
        res_df = self._run(
            measure_sums=["매출", "영업이익"],
            measure_functions={"영업이익": "count"},
            derived_formulas=[DerivedFormulaRule("건당매출", "매출", "영업이익", format_type="number")],
            chunksize=3,
        )

        tv = res_df[res_df["디비전"] == "TV"].iloc[0]
        self.assertEqual(tv["영업이익"], 8.0)
        self.assertAlmostEqual(tv["건당매출"], 9800.0 / 8.0, places=9)

    def test_preview_applies_the_same_functions(self):
        spec = AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=["디비전"],
            measure_sums=["매출", "비용1"],
            measure_functions={"매출": "mean", "비용1": "count"},
            output_format="csv",
        )

        prev_df, _text = preview_aggregation(spec, sample_rows=100)
        tv = prev_df[prev_df["디비전"] == "TV"].iloc[0]

        self.assertAlmostEqual(tv["매출"], 1225.0, places=6)
        self.assertEqual(tv["비용1"], 8.0)

    def test_empty_measure_functions_reproduces_the_summed_output(self):
        """Regression guard: the default empty dict must leave today's bytes alone."""
        paths = {}
        common = dict(
            file_path=self.csv_path,
            group_by_keys=["디비전", "모델"],
            measure_sums=["매출", "영업이익"],
            column_groups=[ColumnGroupRule("직접비", ["비용1", "영업이익"])],
            derived_formulas=[DerivedFormulaRule("이익율(%)", "영업이익", "매출")],
            rollup_annual=True,
            month_column="월",
            output_format="csv",
            chunksize=3,
        )

        for name, functions in (
            ("baseline", None),
            ("explicit_empty", {}),
            ("explicit_sum", {"매출": "sum", "영업이익": "sum"}),
            ("unknown_function", {"매출": "median", "영업이익": ""}),
        ):
            paths[name] = os.path.join(self.temp_dir.name, f"{name}.csv")
            extra = {} if functions is None else {"measure_functions": functions}
            aggregate_dataset(AggregationSpec(output_path=paths[name], **common, **extra))

        with open(paths["baseline"], "rb") as handle:
            baseline_bytes = handle.read()
        for name in ("explicit_empty", "explicit_sum", "unknown_function"):
            with self.subTest(variant=name), open(paths[name], "rb") as handle:
                self.assertEqual(handle.read(), baseline_bytes)

        res_df = pd.read_csv(paths["baseline"])
        self.assertEqual(
            list(res_df.columns),
            ["연도", "디비전", "모델", "매출", "영업이익", "직접비", "이익율(%)"],
        )

    def test_unknown_function_falls_back_to_sum(self):
        res_df = self._run(measure_functions={"매출": "median"})

        self.assertEqual(res_df.set_index("디비전")["매출"].to_dict(), {"Mobile": 7000, "TV": 9800})

    def test_function_name_is_case_and_space_insensitive(self):
        res_df = self._run(measure_functions={"매출": "  MEAN "})

        tv = res_df[res_df["디비전"] == "TV"].iloc[0]
        self.assertAlmostEqual(tv["매출"], 1225.0, places=6)


class TestEstimateResultRows(unittest.TestCase):
    """Exact result-row count from the grouping columns alone."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.csv_path = os.path.join(self.temp_dir.name, "estimate.csv")
        rows = [
            ["202511", "TV", "BestBuy", "OLED65", "500"],
            ["202512", "TV", "Amazon", "OLED65", "1500"],
            ["202601", "TV", "BestBuy", "OLED65", "1000"],
            ["202601", "TV", "Amazon", "OLED65", "2000"],
            ["202601", "TV", "BestBuy", "OLED55", "800"],
            ["202602", "TV", "BestBuy", "OLED65", "1500"],
            ["202602", "Mobile", "Verizon", "GalaxyS", "3000"],
            ["202603", "TV", "Amazon", "OLED65", "2500"],
            ["202603", "Mobile", "AT&T", "GalaxyS", "4000"],
            ["202604", "TV", "Costco", "OLED77", "0"],
        ]
        pd.DataFrame(rows, columns=["월", "디비전", "거래선", "모델", "매출"]).to_csv(
            self.csv_path, index=False, encoding="utf-8-sig"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _spec(self, **kwargs) -> AggregationSpec:
        return AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=kwargs.pop("group_by_keys", ["디비전"]),
            measure_sums=kwargs.pop("measure_sums", ["매출"]),
            output_format="csv",
            chunksize=kwargs.pop("chunksize", 3),
            **kwargs,
        )

    def test_matches_the_real_run_for_several_specs(self):
        specs = {
            "one key": self._spec(),
            "three keys": self._spec(group_by_keys=["디비전", "모델", "거래선"], chunksize=2),
            "filtered": self._spec(
                group_by_keys=["모델"],
                filters=[FilterCondition("디비전", "==", "TV")],
                chunksize=2,
            ),
            "filter column outside the keys": self._spec(
                group_by_keys=["거래선"],
                filters=[FilterCondition("모델", "in", ["OLED65", "GalaxyS"])],
            ),
            "rollup with 연도 placed": self._spec(
                group_by_keys=["연도", "모델"], rollup_annual=True, month_column="월", chunksize=2
            ),
            "rollup replacing the month key": self._spec(
                group_by_keys=["월", "모델"], rollup_annual=True, month_column="월"
            ),
            "month kept without rollup": self._spec(group_by_keys=["월", "디비전"]),
            "rollup and filter together": self._spec(
                group_by_keys=["연도", "거래선"],
                rollup_annual=True,
                month_column="월",
                filters=[FilterCondition("디비전", "==", "TV")],
                chunksize=2,
            ),
            "constant column in the keys": self._spec(
                group_by_keys=["FX", "모델"], constant_columns={"FX": "KRW"}, chunksize=2
            ),
        }

        for name, spec in specs.items():
            with self.subTest(spec=name):
                count, exact = estimate_result_rows(spec)
                self.assertTrue(exact)
                self.assertEqual(count, len(pd.read_csv(aggregate_dataset(spec))))

    def test_only_constant_keys_fold_into_a_single_row(self):
        spec = self._spec(group_by_keys=["FX"], constant_columns={"FX": "KRW"})

        self.assertEqual(estimate_result_rows(spec), (1, True))
        self.assertEqual(len(pd.read_csv(aggregate_dataset(spec))), 1)

    def test_exact_under_the_cap_and_inexact_at_a_tiny_cap(self):
        spec = self._spec(group_by_keys=["디비전", "모델", "거래선"], chunksize=2)

        self.assertEqual(estimate_result_rows(spec, max_distinct=1000), (6, True))

        count, exact = estimate_result_rows(spec, max_distinct=2)
        self.assertFalse(exact)
        self.assertEqual(count, 2)

    def test_measure_columns_are_never_read(self):
        """A measure that does not exist in the file cannot stop the count."""
        spec = self._spec(group_by_keys=["디비전", "모델"], measure_sums=["존재하지않는금액"])

        self.assertEqual(estimate_result_rows(spec), (4, True))
        # The real run does need that column, which is exactly why counting is cheaper.
        with self.assertRaises(ColumnNotFoundError):
            aggregate_dataset(spec)

    def test_rule_columns_are_never_read_either(self):
        spec = self._spec(
            group_by_keys=["디비전"],
            measure_sums=["매출"],
            column_groups=[ColumnGroupRule("직접비", ["없는비용1", "없는비용2"])],
            derived_formulas=[DerivedFormulaRule("이익율(%)", "없는이익", "매출")],
        )

        self.assertEqual(estimate_result_rows(spec), (2, True))

    def test_missing_group_key_still_raises(self):
        spec = self._spec(group_by_keys=["없는컬럼"])

        with self.assertRaises(ColumnNotFoundError):
            estimate_result_rows(spec)

    def test_constant_column_clashing_with_a_source_column_raises(self):
        spec = self._spec(group_by_keys=["디비전"], constant_columns={"디비전": "TV"})

        with self.assertRaises(AggregatorError):
            estimate_result_rows(spec)

    def test_filters_matching_nothing_give_zero(self):
        spec = self._spec(filters=[FilterCondition("디비전", "==", "없는디비전")])

        self.assertEqual(estimate_result_rows(spec), (0, True))
        with self.assertRaises(EmptyResultError):
            aggregate_dataset(spec)

    def test_preset_cancel_event_raises(self):
        cancel_evt = threading.Event()
        cancel_evt.set()

        with self.assertRaises(AggregationCancelledError):
            estimate_result_rows(self._spec(), cancel_event=cancel_evt)

    def test_mid_stream_cancellation(self):
        cancel_evt = threading.Event()
        seen = []

        def progress_cb(current, total, message):
            seen.append(message)
            cancel_evt.set()  # stop at the chunk boundary after the first callback

        spec = self._spec(group_by_keys=["거래선"], chunksize=2)

        with self.assertRaises(AggregationCancelledError):
            estimate_result_rows(spec, progress_callback=progress_cb, cancel_event=cancel_evt)
        self.assertEqual(len(seen), 1)

    def test_missing_file_raises(self):
        spec = AggregationSpec(
            file_path=os.path.join(self.temp_dir.name, "nope.csv"),
            group_by_keys=["디비전"],
            measure_sums=["매출"],
        )

        with self.assertRaises(FileNotFoundError):
            estimate_result_rows(spec)


class TestPreviewRows(unittest.TestCase):
    """format_preview_rows and format_preview_text must render the same cells."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.csv_path = os.path.join(self.temp_dir.name, "preview.csv")
        rows = [
            ["202601", "TV", "OLED65", "1000", "350"],
            ["202601", "TV", "OLED55", "800", "280"],
            ["202602", "TV", "OLED65", "1500", "525"],
            ["202602", "Mobile", "GalaxyS", "3000", "1100"],
            ["202603", "Mobile", "GalaxyS", "4000", "1450"],
        ]
        pd.DataFrame(rows, columns=["월", "디비전", "모델", "매출", "영업이익"]).to_csv(
            self.csv_path, index=False, encoding="utf-8-sig"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _spec(self, **kwargs) -> AggregationSpec:
        return AggregationSpec(
            file_path=self.csv_path,
            group_by_keys=kwargs.pop("group_by_keys", ["디비전"]),
            measure_sums=kwargs.pop("measure_sums", ["매출", "영업이익"]),
            output_format="csv",
            **kwargs,
        )

    def _assert_rows_match_text(self, spec, group_keys, rows=15):
        frame, _text = preview_aggregation(spec, sample_rows=100)
        headers, body = format_preview_rows(frame, spec, group_keys, rows=rows)
        text_lines = format_preview_text(frame, spec, group_keys, rows=rows).split("\n")

        self.assertEqual(headers, [str(c) for c in frame.columns])
        self.assertEqual(text_lines[0].split(), headers)
        self.assertEqual(len(body), min(rows, len(frame)))
        for index, cells in enumerate(body):
            self.assertEqual(text_lines[index + 1].split(), cells)
        return headers, body

    def test_cells_match_the_rendered_text(self):
        spec = self._spec(
            derived_formulas=[
                DerivedFormulaRule("이익율(%)", "영업이익", "매출"),
                DerivedFormulaRule("이익배수", "영업이익", "매출", format_type="ratio"),
                DerivedFormulaRule("이익지수", "영업이익", "매출", multiplier=1000.0, format_type="number"),
            ],
        )

        headers, body = self._assert_rows_match_text(spec, ["디비전"])

        self.assertEqual(headers, ["디비전", "매출", "영업이익", "이익율(%)", "이익배수", "이익지수"])
        # TV: 매출 3,300 and 영업이익 1,155, so the ratio is exactly 0.35
        tv = next(row for row in body if row[0] == "TV")
        self.assertEqual(tv[1], "3,300")   # measure format, no decimals needed
        self.assertEqual(tv[3], "35.00%")  # a ratio in the file, a percent on screen
        self.assertEqual(tv[4], "0.350")   # ratio format keeps three decimals

    def test_count_cells_are_whole_numbers(self):
        spec = self._spec(measure_functions={"영업이익": "count"})

        _headers, body = self._assert_rows_match_text(spec, ["디비전"])

        tv = next(row for row in body if row[0] == "TV")
        self.assertEqual(tv[2], "3")

    def test_constant_and_group_key_cells_stay_labels(self):
        spec = self._spec(
            group_by_keys=["YYYY", "디비전"],
            measure_sums=["매출"],
            constant_columns={"YYYY": "2026"},
        )

        headers, body = self._assert_rows_match_text(spec, ["YYYY", "디비전"])

        self.assertEqual(headers[0], "YYYY")
        self.assertTrue(all(row[0] == "2026" for row in body))  # never 2,026

    def test_row_limit_is_honoured(self):
        spec = self._spec(group_by_keys=["디비전", "모델"], measure_sums=["매출"])
        frame, _text = preview_aggregation(spec, sample_rows=100)

        _headers, body = format_preview_rows(frame, spec, ["디비전", "모델"], rows=1)

        self.assertEqual(len(body), 1)

    def test_empty_frame_returns_headers_only(self):
        spec = self._spec(measure_sums=["매출"])
        frame, _text = preview_aggregation(spec, sample_rows=100)

        headers, body = format_preview_rows(frame.head(0), spec, ["디비전"], rows=10)

        self.assertEqual(headers, ["디비전", "매출"])
        self.assertEqual(body, [])


if __name__ == "__main__":
    unittest.main()
