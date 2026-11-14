"""Reusable data mart recipe definitions, presets, and JSON persistence."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from src.atomic_write import atomic_write_json

VALID_RECIPE_MODES = ("wide_raw", "wide_rollup", "long_detail")
VALID_ROLLUP_LEVELS = ("major", "medium")
VALID_UNMAPPED_POLICIES = ("fail", "other_bucket")


@dataclass(frozen=True)
class MartRecipe:
    name: str
    mode: str = "wide_raw"
    group_by: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    major_categories: tuple[str, ...] = ()
    medium_categories: tuple[str, ...] = ()
    measures: tuple[str, ...] = ()
    exclude_zero: bool = True
    apply_sign: bool = False
    include_non_monetary: tuple[str, ...] = ()
    rollup_level: str = "major"
    unmapped_policy: str = "fail"
    filters: tuple[tuple[str, str], ...] = ()
    period_start: str | None = None
    period_end: str | None = None


def validate_recipe(recipe: MartRecipe) -> MartRecipe:
    """Validate recipe parameters and reject invalid combinations."""
    if not isinstance(recipe.name, str) or not recipe.name.strip():
        raise ValueError("Recipe name is required")
    if recipe.mode not in VALID_RECIPE_MODES:
        raise ValueError(
            f"Unsupported recipe mode '{recipe.mode}'. Expected one of {VALID_RECIPE_MODES}"
        )
    if recipe.rollup_level not in VALID_ROLLUP_LEVELS:
        raise ValueError(
            f"Unsupported rollup_level '{recipe.rollup_level}'. Expected one of {VALID_ROLLUP_LEVELS}"
        )
    if recipe.unmapped_policy not in VALID_UNMAPPED_POLICIES:
        raise ValueError(
            f"Unsupported unmapped_policy '{recipe.unmapped_policy}'. Expected one of {VALID_UNMAPPED_POLICIES}"
        )
    if recipe.mode == "wide_raw" and not recipe.columns:
        raise ValueError("wide_raw recipe requires at least one output column")
    for pair in recipe.filters:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise ValueError("Each recipe filter must be a (column, value) pair")
    return recipe


def recipe_from_dict(data: dict) -> MartRecipe:
    """Parse a dictionary into a validated MartRecipe."""
    if not isinstance(data, dict):
        raise ValueError("Recipe definition must be a dict")
    raw_filters = data.get("filters") or ()
    filters = tuple((str(col), str(val)) for col, val in raw_filters)
    recipe = MartRecipe(
        name=str(data.get("name", "") or "").strip(),
        mode=str(data.get("mode", "wide_raw") or "wide_raw").strip(),
        group_by=tuple(str(x) for x in (data.get("group_by") or ()) if str(x)),
        columns=tuple(str(x) for x in (data.get("columns") or ()) if str(x)),
        major_categories=tuple(
            str(x) for x in (data.get("major_categories") or ()) if str(x)
        ),
        medium_categories=tuple(
            str(x) for x in (data.get("medium_categories") or ()) if str(x)
        ),
        measures=tuple(str(x) for x in (data.get("measures") or ()) if str(x)),
        exclude_zero=bool(data.get("exclude_zero", True)),
        apply_sign=bool(data.get("apply_sign", False)),
        include_non_monetary=tuple(
            str(x) for x in (data.get("include_non_monetary") or ()) if str(x)
        ),
        rollup_level=str(data.get("rollup_level", "major") or "major").strip(),
        unmapped_policy=str(data.get("unmapped_policy", "fail") or "fail").strip(),
        filters=filters,
        period_start=(str(data["period_start"]).strip() or None)
        if data.get("period_start") is not None
        else None,
        period_end=(str(data["period_end"]).strip() or None)
        if data.get("period_end") is not None
        else None,
    )
    return validate_recipe(recipe)


def recipe_to_dict(recipe: MartRecipe) -> dict:
    """Serialize MartRecipe cleanly for JSON/manifest storage."""
    data = asdict(recipe)
    data["filters"] = [list(pair) for pair in recipe.filters]
    return data


def build_default_recipes(model, mappings: Iterable = ()) -> list[MartRecipe]:
    """Build 3 practical preset recipes from a MartModel and optional account mappings."""
    if model is None:
        return []
    if isinstance(model, dict):
        period_col = str(model["period_column"])
        dims = model.get("dimensions", ())
        measures = [str(m["column"]) for m in model.get("measures", ())]
    else:
        period_col = str(model.period_column)
        dims = model.dimensions
        measures = [str(m.column) for m in model.measures]

    dim_keys: list[str] = [period_col]
    for dim in dims:
        keys = dim["key_columns"] if isinstance(dim, dict) else dim.key_columns
        if keys:
            first_key = str(keys[0])
            if first_key not in dim_keys:
                dim_keys.append(first_key)
    default_group = tuple(dim_keys)

    non_monetary = tuple(
        e.source_column
        for e in mappings
        if getattr(e, "measure_type", None) == "non_monetary"
    )[:1]

    raw_measure_slice = tuple(measures[: min(10, len(measures))])
    raw_cols = tuple(dict.fromkeys((*default_group, *raw_measure_slice)))

    recipes = [
        MartRecipe(
            name="손익_대분류_요약_Wide",
            mode="wide_rollup",
            rollup_level="major",
            group_by=default_group,
            include_non_monetary=non_monetary,
            exclude_zero=False,
            apply_sign=False,
        ),
        MartRecipe(
            name="원가_상세_계정전개_Long",
            mode="long_detail",
            group_by=default_group,
            exclude_zero=True,
            apply_sign=False,
        ),
    ]
    if raw_cols:
        recipes.append(
            MartRecipe(
                name="원본_주요지표_Wide",
                mode="wide_raw",
                group_by=default_group,
                columns=raw_cols,
                exclude_zero=False,
            )
        )
    return recipes


def load_recipes(workspace_root: Path) -> list[MartRecipe]:
    """Load saved MartRecipe definitions from workspace/recipes.json."""
    path = Path(workspace_root) / "recipes.json"
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        items = raw.get("recipes", [])
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError("Invalid recipes.json format")
    return [recipe_from_dict(item) for item in items]


def save_recipes(workspace_root: Path, recipes: Sequence[MartRecipe]) -> None:
    """Persist validated MartRecipe definitions atomically to workspace/recipes.json."""
    validated = [validate_recipe(r) for r in recipes]
    names = [r.name for r in validated]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate recipe name in recipes list")
    payload = {
        "version": 1,
        "recipes": [recipe_to_dict(r) for r in validated],
    }
    atomic_write_json(Path(workspace_root) / "recipes.json", payload)
