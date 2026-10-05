"""Tests for Central/Eastern European encoding resilience and automatic recovery."""

import os
import tempfile
import unittest

import pandas as pd

from src.csv_processing import detect_encoding, detect_encoding_precise
from src.data_aggregator import (
    AggregationEncodingError,
    AggregationSpec,
    aggregate_dataset,
    inspect_dataset_schema,
    preview_aggregation,
)


class TestEncodingResilience(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_cp1250_file_with_byte_0xa8(self, pure_ascii_prefix_count: int = 1500) -> str:
        """Create a CSV file in CP1250 containing 0xa8 (e.g. diaeresis / Polish/Czech names)."""
        file_path = os.path.join(self.temp_dir.name, "eastern_europe_sales.csv")
        
        # Header
        lines = ["YearMonth,Customer,Product,Sales,Profit\n"]
        
        # Pure ASCII prefix rows so that initial 1000-row / small sample appears as ASCII
        for i in range(pure_ascii_prefix_count):
            lines.append(f"202601,Customer_{i},Product_Standard,100,20\n")
            
        # Rows with Polish / Czech characters including byte 0xa8 (¨ in CP1250) and Polish ogonek letters
        # In CP1250:
        # 'Kraków' -> 'ó' is 0xf3
        # 'Łódź' -> 'Ł' is 0xa3, 'ó' is 0xf3, 'd', 'ź' is 0x9f
        # String with diaeresis (0xa8) as the very first non-ASCII byte:
        lines.append("202602,Wytw\xa8rnia,OLED65,500,100\n")  # contains 0xa8 first
        lines.append("202602,Krak\xf3w_Branch,OLED55,300,50\n")
        lines.append("202603,Praha_\u010cesko,QLED75,800,150\n")

        with open(file_path, "wb") as f:
            for line in lines:
                f.write(line.encode("cp1250", errors="replace"))
                
        return file_path

    def test_detect_encoding_finds_cp1250_with_byte_0xa8(self):
        file_path = self._create_cp1250_file_with_byte_0xa8(pure_ascii_prefix_count=50)
        enc = detect_encoding(file_path)
        self.assertIn(enc.lower(), ("cp1250", "iso-8859-2"))

    def test_detect_encoding_precise_suggests_cp1250(self):
        file_path = self._create_cp1250_file_with_byte_0xa8(pure_ascii_prefix_count=100)
        enc = detect_encoding_precise(file_path, exclude=("utf-8", "utf-8-sig"))
        self.assertEqual(enc.lower(), "cp1250")

    def test_aggregation_raises_aggregation_encoding_error_on_mismatch(self):
        file_path = self._create_cp1250_file_with_byte_0xa8(pure_ascii_prefix_count=10)
        out_path = os.path.join(self.temp_dir.name, "out.csv")

        # Explicitly enforce UTF-8 to trigger decoding error on byte 0xa8
        spec = AggregationSpec(
            file_path=file_path,
            group_by_keys=["Customer"],
            measure_sums=["Sales", "Profit"],
            encoding="utf-8",
            output_format="csv",
            output_path=out_path,
        )

        with self.assertRaises(AggregationEncodingError) as ctx:
            aggregate_dataset(spec)

        self.assertIn("0xa8", str(ctx.exception).lower())
        self.assertEqual(ctx.exception.suggested_encoding.lower(), "cp1250")

    def test_aggregation_succeeds_with_precise_encoding_mode(self):
        file_path = self._create_cp1250_file_with_byte_0xa8(pure_ascii_prefix_count=10)
        out_path = os.path.join(self.temp_dir.name, "out_precise.csv")

        # In precise mode, either encoding is auto-detected as cp1250 or decode_errors='replace' protects it
        spec = AggregationSpec(
            file_path=file_path,
            group_by_keys=["Customer"],
            measure_sums=["Sales", "Profit"],
            encoding="cp1250",
            precise_encoding=True,
            output_format="csv",
            output_path=out_path,
        )

        result_path = aggregate_dataset(spec)
        self.assertTrue(os.path.exists(result_path))

        df = pd.read_csv(result_path, encoding="utf-8-sig")
        self.assertGreater(len(df), 0)
        self.assertIn("Sales", df.columns)
        self.assertIn("Profit", df.columns)

    def test_inspect_dataset_schema_recovers_from_encoding_mismatch(self):
        file_path = self._create_cp1250_file_with_byte_0xa8(pure_ascii_prefix_count=5)
        # Even if someone attempts to inspect with utf-8, it should recover
        schema = inspect_dataset_schema(file_path, encoding="utf-8")
        self.assertIn("Customer", schema.columns)
        self.assertIn("Sales", schema.columns)

    def test_preview_aggregation_handles_encoding_resilience(self):
        file_path = self._create_cp1250_file_with_byte_0xa8(pure_ascii_prefix_count=5)
        spec = AggregationSpec(
            file_path=file_path,
            group_by_keys=["Customer"],
            measure_sums=["Sales"],
            encoding="cp1250",
            precise_encoding=True,
        )
        preview_df, text = preview_aggregation(spec)
        self.assertFalse(preview_df.empty)
        self.assertIn("Customer", preview_df.columns)
