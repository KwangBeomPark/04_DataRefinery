"""Unit tests for data_aggregator module."""

import os
import tempfile
import threading
import unittest
import pandas as pd

from src.data_aggregator import (
    AggregationCancelledError,
    AggregationSpec,
    ColumnGroupRule,
    ColumnNotFoundError,
    DerivedFormulaRule,
    EmptyResultError,
    FilterCondition,
    aggregate_dataset,
    inspect_dataset_schema,
    preview_aggregation,
)


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
                    multiplier=100.0,
                ),
                DerivedFormulaRule(
                    new_column="직접비율(%)",
                    numerator_column="직접비",
                    denominator_column="매출",
                    multiplier=100.0,
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

        # OLED65: 직접비 = 5400 (all rows). 매출 = 9000. 직접비율 = 5400/9000*100 = 60.0%
        oled65 = res_df[res_df["모델"] == "OLED65"].iloc[0]
        self.assertEqual(oled65["매출"], 9000.0)
        expected_rate = (3175.0 / 9000.0) * 100.0
        self.assertAlmostEqual(oled65["이익율(%)"], expected_rate, places=2)
        expected_cost_rate = (oled65["직접비"] / 9000.0) * 100.0
        self.assertAlmostEqual(oled65["직접비율(%)"], expected_cost_rate, places=2)

        # OLED77 has sales 0 -> 이익율 must be 0.0, not NaN or error
        oled77 = res_df[res_df["모델"] == "OLED77"].iloc[0]
        self.assertEqual(oled77["매출"], 0.0)
        self.assertEqual(oled77["이익율(%)"], 0.0)

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


if __name__ == "__main__":
    unittest.main()
