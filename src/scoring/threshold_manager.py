"""Threshold Manager — configurable Green/Yellow/Red classification.

Loads thresholds from app_config.yaml and classifies final scores
into traffic-light categories.
"""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path

from src.core.config_loader import load_app_config

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


class TrafficLight(str, Enum):
    GREEN = "green"  # Auto-accepted, high confidence
    YELLOW = "yellow"  # Suggestion shown, human review needed
    RED = "red"  # Manual entry, source value displayed
    NEUTRAL = "neutral"  # Intentionally empty optional field (N/A)
    MANUAL_CONFIRMED = "manual_confirmed"  # Human-confirmed override


class ScoringConfig:
    """Loaded scoring configuration."""

    __slots__ = (
        "green_threshold",
        "yellow_threshold",
        "enable_counter_check",
        "conservative_mode",
        "signal_weights",
        "verify_contract_enabled",
        "verify_green_threshold",
        "soft_green_floor",
        "green_extraction_min_confidence",
        "soft_vetoes_as_yellow",
        "empty_non_required_as_yellow",
        "empty_non_required_as_neutral",
    )

    def __init__(
        self,
        green_threshold: float = 0.90,
        yellow_threshold: float = 0.50,
        enable_counter_check: bool = True,
        conservative_mode: bool = True,
        signal_weights: dict[str, float] | None = None,
        verify_contract_enabled: bool = True,
        verify_green_threshold: float = 0.95,
        soft_green_floor: float = 0.70,
        green_extraction_min_confidence: float = 0.80,
        soft_vetoes_as_yellow: bool = False,
        empty_non_required_as_yellow: bool = False,
        empty_non_required_as_neutral: bool = False,
    ) -> None:
        self.green_threshold = green_threshold
        self.yellow_threshold = yellow_threshold
        self.enable_counter_check = enable_counter_check
        self.conservative_mode = conservative_mode
        self.verify_contract_enabled = verify_contract_enabled
        self.verify_green_threshold = verify_green_threshold
        self.soft_green_floor = soft_green_floor
        self.green_extraction_min_confidence = green_extraction_min_confidence
        self.soft_vetoes_as_yellow = soft_vetoes_as_yellow
        self.empty_non_required_as_yellow = empty_non_required_as_yellow
        self.empty_non_required_as_neutral = empty_non_required_as_neutral
        # Default weights: transform confidence 40%, rule-based 40%, counter-check 20%
        self.signal_weights = signal_weights or {
            "transform": 0.40,
            "rules": 0.40,
            "counter_check": 0.20,
        }

    def classify(self, final_score: float) -> TrafficLight:
        raise NotImplementedError(
            "Do not call ScoringConfig.classify() directly. "
            "It bypasses all safety gates (CHECK2–CHECK5, hard vetoes, validators). "
            "Use can_be_green() via ensemble_scorer instead."
        )


def load_scoring_config() -> ScoringConfig:
    """Load scoring configuration from app_config.yaml + overrides.yaml."""
    config_path = _CONFIG_DIR / "app_config.yaml"
    if not config_path.exists():
        logger.warning("app_config.yaml not found, using defaults")
        return ScoringConfig()

    data = load_app_config()
    scoring = data.get("scoring", {})

    return ScoringConfig(
        green_threshold=scoring.get("green_threshold", 0.90),
        yellow_threshold=scoring.get("yellow_threshold", 0.50),
        enable_counter_check=scoring.get("enable_counter_check", True),
        conservative_mode=scoring.get("conservative_mode", True),
        verify_contract_enabled=scoring.get("verify_contract_enabled", True),
        verify_green_threshold=scoring.get("verify_green_threshold", 0.95),
        soft_green_floor=scoring.get("soft_green_floor", 0.70),
        green_extraction_min_confidence=scoring.get(
            "green_extraction_min_confidence", 0.80
        ),
        soft_vetoes_as_yellow=scoring.get("soft_vetoes_as_yellow", False),
        empty_non_required_as_yellow=scoring.get("empty_non_required_as_yellow", False),
        empty_non_required_as_neutral=scoring.get(
            "empty_non_required_as_neutral", False
        ),
    )
