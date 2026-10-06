import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import os
import shutil
import sys
import webbrowser
from pathlib import Path

from src.background_jobs import BackgroundJobRunner, JobCallbacks
from src.diagnostics import record_error
from src.csv_processing import (
    CsvColumnOverflowError,
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
from src.dataset_ui import DatasetPublisherTabFrame
from src.file_reveal import open_containing_folder
from src.update_checker import check_for_update, load_settings, save_settings
from src.ui_components import PALETTE, UpdateMenu

from src.version import __version__

from src.i18n import _LANGUAGE_CODES, _UI_TEXT



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
        self._last_promotion_result = None
        self._last_promotion_source_path = None
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
        style.configure(
            "TaskTab.TButton",
            background=page_bg,
            foreground=muted,
            font=("Segoe UI", 10),
            padding=(18, 10),
            borderwidth=0,
            focuscolor=accent,
            focusthickness=1,
        )
        style.map(
            "TaskTab.TButton",
            background=[("active", "#E3EAF2"), ("pressed", "#DAE3ED")],
            foreground=[("active", text)],
            focuscolor=[("focus", accent)],
        )
        style.configure(
            "TaskTab.Selected.TButton",
            background=surface,
            foreground=accent,
            font=("Segoe UI Semibold", 10),
            padding=(18, 10),
            borderwidth=0,
            focuscolor=accent,
            focusthickness=1,
        )
        style.map(
            "TaskTab.Selected.TButton",
            background=[("active", surface), ("pressed", surface)],
            foreground=[("active", accent)],
            focuscolor=[("focus", accent)],
        )

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

        self.publisher_tab_button = ttk.Button(
            self.task_tabs,
            command=lambda: self._select_task_tab(self.publisher_tab),
            style="TaskTab.TButton",
            width=18,
        )
        self.publisher_tab_button.pack(side="left", padx=2)

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
        self.publisher_tab = DatasetPublisherTabFrame(self.notebook, self, padding=(0, 6, 0, 0))
        self.notebook.add(self.csv_tab)
        self.notebook.add(self.promotion_tab)
        self.notebook.add(self.aggregator_tab)
        self.notebook.add(self.publisher_tab)
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
        self.promotion_filepath.trace_add("write", self._on_promotion_filepath_change)
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
        self.promotion_open_folder_button = ttk.Button(
            self.promotion_action_area,
            command=self.open_promotion_output_folder,
            style="Secondary.TButton",
            state="disabled",
        )
        self.promotion_open_folder_button.grid(row=1, column=1, padx=(0, 8), sticky="e")
        self.btn_promo_open_folder = self.promotion_open_folder_button
        self.promotion_process_button = ttk.Button(
            self.promotion_action_area,
            command=self.process_promotion_template,
            style="Primary.TButton",
            state="disabled",
        )
        self.promotion_process_button.grid(row=1, column=2, sticky="e")

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

    @staticmethod
    def _is_same_template(path1, path2) -> bool:
        if not path1 or not path2:
            return False
        try:
            return os.path.normcase(os.path.abspath(str(path1))) == os.path.normcase(os.path.abspath(str(path2)))
        except Exception:
            return str(path1) == str(path2)

    def _clear_promotion_result(self):
        self._last_promotion_result = None
        self._last_promotion_source_path = None
        self._refresh_promotion_folder_button_state()
        if self._selected_task_id() == "promotion":
            if self._promotion_data is not None:
                self._show_promotion_preview(self._promotion_data)
            else:
                self._set_result_text(self._ui("promo_initial_result"))
                self.log_text.set(self._ui("promo_initial_result"))

    def _on_promotion_filepath_change(self, *args):
        new_path = self.promotion_filepath.get()
        if self._last_promotion_source_path and not self._is_same_template(new_path, self._last_promotion_source_path):
            self._clear_promotion_result()
        self._refresh_promotion_action_state()

    def browse_promotion_template(self):
        filename = filedialog.askopenfilename(
            title=self._ui("promo_select_title"),
            filetypes=(("Excel template", "*.xlsx"),),
        )
        if filename:
            if not self._is_same_template(filename, self._last_promotion_source_path):
                self._clear_promotion_result()
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

    def _refresh_promotion_folder_button_state(self):
        button = getattr(self, "promotion_open_folder_button", None)
        if button is None:
            return
        is_ready = bool(
            self._last_promotion_result is not None
            and not getattr(self, "_promotion_processing", False)
        )
        button.configure(state="normal" if is_ready else "disabled")

    def open_promotion_output_folder(self) -> bool:
        if not self._last_promotion_result:
            return False
        daily_path = (
            getattr(self._last_promotion_result, "daily_path", None)
            or (self._last_promotion_result.get("daily_path") if isinstance(self._last_promotion_result, dict) else None)
        )
        if not daily_path:
            return False
        target = str(daily_path)
        if not open_containing_folder(target):
            self._warn_promotion_open_failed(target)
            return False
        return True

    def _warn_promotion_open_failed(self, path: str):
        messagebox.showwarning(
            self._ui("promo_msg_open_failed_title"),
            self._ui("promo_msg_open_failed").format(path=path),
        )

    def _format_promotion_result_summary(self, result):
        daily_rows = getattr(result, "daily_rows", None)
        if daily_rows is None and isinstance(result, dict):
            daily_rows = result.get("daily_rows", 0)
        daily_rows_str = f"{int(daily_rows):,}" if daily_rows is not None else "0"

        master_path = getattr(result, "master_path", None) or (result.get("master_path") if isinstance(result, dict) else "")
        rules_path = getattr(result, "rules_path", None) or (result.get("rules_path") if isinstance(result, dict) else "")
        daily_path = getattr(result, "daily_path", None) or (result.get("daily_path") if isinstance(result, dict) else "")
        overlap = getattr(result, "overlapping_rule_pairs", None)
        if overlap is None and isinstance(result, dict):
            overlap = result.get("overlapping_rule_pairs", 0)

        master_name = Path(master_path).name if master_path else ""
        rules_name = Path(rules_path).name if rules_path else ""
        daily_name = Path(daily_path).name if daily_path else ""
        output_dir = str(Path(daily_path).parent.resolve()) if daily_path else ""

        done = self._ui("promo_done").format(rows=daily_rows_str)
        lines = [
            done,
            self._ui("promo_summary_location").format(path=output_dir),
            "",
            self._ui("promo_summary_title"),
            self._ui("promo_master_file").format(name=master_name),
            self._ui("promo_rules_file").format(name=rules_name),
            self._ui("promo_daily_file").format(name=daily_name),
            self._ui("promo_overlap").format(count=overlap if overlap is not None else 0),
        ]
        return "\n".join(lines)

    def _on_promotion_success(self, result):
        self._last_promotion_result = result
        self._last_promotion_source_path = self.promotion_filepath.get()
        self._refresh_promotion_folder_button_state()
        daily_rows = getattr(result, "daily_rows", None)
        if daily_rows is None and isinstance(result, dict):
            daily_rows = result.get("daily_rows", 0)
        daily_rows_str = f"{int(daily_rows):,}" if daily_rows is not None else "0"
        done = self._ui("promo_done").format(rows=daily_rows_str)
        self._set_progress(100, done)
        self._set_result_text(self._format_promotion_result_summary(result))
        self.log_text.set(done)

    def _on_promotion_error(self, error):
        messagebox.showerror(
            self._ui("promo_result_title"), str(error) + self.report_error("promotion_export", error)
        )
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
            self._refresh_promotion_folder_button_state()
        else:
            self.promotion_process_button.configure(state="disabled")
            self.promotion_open_folder_button.configure(state="disabled")

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
        if hasattr(self, "publisher_tab") and sel == str(self.publisher_tab):
            return "publisher"
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
        if hasattr(self, "publisher_tab_button"):
            self.publisher_tab_button.configure(
                style="TaskTab.Selected.TButton" if tid == "publisher" else "TaskTab.TButton"
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
            if self._last_promotion_result is not None:
                self._set_result_text(self._format_promotion_result_summary(self._last_promotion_result))
                daily_rows = (
                    getattr(self._last_promotion_result, "daily_rows", None)
                    or (self._last_promotion_result.get("daily_rows") if isinstance(self._last_promotion_result, dict) else 0)
                )
                self.log_text.set(self._ui("promo_done").format(rows=f"{int(daily_rows):,}"))
            elif self._promotion_data is None:
                self._set_result_text(self._ui("promo_initial_result"))
                self.log_text.set(self._ui("promo_initial_result"))
            else:
                self._show_promotion_preview(self._promotion_data)
        elif self._selected_task_id() == "aggregator":
            self.result_section.configure(text=self._ui("task_options")[2])
            self._set_result_text(self._ui("agg_initial_result"))
            self.log_text.set(self._ui("task_options")[2])
        elif self._selected_task_id() == "publisher":
            self.result_section.configure(text=self._ui("task_options")[3])
            self._set_result_text(self._ui("pub_initial_result"))
            self.log_text.set(self._ui("task_options")[3])
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
            failures = []
            release = check_for_update(
                __version__, self._update_settings, on_error=failures.append
            )
            save_settings(self._update_settings)
            return release, failures[0] if failures else None

        self._jobs.start(
            "update-check",
            worker,
            JobCallbacks(
                on_progress=lambda _percent, _detail: None,
                on_success=self._finish_update_check,
                on_error=self._finish_update_error,
                on_finished=lambda: None,
            ),
        )

    def _finish_update_error(self, error):
        record_error("update_check", error)
        self._update_state = "failed"
        self._update_version = None
        self._refresh_update_status()

    def _finish_update_check(self, outcome):
        release, error = outcome
        if error is not None:
            self._finish_update_error(error)
            return
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
            "failed": "update_failed",
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

    def report_error(self, operation, error):
        error_id = record_error(operation, error)
        return self._ui("error_id").format(id=error_id)

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
        if hasattr(self, "publisher_tab"):
            self.notebook.tab(self.publisher_tab, text=text['task_options'][3])
        self.csv_tab_button.configure(text=text['task_options'][0])
        self.promotion_tab_button.configure(text=text['task_options'][1])
        self.aggregator_tab_button.configure(text=text['task_options'][2])
        if hasattr(self, "publisher_tab_button"):
            self.publisher_tab_button.configure(text=text['task_options'][3])
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
        self.promotion_open_folder_button.configure(text=text['promo_open_folder'])
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
        elif isinstance(error, CsvColumnOverflowError):
            messagebox.showwarning(
                self._ui("columns_title"),
                self._ui("column_overflow_message").format(
                    row=error.record_number,
                    actual=error.actual,
                    expected=error.expected,
                ),
            )
            self.log_text.set(self._ui("columns_title"))
        else:
            messagebox.showerror(
                self._ui("error_title"),
                self._ui("error_message").format(error=str(error))
                + self.report_error("csv_processing", error),
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

def main() -> None:
    if "--version" in sys.argv or "-v" in sys.argv:
        print(f"Data Refinery v{__version__}")
        sys.exit(0)
    if "--legacy-tk" in sys.argv:
        root = tk.Tk()
        app = DataRefineryApp(root)
        root.mainloop()
    else:
        from src.qt.app import create_or_get_app
        from src.qt.main_window import MainWindow

        app = create_or_get_app()
        window = MainWindow()
        window.show()
        sys.exit(app.exec())


if __name__ == "__main__":
    main()
