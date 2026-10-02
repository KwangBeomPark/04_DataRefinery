"""Tkinter UI frame component for the Rule-Based Data Mapper tab."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional, Tuple

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
    OP_LESS,
    OP_LESS_EQUAL,
    OP_NOT_CONTAINS,
    OP_NOT_EQUALS,
    OP_NOT_IN,
    OP_REGEX,
    OP_STARTSWITH,
    MapperCancelledError,
    MappingResult,
    MappingRule,
    MappingSpec,
    RuleCondition,
    execute_mapping,
    load_ruleset_file,
    preview_mapping,
    save_ruleset_file,
    validate_rules_against_columns,
)
from src.background_jobs import JobCallbacks
from src.csv_processing import detect_delimiter, detect_encoding
from src.file_reveal import open_containing_folder
from src.ui_components import PALETTE

_OP_LABELS = {
    OP_EQUALS: "= 일치함",
    OP_NOT_EQUALS: "≠ 일치하지 않음",
    OP_CONTAINS: "∋ 포함함",
    OP_NOT_CONTAINS: "∌ 포함하지 않음",
    OP_STARTSWITH: "^ 시작함",
    OP_ENDSWITH: "$ 끝남",
    OP_IN: "∈ 목록에 있음 (쉼표 구분)",
    OP_NOT_IN: "∉ 목록에 없음",
    OP_GREATER: "> 초과 (숫자)",
    OP_GREATER_EQUAL: "≥ 이상 (숫자)",
    OP_LESS: "< 미만 (숫자)",
    OP_LESS_EQUAL: "≤ 이하 (숫자)",
    OP_IS_NULL: "∅ 빈값 / 공백",
    OP_IS_NOT_NULL: "● 값 있음",
    OP_REGEX: ".* 정규식",
}

_LABEL_TO_OP = {v: k for k, v in _OP_LABELS.items()}


class RuleEditDialog(tk.Toplevel):
    """Modal dialog for creating or editing a single MappingRule with multiple conditions."""

    def __init__(
        self,
        parent: tk.Widget,
        available_columns: List[str],
        rule: Optional[MappingRule] = None,
        existing_targets: Optional[List[str]] = None,
    ):
        super().__init__(parent)
        self.transient(parent)
        self.grab_set()
        self.title("매핑 규칙 편집" if rule else "새 매핑 규칙 추가")
        self.geometry("640x520")
        self.minsize(580, 440)
        self.configure(background=PALETTE["surface"])

        self.available_columns = available_columns
        self.existing_targets = existing_targets or []
        self.result_rule: Optional[MappingRule] = None

        self._rule_id = rule.rule_id if rule else ""
        self._init_ui(rule)
        self.wait_window(self)

    def _init_ui(self, rule: Optional[MappingRule]):
        container = ttk.Frame(self, style="App.TFrame", padding=(16, 14))
        container.pack(fill="both", expand=True)

        # Header section: Name & Target Value
        header_frame = ttk.LabelFrame(container, text="1. 기본 정보", style="Card.TLabelframe", padding=(12, 10))
        header_frame.pack(fill="x", pady=(0, 10))
        header_frame.columnconfigure(1, weight=1)

        ttk.Label(header_frame, text="규칙 이름:", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        self.name_var = tk.StringVar(value=rule.name if rule else "새 규칙")
        ttk.Entry(header_frame, textvariable=self.name_var).grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=4)

        ttk.Label(header_frame, text="부여할 카테고리 값:", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self.target_var = tk.StringVar(value=rule.target_value if rule else "")
        self.target_combo = ttk.Combobox(header_frame, textvariable=self.target_var, values=self.existing_targets)
        self.target_combo.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=4)

        # Combine operator (AND / OR)
        combine_frame = ttk.Frame(header_frame, style="App.TFrame")
        combine_frame.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(combine_frame, text="조건 결합 방식:", style="Card.TLabel").pack(side="left")
        self.combine_var = tk.StringVar(value=rule.combine_operator if rule else "AND")
        ttk.Radiobutton(combine_frame, text="모든 조건 만족 (AND)", variable=self.combine_var, value="AND").pack(side="left", padx=(10, 8))
        ttk.Radiobutton(combine_frame, text="하나라도 만족 (OR)", variable=self.combine_var, value="OR").pack(side="left")

        # Conditions section
        cond_frame = ttk.LabelFrame(container, text="2. 조건 설정 (복합 조건)", style="Card.TLabelframe", padding=(12, 10))
        cond_frame.pack(fill="both", expand=True, pady=(0, 10))

        # Condition rows container with scrollbar
        self.cond_canvas = tk.Canvas(cond_frame, background=PALETTE["surface"], highlightthickness=0)
        cond_scroll = ttk.Scrollbar(cond_frame, orient="vertical", command=self.cond_canvas.yview)
        self.cond_inner = ttk.Frame(self.cond_canvas, style="App.TFrame")

        self.cond_inner.bind("<Configure>", lambda e: self.cond_canvas.configure(scrollregion=self.cond_canvas.bbox("all")))
        self.cond_canvas.create_window((0, 0), window=self.cond_inner, anchor="nw")
        self.cond_canvas.configure(yscrollcommand=cond_scroll.set)

        self.cond_canvas.pack(side="left", fill="both", expand=True)
        cond_scroll.pack(side="right", fill="y")

        self.condition_rows: List[Dict[str, Any]] = []

        # Add initial conditions
        if rule and rule.conditions:
            for cond in rule.conditions:
                self._add_condition_row(cond)
        else:
            self._add_condition_row()

        # Add condition button
        btn_add_cond = ttk.Button(cond_frame, text="+ 조건 추가", command=self._add_condition_row, style="Secondary.TButton")
        btn_add_cond.pack(anchor="w", pady=(8, 0))

        # Bottom buttons
        btn_bar = ttk.Frame(container, style="App.TFrame")
        btn_bar.pack(fill="x")
        ttk.Button(btn_bar, text="확인", command=self._on_save, style="Primary.TButton").pack(side="right", padx=(8, 0))
        ttk.Button(btn_bar, text="취소", command=self.destroy, style="Secondary.TButton").pack(side="right")

    def _add_condition_row(self, cond: Optional[RuleCondition] = None):
        row_frame = ttk.Frame(self.cond_inner, style="App.TFrame", padding=(0, 4))
        row_frame.pack(fill="x", expand=True)

        col_var = tk.StringVar(value=cond.column if cond and cond.column in self.available_columns else (self.available_columns[0] if self.available_columns else ""))
        op_var = tk.StringVar(value=_OP_LABELS.get(cond.operator, _OP_LABELS[OP_EQUALS]) if cond else _OP_LABELS[OP_EQUALS])
        val_var = tk.StringVar(value=str(cond.value) if cond and cond.value is not None else "")
        case_var = tk.BooleanVar(value=cond.case_sensitive if cond else False)

        ttk.Combobox(row_frame, textvariable=col_var, values=self.available_columns, state="readonly", width=14).pack(side="left", padx=(0, 6))
        ttk.Combobox(row_frame, textvariable=op_var, values=list(_OP_LABELS.values()), state="readonly", width=18).pack(side="left", padx=(0, 6))
        ent_val = ttk.Entry(row_frame, textvariable=val_var, width=16)
        ent_val.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Checkbutton(row_frame, text="대소문자", variable=case_var).pack(side="left", padx=(0, 6))

        btn_del = ttk.Button(row_frame, text="✕", width=3, command=lambda rf=row_frame: self._remove_condition_row(rf), style="Compact.TButton")
        btn_del.pack(side="left")

        self.condition_rows.append({
            "frame": row_frame,
            "col": col_var,
            "op": op_var,
            "val": val_var,
            "case": case_var,
        })

    def _remove_condition_row(self, frame_to_remove: ttk.Frame):
        if len(self.condition_rows) <= 1:
            messagebox.showinfo("안내", "최소 하나의 조건은 유지해야 합니다.", parent=self)
            return
        for idx, row in enumerate(self.condition_rows):
            if row["frame"] == frame_to_remove:
                row["frame"].destroy()
                self.condition_rows.pop(idx)
                break

    def _on_save(self):
        name = self.name_var.get().strip()
        target = self.target_var.get().strip()
        if not name:
            messagebox.showerror("오류", "규칙 이름을 입력해 주세요.", parent=self)
            return
        if not target:
            messagebox.showerror("오류", "부여할 카테고리/라벨 값을 입력해 주세요.", parent=self)
            return

        conds: List[RuleCondition] = []
        for r in self.condition_rows:
            c_name = r["col"].get()
            op_label = r["op"].get()
            op = _LABEL_TO_OP.get(op_label, OP_EQUALS)
            val = r["val"].get().strip()
            case_sens = r["case"].get()

            if op not in (OP_IS_NULL, OP_IS_NOT_NULL) and not val:
                messagebox.showerror("오류", f"'{c_name}' 조건의 비교값을 입력해 주세요.", parent=self)
                return

            conds.append(RuleCondition(column=c_name, operator=op, value=val, case_sensitive=case_sens))

        self.result_rule = MappingRule(
            rule_id=self._rule_id,
            name=name,
            target_value=target,
            conditions=conds,
            combine_operator=self.combine_var.get(),
            enabled=True,
        )
        self.destroy()


class MapperTabFrame(ttk.Frame):
    """Encapsulates the complete Rule-Based Data Mapping tab UI and execution logic."""

    def __init__(self, parent: tk.Widget, app: Any, **kwargs):
        super().__init__(parent, style="App.TFrame", **kwargs)
        self.app = app
        self._rules: List[MappingRule] = []
        self._available_columns: List[str] = []
        self._cancel_event: Optional[threading.Event] = None
        self._is_processing = False
        self._last_result_path: Optional[str] = None
        self._preview_df: Optional[pd.DataFrame] = None
        self._unmapped_summary: Dict[str, List[Tuple[Any, int]]] = {}

        self._init_ui()

    def _init_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # 1. Top Section: File & Target Settings
        top_card = ttk.LabelFrame(self, text="1. 원본 파일 및 분류 대상 설정", style="Card.TLabelframe", padding=(14, 10))
        top_card.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top_card.columnconfigure(1, weight=1)

        ttk.Label(top_card, text="원본 파일:", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.filepath_var = tk.StringVar()
        self.entry_file = ttk.Entry(top_card, textvariable=self.filepath_var, state="readonly")
        self.entry_file.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(top_card, text="파일 찾기…", command=self._browse_file, style="Secondary.TButton").grid(row=0, column=2)

        settings_frame = ttk.Frame(top_card, style="App.TFrame")
        settings_frame.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        ttk.Label(settings_frame, text="분류 결과 컬럼명:", style="Card.TLabel").pack(side="left")
        self.target_col_var = tk.StringVar(value="Category")
        ttk.Entry(settings_frame, textvariable=self.target_col_var, width=18).pack(side="left", padx=(6, 20))

        ttk.Label(settings_frame, text="미매핑 기본값:", style="Card.TLabel").pack(side="left")
        self.default_val_var = tk.StringVar(value="미분류")
        ttk.Entry(settings_frame, textvariable=self.default_val_var, width=14).pack(side="left", padx=(6, 20))

        self.file_info_var = tk.StringVar(value="CSV/Excel 파일을 선택하세요.")
        ttk.Label(settings_frame, textvariable=self.file_info_var, style="Muted.TLabel").pack(side="left")

        # 2. Main Body: Left (Rules List) & Right (Funnel + Live Preview)
        body_paned = ttk.PanedWindow(self, orient="horizontal")
        body_paned.grid(row=1, column=0, sticky="nsew", pady=(0, 8))

        # Left Panel: Waterfall Rule Sequence
        left_frame = ttk.LabelFrame(body_paned, text="2. 매핑 규칙 순서 (우선순위 Waterfall)", style="Card.TLabelframe", padding=(10, 8))
        left_frame.columnconfigure(0, weight=1)
        left_frame.rowconfigure(0, weight=1)
        body_paned.add(left_frame, weight=2)

        # Rule Treeview
        columns = ("order", "name", "target", "cond", "matched")
        self.rule_tree = ttk.Treeview(left_frame, columns=columns, show="headings", selectmode="browse", style="FieldList.Treeview")
        self.rule_tree.heading("order", text="#")
        self.rule_tree.heading("name", text="규칙 이름")
        self.rule_tree.heading("target", text="분류값")
        self.rule_tree.heading("cond", text="조건 요약")
        self.rule_tree.heading("matched", text="매칭 건수")

        self.rule_tree.column("order", width=36, anchor="center")
        self.rule_tree.column("name", width=110, anchor="w")
        self.rule_tree.column("target", width=100, anchor="w")
        self.rule_tree.column("cond", width=160, anchor="w")
        self.rule_tree.column("matched", width=80, anchor="e")

        rule_scroll = ttk.Scrollbar(left_frame, orient="vertical", command=self.rule_tree.yview)
        self.rule_tree.configure(yscrollcommand=rule_scroll.set)
        self.rule_tree.grid(row=0, column=0, sticky="nsew")
        rule_scroll.grid(row=0, column=1, sticky="ns")

        self.rule_tree.bind("<Double-1>", lambda e: self._on_edit_rule())

        # Rule Action Buttons
        rule_btn_frame = ttk.Frame(left_frame, style="App.TFrame")
        rule_btn_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        ttk.Button(rule_btn_frame, text="+ 규칙 추가", command=self._on_add_rule, style="Secondary.TButton").pack(side="left", padx=(0, 4))
        ttk.Button(rule_btn_frame, text="✎ 수정", command=self._on_edit_rule, style="Secondary.TButton").pack(side="left", padx=(0, 4))
        ttk.Button(rule_btn_frame, text="✕ 삭제", command=self._on_delete_rule, style="Secondary.TButton").pack(side="left", padx=(0, 8))
        ttk.Button(rule_btn_frame, text="▲ 위로", command=lambda: self._on_move_rule(-1), style="Compact.TButton").pack(side="left", padx=(0, 2))
        ttk.Button(rule_btn_frame, text="▼ 아래로", command=lambda: self._on_move_rule(1), style="Compact.TButton").pack(side="left", padx=(0, 8))

        ttk.Button(rule_btn_frame, text="💾 규칙 저장", command=self._on_save_ruleset, style="Secondary.TButton").pack(side="right", padx=(4, 0))
        ttk.Button(rule_btn_frame, text="📁 규칙 불러오기", command=self._on_load_ruleset, style="Secondary.TButton").pack(side="right")

        # Right Panel: Funnel Status & Live Sample Preview
        right_frame = ttk.LabelFrame(body_paned, text="3. 실시간 매핑 현황 및 샘플 미리보기", style="Card.TLabelframe", padding=(10, 8))
        right_frame.columnconfigure(0, weight=1)
        right_frame.rowconfigure(1, weight=1)
        body_paned.add(right_frame, weight=3)

        # Funnel summary bar
        funnel_box = ttk.Frame(right_frame, style="App.TFrame", padding=(4, 2))
        funnel_box.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self.funnel_text_var = tk.StringVar(value="전체: 0행 | 분류 완료: 0행 (0.0%) | ⚠️ 미매핑: 0행 (0.0%)")
        ttk.Label(funnel_box, textvariable=self.funnel_text_var, font=("Segoe UI Semibold", 9), foreground=PALETTE["navy"]).pack(side="left")

        ttk.Button(funnel_box, text="⚠️ 미매핑 분석 및 룰 추천", command=self._on_show_unmapped_analysis, style="Secondary.TButton").pack(side="right")

        # Preview Treeview
        self.preview_tree = ttk.Treeview(right_frame, show="headings", style="FieldList.Treeview")
        preview_scroll_y = ttk.Scrollbar(right_frame, orient="vertical", command=self.preview_tree.yview)
        preview_scroll_x = ttk.Scrollbar(right_frame, orient="horizontal", command=self.preview_tree.xview)
        self.preview_tree.configure(yscrollcommand=preview_scroll_y.set, xscrollcommand=preview_scroll_x.set)

        self.preview_tree.grid(row=1, column=0, sticky="nsew")
        preview_scroll_y.grid(row=1, column=1, sticky="ns")
        preview_scroll_x.grid(row=2, column=0, sticky="ew")

        # 3. Bottom Execution Bar
        bottom_bar = ttk.Frame(self, style="App.TFrame")
        bottom_bar.grid(row=2, column=0, sticky="ew")

        ttk.Button(bottom_bar, text="🔍 미리보기 갱신", command=self._refresh_preview, style="Secondary.TButton").pack(side="left", padx=(0, 6))
        self.btn_unmapped_export = ttk.Button(bottom_bar, text="⚠️ 미매핑 데이터만 따로 저장", command=lambda: self._start_execution(unmapped_only=True), style="Secondary.TButton")
        self.btn_unmapped_export.pack(side="left", padx=(0, 12))

        self.btn_save_csv = ttk.Button(bottom_bar, text="★ CSV로 저장", command=lambda: self._start_execution(fmt="csv"), style="Primary.TButton")
        self.btn_save_csv.pack(side="left", padx=(0, 6))
        self.btn_save_excel = ttk.Button(bottom_bar, text="Excel로 저장 (.xlsx)", command=lambda: self._start_execution(fmt="xlsx"), style="Secondary.TButton")
        self.btn_save_excel.pack(side="left", padx=(0, 12))

        self.btn_cancel = ttk.Button(bottom_bar, text="취소", command=self._cancel_job, state="disabled", style="Secondary.TButton")
        self.btn_cancel.pack(side="left")

        self.btn_open_folder = ttk.Button(bottom_bar, text="📂 결과 폴더 열기", command=self._open_output_folder, state="disabled", style="Secondary.TButton")
        self.btn_open_folder.pack(side="right")

    def _browse_file(self):
        f = filedialog.askopenfilename(
            title="데이터 파일 선택",
            filetypes=(("CSV / Excel", "*.csv *.txt *.xlsx *.xlsm"), ("All Files", "*.*")),
        )
        if not f:
            return
        self.filepath_var.set(f)
        try:
            delim = detect_delimiter(f)
            enc = detect_encoding(f)
            # Read first few rows to discover columns
            sample = pd.read_csv(f, sep=delim, encoding=enc, nrows=5, low_memory=False)
            self._available_columns = list(sample.columns)
            self.file_info_var.set(f"컬럼 {len(self._available_columns)}개 감지됨 ({enc})")
            self._refresh_preview()
        except Exception as err:
            messagebox.showerror("파일 오류", f"파일을 읽는 중 오류가 발생했습니다:\n{err}")

    def _refresh_rule_list(self):
        self.rule_tree.delete(*self.rule_tree.get_children())
        for idx, r in enumerate(self._rules, 1):
            cond_desc = " & ".join(
                f"{c.column} {c.operator} '{c.value}'" if c.value is not None else f"{c.column} {c.operator}"
                for c in r.conditions
            )
            self.rule_tree.insert(
                "",
                "end",
                iid=r.rule_id,
                values=(idx, r.name, r.target_value, cond_desc, "-"),
            )

    def _on_add_rule(self):
        if not self._available_columns:
            messagebox.showinfo("안내", "먼저 원본 파일을 선택해 주세요.")
            return
        existing_targets = list({r.target_value for r in self._rules if r.target_value})
        dlg = RuleEditDialog(self, self._available_columns, existing_targets=existing_targets)
        if dlg.result_rule:
            self._rules.append(dlg.result_rule)
            self._refresh_rule_list()
            self._refresh_preview()

    def _on_edit_rule(self):
        sel = self.rule_tree.selection()
        if not sel:
            return
        rule_id = sel[0]
        rule = next((r for r in self._rules if r.rule_id == rule_id), None)
        if not rule:
            return

        existing_targets = list({r.target_value for r in self._rules if r.target_value})
        dlg = RuleEditDialog(self, self._available_columns, rule=rule, existing_targets=existing_targets)
        if dlg.result_rule:
            idx = self._rules.index(rule)
            self._rules[idx] = dlg.result_rule
            self._refresh_rule_list()
            self._refresh_preview()

    def _on_delete_rule(self):
        sel = self.rule_tree.selection()
        if not sel:
            return
        rule_id = sel[0]
        self._rules = [r for r in self._rules if r.rule_id != rule_id]
        self._refresh_rule_list()
        self._refresh_preview()

    def _on_move_rule(self, delta: int):
        sel = self.rule_tree.selection()
        if not sel:
            return
        rule_id = sel[0]
        idx = next((i for i, r in enumerate(self._rules) if r.rule_id == rule_id), -1)
        if idx == -1:
            return
        new_idx = idx + delta
        if 0 <= new_idx < len(self._rules):
            self._rules[idx], self._rules[new_idx] = self._rules[new_idx], self._rules[idx]
            self._refresh_rule_list()
            self.rule_tree.selection_set(rule_id)
            self._refresh_preview()

    def _on_save_ruleset(self):
        if not self._rules:
            messagebox.showinfo("안내", "저장할 매핑 규칙이 없습니다.")
            return
        dest = filedialog.asksaveasfilename(
            title="규칙 파일 저장",
            defaultextension=".json",
            filetypes=(("JSON Ruleset", "*.json"),),
            initialfile="mapping_ruleset.json",
        )
        if not dest:
            return
        try:
            save_ruleset_file(
                dest,
                self._rules,
                target_column=self.target_col_var.get().strip() or "Category",
                default_value=self.default_val_var.get().strip() or "미분류",
            )
            messagebox.showinfo("완료", "매핑 규칙이 성공적으로 저장되었습니다.")
        except Exception as err:
            messagebox.showerror("저장 오류", f"규칙 저장 중 오류가 발생했습니다:\n{err}")

    def _on_load_ruleset(self):
        src = filedialog.askopenfilename(
            title="규칙 파일 불러오기",
            filetypes=(("JSON Ruleset", "*.json"), ("All Files", "*.*")),
        )
        if not src:
            return
        try:
            rules, target_col, def_val = load_ruleset_file(src)
            self._rules = rules
            self.target_col_var.set(target_col)
            self.default_val_var.set(def_val)
            self._refresh_rule_list()

            if self._available_columns:
                warns = validate_rules_against_columns(self._rules, self._available_columns)
                if warns:
                    messagebox.showwarning("컬럼 확인", "\n".join(warns))

            self._refresh_preview()
            messagebox.showinfo("완료", f"{len(rules)}개의 매핑 규칙을 불러왔습니다.")
        except Exception as err:
            messagebox.showerror("불러오기 오류", f"규칙 파일을 불러오는 중 오류가 발생했습니다:\n{err}")

    def _refresh_preview(self):
        fpath = self.filepath_var.get()
        if not fpath or not os.path.exists(fpath):
            return

        spec = MappingSpec(
            file_path=fpath,
            target_column=self.target_col_var.get().strip() or "Category",
            rules=self._rules,
            default_value=self.default_val_var.get().strip() or "미분류",
        )

        try:
            preview_df, rule_stats, unmapped_summary = preview_mapping(spec, preview_rows=2000)
            self._preview_df = preview_df
            self._unmapped_summary = unmapped_summary

            # Update rule tree match counts
            stat_lookup = {s.rule_id: s for s in rule_stats}
            for r_id in self.rule_tree.get_children():
                st = stat_lookup.get(r_id)
                if st:
                    vals = list(self.rule_tree.item(r_id, "values"))
                    vals[4] = f"{st.matched_count:,} ({st.matched_percent:.1f}%)"
                    self.rule_tree.item(r_id, values=vals)

            # Update funnel text
            total = len(preview_df)
            mapped = sum(s.matched_count for s in rule_stats)
            unmapped = total - mapped
            self.funnel_text_var.set(
                f"샘플 {total:,}행 | 분류 완료: {mapped:,}행 ({mapped/total*100:.1f}%) | ⚠️ 미매핑: {unmapped:,}행 ({unmapped/total*100:.1f}%)"
            )

            # Update preview table
            self.preview_tree.delete(*self.preview_tree.get_children())
            display_cols = list(preview_df.columns)
            self.preview_tree.configure(columns=display_cols)

            for col in display_cols:
                self.preview_tree.heading(col, text=col)
                self.preview_tree.column(col, width=100, anchor="w")

            for _, row in preview_df.head(100).iterrows():
                self.preview_tree.insert("", "end", values=[str(v) if pd.notna(v) else "" for v in row])

        except Exception as err:
            self.app.set_status_log(f"미리보기 오류: {err}")

    def _on_show_unmapped_analysis(self):
        if not self._unmapped_summary:
            messagebox.showinfo("미매핑 분석", "미매핑된 데이터가 없거나 미리보기가 실행되지 않았습니다.")
            return

        lines = ["⚠️ 모든 룰을 통과하고 남은 미매핑 행의 컬럼별 주요 빈도:\n"]
        for col, counts in self._unmapped_summary.items():
            lines.append(f"▶ 컬럼 [{col}]:")
            for val, cnt in counts:
                lines.append(f"   • '{val}': {cnt:,}건")
            lines.append("")

        lines.append("이 값들을 참조하여 새 매핑 규칙을 등록하세요.")
        messagebox.showinfo("미매핑 데이터 분석", "\n".join(lines))

    def _start_execution(self, fmt: str = "csv", unmapped_only: bool = False):
        fpath = self.filepath_var.get()
        if not fpath or not os.path.exists(fpath):
            messagebox.showerror("오류", "원본 파일을 먼저 선택해 주세요.")
            return

        if not self._rules and not unmapped_only:
            messagebox.showwarning("안내", "적어도 하나 이상의 매핑 규칙을 추가해 주세요.")
            return

        spec = MappingSpec(
            file_path=fpath,
            target_column=self.target_col_var.get().strip() or "Category",
            rules=self._rules,
            default_value=self.default_val_var.get().strip() or "미분류",
            output_format=fmt,
            export_unmapped_only=unmapped_only,
        )

        self._cancel_event = threading.Event()
        self._is_processing = True
        self._set_controls_busy(True)
        self.app.set_progress(10, "대용량 매핑 처리 중…")

        def worker(report):
            return execute_mapping(spec, cancel_event=self._cancel_event)

        started = self.app.job_runner.start(
            "data-mapping-job",
            worker,
            JobCallbacks(
                on_progress=lambda p, d: self.app.set_progress(p, f"처리 중: {d:,}행"),
                on_success=self._on_success,
                on_error=self._on_error,
                on_finished=self._on_finished,
            ),
        )
        if not started:
            self._on_finished()

    def _on_success(self, result: MappingResult):
        self._last_result_path = result.output_path
        self.btn_open_folder.configure(state="normal")
        self.app.set_progress(100, "매핑 완료")

        summary_msg = (
            f"매핑 작업이 완료되었습니다!\n\n"
            f"• 저장 파일: {os.path.basename(result.output_path)}\n"
            f"• 총 데이터: {result.total_rows:,}행\n"
            f"• 매핑 완료: {result.mapped_rows:,}행 ({result.mapped_percent:.1f}%)\n"
            f"• 미매핑 잔여: {result.unmapped_rows:,}행 ({result.unmapped_percent:.1f}%)\n\n"
            f"저장 위치: {result.output_path}"
        )
        self.app.set_result_text(summary_msg)
        messagebox.showinfo("매핑 완료", summary_msg)

    def _on_error(self, err: Exception):
        if isinstance(err, MapperCancelledError):
            self.app.set_status_log("매핑 작업이 취소되었습니다.")
            messagebox.showinfo("취소", "매핑 작업이 취소되었습니다.")
        else:
            self.app.set_status_log("매핑 작업 중 오류 발생")
            messagebox.showerror("오류", f"매핑 처리 중 오류가 발생했습니다:\n{err}")

    def _on_finished(self):
        self._is_processing = False
        self._set_controls_busy(False)
        self.app.set_progress(0)

    def _cancel_job(self):
        if self._cancel_event:
            self._cancel_event.set()
            self.btn_cancel.configure(state="disabled")

    def _set_controls_busy(self, busy: bool):
        st = "disabled" if busy else "normal"
        self.btn_save_csv.configure(state=st)
        self.btn_save_excel.configure(state=st)
        self.btn_unmapped_export.configure(state=st)
        self.btn_cancel.configure(state="normal" if busy else "disabled")

    def _open_output_folder(self):
        if self._last_result_path and os.path.exists(self._last_result_path):
            open_containing_folder(self._last_result_path)
