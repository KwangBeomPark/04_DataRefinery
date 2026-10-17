"""Rule creation dialogs for Data Aggregator tab."""

from __future__ import annotations

from typing import Any, List, Optional

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.data_aggregator import (
    ColumnGroupRule,
    DerivedFormulaRule,
    FilterCondition,
)
from src.i18n import _UI_TEXT
from src.qt.dialogs import error_dialog


class GroupRuleDialog(QDialog):
    """Dialog to create a ColumnGroupRule (combining/summing multiple measures)."""

    def __init__(
        self,
        available_measures: List[str],
        existing_names: List[str],
        parent: Optional[QWidget] = None,
        language_code: str = "ko",
    ):
        super().__init__(parent)
        self._lang = language_code
        t = _UI_TEXT.get(language_code, _UI_TEXT["ko"])
        self.setWindowTitle(t.get("agg_dlg_group_title", "컬럼 묶기(합산) 추가"))
        self.resize(380, 360)
        self._existing_names = set(existing_names)
        self.rule: Optional[ColumnGroupRule] = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form = QFormLayout()
        self.edit_name = QLineEdit(t.get("agg_dlg_group_default_name", "새_묶음_컬럼"))
        form.addRow(t.get("agg_dlg_group_name", "새 묶음 컬럼 이름:"), self.edit_name)
        layout.addLayout(form)

        layout.addWidget(QLabel(t.get("agg_dlg_group_sources", "합산할 원본 수치 컬럼들 (다중 선택):")))
        self.list_sources = QListWidget()
        self.list_sources.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        for m in available_measures:
            item = QListWidgetItem(m)
            self.list_sources.addItem(item)
        layout.addWidget(self.list_sources)

        # Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton(t.get("agg_dlg_cancel", "취소"))
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_ok = QPushButton(t.get("agg_dlg_add", "추가"))
        btn_ok.setProperty("primary", "true")
        btn_ok.clicked.connect(self._on_accept)
        btn_box.addWidget(btn_ok)
        layout.addLayout(btn_box)

    def _on_accept(self) -> None:
        t = _UI_TEXT.get(self._lang, _UI_TEXT["ko"])
        name = self.edit_name.text().strip()
        if not name:
            error_dialog(self, t.get("agg_dlg_check_title", "입력 확인"), t.get("agg_dlg_group_incomplete", ""))
            return
        if name in self._existing_names:
            error_dialog(self, t.get("agg_dlg_check_title", "입력 확인"), t.get("agg_dlg_name_taken", ""))
            return

        selected = [item.text() for item in self.list_sources.selectedItems()]
        if not selected:
            error_dialog(self, t.get("agg_dlg_check_title", "입력 확인"), t.get("agg_dlg_group_incomplete", ""))
            return

        self.rule = ColumnGroupRule(new_column=name, source_columns=selected)
        self.accept()


