"""Dataset Publisher UI Tab Frame for Data Refinery.

Provides multi-dataset management, input change detection (new vs modified periods),
transactional accumulation/replacement via DuckDB background jobs,
visual inspection/validation diffs, approval gating, and safe shared folder publication
with Excel Power Query template export.
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, Optional

from src.background_jobs import JobCallbacks
from src.dataset_config import DatasetDefinition, DatasetRegistry
from src.dataset_engine import (
    DatasetEngine,
    DatasetPublishLockError,
    InspectionResult,
    ScanSummary,
)
from src.dataset_excel import (
    create_excel_template_workbook,
    get_powerquery_guide_text,
)
from src.file_reveal import open_containing_folder
from src.ui_components import PALETTE


class DatasetEditDialog(tk.Toplevel):
    """Modal dialog to register or edit a dataset configuration."""

    def __init__(
        self,
        parent: tk.Widget,
        dataset: Optional[DatasetDefinition] = None,
        on_save: Optional[Callable[[DatasetDefinition], None]] = None,
    ):
        super().__init__(parent)
        self.transient(parent)
        self.grab_set()
        self.title("데이터셋 등록 · 수정" if dataset else "새 데이터셋 등록")
        self.geometry("640x620")
        self.minsize(580, 560)
        self.dataset = dataset
        self.on_save = on_save
        self.result: Optional[DatasetDefinition] = None

        self._init_ui()
        self.focus_set()

    def _init_ui(self) -> None:
        pad = 14
        container = ttk.Frame(self, padding=pad)
        container.pack(fill="both", expand=True)

        # Form fields
        self.var_name = tk.StringVar(value=self.dataset.name if self.dataset else "")
        self.var_input = tk.StringVar(value=self.dataset.input_folder if self.dataset else "")
        self.var_publish = tk.StringVar(value=self.dataset.publish_folder if self.dataset else "")
        self.var_period_col = tk.StringVar(value=self.dataset.period_column if self.dataset else "기준년월")
        self.var_period_fmt = tk.StringVar(value=self.dataset.period_format if self.dataset else "Auto")
        self.var_numeric_cols = tk.StringVar(
            value=", ".join(self.dataset.numeric_columns) if self.dataset else "매출액, 영업이익"
        )
        self.var_key_cols = tk.StringVar(
            value=", ".join(self.dataset.key_columns) if self.dataset else "거래선코드, 상품코드"
        )
        self.var_merge_mode = tk.StringVar(value=self.dataset.merge_mode if self.dataset else "union")
        self.var_encoding = tk.StringVar(value=self.dataset.encoding if self.dataset else "auto")

        # 1. Dataset Name
        lbl_name = ttk.Label(container, text="데이터셋 이름 (예: 거래선 손익, TV 상세, 냉장고 상세):", font=("Segoe UI Semibold", 9))
        lbl_name.pack(anchor="w", pady=(0, 2))
        ent_name = ttk.Entry(container, textvariable=self.var_name)
        ent_name.pack(fill="x", pady=(0, 10))

        # 2. Input Folder
        lbl_in = ttk.Label(container, text="입력 폴더 (정리된 월별 CSV가 모이는 곳):", font=("Segoe UI Semibold", 9))
        lbl_in.pack(anchor="w", pady=(0, 2))
        f_in = ttk.Frame(container)
        f_in.pack(fill="x", pady=(0, 10))
        ent_in = ttk.Entry(f_in, textvariable=self.var_input)
        ent_in.pack(side="left", fill="x", expand=True, padx=(0, 4))
        btn_in = ttk.Button(f_in, text="폴더 선택...", command=self._browse_input)
        btn_in.pack(side="right")

        # 3. Publish Folder
        lbl_pub = ttk.Label(container, text="공유 배포 폴더 (소비자용 최종 CSV가 배포될 네트워크/공유 경로):", font=("Segoe UI Semibold", 9))
        lbl_pub.pack(anchor="w", pady=(0, 2))
        f_pub = ttk.Frame(container)
        f_pub.pack(fill="x", pady=(0, 10))
        ent_pub = ttk.Entry(f_pub, textvariable=self.var_publish)
        ent_pub.pack(side="left", fill="x", expand=True, padx=(0, 4))
        btn_pub = ttk.Button(f_pub, text="폴더 선택...", command=self._browse_publish)
        btn_pub.pack(side="right")

        # 4. Period column & format
        f_period = ttk.Frame(container)
        f_period.pack(fill="x", pady=(0, 10))
        f_period.columnconfigure(0, weight=2)
        f_period.columnconfigure(1, weight=1)

        f_p1 = ttk.Frame(f_period)
        f_p1.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Label(f_p1, text="기간 열 이름 (예: 기준년월, Period):", font=("Segoe UI Semibold", 9)).pack(anchor="w")
        ttk.Entry(f_p1, textvariable=self.var_period_col).pack(fill="x")

        f_p2 = ttk.Frame(f_period)
        f_p2.grid(row=0, column=1, sticky="ew")
        ttk.Label(f_p2, text="기간 형식:", font=("Segoe UI Semibold", 9)).pack(anchor="w")
        cb_fmt = ttk.Combobox(f_p2, textvariable=self.var_period_fmt, values=("Auto", "YYYY-MM", "YYYYMM", "YYYY.MM"), state="readonly")
        cb_fmt.pack(fill="x")

        # 5. Numeric columns for inspection total
        lbl_num = ttk.Label(container, text="검수 합산 수치 열 (쉼표로 구분, 예: 매출액, 수량, 영업이익):", font=("Segoe UI Semibold", 9))
        lbl_num.pack(anchor="w", pady=(0, 2))
        ent_num = ttk.Entry(container, textvariable=self.var_numeric_cols)
        ent_num.pack(fill="x", pady=(0, 10))

        # 6. Key columns for duplicate check
        lbl_key = ttk.Label(container, text="식별 키 열 (중복 검사용, 쉼표 구분, 예: 거래선코드, 상품코드):", font=("Segoe UI Semibold", 9))
        lbl_key.pack(anchor="w", pady=(0, 2))
        ent_key = ttk.Entry(container, textvariable=self.var_key_cols)
        ent_key.pack(fill="x", pady=(0, 10))

        # 7. Merge Mode & Encoding
        f_adv = ttk.LabelFrame(container, text="고급 옵션", padding=8)
        f_adv.pack(fill="x", pady=(0, 14))

        ttk.Label(f_adv, text="동일 기간 파일 복수 감지 시 처리:").grid(row=0, column=0, sticky="w", pady=2)
        f_radios = ttk.Frame(f_adv)
        f_radios.grid(row=0, column=1, sticky="w", padx=6)
        ttk.Radiobutton(f_radios, text="모두 합치기 (Union)", variable=self.var_merge_mode, value="union").pack(side="left", padx=4)
        ttk.Radiobutton(f_radios, text="오류로 차단 (Error)", variable=self.var_merge_mode, value="error").pack(side="left", padx=4)

        ttk.Label(f_adv, text="CSV 인코딩:").grid(row=1, column=0, sticky="w", pady=2)
        cb_enc = ttk.Combobox(f_adv, textvariable=self.var_encoding, values=("auto", "utf-8-sig", "utf-8", "cp949"), state="readonly", width=14)
        cb_enc.grid(row=1, column=1, sticky="w", padx=10, pady=2)

        # Buttons at bottom
        f_btns = ttk.Frame(container)
        f_btns.pack(fill="x", side="bottom")

        btn_cancel = ttk.Button(f_btns, text="취소", command=self.destroy)
        btn_cancel.pack(side="right", padx=(4, 0))

        btn_ok = ttk.Button(f_btns, text="저장", style="Accent.TButton", command=self._save)
        btn_ok.pack(side="right")

    def _browse_input(self) -> None:
        init_dir = self.var_input.get() or os.path.expanduser("~")
        chosen = filedialog.askdirectory(parent=self, initialdir=init_dir, title="입력 폴더 선택")
        if chosen:
            self.var_input.set(os.path.normpath(chosen))

    def _browse_publish(self) -> None:
        init_dir = self.var_publish.get() or os.path.expanduser("~")
        chosen = filedialog.askdirectory(parent=self, initialdir=init_dir, title="공유 배포 폴더 선택")
        if chosen:
            self.var_publish.set(os.path.normpath(chosen))

    def _save(self) -> None:
        name = self.var_name.get().strip()
        in_folder = self.var_input.get().strip()
        pub_folder = self.var_publish.get().strip()

        if not name:
            messagebox.showwarning("입력 필요", "데이터셋 이름을 입력하세요.", parent=self)
            return
        if not in_folder:
            messagebox.showwarning("입력 필요", "입력 폴더를 지정하세요.", parent=self)
            return
        if not pub_folder:
            messagebox.showwarning("입력 필요", "공유 배포 폴더를 지정하세요.", parent=self)
            return

        nums = [c.strip() for c in self.var_numeric_cols.get().split(",") if c.strip()]
        keys = [c.strip() for c in self.var_key_cols.get().split(",") if c.strip()]

        if self.dataset:
            # Edit existing
            self.dataset.name = name
            self.dataset.input_folder = in_folder
            self.dataset.publish_folder = pub_folder
            self.dataset.period_column = self.var_period_col.get().strip() or "기준년월"
            self.dataset.period_format = self.var_period_fmt.get()
            self.dataset.numeric_columns = nums
            self.dataset.key_columns = keys
            self.dataset.merge_mode = self.var_merge_mode.get()
            self.dataset.encoding = self.var_encoding.get()
            self.result = self.dataset
        else:
            # Create new
            self.result = DatasetDefinition.create_new(
                name=name,
                input_folder=in_folder,
                publish_folder=pub_folder,
                period_column=self.var_period_col.get().strip() or "기준년월",
                period_format=self.var_period_fmt.get(),
                numeric_columns=nums,
                key_columns=keys,
                merge_mode=self.var_merge_mode.get(),
                encoding=self.var_encoding.get(),
            )

        if self.on_save and self.result:
            self.on_save(self.result)

        self.destroy()


class DatasetPublisherTabFrame(ttk.Frame):
    """
    Main UI component for the 4th tab: '데이터셋 배포' (Dataset Publisher).
    Manages datasets, displays scan diffs, executes accumulation/inspection,
    verifies metrics, and orchestrates safe publishing.
    """

    def __init__(self, parent: tk.Widget, app: Any, **kwargs):
        super().__init__(parent, style="App.TFrame", **kwargs)
        self.app = app
        self.registry = DatasetRegistry()
        self.current_dataset: Optional[DatasetDefinition] = None
        self.current_engine: Optional[DatasetEngine] = None
        self.last_scan: Optional[ScanSummary] = None
        self.last_inspection: Optional[InspectionResult] = None
        self._cancel_event: Optional[threading.Event] = None

        self._init_ui()
        self._refresh_dataset_list()

    def _init_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # PanedWindow: Left (Dataset list) | Right (Workspace)
        self.paned = ttk.PanedWindow(self, orient="horizontal")
        self.paned.grid(row=0, column=0, sticky="nsew")

        # --- LEFT PANEL: Dataset List ---
        self.left_panel = ttk.Frame(self.paned, padding=8)
        self.paned.add(self.left_panel, weight=1)

        # Left Header
        f_left_hdr = ttk.Frame(self.left_panel)
        f_left_hdr.pack(fill="x", pady=(0, 6))
        ttk.Label(f_left_hdr, text="데이터셋 목록", font=("Segoe UI Semibold", 10)).pack(side="left")

        # Action buttons
        f_left_btns = ttk.Frame(self.left_panel)
        f_left_btns.pack(fill="x", pady=(0, 6))
        self.btn_add_ds = ttk.Button(f_left_btns, text="+ 새 데이터셋", command=self._on_add_dataset, width=12)
        self.btn_add_ds.pack(side="left", padx=(0, 4))
        self.btn_edit_ds = ttk.Button(f_left_btns, text="설정 수정", command=self._on_edit_dataset, width=9)
        self.btn_edit_ds.pack(side="left", padx=(0, 4))
        self.btn_del_ds = ttk.Button(f_left_btns, text="삭제", command=self._on_delete_dataset, width=7)
        self.btn_del_ds.pack(side="left")

        # Treeview for dataset list
        columns = ("name", "periods", "status", "published")
        self.tree_ds = ttk.Treeview(self.left_panel, columns=columns, show="headings", selectmode="browse", height=15)
        self.tree_ds.heading("name", text="데이터셋명")
        self.tree_ds.heading("periods", text="포함 기간")
        self.tree_ds.heading("status", text="상태")
        self.tree_ds.heading("published", text="마지막 배포")

        self.tree_ds.column("name", width=120, anchor="w")
        self.tree_ds.column("periods", width=110, anchor="center")
        self.tree_ds.column("status", width=80, anchor="center")
        self.tree_ds.column("published", width=120, anchor="center")

        tree_scroll = ttk.Scrollbar(self.left_panel, orient="vertical", command=self.tree_ds.yview)
        self.tree_ds.configure(yscrollcommand=tree_scroll.set)

        self.tree_ds.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")

        self.tree_ds.bind("<<TreeviewSelect>>", self._on_dataset_select)
        self.tree_ds.bind("<Double-1>", lambda e: self._on_edit_dataset())

        # --- RIGHT PANEL: Workspace with scrollable cards ---
        self.right_panel = ttk.Frame(self.paned, padding=8)
        self.paned.add(self.right_panel, weight=3)

        self._init_workspace()

    def _init_workspace(self) -> None:
        """Create right workspace cards."""
        # Scrollable container for right panel
        self.ws_canvas = tk.Canvas(self.right_panel, highlightthickness=0, bg=PALETTE["page_bg"])
        self.ws_scrollbar = ttk.Scrollbar(self.right_panel, orient="vertical", command=self.ws_canvas.yview)
        self.ws_content = ttk.Frame(self.ws_canvas, style="App.TFrame")

        self.ws_content.bind(
            "<Configure>",
            lambda e: self.ws_canvas.configure(scrollregion=self.ws_canvas.bbox("all")),
        )
        self.ws_canvas_window = self.ws_canvas.create_window((0, 0), window=self.ws_content, anchor="nw")
        self.ws_canvas.configure(yscrollcommand=self.ws_scrollbar.set)

        # Handle canvas resize
        self.ws_canvas.bind(
            "<Configure>",
            lambda e: self.ws_canvas.itemconfig(self.ws_canvas_window, width=e.width),
        )

        self.ws_canvas.pack(side="left", fill="both", expand=True)
        self.ws_scrollbar.pack(side="right", fill="y")

        # Card 1: Overview & Paths
        self.card_overview = ttk.LabelFrame(self.ws_content, text="1. 데이터셋 개요 및 경로", padding=10)
        self.card_overview.pack(fill="x", pady=(0, 10))

        self.lbl_ds_title = ttk.Label(self.card_overview, text="선택된 데이터셋 없음", font=("Segoe UI Semibold", 11))
        self.lbl_ds_title.pack(anchor="w", pady=(0, 4))

        f_paths = ttk.Frame(self.card_overview)
        f_paths.pack(fill="x", pady=(0, 4))
        f_paths.columnconfigure(1, weight=1)

        ttk.Label(f_paths, text="입력 폴더:", font=("Segoe UI Semibold", 9)).grid(row=0, column=0, sticky="w", pady=2)
        self.lbl_in_path = ttk.Label(f_paths, text="-", foreground=PALETTE["muted"])
        self.lbl_in_path.grid(row=0, column=1, sticky="w", padx=6, pady=2)
        self.btn_open_in = ttk.Button(f_paths, text="열기", width=5, command=self._open_in_folder)
        self.btn_open_in.grid(row=0, column=2, padx=2)

        ttk.Label(f_paths, text="배포 폴더:", font=("Segoe UI Semibold", 9)).grid(row=1, column=0, sticky="w", pady=2)
        self.lbl_pub_path = ttk.Label(f_paths, text="-", foreground=PALETTE["muted"])
        self.lbl_pub_path.grid(row=1, column=1, sticky="w", padx=6, pady=2)
        self.btn_open_pub = ttk.Button(f_paths, text="열기", width=5, command=self._open_pub_folder)
        self.btn_open_pub.grid(row=1, column=2, padx=2)

        f_meta = ttk.Frame(self.card_overview)
        f_meta.pack(fill="x", pady=(4, 0))
        self.lbl_period_info = ttk.Label(f_meta, text="포함 기간: - · 총 행수: -", font=("Segoe UI", 9))
        self.lbl_period_info.pack(side="left")
        self.lbl_pub_info = ttk.Label(f_meta, text="마지막 배포: -", foreground=PALETTE["muted"])
        self.lbl_pub_info.pack(side="right")

        # Card 2: Scan & Accumulate execution
        self.card_scan = ttk.LabelFrame(self.ws_content, text="2. 새 자료 확인 및 기간 누적 · 교체", padding=10)
        self.card_scan.pack(fill="x", pady=(0, 10))

        f_scan_hdr = ttk.Frame(self.card_scan)
        f_scan_hdr.pack(fill="x", pady=(0, 6))

        self.btn_scan = ttk.Button(f_scan_hdr, text="🔍 새 자료 확인", command=self._on_scan_files)
        self.btn_scan.pack(side="left", padx=(0, 8))

        self.btn_accumulate = ttk.Button(
            f_scan_hdr,
            text="⚡ 누적 · 검수 실행",
            style="Accent.TButton",
            command=self._on_accumulate_click,
        )
        self.btn_accumulate.pack(side="left", padx=(0, 8))

        self.btn_cancel = ttk.Button(f_scan_hdr, text="취소", command=self._on_cancel_job, state="disabled")
        self.btn_cancel.pack(side="left")

        # Progress bar
        self.progress_bar = ttk.Progressbar(self.card_scan, mode="determinate")
        self.progress_bar.pack(fill="x", pady=(6, 4))
        self.lbl_status = ttk.Label(self.card_scan, text="준비됨. '새 자료 확인'을 클릭하여 입력 폴더를 점검하세요.", foreground=PALETTE["muted"])
        self.lbl_status.pack(anchor="w")

        # Scan results treeview
        self.tree_scan = ttk.Treeview(
            self.card_scan,
            columns=("file", "period", "action", "detail"),
            show="headings",
            height=4,
        )
        self.tree_scan.heading("file", text="파일명")
        self.tree_scan.heading("period", text="기간")
        self.tree_scan.heading("action", text="처리 예정")
        self.tree_scan.heading("detail", text="상세 사유")

        self.tree_scan.column("file", width=180, anchor="w")
        self.tree_scan.column("period", width=70, anchor="center")
        self.tree_scan.column("action", width=80, anchor="center")
        self.tree_scan.column("detail", width=250, anchor="w")

        self.tree_scan.pack(fill="x", pady=(6, 0))

        # Card 3: Inspection & Approval
        self.card_inspect = ttk.LabelFrame(self.ws_content, text="3. 검수 결과 및 승인", padding=10)
        self.card_inspect.pack(fill="x", pady=(0, 10))

        f_inspect_metrics = ttk.Frame(self.card_inspect)
        f_inspect_metrics.pack(fill="x", pady=(0, 8))

        self.lbl_inspect_period = ttk.Label(f_inspect_metrics, text="• 기간 변화: -", font=("Segoe UI Semibold", 9))
        self.lbl_inspect_period.pack(anchor="w")
        self.lbl_inspect_rows = ttk.Label(f_inspect_metrics, text="• 행수 변화: -", font=("Segoe UI Semibold", 9))
        self.lbl_inspect_rows.pack(anchor="w")
        self.lbl_inspect_integrity = ttk.Label(f_inspect_metrics, text="• 무결성: -")
        self.lbl_inspect_integrity.pack(anchor="w")

        # Numeric diff table
        self.tree_inspect = ttk.Treeview(
            self.card_inspect,
            columns=("measure", "before", "delta", "after"),
            show="headings",
            height=4,
        )
        self.tree_inspect.heading("measure", text="수치 항목")
        self.tree_inspect.heading("before", text="반영 전 합계")
        self.tree_inspect.heading("delta", text="변경분 (Delta)")
        self.tree_inspect.heading("after", text="반영 후 합계")

        self.tree_inspect.column("measure", width=140, anchor="w")
        self.tree_inspect.column("before", width=120, anchor="e")
        self.tree_inspect.column("delta", width=120, anchor="e")
        self.tree_inspect.column("after", width=120, anchor="e")

        self.tree_inspect.pack(fill="x", pady=(0, 8))

        f_inspect_action = ttk.Frame(self.card_inspect)
        f_inspect_action.pack(fill="x")

        self.lbl_approval_badge = ttk.Label(
            f_inspect_action,
            text="[검수 미완료 · 배포 불가]",
            foreground="#D9534F",
            font=("Segoe UI Semibold", 9),
        )
        self.lbl_approval_badge.pack(side="left", padx=(0, 10))

        self.btn_approve = ttk.Button(
            f_inspect_action,
            text="✓ 결과 확인 및 검수 승인",
            command=self._on_approve_click,
            state="disabled",
        )
        self.btn_approve.pack(side="right")

        # Card 4: Safe Shared Publication & Excel Templates
        self.card_pub = ttk.LabelFrame(self.ws_content, text="4. 공유 폴더 배포 및 Excel 템플릿", padding=10)
        self.card_pub.pack(fill="x", pady=(0, 10))

        f_pub_action = ttk.Frame(self.card_pub)
        f_pub_action.pack(fill="x", pady=(0, 8))

        self.btn_publish = ttk.Button(
            f_pub_action,
            text="🚀 공유 폴더에 배포하기",
            style="Accent.TButton",
            command=self._on_publish_click,
            state="disabled",
        )
        self.btn_publish.pack(side="left", padx=(0, 8))

        self.btn_gen_template = ttk.Button(
            f_pub_action,
            text="📊 Excel 분석 템플릿 생성",
            command=self._on_create_template_click,
        )
        self.btn_gen_template.pack(side="left", padx=(0, 8))

        self.btn_copy_m = ttk.Button(
            f_pub_action,
            text="📋 Power Query M 코드 복사",
            command=self._on_copy_m_code,
        )
        self.btn_copy_m.pack(side="left")

        self.lbl_pub_notice = ttk.Label(
            self.card_pub,
            text="※ 소비자는 Excel 64비트의 기본 Power Query/피벗으로 조회합니다. (Python/DuckDB/ODBC 추가 설치 불필요)",
            foreground=PALETTE["muted"],
            font=("Segoe UI", 8),
        )
        self.lbl_pub_notice.pack(anchor="w")

    # --- Dataset Selection & List Management ---

    def _refresh_dataset_list(self) -> None:
        """Reload datasets into the left treeview."""
        datasets = self.registry.list_datasets()
        for item in self.tree_ds.get_children():
            self.tree_ds.delete(item)

        selected_id = self.current_dataset.id if self.current_dataset else None

        for ds in datasets:
            # Query periods
            engine = DatasetEngine(ds, self.registry)
            periods = engine.get_existing_periods()
            p_range = f"{periods[0]}~{periods[-1]}" if periods else "없음"

            status = "배포 완료" if ds.last_published_at else ("승인됨" if ds.inspection_approved else "검수 대기")
            pub_date = ds.last_published_at[:10] if ds.last_published_at else "-"

            self.tree_ds.insert(
                "",
                "end",
                iid=ds.id,
                values=(ds.name, p_range, status, pub_date),
            )

        if selected_id and self.tree_ds.exists(selected_id):
            self.tree_ds.selection_set(selected_id)
        elif datasets:
            first_id = datasets[0].id
            self.tree_ds.selection_set(first_id)
            self._select_dataset(datasets[0])
        else:
            self._clear_workspace()

    def _on_dataset_select(self, event: Any = None) -> None:
        sel = self.tree_ds.selection()
        if not sel:
            return
        ds_id = sel[0]
        ds = self.registry.get_dataset(ds_id)
        if ds:
            self._select_dataset(ds)

    def _select_dataset(self, ds: DatasetDefinition) -> None:
        self.current_dataset = ds
        self.current_engine = DatasetEngine(ds, self.registry)
        self._cancel_event = None

        # Update Card 1
        self.lbl_ds_title.config(text=f"{ds.name} ({ds.id})")
        self.lbl_in_path.config(text=ds.input_folder or "(미지정)")
        self.lbl_pub_path.config(text=ds.publish_folder or "(미지정)")

        periods = self.current_engine.get_existing_periods()
        p_str = f"{periods[0]} ~ {periods[-1]} ({len(periods)}개월)" if periods else "없음"
        self.lbl_period_info.config(text=f"포함 기간: {p_str}")
        self.lbl_pub_info.config(text=f"마지막 배포: {ds.last_published_at[:19] if ds.last_published_at else '없음'}")

        # Clear Scan & Inspection UI
        for item in self.tree_scan.get_children():
            self.tree_scan.delete(item)
        for item in self.tree_inspect.get_children():
            self.tree_inspect.delete(item)

        self.last_scan = None
        self.last_inspection = self.current_engine.get_latest_inspection()
        self._update_inspection_display(self.last_inspection)

        # Status & approval badge
        self._update_approval_state()

    def _clear_workspace(self) -> None:
        self.current_dataset = None
        self.current_engine = None
        self.lbl_ds_title.config(text="등록된 데이터셋이 없습니다. [+ 새 데이터셋]을 등록하세요.")
        self.lbl_in_path.config(text="-")
        self.lbl_pub_path.config(text="-")
        self.lbl_period_info.config(text="포함 기간: -")
        self.lbl_pub_info.config(text="마지막 배포: -")
        self.btn_approve.config(state="disabled")
        self.btn_publish.config(state="disabled")

    def _update_approval_state(self) -> None:
        if not self.current_dataset:
            self.btn_approve.config(state="disabled")
            self.btn_publish.config(state="disabled")
            return

        if self.current_dataset.inspection_approved:
            self.lbl_approval_badge.config(text="✓ 검수 승인 완료 · 배포 가능", foreground="#28A745")
            self.btn_publish.config(state="normal")
            self.btn_approve.config(state="disabled")
        else:
            self.lbl_approval_badge.config(text="[검수 미완료 · 배포 불가]", foreground="#D9534F")
            self.btn_publish.config(state="disabled")
            if self.last_inspection and self.last_inspection.is_valid:
                self.btn_approve.config(state="normal")
            else:
                self.btn_approve.config(state="disabled")

    # --- Actions ---

    def _on_add_dataset(self) -> None:
        def on_saved(new_ds: DatasetDefinition):
            self.registry.save_dataset(new_ds)
            self._refresh_dataset_list()
            self._select_dataset(new_ds)

        DatasetEditDialog(self, dataset=None, on_save=on_saved)

    def _on_edit_dataset(self) -> None:
        if not self.current_dataset:
            return

        def on_saved(updated_ds: DatasetDefinition):
            # Invalidate approval if key configurations change
            updated_ds.inspection_approved = False
            self.registry.save_dataset(updated_ds)
            self._refresh_dataset_list()
            self._select_dataset(updated_ds)

        DatasetEditDialog(self, dataset=self.current_dataset, on_save=on_saved)

    def _on_delete_dataset(self) -> None:
        if not self.current_dataset:
            return
        ans = messagebox.askyesno(
            "데이터셋 삭제 확인",
            f"데이터셋 '{self.current_dataset.name}' 및 로컬 작업 DB를 삭제하시겠습니까?\n"
            f"(입력 폴더 및 배포 폴더의 파일은 삭제되지 않습니다)",
            parent=self,
        )
        if ans:
            self.registry.delete_dataset(self.current_dataset.id)
            self.current_dataset = None
            self._refresh_dataset_list()

    def _open_in_folder(self) -> None:
        if self.current_dataset and self.current_dataset.input_folder:
            open_containing_folder(self.current_dataset.input_folder)

    def _open_pub_folder(self) -> None:
        if self.current_dataset and self.current_dataset.publish_folder:
            open_containing_folder(self.current_dataset.publish_folder)

    # --- Scan Files ---

    def _set_busy(self, busy: bool) -> None:
        """Lock or unlock interactive controls while a background job is running."""
        state = "disabled" if busy else "normal"
        self.btn_accumulate.config(state=state)
        self.btn_scan.config(state=state)
        self.btn_add_ds.config(state=state)
        self.btn_edit_ds.config(state=state)
        self.btn_del_ds.config(state=state)
        if hasattr(self, "btn_template"):
            self.btn_template.config(state=state)
        if hasattr(self, "btn_publish"):
            self.btn_publish.config(state="disabled" if busy else ("normal" if (self.current_dataset and self.current_dataset.inspection_approved) else "disabled"))
        if hasattr(self, "btn_approve"):
            self.btn_approve.config(state="disabled" if busy else ("normal" if (self.last_inspection and self.last_inspection.is_valid and not (self.current_dataset and self.current_dataset.inspection_approved)) else "disabled"))

        if busy:
            self.config(cursor="watch")
            self.tree_ds.unbind("<<TreeviewSelect>>")
            self.tree_ds.unbind("<Double-1>")
        else:
            self.config(cursor="")
            self.tree_ds.bind("<<TreeviewSelect>>", self._on_dataset_select)
            self.tree_ds.bind("<Double-1>", lambda e: self._on_edit_dataset())
            self._update_approval_state()

    def _on_scan_files(self) -> None:
        if not self.current_engine or not self.current_dataset:
            return

        active_ds_id = self.current_dataset.id
        self._set_busy(True)
        self.lbl_status.config(text="입력 폴더 파일 스캔 중...")
        self.progress_bar.config(value=10)

        def worker(report: Callable[[int, str], None]) -> ScanSummary:
            report(30, "입력 폴더 파일 목록 및 기간 분석 중...")
            return self.current_engine.scan_input_folder()

        def on_success(scan: ScanSummary):
            if not self.current_dataset or self.current_dataset.id != active_ds_id:
                return
            self.last_scan = scan
            for item in self.tree_scan.get_children():
                self.tree_scan.delete(item)

            action_labels = {
                "new_period": "신규 추가",
                "modified_period": "수정 교체",
                "unchanged": "건너뜀 (동일)",
                "conflict": "충돌 차단",
            }

            for sf in scan.files:
                act = action_labels.get(sf.status, sf.status)
                self.tree_scan.insert("", "end", values=(sf.file_name, sf.period, act, sf.status_detail))

            new_cnt = len(scan.new_periods)
            mod_cnt = len(scan.modified_periods)
            skip_cnt = len(scan.unchanged_files)
            msg = f"스캔 완료: 신규 {new_cnt}개 월 추가 예정, 수정 {mod_cnt}개 월 교체 예정, {skip_cnt}개 파일 동일 (건너뜀)"
            if scan.conflicts:
                msg += f" | ⚠️ 충돌 {len(scan.conflicts)}건 감지됨!"
            self.lbl_status.config(text=msg)
            self.progress_bar.config(value=100)

        def on_error(err: Exception):
            if not self.current_dataset or self.current_dataset.id != active_ds_id:
                return
            self.lbl_status.config(text="스캔 실패.")
            messagebox.showerror("스캔 오류", f"입력 폴더 스캔 중 오류가 발생했습니다:\n{err}", parent=self)

        def on_finished():
            self._set_busy(False)
            self.progress_bar.config(value=0)

        callbacks = JobCallbacks(
            on_progress=lambda pct, msg: (self.progress_bar.config(value=pct), self.lbl_status.config(text=msg)),
            on_success=on_success,
            on_error=on_error,
            on_finished=on_finished,
        )
        self._start_dataset_job("dataset_scan", worker, callbacks)

    def _start_dataset_job(self, name, worker, callbacks):
        """Use the application's real runner contract and release controls on failure."""
        runner = getattr(self.app, "job_runner", None)
        if runner is not None:
            try:
                if not runner.start(name, worker, callbacks):
                    callbacks.on_finished()
            except Exception as error:
                try:
                    callbacks.on_error(error)
                finally:
                    callbacks.on_finished()
        else:
            # Headless unit-test hosts may omit the application's runner.
            try:
                callbacks.on_success(worker(lambda p, m: None))
            except Exception as error:
                callbacks.on_error(error)
            finally:
                callbacks.on_finished()

    # --- Background Accumulate & Inspect ---

    def _on_accumulate_click(self) -> None:
        if not self.current_engine or not self.current_dataset:
            return

        # Confirmation if modified periods exist
        if self.last_scan and self.last_scan.modified_periods:
            periods_str = ", ".join(self.last_scan.modified_periods)
            ans = messagebox.askyesno(
                "수정 기간 교체 확인",
                f"다음 기존 기간의 데이터가 새 파일 내용으로 전면 교체됩니다:\n[{periods_str}]\n\n"
                f"기존의 해당 기간 데이터는 교체되고 다른 기간 데이터는 온전히 보존됩니다.\n"
                f"진행하시겠습니까?",
                parent=self,
            )
            if not ans:
                return

        active_ds_id = self.current_dataset.id
        self._cancel_event = threading.Event()
        self._set_busy(True)
        self.btn_cancel.config(state="normal")
        self.progress_bar.config(value=0)

        # Worker closure
        def worker(report: Callable[[int, str], None]) -> InspectionResult:
            return self.current_engine.accumulate_and_inspect(
                progress_callback=report,
                cancel_event=self._cancel_event,
            )

        def on_success(result: InspectionResult):
            if self.current_dataset and self.current_dataset.id == active_ds_id:
                self._on_accumulate_success(result)

        callbacks = JobCallbacks(
            on_progress=self._on_job_progress,
            on_success=on_success,
            on_error=self._on_accumulate_error,
            on_finished=self._on_accumulate_finished,
        )

        job_runner = getattr(self.app, "job_runner", None)
        if job_runner:
            job_runner.start("dataset_accumulate", worker, callbacks)
        else:
            try:
                res = worker(lambda p, m: None)
                callbacks.on_success(res)
            except Exception as e:
                callbacks.on_error(e)
            finally:
                callbacks.on_finished()

    def _on_cancel_job(self) -> None:
        if self._cancel_event:
            self._cancel_event.set()
            self.lbl_status.config(text="사용자가 취소를 요청했습니다. 롤백 중...")

    def _on_job_progress(self, pct: int, msg: str) -> None:
        self.progress_bar.config(value=pct)
        self.lbl_status.config(text=msg)

    def _on_accumulate_success(self, result: InspectionResult) -> None:
        self.last_inspection = result
        self._update_inspection_display(result)
        self._refresh_dataset_list()
        self._update_approval_state()

        if result.is_valid:
            messagebox.showinfo(
                "누적 및 검수 완료",
                f"데이터 누적 및 검수가 완료되었습니다.\n\n"
                f"• 최종 데이터셋: {result.after_row_count:,} 행 ({result.after_period_min} ~ {result.after_period_max})\n"
                f"• 검수 영역에서 수치와 전/후 비교를 확인하신 후 [검수 승인]을 완료해야 배포할 수 있습니다.",
                parent=self,
            )
        else:
            errors_str = "\n".join(f"• {e}" for e in result.errors)
            messagebox.showwarning(
                "검수 주의 필요",
                f"누적이 완료되었으나 검수에서 오류가 발견되었습니다:\n\n{errors_str}",
                parent=self,
            )

    def _on_accumulate_error(self, error: Exception) -> None:
        self.progress_bar.config(value=0)
        self.lbl_status.config(text=f"오류 발생: {error}")
        messagebox.showerror("누적 실패", f"데이터셋 처리 중 오류가 발생했습니다:\n{error}", parent=self)

    def _on_accumulate_finished(self) -> None:
        self._set_busy(False)
        self.btn_cancel.config(state="disabled")

    # --- Inspection & Approval ---

    def _update_inspection_display(self, result: Optional[InspectionResult]) -> None:
        for item in self.tree_inspect.get_children():
            self.tree_inspect.delete(item)

        if not result:
            self.lbl_inspect_period.config(text="• 기간 변화: 검수 결과 없음")
            self.lbl_inspect_rows.config(text="• 행수 변화: -")
            self.lbl_inspect_integrity.config(text="• 무결성: -")
            return

        # 1. Periods & rows
        p_before = f"{result.before_period_min or '-'} ~ {result.before_period_max or '-'}"
        p_after = f"{result.after_period_min or '-'} ~ {result.after_period_max or '-'}"
        self.lbl_inspect_period.config(text=f"• 기간 변화: [반영 전] {p_before}  ➔  [반영 후] {p_after}")

        delta_rows = result.after_row_count - result.before_row_count
        row_str = f"• 행수 변화: {result.before_row_count:,} 행  ➔  {result.after_row_count:,} 행 ({delta_rows:+,} 행)"
        self.lbl_inspect_rows.config(text=row_str)

        # 2. Integrity
        integ_parts = []
        if result.null_period_count > 0:
            integ_parts.append(f"기간 빈값 {result.null_period_count:,}건")
        if result.duplicate_key_count > 0:
            integ_parts.append(f"식별키 중복 {result.duplicate_key_count:,}건")
        if not integ_parts:
            integ_parts.append("정상 (무결성 통과)")
        self.lbl_inspect_integrity.config(text=f"• 무결성 상태: {', '.join(integ_parts)}")

        # 3. Numeric diff table
        for num_col in self.current_dataset.numeric_columns:
            b_val = result.numeric_totals_before.get(num_col, 0.0)
            d_val = result.numeric_deltas.get(num_col, 0.0)
            a_val = result.numeric_totals_after.get(num_col, 0.0)
            self.tree_inspect.insert(
                "",
                "end",
                values=(
                    num_col,
                    f"{b_val:,.2f}",
                    f"{d_val:+,.2f}",
                    f"{a_val:,.2f}",
                ),
            )

    def _on_approve_click(self) -> None:
        if not self.current_engine or not self.last_inspection:
            return
        try:
            self.current_engine.approve_inspection(self.last_inspection.approval_token)
            self._update_approval_state()
            self._refresh_dataset_list()
            messagebox.showinfo("검수 승인 완료", "검수가 공식 승인되었습니다. 이제 '공유 폴더에 배포'가 가능합니다.", parent=self)
        except Exception as e:
            messagebox.showerror("승인 오류", str(e), parent=self)

    # --- Safe Shared Publication & Excel Templates ---

    def _on_publish_click(self) -> None:
        if not self.current_engine or not self.current_dataset:
            return

        ans = messagebox.askyesno(
            "배포 확인",
            f"검수 승인된 데이터셋을 공유 폴더에 배포하시겠습니까?\n\n"
            f"• 대상 폴더: {self.current_dataset.publish_folder}\n"
            f"• 안전한 원자적 교체 및 이전 버전 자동 백업이 적용됩니다.",
            parent=self,
        )
        if not ans:
            return

        active_ds_id = self.current_dataset.id
        self._set_busy(True)
        self.progress_bar.config(value=0)
        self.lbl_status.config(text="공유 폴더 배포 준비 중...")

        def worker(report: Callable[[int, str], None]) -> Dict[str, Any]:
            return self.current_engine.publish_dataset(progress_callback=report)

        def on_success(manifest: Dict[str, Any]):
            if self.current_dataset and self.current_dataset.id == active_ds_id:
                self._refresh_dataset_list()
                self._update_approval_state()

            messagebox.showinfo(
                "배포 성공",
                f"공유 폴더에 성공적으로 배포되었습니다!\n\n"
                f"• 배포 버전: {manifest.get('version')}\n"
                f"• 파일 위치: {manifest.get('current_csv_absolute')}\n"
                f"• 총 행수: {manifest.get('row_count'):,} 행\n\n"
                f"Excel 소비자는 [데이터 > 모두 새로 고침]을 통해 즉시 확인 가능합니다.",
                parent=self,
            )

        def on_error(err: Exception):
            if isinstance(err, DatasetPublishLockError):
                messagebox.showerror("공유 파일 잠김 오류", str(err), parent=self)
            else:
                messagebox.showerror("배포 실패", f"배포 도중 오류가 발생했습니다:\n{err}", parent=self)

        def on_finished():
            self._set_busy(False)

        callbacks = JobCallbacks(
            on_progress=self._on_job_progress,
            on_success=on_success,
            on_error=on_error,
            on_finished=on_finished,
        )

        job_runner = getattr(self.app, "job_runner", None)
        if job_runner:
            job_runner.start("dataset_publish", worker, callbacks)
        else:
            try:
                res = worker(lambda p, m: None)
                callbacks.on_success(res)
            except Exception as e:
                callbacks.on_error(e)
            finally:
                callbacks.on_finished()

    def _on_create_template_click(self) -> None:
        if not self.current_dataset:
            return
        if not self.current_dataset.last_published_at:
            ans = messagebox.askyesno(
                "배포 확인",
                "아직 공유 폴더에 배포된 적이 없는 데이터셋입니다.\n"
                "배포된 CSV 경로를 기준으로 템플릿을 생성하시겠습니까?",
                parent=self,
            )
            if not ans:
                return

        # Determine target csv path in shared publish folder
        Path(self.current_dataset.publish_folder)
        target_csv = self.current_dataset.published_csv_path()

        save_path = filedialog.asksaveasfilename(
            parent=self,
            title="Excel 분석 템플릿 저장",
            initialdir=os.path.expanduser("~"),
            initialfile=f"{self.current_dataset.name}_분석템플릿.xlsx",
            filetypes=[("Excel 통합 문서", "*.xlsx")],
        )
        if not save_path:
            return

        active_ds_id = self.current_dataset.id
        self._set_busy(True)
        self.lbl_status.config(text="Excel 데이터 모델 분석 템플릿 생성 중...")
        self.progress_bar.config(value=20)

        cols = self.last_inspection.schema_columns if self.last_inspection else None

        def worker(report: Callable[[int, str], None]) -> tuple[bool, str]:
            report(50, "Power Query 및 데이터 모델 연결 템플릿 생성 중...")
            return create_excel_template_workbook(save_path, target_csv, self.current_dataset, cols)

        def on_success(res: tuple[bool, str]):
            if not self.current_dataset or self.current_dataset.id != active_ds_id:
                return
            is_native, msg = res
            self.lbl_status.config(text="Excel 분석 템플릿 생성 완료.")
            self.progress_bar.config(value=100)
            messagebox.showinfo("템플릿 생성 완료", msg, parent=self)

        def on_error(err: Exception):
            if not self.current_dataset or self.current_dataset.id != active_ds_id:
                return
            self.lbl_status.config(text="템플릿 생성 실패.")
            messagebox.showerror("템플릿 생성 오류", f"템플릿 생성 중 오류가 발생했습니다:\n{err}", parent=self)

        def on_finished():
            self._set_busy(False)
            self.progress_bar.config(value=0)

        callbacks = JobCallbacks(
            on_progress=lambda pct, msg: (self.progress_bar.config(value=pct), self.lbl_status.config(text=msg)),
            on_success=on_success,
            on_error=on_error,
            on_finished=on_finished,
        )
        self._start_dataset_job("dataset_template", worker, callbacks)

    def _on_copy_m_code(self) -> None:
        if not self.current_dataset:
            return
        Path(self.current_dataset.publish_folder)
        target_csv = self.current_dataset.published_csv_path()

        guide_text = get_powerquery_guide_text(target_csv, self.current_dataset)

        self.clipboard_clear()
        self.clipboard_append(guide_text)
        messagebox.showinfo(
            "클립보드 복사 완료",
            "Microsoft 365 Power Query M 코드와 30초 연결 가이드가 클립보드에 복사되었습니다.\n\n"
            "Excel [고급 편집기]에 붙여넣어 즉시 연결하실 수 있습니다.",
            parent=self,
        )
