"""Master Data Matcher — matches raw values against Schaufler master data catalogs.

Provides exact and fuzzy matching for:
- Material/Werkstoff against materials.json
- Nitriding types against validation_rules.json
- Coatings against validation_rules.json
- Parts groups against validation_rules.json
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
_FUZZY_MATCH_THRESHOLD = 85.0
_MATERIAL_FUZZY_MATCH_THRESHOLD = 95.0
_STRICT_WERKSTOFF_NR = re.compile(r"\b\d\.\d{4}\b")
_STRICT_WERKSTOFF_WITH_DASH = re.compile(r"\b(\d)[–-](\d{4})\b")


def _load_json(filename: str) -> dict:
    path = _CONFIG_DIR / "master_data" / filename
    if not path.exists():
        logger.warning("Master data file not found: %s", path)
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


class MatchResult:
    """Result of a master-data lookup."""

    __slots__ = ("canonical", "confidence", "method")

    def __init__(self, canonical: str | None, confidence: float, method: str) -> None:
        self.canonical = canonical
        self.confidence = confidence
        self.method = method

    def __repr__(self) -> str:
        return f"MatchResult({self.canonical!r}, conf={self.confidence}, method={self.method})"


class _AliasCatalog:
    """Shared exact + fuzzy alias matching behavior."""

    def __init__(self, alias_map: dict[str, str]) -> None:
        self._alias_map = alias_map
        self._alias_choices = tuple(alias_map.keys())

    def _match_alias(self, value: str) -> MatchResult:
        if not value or not value.strip():
            return MatchResult(None, 0.0, "empty")

        normalized = _normalize(value)
        exact = self._alias_map.get(normalized)
        if exact:
            return MatchResult(exact, 1.0, "exact_alias")

        fuzzy = _best_fuzzy_alias_match(normalized, self._alias_map)
        if fuzzy is not None:
            canonical, score = fuzzy
            return MatchResult(
                canonical, _confidence_from_fuzzy_score(score), "fuzzy_alias"
            )

        return MatchResult(None, 0.0, "no_match")


class MaterialCatalog(_AliasCatalog):
    """Lookup material by alias, Werkstoff number, or fuzzy match."""

    def __init__(self) -> None:
        data = _load_json("materials.json")
        self._materials = data.get("materials", [])
        alias_map: dict[str, str] = {}
        self._werkstoff_map: dict[str, str] = {}
        self._material_family_by_canonical: dict[str, str] = {}

        for material in self._materials:
            canonical = material["canonical"]
            alias_map[_normalize(canonical)] = canonical
            self._material_family_by_canonical[canonical] = (
                _material_family_from_category(str(material.get("category") or ""))
            )

            werkstoff_nr = material.get("werkstoff_nr")
            if werkstoff_nr:
                normalized_werkstoff = _normalize(werkstoff_nr)
                self._werkstoff_map[normalized_werkstoff] = canonical
                alias_map[normalized_werkstoff] = canonical

            # M1: index the DIN name automatically (e.g. "X38CrMoV5-1") so future
            # catalog entries do not need it duplicated into `aliases` by hand.
            # Existing entries win on conflict (do not silently re-point an alias).
            din_name = material.get("din_name")
            if din_name:
                normalized_din = _normalize(din_name)
                if normalized_din and normalized_din not in alias_map:
                    alias_map[normalized_din] = canonical

            for alias in material.get("aliases", []):
                alias_map[_normalize(alias)] = canonical

        super().__init__(alias_map)

    def match(self, value: str) -> MatchResult:
        if not value or not value.strip():
            return MatchResult(None, 0.0, "empty")

        cleaned = value.strip()
        exact_alias = self._alias_map.get(_normalize(cleaned))
        if exact_alias:
            return MatchResult(exact_alias, 1.0, "exact_alias")

        for werkstoff_nr in _extract_werkstoff_candidates(cleaned):
            canonical = self._werkstoff_map.get(_normalize(werkstoff_nr))
            if canonical:
                method = (
                    "werkstoff_nr_extract"
                    if werkstoff_nr == _extract_primary_material_number(cleaned)
                    else "werkstoff_nr_base"
                )
                return MatchResult(canonical, 1.0, method)

        from src.transform.value_transformer import extract_material_number

        werkstoff_nr = extract_material_number(cleaned)
        if werkstoff_nr:
            normalized_werkstoff = _normalize(werkstoff_nr)
            canonical = self._werkstoff_map.get(normalized_werkstoff)
            if canonical:
                return MatchResult(canonical, 1.0, "werkstoff_nr_extract")

            base_werkstoff = (
                werkstoff_nr.split()[0] if " " in werkstoff_nr else werkstoff_nr
            )
            base_canonical = self._werkstoff_map.get(_normalize(base_werkstoff))
            if base_canonical:
                return MatchResult(base_canonical, 1.0, "werkstoff_nr_base")

        fuzzy = _best_fuzzy_alias_match(
            _normalize(cleaned),
            self._alias_map,
            score_cutoff=_MATERIAL_FUZZY_MATCH_THRESHOLD,
        )
        if fuzzy is not None:
            canonical, score = fuzzy
            raw_family = _material_family_from_text(cleaned)
            canonical_family = self._material_family_by_canonical.get(canonical, "")
            if _families_conflict(raw_family, canonical_family):
                return MatchResult(None, 0.0, "no_match")
            return MatchResult(
                canonical, _confidence_from_fuzzy_score(score), "fuzzy_material"
            )

        # M3: structurally-valid DIN Werkstoffnummer that is simply not in our
        # (Schaufler-owned) catalog. The value is a correctly-read material id, so
        # we accept it as-is WITHOUT inventing a catalog entry — this is the
        # format-agnostic, zero-maintenance answer for the long tail of customer
        # materials. The strict \d.\d{4} pattern (after dash→dot) cannot match norm
        # parts (DIN912-12.9, FKL 5.8) or junk — verified across the 18 POC PDFs.
        # GREEN for this method is gated to the deterministic text path in the
        # green gate (the value is read exactly there; never on a Vision misread).
        primary = _extract_primary_material_number(cleaned)
        if not primary:
            # M2: standalone stripped form only — the ENTIRE value is a 5-digit
            # DIN number with the dot swallowed (e.g. "12343"→1.2343, "10116G"→
            # 1.0116). MUST be standalone: norm references (STAHL EN 10088-2-,
            # DIN 16756) carry surrounding context and are deliberately excluded —
            # converting their norm number to a material would be a false-green
            # disaster (~700 such values across the POC corpus).
            primary = _standalone_stripped_werkstoff(cleaned)
        if primary:
            return MatchResult(primary, 0.92, "werkstoff_nr_format")

        return MatchResult(None, 0.0, "no_match")

    def get_hardness_range(self, canonical: str) -> tuple[int, int] | None:
        for material in self._materials:
            if material["canonical"] == canonical:
                hardness_range = material.get("typical_hardness_hrc")
                if (
                    hardness_range
                    and isinstance(hardness_range, list)
                    and len(hardness_range) == 2
                ):
                    return (hardness_range[0], hardness_range[1])
        return None


class NitridingTypeCatalog(_AliasCatalog):
    """Lookup nitriding type aliases → canonical name."""

    def __init__(self) -> None:
        data = _load_json("validation_rules.json")
        nit = data.get("nitriding_types", {})
        alias_map: dict[str, str] = {}
        for canonical, aliases in nit.get("aliases", {}).items():
            alias_map[_normalize(canonical)] = canonical
            for alias in aliases:
                alias_map[_normalize(alias)] = canonical
        for canonical in nit.get("canonical_values", []):
            alias_map[_normalize(canonical)] = canonical
        super().__init__(alias_map)

    def match(self, value: str) -> MatchResult:
        return self._match_alias(value)


class CoatingCatalog(_AliasCatalog):
    """Lookup coating aliases → canonical name."""

    def __init__(self) -> None:
        data = _load_json("validation_rules.json")
        coating_rules = data.get("coatings", {})
        alias_map: dict[str, str] = {}
        for canonical, aliases in coating_rules.get("aliases", {}).items():
            alias_map[_normalize(canonical)] = canonical
            for alias in aliases:
                alias_map[_normalize(alias)] = canonical
        for canonical in coating_rules.get("canonical_values", []):
            alias_map[_normalize(canonical)] = canonical
        super().__init__(alias_map)

    def match(self, value: str) -> MatchResult:
        return self._match_alias(value)


class PartsGroupCatalog:
    """Lookup parts group codes."""

    def __init__(self) -> None:
        data = _load_json("validation_rules.json")
        part_groups = data.get("parts_groups", {})
        self._groups: dict[str, str] = part_groups.get("groups", {})
        self._valid_codes = {code.upper() for code in self._groups}

    def match(self, value: str) -> MatchResult:
        if not value or not value.strip():
            return MatchResult(None, 0.0, "empty")
        cleaned = value.strip().upper()
        if cleaned in self._valid_codes:
            return MatchResult(cleaned, 1.0, "exact")
        return MatchResult(None, 0.0, "no_match")


@lru_cache(maxsize=1)
def get_material_catalog() -> MaterialCatalog:
    return MaterialCatalog()


@lru_cache(maxsize=1)
def get_nitriding_catalog() -> NitridingTypeCatalog:
    return NitridingTypeCatalog()


@lru_cache(maxsize=1)
def get_coating_catalog() -> CoatingCatalog:
    return CoatingCatalog()


@lru_cache(maxsize=1)
def get_parts_group_catalog() -> PartsGroupCatalog:
    return PartsGroupCatalog()


def _best_fuzzy_alias_match(
    normalized_value: str,
    alias_map: dict[str, str],
    *,
    score_cutoff: float = _FUZZY_MATCH_THRESHOLD,
) -> tuple[str, float] | None:
    if not normalized_value or not alias_map:
        return None

    best = process.extractOne(
        normalized_value,
        alias_map.keys(),
        scorer=fuzz.WRatio,
        score_cutoff=score_cutoff,
    )
    if best is None:
        return None

    alias, score, _index = best
    return alias_map[alias], float(score)


def _confidence_from_fuzzy_score(score: float) -> float:
    if score >= 97:
        return 0.98
    if score >= 93:
        return 0.95
    if score >= 90:
        return 0.92
    return 0.88


def _normalize(value: str) -> str:
    """Lowercase, strip, collapse whitespace, normalize unicode."""
    normalized = unicodedata.normalize("NFKC", value).lower().strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _extract_primary_material_number(value: str) -> str | None:
    normalized = _STRICT_WERKSTOFF_WITH_DASH.sub(r"\1.\2", value)
    match = _STRICT_WERKSTOFF_NR.search(normalized)
    if match:
        return match.group(0)
    return None


# M2: the ENTIRE value is a DIN Werkstoffnummer with the dot swallowed —
# main group 1 or 2 (steel / heavy metal), four digits, optional single-letter
# condition suffix. Anchored ^...$ so a norm number embedded in surrounding text
# (e.g. "STAHL EN 10088-2-") can never match.
_STANDALONE_STRIPPED_WERKSTOFF = re.compile(r"^([12])(\d{4})[A-Za-z]?$")


def _standalone_stripped_werkstoff(value: str) -> str | None:
    match = _STANDALONE_STRIPPED_WERKSTOFF.match(value.strip())
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    return None


def _extract_werkstoff_candidates(value: str) -> list[str]:
    normalized = _STRICT_WERKSTOFF_WITH_DASH.sub(r"\1.\2", value)
    candidates: list[str] = []

    primary = _extract_primary_material_number(value)
    if primary:
        candidates.append(primary)

    from src.transform.value_transformer import extract_material_number

    extracted = extract_material_number(normalized)
    if extracted:
        candidates.append(extracted)
        if " " in extracted:
            candidates.append(extracted.split()[0])

    for match in _STRICT_WERKSTOFF_NR.findall(normalized):
        candidates.append(match)

    seen: set[str] = set()
    deduplicated: list[str] = []
    for candidate in candidates:
        normalized_candidate = _normalize(candidate)
        if not normalized_candidate or normalized_candidate in seen:
            continue
        seen.add(normalized_candidate)
        deduplicated.append(candidate)
    return deduplicated


def _material_family_from_category(category: str) -> str:
    normalized = _normalize(category)
    if not normalized:
        return ""
    if "steel" in normalized:
        return "steel"
    if "aluminium" in normalized or "aluminum" in normalized:
        return "aluminium"
    if "plastic" in normalized or "polymer" in normalized:
        return "plastic"
    if "copper" in normalized:
        return "copper"
    return normalized


def _material_family_from_text(value: str) -> str:
    normalized = _normalize(value)
    if not normalized:
        return ""
    if any(token in normalized for token in ("stahl", "steel")):
        return "steel"
    if _STRICT_WERKSTOFF_NR.search(
        _STRICT_WERKSTOFF_WITH_DASH.sub(r"\1.\2", normalized)
    ):
        return "steel"
    if any(token in normalized for token in ("aluminium", "aluminum", "alu", "alsi")):
        return "aluminium"
    if any(
        token in normalized
        for token in ("kunststoff", "plastic", "polymer", "pa6", "pom", "peek")
    ):
        return "plastic"
    if any(token in normalized for token in ("kupfer", "copper", "cube", "ampcoloy")):
        return "copper"
    return ""


def _families_conflict(raw_family: str, canonical_family: str) -> bool:
    if not raw_family or not canonical_family:
        return False
    if raw_family == canonical_family:
        return False
    if raw_family == "steel" and canonical_family in {"aluminium", "plastic"}:
        return True
    if raw_family == "aluminium" and canonical_family == "steel":
        return True
    if raw_family == "plastic" and canonical_family in {"steel", "aluminium"}:
        return True
    return False
