import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import os
import shutil
import sys
import webbrowser
from pathlib import Path

from src.background_jobs import BackgroundJobRunner, JobCallbacks
from src.csv_processing import (
    CsvNoDataError,
    CsvNoTableError,
    CsvProcessingOptions,
    detect_delimiter as detect_csv_delimiter,
    is_excel as is_excel_file,
    normalize_delimiter,
    process_csv_file,
    read_file_rows,
)

from src.promotion_normalizer import (
    EXCEL_MAX_DATA_ROWS,
    export_normalized,
    load_template,
    preview_daily_rows,
)
from src.aggregator_ui import AggregatorTabFrame
from src.update_checker import check_for_update, load_settings, save_settings
from src.ui_components import PALETTE, UpdateMenu

__version__ = "1.11.0"

_LANGUAGE_CODES = {
    "English": "en",
    "한국어": "ko",
    "Polski": "pl",
}

_UI_TEXT = {
    "ko": {
        "header_subtitle": "파일을 복구하고 분석 가능한 구조로 데이터를 정리합니다.",
        "language_label": "언어",
        "settings": "⚙ 설정",
        "section_file": "1. 원본 파일 선택",
        "browse": "파일 찾기...",
        "file_info": "CSV, TXT, Excel(.xlsx/.xlsm)을 고르세요. 원본 파일은 바꾸지 않습니다.",
        "section_import": "2. 파일 읽는 방법",
        "section_output": "3. 저장 방법",
        "delimiter_label": "파일 구분 기호",
        "delimiter_help": "열을 나누는 기호입니다: 쉼표(,), 세미콜론(;), 탭(\\t).",
        "number_label": "숫자 표기 방식",
        "number_options": ("영어식 · 1,234.56", "폴란드식 · 1 234,56"),
        "number_help_english": "영어식: 1,234.56 · 쉼표는 천 단위, 점은 소수점입니다.",
        "number_help_polish": "폴란드식: 1 234,56 · 공백/점은 천 단위, 쉼표는 소수점입니다.",
        "columns_label": "표의 열 개수",
        "columns_help": "파일을 고르면 자동 입력됩니다. 실제 열 수와 다를 때만 바꾸세요.",
        "output_label": "저장 파일 형식",
        "output_options": ("CSV 텍스트 파일 (.csv)", "Excel 통합 문서 (.xlsx)"),
        "output_help_csv": "CSV는 텍스트 파일입니다. Excel은 바로 열어 계산할 수 있습니다.",
        "output_help_excel": "Excel 파일로 저장합니다. 큰 숫자는 정확도를 위해 텍스트로 보존될 수 있습니다.",
        "process": "정리하고 저장하기",
        "result_title": "처리 결과 · 무엇이 정리되었나요?",
        "ready": "준비됨 · 파일을 고르고 숫자 표기 방식을 선택하세요.",
        "initial_result": "아직 처리한 파일이 없습니다. 파일을 고르고 ‘정리하고 저장하기’를 누르세요.",
        "output_hint": "저장 파일 이름: {name}",
        "output_hint_with_file": "저장 위치: 원본과 같은 폴더 · 이름: {name}",
        "dialog_title": "CSV, TXT 또는 Excel 파일 선택",
        "select_file_title": "파일을 선택해 주세요",
        "select_file_message": "정리할 CSV, TXT 또는 Excel 파일을 선택해 주세요.",
        "delimiter_title": "구분 기호 확인",
        "delimiter_message": "구분 기호는 한 글자여야 합니다. 탭은 \\t로 입력하세요.",
        "columns_title": "열 개수 확인",
        "columns_number": "표의 열 개수에는 숫자를 입력해 주세요.",
        "columns_positive": "표의 열 개수는 1 이상이어야 합니다.",
        "reading": "파일을 읽는 중...",
        "scanning": "{rows}개 행을 읽었습니다. 표 구조를 확인하는 중...",
        "converting": "숫자와 날짜를 정리하는 중... ({current}/{total}개 열)",
        "saving": "새 파일을 저장하는 중...",
        "done": "완료되었습니다.",
        "no_table_title": "처리할 표를 찾지 못했습니다",
        "no_table_message": "선택한 열 개수와 맞는 행이 없습니다.",
        "no_data_title": "데이터가 없습니다",
        "no_data_message": "열 이름은 찾았지만 정리할 데이터 행이 없습니다.",
        "error_title": "처리 중 오류",
        "error_message": "파일을 정리하지 못했습니다.\n\n{error}",
        "summary_saved": "저장 완료 · {name}",
        "summary_location": "저장 위치: {path}",
        "summary_title": "이번에 정리한 내용",
        "summary_rows": "• 데이터 행 {rows}개와 열 {columns}개를 새 파일로 저장했습니다.",
        "summary_garbage": "• 위쪽의 표가 아닌 행 {count}개를 건너뛰고, 첫 번째 정상 행을 열 이름으로 사용했습니다.",
        "summary_values": "• 숫자 표기 {numbers}개와 날짜 {dates}개를 읽기 쉬운 값으로 정리했습니다.",
        "summary_flattened": "• 셀 안의 줄바꿈 {count}개를 한 줄 텍스트로 바꿨습니다.",
        "summary_repaired": "• 여러 줄로 끊어진 행 {count}개를 다시 이었습니다.",
        "summary_large": "• Excel 정확도 보호를 위해 큰 숫자 {count}개를 텍스트로 보존했습니다.",
        "summary_encoding": "• 읽은 파일 인코딩: {encoding}",
        "status_done": "완료 · {rows}개 행 저장 → {name}",
        "detected_columns": "열 개수 자동 감지: {columns}개 · 인코딩: {encoding}",
        "detect_columns_error": "열 개수를 자동으로 찾지 못했습니다. 구분 기호를 확인하세요.",
    },
    "en": {
        "header_subtitle": "Repair files and prepare structured data for analysis.",
        "language_label": "Language",
        "settings": "⚙ Settings",
        "section_file": "1. Choose the source file",
        "browse": "Browse...",
        "file_info": "Choose a CSV, TXT, or Excel (.xlsx/.xlsm) file. The original is never changed.",
        "section_import": "2. How to read the file",
        "section_output": "3. How to save the result",
        "delimiter_label": "Column separator",
        "delimiter_help": "Column separator: comma (,), semicolon (;), or tab (\\t).",
        "number_label": "Number format",
        "number_options": ("English · 1,234.56", "Polish · 1 234,56"),
        "number_help_english": "English: 1,234.56 · comma for thousands, dot for decimals.",
        "number_help_polish": "Polish: 1 234,56 · space/dot for thousands, comma for decimals.",
        "columns_label": "Number of columns",
        "columns_help": "Detected after you choose a file. Change it only if it differs from the table.",
        "output_label": "Save as",
        "output_options": ("CSV text file (.csv)", "Excel workbook (.xlsx)"),
        "output_help_csv": "CSV is a text file. Excel can be opened and calculated immediately.",
        "output_help_excel": "Saves an Excel file. Very large numbers may be kept as text for accuracy.",
        "process": "Clean and save",
        "result_title": "Result · What was cleaned?",
        "ready": "Ready · Choose a file and its number format.",
        "initial_result": "No file has been processed yet. Choose a file, then select ‘Clean and save’.",
        "output_hint": "Output file name: {name}",
        "output_hint_with_file": "Saved beside the source file · name: {name}",
        "dialog_title": "Choose a CSV, TXT, or Excel file",
        "select_file_title": "Choose a file",
        "select_file_message": "Choose the CSV, TXT, or Excel file you want to clean.",
        "delimiter_title": "Check the column separator",
        "delimiter_message": "The separator must be one character. Enter \\t for a tab.",
        "columns_title": "Check the number of columns",
        "columns_number": "Enter a number for the number of columns.",
        "columns_positive": "The number of columns must be at least 1.",
        "reading": "Reading the file...",
        "scanning": "Read {rows} rows. Checking the table structure...",
        "converting": "Cleaning numbers and dates... ({current}/{total} columns)",
        "saving": "Saving the new file...",
        "done": "Finished.",
        "no_table_title": "No table found",
        "no_table_message": "No rows match the selected number of columns.",
        "no_data_title": "No data rows found",
        "no_data_message": "A header was found, but there are no data rows to clean.",
        "error_title": "Processing error",
        "error_message": "The file could not be cleaned.\n\n{error}",
        "summary_saved": "Saved · {name}",
        "summary_location": "Location: {path}",
        "summary_title": "What was cleaned",
        "summary_rows": "• Saved {rows} data rows and {columns} columns in a new file.",
        "summary_garbage": "• Skipped {count} non-table rows at the top and used the first complete row as headers.",
        "summary_values": "• Normalized {numbers} number values and {dates} dates.",
        "summary_flattened": "• Changed {count} in-cell line breaks into one-line text.",
        "summary_repaired": "• Rejoined {count} records that had been split across lines.",
        "summary_large": "• Kept {count} very large numbers as text to protect Excel accuracy.",
        "summary_encoding": "• Source encoding: {encoding}",
        "status_done": "Finished · saved {rows} rows → {name}",
        "detected_columns": "Detected {columns} columns · encoding: {encoding}",
        "detect_columns_error": "Could not detect the number of columns. Check the separator.",
    },
    "pl": {
        "header_subtitle": "Naprawia pliki i porządkuje dane w strukturę gotową do analizy.",
        "language_label": "Język",
        "settings": "⚙ Ustawienia",
        "section_file": "1. Wybierz plik źródłowy",
        "browse": "Wybierz plik...",
        "file_info": "Wybierz plik CSV, TXT lub Excel (.xlsx/.xlsm). Oryginał nie zostanie zmieniony.",
        "section_import": "2. Sposób odczytu pliku",
        "section_output": "3. Sposób zapisu wyniku",
        "delimiter_label": "Separator kolumn",
        "delimiter_help": "Separator kolumn: przecinek (,), średnik (;) albo tabulator (\\t).",
        "number_label": "Format liczb",
        "number_options": ("Format angielski · 1,234.56", "Format polski · 1 234,56"),
        "number_help_english": "Angielski: 1,234.56 · przecinek dla tysięcy, kropka dla części dziesiętnej.",
        "number_help_polish": "Polski: 1 234,56 · spacja/kropka dla tysięcy, przecinek dla części dziesiętnej.",
        "columns_label": "Liczba kolumn",
        "columns_help": "Wykrywana po wyborze pliku. Zmień ją tylko, gdy nie pasuje do tabeli.",
        "output_label": "Zapisz jako",
        "output_options": ("Plik tekstowy CSV (.csv)", "Skoroszyt Excel (.xlsx)"),
        "output_help_csv": "CSV to plik tekstowy. Excel można od razu otworzyć i używać do obliczeń.",
        "output_help_excel": "Zapisuje plik Excel. Bardzo duże liczby mogą pozostać tekstem dla zachowania dokładności.",
        "process": "Uporządkuj i zapisz",
        "result_title": "Wynik · Co zostało uporządkowane?",
        "ready": "Gotowe · wybierz plik i jego format liczb.",
        "initial_result": "Nie przetworzono jeszcze pliku. Wybierz plik, a następnie „Uporządkuj i zapisz”.",
        "output_hint": "Nazwa pliku wynikowego: {name}",
        "output_hint_with_file": "Zapis obok pliku źródłowego · nazwa: {name}",
        "dialog_title": "Wybierz plik CSV, TXT lub Excel",
        "select_file_title": "Wybierz plik",
        "select_file_message": "Wybierz plik CSV, TXT lub Excel, który chcesz uporządkować.",
        "delimiter_title": "Sprawdź separator kolumn",
        "delimiter_message": "Separator musi być jednym znakiem. Dla tabulatora wpisz \\t.",
        "columns_title": "Sprawdź liczbę kolumn",
        "columns_number": "Wprowadź liczbę kolumn jako liczbę.",
        "columns_positive": "Liczba kolumn musi wynosić co najmniej 1.",
        "reading": "Odczytywanie pliku...",
        "scanning": "Odczytano {rows} wierszy. Sprawdzanie struktury tabeli...",
        "converting": "Porządkowanie liczb i dat... ({current}/{total} kolumn)",
        "saving": "Zapisywanie nowego pliku...",
        "done": "Gotowe.",
        "no_table_title": "Nie znaleziono tabeli",
        "no_table_message": "Żaden wiersz nie pasuje do wybranej liczby kolumn.",
        "no_data_title": "Brak wierszy danych",
        "no_data_message": "Znaleziono nagłówek, ale nie ma danych do uporządkowania.",
        "error_title": "Błąd przetwarzania",
        "error_message": "Nie udało się uporządkować pliku.\n\n{error}",
        "summary_saved": "Zapisano · {name}",
        "summary_location": "Lokalizacja: {path}",
        "summary_title": "Co zostało uporządkowane",
        "summary_rows": "• Zapisano {rows} wierszy danych i {columns} kolumn w nowym pliku.",
        "summary_garbage": "• Pominięto {count} wierszy spoza tabeli na początku i użyto pierwszego pełnego wiersza jako nagłówków.",
        "summary_values": "• Ujednolicono {numbers} wartości liczbowych i {dates} dat.",
        "summary_flattened": "• Zamieniono {count} podziałów linii wewnątrz komórek na tekst jednoliniowy.",
        "summary_repaired": "• Połączono ponownie {count} rekordów podzielonych między wiersze.",
        "summary_large": "• Zachowano {count} bardzo dużych liczb jako tekst, aby chronić dokładność Excela.",
        "summary_encoding": "• Kodowanie pliku źródłowego: {encoding}",
        "status_done": "Gotowe · zapisano {rows} wierszy → {name}",
        "detected_columns": "Wykryto {columns} kolumn · kodowanie: {encoding}",
        "detect_columns_error": "Nie udało się wykryć liczby kolumn. Sprawdź separator.",
    },
}

