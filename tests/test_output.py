import csv
import tempfile
import tracemalloc
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import openpyxl
import pandas as pd

from src.csv_processing import (
    CsvProcessingOptions,
    ParsedNumber,
    build_output_path,
    format_csv_value,
    parse_number,
    process_csv_file,
    write_excel_output,
)
from src.data_refinery import DataRefineryApp, _LANGUAGE_CODES


class _Value:
    def __init__(self, value=''):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class TestOutput(unittest.TestCase):
    def test_language_order_defaults_to_english_then_korean_then_polish(self):
        self.assertEqual(tuple(_LANGUAGE_CODES), ('English', '한국어', 'Polski'))

    def test_localized_format_choices_and_result_summary(self):
        self.assertEqual(DataRefineryApp._number_mode('폴란드식 · 1 234,56'), 'Polish')
        self.assertEqual(DataRefineryApp._number_mode('Format polski · 1 234,56'), 'Polish')
        self.assertEqual(DataRefineryApp._output_format('Skoroszyt Excel (.xlsx)'), 'Excel (.xlsx)')

        app = DataRefineryApp.__new__(DataRefineryApp)
        app.language = _Value('Polski')
        summary = app._format_result_summary({
            'out_path': 'C:/temp/processed_output_20300102_0304.csv',
            'rows': '3',
            'columns': '5',
            'garbage_skipped': '2',
            'numbers': '3',
            'dates': '3',
            'flattened': '2',
            'repaired': '0',
            'large_numbers_as_text': '0',
            'encoding': 'utf-8-sig',
        })
        self.assertIn('Co zostało uporządkowane', summary)
        self.assertIn('Zapisano', summary)

    def test_output_name_includes_timestamp_and_avoids_same_minute_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.csv'
            source.touch()
            timestamp = datetime(2030, 1, 2, 3, 4)
            first = Path(build_output_path(source, 'CSV', timestamp))
            self.assertEqual(first.name, 'processed_output_20300102_0304.csv')
            first.touch()
            second = Path(build_output_path(source, 'CSV', timestamp))

        self.assertEqual(second.name, 'processed_output_20300102_0304_02.csv')

    def test_csv_formatter_preserves_each_cell_decimal_scale(self):
        values = [
            parse_number('500,00', 'Polish'),
            parse_number('1,2', 'Polish'),
            parse_number('2,345', 'Polish'),
        ]
        self.assertEqual(
            [format_csv_value(value, ',') for value in values],
            ['500,00', '1,2', '2,345'],
        )

    def test_excel_output_preserves_formats_and_text_protects_large_numbers(self):
        values = [
            parse_number('500,00', 'Polish'),
            parse_number('1,2', 'Polish'),
            parse_number('9999999999999999,99', 'Polish'),
            parse_number('1234567890123456', 'Polish'),
            parse_number('100000000000000,00', 'Polish'),
        ]
        self.assertTrue(all(isinstance(value, ParsedNumber) for value in values))
        frame = pd.DataFrame([values], columns=['two', 'one', 'large_decimal', 'large_integer', 'safe_zeroes'])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'output.xlsx'
            protected_count = write_excel_output(frame, path)
            workbook = openpyxl.load_workbook(path, data_only=False)
            sheet = workbook.active

        self.assertEqual(protected_count, 2)
        self.assertEqual(sheet['A2'].value, 500)
        self.assertEqual(sheet['A2'].data_type, 'n')
        self.assertEqual(sheet['A2'].number_format, '0.00')
        self.assertEqual(sheet['B2'].value, 1.2)
        self.assertEqual(sheet['B2'].number_format, '0.0')
        self.assertEqual(sheet['C2'].value, '9999999999999999,99')
        self.assertEqual(sheet['D2'].value, '1234567890123456')
        self.assertEqual(sheet['E2'].value, 100000000000000)
        self.assertEqual(sheet['E2'].number_format, '0.00')

    def test_sample_file_regression_keeps_all_columns_and_formats(self):
        repository_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.csv'
            source.write_bytes((repository_root / 'tests' / 'fixtures' / 'test_data.csv').read_bytes())
            progress = []
            result = process_csv_file(
                CsvProcessingOptions(str(source), ',', 'Polish', 'CSV', 5),
                lambda percent, event: progress.append((percent, event)),
            )
            output_files = list(Path(directory).glob('processed_output_*.csv'))
            self.assertEqual(len(output_files), 1)
            self.assertEqual(result.rows, 3)
            self.assertEqual(progress[0], (0, 'reading'))
            self.assertTrue(any(event.startswith('scanning:') for _, event in progress))
            self.assertTrue(any(event == 'saving' for _, event in progress))
            output = output_files[0]
            with output.open(encoding='utf-8-sig', newline='') as file:
                rows = list(csv.reader(file, delimiter=';'))

        self.assertEqual(rows[0], ['Id', 'Name', 'Balance', 'Date', 'Notes'])
        self.assertEqual(rows[1][2], '1234,56')
        self.assertEqual(rows[2][2], '500,00')
        self.assertEqual(rows[3][2], '1000000,00')
        self.assertEqual(rows[1][4], 'First line Second line')

    def test_large_csv_streams_without_pandas_and_repairs_across_progress_boundaries(self):
        row_count = 100000
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'large.csv'
            with source.open('w', encoding='utf-8', newline='') as file:
                writer = csv.writer(file)
                writer.writerow(['Id', 'Name', 'Notes'])
                for index in range(1, row_count + 1):
                    writer.writerow([index, f'Name {index}', f'Note {index}'])
                file.write('continued note\n')

            progress = []
            tracemalloc.start()
            try:
                with mock.patch(
                'src.csv_processing._get_pandas',
                    side_effect=AssertionError('text CSV must not create a DataFrame'),
                ):
                    result = process_csv_file(
                        CsvProcessingOptions(str(source), ',', 'English', 'CSV', 3),
                        lambda percent, event: progress.append((percent, event)),
                    )
                _current_memory, peak_memory = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()

            with Path(result.out_path).open(encoding='utf-8-sig', newline='') as file:
                output_row_count = 0
                last_row = None
                for output_row_count, last_row in enumerate(csv.reader(file), start=1):
                    pass

        self.assertEqual(result.rows, row_count)
        self.assertEqual(result.repaired, 1)
        self.assertEqual(output_row_count, row_count + 1)
        self.assertEqual(last_row[2], f'Note {row_count} continued note')
        self.assertLess(peak_memory, 32 * 1024 * 1024)
        self.assertTrue(any(event == 'scanning:5000' for _, event in progress))
        self.assertTrue(any(event == 'scanning:100000' for _, event in progress))

    def test_failed_streaming_publish_removes_temporary_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.csv'
            source.write_text('Id,Value\n1,10\n', encoding='utf-8')

            with mock.patch('src.csv_processing.os.replace', side_effect=OSError('publish failed')):
                with self.assertRaisesRegex(OSError, 'publish failed'):
                    process_csv_file(
                        CsvProcessingOptions(str(source), ',', 'English', 'CSV', 2)
                    )

            self.assertEqual(list(Path(directory).glob('processed_output_*.csv')), [])
            self.assertEqual(list(Path(directory).glob('.*.tmp')), [])

    def test_streaming_csv_to_excel_keeps_number_formats(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.csv'
            source.write_text(
                'Id,Amount,Large\n1,500.00,1234567890123456\n',
                encoding='utf-8',
            )
            result = process_csv_file(
                CsvProcessingOptions(
                    str(source),
                    ',',
                    'English',
                    'Excel (.xlsx)',
                    3,
                )
            )
            workbook = openpyxl.load_workbook(result.out_path, data_only=False)
            sheet = workbook.active

        self.assertEqual(sheet['B2'].value, 500)
        self.assertEqual(sheet['B2'].number_format, '0.00')
        self.assertEqual(sheet['C2'].value, '1234567890123456')
        self.assertEqual(result.large_numbers_as_text, 1)
