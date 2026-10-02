"""Unit tests and benchmarks for src/data_mapper.py."""

import os
import shutil
import tempfile
import time
import unittest

import numpy as np
import pandas as pd

from src.data_mapper import (
    OP_CONTAINS,
    OP_ENDSWITH,
    OP_EQUALS,
    OP_GREATER,
    OP_GREATER_EQUAL,
    OP_IN,
    OP_IS_NOT_NULL,
    OP_IS_NULL,
    OP_LESS_EQUAL,
    OP_NOT_CONTAINS,
    OP_NOT_EQUALS,
    OP_NOT_IN,
    OP_REGEX,
    OP_STARTSWITH,
    MappingRule,
    MappingSpec,
    RuleCondition,
    analyze_unmapped_values,
    apply_mapping_to_dataframe,
    evaluate_condition,
    execute_mapping,
    load_ruleset_file,
    save_ruleset_file,
    validate_rules_against_columns,
)


class TestDataMapperConditionOperators(unittest.TestCase):
    """Verifies that individual condition operators evaluate accurately."""

    def test_equality_and_inequality(self):
        s = pd.Series(["Apple", "Banana", "cherry", None, ""])
        # Case-insensitive equals
        c_eq = RuleCondition(column="fruit", operator=OP_EQUALS, value="apple", case_sensitive=False)
        res_eq = evaluate_condition(s, c_eq)
        self.assertTrue(res_eq.iloc[0])
        self.assertFalse(res_eq.iloc[1])

        # Case-sensitive equals
        c_eq_sens = RuleCondition(column="fruit", operator=OP_EQUALS, value="apple", case_sensitive=True)
        res_eq_sens = evaluate_condition(s, c_eq_sens)
        self.assertFalse(res_eq_sens.iloc[0])  # 'Apple' != 'apple'

        # Not equals
        c_neq = RuleCondition(column="fruit", operator=OP_NOT_EQUALS, value="Banana")
        res_neq = evaluate_condition(s, c_neq)
        self.assertTrue(res_neq.iloc[0])
        self.assertFalse(res_neq.iloc[1])

    def test_contains_and_not_contains(self):
        s = pd.Series(["Galaxy S24", "Galaxy Tab S9", "iPhone 15", "Xiaomi 14"])
        c_contains = RuleCondition(column="item", operator=OP_CONTAINS, value="Galaxy")
        res_contains = evaluate_condition(s, c_contains)
        self.assertEqual(res_contains.tolist(), [True, True, False, False])

        c_not_contains = RuleCondition(column="item", operator=OP_NOT_CONTAINS, value="Galaxy")
        res_not_contains = evaluate_condition(s, c_not_contains)
        self.assertEqual(res_not_contains.tolist(), [False, False, True, True])

    def test_startswith_and_endswith(self):
        s = pd.Series(["PRE_001", "POST_002", "PRE_003_END", "TEST"])
        c_starts = RuleCondition(column="code", operator=OP_STARTSWITH, value="PRE_")
        self.assertEqual(evaluate_condition(s, c_starts).tolist(), [True, False, True, False])

        c_ends = RuleCondition(column="code", operator=OP_ENDSWITH, value="_END")
        self.assertEqual(evaluate_condition(s, c_ends).tolist(), [False, False, True, False])

    def test_in_and_not_in(self):
        s = pd.Series(["KR", "US", "JP", "DE", "FR"])
        # Comma-separated string
        c_in = RuleCondition(column="country", operator=OP_IN, value="kr, jp, fr")
        self.assertEqual(evaluate_condition(s, c_in).tolist(), [True, False, True, False, True])

        # Python list
        c_not_in = RuleCondition(column="country", operator=OP_NOT_IN, value=["KR", "US"])
        self.assertEqual(evaluate_condition(s, c_not_in).tolist(), [False, False, True, True, True])

    def test_numeric_comparisons(self):
        s = pd.Series([10, "20", 35.5, "invalid", 50])
        c_gt = RuleCondition(column="val", operator=OP_GREATER, value=20)
        self.assertEqual(evaluate_condition(s, c_gt).tolist(), [False, False, True, False, True])

        c_lte = RuleCondition(column="val", operator=OP_LESS_EQUAL, value=20)
        self.assertEqual(evaluate_condition(s, c_lte).tolist(), [True, True, False, False, False])

    def test_null_checks(self):
        s = pd.Series(["Val", "", None, "   ", np.nan])
        c_null = RuleCondition(column="col", operator=OP_IS_NULL)
        self.assertEqual(evaluate_condition(s, c_null).tolist(), [False, True, True, True, True])

        c_not_null = RuleCondition(column="col", operator=OP_IS_NOT_NULL)
        self.assertEqual(evaluate_condition(s, c_not_null).tolist(), [True, False, False, False, False])

    def test_regex_matching(self):
        s = pd.Series(["A-1234", "B-9999", "C_123", "A-5678"])
        c_reg = RuleCondition(column="sku", operator=OP_REGEX, value=r"^A-\d{4}$")
        self.assertEqual(evaluate_condition(s, c_reg).tolist(), [True, False, False, True])


