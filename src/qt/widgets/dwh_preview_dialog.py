"""Interactive preview dialog for DWH Parquet Mart data."""

from __future__ import annotations

from pathlib import Path
from typing import Optional
import duckdb

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.i18n import _UI_TEXT
from src.qt.dialogs import info_dialog
from src.ui_components import PALETTE


class DwhPreviewDialog(QDialog):
    """Modal dialog rendering top 50 rows of exported Parquet Mart."""

    def __init__(
        self,
        parquet_path: Path | str,
        parent: Optional[QWidget] = None,
        language_code: str = "ko",
    ) -> None:
        super().__init__(parent)
        self.parquet_path = Path(parquet_path).resolve()
        self.language_code = language_code
        self.resize(920, 560)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._init_ui()
        self._load_data()
        self.apply_language(self.language_code)

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # Header Title & Summary
        self.lbl_title = QLabel()
        self.lbl_title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {PALETTE['navy']};")
        layout.addWidget(self.lbl_title)

        self.lbl_summary = QLabel()
        self.lbl_summary.setStyleSheet("color: #475569; font-size: 11px;")
        layout.addWidget(self.lbl_summary)

        # Search Filter Row
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        self.lbl_search_icon = QLabel("🔍")
        self.edit_filter = QLineEdit()
        self.edit_filter.setClearButtonEnabled(True)
        self.edit_filter.setStyleSheet(
            "QLineEdit { border: 1px solid #CBD5E1; border-radius: 4px; padding: 4px 8px; font-size: 11px; }"
            "QLineEdit:focus { border-color: #3B82F6; }"
        )
        self.edit_filter.textChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.lbl_search_icon)
        filter_row.addWidget(self.edit_filter, stretch=1)

        self.combo_limit = QComboBox()
        self.combo_limit.addItems(["50", "100", "200"])
        self.combo_limit.setStyleSheet("padding: 2px 6px; font-size: 11px;")
        self.combo_limit.currentIndexChanged.connect(self._on_limit_changed)
        filter_row.addWidget(self.combo_limit)

        layout.addLayout(filter_row)

        # Table
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(
            "QTableWidget { gridline-color: #E2E8F0; font-size: 11px; }"
            "QHeaderView::section { background-color: #F1F5F9; font-weight: bold; padding: 4px; }"
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, stretch=1)

        # Bottom Bar
        bottom_row = QHBoxLayout()
        self.btn_copy_all = QPushButton()
        self.btn_copy_all.clicked.connect(self._on_copy_all)
        bottom_row.addWidget(self.btn_copy_all)
        bottom_row.addStretch()

        self.btn_close = QPushButton()
        self.btn_close.clicked.connect(self.accept)
        bottom_row.addWidget(self.btn_close)

        layout.addLayout(bottom_row)

    def _load_data(self) -> None:
        if not self.parquet_path.is_file():
            return

        con = duckdb.connect()
        try:
            total_rows = con.execute("SELECT COUNT(*) FROM read_parquet(?)", [str(self.parquet_path)]).fetchone()[0]
            desc = con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(self.parquet_path)]).fetchall()
            all_cols = [c[0] for c in desc]

            priority_cols = [
                "period", "channel_group", "customer_name", "model_code", "product_name",
                "sales_qty", "gross_revenue_reporting", "cogs_reporting",
                "gross_margin_reporting", "operating_profit_reporting"
            ]
            selected_cols = [c for c in priority_cols if c in all_cols]
            for c in all_cols:
                if c not in selected_cols and len(selected_cols) < 14:
                    selected_cols.append(c)

            cols_sql = ", ".join(f'"{c}"' for c in selected_cols)
            limit = int(self.combo_limit.currentText()) if hasattr(self, "combo_limit") else 50
            rows = con.execute(f"SELECT {cols_sql} FROM read_parquet(?) LIMIT {limit}", [str(self.parquet_path)]).fetchall()

            self.table.setSortingEnabled(False)
            self.table.setColumnCount(len(selected_cols))
            self.table.setHorizontalHeaderLabels(selected_cols)
            self.table.setRowCount(len(rows))

            for r_idx, row in enumerate(rows):
                for c_idx, val in enumerate(row):
                    col_name = selected_cols[c_idx]
                    is_numeric = isinstance(val, (int, float)) and not ("code" in col_name or "period" in col_name)

                    item = QTableWidgetItem()
                    if val is None:
                        item.setData(Qt.ItemDataRole.EditRole, "")
                        item.setData(Qt.ItemDataRole.DisplayRole, "-")
                        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                    elif is_numeric:
                        item.setData(Qt.ItemDataRole.EditRole, float(val))
                        display_text = f"{val:,.0f}" if isinstance(val, int) or val.is_integer() else f"{val:,.2f}"
                        item.setData(Qt.ItemDataRole.DisplayRole, display_text)
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    else:
                        display_text = str(val)
                        item.setData(Qt.ItemDataRole.EditRole, display_text)
                        item.setData(Qt.ItemDataRole.DisplayRole, display_text)
                        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                    self.table.setItem(r_idx, c_idx, item)

            self.table.setSortingEnabled(True)
            self._total_rows = total_rows
            self._shown_rows = len(rows)
            self._selected_cols = selected_cols
            self._rows = rows
        finally:
            con.close()

    def _on_filter_changed(self, text: str) -> None:
        """Filter table rows in real-time based on query text."""
        query = text.strip().lower()
        for r in range(self.table.rowCount()):
            if not query:
                self.table.setRowHidden(r, False)
                continue
            row_match = False
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                if item and query in item.text().lower():
                    row_match = True
                    break
            self.table.setRowHidden(r, not row_match)

    def _on_limit_changed(self) -> None:
        """Reload data when row limit combo changes."""
        self._load_data()
        self.apply_language(self.language_code)
        if hasattr(self, "edit_filter") and self.edit_filter.text():
            self._on_filter_changed(self.edit_filter.text())

    def _on_copy_all(self) -> None:
        """Copy table data to clipboard in TSV format."""
        if not hasattr(self, "_rows") or not self._rows:
            return

        lines = ["\t".join(self._selected_cols)]
        for row in self._rows:
            lines.append("\t".join(str(v if v is not None else "") for v in row))

        QGuiApplication.clipboard().setText("\n".join(lines))
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        info_dialog(
            self,
            t.get("dwh_dlg_clipboard_title", "클립보드 복사"),
            t.get("dwh_preview_copied", "미리보기 데이터가 클립보드에 복사되었습니다!"),
        )

    def apply_language(self, lang: str) -> None:
        self.language_code = lang
        t = _UI_TEXT.get(lang, _UI_TEXT["ko"])

        title_text = t.get("dwh_preview_title", "DWH 마트 결과 미리보기 (상위 50행)")
        self.setWindowTitle(title_text)
        self.lbl_title.setText(f"📊 {title_text}")

        total = getattr(self, "_total_rows", 0)
        shown = getattr(self, "_shown_rows", 0)
        summary_template = t.get("dwh_preview_summary", "총 {total:,}개 행 중 상위 {shown}개 행 표시 중 (정상 집계 완료)")
        self.lbl_summary.setText(summary_template.format(total=total, shown=shown))

        self.btn_copy_all.setText(t.get("dwh_preview_copy_btn", "📋 전체 클립보드 복사 (TSV)"))
        self.btn_close.setText(t.get("close", "Close" if lang != "ko" else "닫기"))
        self.edit_filter.setPlaceholderText(
            t.get("dwh_preview_search_placeholder", "결과 데이터 실시간 검색 (고객명, 제품코드, 기간 등)...")
        )