class FormulaRuleDialog(QDialog):
    """Dialog to create a DerivedFormulaRule (ratio / margin)."""

    def __init__(
        self,
        available_measures: List[str],
        existing_names: List[str],
        parent: Optional[QWidget] = None,
        language_code: str = "ko",
    ):
        super().__init__(parent)
        self._lang = language_code
        t = _UI_TEXT.get(language_code, _UI_TEXT["ko"])
        self.setWindowTitle(t.get("agg_dlg_formula_title", "비율/파생 수식 추가"))
        self.resize(400, 320)
        self._existing_names = set(existing_names)
        self.rule: Optional[DerivedFormulaRule] = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)

        self.edit_name = QLineEdit(t.get("agg_dlg_formula_default_name", "이익율(%)"))
        form.addRow(t.get("agg_dlg_formula_name", "새 수식 컬럼 이름:"), self.edit_name)

        self.combo_num = QComboBox()
        self.combo_num.addItems(available_measures)
        form.addRow(t.get("agg_dlg_formula_numerator", "분자:"), self.combo_num)

        self.combo_den = QComboBox()
        self.combo_den.addItems(available_measures)
        if len(available_measures) > 1:
            self.combo_den.setCurrentIndex(1)
        form.addRow(t.get("agg_dlg_formula_denominator", "÷ 분모:"), self.combo_den)

        self.combo_format = QComboBox()
        self.combo_format.addItems([
            t.get("agg_dlg_format_percent", "백분율 (%)"),
            t.get("agg_dlg_format_ratio", "비율 (배수)"),
            t.get("agg_dlg_format_number", "일반 숫자"),
        ])
        form.addRow(t.get("agg_dlg_formula_format", "표시 형식:"), self.combo_format)

        layout.addLayout(form)

        lbl_note = QLabel(t.get("agg_dlg_formula_note", ""))
        lbl_note.setWordWrap(True)
        lbl_note.setProperty("caption", "true")
        layout.addWidget(lbl_note)

        # Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton(t.get("agg_dlg_cancel", "취소"))
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_ok = QPushButton(t.get("agg_dlg_add", "추가"))
        btn_ok.setProperty("primary", "true")
        btn_ok.clicked.connect(self._on_accept)
        btn_box.addWidget(btn_ok)
        layout.addLayout(btn_box)

    def _on_accept(self) -> None:
        t = _UI_TEXT.get(self._lang, _UI_TEXT["ko"])
        name = self.edit_name.text().strip()
        if not name:
            error_dialog(self, t.get("agg_dlg_check_title", "입력 확인"), t.get("agg_dlg_formula_incomplete", ""))
            return
        if name in self._existing_names:
            error_dialog(self, t.get("agg_dlg_check_title", "입력 확인"), t.get("agg_dlg_name_taken", ""))
            return

        num = self.combo_num.currentText()
        den = self.combo_den.currentText()
        if not num or not den:
            error_dialog(self, t.get("agg_dlg_check_title", "입력 확인"), t.get("agg_dlg_formula_incomplete", ""))
            return

        fmt_idx = self.combo_format.currentIndex()
        fmt_str = "percent" if fmt_idx == 0 else ("ratio" if fmt_idx == 1 else "number")
        multiplier = 100.0 if fmt_str == "percent" else 1.0

        self.rule = DerivedFormulaRule(
            new_column=name,
            numerator_column=num,
            denominator_column=den,
            multiplier=multiplier,
            format_type=fmt_str,
        )
        self.accept()


class FilterRuleDialog(QDialog):
    """Dialog to create a FilterCondition."""

    OPERATORS = ["==", "!=", "in", "not in", "contains"]

    def __init__(
        self,
        available_columns: List[str],
        parent: Optional[QWidget] = None,
        language_code: str = "ko",
    ):
        super().__init__(parent)
        self._lang = language_code
        t = _UI_TEXT.get(language_code, _UI_TEXT["ko"])
        self.setWindowTitle(t.get("agg_dlg_filter_title", "필터 조건 추가"))
        self.resize(380, 240)
        self.condition: Optional[FilterCondition] = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)

        self.combo_col = QComboBox()
        self.combo_col.addItems(available_columns)
        form.addRow(t.get("agg_dlg_filter_column", "대상 컬럼:"), self.combo_col)

        self.combo_op = QComboBox()
        self.combo_op.addItems(self.OPERATORS)
        form.addRow(t.get("agg_dlg_filter_operator", "조건 연산자:"), self.combo_op)

        self.edit_val = QLineEdit()
        self.edit_val.setPlaceholderText(t.get("agg_dlg_filter_note", ""))
        form.addRow(t.get("agg_dlg_filter_value", "비교 값:"), self.edit_val)

        layout.addLayout(form)

        # Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton(t.get("agg_dlg_cancel", "취소"))
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_ok = QPushButton(t.get("agg_dlg_add", "추가"))
        btn_ok.setProperty("primary", "true")
        btn_ok.clicked.connect(self._on_accept)
        btn_box.addWidget(btn_ok)
        layout.addLayout(btn_box)

    def _on_accept(self) -> None:
        t = _UI_TEXT.get(self._lang, _UI_TEXT["ko"])
        col = self.combo_col.currentText()
        op = self.combo_op.currentText()
        val = self.edit_val.text().strip()

        if not col:
            error_dialog(self, t.get("agg_dlg_check_title", "입력 확인"), t.get("agg_dlg_filter_incomplete", ""))
            return
        if not val:
            error_dialog(self, t.get("agg_dlg_check_title", "입력 확인"), t.get("agg_dlg_filter_value_required", ""))
            return

        if op in ("in", "not in"):
            parsed_val: Any = [x.strip() for x in val.split(",") if x.strip()]
        else:
            parsed_val = val

        self.condition = FilterCondition(column=col, operator=op, value=parsed_val)
        self.accept()

