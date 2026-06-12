"""Settings and administration endpoints.

Single-instance admin surface for editable overrides, master-data files and
system diagnostics. File writes are validated and atomic so a broken browser
save cannot leave the runtime with half-written JSON/YAML.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.api.job_store import job_store
from src.export.feedback_store import FeedbackStore
from src.scoring.threshold_manager import load_scoring_config, validate_contract
from src.transform.master_data_matcher import (
    get_coating_catalog,
    get_manufacturer_catalog,
    get_material_catalog,
    get_nitriding_catalog,
    get_parts_group_catalog,
)

router = APIRouter(prefix="/settings")

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_DIR = _PROJECT_ROOT / "config"
_MASTER_DATA_DIR = _CONFIG_DIR / "master_data"
_APP_CONFIG_PATH = _CONFIG_DIR / "app_config.yaml"
_OVERRIDES_PATH = _CONFIG_DIR / "overrides.yaml"

_MASTER_DATA_FILES = {
    "materials": _MASTER_DATA_DIR / "materials.json",
    "units": _MASTER_DATA_DIR / "units.json",
    "validation_rules": _MASTER_DATA_DIR / "validation_rules.json",
}

_STATE: dict[str, float | None] = {"last_reload_at": None}


class TextSaveRequest(BaseModel):
    content: str = ""


class JsonSaveRequest(BaseModel):
    content: dict[str, Any]


class MaterialSaveRequest(BaseModel):
    material: dict[str, Any] = Field(default_factory=dict)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(f"{path.suffix}.{int(time.time())}.bak")
        shutil.copy2(path, backup)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise HTTPException(status_code=404, detail="Master-data file not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=422, detail="Master-data root must be an object"
        )
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    json.loads(rendered)
    _atomic_write(path, rendered)


def _catalog_path(catalog: str) -> Path:
    path = _MASTER_DATA_FILES.get(catalog)
    if path is None:
        raise HTTPException(status_code=404, detail="Unknown master-data catalog")
    return path


def _validate_yaml_mapping(content: str) -> dict[str, Any]:
    if not content.strip():
        return {}
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid YAML: {exc}") from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail="YAML root must be an object")
    return parsed


def _validate_material(material: dict[str, Any]) -> str:
    canonical = str(material.get("canonical") or "").strip()
    if not canonical:
        raise HTTPException(status_code=422, detail="Material requires canonical")
    aliases = material.get("aliases", [])
    if aliases is not None and not isinstance(aliases, list):
        raise HTTPException(status_code=422, detail="Material aliases must be a list")
    hardness = material.get("typical_hardness_hrc")
    if hardness is not None and (
        not isinstance(hardness, list)
        or len(hardness) != 2
        or not all(isinstance(v, int | float) for v in hardness)
    ):
        raise HTTPException(
            status_code=422,
            detail="typical_hardness_hrc must be null or [min, max]",
        )
    return canonical


def _clear_runtime_caches() -> None:
    get_material_catalog.cache_clear()
    get_nitriding_catalog.cache_clear()
    get_coating_catalog.cache_clear()
    get_parts_group_catalog.cache_clear()
    get_manufacturer_catalog.cache_clear()


@router.get("/config")
def get_config() -> dict[str, Any]:
    return {
        "app_config_yaml": _read_text(_APP_CONFIG_PATH),
        "overrides_yaml": _read_text(_OVERRIDES_PATH),
        "overrides_exists": _OVERRIDES_PATH.exists(),
        "last_reload_at": _STATE["last_reload_at"],
        "paths": {
            "app_config": str(_APP_CONFIG_PATH.relative_to(_PROJECT_ROOT)),
            "overrides": str(_OVERRIDES_PATH.relative_to(_PROJECT_ROOT)),
        },
    }


@router.put("/config/overrides")
def save_overrides(request: TextSaveRequest) -> dict[str, Any]:
    parsed = _validate_yaml_mapping(request.content)
    _atomic_write(_OVERRIDES_PATH, request.content.rstrip() + "\n")
    return {"ok": True, "keys": sorted(parsed.keys()), "path": "config/overrides.yaml"}


@router.post("/reload")
def reload_settings() -> dict[str, Any]:
    _validate_yaml_mapping(_read_text(_APP_CONFIG_PATH))
    _validate_yaml_mapping(_read_text(_OVERRIDES_PATH))
    _clear_runtime_caches()
    _STATE["last_reload_at"] = time.time()
    return {
        "ok": True,
        "reloaded_at": _STATE["last_reload_at"],
        "cleared_caches": [
            "material_catalog",
            "nitriding_catalog",
            "coating_catalog",
            "parts_group_catalog",
            "manufacturer_catalog",
        ],
    }


@router.get("/master-data")
def list_master_data() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for catalog, path in _MASTER_DATA_FILES.items():
        data = _read_json(path) if path.exists() else {}
        if catalog == "materials":
            entry_count = len(data.get("materials", []))
        elif catalog == "units":
            entry_count = sum(1 for value in data.values() if isinstance(value, dict))
        else:
            entry_count = len(data.get("field_rules", {}))
        rows.append(
            {
                "catalog": catalog,
                "filename": str(path.relative_to(_PROJECT_ROOT)),
                "exists": path.exists(),
                "updated_at": path.stat().st_mtime if path.exists() else None,
                "entry_count": entry_count,
            }
        )
    return rows


@router.get("/master-data/{catalog}")
def get_master_data(catalog: str) -> dict[str, Any]:
    path = _catalog_path(catalog)
    return {
        "catalog": catalog,
        "filename": str(path.relative_to(_PROJECT_ROOT)),
        "content": _read_json(path),
    }


@router.put("/master-data/{catalog}")
def save_master_data(catalog: str, request: JsonSaveRequest) -> dict[str, Any]:
    path = _catalog_path(catalog)
    _write_json(path, request.content)
    _clear_runtime_caches()
    return {"ok": True, "catalog": catalog}


@router.post("/master-data/materials")
def create_material(request: MaterialSaveRequest) -> dict[str, Any]:
    path = _catalog_path("materials")
    data = _read_json(path)
    materials = data.setdefault("materials", [])
    if not isinstance(materials, list):
        raise HTTPException(status_code=422, detail="materials must be a list")
    canonical = _validate_material(request.material)
    if any(
        str(m.get("canonical") or "") == canonical
        for m in materials
        if isinstance(m, dict)
    ):
        raise HTTPException(status_code=409, detail="Material already exists")
    materials.append(request.material)
    _write_json(path, data)
    _clear_runtime_caches()
    return {"ok": True, "canonical": canonical}


@router.put("/master-data/materials/{canonical}")
def update_material(canonical: str, request: MaterialSaveRequest) -> dict[str, Any]:
    path = _catalog_path("materials")
    data = _read_json(path)
    materials = data.get("materials", [])
    if not isinstance(materials, list):
        raise HTTPException(status_code=422, detail="materials must be a list")
    next_canonical = _validate_material(request.material)
    for index, material in enumerate(materials):
        if (
            isinstance(material, dict)
            and str(material.get("canonical") or "") == canonical
        ):
            if next_canonical != canonical and any(
                str(m.get("canonical") or "") == next_canonical
                for i, m in enumerate(materials)
                if i != index and isinstance(m, dict)
            ):
                raise HTTPException(status_code=409, detail="Material already exists")
            materials[index] = request.material
            _write_json(path, data)
            _clear_runtime_caches()
            return {"ok": True, "canonical": next_canonical}
    raise HTTPException(status_code=404, detail="Material not found")


@router.delete("/master-data/materials/{canonical}")
def delete_material(canonical: str) -> dict[str, Any]:
    path = _catalog_path("materials")
    data = _read_json(path)
    materials = data.get("materials", [])
    if not isinstance(materials, list):
        raise HTTPException(status_code=422, detail="materials must be a list")
    kept = [
        material
        for material in materials
        if not (
            isinstance(material, dict)
            and str(material.get("canonical") or "") == canonical
        )
    ]
    if len(kept) == len(materials):
        raise HTTPException(status_code=404, detail="Material not found")
    data["materials"] = kept
    _write_json(path, data)
    _clear_runtime_caches()
    return {"ok": True, "canonical": canonical}


@router.get("/system")
def system_info() -> dict[str, Any]:
    jobs = job_store.list_summaries(include_archived=True)
    feedback = FeedbackStore()
    env = {
        "azure_openai_endpoint_configured": bool(os.getenv("AZURE_OPENAI_ENDPOINT")),
        "azure_openai_key_configured": bool(os.getenv("AZURE_OPENAI_KEY")),
        "azure_openai_api_version": os.getenv("AZURE_OPENAI_API_VERSION", ""),
        "deployment_main": os.getenv("AZURE_OPENAI_DEPLOYMENT_MAIN", ""),
        "deployment_mini": os.getenv("AZURE_OPENAI_DEPLOYMENT_MINI", ""),
    }
    files = {
        "app_config": _APP_CONFIG_PATH.exists(),
        "overrides": _OVERRIDES_PATH.exists(),
        **{catalog: path.exists() for catalog, path in _MASTER_DATA_FILES.items()},
    }
    return {
        "app_version": "1.0.0",
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "project_root": str(_PROJECT_ROOT),
        "last_reload_at": _STATE["last_reload_at"],
        "jobs": {
            "total": len(jobs),
            "active": sum(1 for job in jobs if not job.archived),
            "archived": sum(1 for job in jobs if job.archived),
            "completed": sum(1 for job in jobs if job.status == "completed"),
        },
        "corrections": feedback.correction_count(),
        "files": files,
        "azure_openai": env,
        "scoring_contract_deviations": validate_contract(load_scoring_config()),
    }
