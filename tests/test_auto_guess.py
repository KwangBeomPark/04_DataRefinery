"""Unit tests for the mart auto-guess and role assignment engine."""

import unittest

from src.mart.auto_guess import (
    ROLE_DIM_ATTR,
    ROLE_DIM_KEY,
    ROLE_EXCLUDED,
    ROLE_MEASURE,
    ROLE_PERIOD,
    ROLE_ROW_KEY,
    assignments_to_model,
    guess_model_assignments,
    model_to_assignments,
)
from src.mart.model import Dimension, MartModel, Measure


class TestMartAutoGuess(unittest.TestCase):
    def test_guess_korean_pnl_columns(self):
        columns = [
            "기준년월",
            "법인코드",
            "전표번호",
            "거래처코드",
            "거래처명",
            "상품코드",
            "상품명",
            "매출액",
            "매출원가",
            "판매수량",
            "비고",
        ]
        res = guess_model_assignments(columns, default_fact_name="fact_pnl")

        self.assertEqual(res.period_column, "기준년월")
        self.assertEqual(res.fact_table, "fact_pnl")

        # Check assignment roles
        role_map = {a.column_name: a for a in res.assignments}
        self.assertEqual(role_map["기준년월"].role, ROLE_PERIOD)
        self.assertEqual(role_map["거래처코드"].role, ROLE_DIM_KEY)
        self.assertEqual(role_map["거래처명"].role, ROLE_DIM_ATTR)
        self.assertEqual(role_map["상품코드"].role, ROLE_DIM_KEY)
        self.assertEqual(role_map["상품명"].role, ROLE_DIM_ATTR)
        self.assertEqual(role_map["매출액"].role, ROLE_MEASURE)
        self.assertEqual(role_map["매출원가"].role, ROLE_MEASURE)
        self.assertEqual(role_map["판매수량"].role, ROLE_MEASURE)

        # Check built dimensions
        dim_names = [d.name for d in res.dimensions]
        self.assertIn("고객/거래처", dim_names)
        self.assertIn("제품/품목", dim_names)

        # Build model and ensure validity
        # Make sure remaining columns are excluded or assigned
        for a in res.assignments:
            if a.role == "unassigned":
                a.role = ROLE_EXCLUDED

        model = assignments_to_model(
            res.assignments,
            model_name="test_pnl_mart",
            fact_table=res.fact_table,
            period_column=res.period_column,
        )
        self.assertEqual(model.name, "test_pnl_mart")
        self.assertEqual(model.period_column, "기준년월")
        self.assertTrue(len(model.measures) >= 3)
        self.assertTrue(len(model.dimensions) >= 2)

    def test_model_to_assignments_roundtrip(self):
        all_cols = ["period", "row_id", "cust_code", "cust_name", "amount", "notes"]
        model = MartModel(
            name="test_roundtrip",
            fact_table="fact_sales",
            period_column="period",
            row_key_columns=("row_id",),
            dimensions=(
                Dimension(name="Customer", key_columns=("cust_code",), attributes=("cust_name",)),
            ),
            measures=(
                Measure(name="amount", column="amount", kind="value"),
            ),
            excluded_columns=("notes",),
            version="2",
        )

        assignments = model_to_assignments(model, all_cols)
        role_map = {a.column_name: a for a in assignments}

        self.assertEqual(role_map["period"].role, ROLE_PERIOD)
        self.assertEqual(role_map["row_id"].role, ROLE_ROW_KEY)
        self.assertEqual(role_map["cust_code"].role, ROLE_DIM_KEY)
        self.assertEqual(role_map["cust_name"].role, ROLE_DIM_ATTR)
        self.assertEqual(role_map["amount"].role, ROLE_MEASURE)
        self.assertEqual(role_map["notes"].role, ROLE_EXCLUDED)

    def test_english_spaced_columns_stemming_and_measures(self):
        """Test English space-separated headers like Derek P&L dataset."""
        columns = [
            "YYYYMM",
            "Division",
            "Subsidiary",
            "Product Lvl4 Code",
            "Product Lvl4 Model Suffix",
            "Bill To Customer",
            "Bill To Customer Name",
            "Ship To Customer",
            "Ship To Customer Name",
            "Internal Exp Amount",
            "External Revenue",
        ]
        res = guess_model_assignments(columns, default_fact_name="fact_pnl")
        self.assertEqual(res.period_column, "YYYYMM")

        role_map = {a.column_name: a for a in res.assignments}
        self.assertEqual(role_map["YYYYMM"].role, ROLE_PERIOD)
        self.assertEqual(role_map["Product Lvl4 Code"].role, ROLE_DIM_KEY)
        self.assertEqual(role_map["Product Lvl4 Model Suffix"].role, ROLE_DIM_ATTR)
        self.assertEqual(role_map["Bill To Customer"].role, ROLE_DIM_KEY)
        self.assertEqual(role_map["Bill To Customer Name"].role, ROLE_DIM_ATTR)
        self.assertEqual(role_map["Internal Exp Amount"].role, ROLE_MEASURE)
        self.assertEqual(role_map["External Revenue"].role, ROLE_MEASURE)


if __name__ == "__main__":
    unittest.main()

