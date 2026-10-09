"""Configuration and registry management for datasets in Data Refinery.

Stores dataset configurations in the user's local application data folder,
completely isolated from application source files and binary paths.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.app_paths import dataset_storage_directory


def get_default_storage_dir() -> Path:
    """Return UserSetting/datasets, migrating legacy settings and DBs once."""
    return dataset_storage_directory()


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
    include_keywords: List[str] = field(default_factory=list)
    exclude_keywords: List[str] = field(default_factory=list)
    keyword_mode: str = "or"  # "and" or "or"
    include_subfolders: bool = False
    excluded_files: List[str] = field(default_factory=list)
    baseline_columns: List[str] = field(default_factory=list)
    number_format: str = "auto"  # "auto", "1,234.56", "1 234,56", "1.234,56"
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
        include_keywords: Optional[List[str]] = None,
        exclude_keywords: Optional[List[str]] = None,
        keyword_mode: str = "or",
        include_subfolders: bool = False,
        excluded_files: Optional[List[str]] = None,
        baseline_columns: Optional[List[str]] = None,
        number_format: str = "auto",
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
            include_keywords=include_keywords or [],
            exclude_keywords=exclude_keywords or [],
            keyword_mode=keyword_mode,
            include_subfolders=include_subfolders,
            excluded_files=excluded_files or [],
            baseline_columns=baseline_columns or [],
            number_format=number_format,
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
            "include_keywords": sorted(self.include_keywords),
            "exclude_keywords": sorted(self.exclude_keywords),
            "keyword_mode": self.keyword_mode,
            "include_subfolders": self.include_subfolders,
            "excluded_files": sorted(self.excluded_files),
            "baseline_columns": self.baseline_columns,
            "number_format": self.number_format,
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
        try:
            return self._read_datasets_for_update()
        except Exception:
            return []

    def _read_datasets_for_update(self) -> List[DatasetDefinition]:
        """Unreadable existing definitions must never be replaced by an empty list."""
        try:
            with open(self._registry_file, "r", encoding="utf-8") as stream:
                data = json.load(stream)
        except FileNotFoundError:
            return []
        if not isinstance(data, list):
            raise ValueError("Dataset registry must contain a list of definitions.")
        return [DatasetDefinition.from_dict(item) for item in data]

    def get_dataset(self, dataset_id: str) -> Optional[DatasetDefinition]:
        for ds in self.list_datasets():
            if ds.id == dataset_id:
                return ds
        return None

    def save_dataset(self, dataset: DatasetDefinition) -> None:
        datasets = self._read_datasets_for_update()

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

        from src.atomic_write import atomic_write_json

        payload = [ds.to_dict() for ds in datasets]
        atomic_write_json(self._registry_file, payload)

    def delete_dataset(self, dataset_id: str) -> bool:
        datasets = self._read_datasets_for_update()
        new_list = [ds for ds in datasets if ds.id != dataset_id]
        if len(new_list) == len(datasets):
            return False

        from src.atomic_write import atomic_write_json

        payload = [ds.to_dict() for ds in new_list]
        atomic_write_json(self._registry_file, payload)

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
