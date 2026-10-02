"""Configuration and registry management for datasets in Data Refinery.

Stores dataset configurations in the user's local application data folder,
completely isolated from application source files and binary paths.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def get_default_storage_dir() -> Path:
    """Return the base local storage path for dataset databases and metadata."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data) / "DataRefinery" / "datasets"
    else:
        app_data = os.environ.get("APPDATA")
        if app_data:
            base = Path(app_data) / "DataRefinery" / "datasets"
        else:
            base = Path.home() / ".datarefinery" / "datasets"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DatasetDefinition:
    id: str
    name: str
    input_folder: str
    publish_folder: str
    period_column: str = "기준년월"
    period_format: str = "Auto"
    column_types: Dict[str, str] = field(default_factory=dict)
    numeric_columns: List[str] = field(default_factory=list)
    key_columns: List[str] = field(default_factory=list)
    file_pattern: str = "*.csv"
    encoding: str = "auto"
    delimiter: str = ","
    merge_mode: str = "union"  # "union" or "error"
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)
    last_inspected_at: Optional[str] = None
    last_published_at: Optional[str] = None
    inspection_approved: bool = False
    approval_token: Optional[str] = None
    published_filename: Optional[str] = None

    def __post_init__(self):
        if not self.published_filename:
            slug = re.sub(r"[^\w\-_]", "_", self.name) or "dataset"
            self.published_filename = f"{slug}.csv"
        if Path(self.published_filename).name != self.published_filename:
            raise ValueError("배포 파일 이름에 폴더 경로를 사용할 수 없습니다.")

    def published_csv_path(self) -> Path:
        """Keep the connection address stable when the display name changes."""
        return Path(self.publish_folder) / "current" / self.published_filename

    @classmethod
    def create_new(
        cls,
        name: str,
        input_folder: str,
        publish_folder: str,
        period_column: str = "기준년월",
        period_format: str = "Auto",
        column_types: Optional[Dict[str, str]] = None,
        numeric_columns: Optional[List[str]] = None,
        key_columns: Optional[List[str]] = None,
        file_pattern: str = "*.csv",
        encoding: str = "auto",
        delimiter: str = ",",
        merge_mode: str = "union",
    ) -> DatasetDefinition:
        clean_name = re.sub(r"[^\w\-_]", "_", name.strip()) or "dataset"
        unique_id = f"{clean_name}_{uuid.uuid4().hex[:8]}"
        now = _utc_now_iso()
        return cls(
            id=unique_id,
            name=name.strip(),
            input_folder=str(Path(input_folder).resolve()) if input_folder else "",
            publish_folder=str(Path(publish_folder).resolve()) if publish_folder else "",
            period_column=period_column.strip(),
            period_format=period_format,
            column_types=column_types or {},
            numeric_columns=numeric_columns or [],
            key_columns=key_columns or [],
            file_pattern=file_pattern,
            encoding=encoding,
            delimiter=delimiter,
            merge_mode=merge_mode,
            created_at=now,
            updated_at=now,
        )

    def get_fingerprint(self) -> str:
        """Return deterministic hash of dataset structure and settings."""
        import hashlib
        payload = {
            "name": self.name,
            "period_column": self.period_column,
            "period_format": self.period_format,
            "column_types": sorted(self.column_types.items()),
            "numeric_columns": sorted(self.numeric_columns),
            "key_columns": sorted(self.key_columns),
            "file_pattern": self.file_pattern,
            "delimiter": self.delimiter,
            "encoding": self.encoding,
            "merge_mode": self.merge_mode,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DatasetDefinition:
        # Filter unknown keys for forward compatibility
        known = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


class DatasetRegistry:
    """Manages the lifecycle and metadata persistence of registered datasets."""

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = Path(storage_dir) if storage_dir else get_default_storage_dir()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._registry_file = self.storage_dir / "datasets_registry.json"

    def list_datasets(self) -> List[DatasetDefinition]:
        if not self._registry_file.exists():
            return []
        try:
            with open(self._registry_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [DatasetDefinition.from_dict(item) for item in data]
        except Exception:
            return []

    def get_dataset(self, dataset_id: str) -> Optional[DatasetDefinition]:
        for ds in self.list_datasets():
            if ds.id == dataset_id:
                return ds
        return None

    def save_dataset(self, dataset: DatasetDefinition) -> None:
        datasets = self.list_datasets()

        # Prevent duplicate publish_folder across different datasets
        if dataset.publish_folder:
            target_pub = Path(dataset.publish_folder).resolve()
            for existing in datasets:
                if existing.id != dataset.id and existing.publish_folder:
                    other_pub = Path(existing.publish_folder).resolve()
                    if other_pub == target_pub:
                        raise ValueError(
                            f"배포 대상 폴더('{dataset.publish_folder}')는 이미 다른 데이터셋('{existing.name}')에서 사용 중입니다.\n"
                            f"데이터 덮어쓰기 및 충돌을 방지하기 위해 서로 다른 배포 폴더를 지정해 주세요."
                        )

        dataset.updated_at = _utc_now_iso()
        found = False
        for i, existing in enumerate(datasets):
            if existing.id == dataset.id:
                datasets[i] = dataset
                found = True
                break
        if not found:
            datasets.append(dataset)

        # Make sure dataset subfolder exists for DuckDB and cache
        ds_dir = self.get_dataset_dir(dataset.id)
        ds_dir.mkdir(parents=True, exist_ok=True)

        # Atomically write registry JSON
        tmp_file = self._registry_file.with_suffix(".tmp")
        payload = [ds.to_dict() for ds in datasets]
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp_file.replace(self._registry_file)

    def delete_dataset(self, dataset_id: str) -> bool:
        datasets = self.list_datasets()
        new_list = [ds for ds in datasets if ds.id != dataset_id]
        if len(new_list) == len(datasets):
            return False

        tmp_file = self._registry_file.with_suffix(".tmp")
        payload = [ds.to_dict() for ds in new_list]
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp_file.replace(self._registry_file)

        # Optional: remove dataset workspace files
        ds_dir = self.get_dataset_dir(dataset_id)
        if ds_dir.exists():
            import shutil
            shutil.rmtree(ds_dir, ignore_errors=True)
        return True

    def get_dataset_dir(self, dataset_id: str) -> Path:
        return self.storage_dir / dataset_id

    def get_dataset_db_path(self, dataset_id: str) -> Path:
        return self.get_dataset_dir(dataset_id) / "workspace.duckdb"
