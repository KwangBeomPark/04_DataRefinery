"""Integrated Data Tools Tab combining CSV Repair and Promotion Normalizer."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from src.background_jobs import BackgroundJobRunner
from src.i18n import _UI_TEXT
from src.qt.tabs.csv_tab import CsvRepairTab
from src.qt.tabs.promotion_tab import PromotionTab


class DataToolsTab(QWidget):
    """Auxiliary data preparation tools containing CSV repair and promotion normalizer subtabs."""

    def __init__(
        self,
        language_code: str = "ko",
        job_runner: Optional[BackgroundJobRunner] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.language_code = language_code
        self.job_runner = job_runner
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        self.sub_tab_widget = QTabWidget()
        self.sub_tab_widget.setObjectName("subTabWidget")

        self.tab_csv = CsvRepairTab(language_code=self.language_code, job_runner=self.job_runner)
        self.tab_promotion = PromotionTab(language_code=self.language_code, job_runner=self.job_runner)

        self.sub_tab_widget.addTab(self.tab_csv, "CSV Repair")
        self.sub_tab_widget.addTab(self.tab_promotion, "Promotion Normalizer")

        layout.addWidget(self.sub_tab_widget)
        self.apply_language(self.language_code)

    def apply_language(self, lang_code: str) -> None:
        """Update subtab titles and propagate language changes to child tabs."""
        self.language_code = lang_code
        t = _UI_TEXT.get(lang_code, _UI_TEXT["en"])

        subtab_csv = t.get("subtab_csv_repair", "CSV Repair")
        subtab_promo = t.get("subtab_promo_calc", "Promotion Normalizer")

        self.sub_tab_widget.setTabText(0, f"📄 {subtab_csv}")
        self.sub_tab_widget.setTabText(1, f"🏷️ {subtab_promo}")

        if hasattr(self.tab_csv, "apply_language"):
            self.tab_csv.apply_language(lang_code)
        if hasattr(self.tab_promotion, "apply_language"):
            self.tab_promotion.apply_language(lang_code)
