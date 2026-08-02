import tempfile
import unittest
from pathlib import Path

from src.data_refinery import DataRefineryApp


class _Value:
    def __init__(self, value=''):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class TestDelimiterDetection(unittest.TestCase):
    def setUp(self):
        self.app = DataRefineryApp.__new__(DataRefineryApp)

    def test_detects_comma_after_garbage_rows_and_quoted_multiline_cell(self):
        content = (
            'report date: 2026-07-14\n'
            'garbage row\n'
            'Id,Name,Notes\n'
            '1,John,"First line\nSecond line"\n'
            '2,Jane,Single line\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.csv'
            path.write_text(content, encoding='utf-8')
            self.assertEqual(self.app._detect_delimiter(path), ',')

    def test_detects_semicolon_with_polish_numbers(self):
        content = 'report\nId;Amount\n1;"1 234,56"\n2;"500,00"\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.csv'
            path.write_text(content, encoding='utf-8')
            self.assertEqual(self.app._detect_delimiter(path), ';')

    def test_does_not_overwrite_a_manual_delimiter_choice(self):
        self.app.delimiter = _Value(',')
        self.app._delimiter_user_set = True
        self.assertFalse(self.app._apply_detected_delimiter(';'))
        self.assertEqual(self.app.delimiter.get(), ',')

    def test_applies_a_detected_delimiter_before_any_manual_choice(self):
        self.app.delimiter = _Value(',')
        self.app._delimiter_user_set = False
        self.assertTrue(self.app._apply_detected_delimiter('\t'))
        self.assertEqual(self.app.delimiter.get(), '\\t')
