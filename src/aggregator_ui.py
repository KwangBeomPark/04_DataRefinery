"""Tkinter UI frame component for the Data Aggregator tab."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from src.data_aggregator import (
    AggregationCancelledError,
    AggregationResult,
    AggregationSpec,
    ColumnGroupRule,
    ColumnNotFoundError,
    DerivedFormulaRule,
    EmptyResultError,
    FilterCondition,
    aggregate_dataset,
    inspect_dataset_schema,
    preview_aggregation,
)
from src.preset_manager import (
    AggregationPreset,
    PresetValidationError,
    delete_preset,
    export_preset_file,
    import_preset_file,
    list_presets,
    load_preset,
    preset_exists,
    save_preset,
    validate_preset_against_columns,
)


class AggregatorTabFrame(ttk.Frame):
    """Encapsulates the entire Data Aggregator tab UI and background execution."""

    def __init__(self, parent: tk.Widget, app: Any, **kwargs):
        super().__init__(parent, style="App.TFrame", **kwargs)
        self.app = app
        self._schema = None
        self._cancel_event: Optional[threading.Event] = None
        self._is_aggregating = False

        # State variables
        self.filepath_var = tk.StringVar()
        self.preset_name_var = tk.StringVar()
        self.rollup_annual_var = tk.BooleanVar(value=True)
        self.output_format_var = tk.StringVar(value="Excel (.xlsx)")

        # In-memory configuration lists
        self.selected_group_keys: List[str] = []
        self.selected_measures: List[str] = []
        self.filters: List[FilterCondition] = []
        self.column_groups: List[ColumnGroupRule] = []
        self.derived_formulas: List[DerivedFormulaRule] = []

        self._build_ui()
        self.refresh_presets_dropdown()

    def _ui(self, key: str) -> str:
        return self.app._ui(key)

    def _build_ui(self):
        self.columnconfigure(0, weight=1)

        # -------------------------------------------------------------
        # 1. Top Section: File & Preset Selection Card
        # -------------------------------------------------------------
        top_card = ttk.LabelFrame(self, style="Card.TLabelframe", padding=(14, 10))
        top_card.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top_card.columnconfigure(1, weight=1)

        # File selection
        ttk.Label(top_card, text="원본 파일:", style="Field.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.ent_file = ttk.Entry(top_card, textvariable=self.filepath_var, state="readonly")
        self.ent_file.grid(row=0, column=1, sticky="ew")

        btn_browse = ttk.Button(top_card, text="파일 찾기...", command=self.browse_file, style="Secondary.TButton")
        btn_browse.grid(row=0, column=2, padx=(8, 0))

        # Presets toolbar
        preset_frame = ttk.Frame(top_card, style="App.TFrame")
        preset_frame.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        preset_frame.columnconfigure(1, weight=1)

        ttk.Label(preset_frame, text="프리셋:", style="Field.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.combo_presets = ttk.Combobox(preset_frame, textvariable=self.preset_name_var, state="readonly")
        self.combo_presets.grid(row=0, column=1, sticky="ew")
        self.combo_presets.bind("<<ComboboxSelected>>", self._on_preset_selected)

        btn_load_preset = ttk.Button(preset_frame, text="불러오기", command=self.apply_selected_preset, style="Secondary.TButton")
        btn_load_preset.grid(row=0, column=2, padx=(6, 2))

        btn_save_preset = ttk.Button(preset_frame, text="저장", command=self.save_current_as_preset, style="Secondary.TButton")
        btn_save_preset.grid(row=0, column=3, padx=2)

        btn_export_preset = ttk.Button(preset_frame, text="내보내기", command=self.export_preset, style="Secondary.TButton")
        btn_export_preset.grid(row=0, column=4, padx=2)

        btn_import_preset = ttk.Button(preset_frame, text="가져오기", command=self.import_preset, style="Secondary.TButton")
        btn_import_preset.grid(row=0, column=5, padx=2)

        btn_del_preset = ttk.Button(preset_frame, text="삭제", command=self.delete_current_preset, style="Secondary.TButton")
        btn_del_preset.grid(row=0, column=6, padx=(2, 0))

        # -------------------------------------------------------------
        # 2. Main 2-Pane Content: Columns on Left, Builder on Right
        # -------------------------------------------------------------
        pane_frame = ttk.Frame(self, style="App.TFrame")
        pane_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        self.rowconfigure(1, weight=1)
        pane_frame.columnconfigure(0, weight=4)  # Left: columns
        pane_frame.columnconfigure(1, weight=6)  # Right: config
        pane_frame.rowconfigure(0, weight=1)

        # Left Pane: Discovered Columns
        left_card = ttk.LabelFrame(pane_frame, style="Card.TLabelframe", text=" 원본 컬럼 (더블클릭하여 추가) ", padding=(10, 8))
        left_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left_card.columnconfigure(0, weight=1)
        left_card.rowconfigure(1, weight=1)
        left_card.rowconfigure(3, weight=1)

        ttk.Label(left_card, text="📁 차원/키 컬럼 (더블클릭 ➔ 행 그룹)", style="Field.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 2))
        self.lb_dimensions = tk.Listbox(left_card, height=6, exportselection=False)
        self.lb_dimensions.grid(row=1, column=0, sticky="nsew")
        self.lb_dimensions.bind("<Double-Button-1>", lambda e: self._add_dimension_to_group())

        dim_btn_frame = ttk.Frame(left_card, style="App.TFrame")
        dim_btn_frame.grid(row=2, column=0, sticky="ew", pady=(2, 6))
        ttk.Button(dim_btn_frame, text="➔ 행 그룹에 추가", command=self._add_dimension_to_group, style="Secondary.TButton").pack(side="left", padx=2)
        ttk.Button(dim_btn_frame, text="↔ 수치로 전환", command=self._switch_dim_to_measure, style="Secondary.TButton").pack(side="right", padx=2)

        ttk.Label(left_card, text="📊 수치/값 컬럼 (더블클릭 ➔ 합산값)", style="Field.TLabel").grid(row=3, column=0, sticky="w", pady=(4, 2))
        self.lb_measures = tk.Listbox(left_card, height=6, exportselection=False)
        self.lb_measures.grid(row=4, column=0, sticky="nsew")
        self.lb_measures.bind("<Double-Button-1>", lambda e: self._add_measure_to_sums())

        meas_btn_frame = ttk.Frame(left_card, style="App.TFrame")
        meas_btn_frame.grid(row=5, column=0, sticky="ew", pady=(2, 0))
        ttk.Button(meas_btn_frame, text="➔ 합산값에 추가", command=self._add_measure_to_sums, style="Secondary.TButton").pack(side="left", padx=2)
        ttk.Button(meas_btn_frame, text="↔ 차원으로 전환", command=self._switch_measure_to_dim, style="Secondary.TButton").pack(side="right", padx=2)

        # Right Pane: Aggregation Settings & Rules
        right_card = ttk.LabelFrame(pane_frame, style="Card.TLabelframe", text=" 집계 및 계산 규칙 설정 ", padding=(10, 8))
        right_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right_card.columnconfigure(0, weight=1)

        # Right Section 1: Group By & Annual Rollup
        grp_box = ttk.LabelFrame(right_card, text="1. 행 그룹 (Group By Keys)", padding=(8, 6))
        grp_box.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        grp_box.columnconfigure(0, weight=1)

        self.chk_rollup = ttk.Checkbutton(grp_box, text="연간 통합 (월 YYYYMM ➔ 연도 YYYY 롤업 합산)", variable=self.rollup_annual_var)
        self.chk_rollup.grid(row=0, column=0, sticky="w", pady=(0, 4))

        self.lb_group_keys = tk.Listbox(grp_box, height=3, exportselection=False)
        self.lb_group_keys.grid(row=1, column=0, sticky="ew")
        ttk.Button(grp_box, text="선택 항목 삭제", command=self._remove_selected_group_key, style="Secondary.TButton").grid(row=2, column=0, sticky="e", pady=(2, 0))

        # Right Section 2: Measure Sums
        meas_box = ttk.LabelFrame(right_card, text="2. 기본 합산 항목 (Measure Sums)", padding=(8, 6))
        meas_box.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        meas_box.columnconfigure(0, weight=1)

        self.lb_selected_measures = tk.Listbox(meas_box, height=3, exportselection=False)
        self.lb_selected_measures.grid(row=0, column=0, sticky="ew")
        ttk.Button(meas_box, text="선택 항목 삭제", command=self._remove_selected_measure, style="Secondary.TButton").grid(row=1, column=0, sticky="e", pady=(2, 0))

        # Right Section 3: Column Groups & Formulas
        calc_box = ttk.LabelFrame(right_card, text="3. 컬럼 묶기 & 비율 수식", padding=(8, 6))
        calc_box.grid(row=2, column=0, sticky="ew", pady=(0, 4))
        calc_box.columnconfigure(0, weight=1)

        self.lb_custom_rules = tk.Listbox(calc_box, height=3, exportselection=False)
        self.lb_custom_rules.grid(row=0, column=0, sticky="ew")

        rule_btn_frame = ttk.Frame(calc_box, style="App.TFrame")
        rule_btn_frame.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        ttk.Button(rule_btn_frame, text="+ 컬럼 묶기(합산)", command=self._popup_add_column_group, style="Secondary.TButton").pack(side="left", padx=2)
        ttk.Button(rule_btn_frame, text="+ 비율식(이익율 등)", command=self._popup_add_formula, style="Secondary.TButton").pack(side="left", padx=2)
        ttk.Button(rule_btn_frame, text="규칙 삭제", command=self._remove_selected_custom_rule, style="Secondary.TButton").pack(side="right", padx=2)

        # -------------------------------------------------------------
        # 3. Bottom Action Bar: Output format, Progress, Action buttons
        # -------------------------------------------------------------
        bottom_bar = ttk.Frame(self, style="App.TFrame")
        bottom_bar.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        bottom_bar.columnconfigure(0, weight=1)

        fmt_frame = ttk.Frame(bottom_bar, style="App.TFrame")
        fmt_frame.grid(row=0, column=0, sticky="w")
        ttk.Label(fmt_frame, text="출력 형식:", style="Field.TLabel").pack(side="left", padx=(0, 6))
        self.combo_out_fmt = ttk.Combobox(
            fmt_frame,
            textvariable=self.output_format_var,
            values=["Excel (.xlsx)", "CSV (.csv)"],
            state="readonly",
            width=14,
        )
        self.combo_out_fmt.pack(side="left")

        btn_frame = ttk.Frame(bottom_bar, style="App.TFrame")
        btn_frame.grid(row=0, column=1, sticky="e")

        self.btn_preview = ttk.Button(btn_frame, text="🔍 미리보기 (샘플)", command=self.run_preview, style="Secondary.TButton")
        self.btn_preview.pack(side="left", padx=(0, 6))

        self.btn_cancel = ttk.Button(btn_frame, text="취소", command=self.cancel_aggregation, style="Secondary.TButton", state="disabled")
        self.btn_cancel.pack(side="left", padx=(0, 8))

        self.btn_run = ttk.Button(btn_frame, text="★ 데이터 집계 및 저장하기", command=self.run_aggregation, style="Primary.TButton")
        self.btn_run.pack(side="left")

    # -------------------------------------------------------------
    # Event Handlers & Helper Methods
    # -------------------------------------------------------------
    def browse_file(self):
        fn = filedialog.askopenfilename(
            title="대용량 데이터 파일 선택 (CSV)",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
        )
        if not fn:
            return

        self.filepath_var.set(fn)
        try:
            self.app.set_progress(0, "파일 컬럼 구조를 분석하는 중...")
            schema = inspect_dataset_schema(fn)
            self._schema = schema
            self._populate_discovered_columns(schema)
            self.app.set_progress(100, f"컬럼 감지 완료 ({len(schema.columns)}개 열)")
            self.app.set_status_log(f"파일 감지: {os.path.basename(fn)} (총 {len(schema.columns)}개 컬럼)")
        except Exception as e:
            messagebox.showerror("파일 분석 오류", f"파일 스키마를 읽을 수 없습니다:\n{e}")

    def _populate_discovered_columns(self, schema):
        self.lb_dimensions.delete(0, tk.END)
        for d in schema.dimension_candidates:
            tag = " [월]" if d == schema.detected_month_column else ""
            self.lb_dimensions.insert(tk.END, f"{d}{tag}")

        self.lb_measures.delete(0, tk.END)
        for m in schema.measure_candidates:
            self.lb_measures.insert(tk.END, m)

        # Default auto-select: if month detected, keep rollup enabled
        if schema.detected_month_column:
            self.rollup_annual_var.set(True)

    def _clean_col_name(self, text: str) -> str:
        return text.replace(" [월]", "").strip()

    def _add_dimension_to_group(self):
        sel = self.lb_dimensions.curselection()
        if not sel:
            return
        col = self._clean_col_name(self.lb_dimensions.get(sel[0]))
        if col not in self.selected_group_keys:
            self.selected_group_keys.append(col)
            self.lb_group_keys.insert(tk.END, col)

    def _remove_selected_group_key(self):
        sel = self.lb_group_keys.curselection()
        if not sel:
            return
        idx = sel[0]
        val = self.lb_group_keys.get(idx)
        self.selected_group_keys.remove(val)
        self.lb_group_keys.delete(idx)

    def _add_measure_to_sums(self):
        sel = self.lb_measures.curselection()
        if not sel:
            return
        col = self._clean_col_name(self.lb_measures.get(sel[0]))
        if col not in self.selected_measures:
            self.selected_measures.append(col)
            self.lb_selected_measures.insert(tk.END, col)

    def _remove_selected_measure(self):
        sel = self.lb_selected_measures.curselection()
        if not sel:
            return
        idx = sel[0]
        val = self.lb_selected_measures.get(idx)
        self.selected_measures.remove(val)
        self.lb_selected_measures.delete(idx)

    def _switch_dim_to_measure(self):
        sel = self.lb_dimensions.curselection()
        if not sel:
            return
        val = self._clean_col_name(self.lb_dimensions.get(sel[0]))
        self.lb_dimensions.delete(sel[0])
        self.lb_measures.insert(tk.END, val)

    def _switch_measure_to_dim(self):
        sel = self.lb_measures.curselection()
        if not sel:
            return
        val = self._clean_col_name(self.lb_measures.get(sel[0]))
        self.lb_measures.delete(sel[0])
        self.lb_dimensions.insert(tk.END, val)

    def _popup_add_column_group(self):
        if not self._schema:
            messagebox.showinfo("알림", "먼저 원본 파일을 선택해 주세요.")
            return

        top = tk.Toplevel(self)
        top.title("컬럼 묶기(합산) 추가")
        top.geometry("380x320")
        top.transient(self)
        top.grab_set()

        ttk.Label(top, text="새 묶음 컬럼 이름:", style="Field.TLabel").pack(anchor="w", padx=12, pady=(12, 4))
        name_var = tk.StringVar(value="새_묶음_컬럼")
        ttk.Entry(top, textvariable=name_var).pack(fill="x", padx=12)

        ttk.Label(top, text="합산할 원본 수치 컬럼들 (Ctrl 누르고 다중 선택):", style="Field.TLabel").pack(anchor="w", padx=12, pady=(8, 4))
        lb = tk.Listbox(top, selectmode="multiple", height=7)
        lb.pack(fill="both", expand=True, padx=12)

        for col in self._schema.measure_candidates:
            lb.insert(tk.END, col)

        def on_ok():
            name = name_var.get().strip()
            selections = [lb.get(i) for i in lb.curselection()]
            if not name or not selections:
                messagebox.showwarning("입력 확인", "컬럼 이름과 최소 하나 이상의 합산 컬럼을 선택해 주세요.")
                return
            rule = ColumnGroupRule(new_column=name, source_columns=selections)
            self.column_groups.append(rule)
            self.lb_custom_rules.insert(tk.END, f"[묶음] {name} = {' + '.join(selections)}")
            top.destroy()

        btn_box = ttk.Frame(top, style="App.TFrame")
        btn_box.pack(fill="x", padx=12, pady=10)
        ttk.Button(btn_box, text="추가", command=on_ok, style="Primary.TButton").pack(side="right", padx=4)
        ttk.Button(btn_box, text="취소", command=top.destroy, style="Secondary.TButton").pack(side="right")

    def _popup_add_formula(self):
        if not self._schema:
            messagebox.showinfo("알림", "먼저 원본 파일을 선택해 주세요.")
            return

        all_candidates = list(self._schema.measure_candidates)
        # Add column group names as well
        for cg in self.column_groups:
            if cg.new_column not in all_candidates:
                all_candidates.append(cg.new_column)

        top = tk.Toplevel(self)
        top.title("비율/파생 수식 추가")
        top.geometry("380x280")
        top.transient(self)
        top.grab_set()

        ttk.Label(top, text="새 수식 컬럼 이름:", style="Field.TLabel").pack(anchor="w", padx=12, pady=(12, 4))
        name_var = tk.StringVar(value="이익율(%)")
        ttk.Entry(top, textvariable=name_var).pack(fill="x", padx=12)

        grid_f = ttk.Frame(top, style="App.TFrame")
        grid_f.pack(fill="x", padx=12, pady=8)
        grid_f.columnconfigure(1, weight=1)

        ttk.Label(grid_f, text="분자 (Numerator):").grid(row=0, column=0, sticky="w", pady=4)
        num_var = tk.StringVar()
        combo_num = ttk.Combobox(grid_f, textvariable=num_var, values=all_candidates, state="readonly")
        combo_num.grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(grid_f, text="÷ 분모 (Denominator):").grid(row=1, column=0, sticky="w", pady=4)
        den_var = tk.StringVar()
        combo_den = ttk.Combobox(grid_f, textvariable=den_var, values=all_candidates, state="readonly")
        combo_den.grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(grid_f, text="× 승수 (Multiplier):").grid(row=2, column=0, sticky="w", pady=4)
        mult_var = tk.StringVar(value="100")
        ttk.Entry(grid_f, textvariable=mult_var).grid(row=2, column=1, sticky="ew", pady=4)

        def on_ok():
            name = name_var.get().strip()
            num = num_var.get()
            den = den_var.get()
            try:
                mult = float(mult_var.get())
            except ValueError:
                mult = 100.0

            if not name or not num or not den:
                messagebox.showwarning("입력 확인", "이름, 분자, 분모를 모두 지정해 주세요.")
                return

            rule = DerivedFormulaRule(new_column=name, numerator_column=num, denominator_column=den, multiplier=mult)
            self.derived_formulas.append(rule)
            self.lb_custom_rules.insert(tk.END, f"[비율] {name} = ({num} ÷ {den}) × {mult}")
            top.destroy()

        btn_box = ttk.Frame(top, style="App.TFrame")
        btn_box.pack(fill="x", padx=12, pady=10)
        ttk.Button(btn_box, text="추가", command=on_ok, style="Primary.TButton").pack(side="right", padx=4)
        ttk.Button(btn_box, text="취소", command=top.destroy, style="Secondary.TButton").pack(side="right")

    def _remove_selected_custom_rule(self):
        sel = self.lb_custom_rules.curselection()
        if not sel:
            return
        idx = sel[0]
        text = self.lb_custom_rules.get(idx)
        self.lb_custom_rules.delete(idx)

        if text.startswith("[묶음]"):
            col_name = text.split(" = ")[0].replace("[묶음] ", "").strip()
            self.column_groups = [cg for cg in self.column_groups if cg.new_column != col_name]
        elif text.startswith("[비율]"):
            col_name = text.split(" = ")[0].replace("[비율] ", "").strip()
            self.derived_formulas = [df for df in self.derived_formulas if df.new_column != col_name]

    # -------------------------------------------------------------
    # Presets Management UI Actions
    # -------------------------------------------------------------
    def refresh_presets_dropdown(self):
        presets = list_presets()
        names = [p.name for p in presets]
        self.combo_presets["values"] = names
        if names and not self.preset_name_var.get():
            self.preset_name_var.set(names[0])

    def _on_preset_selected(self, event=None):
        pass

    def apply_selected_preset(self):
        name = self.preset_name_var.get().strip()
        if not name:
            messagebox.showinfo("알림", "불러올 프리셋을 선택해 주세요.")
            return

        try:
            preset = load_preset(name)
        except Exception as e:
            messagebox.showerror("프리셋 오류", f"프리셋을 불러오지 못했습니다:\n{e}")
            return

        # Validate against schema if file is loaded
        if self._schema:
            missing = validate_preset_against_columns(preset, self._schema.columns)
            if missing:
                proceed = messagebox.askyesno(
                    "컬럼 불일치 경고",
                    f"선택한 프리셋의 컬럼 중 현재 파일에 없는 컬럼이 있습니다:\n{', '.join(missing)}\n\n계속 적용하시겠습니까?",
                )
                if not proceed:
                    return

        # Apply to UI
        self.rollup_annual_var.set(preset.rollup_annual)
        self.output_format_var.set("Excel (.xlsx)" if preset.output_format.lower() == "xlsx" else "CSV (.csv)")

        self.selected_group_keys = list(preset.group_by_keys)
        self.lb_group_keys.delete(0, tk.END)
        for k in self.selected_group_keys:
            self.lb_group_keys.insert(tk.END, k)

        self.selected_measures = list(preset.measure_sums)
        self.lb_selected_measures.delete(0, tk.END)
        for m in self.selected_measures:
            self.lb_selected_measures.insert(tk.END, m)

        self.column_groups = list(preset.column_groups)
        self.derived_formulas = list(preset.derived_formulas)
        self.lb_custom_rules.delete(0, tk.END)

        for cg in self.column_groups:
            self.lb_custom_rules.insert(tk.END, f"[묶음] {cg.new_column} = {' + '.join(cg.source_columns)}")
        for df in self.derived_formulas:
            self.lb_custom_rules.insert(tk.END, f"[비율] {df.new_column} = ({df.numerator_column} ÷ {df.denominator_column}) × {df.multiplier}")

        self.app.set_status_log(f"프리셋 '{name}' 적용 완료")
        if preset.description:
            self.app.set_result_text(f"프리셋: {name}\n설명: {preset.description}")

    def save_current_as_preset(self):
        name = simpledialog.askstring("프리셋 저장", "저장할 프리셋의 이름을 입력하세요:", initialvalue=self.preset_name_var.get())
        if not name:
            return
        name = name.strip()
        if not name:
            return

        if preset_exists(name):
            overwrite = messagebox.askyesno(
                "프리셋 덮어쓰기 확인",
                f"이미 '{name}' 이름의 프리셋이 존재합니다.\n기존 설정을 덮어쓰시겠습니까?",
            )
            if not overwrite:
                return

        desc = simpledialog.askstring("프리셋 메모", "설명 또는 메모를 입력하세요 (선택 사항):", initialvalue="") or ""

        month_col = self._schema.detected_month_column if self._schema else "월"

        preset = AggregationPreset(
            name=name,
            description=desc.strip(),
            group_by_keys=list(self.selected_group_keys),
            measure_sums=list(self.selected_measures),
            column_groups=list(self.column_groups),
            derived_formulas=list(self.derived_formulas),
            filters=list(self.filters),
            rollup_annual=self.rollup_annual_var.get(),
            month_column=month_col,
            output_format="xlsx" if "excel" in self.output_format_var.get().lower() else "csv",
        )

        save_preset(preset)
        self.refresh_presets_dropdown()
        self.preset_name_var.set(preset.name)
        messagebox.showinfo("저장 완료", f"프리셋 '{preset.name}'이(가) 저장되었습니다.")

    def export_preset(self):
        name = self.preset_name_var.get().strip()
        if not name:
            messagebox.showinfo("알림", "내보낼 프리셋을 선택해 주세요.")
            return

        dest = filedialog.asksaveasfilename(
            title="프리셋 내보내기",
            defaultextension=".json",
            initialfile=f"{name}.json",
            filetypes=(("JSON preset file", "*.json"),),
        )
        if not dest:
            return

        try:
            preset = load_preset(name)
            export_preset_file(preset, dest)
            messagebox.showinfo("내보내기 완료", f"프리셋을 성공적으로 내보냈습니다:\n{dest}")
        except Exception as e:
            messagebox.showerror("내보내기 오류", str(e))

    def import_preset(self):
        src = filedialog.askopenfilename(
            title="공유된 프리셋 파일 가져오기",
            filetypes=(("JSON preset file", "*.json"), ("All files", "*.*")),
        )
        if not src:
            return

        try:
            imported = import_preset_file(src, save_to_local=True)
            self.refresh_presets_dropdown()
            self.preset_name_var.set(imported.name)
            messagebox.showinfo("가져오기 완료", f"프리셋 '{imported.name}'을(를) 성공적으로 가져왔습니다.")
            self.apply_selected_preset()
        except Exception as e:
            messagebox.showerror("가져오기 오류", f"프리셋 파일을 가져오지 못했습니다:\n{e}")

    def delete_current_preset(self):
        name = self.preset_name_var.get().strip()
        if not name:
            return
        if messagebox.askyesno("프리셋 삭제", f"정말로 프리셋 '{name}'을(를) 삭제하시겠습니까?"):
            delete_preset(name)
            self.preset_name_var.set("")
            self.refresh_presets_dropdown()

    # -------------------------------------------------------------
    # Execution & Cancellation
    # -------------------------------------------------------------
    def cancel_aggregation(self):
        if self._cancel_event and not self._cancel_event.is_set():
            self._cancel_event.set()
            self.btn_cancel.configure(state="disabled")
            self.app.set_status_log("집계 취소 요청됨... 정리 중")

    def _build_spec(self) -> Optional[AggregationSpec]:
        fp = self.filepath_var.get().strip()
        if not fp or not os.path.exists(fp):
            messagebox.showerror("파일 선택 필요", "먼저 처리할 원본 CSV 파일을 선택해 주세요.")
            return None

        if not self.selected_group_keys:
            messagebox.showwarning("설정 확인", "최소 하나 이상의 행 그룹(Group By 키)을 선택해 주세요.")
            return None

        if not self.selected_measures and not self.column_groups and not self.derived_formulas:
            messagebox.showwarning("설정 확인", "합산할 수치 컬럼 또는 계산 규칙을 최소 하나 이상 지정해 주세요.")
            return None

        num_mode = getattr(self.app, "_number_mode", lambda _: "English")(getattr(self.app, "num_format", tk.StringVar()).get())
        month_col = self._schema.detected_month_column if self._schema else "월"
        out_fmt = "xlsx" if "excel" in self.output_format_var.get().lower() else "csv"
        delim = self._schema.delimiter if self._schema else None
        enc = self._schema.encoding if self._schema else None

        return AggregationSpec(
            file_path=fp,
            group_by_keys=list(self.selected_group_keys),
            measure_sums=list(self.selected_measures),
            column_groups=list(self.column_groups),
            derived_formulas=list(self.derived_formulas),
            filters=list(self.filters),
            rollup_annual=self.rollup_annual_var.get(),
            month_column=month_col,
            delimiter=delim,
            encoding=enc,
            number_mode=num_mode,
            output_format=out_fmt,
        )

    def run_preview(self):
        spec = self._build_spec()
        if not spec:
            return

        try:
            self.app.set_status_log("샘플 2,000행 대상 간이 집계 미리보기 생성 중...")
            preview_df, preview_text = preview_aggregation(spec, sample_rows=2000)
            msg = (
                f"=== [미리보기] 상위 샘플 2,000행 집계 결과 (최대 15행 표시) ===\n\n"
                f"{preview_text}\n\n"
                f"※ 위 결과는 상위 2,000개 행을 대상으로 한 즉시 집계 샘플이며, 전체 데이터 집계 시에는 모든 행이 처리됩니다."
            )
            self.app.set_result_text(msg)
            self.app.set_status_log("미리보기 표시 완료")
        except Exception as e:
            messagebox.showerror("미리보기 오류", f"미리보기를 생성하지 못했습니다:\n{e}")

    def run_aggregation(self):
        if getattr(self.app, "_csv_processing", False) or getattr(self.app, "_promotion_processing", False):
            messagebox.showinfo("작업 중", "현재 다른 작업이 실행 중입니다. 완료 후 다시 시도해 주세요.")
            return

        spec = self._build_spec()
        if not spec:
            return

        self._cancel_event = threading.Event()
        self._is_aggregating = True
        self.btn_run.configure(state="disabled")
        self.btn_preview.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        if hasattr(self.app, "_refresh_csv_action_state"):
            self.app._refresh_csv_action_state()
        if hasattr(self.app, "_refresh_promotion_action_state"):
            self.app._refresh_promotion_action_state()
        self.app.set_progress(0, "집계 준비 중...")

        def worker(report_progress):
            def progress_bridge(cur, total, msg):
                pct = int((cur / max(1, total)) * 100)
                report_progress(pct, msg)

            return aggregate_dataset(spec, progress_callback=progress_bridge, cancel_event=self._cancel_event)

        def on_success(result: AggregationResult):
            out_path = result.out_path
            self.app.set_progress(100, "집계 완료!")
            self.app.set_status_log(f"저장 완료: {os.path.basename(out_path)}")

            warning_msg = ""
            if result.coerced_numbers_count > 0:
                warning_msg = f"\n\n⚠️ 주의: 비정상 또는 결측 수치 데이터 {result.coerced_numbers_count:,}건이 0으로 자동 치환되었습니다."

            summary = (
                f"데이터 집계 완료!\n\n"
                f"• 저장 파일: {os.path.basename(out_path)}\n"
                f"• 저장 경로: {os.path.dirname(out_path)}\n"
                f"• 최종 집계 행 수: {result.final_rows:,}행\n"
                f"• 집계 기준: {', '.join(spec.group_by_keys)}\n"
                f"• 연간 롤업: {'적용됨' if spec.rollup_annual else '미적용'}\n"
                f"• 계산 항목: {len(spec.measure_sums) + len(spec.column_groups) + len(spec.derived_formulas)}개 컬럼"
                f"{warning_msg}\n\n"
                f"=== 상위 10행 미리보기 ===\n"
                f"{result.sample_preview_text}"
            )
            self.app.set_result_text(summary)

            info_box_msg = f"데이터 집계가 성공적으로 완료되었습니다.\n\n저장 위치:\n{out_path}"
            if result.coerced_numbers_count > 0:
                info_box_msg += f"\n\n(비정상/결측 수치 {result.coerced_numbers_count:,}건이 0으로 보정되었습니다.)"
            messagebox.showinfo("집계 완료", info_box_msg)

        def on_error(err):
            if isinstance(err, AggregationCancelledError):
                self.app.set_progress(0, "작업 취소됨")
                self.app.set_status_log("사용자에 의해 집계가 취소되었습니다.")
                self.app.set_result_text("집계 작업이 취소되었습니다.")
            else:
                self.app.set_progress(0, "오류 발생")
                self.app.set_status_log("집계 오류 발생")
                messagebox.showerror("집계 오류", f"집계 중 오류가 발생했습니다:\n{err}")

        def on_finished():
            self._is_aggregating = False
            self.btn_run.configure(state="normal")
            self.btn_preview.configure(state="normal")
            self.btn_cancel.configure(state="disabled")
            if hasattr(self.app, "_refresh_csv_action_state"):
                self.app._refresh_csv_action_state()
            if hasattr(self.app, "_refresh_promotion_action_state"):
                self.app._refresh_promotion_action_state()

        from src.background_jobs import JobCallbacks
        started = self.app.job_runner.start(
            "dataset-aggregation",
            worker,
            JobCallbacks(
                on_progress=lambda pct, msg: self.app.set_progress(pct, msg),
                on_success=on_success,
                on_error=on_error,
                on_finished=on_finished,
            ),
        )
        if not started:
            on_finished()
