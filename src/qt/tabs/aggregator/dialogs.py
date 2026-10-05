"""Rule creation dialogs for Data Aggregator tab."""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import Qt
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
from src.qt.dialogs import error_dialog


class GroupRuleDialog(QDialog):
    """Dialog to create a ColumnGroupRule (combining/summing multiple measures)."""

    def __init__(
        self,
        available_measures: List[str],
        existing_names: List[str],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("컬럼 묶기(합산) 추가")
        self.resize(380, 360)
        self._existing_names = set(existing_names)
        self.rule: Optional[ColumnGroupRule] = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form = QFormLayout()
        self.edit_name = QLineEdit("새_묶음_컬럼")
        form.addRow("새 묶음 컬럼 이름:", self.edit_name)
        layout.addLayout(form)

        layout.addWidget(QLabel("합산할 원본 수치 컬럼들 (다중 선택):"))
        self.list_sources = QListWidget()
        self.list_sources.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        for m in available_measures:
            item = QListWidgetItem(m)
            self.list_sources.addItem(item)
        layout.addWidget(self.list_sources)

        # Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("취소")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_ok = QPushButton("추가")
        btn_ok.setProperty("primary", "true")
        btn_ok.clicked.connect(self._on_accept)
        btn_box.addWidget(btn_ok)
        layout.addLayout(btn_box)

    def _on_accept(self) -> None:
        name = self.edit_name.text().strip()
        if not name:
            error_dialog(self, "입력 확인", "컬럼 이름을 입력해 주세요.")
            return
        if name in self._existing_names:
            error_dialog(self, "입력 확인", "이미 존재하는 컬럼 이름입니다. 다른 이름을 사용하세요.")
            return

        selected = [item.text() for item in self.list_sources.selectedItems()]
        if not selected:
            error_dialog(self, "입력 확인", "합산할 수치 컬럼을 1개 이상 선택해 주세요.")
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
    ):
        super().__init__(parent)
        self.setWindowTitle("비율/파생 수식 추가")
        self.resize(400, 320)
        self._existing_names = set(existing_names)
        self.rule: Optional[DerivedFormulaRule] = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)

        self.edit_name = QLineEdit("이익율(%)")
        form.addRow("새 수식 컬럼 이름:", self.edit_name)

        self.combo_num = QComboBox()
        self.combo_num.addItems(available_measures)
        form.addRow("분자 (Numerator):", self.combo_num)

        self.combo_den = QComboBox()
        self.combo_den.addItems(available_measures)
        if len(available_measures) > 1:
            self.combo_den.setCurrentIndex(1)
        form.addRow("÷ 분모 (Denominator):", self.combo_den)

        self.combo_format = QComboBox()
        self.combo_format.addItems(["백분율 (%)", "비율 (배수)", "일반 숫자"])
        form.addRow("표시 형식:", self.combo_format)

        layout.addLayout(form)

        lbl_note = QLabel(
            "※ 비율은 그룹 합산이 완료된 후 계산됩니다.\n"
            "0으로 나누는 행은 0으로 안전하게 치환됩니다."
        )
        lbl_note.setProperty("caption", "true")
        layout.addWidget(lbl_note)

        # Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("취소")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_ok = QPushButton("추가")
        btn_ok.setProperty("primary", "true")
        btn_ok.clicked.connect(self._on_accept)
        btn_box.addWidget(btn_ok)
        layout.addLayout(btn_box)

    def _on_accept(self) -> None:
        name = self.edit_name.text().strip()
        if not name:
            error_dialog(self, "입력 확인", "수식 컬럼 이름을 입력해 주세요.")
            return
        if name in self._existing_names:
            error_dialog(self, "입력 확인", "이미 존재하는 컬럼 이름입니다. 다른 이름을 사용하세요.")
            return

        num = self.combo_num.currentText()
        den = self.combo_den.currentText()
        if not num or not den:
            error_dialog(self, "입력 확인", "분자와 분모를 모두 선택해 주세요.")
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
    ):
        super().__init__(parent)
        self.setWindowTitle("필터 조건 추가")
        self.resize(380, 240)
        self.condition: Optional[FilterCondition] = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)

        self.combo_col = QComboBox()
        self.combo_col.addItems(available_columns)
        form.addRow("대상 컬럼:", self.combo_col)

        self.combo_op = QComboBox()
        self.combo_op.addItems(self.OPERATORS)
        form.addRow("조건 연산자:", self.combo_op)

        self.edit_val = QLineEdit()
        self.edit_val.setPlaceholderText("비교할 값 (in / not in은 쉼표 구분)")
        form.addRow("비교 값:", self.edit_val)

        layout.addLayout(form)

        # Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("취소")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_ok = QPushButton("추가")
        btn_ok.setProperty("primary", "true")
        btn_ok.clicked.connect(self._on_accept)
        btn_box.addWidget(btn_ok)
        layout.addLayout(btn_box)

    def _on_accept(self) -> None:
        col = self.combo_col.currentText()
        op = self.combo_op.currentText()
        val = self.edit_val.text().strip()

        if not col:
            error_dialog(self, "입력 확인", "대상 컬럼을 선택해 주세요.")
            return
        if not val:
            error_dialog(self, "입력 확인", "비교 값을 입력해 주세요.")
            return

        if op in ("in", "not in"):
            parsed_val: Any = [x.strip() for x in val.split(",") if x.strip()]
        else:
            parsed_val = val

        self.condition = FilterCondition(column=col, operator=op, value=parsed_val)
        self.accept()