_UI_TEXT["en"].update({
    "task_label": "Task",
    "task_options": ("CSV repair", "Promotion template", "Data aggregator"),
    "agg_initial_result": "Select a CSV file to inspect columns, configure group keys and measures, or load a saved preset.",
    "update_enabled": "Check for updates automatically",
    "check_updates": "Check now",
    "hide_updates": "Hide updates",
    "checking_updates": "Checking GitHub for updates…",
    "update_current": "You have the latest version.",
    "update_off": "Automatic update checks are off.",
    "update_available": "Version {version} is available.",
    "download_update": "Download update",
    "promo_file_section": "Promotion template",
    "promo_file_info": "Use the two-sheet Excel template. Dates must use YYYY-MM-DD.",
    "promo_download": "Download template…",
    "promo_browse": "Choose template…",
    "promo_output_section": "Daily support output",
    "promo_output_label": "Save daily support as",
    "promo_output_options": ("CSV daily support (.csv)", "Excel daily support (.xlsx)"),
    "promo_output_help": "Promotion master and support rules are always saved as separate CSV files.",
    "promo_process": "Create daily support files",
    "promo_result_title": "Promotion template result",
    "promo_initial_result": "Download the template, enter promotion rules, then choose the completed Excel file.",
    "promo_select_title": "Choose a promotion template",
    "promo_select_message": "Choose the completed promotion template (.xlsx).",
    "promo_template_saved": "Template saved: {name}",
    "promo_invalid": "Template needs attention ({count} issue(s))",
    "promo_valid": "Template is ready · {rules} support rules create {rows} daily rows.",
    "promo_preview": "Preview (first {count} daily rows)",
    "promo_overlap": "Overlapping rule pairs for the same model: {count}. They are kept separately.",
    "promo_saving": "Creating compact and daily support files…",
    "promo_done": "Created {rows} daily support rows.",
    "promo_summary_title": "Files created",
    "promo_master_file": "• Promotion master: {name}",
    "promo_rules_file": "• Support rules: {name}",
    "promo_daily_file": "• Daily support: {name}",
    "promo_issue_more": "• … and {count} more issue(s).",
    "promo_excel_limit": "Excel output is limited to {limit:,} data rows. Choose CSV for this template.",
})
_UI_TEXT["ko"].update({
    "task_label": "작업 방식",
    "task_options": ("CSV 구조 복구", "프로모션 템플릿", "데이터 집계·슬라이서"),
    "agg_initial_result": "대용량 CSV 파일을 선택하여 컬럼을 분석하고, 행 그룹과 합산 항목을 지정하거나 저장된 프리셋을 불러오세요.",
    "update_enabled": "새 버전 자동 확인",
    "check_updates": "지금 확인",
    "hide_updates": "업데이트 숨기기",
    "checking_updates": "GitHub에서 새 버전을 확인하는 중…",
    "update_current": "현재 최신 버전을 사용하고 있습니다.",
    "update_off": "새 버전 자동 확인이 꺼져 있습니다.",
    "update_available": "새 버전 {version}을 사용할 수 있습니다.",
    "download_update": "업데이트 다운로드",
    "promo_file_section": "프로모션 템플릿",
    "promo_file_info": "두 시트로 된 Excel 템플릿을 사용합니다. 날짜는 YYYY-MM-DD 형식으로 입력하세요.",
    "promo_download": "템플릿 받기…",
    "promo_browse": "작성한 템플릿 선택…",
    "promo_output_section": "일별 지원금 저장",
    "promo_output_label": "일별 지원금 파일 형식",
    "promo_output_options": ("CSV 일별 지원금 (.csv)", "Excel 일별 지원금 (.xlsx)"),
    "promo_output_help": "프로모션 마스터와 지원 규칙은 별도의 CSV 파일로 항상 함께 저장됩니다.",
    "promo_process": "일별 지원금 파일 만들기",
    "promo_result_title": "프로모션 템플릿 결과",
    "promo_initial_result": "템플릿을 받은 뒤 프로모션 규칙을 입력하고, 작성한 Excel 파일을 선택하세요.",
    "promo_select_title": "프로모션 템플릿 선택",
    "promo_select_message": "작성한 프로모션 템플릿(.xlsx)을 선택하세요.",
    "promo_template_saved": "템플릿 저장 완료: {name}",
    "promo_invalid": "템플릿에 확인할 내용이 있습니다 ({count}개).",
    "promo_valid": "템플릿 준비 완료 · 지원 규칙 {rules}개가 일별 행 {rows}개를 만듭니다.",
    "promo_preview": "미리보기 (일별 결과 처음 {count}개)",
    "promo_overlap": "같은 모델에서 겹치는 지원 규칙 쌍: {count}개 · 각각 별도로 유지됩니다.",
    "promo_saving": "기준 파일과 일별 지원금 파일을 만드는 중…",
    "promo_done": "일별 지원금 행 {rows}개를 만들었습니다.",
    "promo_summary_title": "생성된 파일",
    "promo_master_file": "• 프로모션 마스터: {name}",
    "promo_rules_file": "• 지원 규칙: {name}",
    "promo_daily_file": "• 일별 지원금: {name}",
    "promo_issue_more": "• 그 외 {count}개 문제",
    "promo_excel_limit": "Excel은 데이터 행을 최대 {limit:,}개까지만 저장할 수 있습니다. 이 템플릿은 CSV를 선택하세요.",
})
_UI_TEXT["pl"].update({
    "task_label": "Zadanie",
    "task_options": ("Naprawa CSV", "Szablon promocji", "Agregacja danych"),
    "agg_initial_result": "Wybierz plik CSV, aby przeanalizować kolumny, skonfigurować grupowanie lub wczytać szablon.",
    "update_enabled": "Sprawdzaj aktualizacje automatycznie",
    "check_updates": "Sprawdź teraz",
    "hide_updates": "Ukryj aktualizacje",
    "checking_updates": "Sprawdzanie aktualizacji na GitHubie…",
    "update_current": "Używasz najnowszej wersji.",
    "update_off": "Automatyczne sprawdzanie aktualizacji jest wyłączone.",
    "update_available": "Dostępna jest wersja {version}.",
    "download_update": "Pobierz aktualizację",
    "promo_file_section": "Szablon promocji",
    "promo_file_info": "Użyj szablonu Excel z dwoma arkuszami. Daty wpisuj jako YYYY-MM-DD.",
    "promo_download": "Pobierz szablon…",
    "promo_browse": "Wybierz szablon…",
    "promo_output_section": "Dzienna dopłata",
    "promo_output_label": "Zapisz dzienną dopłatę jako",
    "promo_output_options": ("Dzienna dopłata CSV (.csv)", "Dzienna dopłata Excel (.xlsx)"),
    "promo_output_help": "Master promocji i reguły dopłat są zawsze zapisywane jako osobne pliki CSV.",
    "promo_process": "Utwórz dzienne pliki dopłat",
    "promo_result_title": "Wynik szablonu promocji",
    "promo_initial_result": "Pobierz szablon, wpisz reguły promocji, a następnie wybierz gotowy plik Excel.",
    "promo_select_title": "Wybierz szablon promocji",
    "promo_select_message": "Wybierz gotowy szablon promocji (.xlsx).",
    "promo_template_saved": "Zapisano szablon: {name}",
    "promo_invalid": "Szablon wymaga poprawek ({count}).",
    "promo_valid": "Szablon jest gotowy · {rules} reguł tworzy {rows} dziennych wierszy.",
    "promo_preview": "Podgląd (pierwsze {count} dziennych wierszy)",
    "promo_overlap": "Nakładające się pary reguł dla tego samego modelu: {count}. Są zachowane osobno.",
    "promo_saving": "Tworzenie plików źródłowych i dziennych dopłat…",
    "promo_done": "Utworzono {rows} dziennych wierszy dopłat.",
    "promo_summary_title": "Utworzone pliki",
    "promo_master_file": "• Master promocji: {name}",
    "promo_rules_file": "• Reguły dopłat: {name}",
    "promo_daily_file": "• Dzienna dopłata: {name}",
    "promo_issue_more": "• … oraz {count} kolejnych problemów.",
    "promo_excel_limit": "Excel zapisuje najwyżej {limit:,} wierszy danych. Wybierz CSV dla tego szablonu.",
})

