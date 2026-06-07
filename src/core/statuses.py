"""Shared status enums for strict triple-check scoring."""

from __future__ import annotations

from enum import Enum


class MatchResult(str, Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNCERTAIN = "uncertain"
    NOT_APPLICABLE = "not_applicable"


class FinalStatus(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    NEUTRAL = "neutral"
    MANUAL_CONFIRMED = "manual_confirmed"