class TestDataMapperWaterfallLogic(unittest.TestCase):
    """Verifies that the sequential waterfall (first-match-wins) functions flawlessly."""

    def setUp(self):
        self.df = pd.DataFrame({
            "채널": ["온라인", "오프라인", "온라인", "B2B", "온라인"],
            "브랜드": ["삼성", "삼성", "애플", "LG", "기타"],
            "수량": [10, 5, 2, 100, 1],
            "단가": [1000000, 1200000, 1500000, 500000, 20000],
        })

    def test_waterfall_precedence(self):
        # Rule 1: B2B 대량 구매 (수량 >= 50) -> 'B2B대량'
        # Rule 2: 온라인 채널 중 삼성 (채널=='온라인' AND 브랜드=='삼성') -> '온라인삼성'
        # Rule 3: 온라인 채널 중 애플 (채널=='온라인' AND 브랜드=='애플') -> '온라인애플'
        # Rule 4: 일반 오프라인 (채널=='오프라인') -> '오프라인'
        # Fallback -> '미분류'

        rules = [
            MappingRule(
                rule_id="r1",
                name="B2B 대량",
                target_value="B2B대량",
                conditions=[RuleCondition(column="수량", operator=OP_GREATER_EQUAL, value=50)],
            ),
            MappingRule(
                rule_id="r2",
                name="온라인 삼성",
                target_value="온라인삼성",
                conditions=[
                    RuleCondition(column="채널", operator=OP_EQUALS, value="온라인"),
                    RuleCondition(column="브랜드", operator=OP_EQUALS, value="삼성"),
                ],
                combine_operator="AND",
            ),
            MappingRule(
                rule_id="r3",
                name="온라인 애플",
                target_value="온라인애플",
                conditions=[
                    RuleCondition(column="채널", operator=OP_EQUALS, value="온라인"),
                    RuleCondition(column="브랜드", operator=OP_EQUALS, value="애플"),
                ],
                combine_operator="AND",
            ),
            MappingRule(
                rule_id="r4",
                name="오프라인 매장",
                target_value="오프라인",
                conditions=[RuleCondition(column="채널", operator=OP_EQUALS, value="오프라인")],
            ),
        ]

        mapped_df, stats, unmapped_mask = apply_mapping_to_dataframe(
            self.df.copy(), rules, target_column="분류", default_value="기타미분류"
        )

        expected_categories = [
            "온라인삼성",   # 행 0: 온라인 & 삼성
            "오프라인",     # 행 1: 오프라인
            "온라인애플",   # 행 2: 온라인 & 애플
            "B2B대량",      # 행 3: 수량 100
            "기타미분류",   # 행 4: 온라인 & 기타브랜드 (어떤 룰에도 안 걸림)
        ]

        self.assertEqual(mapped_df["분류"].tolist(), expected_categories)
        self.assertEqual(unmapped_mask.sum(), 1)
        self.assertTrue(unmapped_mask.iloc[4])

        # Check matched counts
        stat_dict = {s["rule_id"]: s["matched_count"] for s in stats}
        self.assertEqual(stat_dict["r1"], 1)
        self.assertEqual(stat_dict["r2"], 1)
        self.assertEqual(stat_dict["r3"], 1)
        self.assertEqual(stat_dict["r4"], 1)

    def test_unmapped_values_analysis(self):
        rules = [
            MappingRule(
                rule_id="r1",
                name="온라인",
                target_value="온라인",
                conditions=[RuleCondition(column="채널", operator=OP_EQUALS, value="온라인")],
            ),
        ]
        mapped_df, _, unmapped_mask = apply_mapping_to_dataframe(
            self.df.copy(), rules, target_column="분류"
        )
        unmapped_top = analyze_unmapped_values(mapped_df, unmapped_mask, candidate_columns=["채널", "브랜드"])
        # Unmapped channels should contain '오프라인' and 'B2B'
        channels = [val for val, count in unmapped_top["채널"]]
        self.assertIn("오프라인", channels)
        self.assertIn("B2B", channels)