# --- Data aggregator tab -------------------------------------------------
_UI_TEXT["en"].update({
    'agg_undo': '↶ Undo',
    'agg_fn_sum': 'Sum',
    'agg_fn_mean': 'Average',
    'agg_fn_count': 'Count',
    'agg_fn_min': 'Minimum',
    'agg_fn_max': 'Maximum',
    'agg_preview_title': 'Preview · sampled result',
    'agg_preview_close': 'Close',
    'agg_preview_counting': 'Counting the rows the full run will produce…',
    'agg_preview_summary': '{shown} sampled rows shown · the full run produces {rows} rows',
    'agg_preview_summary_capped': '{shown} sampled rows shown · the full run produces more than {rows} rows',
    'agg_preview_summary_sample': '{shown} rows from the sample · the full total could not be counted',
    'agg_search_placeholder': 'Type to find a column…',
    'agg_preset_menu': 'Presets ▾',
    'agg_year_tag': '[YYYY]',
    'agg_search_clear': 'Clear',
    'agg_btn_constant': '✎ Fixed value',
    'agg_hint_constant': '{name} = {value} on every row',
    'agg_hint_year': 'The year taken from {month} (YYYYMM ➔ YYYY)',
    'agg_msg_schema_restored': '{name} ({count} columns) · restored your last setup',
    'agg_dlg_constant_title': 'Add a fixed-value column',
    'agg_dlg_constant_name': 'Column header:',
    'agg_dlg_constant_value': 'Value:',
    'agg_dlg_constant_default_name': 'YYYY',
    'agg_dlg_constant_note': 'The same value is written on every row. Use it to stamp a result, for example a header YYYY carrying 2026. Drop it into Row groups to include it in the output.',
    'agg_dlg_constant_incomplete': 'Enter both a column header and a value.',
    'agg_output_dir': 'Folder',
    'agg_output_name': 'File name',
    'agg_browse_folder': 'Browse…',
    'agg_open_folder': '📂 Open folder',
    'agg_open_file': '📄 Open file',
    'agg_msg_choose_folder': 'Choose the folder to save into',
    'agg_msg_open_failed_title': 'Could not open',
    'agg_msg_open_failed': 'The file could not be opened:\n{path}',
    'agg_msg_bad_folder': 'This folder does not exist:\n{folder}',
    'agg_msg_overwrite_title': 'File already exists',
    'agg_msg_overwrite': "'{name}' already exists in that folder.\nOverwrite it?",
    'agg_dlg_formula_format': 'Display as:',
    'agg_dlg_format_percent': 'Percentage (%)',
    'agg_dlg_format_ratio': 'Ratio',
    'agg_dlg_format_number': 'Plain number',
    'agg_file_label': 'Source',
    'agg_browse': 'Browse…',
    'agg_file_dialog_title': 'Select a large data file (CSV)',
    'agg_preset_label': 'Preset:',
    'agg_preset_load': 'Load',
    'agg_preset_save': 'Save',
    'agg_preset_export': 'Export',
    'agg_preset_import': 'Import',
    'agg_preset_delete': 'Delete',
    'agg_source_card': ' Source columns (double-click or drag) ',
    'agg_dim_header': '📁 Dimensions / keys',
    'agg_measure_header': '📊 Measures / values',
    'agg_month_tag': '[month]',
    'agg_rules_card': ' Aggregation rules ',
    'agg_rows_box': '1. Row groups',
    'agg_values_box': '2. Values',
    'agg_filters_box': 'Filters',
    'agg_btn_group_rule': '∑ Combine',
    'agg_btn_formula_rule': '% Ratio',
    'agg_btn_add_filter': '+ Filter',
    'agg_hint_dimensions': 'Select a source file',
    'agg_hint_measures': 'Select a source file',
    'agg_hint_rows': 'Drop a dimension here',
    'agg_hint_values': 'Drop a measure here',
    'agg_hint_filters': 'No filter · every row is included',
    'agg_preview': '🔍 Preview (sample)',
    'agg_cancel': 'Cancel',
    'agg_run': '★ Aggregate and save',
    'agg_dlg_add': 'Add',
    'agg_dlg_cancel': 'Cancel',
    'agg_dlg_check_title': 'Check your input',
    'agg_dlg_name_taken': 'A column or rule with that name already exists. Choose another name.',
    'agg_dlg_group_title': 'Combine columns (sum)',
    'agg_dlg_group_name': 'New combined column name:',
    'agg_dlg_group_sources': 'Source measure columns to add up (Ctrl-click for several):',
    'agg_dlg_group_default_name': 'combined_column',
    'agg_dlg_group_incomplete': 'Enter a column name and select at least one source column.',
    'agg_dlg_formula_title': 'Add a ratio / derived column',
    'agg_dlg_formula_name': 'New formula column name:',
    'agg_dlg_formula_numerator': 'Numerator:',
    'agg_dlg_formula_denominator': '÷ Denominator:',
    'agg_dlg_formula_multiplier': '× Multiplier:',
    'agg_dlg_formula_default_name': 'Margin (%)',
    'agg_dlg_formula_incomplete': 'Enter a name, a numerator and a denominator.',
    'agg_dlg_formula_note': "Ratios are calculated after the group totals. A percentage is stored as a ratio (0.27) and shown as 27.00% by the spreadsheet's own format. Columns created by earlier rules can be used as the numerator or denominator.",
    'agg_dlg_filter_title': 'Add a filter condition',
    'agg_dlg_filter_column': 'Column:',
    'agg_dlg_filter_operator': 'Operator:',
    'agg_dlg_filter_value': 'Value:',
    'agg_dlg_filter_note': 'Values are compared as raw text. Separate several values with commas for in / not in.',
    'agg_dlg_filter_incomplete': 'Choose a column and an operator.',
    'agg_dlg_filter_value_required': 'Enter a value to compare against.',
    'agg_msg_notice': 'Notice',
    'agg_msg_select_file_first': 'Select a source file first.',
    'agg_msg_schema_progress': "Inspecting the file's column structure…",
    'agg_msg_schema_done': 'Columns detected ({count} columns)',
    'agg_msg_schema_log': 'File inspected: {name} ({count} columns)',
    'agg_msg_schema_error_title': 'File inspection error',
    'agg_msg_schema_error': 'The file schema could not be read:\n{error}',
    'agg_msg_cascade_title': 'Delete dependent rules',
    'agg_msg_cascade': "Deleting '{name}' also deletes the rules that use it:\n{dependents}\n\nContinue?",
    'agg_msg_need_file_title': 'File required',
    'agg_msg_need_file': 'Select the source CSV file to process first.',
    'agg_msg_config_title': 'Check your settings',
    'agg_msg_need_group_key': 'Choose at least one row group (group-by key).',
    'agg_msg_need_measure': "Put at least one field into the '2. Values' area.",
    'agg_msg_preset_missing': 'Select a preset first.',
    'agg_msg_preset_load_error_title': 'Preset error',
    'agg_msg_preset_load_error': 'The preset could not be loaded:\n{error}',
    'agg_msg_preset_mismatch_title': 'Column mismatch',
    'agg_msg_preset_mismatch': 'The preset refers to columns that are missing from the current file:\n{columns}\n\nApply it anyway?',
    'agg_msg_preset_applied': "Preset '{name}' applied",
    'agg_msg_preset_desc': 'Preset: {name}\nDescription: {description}',
    'agg_msg_preset_save_title': 'Save preset',
    'agg_msg_preset_save_prompt': 'Enter a name for the preset:',
    'agg_msg_preset_memo_title': 'Preset note',
    'agg_msg_preset_memo_prompt': 'Enter a description or note (optional):',
    'agg_msg_preset_overwrite_title': 'Confirm overwrite',
    'agg_msg_preset_overwrite': "A preset named '{name}' already exists.\nOverwrite the saved settings?",
    'agg_msg_preset_saved_title': 'Saved',
    'agg_msg_preset_saved': "Preset '{name}' has been saved.",
    'agg_msg_preset_export_title': 'Export preset',
    'agg_msg_preset_export_done_title': 'Export complete',
    'agg_msg_preset_export_done': 'The preset was exported successfully:\n{path}',
    'agg_msg_preset_export_error': 'Export error',
    'agg_msg_preset_import_title': 'Import a shared preset file',
    'agg_msg_preset_import_done_title': 'Import complete',
    'agg_msg_preset_import_done': "Preset '{name}' was imported successfully.",
    'agg_msg_preset_import_error': 'Import error',
    'agg_msg_preset_import_error_body': 'The preset file could not be imported:\n{error}',
    'agg_msg_preset_delete_title': 'Delete preset',
    'agg_msg_preset_delete': "Delete the preset '{name}'?",
    'agg_msg_busy_title': 'Busy',
    'agg_msg_busy': 'Another job is running. Try again once it finishes.',
    'agg_msg_cancel_requested': 'Cancellation requested… cleaning up',
    'agg_msg_preview_running': 'Building a quick preview from the first 2,000 rows…',
    'agg_msg_preview_done': 'Preview ready',
    'agg_msg_preview_error_title': 'Preview error',
    'agg_msg_preview_error': 'The preview could not be created:\n{error}',
    'agg_msg_prepare': 'Preparing the aggregation…',
    'agg_msg_done_progress': 'Aggregation complete.',
    'agg_msg_saved_log': 'Saved: {name}',
    'agg_msg_rollup_on': 'applied',
    'agg_msg_rollup_off': 'not applied',
    'agg_msg_coerced': '\n\n⚠️ Note: {count:,} invalid or missing numeric values were replaced with 0.',
    'agg_msg_result_body': 'Aggregation complete.\n\n• File: {name}\n• Folder: {folder}\n• Rows written: {rows}\n• Grouped by: {keys}\n• Annual roll-up: {rollup}\n• Value columns: {columns}{warning}\n\n=== First 10 rows ===\n{preview}',
    'agg_msg_complete_title': 'Aggregation complete',
    'agg_msg_complete': 'The data aggregation finished successfully.\n\nSaved to:\n{path}',
    'agg_msg_complete_coerced': '\n\n({count:,} invalid or missing numeric values were replaced with 0.)',
    'agg_msg_cancelled_progress': 'Cancelled',
    'agg_msg_cancelled_log': 'The aggregation was cancelled by the user.',
    'agg_msg_cancelled_text': 'The aggregation was cancelled.',
    'agg_msg_error_progress': 'Error',
    'agg_msg_error_log': 'Aggregation error',
    'agg_msg_error_title': 'Aggregation error',
    'agg_msg_error': 'The aggregation failed:\n{error}',
})
_UI_TEXT["ko"].update({
    'agg_undo': '↶ 되돌리기',
    'agg_fn_sum': '합계',
    'agg_fn_mean': '평균',
    'agg_fn_count': '개수',
    'agg_fn_min': '최솟값',
    'agg_fn_max': '최댓값',
    'agg_preview_title': '미리보기 · 샘플 집계 결과',
    'agg_preview_close': '닫기',
    'agg_preview_counting': '전체 실행 시 생성될 행 수를 세는 중…',
    'agg_preview_summary': '샘플 {shown}행 표시 · 전체 실행하면 {rows}행이 만들어집니다',
    'agg_preview_summary_capped': '샘플 {shown}행 표시 · 전체 실행하면 {rows}행보다 많아집니다',
    'agg_preview_summary_sample': '샘플 {shown}행 표시 · 전체 행 수는 세지 못했습니다',
    'agg_search_placeholder': '컬럼 이름을 입력해 찾기…',
    'agg_preset_menu': '프리셋 ▾',
    'agg_year_tag': '[연]',
    'agg_search_clear': '지우기',
    'agg_btn_constant': '✎ 고정값',
    'agg_hint_constant': '모든 행에 {name} = {value}',
    'agg_hint_year': '{month} 에서 뽑은 연도 (YYYYMM ➔ YYYY)',
    'agg_msg_schema_restored': '{name} ({count}개 컬럼) · 지난 설정을 복원했습니다',
    'agg_dlg_constant_title': '고정값 컬럼 추가',
    'agg_dlg_constant_name': '컬럼 헤더:',
    'agg_dlg_constant_value': '값:',
    'agg_dlg_constant_default_name': 'YYYY',
    'agg_dlg_constant_note': '모든 행에 같은 값이 기록됩니다. 예를 들어 헤더 YYYY 에 2026 을 넣어 결과에 표시할 때 씁니다. 행 그룹으로 끌어다 놓아야 출력에 포함됩니다.',
    'agg_dlg_constant_incomplete': '컬럼 헤더와 값을 모두 입력해 주세요.',
    'agg_output_dir': '폴더',
    'agg_output_name': '파일 이름',
    'agg_browse_folder': '찾아보기…',
    'agg_open_folder': '📂 폴더 열기',
    'agg_open_file': '📄 파일 열기',
    'agg_msg_choose_folder': '저장할 폴더 선택',
    'agg_msg_open_failed_title': '열 수 없음',
    'agg_msg_open_failed': '파일을 열지 못했습니다:\n{path}',
    'agg_msg_bad_folder': '다음 폴더가 존재하지 않습니다:\n{folder}',
    'agg_msg_overwrite_title': '파일이 이미 있습니다',
    'agg_msg_overwrite': "해당 폴더에 '{name}' 파일이 이미 있습니다.\n덮어쓰시겠습니까?",
    'agg_dlg_formula_format': '표시 형식:',
    'agg_dlg_format_percent': '백분율 (%)',
    'agg_dlg_format_ratio': '비율 (배수)',
    'agg_dlg_format_number': '일반 숫자',
    'agg_file_label': '소스',
    'agg_browse': '파일 찾기...',
    'agg_file_dialog_title': '대용량 데이터 파일 선택 (CSV)',
    'agg_preset_label': '프리셋:',
    'agg_preset_load': '불러오기',
    'agg_preset_save': '저장',
    'agg_preset_export': '내보내기',
    'agg_preset_import': '가져오기',
    'agg_preset_delete': '삭제',
    'agg_source_card': ' 원본 컬럼 탐색기 (더블클릭 또는 드래그) ',
    'agg_dim_header': '📁 차원/키',
    'agg_measure_header': '📊 수치/값',
    'agg_month_tag': '[월]',
    'agg_rules_card': ' 집계 규칙 설정 ',
    'agg_rows_box': '1. 행 그룹 (Rows)',
    'agg_values_box': '2. 값 (Values)',
    'agg_filters_box': '조건 필터',
    'agg_btn_group_rule': '∑ 묶기',
    'agg_btn_formula_rule': '% 비율식',
    'agg_btn_add_filter': '+ 필터',
    'agg_hint_dimensions': '원본 파일을 선택하세요',
    'agg_hint_measures': '원본 파일을 선택하세요',
    'agg_hint_rows': '차원을 끌어다 놓으세요',
    'agg_hint_values': '수치를 끌어다 놓으세요',
    'agg_hint_filters': '필터 없음 · 모든 행이 집계됩니다',
    'agg_preview': '🔍 미리보기 (샘플)',
    'agg_cancel': '취소',
    'agg_run': '★ 데이터 집계 및 저장하기',
    'agg_dlg_add': '추가',
    'agg_dlg_cancel': '취소',
    'agg_dlg_check_title': '입력 확인',
    'agg_dlg_name_taken': '이미 같은 이름의 컬럼 또는 규칙이 있습니다. 다른 이름을 사용해 주세요.',
    'agg_dlg_group_title': '컬럼 묶기(합산) 추가',
    'agg_dlg_group_name': '새 묶음 컬럼 이름:',
    'agg_dlg_group_sources': '합산할 원본 수치 컬럼들 (Ctrl 누르고 다중 선택):',
    'agg_dlg_group_default_name': '새_묶음_컬럼',
    'agg_dlg_group_incomplete': '컬럼 이름과 최소 하나 이상의 합산 컬럼을 선택해 주세요.',
    'agg_dlg_formula_title': '비율/파생 수식 추가',
    'agg_dlg_formula_name': '새 수식 컬럼 이름:',
    'agg_dlg_formula_numerator': '분자:',
    'agg_dlg_formula_denominator': '÷ 분모:',
    'agg_dlg_formula_multiplier': '× 승수:',
    'agg_dlg_formula_default_name': '이익율(%)',
    'agg_dlg_formula_incomplete': '이름, 분자, 분모를 모두 지정해 주세요.',
    'agg_dlg_formula_note': '비율은 그룹 합산이 끝난 뒤에 계산됩니다. 백분율은 비율값(0.27)으로 저장되고 엑셀 서식이 27.00%로 표시합니다. 앞서 만든 묶음·비율 컬럼도 분자·분모로 쓸 수 있습니다.',
    'agg_dlg_filter_title': '필터 조건 추가',
    'agg_dlg_filter_column': '대상 컬럼:',
    'agg_dlg_filter_operator': '조건 연산자:',
    'agg_dlg_filter_value': '비교 값:',
    'agg_dlg_filter_note': '※ 텍스트 원본 기준 일치 비교 (in / not in 은 콤마로 여러 값 구분)',
    'agg_dlg_filter_incomplete': '컬럼과 연산자를 지정해 주세요.',
    'agg_dlg_filter_value_required': '비교할 값을 입력해 주세요.',
    'agg_msg_notice': '알림',
    'agg_msg_select_file_first': '먼저 원본 파일을 선택해 주세요.',
    'agg_msg_schema_progress': '파일 컬럼 구조를 분석하는 중...',
    'agg_msg_schema_done': '컬럼 감지 완료 ({count}개 열)',
    'agg_msg_schema_log': '파일 감지: {name} (총 {count}개 컬럼)',
    'agg_msg_schema_error_title': '파일 분석 오류',
    'agg_msg_schema_error': '파일 스키마를 읽을 수 없습니다:\n{error}',
    'agg_msg_cascade_title': '연결된 규칙 삭제',
    'agg_msg_cascade': "'{name}' 을(를) 삭제하면 이를 참조하는 규칙도 함께 삭제됩니다:\n{dependents}\n\n계속하시겠습니까?",
    'agg_msg_need_file_title': '파일 선택 필요',
    'agg_msg_need_file': '먼저 처리할 원본 CSV 파일을 선택해 주세요.',
    'agg_msg_config_title': '설정 확인',
    'agg_msg_need_group_key': '최소 하나 이상의 행 그룹(Group By 키)을 선택해 주세요.',
    'agg_msg_need_measure': "집계할 값을 최소 하나 이상 '2. 값' 영역에 올려 주세요.",
    'agg_msg_preset_missing': '프리셋을 선택해 주세요.',
    'agg_msg_preset_load_error_title': '프리셋 오류',
    'agg_msg_preset_load_error': '프리셋을 불러오지 못했습니다:\n{error}',
    'agg_msg_preset_mismatch_title': '컬럼 불일치 경고',
    'agg_msg_preset_mismatch': '선택한 프리셋의 컬럼 중 현재 파일에 없는 컬럼이 있습니다:\n{columns}\n\n계속 적용하시겠습니까?',
    'agg_msg_preset_applied': "프리셋 '{name}' 적용 완료",
    'agg_msg_preset_desc': '프리셋: {name}\n설명: {description}',
    'agg_msg_preset_save_title': '프리셋 저장',
    'agg_msg_preset_save_prompt': '저장할 프리셋의 이름을 입력하세요:',
    'agg_msg_preset_memo_title': '프리셋 메모',
    'agg_msg_preset_memo_prompt': '설명 또는 메모를 입력하세요 (선택 사항):',
    'agg_msg_preset_overwrite_title': '프리셋 덮어쓰기 확인',
    'agg_msg_preset_overwrite': "이미 '{name}' 이름의 프리셋이 존재합니다.\n기존 설정을 덮어쓰시겠습니까?",
    'agg_msg_preset_saved_title': '저장 완료',
    'agg_msg_preset_saved': "프리셋 '{name}'이(가) 저장되었습니다.",
    'agg_msg_preset_export_title': '프리셋 내보내기',
    'agg_msg_preset_export_done_title': '내보내기 완료',
    'agg_msg_preset_export_done': '프리셋을 성공적으로 내보냈습니다:\n{path}',
    'agg_msg_preset_export_error': '내보내기 오류',
    'agg_msg_preset_import_title': '공유된 프리셋 파일 가져오기',
    'agg_msg_preset_import_done_title': '가져오기 완료',
    'agg_msg_preset_import_done': "프리셋 '{name}'을(를) 성공적으로 가져왔습니다.",
    'agg_msg_preset_import_error': '가져오기 오류',
    'agg_msg_preset_import_error_body': '프리셋 파일을 가져오지 못했습니다:\n{error}',
    'agg_msg_preset_delete_title': '프리셋 삭제',
    'agg_msg_preset_delete': "정말로 프리셋 '{name}'을(를) 삭제하시겠습니까?",
    'agg_msg_busy_title': '작업 중',
    'agg_msg_busy': '현재 다른 작업이 실행 중입니다. 완료 후 다시 시도해 주세요.',
    'agg_msg_cancel_requested': '집계 취소 요청됨... 정리 중',
    'agg_msg_preview_running': '샘플 2,000행 대상 간이 집계 미리보기 생성 중...',
    'agg_msg_preview_done': '미리보기 표시 완료',
    'agg_msg_preview_error_title': '미리보기 오류',
    'agg_msg_preview_error': '미리보기를 생성하지 못했습니다:\n{error}',
    'agg_msg_prepare': '집계 준비 중...',
    'agg_msg_done_progress': '집계 완료!',
    'agg_msg_saved_log': '저장 완료: {name}',
    'agg_msg_rollup_on': '적용됨',
    'agg_msg_rollup_off': '미적용',
    'agg_msg_coerced': '\n\n⚠️ 주의: 비정상 또는 결측 수치 데이터 {count:,}건이 0으로 자동 치환되었습니다.',
    'agg_msg_result_body': '데이터 집계 완료!\n\n• 저장 파일: {name}\n• 저장 경로: {folder}\n• 최종 집계 행 수: {rows}행\n• 집계 기준: {keys}\n• 연간 롤업: {rollup}\n• 계산 항목: {columns}개 컬럼{warning}\n\n=== 상위 10행 미리보기 ===\n{preview}',
    'agg_msg_complete_title': '집계 완료',
    'agg_msg_complete': '데이터 집계가 성공적으로 완료되었습니다.\n\n저장 위치:\n{path}',
    'agg_msg_complete_coerced': '\n\n(비정상/결측 수치 {count:,}건이 0으로 보정되었습니다.)',
    'agg_msg_cancelled_progress': '작업 취소됨',
    'agg_msg_cancelled_log': '사용자에 의해 집계가 취소되었습니다.',
    'agg_msg_cancelled_text': '집계 작업이 취소되었습니다.',
    'agg_msg_error_progress': '오류 발생',
    'agg_msg_error_log': '집계 오류 발생',
    'agg_msg_error_title': '집계 오류',
    'agg_msg_error': '집계 중 오류가 발생했습니다:\n{error}',
})
_UI_TEXT["pl"].update({
    'agg_undo': '↶ Cofnij',
    'agg_fn_sum': 'Suma',
    'agg_fn_mean': 'Średnia',
    'agg_fn_count': 'Liczba',
    'agg_fn_min': 'Minimum',
    'agg_fn_max': 'Maksimum',
    'agg_preview_title': 'Podgląd · wynik z próbki',
    'agg_preview_close': 'Zamknij',
    'agg_preview_counting': 'Liczenie wierszy pełnego uruchomienia…',
    'agg_preview_summary': 'Pokazano {shown} wierszy z próbki · pełne uruchomienie da {rows} wierszy',
    'agg_preview_summary_capped': 'Pokazano {shown} wierszy z próbki · pełne uruchomienie da ponad {rows} wierszy',
    'agg_preview_summary_sample': 'Pokazano {shown} wierszy z próbki · nie udało się policzyć całości',
    'agg_search_placeholder': 'Wpisz, aby znaleźć kolumnę…',
    'agg_preset_menu': 'Szablony ▾',
    'agg_year_tag': '[RRRR]',
    'agg_search_clear': 'Wyczyść',
    'agg_btn_constant': '✎ Stała',
    'agg_hint_constant': '{name} = {value} w każdym wierszu',
    'agg_hint_year': 'Rok odczytany z {month} (YYYYMM ➔ YYYY)',
    'agg_msg_schema_restored': '{name} ({count} kolumn) · przywrócono ostatnie ustawienia',
    'agg_dlg_constant_title': 'Dodaj kolumnę o stałej wartości',
    'agg_dlg_constant_name': 'Nagłówek kolumny:',
    'agg_dlg_constant_value': 'Wartość:',
    'agg_dlg_constant_default_name': 'YYYY',
    'agg_dlg_constant_note': 'Ta sama wartość trafia do każdego wiersza. Przydaje się do oznaczenia wyniku, np. nagłówek YYYY z wartością 2026. Przeciągnij ją do Grup wierszy, aby znalazła się w wyniku.',
    'agg_dlg_constant_incomplete': 'Podaj nagłówek kolumny i wartość.',
    'agg_output_dir': 'Folder',
    'agg_output_name': 'Nazwa pliku',
    'agg_browse_folder': 'Przeglądaj…',
    'agg_open_folder': '📂 Otwórz folder',
    'agg_open_file': '📄 Otwórz plik',
    'agg_msg_choose_folder': 'Wybierz folder zapisu',
    'agg_msg_open_failed_title': 'Nie można otworzyć',
    'agg_msg_open_failed': 'Nie udało się otworzyć pliku:\n{path}',
    'agg_msg_bad_folder': 'Ten folder nie istnieje:\n{folder}',
    'agg_msg_overwrite_title': 'Plik już istnieje',
    'agg_msg_overwrite': 'W tym folderze plik „{name}” już istnieje.\nNadpisać go?',
    'agg_dlg_formula_format': 'Format wyświetlania:',
    'agg_dlg_format_percent': 'Procent (%)',
    'agg_dlg_format_ratio': 'Współczynnik',
    'agg_dlg_format_number': 'Zwykła liczba',
    'agg_file_label': 'Źródło',
    'agg_browse': 'Przeglądaj…',
    'agg_file_dialog_title': 'Wybierz duży plik danych (CSV)',
    'agg_preset_label': 'Szablon:',
    'agg_preset_load': 'Wczytaj',
    'agg_preset_save': 'Zapisz',
    'agg_preset_export': 'Eksportuj',
    'agg_preset_import': 'Importuj',
    'agg_preset_delete': 'Usuń',
    'agg_source_card': ' Kolumny źródłowe (kliknij dwukrotnie lub przeciągnij) ',
    'agg_dim_header': '📁 Wymiary / klucze',
    'agg_measure_header': '📊 Miary / wartości',
    'agg_month_tag': '[miesiąc]',
    'agg_rules_card': ' Reguły agregacji ',
    'agg_rows_box': '1. Grupy wierszy',
    'agg_values_box': '2. Wartości',
    'agg_filters_box': 'Filtry',
    'agg_btn_group_rule': '∑ Połącz',
    'agg_btn_formula_rule': '% Wskaźnik',
    'agg_btn_add_filter': '+ Filtr',
    'agg_hint_dimensions': 'Wybierz plik źródłowy',
    'agg_hint_measures': 'Wybierz plik źródłowy',
    'agg_hint_rows': 'Upuść tu wymiar',
    'agg_hint_values': 'Upuść tu miarę',
    'agg_hint_filters': 'Brak filtra · wszystkie wiersze',
    'agg_preview': '🔍 Podgląd (próbka)',
    'agg_cancel': 'Anuluj',
    'agg_run': '★ Agreguj i zapisz',
    'agg_dlg_add': 'Dodaj',
    'agg_dlg_cancel': 'Anuluj',
    'agg_dlg_check_title': 'Sprawdź dane',
    'agg_dlg_name_taken': 'Kolumna lub reguła o tej nazwie już istnieje. Wybierz inną nazwę.',
    'agg_dlg_group_title': 'Połącz kolumny (suma)',
    'agg_dlg_group_name': 'Nazwa nowej kolumny zbiorczej:',
    'agg_dlg_group_sources': 'Kolumny liczbowe do zsumowania (Ctrl, aby wybrać kilka):',
    'agg_dlg_group_default_name': 'kolumna_zbiorcza',
    'agg_dlg_group_incomplete': 'Podaj nazwę kolumny i wybierz co najmniej jedną kolumnę źródłową.',
    'agg_dlg_formula_title': 'Dodaj wskaźnik / kolumnę pochodną',
    'agg_dlg_formula_name': 'Nazwa nowej kolumny wyliczanej:',
    'agg_dlg_formula_numerator': 'Licznik:',
    'agg_dlg_formula_denominator': '÷ Mianownik:',
    'agg_dlg_formula_multiplier': '× Mnożnik:',
    'agg_dlg_formula_default_name': 'Marża (%)',
    'agg_dlg_formula_incomplete': 'Podaj nazwę, licznik i mianownik.',
    'agg_dlg_formula_note': 'Wskaźniki są liczone po zsumowaniu grup. Procent jest zapisywany jako współczynnik (0,27) i wyświetlany jako 27,00% przez format arkusza. Kolumny utworzone wcześniejszymi regułami mogą służyć jako licznik lub mianownik.',
    'agg_dlg_filter_title': 'Dodaj warunek filtra',
    'agg_dlg_filter_column': 'Kolumna:',
    'agg_dlg_filter_operator': 'Operator:',
    'agg_dlg_filter_value': 'Wartość:',
    'agg_dlg_filter_note': 'Wartości są porównywane jako zwykły tekst. Dla in / not in oddziel wartości przecinkami.',
    'agg_dlg_filter_incomplete': 'Wybierz kolumnę i operator.',
    'agg_dlg_filter_value_required': 'Podaj wartość do porównania.',
    'agg_msg_notice': 'Informacja',
    'agg_msg_select_file_first': 'Najpierw wybierz plik źródłowy.',
    'agg_msg_schema_progress': 'Analizowanie struktury kolumn pliku…',
    'agg_msg_schema_done': 'Wykryto kolumny ({count})',
    'agg_msg_schema_log': 'Przeanalizowano plik: {name} ({count} kolumn)',
    'agg_msg_schema_error_title': 'Błąd analizy pliku',
    'agg_msg_schema_error': 'Nie można odczytać struktury pliku:\n{error}',
    'agg_msg_cascade_title': 'Usuwanie powiązanych reguł',
    'agg_msg_cascade': 'Usunięcie „{name}” usunie także reguły, które go używają:\n{dependents}\n\nKontynuować?',
    'agg_msg_need_file_title': 'Wymagany plik',
    'agg_msg_need_file': 'Najpierw wybierz źródłowy plik CSV.',
    'agg_msg_config_title': 'Sprawdź ustawienia',
    'agg_msg_need_group_key': 'Wybierz co najmniej jedną grupę wierszy.',
    'agg_msg_need_measure': 'Przenieś co najmniej jedno pole do obszaru „2. Wartości”.',
    'agg_msg_preset_missing': 'Najpierw wybierz szablon.',
    'agg_msg_preset_load_error_title': 'Błąd szablonu',
    'agg_msg_preset_load_error': 'Nie można wczytać szablonu:\n{error}',
    'agg_msg_preset_mismatch_title': 'Niezgodność kolumn',
    'agg_msg_preset_mismatch': 'Szablon odwołuje się do kolumn, których nie ma w bieżącym pliku:\n{columns}\n\nZastosować mimo to?',
    'agg_msg_preset_applied': 'Zastosowano szablon „{name}”',
    'agg_msg_preset_desc': 'Szablon: {name}\nOpis: {description}',
    'agg_msg_preset_save_title': 'Zapisz szablon',
    'agg_msg_preset_save_prompt': 'Podaj nazwę szablonu:',
    'agg_msg_preset_memo_title': 'Notatka szablonu',
    'agg_msg_preset_memo_prompt': 'Podaj opis lub notatkę (opcjonalnie):',
    'agg_msg_preset_overwrite_title': 'Potwierdź nadpisanie',
    'agg_msg_preset_overwrite': 'Szablon o nazwie „{name}” już istnieje.\nNadpisać zapisane ustawienia?',
    'agg_msg_preset_saved_title': 'Zapisano',
    'agg_msg_preset_saved': 'Szablon „{name}” został zapisany.',
    'agg_msg_preset_export_title': 'Eksportuj szablon',
    'agg_msg_preset_export_done_title': 'Eksport zakończony',
    'agg_msg_preset_export_done': 'Szablon został wyeksportowany:\n{path}',
    'agg_msg_preset_export_error': 'Błąd eksportu',
    'agg_msg_preset_import_title': 'Importuj udostępniony plik szablonu',
    'agg_msg_preset_import_done_title': 'Import zakończony',
    'agg_msg_preset_import_done': 'Szablon „{name}” został zaimportowany.',
    'agg_msg_preset_import_error': 'Błąd importu',
    'agg_msg_preset_import_error_body': 'Nie można zaimportować pliku szablonu:\n{error}',
    'agg_msg_preset_delete_title': 'Usuń szablon',
    'agg_msg_preset_delete': 'Usunąć szablon „{name}”?',
    'agg_msg_busy_title': 'Zajęte',
    'agg_msg_busy': 'Trwa inne zadanie. Spróbuj ponownie po jego zakończeniu.',
    'agg_msg_cancel_requested': 'Zażądano anulowania… porządkowanie',
    'agg_msg_preview_running': 'Tworzenie szybkiego podglądu z pierwszych 2 000 wierszy…',
    'agg_msg_preview_done': 'Podgląd gotowy',
    'agg_msg_preview_error_title': 'Błąd podglądu',
    'agg_msg_preview_error': 'Nie można utworzyć podglądu:\n{error}',
    'agg_msg_prepare': 'Przygotowywanie agregacji…',
    'agg_msg_done_progress': 'Agregacja zakończona.',
    'agg_msg_saved_log': 'Zapisano: {name}',
    'agg_msg_rollup_on': 'zastosowane',
    'agg_msg_rollup_off': 'niezastosowane',
    'agg_msg_coerced': '\n\n⚠️ Uwaga: {count:,} nieprawidłowych lub brakujących wartości liczbowych zastąpiono zerem.',
    'agg_msg_result_body': 'Agregacja zakończona.\n\n• Plik: {name}\n• Folder: {folder}\n• Zapisane wiersze: {rows}\n• Grupowanie: {keys}\n• Sumowanie roczne: {rollup}\n• Kolumny wartości: {columns}{warning}\n\n=== Pierwsze 10 wierszy ===\n{preview}',
    'agg_msg_complete_title': 'Agregacja zakończona',
    'agg_msg_complete': 'Agregacja danych zakończyła się pomyślnie.\n\nZapisano w:\n{path}',
    'agg_msg_complete_coerced': '\n\n({count:,} nieprawidłowych lub brakujących wartości liczbowych zastąpiono zerem.)',
    'agg_msg_cancelled_progress': 'Anulowano',
    'agg_msg_cancelled_log': 'Agregacja została anulowana przez użytkownika.',
    'agg_msg_cancelled_text': 'Agregacja została anulowana.',
    'agg_msg_error_progress': 'Błąd',
    'agg_msg_error_log': 'Błąd agregacji',
    'agg_msg_error_title': 'Błąd agregacji',
    'agg_msg_error': 'Agregacja nie powiodła się:\n{error}',
})


