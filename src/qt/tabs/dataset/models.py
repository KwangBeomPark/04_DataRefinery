"""Qt Table Models for Dataset Wizard: File filtering and Column role assignment."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal

from src.dataset_engine import ScannedFile
from src.dataset_profiler import ColumnProfile


class DatasetFileTableModel(QAbstractTableModel):
    """Table model displaying scanned candidate files with inclusion checkboxes and badges."""

    selectionChanged = Signal()

    COLUMNS = ["선택", "파일명", "기간", "크기", "헤더 상태", "수정일시"]

    def __init__(self, parent: Optional[Any] = None):
        super().__init__(parent)
        self._files: List[ScannedFile] = []
        self._included: List[bool] = []

    def set_files(self, files: List[ScannedFile], excluded_names: Optional[Set[str]] = None) -> None:
        self.beginResetModel()
        self._files = list(files)
        exc = excluded_names or set()
        self._included = [f.file_name not in exc for f in self._files]
        self.endResetModel()
        self.selectionChanged.emit()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._files)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._files)):
            return None

        sf = self._files[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.CheckStateRole and col == 0:
            return Qt.CheckState.Checked if self._included[index.row()] else Qt.CheckState.Unchecked

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 1:
                return sf.file_name
            elif col == 2:
                return sf.period or "-"
            elif col == 3:
                return self._format_size(sf.file_size)
            elif col == 4:
                return sf.header_detail or sf.header_status
            elif col == 5:
                try:
                    return datetime.fromtimestamp(sf.mtime).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    return "-"

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (0, 2, 3, 4):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base_flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 0:
            return base_flags | Qt.ItemFlag.ItemIsUserCheckable
        return base_flags

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if index.isValid() and index.column() == 0 and role == Qt.ItemDataRole.CheckStateRole:
            is_checked = (
                value == Qt.CheckState.Checked
                or value == Qt.CheckState.Checked.value
                or value == 2
                or value is True
            )
            self._included[index.row()] = is_checked
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
            self.selectionChanged.emit()
            return True
        return False

    def select_all(self, state: bool = True) -> None:
        self.beginResetModel()
        self._included = [state] * len(self._files)
        self.endResetModel()
        self.selectionChanged.emit()

    def set_excluded_files(self, excluded_names: Set[str]) -> None:
        self.beginResetModel()
        self._included = [f.file_name not in excluded_names for f in self._files]
        self.endResetModel()
        self.selectionChanged.emit()

    def get_excluded_file_names(self) -> List[str]:
        return [
            self._files[i].file_name
            for i, inc in enumerate(self._included)
            if not inc
        ]

    def get_included_files(self) -> List[ScannedFile]:
        return [
            self._files[i]
            for i, inc in enumerate(self._included)
            if inc
        ]

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes / (1024 * 1024):.1f} MB"


class ColumnRoleTableModel(QAbstractTableModel):
    """Table model displaying discovered columns, sampled values, and assignable roles."""

    rolesChanged = Signal()

    COLUMNS = ["컬럼명", "추론 타입", "샘플 값 미리보기", "역할"]
    ROLE_OPTIONS = ["식별 키", "수치(측도)", "기준 기간", "제외"]
    ROLE_CODE_MAP = {
        "key": "식별 키",
        "numeric": "수치(측도)",
        "period": "기준 기간",
        "ignore": "제외",
    }
    CODE_FROM_ROLE = {v: k for k, v in ROLE_CODE_MAP.items()}

    def __init__(self, parent: Optional[Any] = None):
        super().__init__(parent)
        self._profiles: List[ColumnProfile] = []
        self._roles: List[str] = []  # "key", "numeric", "period", "ignore"
        self._filter_query = ""
        self._role_filter = "all"  # "all", "period", "key", "numeric", "ignore", "unassigned"

    def set_profiles(
        self,
        profiles: List[ColumnProfile],
        initial_period: Optional[str] = None,
        initial_keys: Optional[List[str]] = None,
        initial_numerics: Optional[List[str]] = None,
    ) -> None:
        self.beginResetModel()
        self._profiles = list(profiles)
        self._roles = []

        keys_set = set(initial_keys or [])
        num_set = set(initial_numerics or [])

        for p in self._profiles:
            if initial_period and p.name == initial_period:
                self._roles.append("period")
            elif p.name in keys_set:
                self._roles.append("key")
            elif p.name in num_set:
                self._roles.append("numeric")
            else:
                self._roles.append(p.suggested_role)

        # Ensure at most one period is selected
        period_indices = [i for i, r in enumerate(self._roles) if r == "period"]
        if len(period_indices) > 1:
            for idx in period_indices[1:]:
                self._roles[idx] = "key"

        self.endResetModel()
        self.rolesChanged.emit()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._profiles)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._profiles)):
            return None

        prof = self._profiles[index.row()]
        col = index.column()
        current_role = self._roles[index.row()]

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return prof.name
            elif col == 1:
                return prof.inferred_type.upper()
            elif col == 2:
                samples = prof.sample_values[:3]
                return " | ".join(samples) if samples else "-"
            elif col == 3:
                return self.ROLE_CODE_MAP.get(current_role, "제외")

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col == 1:
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 3:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if index.isValid() and index.column() == 3 and role == Qt.ItemDataRole.EditRole:
            new_role_code = self.CODE_FROM_ROLE.get(value, value)
            if new_role_code not in ("period", "key", "numeric", "ignore"):
                return False

            row = index.row()
            if new_role_code == "period":
                # Only one column can be period: downgrade any other period to key
                for i in range(len(self._roles)):
                    if self._roles[i] == "period" and i != row:
                        self._roles[i] = "key"
                        idx_other = self.index(i, 3)
                        self.dataChanged.emit(idx_other, idx_other, [Qt.ItemDataRole.DisplayRole])

            self._roles[row] = new_role_code
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])
            self.rolesChanged.emit()
            return True
        return False

    def set_role_for_row(self, row: int, role_code: str) -> None:
        if 0 <= row < len(self._roles):
            idx = self.index(row, 3)
            self.setData(idx, role_code, Qt.ItemDataRole.EditRole)

    def apply_auto_guess(self) -> None:
        """Re-apply recommended roles from the profiler."""
        self.beginResetModel()
        self._roles = [p.suggested_role for p in self._profiles]
        # Ensure at most one period
        period_indices = [i for i, r in enumerate(self._roles) if r == "period"]
        if len(period_indices) > 1:
            for idx in period_indices[1:]:
                self._roles[idx] = "key"
        self.endResetModel()
        self.rolesChanged.emit()

    def get_role_summary(self) -> Dict[str, Any]:
        """Return counts and column names for each role."""
        period_col = ""
        key_cols: List[str] = []
        numeric_cols: List[str] = []
        ignore_cols: List[str] = []

        for p, r in zip(self._profiles, self._roles):
            if r == "period":
                period_col = p.name
            elif r == "key":
                key_cols.append(p.name)
            elif r == "numeric":
                numeric_cols.append(p.name)
            else:
                ignore_cols.append(p.name)

        return {
            "period": period_col,
            "keys": key_cols,
            "numerics": numeric_cols,
            "ignores": ignore_cols,
            "total_columns": len(self._profiles),
        }
