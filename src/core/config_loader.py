"""Small YAML config loader with optional instance overrides."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
_APP_CONFIG_PATH = _CONFIG_DIR / "app_config.yaml"
_OVERRIDES_PATH = _CONFIG_DIR / "overrides.yaml"


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be an object: {path}")
    return data


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overrides.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_app_config(*, include_overrides: bool = True) -> dict[str, Any]:
    """Load app_config.yaml and merge config/overrides.yaml when present."""
    base = _load_yaml_mapping(_APP_CONFIG_PATH)
    if not include_overrides:
        return base
    overrides = _load_yaml_mapping(_OVERRIDES_PATH)
    return _deep_merge(base, overrides)
