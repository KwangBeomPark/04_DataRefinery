import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import csv
import math
import os
import re
import shutil
import sys
import threading
import webbrowser
from datetime import datetime
import decimal
from collections import Counter

from promotion_normalizer import (
    EXCEL_MAX_DATA_ROWS,
    export_normalized,
    load_template,
    preview_daily_rows,
)
from update_checker import check_for_update, load_settings, save_settings

__version__ = "1.6.0"

_PANDAS = None


def _get_pandas():
    """Import pandas only when a selected file actually needs processing."""
    global _PANDAS
    if _PANDAS is None:
        import pandas as pandas_module
        _PANDAS = pandas_module
    return _PANDAS

class ParsedNumber:
    __slots__ = ['value', 'orig_decimals', 'orig_text']

    def __init__(self, value: decimal.Decimal, orig_decimals: int, orig_text: str):
        self.value = value
        self.orig_decimals = orig_decimals
        self.orig_text = orig_text


# Pre-compiled patterns (compiling once matters a lot on large files).
# _DATE_HINT_RE cheaply rejects non-date cells so we skip the expensive
# strptime attempts on the vast majority of values.
_DATE_HINT_RE = re.compile(r'\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}')
# Every character Excel/Windows can smuggle into a cell as a line break:
# CR/LF plus vertical tab (Alt+Enter survives copy-paste as \x0b), form feed,
# NEL, and the Unicode line/paragraph separators.
_LINEBREAK_RE = re.compile('[\r\n\x0b\x0c\x85\u2028\u2029]+')
_EN_NUM_RE = re.compile(r'-?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?')
_PL_NUM_RE = re.compile(r'-?(?:\d+|\d{1,3}(?: \d{3})+|\d{1,3}(?:\.\d{3})+)(?:,\d+)?')

_LANGUAGE_CODES = {
    "English": "en",
    "한국어": "ko",
    "Polski": "pl",
}

