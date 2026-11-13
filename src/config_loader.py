"""Configuration loader and validator for Data Refinery DWH pipeline."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.app_paths import (
    bundle_or_source_root,
    dwh_legacy_directory,
    installation_directory,
)


class ConfigError(Exception):
    """Raised when pipeline configuration is invalid or missing."""


@dataclass(frozen=True)
class AccountRule:
    """Mapping definition for an individual P&L account."""
    account_code: str
    account_name: str
    statement_line: str
    category: str
    sign_multiplier: int
    waterfall_step: str
    is_subtotal: bool = False


@dataclass
class DwhSettings:
    """Runtime settings for entity isolation, paths, and currency."""
    project_name: str
    project_version: str
    entity_code: str
    entity_name: str
    allowed_currencies: list[str]
    reporting_currency: str
    number_format: str
    raw_dir: Path
    masters_dir: Path
    pnl_dir: Path
    inventory_dir: Path
    price_dir: Path
    staging_dir: Path
    warehouse_dir: Path
    warehouse_db: Path
    mart_export_dir: Path
    logs_dir: Path
    fx_rates: dict[str, dict[str, float]] = field(default_factory=dict)

    def ensure_directories(self) -> None:
        """Create all configured directories if they do not exist."""
        for p in [
            self.raw_dir,
            self.masters_dir,
            self.pnl_dir,
            self.inventory_dir,
            self.price_dir,
            self.staging_dir,
            self.warehouse_dir,
            self.mart_export_dir,
            self.logs_dir,
        ]:
            p.mkdir(parents=True, exist_ok=True)

    def get_fx_rate(self, period: str, currency: str) -> float:
        """Get exchange rate to reporting currency for given period and currency."""
        curr = currency.upper().strip()
        if curr == self.reporting_currency:
            return 1.0

        period_key = period.strip()
        period_rates = self.fx_rates.get(period_key) or self.fx_rates.get("DEFAULT") or {}
        rate = period_rates.get(curr)
        if rate is None:
            # Fallback to default rates if period-specific rate is missing
            default_rates = self.fx_rates.get("DEFAULT", {})
            rate = default_rates.get(curr)

        if rate is None or rate <= 0:
            raise ConfigError(
                f"Exchange rate for currency '{curr}' in period '{period}' is not configured or non-positive."
            )
        return float(rate)


class ConfigLoader:
    """Loads and validates pipeline configuration files."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        if base_dir is not None:
            self.base_dir = Path(base_dir).resolve()
            self.config_dir = self.base_dir / "config"
            return

        cwd = Path.cwd().resolve()
        is_frozen = bool(getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", None))
        if (
            not is_frozen
            and cwd != installation_directory().resolve()
            and (cwd / "config" / "settings.yaml").is_file()
        ):
            self.base_dir = cwd
            self.config_dir = self.base_dir / "config"
        else:
            self.base_dir = dwh_legacy_directory().resolve()
            self.config_dir = self.base_dir / "config"
            self._seed_default_configs()

    def _seed_default_configs(self) -> None:
        """Atomically seed default YAML configuration files into the user legacy workspace once."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        resource_root = bundle_or_source_root()
        fallback_root = Path(__file__).resolve().parents[1]

        for filename in ("settings.yaml", "account_mapping.yaml"):
            target_path = self.config_dir / filename
            if target_path.exists():
                continue

            source_path = resource_root / "config" / filename
            if not source_path.is_file() and fallback_root != resource_root:
                candidate = fallback_root / "config" / filename
                if candidate.is_file():
                    source_path = candidate

            if not source_path.is_file():
                continue

            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{filename}.",
                suffix=".tmp",
                dir=str(self.config_dir),
            )
            os.close(fd)
            tmp_path = Path(tmp_name)
            try:
                shutil.copy2(source_path, tmp_path)
                if not target_path.exists():
                    os.replace(tmp_path, target_path)
            finally:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass

    def load_settings(self, settings_path: Path | str | None = None) -> DwhSettings:
        """Load and validate settings.yaml."""
        path = Path(settings_path) if settings_path else self.config_dir / "settings.yaml"
        if not path.is_file():
            raise ConfigError(f"Settings file not found: {path}")

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            raise ConfigError(f"Failed to parse settings YAML at {path}: {e}") from e

        project = data.get("project", {})
        entity = data.get("entity", {})
        paths = data.get("paths", {})
        fx_rates = data.get("fx_rates", {})

        def resolve_p(p_str: str) -> Path:
            p = Path(p_str)
            return p if p.is_absolute() else (self.base_dir / p).resolve()

        return DwhSettings(
            project_name=project.get("name", "DataRefinery_DWH"),
            project_version=project.get("version", "2.2.0"),
            entity_code=str(entity.get("code", "PL")).strip().upper(),
            entity_name=str(entity.get("name", "Poland Subsidiary")),
            allowed_currencies=[str(c).strip().upper() for c in entity.get("allowed_currencies", ["PLN", "EUR"])],
            reporting_currency=str(entity.get("reporting_currency", "PLN")).strip().upper(),
            number_format=str(entity.get("number_format", "polish")).lower(),
            raw_dir=resolve_p(paths.get("raw_dir", "data/01_raw")),
            masters_dir=resolve_p(paths.get("masters_dir", "data/01_raw/masters")),
            pnl_dir=resolve_p(paths.get("pnl_dir", "data/01_raw/pnl")),
            inventory_dir=resolve_p(paths.get("inventory_dir", "data/01_raw/inventory")),
            price_dir=resolve_p(paths.get("price_dir", "data/01_raw/price")),
            staging_dir=resolve_p(paths.get("staging_dir", "data/02_staging")),
            warehouse_dir=resolve_p(paths.get("warehouse_dir", "data/03_warehouse")),
            warehouse_db=resolve_p(paths.get("warehouse_db", "data/03_warehouse/warehouse.duckdb")),
            mart_export_dir=resolve_p(paths.get("mart_export_dir", "data/04_mart_export")),
            logs_dir=resolve_p(paths.get("logs_dir", "logs")),
            fx_rates=fx_rates,
        )

    def load_account_mapping(self, mapping_path: Path | str | None = None) -> dict[str, AccountRule]:
        """Load and validate account_mapping.yaml."""
        path = Path(mapping_path) if mapping_path else self.config_dir / "account_mapping.yaml"
        if not path.is_file():
            raise ConfigError(f"Account mapping file not found: {path}")

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            raise ConfigError(f"Failed to parse account mapping YAML at {path}: {e}") from e

        waterfall_lines = data.get("waterfall_lines", {})
        accounts_data = data.get("accounts", {})

        if not accounts_data:
            raise ConfigError("No accounts defined in account_mapping.yaml")

        rules: dict[str, AccountRule] = {}
        for acc_code, acc_info in accounts_data.items():
            acc_name = acc_info.get("name", acc_code)
            stmt_line = acc_info.get("statement_line")
            if not stmt_line or stmt_line not in waterfall_lines:
                raise ConfigError(
                    f"Account '{acc_code}' references invalid or missing statement_line: '{stmt_line}'"
                )

            line_spec = waterfall_lines[stmt_line]
            rule = AccountRule(
                account_code=str(acc_code).strip(),
                account_name=str(acc_name).strip(),
                statement_line=str(stmt_line).strip(),
                category=str(line_spec.get("category", "EXPENSE")),
                sign_multiplier=int(line_spec.get("sign_multiplier", 1)),
                waterfall_step=str(line_spec.get("waterfall_step", "OPERATING_PROFIT")),
                is_subtotal=bool(line_spec.get("is_subtotal", False)),
            )
            rules[rule.account_code] = rule

        return rules


def load_settings(settings_path: Path | str | None = None) -> DwhSettings:
    """Convenience helper to load runtime DwhSettings."""
    return ConfigLoader().load_settings(settings_path)


def load_account_mapping(mapping_path: Path | str | None = None) -> dict[str, AccountRule]:
    """Convenience helper to load account mapping rules."""
    return ConfigLoader().load_account_mapping(mapping_path)
