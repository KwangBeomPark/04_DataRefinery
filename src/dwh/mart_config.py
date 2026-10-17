"""Configuration, entity isolation, and account mapping rules for the DWH layer."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.atomic_write import atomic_write_text
from src.dataset_config import get_default_storage_dir


class DwhConfigError(Exception):
    """Raised when DWH mart configuration, FX rate, period filter, or account mapping is invalid."""


VALID_STATEMENT_LINES: tuple[str, ...] = (
    "GROSS_REVENUE",
    "SALES_DEDUCTION",
    "COGS",
    "LOGISTICS_COST",
    "PROMOTION_COST",
    "WARRANTY_COST",
    "FIXED_SGNA",
    "SUBTOTAL_IGNORED",
)

VALID_WATERFALL_STEPS: tuple[str, ...] = (
    "GROSS_MARGIN",
    "CONTRIBUTION_MARGIN",
    "OPERATING_PROFIT",
    "NON_ADDITIVE",
)

VALID_COST_CATEGORIES: tuple[str, ...] = (
    "REVENUE",
    "DEDUCTION",
    "COGS",
    "LOGISTICS",
    "PROMOTION",
    "WARRANTY",
    "FIXED_COST",
    "SUBTOTAL",
)

REQUIRED_ACCOUNT_MAPPING_COLUMNS: tuple[str, ...] = (
    "account_code",
    "account_name",
    "statement_line",
    "waterfall_step",
    "cost_category",
    "sign_multiplier",
    "is_subtotal",
)


_ENTITY_CODE_PATTERN = re.compile(r"^[A-Z0-9_]{2,16}$")


def _validate_entity_code(entity_code: str) -> str:
    cleaned = str(entity_code).strip().upper()
    if not _ENTITY_CODE_PATTERN.match(cleaned):
        raise DwhConfigError(
            f"Invalid entity_code {entity_code!r}. Must match ^[A-Z0-9_]{{2,16}}$."
        )
    return cleaned


DEMO_SAMPLE_FX_RATES: dict[str, dict[str, float]] = {
    "2026-01": {"PLN": 1.0, "EUR": 4.30},
    "2026-02": {"PLN": 1.0, "EUR": 4.30},
    "2026-03": {"PLN": 1.0, "EUR": 4.30},
    "2026-04": {"PLN": 1.0, "EUR": 4.30},
}


def _default_fx_rates() -> dict[str, dict[str, float]]:
    """Return an empty FX rate dictionary for production runs so sample FX rates are never implicitly applied."""
    return {}



def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "yes", "y", "t"):
            return True
        if normalized in ("false", "0", "no", "n", "f", ""):
            return False
    raise DwhConfigError(f"Invalid boolean value for is_subtotal: {value!r}")


def _parse_sign_multiplier(value: Any) -> int:
    if isinstance(value, bool):
        raise DwhConfigError(f"Invalid sign_multiplier (boolean not allowed): {value!r}")
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise DwhConfigError(f"Invalid sign_multiplier: {value!r}") from exc
    if parsed not in (1, -1):
        raise DwhConfigError(f"sign_multiplier must be 1 or -1, got {parsed}")
    return parsed


@dataclass(frozen=True)
class AccountMappingRule:
    """Maps a raw ERP P&L column or account code to standardized P&L hierarchy and waterfall steps."""

    account_code: str
    account_name: str
    statement_line: str
    waterfall_step: str
    cost_category: str
    sign_multiplier: int = 1
    is_subtotal: bool = False

    def __post_init__(self) -> None:
        code = str(self.account_code).strip()
        name = str(self.account_name).strip()
        line = str(self.statement_line).strip().upper()
        step = str(self.waterfall_step).strip().upper()
        category = str(self.cost_category).strip().upper()

        if not code:
            raise DwhConfigError("account_code must not be empty.")
        if not name:
            raise DwhConfigError(f"account_name must not be empty for {code!r}.")
        if line not in VALID_STATEMENT_LINES:
            raise DwhConfigError(
                f"Invalid statement_line {line!r} for {code!r}. Allowed: {VALID_STATEMENT_LINES}"
            )
        if step not in VALID_WATERFALL_STEPS:
            raise DwhConfigError(
                f"Invalid waterfall_step {step!r} for {code!r}. Allowed: {VALID_WATERFALL_STEPS}"
            )
        if category not in VALID_COST_CATEGORIES:
            raise DwhConfigError(
                f"Invalid cost_category {category!r} for {code!r}. Allowed: {VALID_COST_CATEGORIES}"
            )

        multiplier = _parse_sign_multiplier(self.sign_multiplier)
        subtotal_flag = _parse_bool(self.is_subtotal)

        object.__setattr__(self, "account_code", code)
        object.__setattr__(self, "account_name", name)
        object.__setattr__(self, "statement_line", line)
        object.__setattr__(self, "waterfall_step", step)
        object.__setattr__(self, "cost_category", category)
        object.__setattr__(self, "sign_multiplier", multiplier)
        object.__setattr__(self, "is_subtotal", subtotal_flag)


DEFAULT_ACCOUNT_MAPPING_RULES: tuple[AccountMappingRule, ...] = (
    AccountMappingRule(
        account_code="REV_GROSS",
        account_name="총매출액",
        statement_line="GROSS_REVENUE",
        waterfall_step="GROSS_MARGIN",
        cost_category="REVENUE",
        sign_multiplier=1,
        is_subtotal=False,
    ),
    AccountMappingRule(
        account_code="DED_REBATE",
        account_name="매출할인및리베이트",
        statement_line="SALES_DEDUCTION",
        waterfall_step="GROSS_MARGIN",
        cost_category="DEDUCTION",
        sign_multiplier=-1,
        is_subtotal=False,
    ),
    AccountMappingRule(
        account_code="COGS_MAT",
        account_name="제조매출원가",
        statement_line="COGS",
        waterfall_step="GROSS_MARGIN",
        cost_category="COGS",
        sign_multiplier=-1,
        is_subtotal=False,
    ),
    AccountMappingRule(
        account_code="CALC_GROSS_PROFIT",
        account_name="원천매출총이익소계",
        statement_line="SUBTOTAL_IGNORED",
        waterfall_step="NON_ADDITIVE",
        cost_category="SUBTOTAL",
        sign_multiplier=1,
        is_subtotal=True,
    ),
    AccountMappingRule(
        account_code="CALC_GROSS_PROFIT_SUBTOTAL",
        account_name="원천매출총이익소계",
        statement_line="SUBTOTAL_IGNORED",
        waterfall_step="NON_ADDITIVE",
        cost_category="SUBTOTAL",
        sign_multiplier=1,
        is_subtotal=True,
    ),
    AccountMappingRule(
        account_code="COST_LOG_FREIGHT",
        account_name="내륙운송비",
        statement_line="LOGISTICS_COST",
        waterfall_step="CONTRIBUTION_MARGIN",
        cost_category="LOGISTICS",
        sign_multiplier=-1,
        is_subtotal=False,
    ),
    AccountMappingRule(
        account_code="COST_LOG_WH",
        account_name="창고보관하역비",
        statement_line="LOGISTICS_COST",
        waterfall_step="CONTRIBUTION_MARGIN",
        cost_category="LOGISTICS",
        sign_multiplier=-1,
        is_subtotal=False,
    ),
    AccountMappingRule(
        account_code="COST_PROMO_COOP",
        account_name="유통판촉지원비",
        statement_line="PROMOTION_COST",
        waterfall_step="CONTRIBUTION_MARGIN",
        cost_category="PROMOTION",
        sign_multiplier=-1,
        is_subtotal=False,
    ),
    AccountMappingRule(
        account_code="COST_WARRANTY",
        account_name="품질보증충당비",
        statement_line="WARRANTY_COST",
        waterfall_step="CONTRIBUTION_MARGIN",
        cost_category="WARRANTY",
        sign_multiplier=-1,
        is_subtotal=False,
    ),
    AccountMappingRule(
        account_code="COST_FIXED_RENT",
        account_name="법인임차료",
        statement_line="FIXED_SGNA",
        waterfall_step="OPERATING_PROFIT",
        cost_category="FIXED_COST",
        sign_multiplier=-1,
        is_subtotal=False,
    ),
    AccountMappingRule(
        account_code="COST_FIXED_LABOR",
        account_name="법인인건비",
        statement_line="FIXED_SGNA",
        waterfall_step="OPERATING_PROFIT",
        cost_category="FIXED_COST",
        sign_multiplier=-1,
        is_subtotal=False,
    ),
)


def get_default_account_mapping() -> dict[str, AccountMappingRule]:
    """Return a copy of the default account mapping dictionary."""
    return {rule.account_code: rule for rule in DEFAULT_ACCOUNT_MAPPING_RULES}


def normalize_number_format(number_format: str | None) -> str:
    """Normalize number format aliases to canonical 'polish' or 'us' format."""
    raw = str(number_format or "").strip().lower()
    if raw in ("", "polish", "eu", "european", "1 234,56", "1.234,56"):
        return "polish"
    if raw in ("us", "en", "standard", "1,234.56"):
        return "us"
    raise DwhConfigError(
        f"Unsupported number_format {number_format!r}. "
        "Allowed values: 'polish'/'eu'/'european'/'1 234,56'/'1.234,56' or 'us'/'en'/'standard'/'1,234.56'."
    )


@dataclass
class EntityIsolationConfig:
    """Defines entity-level data isolation, currency rules, and FX conversion rates."""

    entity_code: str = "PL"
    allowed_currencies: tuple[str, ...] = ("PLN", "EUR")
    reporting_currency: str = "PLN"
    number_format: str = "polish"
    fx_rates: dict[str, dict[str, float]] = field(default_factory=_default_fx_rates)
    allow_default_fx_fallback: bool = False

    @classmethod
    def for_demo(
        cls,
        entity_code: str = "PL",
        reporting_currency: str = "PLN",
        allowed_currencies: tuple[str, ...] = ("PLN", "EUR"),
        number_format: str = "polish",
    ) -> EntityIsolationConfig:
        """Create an EntityIsolationConfig populated with synthetic demo FX rates."""
        rep = str(reporting_currency).strip().upper()
        demo_fx: dict[str, dict[str, float]] = {}
        for period_key, rate_map in DEMO_SAMPLE_FX_RATES.items():
            pivot = rate_map.get(rep, 1.0)
            demo_fx[period_key] = {
                c: (1.0 if c == rep else round(r / pivot, 8))
                for c, r in rate_map.items()
            }
        return cls(
            entity_code=entity_code,
            allowed_currencies=allowed_currencies,
            reporting_currency=rep,
            number_format=number_format,
            fx_rates=demo_fx,
            allow_default_fx_fallback=False,
        )

    def __post_init__(self) -> None:
        self.entity_code = _validate_entity_code(self.entity_code)

        if isinstance(self.allowed_currencies, str):
            currencies = tuple(
                c.strip().upper() for c in self.allowed_currencies.split(",") if c.strip()
            )
        else:
            currencies = tuple(
                str(c).strip().upper() for c in self.allowed_currencies if str(c).strip()
            )
        if not currencies:
            raise DwhConfigError("allowed_currencies must contain at least one currency.")
        self.allowed_currencies = currencies

        self.reporting_currency = str(self.reporting_currency).strip().upper()
        if self.reporting_currency not in self.allowed_currencies:
            raise DwhConfigError(
                f"reporting_currency {self.reporting_currency!r} must be in allowed_currencies {self.allowed_currencies}"
            )

        self.number_format = normalize_number_format(self.number_format)
        self.allow_default_fx_fallback = _parse_bool(self.allow_default_fx_fallback)

        normalized_fx: dict[str, dict[str, float]] = {}
        for period_key, rate_map in (self.fx_rates or {}).items():
            raw_period = str(period_key).strip()
            if raw_period.upper() == "DEFAULT":
                norm_periods = ["DEFAULT"]
            else:
                norm_periods = _expand_single_period_token(raw_period)
            for norm_period in norm_periods:
                normalized_fx.setdefault(norm_period, {})
                for curr_key, rate_val in (rate_map or {}).items():
                    norm_curr = str(curr_key).strip().upper()
                    try:
                        numeric_rate = float(rate_val)
                    except (TypeError, ValueError) as exc:
                        raise DwhConfigError(
                            f"Invalid FX rate {rate_val!r} for period {norm_period!r}, currency {norm_curr!r}"
                        ) from exc
                    if not math.isfinite(numeric_rate) or numeric_rate <= 0:
                        raise DwhConfigError(
                            f"FX rate must be finite and positive for period {norm_period!r}, "
                            f"currency {norm_curr!r}: {numeric_rate}"
                        )
                    normalized_fx[norm_period][norm_curr] = numeric_rate

                rep_rate = normalized_fx[norm_period].get(self.reporting_currency)
                if rep_rate is not None and rep_rate != 1.0:
                    raise DwhConfigError(
                        f"FX rate for reporting_currency {self.reporting_currency!r} in period "
                        f"{norm_period!r} must be 1.0, got {rep_rate}"
                    )
        self.fx_rates = normalized_fx

    def get_fx_rate(self, period: str, currency: str) -> float:
        """Return FX conversion rate to reporting_currency for a given period and currency."""
        norm_curr = str(currency).strip().upper()
        raw_period = str(period).strip()
        if not raw_period:
            raise DwhConfigError("Period must not be empty when resolving FX rate.")
        expanded = _expand_single_period_token(raw_period)
        if len(expanded) != 1:
            raise DwhConfigError(f"FX rate lookup requires a single YYYY-MM period, got {period!r}")
        norm_period = expanded[0]

        if not norm_curr or norm_curr not in self.allowed_currencies:
            raise DwhConfigError(
                f"Currency {norm_curr!r} is not allowed for entity {self.entity_code!r}. "
                f"Allowed: {self.allowed_currencies}"
            )

        period_rates = self.fx_rates.get(norm_period, {})
        rate = period_rates.get(norm_curr)
        if rate is None and self.allow_default_fx_fallback:
            default_rates = self.fx_rates.get("DEFAULT", {})
            rate = default_rates.get(norm_curr)

        if rate is None and norm_curr == self.reporting_currency:
            rate = 1.0

        if rate is None:
            raise DwhConfigError(
                f"No FX rate configured for currency {norm_curr!r} in period {norm_period!r}."
            )
        numeric_rate = float(rate)
        if not math.isfinite(numeric_rate) or numeric_rate <= 0:
            raise DwhConfigError(
                f"FX rate must be finite and greater than 0 for currency {norm_curr!r} "
                f"in period {norm_period!r}, got {rate}"
            )
        if norm_curr == self.reporting_currency and numeric_rate != 1.0:
            raise DwhConfigError(
                f"FX rate for reporting_currency {self.reporting_currency!r} must be 1.0, got {numeric_rate}"
            )
        return numeric_rate



@dataclass
class MartWorkspacePaths:
    """Standardized workspace directory layout for Staging, Warehouse, Serving, and Audit Logs."""

    root_dir: Path

    def __post_init__(self) -> None:
        self.root_dir = Path(self.root_dir)

    @property
    def staging_dir(self) -> Path:
        return self.root_dir / "staging"

    @property
    def warehouse_dir(self) -> Path:
        return self.root_dir / "warehouse"

    @property
    def warehouse_db_path(self) -> Path:
        return self.warehouse_dir / "warehouse.duckdb"

    @property
    def serving_dir(self) -> Path:
        return self.root_dir / "serving"

    @property
    def logs_dir(self) -> Path:
        return self.root_dir / "logs"

    @property
    def unmapped_log_path(self) -> Path:
        return self.logs_dir / "unmapped_codes.csv"

    @property
    def audit_log_path(self) -> Path:
        return self.logs_dir / "pipeline_audit.json"

    def ensure_dirs(self) -> None:
        """Create all workspace directories if they do not already exist."""
        for directory in (
            self.root_dir,
            self.staging_dir,
            self.warehouse_dir,
            self.serving_dir,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_entity(
        cls,
        entity_code: str = "PL",
        base_dir: Path | str | None = None,
    ) -> MartWorkspacePaths:
        """Build isolated workspace paths for a specific entity."""
        validated_entity = _validate_entity_code(entity_code)
        clean_entity = validated_entity.lower()
        folder_name = f"dwh_{clean_entity}"
        if base_dir is None:
            root = get_default_storage_dir() / folder_name
        else:
            base_path = Path(base_dir)
            root = base_path if base_path.name.lower() == folder_name else base_path / folder_name
        return cls(root_dir=root)


def _expand_single_period_token(token: str) -> list[str]:
    clean = token.strip().upper()
    if not clean:
        raise DwhConfigError("Empty period token in period specification.")

    # Full year: "2026"
    if len(clean) == 4 and clean.isdigit():
        year = int(clean)
        if not (1900 <= year <= 2100):
            raise DwhConfigError(f"Year out of valid range (1900-2100): {token!r}")
        return [f"{year:04d}-{month:02d}" for month in range(1, 13)]

    # Quarter: "2026-Q1" or "2026Q1"
    quarter_normalized = clean.replace("_", "-").replace("/", "-")
    if "-Q" in quarter_normalized or (len(quarter_normalized) == 6 and quarter_normalized[4] == "Q"):
        if "-Q" in quarter_normalized:
            parts = quarter_normalized.split("-Q", 1)
        else:
            parts = [quarter_normalized[:4], quarter_normalized[5:]]
        if len(parts) == 2 and len(parts[0]) == 4 and parts[0].isdigit() and parts[1] in ("1", "2", "3", "4"):
            year = int(parts[0])
            if not (1900 <= year <= 2100):
                raise DwhConfigError(f"Year out of valid range (1900-2100): {token!r}")
            quarter = int(parts[1])
            start_month = (quarter - 1) * 3 + 1
            return [f"{year:04d}-{month:02d}" for month in range(start_month, start_month + 3)]
        raise DwhConfigError(f"Invalid quarter period specification: {token!r}")

    # Year-month: "2026-01", "202601", "2026/01", "2026.01"
    ym_normalized = clean.replace("/", "-").replace(".", "-")
    if len(ym_normalized) == 7 and ym_normalized[4] == "-":
        year_str, month_str = ym_normalized[:4], ym_normalized[5:]
    elif len(ym_normalized) == 6 and ym_normalized.isdigit():
        year_str, month_str = ym_normalized[:4], ym_normalized[4:]
    else:
        raise DwhConfigError(f"Invalid period filter format: {token!r}")

    if not (year_str.isdigit() and month_str.isdigit()):
        raise DwhConfigError(f"Invalid period filter format: {token!r}")

    year = int(year_str)
    month = int(month_str)
    if not (1900 <= year <= 2100):
        raise DwhConfigError(f"Year out of valid range (1900-2100): {token!r}")
    if not (1 <= month <= 12):
        raise DwhConfigError(f"Month out of valid range (01-12): {token!r}")

    return [f"{year:04d}-{month:02d}"]


def expand_period_filter(period_spec: str | None) -> list[str] | None:
    """Expand a period specification ('ALL', '2026-Q1', '2026', '2026-01', '202601') into YYYY-MM strings."""
    if period_spec is None:
        return None

    clean = str(period_spec).strip()
    if not clean or clean.upper() in ("ALL", "*"):
        return None

    tokens = [part.strip() for part in clean.split(",") if part.strip()]
    if not tokens:
        return None

    expanded: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token.upper() in ("ALL", "*"):
            return None
        for ym in _expand_single_period_token(token):
            if ym not in seen:
                seen.add(ym)
                expanded.append(ym)
    return expanded


def load_account_mapping(path: Path | str) -> dict[str, AccountMappingRule]:
    """Load and validate AccountMappingRule entries from a CSV or JSON file."""
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise DwhConfigError(f"Account mapping file not found: {file_path}")

    suffix = file_path.suffix.lower()
    raw_rows: list[Mapping[str, Any]] = []

    if suffix == ".csv":
        try:
            with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames is None:
                    raise DwhConfigError(f"Account mapping CSV has no header: {file_path}")
                header_cols = {col.strip() for col in reader.fieldnames if col}
                missing = [col for col in REQUIRED_ACCOUNT_MAPPING_COLUMNS if col not in header_cols]
                if missing:
                    raise DwhConfigError(
                        f"Account mapping CSV missing required columns {missing} in {file_path}"
                    )
                for row in reader:
                    raw_rows.append({(k.strip() if k else ""): v for k, v in row.items()})
        except OSError as exc:
            raise DwhConfigError(f"Failed to read account mapping CSV {file_path}: {exc}") from exc
    elif suffix == ".json":
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DwhConfigError(f"Failed to parse account mapping JSON {file_path}: {exc}") from exc

        if isinstance(payload, dict):
            if "rules" in payload and isinstance(payload["rules"], list):
                items = payload["rules"]
            else:
                items = [
                    {"account_code": k, **v} if isinstance(v, dict) else v
                    for k, v in payload.items()
                ]
        elif isinstance(payload, list):
            items = payload
        else:
            raise DwhConfigError(f"Unsupported JSON structure in account mapping file: {file_path}")

        for item in items:
            if not isinstance(item, Mapping):
                raise DwhConfigError(f"Invalid mapping entry in {file_path}: {item!r}")
            missing = [col for col in REQUIRED_ACCOUNT_MAPPING_COLUMNS if col not in item]
            if missing:
                raise DwhConfigError(
                    f"Account mapping JSON entry missing required fields {missing} in {file_path}"
                )
            raw_rows.append(item)
    else:
        raise DwhConfigError(
            f"Unsupported account mapping file extension {suffix!r} (expected .csv or .json)"
        )

    if not raw_rows:
        raise DwhConfigError(f"Account mapping file contains no rules: {file_path}")

    rules: dict[str, AccountMappingRule] = {}
    for idx, row in enumerate(raw_rows, start=1):
        try:
            rule = AccountMappingRule(
                account_code=str(row.get("account_code", "")).strip(),
                account_name=str(row.get("account_name", "")).strip(),
                statement_line=str(row.get("statement_line", "")).strip(),
                waterfall_step=str(row.get("waterfall_step", "")).strip(),
                cost_category=str(row.get("cost_category", "")).strip(),
                sign_multiplier=_parse_sign_multiplier(row.get("sign_multiplier", 1)),
                is_subtotal=_parse_bool(row.get("is_subtotal", False)),
            )
        except DwhConfigError as exc:
            raise DwhConfigError(f"Row {idx} in {file_path.name}: {exc}") from exc

        if rule.account_code in rules:
            raise DwhConfigError(
                f"Duplicate account_code {rule.account_code!r} at row {idx} in {file_path.name}"
            )
        rules[rule.account_code] = rule

    return rules


def save_account_mapping_json(
    path: Path | str,
    mapping_rules: Mapping[str, AccountMappingRule] | Iterable[AccountMappingRule],
) -> None:
    """Persist account mapping rules atomically as deterministic JSON."""
    items = (
        list(mapping_rules.values())
        if isinstance(mapping_rules, Mapping)
        else list(mapping_rules)
    )
    serialized = [asdict(rule) for rule in sorted(items, key=lambda r: r.account_code)]
    atomic_write_text(
        Path(path),
        json.dumps({"rules": serialized}, ensure_ascii=False, indent=2),
    )


def load_fx_rates(path: Path | str) -> dict[str, dict[str, float]]:
    """Load and validate period FX conversion rates from a CSV (`period,currency_code,fx_rate`) or JSON file."""
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise DwhConfigError(f"FX rates file not found: {file_path}")

    suffix = file_path.suffix.lower()
    fx_rates: dict[str, dict[str, float]] = {}

    if suffix == ".csv":
        try:
            with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames is None:
                    raise DwhConfigError(f"FX rates CSV has no header: {file_path}")
                header_map = {col.strip().lower(): col for col in reader.fieldnames if col}
                period_key = header_map.get("period")
                curr_key = header_map.get("currency_code") or header_map.get("currency")
                rate_key = header_map.get("fx_rate") or header_map.get("rate")
                if not (period_key and curr_key and rate_key):
                    raise DwhConfigError(
                        f"FX rates CSV must contain period, currency_code, and fx_rate columns: {file_path}"
                    )
                for idx, row in enumerate(reader, start=1):
                    raw_period = str(row.get(period_key, "")).strip()
                    raw_curr = str(row.get(curr_key, "")).strip().upper()
                    raw_rate = str(row.get(rate_key, "")).strip()
                    if not raw_period and not raw_curr and not raw_rate:
                        continue
                    if not raw_period or not raw_curr or not raw_rate:
                        raise DwhConfigError(
                            f"Incomplete FX rate row {idx} in {file_path.name}: {row!r}"
                        )
                    norm_periods = (
                        ["DEFAULT"]
                        if raw_period.upper() == "DEFAULT"
                        else _expand_single_period_token(raw_period)
                    )
                    try:
                        numeric_rate = float(raw_rate.replace(",", "."))
                    except ValueError as exc:
                        raise DwhConfigError(
                            f"Invalid FX rate {raw_rate!r} at row {idx} in {file_path.name}"
                        ) from exc
                    if not math.isfinite(numeric_rate) or numeric_rate <= 0:
                        raise DwhConfigError(
                            f"FX rate must be finite and positive at row {idx} in {file_path.name}: {numeric_rate}"
                        )
                    for norm_p in norm_periods:
                        fx_rates.setdefault(norm_p, {})[raw_curr] = numeric_rate
        except OSError as exc:
            raise DwhConfigError(f"Failed to read FX rates CSV {file_path}: {exc}") from exc
    elif suffix == ".json":
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DwhConfigError(f"Failed to parse FX rates JSON {file_path}: {exc}") from exc

        if not isinstance(payload, Mapping):
            raise DwhConfigError(f"FX rates JSON must be an object mapping period -> currency -> rate: {file_path}")
        for period_key, rate_map in payload.items():
            if not isinstance(rate_map, Mapping):
                raise DwhConfigError(
                    f"Invalid FX rate mapping for period {period_key!r} in {file_path.name}"
                )
            raw_period = str(period_key).strip()
            norm_periods = (
                ["DEFAULT"]
                if raw_period.upper() == "DEFAULT"
                else _expand_single_period_token(raw_period)
            )
            for curr_key, rate_val in rate_map.items():
                norm_curr = str(curr_key).strip().upper()
                try:
                    numeric_rate = float(rate_val)
                except (TypeError, ValueError) as exc:
                    raise DwhConfigError(
                        f"Invalid FX rate {rate_val!r} for {raw_period}/{norm_curr} in {file_path.name}"
                    ) from exc
                if not math.isfinite(numeric_rate) or numeric_rate <= 0:
                    raise DwhConfigError(
                        f"FX rate must be finite and positive for {raw_period}/{norm_curr} in {file_path.name}"
                    )
                for norm_p in norm_periods:
                    fx_rates.setdefault(norm_p, {})[norm_curr] = numeric_rate
    else:
        raise DwhConfigError(
            f"Unsupported FX rates file extension {suffix!r} (expected .csv or .json)"
        )

    if not fx_rates:
        raise DwhConfigError(f"FX rates file contains no valid entries: {file_path}")
    return fx_rates


def compute_mart_fingerprint(
    config: EntityIsolationConfig,
    mapping_rules: Mapping[str, AccountMappingRule],
) -> str:
    """Compute a deterministic SHA-256 fingerprint of entity isolation config and account mapping rules."""
    sorted_fx = {
        period: {curr: float(rate) for curr, rate in sorted(rates.items())}
        for period, rates in sorted(config.fx_rates.items())
    }
    sorted_rules = [
        {
            "account_code": rule.account_code,
            "account_name": rule.account_name,
            "statement_line": rule.statement_line,
            "waterfall_step": rule.waterfall_step,
            "cost_category": rule.cost_category,
            "sign_multiplier": int(rule.sign_multiplier),
            "is_subtotal": bool(rule.is_subtotal),
        }
        for _, rule in sorted(mapping_rules.items(), key=lambda item: item[0])
    ]
    payload = {
        "entity_code": config.entity_code,
        "allowed_currencies": list(config.allowed_currencies),
        "reporting_currency": config.reporting_currency,
        "number_format": config.number_format,
        "allow_default_fx_fallback": bool(config.allow_default_fx_fallback),
        "fx_rates": sorted_fx,
        "mapping_rules": sorted_rules,
    }
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

