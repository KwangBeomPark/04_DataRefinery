"""Preset management for dataset aggregation configurations."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.data_aggregator import (
    AggregationSpec,
    ColumnGroupRule,
    DerivedFormulaRule,
    FilterCondition,
)
from src.update_checker import application_data_directory

PRESET_SCHEMA_VERSION = 1


class PresetError(Exception):
    """Base exception for preset operations."""


class PresetValidationError(PresetError):
    """Raised when a preset JSON has invalid schema or missing required fields."""


@dataclass
class AggregationPreset:
    name: str
    description: str = ""
    version: int = PRESET_SCHEMA_VERSION
    group_by_keys: List[str] = field(default_factory=list)
    measure_sums: List[str] = field(default_factory=list)
    column_groups: List[ColumnGroupRule] = field(default_factory=list)
    derived_formulas: List[DerivedFormulaRule] = field(default_factory=list)
    filters: List[FilterCondition] = field(default_factory=list)
    rollup_annual: bool = False
    month_column: Optional[str] = None
    annual_column_name: str = "연도"
    output_format: str = "xlsx"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at or datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "group_by_keys": list(self.group_by_keys),
            "measure_sums": list(self.measure_sums),
            "column_groups": [asdict(cg) for cg in self.column_groups],
            "derived_formulas": [asdict(df) for df in self.derived_formulas],
            "filters": [asdict(f) for f in self.filters],
            "rollup_annual": self.rollup_annual,
            "month_column": self.month_column,
            "annual_column_name": self.annual_column_name,
            "output_format": self.output_format,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AggregationPreset:
        if not isinstance(data, dict):
            raise PresetValidationError("Preset data must be a JSON object.")

        name = data.get("name", "").strip()
        if not name:
            raise PresetValidationError("Preset 'name' is required.")

        version = data.get("version", 1)
        description = data.get("description", "")
        group_by_keys = list(data.get("group_by_keys", []))
        measure_sums = list(data.get("measure_sums", []))

        # Column groups
        raw_cgs = data.get("column_groups", [])
        column_groups = [
            ColumnGroupRule(
                new_column=item["new_column"],
                source_columns=list(item.get("source_columns", [])),
            )
            for item in raw_cgs
            if isinstance(item, dict) and "new_column" in item
        ]

        # Derived formulas
        raw_dfs = data.get("derived_formulas", [])
        derived_formulas = [
            DerivedFormulaRule(
                new_column=item["new_column"],
                numerator_column=item["numerator_column"],
                denominator_column=item["denominator_column"],
                multiplier=float(item.get("multiplier", 100.0)),
                format_type=item.get("format_type", "percent"),
            )
            for item in raw_dfs
            if isinstance(item, dict) and "new_column" in item and "numerator_column" in item and "denominator_column" in item
        ]

        # Filters
        raw_filters = data.get("filters", [])
        filters = [
            FilterCondition(
                column=f["column"],
                operator=f.get("operator", "=="),
                value=f.get("value"),
            )
            for f in raw_filters
            if isinstance(f, dict) and "column" in f
        ]

        return cls(
            name=name,
            description=description,
            version=version,
            group_by_keys=group_by_keys,
            measure_sums=measure_sums,
            column_groups=column_groups,
            derived_formulas=derived_formulas,
            filters=filters,
            rollup_annual=bool(data.get("rollup_annual", False)),
            month_column=data.get("month_column"),
            annual_column_name=data.get("annual_column_name", "연도"),
            output_format=data.get("output_format", "xlsx"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def to_spec(self, file_path: str, output_path: Optional[str] = None) -> AggregationSpec:
        """Convert preset into a runnable AggregationSpec for a given file."""
        return AggregationSpec(
            file_path=file_path,
            group_by_keys=list(self.group_by_keys),
            measure_sums=list(self.measure_sums),
            column_groups=list(self.column_groups),
            derived_formulas=list(self.derived_formulas),
            filters=list(self.filters),
            rollup_annual=self.rollup_annual,
            month_column=self.month_column,
            annual_column_name=self.annual_column_name,
            output_format=self.output_format,
            output_path=output_path,
        )


def presets_directory() -> Path:
    """Return the user's preset storage directory under AppData."""
    p = application_data_directory() / "presets"
    p.mkdir(parents=True, exist_ok=True)
    return p


_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "_", name.strip())
    cleaned = cleaned.strip(". ")
    if cleaned.upper() in _WINDOWS_RESERVED_NAMES or not cleaned:
        cleaned = f"_{cleaned or 'preset'}"
    return cleaned[:100]