class TestDataMapperStreamingExecution(unittest.TestCase):
    """Tests chunked file streaming and atomic persistence."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.csv_path = os.path.join(self.temp_dir, "input_test.csv")
        # Create a small dataset
        df = pd.DataFrame({
            "ID": [1, 2, 3, 4, 5],
            "Type": ["A", "B", "A", "C", "B"],
            "Amount": [100, 200, 150, 300, 250],
        })
        df.to_csv(self.csv_path, index=False, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_execute_mapping_csv(self):
        rules = [
            MappingRule(
                rule_id="r_a",
                name="Type A",
                target_value="Category_A",
                conditions=[RuleCondition(column="Type", operator=OP_EQUALS, value="A")],
            ),
            MappingRule(
                rule_id="r_b",
                name="Type B",
                target_value="Category_B",
                conditions=[RuleCondition(column="Type", operator=OP_EQUALS, value="B")],
            ),
        ]

        spec = MappingSpec(
            file_path=self.csv_path,
            target_column="Mapped_Category",
            rules=rules,
            default_value="Unassigned",
            output_format="csv",
            chunksize=2,  # force multiple chunks
        )

        res = execute_mapping(spec)
        self.assertEqual(res.total_rows, 5)
        self.assertEqual(res.mapped_rows, 4)
        self.assertEqual(res.unmapped_rows, 1)  # Type C
        self.assertTrue(os.path.exists(res.output_path))

        # Inspect resulting output
        out_df = pd.read_csv(res.output_path)
        self.assertIn("Mapped_Category", out_df.columns)
        self.assertEqual(out_df["Mapped_Category"].tolist(), ["Category_A", "Category_B", "Category_A", "Unassigned", "Category_B"])

    def test_ruleset_save_and_load(self):
        rules = [
            MappingRule(
                rule_id="rule_1",
                name="Rule 1",
                target_value="VIP",
                conditions=[
                    RuleCondition(column="Amount", operator=OP_GREATER, value=200),
                ],
            )
        ]
        json_path = os.path.join(self.temp_dir, "ruleset.json")
        save_ruleset_file(json_path, rules, target_column="Tier", default_value="Normal")

        loaded_rules, target_col, def_val = load_ruleset_file(json_path)
        self.assertEqual(target_col, "Tier")
        self.assertEqual(def_val, "Normal")
        self.assertEqual(len(loaded_rules), 1)
        self.assertEqual(loaded_rules[0].target_value, "VIP")
        self.assertEqual(loaded_rules[0].conditions[0].operator, OP_GREATER)

    def test_validation_warnings(self):
        rules = [
            MappingRule(
                rule_id="r1",
                name="Invalid Col Rule",
                target_value="Test",
                conditions=[RuleCondition(column="NonExistentCol", operator=OP_EQUALS, value="X")],
            )
        ]
        warnings = validate_rules_against_columns(rules, available_columns=["ID", "Type", "Amount"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("NonExistentCol", warnings[0])


class TestDataMapperBenchmark1MillionRows(unittest.TestCase):
    """Benchmarks 1,000,000 rows processing speed and memory safety."""

    def test_benchmark_one_million_rows_memory(self):
        print("\n--- [1,000,000 Rows Benchmark Test Starting] ---")
        n_rows = 1_000_000

        # Generate realistic business dataset in memory
        np.random.seed(42)
        channels = np.random.choice(["온라인", "오프라인", "B2B", "글로벌", "특판"], size=n_rows)
        brands = np.random.choice(["삼성", "LG", "애플", "샤오미", "소니"], size=n_rows)
        quantities = np.random.randint(1, 150, size=n_rows)
        prices = np.random.choice([50000, 150000, 800000, 1200000, 2000000], size=n_rows)

        df = pd.DataFrame({
            "채널": channels,
            "브랜드": brands,
            "수량": quantities,
            "단가": prices,
        })

        rules = [
            MappingRule(
                rule_id="r1",
                name="B2B 대량 구매",
                target_value="B2B_Bulk",
                conditions=[
                    RuleCondition(column="채널", operator=OP_EQUALS, value="B2B"),
                    RuleCondition(column="수량", operator=OP_GREATER_EQUAL, value=50),
                ],
                combine_operator="AND",
            ),
            MappingRule(
                rule_id="r2",
                name="온라인 플래그십 (삼성/애플 고가품)",
                target_value="Online_Premium",
                conditions=[
                    RuleCondition(column="채널", operator=OP_EQUALS, value="온라인"),
                    RuleCondition(column="브랜드", operator=OP_IN, value=["삼성", "애플"]),
                    RuleCondition(column="단가", operator=OP_GREATER_EQUAL, value=1000000),
                ],
                combine_operator="AND",
            ),
            MappingRule(
                rule_id="r3",
                name="글로벌 수출건",
                target_value="Global_Export",
                conditions=[RuleCondition(column="채널", operator=OP_EQUALS, value="글로벌")],
            ),
            MappingRule(
                rule_id="r4",
                name="오프라인 매장",
                target_value="Retail_Store",
                conditions=[RuleCondition(column="채널", operator=OP_EQUALS, value="오프라인")],
            ),
            MappingRule(
                rule_id="r5",
                name="가성비 샤오미",
                target_value="Budget_Xiaomi",
                conditions=[RuleCondition(column="브랜드", operator=OP_EQUALS, value="샤오미")],
            ),
        ]

        t0 = time.perf_counter()
        mapped_df, stats, unmapped_mask = apply_mapping_to_dataframe(
            df, rules, target_column="분류_카테고리", default_value="기타_미분류"
        )
        elapsed = time.perf_counter() - t0

        total_mapped = n_rows - unmapped_mask.sum()
        unmapped_count = unmapped_mask.sum()

        print(f"[OK] 1,000,000 rows 5-step mapping elapsed time: {elapsed:.3f}s")
        print(f"[OK] Mapped: {total_mapped:,} rows ({total_mapped/n_rows*100:.1f}%), Unmapped: {unmapped_count:,} rows ({unmapped_count/n_rows*100:.1f}%)")
        for s in stats:
            print(f"   - Rule {s['rule_id']}: {s['matched_count']:,} rows matched")

        # Assertion: 1,000,000 rows must complete in under 5.0 seconds (typically ~0.6-1.5s on modern PC)
        self.assertLess(elapsed, 5.0, f"100만 행 연산이 너무 느립니다: {elapsed:.2f}s")
        self.assertEqual(len(mapped_df), n_rows)
        self.assertIn("분류_카테고리", mapped_df.columns)
        self.assertGreater(total_mapped, 0)


if __name__ == "__main__":
    unittest.main()
