"""Qt Item Delegates for inline column role selection in dataset wizard."""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QStyledItemDelegate,
    QWidget,
)

from src.qt.tabs.dataset.models import ColumnRoleTableModel


class RoleDelegate(QStyledItemDelegate):
    """Delegate rendering a dropdown combobox for column role assignment."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

    def createEditor(self, parent: QWidget, option: Any, index: QModelIndex) -> QWidget:
        combo = QComboBox(parent)
        combo.addItems(ColumnRoleTableModel.ROLE_OPTIONS)
        return combo

    def setEditorData(self, editor: QWidget, index: QModelIndex) -> None:
        combo: QComboBox = editor  # type: ignore
        current_text = index.data(Qt.ItemDataRole.DisplayRole)
        idx = combo.findText(current_text)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def setModelData(self, editor: QWidget, model: Any, index: QModelIndex) -> None:
        combo: QComboBox = editor  # type: ignore
        selected_text = combo.currentText()
        model.setData(index, selected_text, Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor: QWidget, option: Any, index: QModelIndex) -> None:
        editor.setGeometry(option.rect)