def _atomic_write_text(file_path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write text content atomically using a temporary file and os.replace."""
    temp_path = file_path.with_name(f"{file_path.name}.tmp_{os.getpid()}")
    try:
        temp_path.write_text(content, encoding=encoding)
        os.replace(temp_path, file_path)
    except Exception:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        raise


def preset_exists(name: str, dir_path: Optional[Path] = None) -> bool:
    """Check if a preset with the given name already exists."""
    target_dir = dir_path or presets_directory()
    filename = f"{_sanitize_filename(name)}.json"
    return (target_dir / filename).exists()


def list_presets(dir_path: Optional[Path] = None) -> List[AggregationPreset]:
    """Scan and list all valid presets in the preset folder."""
    target_dir = dir_path or presets_directory()
    if not target_dir.exists():
        return []

    presets: List[AggregationPreset] = []
    for json_file in sorted(target_dir.glob("*.json")):
        try:
            content = json.loads(json_file.read_text(encoding="utf-8"))
            preset = AggregationPreset.from_dict(content)
            presets.append(preset)
        except Exception:
            # Silently ignore corrupt or non-preset json files
            continue
    return presets


def save_preset(preset: AggregationPreset, dir_path: Optional[Path] = None) -> Path:
    """Save preset to local user directory atomically."""
    target_dir = dir_path or presets_directory()
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{_sanitize_filename(preset.name)}.json"
    file_path = target_dir / filename

    content = json.dumps(preset.to_dict(), ensure_ascii=False, indent=2)
    _atomic_write_text(file_path, content, encoding="utf-8")
    return file_path


def load_preset(name: str, dir_path: Optional[Path] = None) -> AggregationPreset:
    """Load a preset by name from the preset directory."""
    target_dir = dir_path or presets_directory()
    filename = f"{_sanitize_filename(name)}.json"
    file_path = target_dir / filename

    if not file_path.exists():
        raise FileNotFoundError(f"Preset not found: {name}")

    try:
        content = json.loads(file_path.read_text(encoding="utf-8"))
        return AggregationPreset.from_dict(content)
    except Exception as e:
        raise PresetValidationError(f"Failed to read preset '{name}': {e}") from e


def delete_preset(name: str, dir_path: Optional[Path] = None) -> bool:
    """Delete a preset by name."""
    target_dir = dir_path or presets_directory()
    filename = f"{_sanitize_filename(name)}.json"
    file_path = target_dir / filename

    if file_path.exists():
        file_path.unlink()
        return True
    return False


def export_preset_file(preset: AggregationPreset, export_path: str) -> str:
    """Export preset to an external JSON file for sharing atomically."""
    target = Path(export_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(preset.to_dict(), ensure_ascii=False, indent=2)
    _atomic_write_text(target, content, encoding="utf-8")
    return str(target.resolve())


def import_preset_file(
    import_path: str,
    save_to_local: bool = True,
    dir_path: Optional[Path] = None,
) -> AggregationPreset:
    """Import a shared JSON preset file, validating its structure."""
    source = Path(import_path)
    if not source.exists():
        raise FileNotFoundError(f"Import file not found: {import_path}")

    try:
        content = json.loads(source.read_text(encoding="utf-8"))
        preset = AggregationPreset.from_dict(content)
    except Exception as e:
        raise PresetValidationError(f"Invalid preset file: {e}") from e

    if save_to_local:
        save_preset(preset, dir_path=dir_path)

    return preset


def validate_preset_against_columns(
    preset: AggregationPreset,
    available_columns: Sequence[str],
) -> List[str]:
    """
    Check if all columns required by the preset exist in available_columns.
    Returns a list of missing column names (empty if all exist).
    """
    col_set = set(available_columns)
    missing: List[str] = []

    # Check GroupBy keys
    for k in preset.group_by_keys:
        if k not in col_set:
            missing.append(k)

    # Check Month column if rollup is requested
    if preset.rollup_annual and preset.month_column:
        if preset.month_column not in col_set:
            missing.append(preset.month_column)

    # Check Filters
    for f in preset.filters:
        if f.column not in col_set:
            missing.append(f.column)

    # Check measure sums
    for m in preset.measure_sums:
        if m not in col_set:
            missing.append(m)

    # Check column groups sources
    for cg in preset.column_groups:
        for sc in cg.source_columns:
            if sc not in col_set:
                missing.append(sc)

    # Check derived formulas sources (numerator / denominator)
    # Note: numerator or denominator could be a newly created column from column_groups
    created_columns = {cg.new_column for cg in preset.column_groups}
    for df in preset.derived_formulas:
        if df.numerator_column not in col_set and df.numerator_column not in created_columns:
            missing.append(df.numerator_column)
        if df.denominator_column not in col_set and df.denominator_column not in created_columns:
            missing.append(df.denominator_column)

    return list(dict.fromkeys(missing))