class DataRefineryApp:
    @staticmethod
    def _resource_path(relative_path):
        """Find bundled assets both during development and in PyInstaller builds."""
        bundle_root = getattr(sys, '_MEIPASS', None)
        if bundle_root is not None:
            return os.path.join(bundle_root, 'assets', relative_path)
        return str(Path(__file__).resolve().parents[1] / 'assets' / relative_path)

    def __init__(self, root):
        self.root = root
        self.language = tk.StringVar(value="English")
        self._last_result = None
        self._promotion_data = None
        self._csv_processing = False
        self._promotion_processing = False
        self._jobs = BackgroundJobRunner(root.after)
        self._update_url = None
        self._update_state = "idle"
        self._update_version = None
        self._update_settings = load_settings()
        self.update_check_enabled = tk.BooleanVar(
            value=self._update_settings.get("update_check_enabled", True)
        )
        self.root.title(f"Data Refinery v{__version__}")
        # The aggregator tab is the tallest page: two field-list panes plus the
        # save card need roughly 600px before the shared result panel gets a say.
        self.root.geometry("900x900")
        self.root.minsize(780, 760)

        try:
            self.root.iconbitmap(self._resource_path("icons/icon.ico"))
        except tk.TclError:
            pass

        # Shared palette lives in ui_components so reusable widgets match the shell.
        page_bg = PALETTE["page_bg"]
        surface = PALETTE["surface"]
        surface_alt = PALETTE["surface_alt"]
        navy = PALETTE["navy"]
        text = PALETTE["text"]
        muted = PALETTE["muted"]
        border = PALETTE["border"]
        border_strong = PALETTE["border_strong"]
        accent = PALETTE["accent"]
        accent_active = PALETTE["accent_active"]
        accent_soft = PALETTE["accent_soft"]
        accent_tint = PALETTE["accent_tint"]

        self.root.configure(background=page_bg)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # clam draws bevels by default.  Setting lightcolor/darkcolor to the same
        # value as bordercolor flattens every element into a hairline outline,
        # which is the single biggest difference between a dated Tk app and a
        # current-looking one.
        def flat(**options):
            edge = options.pop("edge", border)
            options.setdefault("bordercolor", edge)
            options.setdefault("lightcolor", edge)
            options.setdefault("darkcolor", edge)
            return options

        style.configure(".", font=("Segoe UI", 10), background=page_bg, foreground=text)
        style.configure("App.TFrame", background=page_bg)
        style.configure("Dialog.TFrame", background=surface)
        style.configure("Card.TFrame", background=surface)
        style.configure("Rule.TFrame", background=border)  # 1px separator lines

        style.configure("Card.TLabelframe", background=surface, relief="solid", borderwidth=1, **flat())
        style.configure("Card.TLabelframe.Label", background=surface, foreground=navy, font=("Segoe UI Semibold", 10))
        style.configure("TLabel", background=surface, foreground=text)
        style.configure("Muted.TLabel", background=surface, foreground=muted, font=("Segoe UI", 9))
        style.configure("Help.TLabel", background=surface, foreground=muted, font=("Segoe UI", 8))
        style.configure("Footer.TLabel", background=page_bg, foreground=muted, font=("Segoe UI", 9))
        style.configure("Field.TLabel", background=surface, foreground=text, font=("Segoe UI Semibold", 9))
        style.configure("Caption.TLabel", background=surface, foreground=muted, font=("Segoe UI Semibold", 8))

        style.configure("TEntry", fieldbackground=surface, foreground=text, padding=(10, 7), insertcolor=text, **flat())
        style.map(
            "TEntry",
            bordercolor=[("focus", accent)],
            lightcolor=[("focus", accent)],
            darkcolor=[("focus", accent)],
            fieldbackground=[("readonly", surface_alt)],
            foreground=[("readonly", muted)],
        )
        style.configure("TCombobox", fieldbackground=surface, foreground=text, padding=(8, 6), arrowcolor=muted, **flat())
        # Denser variants for the setup card, whose rows are single-line fields.
        style.configure("Compact.TEntry", fieldbackground=surface, foreground=text, padding=(8, 3), insertcolor=text, **flat())
        style.map(
            "Compact.TEntry",
            bordercolor=[("focus", accent)],
            lightcolor=[("focus", accent)],
            darkcolor=[("focus", accent)],
            fieldbackground=[("readonly", surface_alt)],
            foreground=[("readonly", muted)],
        )
        style.configure("Compact.TCombobox", fieldbackground=surface, foreground=text, padding=(6, 3), arrowcolor=muted, **flat())
        style.map(
            "Compact.TCombobox",
            fieldbackground=[("readonly", surface)],
            bordercolor=[("focus", accent), ("hover", border_strong)],
            lightcolor=[("focus", accent)],
            darkcolor=[("focus", accent)],
            arrowcolor=[("hover", accent)],
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", surface)],
            bordercolor=[("focus", accent), ("hover", border_strong)],
            lightcolor=[("focus", accent)],
            darkcolor=[("focus", accent)],
            arrowcolor=[("hover", accent)],
        )
        style.configure("TCheckbutton", background=surface, foreground=text, font=("Segoe UI", 9), focuscolor=surface)
        style.map("TCheckbutton", background=[("active", surface)], foreground=[("active", navy)])

        style.configure(
            "Primary.TButton",
            background=accent, foreground="#FFFFFF", font=("Segoe UI Semibold", 10),
            padding=(20, 11), borderwidth=0, focuscolor=accent, **flat(edge=accent),
        )
        style.map(
            "Primary.TButton",
            background=[("active", accent_active), ("pressed", accent_active), ("disabled", "#BAC8D4")],
            **{k: [("active", accent_active), ("pressed", accent_active), ("disabled", "#BAC8D4")]
               for k in ("bordercolor", "lightcolor", "darkcolor")},
            foreground=[("disabled", "#EDF2F7")],
        )
        # Secondary and compact are outlined rather than filled, so a screen with
        # many buttons has exactly one filled call to action.
        style.configure(
            "Secondary.TButton",
            background=surface, foreground=text, font=("Segoe UI Semibold", 9),
            padding=(14, 9), relief="solid", borderwidth=1, focuscolor=surface, **flat(),
        )
        style.map(
            "Secondary.TButton",
            background=[("active", surface_alt), ("pressed", accent_soft), ("disabled", surface_alt)],
            foreground=[("active", accent), ("disabled", "#AFBECC")],
            **{k: [("active", accent), ("pressed", accent), ("disabled", border)]
               for k in ("bordercolor", "lightcolor", "darkcolor")},
        )
        style.configure(
            "Compact.TButton",
            background=surface, foreground=accent, font=("Segoe UI Semibold", 8),
            padding=(6, 6), relief="solid", borderwidth=1, focuscolor=surface, **flat(),
        )
        style.map(
            "Compact.TButton",
            background=[("active", accent_tint), ("pressed", accent_soft), ("disabled", surface)],
            foreground=[("disabled", "#AFBECC")],
            **{k: [("active", accent), ("pressed", accent), ("disabled", border)]
               for k in ("bordercolor", "lightcolor", "darkcolor")},
        )

        style.configure("FieldList.Treeview", background=surface, fieldbackground=surface, foreground=text, borderwidth=0, rowheight=24, font=("Segoe UI", 9))
        # A tinted selection instead of a solid accent block keeps long lists calm.
        style.map("FieldList.Treeview", background=[("selected", PALETTE["selection"])], foreground=[("selected", navy)])
        style.configure("Vertical.TScrollbar", background=border, troughcolor=surface, arrowcolor=muted, borderwidth=0, **flat(edge=surface))
        style.map("Vertical.TScrollbar", background=[("active", border_strong), ("pressed", muted)])

        style.configure("App.TNotebook", background=page_bg, borderwidth=0)
        # The native notebook lifts its selected tab, which makes otherwise
        # identical labels look like different heights.  The task switcher
        # below owns the visible tab headers, while the notebook keeps its
        # reliable page-selection behavior.
        style.layout("App.TNotebook.Tab", [])
        # Segmented control: the selected task reads as the card the content
        # below belongs to, the others recede into the page.
        style.configure("TaskTab.TButton", background=page_bg, foreground=muted, font=("Segoe UI", 10), padding=(18, 10), borderwidth=0, focuscolor=page_bg)
        style.map("TaskTab.TButton", background=[("active", "#E3EAF2"), ("pressed", "#DAE3ED")], foreground=[("active", text)])
        style.configure("TaskTab.Selected.TButton", background=surface, foreground=accent, font=("Segoe UI Semibold", 10), padding=(18, 10), borderwidth=0, focuscolor=surface)
        style.map("TaskTab.Selected.TButton", background=[("active", surface), ("pressed", surface)], foreground=[("active", accent)])

        style.configure("Status.TFrame", background=surface)
        style.configure("Status.TLabel", background=surface, foreground=muted, font=("Segoe UI", 9))
        style.configure("App.Horizontal.TProgressbar", troughcolor=PALETTE["accent_soft"], background=accent, thickness=5, borderwidth=0, **flat(edge=PALETTE["accent_soft"]))

        main = ttk.Frame(root, style="App.TFrame", padding=(16, 10, 16, 8))
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        # The page takes most of the slack; the result panel takes the rest so a
        # short tab does not leave a dead gap above the status bar.
        main.rowconfigure(1, weight=3)
        main.rowconfigure(3, weight=1)

        # 1-Line Compact Top Bar (Tabs on Left, Language/Update on Right)
        top_bar = ttk.Frame(main, style="App.TFrame")
        top_bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self.task_tabs = ttk.Frame(top_bar, style="App.TFrame")
        self.task_tabs.pack(side="left")

        self.csv_tab_button = ttk.Button(
            self.task_tabs,
            command=lambda: self._select_task_tab(self.csv_tab),
            style="TaskTab.Selected.TButton",
            width=18,
        )
        self.csv_tab_button.pack(side="left", padx=(0, 2))

        self.promotion_tab_button = ttk.Button(
            self.task_tabs,
            command=lambda: self._select_task_tab(self.promotion_tab),
            style="TaskTab.TButton",
            width=18,
        )
        self.promotion_tab_button.pack(side="left", padx=2)

        self.aggregator_tab_button = ttk.Button(
            self.task_tabs,
            command=lambda: self._select_task_tab(self.aggregator_tab),
            style="TaskTab.TButton",
            width=18,
        )
        self.aggregator_tab_button.pack(side="left", padx=2)

        # Utility controls (Right side)
        util_frame = ttk.Frame(top_bar, style="App.TFrame")
        util_frame.pack(side="right")

        self.header_subtitle = ttk.Label(util_frame, text="")  # keep for language binding compatibility

        # Language and updates are set once and forgotten, so they live behind one
        # settings button instead of taking permanent space beside the task tabs.
        self.update_details_button = ttk.Button(
            util_frame,
            command=self._show_update_menu,
            style="Secondary.TButton",
        )
        self.update_details_button.pack(side="left")

        self.update_menu = UpdateMenu(
            root,
            self.update_check_enabled,
            on_check=lambda: self._start_update_check(force=True),
            on_download=self._open_update_page,
            on_preference_changed=self._save_update_preference,
            language_variable=self.language,
            languages=tuple(_LANGUAGE_CODES),
            on_language_changed=self._apply_language,
        )

        self.job_runner = self._jobs

        self.notebook = ttk.Notebook(main, style="App.TNotebook")
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self.csv_tab = ttk.Frame(self.notebook, style="App.TFrame", padding=(0, 6, 0, 0))
        self.promotion_tab = ttk.Frame(self.notebook, style="App.TFrame", padding=(0, 6, 0, 0))
        self.aggregator_tab = AggregatorTabFrame(self.notebook, self, padding=(0, 6, 0, 0))
        self.notebook.add(self.csv_tab)
        self.notebook.add(self.promotion_tab)
        self.notebook.add(self.aggregator_tab)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_task_tab_change)

        self.file_section = ttk.LabelFrame(self.csv_tab, style="Card.TLabelframe", padding=(18, 14))
        self.file_section.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.csv_tab.columnconfigure(0, weight=1)
        self.csv_tab.rowconfigure(1, weight=1)
        self.file_section.columnconfigure(0, weight=1)

        # File Path
        self.filepath = tk.StringVar()
        self.lbl_file = ttk.Entry(self.file_section, textvariable=self.filepath, state="readonly")
        self.lbl_file.grid(row=0, column=0, sticky="ew")
        self.browse_button = ttk.Button(self.file_section, command=self.browse_file, style="Secondary.TButton")
        self.browse_button.grid(row=0, column=1, padx=(10, 0))
        self.file_info_label = ttk.Label(
            self.file_section,
            style="Muted.TLabel",
        )
        self.file_info_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self.settings = ttk.Frame(self.csv_tab, style="App.TFrame")
        self.settings.grid(row=1, column=0, sticky="nsew")
        self.settings.columnconfigure(0, weight=1)
        self.settings.columnconfigure(1, weight=1)
        self.settings.rowconfigure(0, weight=1)

        self.import_section = ttk.LabelFrame(self.settings, style="Card.TLabelframe", padding=(18, 14))
        self.import_section.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.import_section.columnconfigure(1, weight=1)

        self.output_section = ttk.LabelFrame(self.settings, style="Card.TLabelframe", padding=(18, 14))
        self.output_section.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.output_section.columnconfigure(1, weight=1)

        # Delimiter
        self.delimiter = tk.StringVar(value=",")
        self.delimiter_label = ttk.Label(self.import_section, style="Field.TLabel")
        self.delimiter_label.grid(row=0, column=0, sticky="w")
        self.ent_delimiter = ttk.Entry(self.import_section, textvariable=self.delimiter, width=8)
        self.ent_delimiter.grid(row=0, column=1, sticky="ew")
        self._delimiter_user_set = False
        self.ent_delimiter.bind("<KeyRelease>", self._on_delimiter_user_change)
        self.delimiter_help_label = ttk.Label(
            self.import_section,
            style="Help.TLabel",
            wraplength=260,
        )
        self.delimiter_help_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 12))

        # Number Format
        self.num_format = tk.StringVar(value=_UI_TEXT["ko"]["number_options"][0])
        self.number_format_label = ttk.Label(self.import_section, style="Field.TLabel")
        self.number_format_label.grid(row=2, column=0, sticky="w")
        self.combo_format = ttk.Combobox(
            self.import_section,
            textvariable=self.num_format,
            values=_UI_TEXT["ko"]["number_options"],
            state="readonly",
            width=22,
        )
        self.combo_format.grid(row=2, column=1, sticky="ew")
        self.number_format_help = tk.StringVar()
        self.number_format_help_label = ttk.Label(self.import_section, textvariable=self.number_format_help, style="Help.TLabel", wraplength=260)
        self.number_format_help_label.grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        self.combo_format.bind("<<ComboboxSelected>>", self._update_number_format_help)

        # Max Columns
        self.max_cols = tk.StringVar()
        self.columns_label = ttk.Label(self.output_section, style="Field.TLabel")
        self.columns_label.grid(row=0, column=0, sticky="w")
        self.ent_max_cols = ttk.Entry(self.output_section, textvariable=self.max_cols, width=8)
        self.ent_max_cols.grid(row=0, column=1, sticky="ew")
        self.columns_help_label = ttk.Label(
            self.output_section,
            style="Help.TLabel",
            wraplength=260,
        )
        self.columns_help_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 12))

        # Output Format
        self.out_format = tk.StringVar(value=_UI_TEXT["ko"]["output_options"][0])
        self.output_format_label = ttk.Label(self.output_section, style="Field.TLabel")
        self.output_format_label.grid(row=2, column=0, sticky="w")
        self.combo_out_format = ttk.Combobox(
            self.output_section,
            textvariable=self.out_format,
            values=_UI_TEXT["ko"]["output_options"],
            state="readonly",
            width=22,
        )
        self.combo_out_format.grid(row=2, column=1, sticky="ew")
        self.output_format_help = tk.StringVar()
        self.output_format_help_label = ttk.Label(self.output_section, textvariable=self.output_format_help, style="Help.TLabel", wraplength=260)
        self.output_format_help_label.grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        self.combo_out_format.bind("<<ComboboxSelected>>", self._update_output_hint)

        self.action_area = ttk.Frame(self.csv_tab, style="App.TFrame")
        self.action_area.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        self.action_area.columnconfigure(0, weight=1)
        self.output_hint = tk.StringVar()
        ttk.Label(self.action_area, textvariable=self.output_hint, style="Footer.TLabel").grid(row=0, column=0, sticky="w")

        # Process Button
        self.btn_process = ttk.Button(self.action_area, command=self.process_csv, style="Primary.TButton")
        self.btn_process.grid(row=0, column=1, sticky="e")
        self.filepath.trace_add("write", self._refresh_csv_action_state)
        self.max_cols.trace_add("write", self._refresh_csv_action_state)
        self._refresh_csv_action_state()

        # Promotion keeps its own tab while sharing the explanatory result panel below.
        self.promotion_tab.columnconfigure(0, weight=1)
        self.promotion_tab.rowconfigure(2, weight=1)
        self.promotion_file_section = ttk.LabelFrame(self.promotion_tab, style="Card.TLabelframe", padding=(18, 14))
        self.promotion_file_section.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.promotion_file_section.columnconfigure(0, weight=1)
        self.promotion_filepath = tk.StringVar()
        self.promotion_path_entry = ttk.Entry(
            self.promotion_file_section,
            textvariable=self.promotion_filepath,
            state="readonly",
        )
        self.promotion_path_entry.grid(row=0, column=0, sticky="ew")
        self.promotion_browse_button = ttk.Button(
            self.promotion_file_section,
            command=self.browse_promotion_template,
            style="Secondary.TButton",
        )
        self.promotion_browse_button.grid(row=0, column=1, padx=(10, 0))
        self.promotion_download_button = ttk.Button(
            self.promotion_file_section,
            command=self.download_promotion_template,
            style="Secondary.TButton",
        )
        self.promotion_download_button.grid(row=0, column=2, padx=(10, 0))
        self.promotion_info_label = ttk.Label(self.promotion_file_section, style="Muted.TLabel", wraplength=720)
        self.promotion_info_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

        self.promotion_output_section = ttk.LabelFrame(self.promotion_tab, style="Card.TLabelframe", padding=(18, 14))
        self.promotion_output_section.grid(row=1, column=0, sticky="ew")
        self.promotion_output_section.columnconfigure(1, weight=1)
        self.promotion_output_format = tk.StringVar(value="CSV")
        self.promotion_output_label = ttk.Label(self.promotion_output_section, style="Field.TLabel")
        self.promotion_output_label.grid(row=0, column=0, sticky="w")
        self.promotion_output_combo = ttk.Combobox(
            self.promotion_output_section,
            textvariable=self.promotion_output_format,
            state="readonly",
            width=30,
        )
        self.promotion_output_combo.grid(row=0, column=1, sticky="ew", padx=(14, 0))
        self.promotion_output_combo.bind("<<ComboboxSelected>>", self._update_promotion_output_hint)
        self.promotion_output_help = ttk.Label(self.promotion_output_section, style="Help.TLabel", wraplength=600)
        self.promotion_output_help.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self.promotion_action_area = ttk.Frame(self.promotion_tab, style="App.TFrame")
        self.promotion_action_area.grid(row=2, column=0, sticky="nsew", pady=(16, 0))
        self.promotion_action_area.columnconfigure(0, weight=1)
        self.promotion_action_area.rowconfigure(0, weight=1)
        self.promotion_output_hint = tk.StringVar()
        ttk.Label(self.promotion_action_area, textvariable=self.promotion_output_hint, style="Footer.TLabel").grid(
            row=1, column=0, sticky="w"
        )
        self.promotion_process_button = ttk.Button(
            self.promotion_action_area,
            command=self.process_promotion_template,
            style="Primary.TButton",
            state="disabled",
        )
        self.promotion_process_button.grid(row=1, column=1, sticky="e")

        # Progress bar (advances during processing)
        self.progress = ttk.Progressbar(main, mode="determinate", maximum=100, style="App.Horizontal.TProgressbar")
        self.progress.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.progress.grid_remove()  # shown only while a job is running

        self.result_section = ttk.LabelFrame(
            main,
            style="Card.TLabelframe",
            padding=(12, 8),
        )
        self.result_section.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        self.result_section.columnconfigure(0, weight=1)
        self.result_section.rowconfigure(0, weight=1)
        self.result_text = tk.Text(
            self.result_section,
            height=6,
            wrap="word",
            relief="flat",
            borderwidth=0,
            background="#FFFFFF",
            foreground=text,
            font=("Segoe UI", 9),
            padx=6,
            pady=4,
            state="disabled",
        )
        self.result_text.grid(row=0, column=0, sticky="nsew")

        # Status bar, separated from the page by a hairline rather than a dark slab
        self.log_text = tk.StringVar()
        status_wrap = ttk.Frame(root, style="Status.TFrame")
        status_wrap.grid(row=1, column=0, sticky="ew")
        status_wrap.columnconfigure(0, weight=1)
        ttk.Frame(status_wrap, style="Rule.TFrame", height=1).grid(row=0, column=0, sticky="ew")
        status_bar = ttk.Frame(status_wrap, style="Status.TFrame", padding=(24, 8))
        status_bar.grid(row=1, column=0, sticky="ew")
        ttk.Label(status_bar, textvariable=self.log_text, style="Status.TLabel").grid(row=0, column=0, sticky="w")

        self._apply_language()
        self._on_task_tab_change()
        self.root.after(350, self._start_update_check)

    def browse_file(self):
        filename = filedialog.askopenfilename(
            title=self._ui("dialog_title"),
            filetypes=(
                ("CSV/TXT/Excel files", "*.csv *.txt *.xlsx *.xlsm"),
                ("All files", "*.*"),
            )
        )
        if filename:
            self.filepath.set(filename)
            detected_delim = self._detect_delimiter(filename)
            self._apply_detected_delimiter(detected_delim)
            self.update_max_columns()
            self._update_output_hint()

    def download_promotion_template(self):
        destination = filedialog.asksaveasfilename(
            title=self._ui("promo_download"),
            defaultextension=".xlsx",
            initialfile="promotion_template.xlsx",
            filetypes=(("Excel workbook", "*.xlsx"),),
        )
        if not destination:
            return
        try:
            shutil.copyfile(
                self._resource_path("templates/promotion_template.xlsx"),
                destination,
            )
            self.log_text.set(self._ui("promo_template_saved").format(name=os.path.basename(destination)))
            self._set_result_text(self._ui("promo_template_saved").format(name=destination))
        except OSError as error:
            messagebox.showerror(self._ui("promo_file_section"), str(error))

    def browse_promotion_template(self):
        filename = filedialog.askopenfilename(
            title=self._ui("promo_select_title"),
            filetypes=(("Excel template", "*.xlsx"),),
        )
        if filename:
            self._promotion_data = None
            self.promotion_filepath.set(filename)
            self._refresh_promotion_action_state()
            self._load_promotion_template()

    def _load_promotion_template(self):
        path = self.promotion_filepath.get()
        if not path or not os.path.exists(path):
            self._promotion_data = None
            self._refresh_promotion_action_state()
            return None
        try:
            data, issues = load_template(path)
        except Exception as error:
            self._promotion_data = None
            self._refresh_promotion_action_state()
            self._set_result_text(str(error))
            return None
        if issues:
            self._promotion_data = None
            self._refresh_promotion_action_state()
            shown = [f"• {issue.display()}" for issue in issues[:6]]
            if len(issues) > len(shown):
                shown.append(self._ui("promo_issue_more").format(count=len(issues) - len(shown)))
            self.log_text.set(self._ui("promo_invalid").format(count=len(issues)))
            self._set_result_text("\n".join((
                self._ui("promo_invalid").format(count=len(issues)),
                "",
                *shown,
            )))
            return None
        self._promotion_data = data
        self._refresh_promotion_action_state()
        self._show_promotion_preview(data)
        return data

    def _show_promotion_preview(self, data):
        preview = preview_daily_rows(data, limit=20)
        lines = [
            self._ui("promo_valid").format(rules=len(data.support_rules), rows=f"{data.estimated_daily_rows:,}"),
            self._ui("promo_overlap").format(count=data.overlapping_rule_pairs),
            "",
            self._ui("promo_preview").format(count=len(preview)),
        ]
        lines.extend(
            "{applied_date} | {model_code} | {promotion_id} | {support_per_unit} {currency}".format(**row)
            for row in preview
        )
        self.log_text.set(self._ui("promo_valid").format(rules=len(data.support_rules), rows=f"{data.estimated_daily_rows:,}"))
        self._set_result_text("\n".join(lines))

    def _update_promotion_output_hint(self):
        extension = ".xlsx" if self._promotion_output_id(self.promotion_output_format.get()) == "Excel (.xlsx)" else ".csv"
        self.promotion_output_hint.set(f"promotion_daily_support_YYYYMMDD_HHMM{extension}")

    def process_promotion_template(self):
        if not self.promotion_filepath.get():
            messagebox.showerror(self._ui("promo_select_title"), self._ui("promo_select_message"))
            return
        data = self._load_promotion_template()
        if data is None:
            return
        daily_format = self._promotion_output_id(self.promotion_output_format.get())
        if daily_format == "Excel (.xlsx)" and data.estimated_daily_rows > EXCEL_MAX_DATA_ROWS:
            self._set_result_text(self._ui("promo_excel_limit").format(limit=EXCEL_MAX_DATA_ROWS))
            return
        source_path = self.promotion_filepath.get()

        def worker(report):
            report(15, "saving")
            return export_normalized(
                data,
                source_path,
                daily_format=daily_format,
                progress=report,
            )

        self._promotion_processing = True
        self._set_promotion_controls_enabled(False)
        self._refresh_csv_action_state()
        self._set_progress(15, self._ui("promo_saving"))
        started = self._jobs.start(
            "promotion-processing",
            worker,
            JobCallbacks(
                on_progress=self._on_promotion_progress,
                on_success=self._on_promotion_success,
                on_error=self._on_promotion_error,
                on_finished=self._finish_promotion_processing,
            ),
        )
        if not started:
            self._finish_promotion_processing()

    def _on_promotion_progress(self, percent, detail):
        message = self._ui("promo_saving") if detail == "saving" else None
        self._set_progress(percent, message)

    def _on_promotion_success(self, result):
        done = self._ui("promo_done").format(rows=f"{result.daily_rows:,}")
        self._set_progress(100, done)
        self._set_result_text("\n".join((
            done,
            "",
            self._ui("promo_summary_title"),
            self._ui("promo_master_file").format(name=result.master_path.name),
            self._ui("promo_rules_file").format(name=result.rules_path.name),
            self._ui("promo_daily_file").format(name=result.daily_path.name),
            self._ui("promo_overlap").format(count=result.overlapping_rule_pairs),
        )))
        self.log_text.set(done)

    def _on_promotion_error(self, error):
        messagebox.showerror(self._ui("promo_result_title"), str(error))
        self.log_text.set(self._ui("promo_result_title"))

    def _finish_promotion_processing(self):
        self._promotion_processing = False
        self._set_promotion_controls_enabled(True)
        self._refresh_csv_action_state()
        self._set_progress(0)

    def _set_promotion_controls_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        self.promotion_browse_button.configure(state=state)
        self.promotion_download_button.configure(state=state)
        self.promotion_output_combo.configure(state="readonly" if enabled else "disabled")
        if enabled:
            self._refresh_promotion_action_state()
        else:
            self.promotion_process_button.configure(state="disabled")

    def _language_code(self):
        language = getattr(self, 'language', None)
        selection = language.get() if language is not None else "한국어"
        return _LANGUAGE_CODES.get(selection, "ko")

    def _selected_task_id(self):
        """Return a stable feature id for the notebook's selected tab."""
        sel = self.notebook.select()
        if sel == str(self.promotion_tab):
            return "promotion"
        if sel == str(self.aggregator_tab):
            return "aggregator"
        return "csv"

    def _select_task_tab(self, tab):
        """Select a task page from the equal-height task switcher."""
        self.notebook.select(tab)
        self._on_task_tab_change()

    def _refresh_task_tab_buttons(self):
        tid = self._selected_task_id()
        self.csv_tab_button.configure(
            style="TaskTab.Selected.TButton" if tid == "csv" else "TaskTab.TButton"
        )
        self.promotion_tab_button.configure(
            style="TaskTab.Selected.TButton" if tid == "promotion" else "TaskTab.TButton"
        )
        self.aggregator_tab_button.configure(
            style="TaskTab.Selected.TButton" if tid == "aggregator" else "TaskTab.TButton"
        )

    @staticmethod
    def _has_positive_column_count(value):
        try:
            return int(str(value)) > 0
        except (TypeError, ValueError):
            return False

    def _is_aggregating(self) -> bool:
        return bool(getattr(getattr(self, "aggregator_tab", None), "_is_aggregating", False))

    def _refresh_csv_action_state(self, *_):
        """Only enable CSV processing once a usable source and table width are available."""
        if (
            getattr(self, "_csv_processing", False)
            or getattr(self, "_promotion_processing", False)
            or self._is_aggregating()
        ):
            self.btn_process.configure(state="disabled")
            return
        is_ready = bool(
            self.filepath.get()
            and os.path.exists(self.filepath.get())
            and self._has_positive_column_count(self.max_cols.get())
        )
        self.btn_process.configure(state="normal" if is_ready else "disabled")

    def _refresh_promotion_action_state(self):
        """Prevent an avoidable validation dialog until a valid template is loaded."""
        is_ready = bool(
            not self._csv_processing
            and not self._promotion_processing
            and not self._is_aggregating()
            and self._promotion_data is not None
            and self.promotion_filepath.get()
            and os.path.exists(self.promotion_filepath.get())
        )
        self.promotion_process_button.configure(state="normal" if is_ready else "disabled")

    @staticmethod
    def _promotion_output_id(selection):
        return "Excel (.xlsx)" if "excel" in str(selection).casefold() else "CSV"

    def _on_task_tab_change(self, event=None):
        """Refresh the shared explanation panel for the selected feature tab."""
        self._refresh_task_tab_buttons()
        if self._selected_task_id() == "promotion":
            self.result_section.configure(text=self._ui("promo_result_title"))
            self._update_promotion_output_hint()
            if self._promotion_data is None:
                self._set_result_text(self._ui("promo_initial_result"))
                self.log_text.set(self._ui("promo_initial_result"))
            else:
                self._show_promotion_preview(self._promotion_data)
        elif self._selected_task_id() == "aggregator":
            self.result_section.configure(text=self._ui("task_options")[2])
            self._set_result_text(self._ui("agg_initial_result"))
            self.log_text.set(self._ui("task_options")[2])
        else:
            self.result_section.configure(text=self._ui("result_title"))
            if self._last_result is None:
                self._set_result_text(self._ui("initial_result"))
                self.log_text.set(self._ui("ready"))
            else:
                self._set_result_text(self._format_result_summary(self._last_result))

    def _save_update_preference(self):
        self._update_settings["update_check_enabled"] = bool(self.update_check_enabled.get())
        save_settings(self._update_settings)
        if not self.update_check_enabled.get():
            self._update_state = "off"
            self._refresh_update_status()
        else:
            # Re-enable the normal background check so the visible status is
            # never left saying that checks are disabled.
            self._start_update_check()

    def _start_update_check(self, force=False):
        if not self.update_check_enabled.get() and not force:
            self._update_state = "off"
            self._refresh_update_status()
            return
        if self._jobs.is_running("update-check"):
            return
        self._update_state = "checking"
        self._update_version = None
        self._refresh_update_status()
        self.update_menu.set_download_enabled(False)
        self._update_url = None
        if force:
            self._update_settings["last_update_check"] = ""

        def worker(_report):
            release = check_for_update(__version__, self._update_settings)
            save_settings(self._update_settings)
            return release

        self._jobs.start(
            "update-check",
            worker,
            JobCallbacks(
                on_progress=lambda _percent, _detail: None,
                on_success=self._finish_update_check,
                on_error=lambda _error: self._finish_update_check(None),
                on_finished=lambda: None,
            ),
        )

    def _finish_update_check(self, release):
        if release is None:
            self._update_state = "current"
            self._update_version = None
            self._refresh_update_status()
            return
        self._update_url = release.url
        self._update_state = "available"
        self._update_version = release.version
        self._refresh_update_status()
        self.update_menu.set_download_enabled(True)

    def _refresh_update_status(self):
        """Render the stored update state in the currently selected language."""
        key = {
            "idle": "checking_updates",
            "checking": "checking_updates",
            "current": "update_current",
            "off": "update_off",
            "available": "update_available",
        }.get(self._update_state)
        if not key:
            return
        message = self._ui(key)
        if self._update_state == "available":
            message = message.format(version=self._update_version)
        self.update_menu.set_status(message)
        button_text = self._ui("settings")
        if self._update_state == "available":
            button_text = f"{button_text} •"  # a dot is enough to say "something is waiting"
        self.update_details_button.configure(text=button_text)

    def _show_update_menu(self):
        self.update_menu.show_below(self.update_details_button)

    def _open_update_page(self):
        if self._update_url:
            webbrowser.open(self._update_url)

    def _ui(self, key):
        text = _UI_TEXT[self._language_code()]
        if key in text:
            return text[key]
        return _UI_TEXT["en"].get(key, key)

    def _apply_language(self, event=None):
        """Refresh all user-facing labels while keeping the chosen data settings."""
        number_mode = self._number_mode(self.num_format.get())
        output_fmt = self._output_format(self.out_format.get())
        promotion_output_id = self._promotion_output_id(self.promotion_output_format.get())
        text = _UI_TEXT[self._language_code()]

        self.header_subtitle.configure(text=text['header_subtitle'])
        self.update_menu.set_language_label(text['language_label'])
        self.file_section.configure(text=text['section_file'])
        self.browse_button.configure(text=text['browse'])
        self.file_info_label.configure(text=text['file_info'])
        self.import_section.configure(text=text['section_import'])
        self.output_section.configure(text=text['section_output'])
        self.delimiter_label.configure(text=text['delimiter_label'])
        self.delimiter_help_label.configure(text=text['delimiter_help'])
        self.number_format_label.configure(text=text['number_label'])
        self.columns_label.configure(text=text['columns_label'])
        self.columns_help_label.configure(text=text['columns_help'])
        self.output_format_label.configure(text=text['output_label'])
        self.btn_process.configure(text=text['process'])
        self.notebook.tab(self.csv_tab, text=text['task_options'][0])
        self.notebook.tab(self.promotion_tab, text=text['task_options'][1])
        self.notebook.tab(self.aggregator_tab, text=text['task_options'][2])
        self.csv_tab_button.configure(text=text['task_options'][0])
        self.promotion_tab_button.configure(text=text['task_options'][1])
        self.aggregator_tab_button.configure(text=text['task_options'][2])
        self.update_menu.set_texts(
            status="",
            check=text['check_updates'],
            download=text['download_update'],
            enabled=text['update_enabled'],
        )

        self.promotion_file_section.configure(text=text['promo_file_section'])
        self.promotion_info_label.configure(text=text['promo_file_info'])
        self.promotion_download_button.configure(text=text['promo_download'])
        self.promotion_browse_button.configure(text=text['promo_browse'])
        self.promotion_output_section.configure(text=text['promo_output_section'])
        self.promotion_output_label.configure(text=text['promo_output_label'])
        self.promotion_output_combo.configure(values=text['promo_output_options'])
        self.promotion_output_format.set(text['promo_output_options'][1 if promotion_output_id == 'Excel (.xlsx)' else 0])
        self.promotion_output_help.configure(text=text['promo_output_help'])
        self.promotion_process_button.configure(text=text['promo_process'])

        self.combo_format.configure(values=text['number_options'])
        self.num_format.set(text['number_options'][1 if number_mode == 'Polish' else 0])
        self.combo_out_format.configure(values=text['output_options'])
        self.out_format.set(text['output_options'][1 if output_fmt == 'Excel (.xlsx)' else 0])

        self.aggregator_tab.apply_language()

        self._update_number_format_help()
        self._update_output_hint()
        self._refresh_update_status()
        self._on_task_tab_change()

    @staticmethod
    def _number_mode(selection):
        """Map friendly combobox text to the parser's stable internal mode."""
        value = str(selection).casefold()
        return 'Polish' if ('polish' in value or 'polski' in value or '폴란드' in value) else 'English'

    @staticmethod
    def _output_format(selection):
        """Map friendly combobox text to the stable output format identifier."""
        value = str(selection).casefold()
        return 'Excel (.xlsx)' if ('excel' in value or 'skoroszyt' in value or '통합' in value) else 'CSV'

    def _update_number_format_help(self, event=None):
        key = 'number_help_polish' if self._number_mode(self.num_format.get()) == 'Polish' else 'number_help_english'
        self.number_format_help.set(self._ui(key))

    @staticmethod
    def _output_extension(output_fmt):
        return '.xlsx' if output_fmt == 'Excel (.xlsx)' else '.csv'

    def _update_output_hint(self, event=None):
        output_fmt = self._output_format(self.out_format.get())
        extension = self._output_extension(output_fmt)
        expected_name = f"processed_output_YYYYMMDD_HHMM{extension}"
        self.output_format_help.set(self._ui('output_help_csv' if output_fmt == 'CSV' else 'output_help_excel'))
        if self.filepath.get():
            self.output_hint.set(self._ui('output_hint_with_file').format(name=expected_name))
        else:
            self.output_hint.set(self._ui('output_hint').format(name=expected_name))

    def _format_result_summary(self, result):
        """Render the same processing result in the language currently selected."""
        text = _UI_TEXT[self._language_code()]
        return '\n'.join((
            text['summary_saved'].format(name=os.path.basename(result['out_path'])),
            text['summary_location'].format(path=os.path.dirname(result['out_path'])),
            '',
            text['summary_title'],
            text['summary_rows'].format(rows=result['rows'], columns=result['columns']),
            text['summary_garbage'].format(count=result['garbage_skipped']),
            text['summary_values'].format(numbers=result['numbers'], dates=result['dates']),
            text['summary_flattened'].format(count=result['flattened']),
            text['summary_repaired'].format(count=result['repaired']),
            text['summary_large'].format(count=result['large_numbers_as_text']),
            text['summary_encoding'].format(encoding=result['encoding']),
        ))

    def _set_result_text(self, message):
        """Show the processing explanation inside the app instead of a success popup."""
        result_widget = getattr(self, 'result_text', None)
        if result_widget is None:
            return
        result_widget.configure(state='normal')
        result_widget.delete('1.0', tk.END)
        result_widget.insert('1.0', message)
        result_widget.configure(state='disabled')

    @staticmethod
    def _is_excel(file_path):
        return is_excel_file(file_path)

    def _on_delimiter_user_change(self, event=None):
        """Remember an explicit delimiter choice so file selection cannot overwrite it."""
        self._delimiter_user_set = True
        self.update_max_columns(event)

    def _apply_detected_delimiter(self, detected_delim):
        """Apply an inferred delimiter only while the user has not chosen one."""
        if not detected_delim or getattr(self, '_delimiter_user_set', False):
            return False
        self.delimiter.set('\\t' if detected_delim == '\t' else detected_delim)
        return True

    def _detect_delimiter(self, file_path, sample_rows=50):
        return detect_csv_delimiter(file_path, sample_rows)

    @staticmethod
    def _normalize_delim(delim):
        return normalize_delimiter(delim)

    def read_file_lines(self, file_path, delim, max_lines=None):
        return read_file_rows(file_path, delim, max_lines)

    def update_max_columns(self, event=None):
        file_path = self.filepath.get()
        delim = self.delimiter.get()
        if not file_path or not os.path.exists(file_path):
            return
        if not delim:
            return
        
        try:
            rows, enc = self.read_file_lines(file_path, delim, max_lines=10)
            max_cols = max((len(r) for r in rows), default=0)
            
            self.max_cols.set(str(max_cols))
            self.log_text.set(self._ui('detected_columns').format(columns=max_cols, encoding=enc))
        except Exception:
            self.log_text.set(self._ui('detect_columns_error'))

    def _set_progress(self, pct, msg=None):
        """Move the progress bar and (optionally) the status text, then repaint."""
        try:
            value = max(0, min(100, pct))
            self.progress['value'] = value
            # An empty trough sitting between the page and the result panel reads
            # as a stray coloured band, so the bar only exists while work does.
            if value <= 0 or value >= 100:
                self.progress.grid_remove()
            else:
                self.progress.grid()
            if msg is not None:
                self.log_text.set(msg)
            self.root.update_idletasks()
        except Exception:
            pass

    def set_progress(self, pct, msg=None):
        self._set_progress(pct, msg)

    def set_status_log(self, text):
        self.log_text.set(text)

    def set_result_text(self, text):
        self._set_result_text(text)

    def _set_csv_controls_enabled(self, enabled):
        """Keep inputs stable while the worker owns the selected file."""
        state = "normal" if enabled else "disabled"
        self.browse_button.configure(state=state)
        self.ent_delimiter.configure(state=state)
        self.ent_max_cols.configure(state=state)
        self.combo_format.configure(state="readonly" if enabled else "disabled")
        self.combo_out_format.configure(state="readonly" if enabled else "disabled")
        if enabled:
            self._refresh_csv_action_state()
        else:
            self.btn_process.configure(state="disabled")

    def _csv_progress_message(self, event):
        if event == "reading":
            return self._ui("reading")
        if event.startswith("scanning:"):
            return self._ui("scanning").format(rows=f"{int(event.split(':', 1)[1]):,}")
        if event.startswith("converting:"):
            _, current, total = event.split(":")
            return self._ui("converting").format(current=current, total=total)
        if event == "saving":
            return self._ui("saving")
        return None

    def _on_csv_progress(self, percent, detail):
        self._set_progress(percent, self._csv_progress_message(detail))

    def _on_csv_success(self, result):
        self._set_progress(100, self._ui("done"))
        self._last_result = {
            "out_path": result.out_path,
            "rows": f"{result.rows:,}",
            "columns": f"{result.columns:,}",
            "garbage_skipped": f"{result.garbage_skipped:,}",
            "numbers": f"{result.numbers:,}",
            "dates": f"{result.dates:,}",
            "flattened": f"{result.flattened:,}",
            "repaired": f"{result.repaired:,}",
            "large_numbers_as_text": f"{result.large_numbers_as_text:,}",
            "encoding": result.encoding,
        }
        self.log_text.set(
            self._ui("status_done").format(
                rows=f"{result.rows:,}", name=os.path.basename(result.out_path)
            )
        )
        self._set_result_text(self._format_result_summary(self._last_result))

    def _on_csv_error(self, error):
        if isinstance(error, CsvNoTableError):
            messagebox.showwarning(self._ui("no_table_title"), self._ui("no_table_message"))
            self.log_text.set(self._ui("no_table_title"))
        elif isinstance(error, CsvNoDataError):
            messagebox.showwarning(self._ui("no_data_title"), self._ui("no_data_message"))
            self.log_text.set(self._ui("no_data_title"))
        else:
            messagebox.showerror(
                self._ui("error_title"),
                self._ui("error_message").format(error=str(error)),
            )
            self.log_text.set(self._ui("error_title"))

    def _finish_csv_processing(self):
        self._csv_processing = False
        self._set_csv_controls_enabled(True)
        self._refresh_promotion_action_state()
        self._set_progress(0)

    def process_csv(self):
        file_path = self.filepath.get()
        delimiter = self._normalize_delim(self.delimiter.get())
        number_mode = self._number_mode(self.num_format.get())
        output_format = self._output_format(self.out_format.get())

        if not file_path or not os.path.exists(file_path):
            messagebox.showerror(self._ui("select_file_title"), self._ui("select_file_message"))
            return
        if not self._is_excel(file_path) and (not delimiter or len(delimiter) != 1):
            messagebox.showerror(self._ui("delimiter_title"), self._ui("delimiter_message"))
            return
        try:
            max_columns = int(self.max_cols.get())
        except ValueError:
            messagebox.showerror(self._ui("columns_title"), self._ui("columns_number"))
            return
        if max_columns <= 0:
            messagebox.showerror(self._ui("columns_title"), self._ui("columns_positive"))
            return

        options = CsvProcessingOptions(
            file_path=file_path,
            delimiter=delimiter,
            number_mode=number_mode,
            output_format=output_format,
            max_columns=max_columns,
        )
        self._csv_processing = True
        self._set_csv_controls_enabled(False)
        self._refresh_promotion_action_state()
        self._set_progress(0, self._ui("reading"))
        started = self._jobs.start(
            "csv-processing",
            lambda report: process_csv_file(options, report),
            JobCallbacks(
                on_progress=self._on_csv_progress,
                on_success=self._on_csv_success,
                on_error=self._on_csv_error,
                on_finished=self._finish_csv_processing,
            ),
        )
        if not started:
            self._finish_csv_processing()

if __name__ == "__main__":
    root = tk.Tk()
    app = DataRefineryApp(root)
    root.mainloop()
