"""Unit tests for the mart auto-guess and role assignment engine."""

import tempfile
import unittest
from pathlib import Path

from src.mart.auto_guess import (
    ROLE_DIM_ATTR,
    ROLE_DIM_KEY,
    ROLE_EXCLUDED,
    ROLE_MEASURE,
    ROLE_PARTITION,
    ROLE_PERIOD,
    ROLE_ROW_KEY,
    ROLE_UNASSIGNED,
    ColumnAssignment,
    assignments_to_model,
    guess_model_assignments,
    infer_decimal_precision_scale,
    model_to_assignments,
)
from src.mart.model import Dimension, MartModel, Measure, model_from_dict


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

    def test_prefix_pair_code_vs_name_role_inversion_fixed(self):
        """Verify <Base> Code is dim_key and <Base> is dim_attr when both exist."""
        columns = [
            "YYYYMM",
            "Division",
            "Subsidiary",
            "Subsidiary(R&R)",
            "Product Lvl4 Code",
            "Product Lvl4",
            "Model Suffix",
            "Bill To Customer",
            "Bill To Customer Name",
            "Ship To Customer",
            "Ship To Customer Name",
            "Net Sales",
        ]
        res = guess_model_assignments(columns, default_fact_name="fact_pnl")
        role_map = {a.column_name: a for a in res.assignments}

        self.assertEqual(role_map["Product Lvl4 Code"].role, ROLE_DIM_KEY)
        self.assertEqual(role_map["Product Lvl4"].role, ROLE_DIM_ATTR)
        self.assertEqual(
            role_map["Product Lvl4 Code"].dimension_name,
            role_map["Product Lvl4"].dimension_name,
        )
        self.assertEqual(role_map["Model Suffix"].role, ROLE_DIM_KEY)
        self.assertEqual(role_map["Division"].role, ROLE_DIM_KEY)
        self.assertEqual(role_map["Subsidiary"].role, ROLE_PARTITION)
        self.assertEqual(role_map["Subsidiary(R&R)"].role, ROLE_DIM_ATTR)
        unassigned = [a.column_name for a in res.assignments if a.role == ROLE_UNASSIGNED]
        self.assertEqual(unassigned, [])

    def test_infer_decimal_precision_scale_lossless(self):
        """Verify infer_decimal_precision_scale handles standard decimals, high scale, and scientific notation."""
        # Standard 2-decimal or integer -> (18, 2)
        self.assertEqual(infer_decimal_precision_scale(["1000", "2500.50", "0"]), (18, 2))
        # 9 fractional digits -> R=9, S=11, P=18
        self.assertEqual(infer_decimal_precision_scale(["123.123456789", "-45.5"]), (18, 11))
        # Scientific notation 5.46E-10 -> R=12, S=14, P=18
        self.assertEqual(infer_decimal_precision_scale(["5.46E-10", "0"]), (18, 14))
        # Scientific notation 5.95E-11 -> R=13, S=15, P=18
        self.assertEqual(infer_decimal_precision_scale(["5.95E-11", "3.80E-09"]), (18, 15))
        # Large integer + high scale exceeding 18 digits -> promotes precision to 38
        self.assertEqual(infer_decimal_precision_scale(["1234567890.123456789"]), (38, 11))

    def test_wide_218_column_pnl_zero_unassigned_and_207_measures(self):
        """Verify 218-column wide P&L TSV with empty cost columns and scientific notation."""
        id_cols = [
            "YYYYMM",
            "Division",
            "Subsidiary",
            "Subsidiary(R&R)",
            "Product Lvl4 Code",
            "Product Lvl4",
            "Model Suffix",
            "Bill To Customer",
            "Bill To Customer Name",
            "Ship To Customer",
            "Ship To Customer Name",
        ]
        empty_accounting_cols = [
            "Labor Cost(General)",
            "Labor Cost(Global Cost)",
            "Manuf Exp(General)",
            "Manuf Exp(Global Cost)",
            "Variable Labor Cost(General)",
            "Variable Labor Cost(Global Cost)",
            "Variable Manuf Exp(General)",
            "Variable Manuf Exp(Global Cost)",
            "Fixed Labor Cost(General)",
            "Fixed Labor Cost(Global Cost)",
            "Fixed Manuf Exp(General)",
            "Fixed Manuf Exp(Global Cost)",
        ]
        sci_col = "Variable Installation Exp"
        other_measures = [f"Account Revenue {i}" for i in range(207 - len(empty_accounting_cols) - 1)]
        all_measure_cols = other_measures[:79] + empty_accounting_cols + [sci_col] + other_measures[79:]
        headers = id_cols + all_measure_cols
        self.assertEqual(len(headers), 218)

        row_vals = [
            "201401",
            "DIV01",
            "SUB01",
            "SUB01(R&R)",
            "PLVL0001",
            "Product Line A",
            "MDL01.AAA",
            "CUST001",
            "Customer One",
            "SHIP001",
            "Ship One",
        ]
        for c in all_measure_cols:
            if c in empty_accounting_cols:
                row_vals.append("")
            elif c == sci_col:
                row_vals.append("5.95E-11")
            else:
                row_vals.append("1234.123456789")

        with tempfile.TemporaryDirectory() as td:
            tsv_path = Path(td) / "wide_pnl.tsv"
            tsv_path.write_text(
                "\t".join(headers) + "\n" + "\t".join(row_vals) + "\n",
                encoding="utf-8-sig",
            )
            res = guess_model_assignments(headers, csv_path=tsv_path)

            unassigned = [a.column_name for a in res.assignments if a.role == ROLE_UNASSIGNED]
            self.assertEqual(unassigned, [])
            self.assertEqual(len(res.measures), 207)

            role_map = {a.column_name: a for a in res.assignments}
            self.assertEqual(role_map["Product Lvl4 Code"].role, ROLE_DIM_KEY)
            self.assertEqual(role_map["Product Lvl4"].role, ROLE_DIM_ATTR)
            for empty_col in empty_accounting_cols:
                self.assertEqual(role_map[empty_col].role, ROLE_MEASURE)
            self.assertEqual(role_map[sci_col].role, ROLE_MEASURE)
            self.assertEqual(role_map[sci_col].measure_precision, 18)
            self.assertGreaterEqual(role_map[sci_col].measure_scale, 15)

    def test_duplicate_grain_policy_and_auto_row_key_cap(self):
        """Verify MartModel duplicate_grain_policy and assignments_to_model auto row_key cap at 8."""
        asgns = [ColumnAssignment(column_name="dt", role=ROLE_PERIOD)]
        for i in range(9):
            asgns.append(
                ColumnAssignment(
                    column_name=f"k{i}",
                    role=ROLE_DIM_KEY,
                    dimension_name=f"dim_{i}",
                )
            )
        asgns.append(ColumnAssignment(column_name="amt", role=ROLE_MEASURE))

        model = assignments_to_model(
            asgns,
            model_name="wide_dims",
            fact_table="fact_wide",
            period_column="dt",
            duplicate_grain_policy="sum",
        )
        self.assertEqual(len(model.row_key_columns), 8)
        self.assertEqual(model.duplicate_grain_policy, "sum")

        restored = model_from_dict(
            {
                "name": "legacy",
                "fact_table": "fact_legacy",
                "dimensions": [{"name": "d1", "key_columns": ["k0"]}],
                "measures": [{"name": "amt", "column": "amt"}],
                "period_column": "dt",
                "row_key_columns": ["dt", "k0"],
            }
        )
        self.assertEqual(restored.duplicate_grain_policy, "reject")


if __name__ == "__main__":
    unittest.main()