_UI_TEXT = {
    "ko": {
        "header_subtitle": "파일을 복구하고 분석 가능한 구조로 데이터를 정리합니다.",
        "language_label": "언어",
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
    "task_options": ("CSV repair", "Promotion template"),
    "update_enabled": "Check for updates automatically",
    "check_updates": "Check now",
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
    "task_options": ("CSV 구조 복구", "프로모션 템플릿"),
    "update_enabled": "새 버전 자동 확인",
    "check_updates": "지금 확인",
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
    "task_options": ("Naprawa CSV", "Szablon promocji"),
    "update_enabled": "Sprawdzaj aktualizacje automatycznie",
    "check_updates": "Sprawdź teraz",
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


class DataRefineryApp:
    @staticmethod
    def _resource_path(filename):
        """Find bundled assets both during development and in PyInstaller builds."""
        base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base_dir, filename)

    def __init__(self, root):
        self.root = root
        self.language = tk.StringVar(value="English")
        self._last_result = None
        self._promotion_data = None
        self._update_url = None
        self._update_state = "idle"
        self._update_version = None
        self._update_settings = load_settings()
        self.update_check_enabled = tk.BooleanVar(
            value=self._update_settings.get("update_check_enabled", True)
        )
        self.root.title(f"Data Refinery v{__version__}")
        self.root.geometry("880x840")
        self.root.minsize(720, 700)

        try:
            self.root.iconbitmap(self._resource_path("icon.ico"))
        except Exception:
            pass

        self._header_icon_image = None
        try:
            self._header_icon_image = tk.PhotoImage(file=self._resource_path("header_icon.png"))
        except tk.TclError:
            pass

        # A calm, high-contrast palette that remains readable with the clam theme.
        page_bg = "#F4F7FB"
        surface = "#FFFFFF"
        navy = "#102A43"
        text = "#243B53"
        muted = "#627D98"
        border = "#D9E2EC"
        accent = "#147D8A"
        accent_active = "#0F6974"

        self.root.configure(background=page_bg)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", font=("Segoe UI", 10), background=page_bg, foreground=text)
        style.configure("App.TFrame", background=page_bg)
        style.configure("Header.TFrame", background=navy)
        style.configure("Header.Icon.TLabel", background=navy)
        style.configure("Header.Title.TLabel", background=navy, foreground="#FFFFFF", font=("Segoe UI Semibold", 20))
        style.configure("Header.Subtitle.TLabel", background=navy, foreground="#C9D6E2", font=("Segoe UI", 10))
        style.configure("Header.TCheckbutton", background=navy, foreground="#C9D6E2", font=("Segoe UI", 8))
        style.map("Header.TCheckbutton", background=[("active", navy)], foreground=[("active", "#FFFFFF")])
        style.configure("Card.TLabelframe", background=surface, bordercolor=border, relief="solid", borderwidth=1)
        style.configure("Card.TLabelframe.Label", background=surface, foreground=navy, font=("Segoe UI Semibold", 10))
        style.configure("TLabel", background=surface, foreground=text)
        style.configure("Muted.TLabel", background=surface, foreground=muted, font=("Segoe UI", 9))
        style.configure("Help.TLabel", background=surface, foreground=muted, font=("Segoe UI", 8))
        style.configure("Footer.TLabel", background=page_bg, foreground=muted, font=("Segoe UI", 9))
        style.configure("Field.TLabel", background=surface, foreground=text, font=("Segoe UI Semibold", 9))
        style.configure("TEntry", fieldbackground="#FFFFFF", foreground=text, padding=(8, 6), bordercolor=border)
        style.map("TEntry", bordercolor=[("focus", accent)], fieldbackground=[("readonly", "#F8FAFC")])
        style.configure("TCombobox", fieldbackground="#FFFFFF", foreground=text, padding=(6, 5), bordercolor=border)
        style.map("TCombobox", fieldbackground=[("readonly", "#FFFFFF")], bordercolor=[("focus", accent)])
        style.configure("Primary.TButton", background=accent, foreground="#FFFFFF", font=("Segoe UI Semibold", 10), padding=(18, 10), borderwidth=0)
        style.map("Primary.TButton", background=[("active", accent_active), ("pressed", accent_active), ("disabled", "#9FB3C8")], foreground=[("disabled", "#F0F4F8")])
        style.configure("Secondary.TButton", background="#E8F1F5", foreground=accent, font=("Segoe UI Semibold", 9), padding=(12, 8), borderwidth=0)
        style.map("Secondary.TButton", background=[("active", "#D4E8EE"), ("pressed", "#C4DDE5")])
        style.configure("App.TNotebook", background=page_bg, borderwidth=0)
        # The native notebook lifts its selected tab, which makes otherwise
        # identical labels look like different heights.  The task switcher
        # below owns the visible tab headers, while the notebook keeps its
        # reliable page-selection behavior.
        style.layout("App.TNotebook.Tab", [])
        style.configure("TaskTab.TButton", background="#D9E2EC", foreground=text, font=("Segoe UI Semibold", 10), padding=(14, 9), borderwidth=1)
        style.map("TaskTab.TButton", background=[("active", "#E8F1F5"), ("pressed", "#C4DDE5")])
        style.configure("TaskTab.Selected.TButton", background=surface, foreground=navy, font=("Segoe UI Semibold", 10), padding=(14, 9), borderwidth=1)
        style.map("TaskTab.Selected.TButton", background=[("active", surface), ("pressed", surface)])
        style.configure("Status.TFrame", background=navy)
        style.configure("Status.TLabel", background=navy, foreground="#D9E2EC", font=("Segoe UI", 9))
        style.configure("App.Horizontal.TProgressbar", troughcolor=border, background=accent, bordercolor=border, lightcolor=accent, darkcolor=accent)

        main = ttk.Frame(root, style="App.TFrame", padding=(24, 20, 24, 18))
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)

        header = ttk.Frame(main, style="Header.TFrame", padding=(22, 18))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        title_column = 0
        if self._header_icon_image is not None:
            ttk.Label(header, image=self._header_icon_image, style="Header.Icon.TLabel").grid(
                row=0, column=0, rowspan=3, sticky="w", padx=(0, 14)
            )
            title_column = 1
        self.header_title = ttk.Label(header, text="Data Refinery", style="Header.Title.TLabel")
        self.header_title.grid(row=0, column=title_column, sticky="w")
        self.header_subtitle = ttk.Label(
            header,
            style="Header.Subtitle.TLabel",
        )
        self.header_subtitle.grid(row=1, column=title_column, sticky="w", pady=(3, 0))
        self.language_label = ttk.Label(header, style="Header.Subtitle.TLabel")
        self.language_label.grid(row=0, column=2, sticky="e", padx=(16, 8))
        self.language_combo = ttk.Combobox(
            header,
            textvariable=self.language,
            values=tuple(_LANGUAGE_CODES),
            state="readonly",
            width=11,
        )
        self.language_combo.grid(row=0, column=3, rowspan=2, sticky="e")
        self.language_combo.bind("<<ComboboxSelected>>", self._apply_language)

        self.update_frame = ttk.Frame(header, style="Header.TFrame")
        self.update_frame.grid(row=2, column=title_column, columnspan=3, sticky="ew", pady=(10, 0))
        self.update_frame.columnconfigure(0, weight=1)
        self.update_status = tk.StringVar()
        ttk.Label(self.update_frame, textvariable=self.update_status, style="Header.Subtitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.update_check_button = ttk.Button(
            self.update_frame,
            command=lambda: self._start_update_check(force=True),
            style="Secondary.TButton",
        )
        self.update_check_button.grid(row=0, column=1, padx=(8, 0))
        self.download_update_button = ttk.Button(
            self.update_frame,
            command=self._open_update_page,
            style="Secondary.TButton",
            state="disabled",
        )
        self.download_update_button.grid(row=0, column=2, padx=(8, 0))
        self.update_enabled_check = ttk.Checkbutton(
            self.update_frame,
            variable=self.update_check_enabled,
            command=self._save_update_preference,
            style="Header.TCheckbutton",
        )
        self.update_enabled_check.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.task_tabs = ttk.Frame(main, style="App.TFrame")
        self.task_tabs.grid(row=1, column=0, sticky="w", pady=(16, 0))
        self.task_tabs.columnconfigure(0, weight=1)
        self.task_tabs.columnconfigure(1, weight=1)

        self.notebook = ttk.Notebook(main, style="App.TNotebook")
        self.notebook.grid(row=2, column=0, sticky="nsew")
        self.csv_tab = ttk.Frame(self.notebook, style="App.TFrame", padding=(0, 12, 0, 0))
        self.promotion_tab = ttk.Frame(self.notebook, style="App.TFrame", padding=(0, 12, 0, 0))
        self.notebook.add(self.csv_tab)
        self.notebook.add(self.promotion_tab)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_task_tab_change)
        self.csv_tab_button = ttk.Button(
            self.task_tabs,
            command=lambda: self._select_task_tab(self.csv_tab),
            style="TaskTab.Selected.TButton",
            width=22,
        )
        self.csv_tab_button.grid(row=0, column=0, sticky="ew")
        self.promotion_tab_button = ttk.Button(
            self.task_tabs,
            command=lambda: self._select_task_tab(self.promotion_tab),
            style="TaskTab.TButton",
            width=22,
        )
        self.promotion_tab_button.grid(row=0, column=1, sticky="ew")

        self.file_section = ttk.LabelFrame(self.csv_tab, style="Card.TLabelframe", padding=(18, 14))
        self.file_section.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.csv_tab.columnconfigure(0, weight=1)
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
        self.progress.grid(row=3, column=0, sticky="ew", pady=(14, 0))

        self.result_section = ttk.LabelFrame(
            main,
            style="Card.TLabelframe",
            padding=(12, 10),
        )
        self.result_section.grid(row=4, column=0, sticky="nsew", pady=(14, 0))
        self.result_section.columnconfigure(0, weight=1)
        self.result_section.rowconfigure(0, weight=1)
        self.result_text = tk.Text(
            self.result_section,
            height=8,
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

        # Status bar
        self.log_text = tk.StringVar()
        status_bar = ttk.Frame(root, style="Status.TFrame", padding=(24, 9))
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
            shutil.copyfile(self._resource_path("promotion_template.xlsx"), destination)
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
        try:
            self._set_progress(15, self._ui("promo_saving"))
            result = export_normalized(data, self.promotion_filepath.get(), daily_format=daily_format)
            self._set_progress(100, self._ui("promo_done").format(rows=f"{result.daily_rows:,}"))
            self._set_result_text("\n".join((
                self._ui("promo_done").format(rows=f"{result.daily_rows:,}"),
                "",
                self._ui("promo_summary_title"),
                self._ui("promo_master_file").format(name=result.master_path.name),
                self._ui("promo_rules_file").format(name=result.rules_path.name),
                self._ui("promo_daily_file").format(name=result.daily_path.name),
                self._ui("promo_overlap").format(count=result.overlapping_rule_pairs),
            )))
            self.log_text.set(self._ui("promo_done").format(rows=f"{result.daily_rows:,}"))
        except Exception as error:
            messagebox.showerror(self._ui("promo_result_title"), str(error))
        finally:
            self._set_progress(0)

    def _language_code(self):
        language = getattr(self, 'language', None)
        selection = language.get() if language is not None else "한국어"
        return _LANGUAGE_CODES.get(selection, "ko")

    def _selected_task_id(self):
        """Return a stable feature id for the notebook's selected tab."""
        return "promotion" if self.notebook.select() == str(self.promotion_tab) else "csv"

    def _select_task_tab(self, tab):
        """Select a task page from the equal-height task switcher."""
        self.notebook.select(tab)
        self._on_task_tab_change()

    def _refresh_task_tab_buttons(self):
        promotion_selected = self._selected_task_id() == "promotion"
        self.csv_tab_button.configure(
            style="TaskTab.TButton" if promotion_selected else "TaskTab.Selected.TButton"
        )
        self.promotion_tab_button.configure(
            style="TaskTab.Selected.TButton" if promotion_selected else "TaskTab.TButton"
        )

    @staticmethod
    def _has_positive_column_count(value):
        try:
            return int(str(value)) > 0
        except (TypeError, ValueError):
            return False

    def _refresh_csv_action_state(self, *_):
        """Only enable CSV processing once a usable source and table width are available."""
        is_ready = bool(
            self.filepath.get()
            and os.path.exists(self.filepath.get())
            and self._has_positive_column_count(self.max_cols.get())
        )
        self.btn_process.configure(state="normal" if is_ready else "disabled")

    def _refresh_promotion_action_state(self):
        """Prevent an avoidable validation dialog until a valid template is loaded."""
        is_ready = bool(
            self._promotion_data is not None
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
        self._update_state = "checking"
        self._update_version = None
        self._refresh_update_status()
        self.download_update_button.configure(state="disabled")
        self._update_url = None
        if force:
            self._update_settings["last_update_check"] = ""

        def worker():
            release = check_for_update(__version__, self._update_settings)
            save_settings(self._update_settings)
            try:
                self.root.after(0, lambda: self._finish_update_check(release))
            except tk.TclError:
                pass

        threading.Thread(target=worker, name="update-check", daemon=True).start()

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
        self.download_update_button.configure(state="normal")

    def _refresh_update_status(self):
        """Render the stored update state in the currently selected language."""
        key = {
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
        self.update_status.set(message)

    def _open_update_page(self):
        if self._update_url:
            webbrowser.open(self._update_url)

    def _ui(self, key):
        return _UI_TEXT[self._language_code()][key]

    def _apply_language(self, event=None):
        """Refresh all user-facing labels while keeping the chosen data settings."""
        number_mode = self._number_mode(self.num_format.get())
        output_fmt = self._output_format(self.out_format.get())
        promotion_output_id = self._promotion_output_id(self.promotion_output_format.get())
        text = _UI_TEXT[self._language_code()]

        self.header_subtitle.configure(text=text['header_subtitle'])
        self.language_label.configure(text=text['language_label'])
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
        self.csv_tab_button.configure(text=text['task_options'][0])
        self.promotion_tab_button.configure(text=text['task_options'][1])
        self.update_check_button.configure(text=text['check_updates'])
        self.download_update_button.configure(text=text['download_update'])
        self.update_enabled_check.configure(text=text['update_enabled'])

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

    @classmethod
    def _build_output_path(cls, file_path, output_fmt, timestamp=None):
        """Create a timestamped output path and avoid overwriting a same-minute run."""
        stamp = (timestamp or datetime.now()).strftime('%Y%m%d_%H%M')
        extension = cls._output_extension(output_fmt)
        directory = os.path.dirname(file_path)
        base_name = f"processed_output_{stamp}"
        candidate = os.path.join(directory, f"{base_name}{extension}")
        sequence = 2
        while os.path.exists(candidate):
            candidate = os.path.join(directory, f"{base_name}_{sequence:02d}{extension}")
            sequence += 1
        return candidate

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
        return os.path.splitext(file_path)[1].lower() in ('.xlsx', '.xlsm', '.xls')

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
        """Infer a delimiter from logical CSV records, not physical text lines.

        This deliberately tolerates introductory one-column rows and quoted
        multi-line cells. The candidate with the most stable non-trivial row
        width wins; a one-column candidate is never selected automatically.
        """
        if self._is_excel(file_path):
            return None
        try:
            enc = self._detect_encoding(file_path)
            candidates = [';', ',', '\t', '|']
            best_delimiter = None
            best_score = None
            for cand in candidates:
                with open(file_path, 'r', encoding=enc, newline='') as f:
                    reader = csv.reader(f, delimiter=cand)
                    rows = []
                    for row in reader:
                        if row:
                            rows.append(row)
                        if len(rows) >= sample_rows:
                            break
                if not rows:
                    continue

                widths = [len(row) for row in rows]
                width, count = max(Counter(widths).items(), key=lambda item: (item[1], item[0]))
                if width <= 1:
                    continue

                # Ratio first prevents a one-off delimiter in prose from
                # beating a consistently structured file; width breaks ties.
                score = (count / len(widths), count, width)
                if best_score is None or score > best_score:
                    best_score = score
                    best_delimiter = cand
            return best_delimiter
        except Exception:
            return None

    @staticmethod
    def _normalize_delim(delim):
        # Let the user type "\t" or "tab" for tab-separated files
        # (Excel's "Unicode Text" export is tab-separated).
        return {'\\t': '\t', 'tab': '\t', 'TAB': '\t', 'Tab': '\t'}.get(delim, delim)

    def _detect_encoding(self, file_path):
        """Pick the most plausible text encoding.

        UTF-8 (with or without BOM) is authoritative when it decodes cleanly.
        Among legacy codecs, prefer the one that yields the fewest suspicious
        characters (C1 controls / replacement chars), which are a strong signal
        of a wrong codepage. This avoids cp1252 (or cp949) silently mis-decoding
        a file just because it happens to accept the bytes.
        """
        with open(file_path, 'rb') as f:
            raw = f.read()

        # Excel's "Unicode Text" / "CSV UTF-16" exports are UTF-16. The BOM is
        # authoritative, and it must be checked before UTF-8: ASCII-only
        # UTF-16 also decodes "successfully" as UTF-8 (with NULs between
        # every character), which then crashes csv with "line contains NUL".
        if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
            return 'utf-16'
        if raw and raw.count(b'\x00') > len(raw) // 4:
            for enc in ('utf-16-le', 'utf-16-be'):
                try:
                    raw.decode(enc)
                    return enc
                except UnicodeDecodeError:
                    pass

        for enc in ('utf-8-sig', 'utf-8'):
            try:
                raw.decode(enc)
                return enc
            except UnicodeDecodeError:
                pass

        best = None  # (encoding, suspicious_char_count)
        for enc in ('cp949', 'cp1252'):
            try:
                text = raw.decode(enc)
            except UnicodeDecodeError:
                continue
            score = sum(1 for ch in text if ('\x80' <= ch <= '\x9f') or ch == '�')
            if best is None or score < best[1]:
                best = (enc, score)

        if best is not None:
            return best[0]
        return 'latin-1'  # never fails; last-resort so the app degrades gracefully

    def read_file_lines(self, file_path, delim, max_lines=None):
        if self._is_excel(file_path):
            return self._read_excel_rows(file_path, max_lines), 'Excel'
        delim = self._normalize_delim(delim)
        if not delim or len(delim) != 1:
            raise ValueError("Delimiter must be a single character.")
        enc = self._detect_encoding(file_path)
        with open(file_path, 'r', encoding=enc, newline='') as f:
            reader = csv.reader(f, delimiter=delim)
            rows = []
            for i, row in enumerate(reader):
                if max_lines is not None and i >= max_lines:
                    break
                rows.append(row)
        return rows, enc

    def _read_excel_rows(self, file_path, max_lines=None):
        """Read an Excel sheet into rows of native values (no CSV round-trip,
        so quoting/encoding problems cannot occur). Trailing empty cells are
        trimmed per row so garbage/header detection works the same as for CSV."""
        pd = _get_pandas()
        df = pd.read_excel(file_path, header=None, dtype=object, nrows=max_lines)
        rows = []
        for rec in df.itertuples(index=False, name=None):
            row = []
            for v in rec:
                if v is None or (not isinstance(v, str) and pd.isna(v)):
                    row.append('')
                elif isinstance(v, datetime):
                    row.append(v.date() if (v.hour, v.minute, v.second) == (0, 0, 0) else v)
                else:
                    row.append(v)
            while row and row[-1] == '':
                row.pop()
            rows.append(row)
        return rows

    @staticmethod
    def _repair_split_rows(rows, max_c):
        """Rejoin records that unquoted in-cell line breaks split across
        physical lines (a frequent defect in Excel-exported or hand-edited
        CSVs). A short row is merged with the following row(s) only when the
        pieces reassemble to exactly max_c columns; genuinely short rows are
        left untouched and padded later as before."""
        out = []
        repaired = 0
        i = 0
        n = len(rows)
        while i < n:
            row = rows[i]
            if not 0 < len(row) < max_c:
                out.append(row)
                i += 1
                continue
            # Case 1: a break inside the LAST field leaves the record itself
            # complete and spills the remainder onto its own one-cell line —
            # glue that remainder onto the previous row's last cell. This must
            # be checked first: forward-merging such an orphan would swallow
            # the next real record instead.
            if len(row) == 1 and out and len(out[-1]) == max_c:
                out[-1][-1] = f"{out[-1][-1]} {row[0]}".strip()
                repaired += 1
                i += 1
                continue
            # Case 2: a break inside a MIDDLE field leaves every fragment
            # short. Rebuild the record from consecutive short fragments,
            # joining the two halves of the broken cell with a space. Never
            # consume a complete row, and commit only if the pieces
            # reassemble to exactly max_c columns.
            merged = list(row)
            j = i + 1
            while j < n and len(merged) < max_c and j - i <= 20:
                nxt = rows[j]
                if len(nxt) >= max_c or len(merged) + max(len(nxt), 1) - 1 > max_c:
                    break
                if nxt:
                    merged[-1] = f"{merged[-1]} {nxt[0]}".strip()
                    merged.extend(nxt[1:])
                j += 1
            if len(merged) == max_c and j > i + 1:
                out.append(merged)
                repaired += 1
                i = j
                continue
            out.append(row)
            i += 1
        return out, repaired

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
        except Exception as e:
            self.log_text.set(self._ui('detect_columns_error'))

    @staticmethod
    def parse_number(val, mode):
        """Parse a safely validated English or Polish number into Decimal.

        A parsed value retains its source decimal scale so CSV and Excel
        exporters can preserve values such as ``500,00`` cell by cell.
        Invalid or identifier-like values deliberately remain text.
        """
        if not isinstance(val, str):
            return val
        v = val.strip()
        if not v:
            return val

        v_norm = v.replace('\u00A0', ' ').replace('\u202F', ' ').replace('\u2212', '-')

        sign = ''
        if v_norm.startswith('-') or v_norm.startswith('+'):
            sign = v_norm[0]
            v_norm = v_norm[1:]

        if not v_norm:
            return val

        if mode == 'Polish':
            if not re.fullmatch(r'[0-9 \.,]+', v_norm):
                return val

            parts = v_norm.split(',')
            if len(parts) > 2:
                return val

            int_part = parts[0]
            frac_part = parts[1] if len(parts) == 2 else None

            if frac_part == '':
                return val

            if not int_part and frac_part:
                int_part = '0'

            if ' ' in int_part and '.' in int_part:
                return val

            sep = ' ' if ' ' in int_part else '.' if '.' in int_part else None
            if sep:
                groups = int_part.split(sep)
                if not groups[0] or len(groups[0]) > 3:
                    return val
                for g in groups[1:]:
                    if len(g) != 3:
                        return val
                int_part = ''.join(groups)

            if frac_part and ('.' in frac_part or ' ' in frac_part):
                return val

            if len(int_part) > 1 and int_part.startswith('0'):
                return val

            orig_decimals = len(frac_part) if frac_part else 0
            dec_str = f"{sign}{int_part}.{frac_part}" if frac_part else f"{sign}{int_part}"

            try:
                d = decimal.Decimal(dec_str)
                return ParsedNumber(d, orig_decimals, val)
            except decimal.InvalidOperation:
                return val

        elif mode == 'English':
            if not re.fullmatch(r'[0-9\.,]+', v_norm):
                return val

            parts = v_norm.split('.')
            if len(parts) > 2:
                return val

            int_part = parts[0]
            frac_part = parts[1] if len(parts) == 2 else None

            if frac_part == '':
                return val

            if not int_part and frac_part:
                int_part = '0'

            if ',' in int_part:
                groups = int_part.split(',')
                if not groups[0] or len(groups[0]) > 3:
                    return val
                for g in groups[1:]:
                    if len(g) != 3:
                        return val
                int_part = ''.join(groups)

            if frac_part and ',' in frac_part:
                return val

            if len(int_part) > 1 and int_part.startswith('0'):
                return val

            orig_decimals = len(frac_part) if frac_part else 0
            dec_str = f"{sign}{int_part}.{frac_part}" if frac_part else f"{sign}{int_part}"

            try:
                d = decimal.Decimal(dec_str)
                return ParsedNumber(d, orig_decimals, val)
            except decimal.InvalidOperation:
                return val

        return val

    @staticmethod
    def _decimal_significant_digits(value):
        """Count significant decimal digits without treating trailing scale zeros as precision."""
        normalized = value.normalize()
        digits = normalized.as_tuple().digits
        return 1 if all(digit == 0 for digit in digits) else len(digits)

    @classmethod
    def _requires_excel_text(cls, value):
        """Excel stores at most 15 significant decimal digits reliably."""
        return cls._decimal_significant_digits(value.value) > 15

    @staticmethod
    def _format_csv_value(value, decimal_separator):
        """Render a converted value without losing its original decimal scale."""
        if isinstance(value, bool):
            return value
        if isinstance(value, ParsedNumber):
            text = f"{value.value:.{value.orig_decimals}f}"
            return text.replace('.', decimal_separator) if decimal_separator != '.' else text
        if isinstance(value, float):
            if math.isnan(value):
                return ''
            text = repr(value)
            return text.replace('.', decimal_separator) if decimal_separator != '.' else text
        return value

    @classmethod
    def _write_excel_output(cls, df, out_path):
        """Write converted data and return how many values were protected as text."""
        import openpyxl

        workbook = openpyxl.Workbook()
        worksheet = workbook.active
        worksheet.append(list(df.columns))
        large_numbers_as_text = 0

        for row in df.itertuples(index=False):
            output_row = []
            for value in row:
                if isinstance(value, ParsedNumber):
                    if cls._requires_excel_text(value):
                        large_numbers_as_text += 1
                        output_row.append(value.orig_text)
                    else:
                        output_row.append(float(value.value))
                else:
                    output_row.append(value)
            worksheet.append(output_row)

        for row_index, row in enumerate(df.itertuples(index=False), start=2):
            for column_index, value in enumerate(row, start=1):
                if isinstance(value, ParsedNumber) and not cls._requires_excel_text(value):
                    cell = worksheet.cell(row=row_index, column=column_index)
                    cell.number_format = '0' if value.orig_decimals == 0 else '0.' + ('0' * value.orig_decimals)

        workbook.save(out_path)
        return large_numbers_as_text

    def parse_date(self, val):
        """Try to parse a string into a date. Returns a date on success, else the original value."""
        if not isinstance(val, str):
            return val
        v = val.strip()
        # Skip strptime unless the value even looks like a date. This is the
        # single biggest speed-up on large files (most cells are not dates).
        if not v or not _DATE_HINT_RE.fullmatch(v):
            return val
        formats = [
            "%Y-%m-%d", "%Y/%m/%d",
            "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y",
            "%m/%d/%Y",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(v, fmt).date()
            except ValueError:
                continue
        return val

    def convert_value(self, val, mode):
        """Type coercion for a single cell: try date, then number, else keep original text."""
        d = self.parse_date(val)
        if not isinstance(d, str):
            return d
        return self.parse_number(val, mode)

    def _set_progress(self, pct, msg=None):
        """Move the progress bar and (optionally) the status text, then repaint."""
        try:
            self.progress['value'] = max(0, min(100, pct))
            if msg is not None:
                self.log_text.set(msg)
            self.root.update_idletasks()
        except Exception:
            pass

    def process_csv(self):
        file_path = self.filepath.get()
        delim = self._normalize_delim(self.delimiter.get())
        mode = self._number_mode(self.num_format.get())
        output_fmt = self._output_format(self.out_format.get())

        if not file_path or not os.path.exists(file_path):
            messagebox.showerror(self._ui('select_file_title'), self._ui('select_file_message'))
            return

        is_excel = self._is_excel(file_path)
        if not is_excel and (not delim or len(delim) != 1):
            messagebox.showerror(self._ui('delimiter_title'), self._ui('delimiter_message'))
            return

        try:
            max_c = int(self.max_cols.get())
        except ValueError:
            messagebox.showerror(self._ui('columns_title'), self._ui('columns_number'))
            return

        if max_c <= 0:
            messagebox.showerror(self._ui('columns_title'), self._ui('columns_positive'))
            return

        self._set_progress(0, self._ui('reading'))
        self.root.update()

        try:
            rows, enc = self.read_file_lines(file_path, delim)
            self._set_progress(10, self._ui('scanning').format(rows=f"{len(rows):,}"))

            # Find the start index (first row that has max_cols) to skip garbage
            start_idx = 0
            for i, row in enumerate(rows):
                if len(row) == max_c:
                    start_idx = i
                    break

            garbage_skipped = start_idx
            data_rows = rows[start_idx:]

            # Rejoin records that unquoted in-cell line breaks split across
            # physical lines (Excel input needs no repair: cells arrive intact).
            rows_repaired = 0
            if not is_excel:
                data_rows, rows_repaired = self._repair_split_rows(data_rows, max_c)

            # Pad or truncate short/long rows to exactly max_c columns
            padded_rows = []
            for r in data_rows:
                r = list(r)
                if len(r) < max_c:
                    r.extend([''] * (max_c - len(r)))
                elif len(r) > max_c:
                    r = r[:max_c]
                padded_rows.append(r)

            if not padded_rows:
                messagebox.showwarning(self._ui('no_table_title'), self._ui('no_table_message'))
                self.log_text.set(self._ui('no_table_title'))
                return

            # Promote the first matching row to the header, remaining rows are data
            header_row = padded_rows[0]
            body_rows = padded_rows[1:]

            if not body_rows:
                messagebox.showwarning(self._ui('no_data_title'), self._ui('no_data_message'))
                self.log_text.set(self._ui('no_data_title'))
                return

            # Build unique, non-empty column names from the header
            col_names = []
            seen = {}
            for i, name in enumerate(header_row):
                name = '' if name is None else str(name)
                name = _LINEBREAK_RE.sub(' ', name).strip()
                name = name or f"Column{i+1}"
                if name in seen:
                    seen[name] += 1
                    name = f"{name}_{seen[name]}"
                else:
                    seen[name] = 0
                col_names.append(name)

            # Create DataFrame from the data rows (header excluded)
            pd = _get_pandas()
            df = pd.DataFrame(body_rows, columns=col_names)

            # Type coercion (date -> number -> text) in a single pass per column
            # that also tallies what changed and records each column's original
            # decimal precision (used to keep values like "500,00" intact on export).
            dec_sep = ',' if mode == 'Polish' else '.'
            stats = {'numbers': 0, 'dates': 0, 'flattened': 0, 'large_numbers_as_text': 0}
            ncols = len(df.columns)

            for ci, col in enumerate(df.columns):
                def convert(x):
                    if not isinstance(x, str):
                        return x  # Excel input arrives already typed (dates, numbers)
                    # Flatten in-cell line breaks — including the exotic ones Excel
                    # produces (\x0b from Alt+Enter copy-paste, U+2028/U+2029) — so a
                    # multi-line field becomes a single physical line in the output.
                    if _LINEBREAK_RE.search(x):
                        x = _LINEBREAK_RE.sub(' ', x)
                        stats['flattened'] += 1
                    d = self.parse_date(x)
                    if not isinstance(d, str):
                        stats['dates'] += 1
                        return d
                    n = self.parse_number(x, mode)
                    if isinstance(n, ParsedNumber):
                        stats['numbers'] += 1
                        return n
                    return n

                df[col] = df[col].map(convert)
                self._set_progress(10 + int(70 * (ci + 1) / ncols),
                                   self._ui('converting').format(current=ci + 1, total=ncols))

            # Replace NaNs with empty strings
            df = df.fillna('')

            # Export
            self._set_progress(85, self._ui('saving'))
            out_path = self._build_output_path(file_path, output_fmt)
            if output_fmt == "Excel (.xlsx)":
                stats['large_numbers_as_text'] = self._write_excel_output(df, out_path)
            else:
                sep = ';' if mode == 'Polish' else ','
                out_df = df.copy()
                for col in out_df.columns:
                    out_df[col] = out_df[col].map(
                        lambda value: self._format_csv_value(value, dec_sep)
                    )

                out_df.to_csv(out_path, sep=sep, index=False, encoding='utf-8-sig')

            self._set_progress(100, self._ui('done'))
            self._last_result = {
                'out_path': out_path,
                'rows': f"{len(body_rows):,}",
                'columns': f"{ncols:,}",
                'garbage_skipped': f"{garbage_skipped:,}",
                'numbers': f"{stats['numbers']:,}",
                'dates': f"{stats['dates']:,}",
                'flattened': f"{stats['flattened']:,}",
                'repaired': f"{rows_repaired:,}",
                'large_numbers_as_text': f"{stats['large_numbers_as_text']:,}",
                'encoding': enc,
            }
            self.log_text.set(
                self._ui('status_done').format(rows=f"{len(body_rows):,}", name=os.path.basename(out_path))
            )
            self._set_result_text(self._format_result_summary(self._last_result))

        except Exception as e:
            messagebox.showerror(self._ui('error_title'), self._ui('error_message').format(error=str(e)))
            self.log_text.set(self._ui('error_title'))
        finally:
            self._set_progress(0)

if __name__ == "__main__":
    root = tk.Tk()
    app = DataRefineryApp(root)
    root.mainloop()
