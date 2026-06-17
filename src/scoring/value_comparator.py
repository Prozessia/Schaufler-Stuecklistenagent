"""Deterministic value comparator for triple-check scoring."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src.core.statuses import MatchResult

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

_CATEGORY_A_FIELDS = {
    "Detail Number",
    "Design Count",
    "Spare Count",
    "Dimensions X/D",
    "Dimensions Y/L",
    "Dimensions Z",
    "Material",
    "Hardness",
    "Nitriding",
    "Nitriding type",
    "Nitriding depth",
    "Coating",
    "Parts Group",
    "Customer Part Number",
    "Manufacturer part no.",
}

_CATEGORY_B_FIELDS = {
    "Description",
    "Manufacturer",
    "Special Notes",
    "Target cost block description",
}

_QUANTITY_MATCH_FIELDS = {"Design Count", "Spare Count"}
_INTEGER_FIELDS = {"Design Count", "Spare Count"}
_DECIMAL_FIELDS = {
    "Dimensions X/D",
    "Dimensions Y/L",
    "Dimensions Z",
    "Nitriding depth",
}
_DIMENSION_MATCH_FIELDS = {
    "Dimensions X/D",
    "Dimensions Y/L",
    "Dimensions Z",
}
# Fix A: position of each component within a combined dimension string
# ("2300x2080x563" → X/D=0, Y/L=1, Z=2).
_DIMENSION_COMPONENT_INDEX = {
    "Dimensions X/D": 0,
    "Dimensions Y/L": 1,
    "Dimensions Z": 2,
}
_BOOLEAN_FIELDS = {
    "Nitriding",
    "2D exists",
    "3D exists",
    "Higher index available",
    "Manufacturer officially required",
    "Schaufler Tooling Standard",
    "Refurbishment",
    "Pre assembling",
    "Detailed Drawing",
    "Drawings sent to STL",
    "Drawings sent from STL",
}
_ALIAS_FIELDS = {"Nitriding type", "Coating", "Parts Group", "Manufacturer"}

_TRUE_DEFAULTS = {"yes", "ja", "true", "1", "x", "oui", "si", "ano"}
_FALSE_DEFAULTS = {"no", "nein", "false", "0", "", "-", "non", "ne"}


@dataclass(slots=True)
class ValueCompareResult:
    result: MatchResult
    detail: str
    field_category: str
    strict_exact_match: bool
    normalized_mapped: str
    normalized_extracted: str


class ValueComparator:
    """Schema-driven deterministic comparator for mapped vs extracted values."""

    def __init__(self) -> None:
        rules = _load_validation_rules()
        self._alias_lookup = _build_alias_lookup(rules)
        nitriding_rule = rules.get("field_rules", {}).get("Nitriding", {})
        self._true_values = {
            _normalize_text(v)
            for v in nitriding_rule.get("true_values", _TRUE_DEFAULTS)
        }
        self._false_values = {
            _normalize_text(v)
            for v in nitriding_rule.get("false_values", _FALSE_DEFAULTS)
        }

    def compare_values(
        self,
        mapped_value: str | None,
        extracted_value: str | None,
        target_field: str,
        extraction_confidence: float | None = None,
        extraction_reason: str | None = None,
        document_text_layer: str | None = None,
        extraction_match_type: str | None = None,
    ) -> ValueCompareResult:
        mapped_norm = _normalize_text(mapped_value)
        extracted_norm = _normalize_text(extracted_value)
        category = _field_category(target_field)

        if not mapped_norm and not extracted_norm:
            return ValueCompareResult(
                result=MatchResult.NOT_APPLICABLE,
                detail="both values empty",
                field_category=category,
                strict_exact_match=False,
                normalized_mapped=mapped_norm,
                normalized_extracted=extracted_norm,
            )

        # GREEN-RECOVERY P0/P1: a "row_fallback" extraction is the WHOLE row line
        # (the parser could not isolate the column's x-corridor). It is therefore
        # NOT a column-scoped value that can CONTRADICT the mapped value, and must
        # never produce a MISMATCH hard-veto (P0). We confirm by in-row
        # containment instead: a strong-identity token present in its own row is
        # row-scoped identity proof and GREEN-eligible (P1); anything weaker stays
        # UNCERTAIN → YELLOW (review), never RED.
        if extraction_match_type == "row_fallback" and mapped_norm:
            return self._compare_via_row_containment(
                mapped_norm, extracted_norm, category
            )

        if target_field == "Customer Part Number":
            return self._compare_customer_part_number(
                mapped_norm,
                extracted_norm,
                category,
                extraction_confidence,
                extraction_reason,
                document_text_layer,
            )

        if not extracted_norm:
            return ValueCompareResult(
                result=MatchResult.UNCERTAIN,
                detail="missing independent PDF extraction",
                field_category=category,
                strict_exact_match=False,
                normalized_mapped=mapped_norm,
                normalized_extracted=extracted_norm,
            )

        if not mapped_norm and extracted_norm:
            return ValueCompareResult(
                result=MatchResult.MISMATCH,
                detail="mapped value empty but PDF value present",
                field_category=category,
                strict_exact_match=False,
                normalized_mapped=mapped_norm,
                normalized_extracted=extracted_norm,
            )

        if target_field == "Detail Number":
            return self._compare_detail_number(
                mapped_norm,
                extracted_norm,
                category,
                extraction_confidence,
            )

        if target_field in _DIMENSION_MATCH_FIELDS:
            return self._compare_dimension_field(
                mapped_norm,
                extracted_norm,
                category,
                extraction_confidence,
                target_field,
            )

        if target_field == "Description":
            return self._compare_description(
                mapped_norm,
                extracted_norm,
                category,
                extraction_confidence,
            )

        if target_field in _QUANTITY_MATCH_FIELDS:
            return self._compare_quantity_field(
                mapped_norm,
                extracted_norm,
                category,
                extraction_confidence,
            )

        if target_field in _INTEGER_FIELDS:
            return self._compare_integer(mapped_norm, extracted_norm, category)

        if target_field in _DECIMAL_FIELDS:
            return self._compare_decimal(mapped_norm, extracted_norm, category)

        if target_field in _BOOLEAN_FIELDS:
            return self._compare_boolean(mapped_norm, extracted_norm, category)

        if target_field in _ALIAS_FIELDS:
            return self._compare_alias(
                target_field, mapped_norm, extracted_norm, category
            )

        if target_field == "Material":
            return self._compare_material(mapped_norm, extracted_norm, category)

        if target_field == "Hardness":
            return self._compare_hardness(mapped_norm, extracted_norm, category)

        return _compare_generic_text(mapped_norm, extracted_norm, category)

    def _compare_via_row_containment(
        self,
        mapped_norm: str,
        extracted_norm: str,
        category: str,
    ) -> ValueCompareResult:
        """Confirm a value against the whole-row text of a row_fallback cell.

        Contract (GREEN-RECOVERY P0/P1):
        - exact equality of the (degenerate) row text → genuine MATCH;
        - strong-identity token present in its own row → MATCH (row-scoped
          identity proof, GREEN-eligible, strict_exact=True);
        - present-but-weak, or absent → UNCERTAIN (YELLOW). NEVER MISMATCH —
          a whole-row blob cannot contradict a single column value.
        """
        if extracted_norm and mapped_norm == extracted_norm:
            return ValueCompareResult(
                result=MatchResult.MATCH,
                detail="exact normalized match",
                field_category=category,
                strict_exact_match=True,
                normalized_mapped=mapped_norm,
                normalized_extracted=extracted_norm,
            )

        if not extracted_norm:
            return ValueCompareResult(
                result=MatchResult.UNCERTAIN,
                detail="row-fallback extraction empty",
                field_category=category,
                strict_exact_match=False,
                normalized_mapped=mapped_norm,
                normalized_extracted=extracted_norm,
            )

        present = _value_present_in_row(mapped_norm, extracted_norm)
        if present and _is_strong_row_identity(mapped_norm):
            return ValueCompareResult(
                result=MatchResult.MATCH,
                detail="value confirmed within its row (row-scoped containment)",
                field_category=category,
                strict_exact_match=True,
                normalized_mapped=mapped_norm,
                normalized_extracted=mapped_norm,
            )

        detail = (
            "row-fallback: value present but identity too weak to confirm"
            if present
            else "row-fallback: value not confirmable in row text"
        )
        return ValueCompareResult(
            result=MatchResult.UNCERTAIN,
            detail=detail,
            field_category=category,
            strict_exact_match=False,
            normalized_mapped=mapped_norm,
            normalized_extracted=extracted_norm,
        )

    def _compare_hardness(
        self,
        mapped_norm: str,
        extracted_norm: str,
        category: str,
    ) -> ValueCompareResult:
        generic = _compare_generic_text(mapped_norm, extracted_norm, category)
        if generic.result != MatchResult.MISMATCH:
            return generic

        # Same hardness value, only unit word ("HRC") or word order differs:
        # "31-35 HRC" vs "HRC 31-35", "470-630 HRC" vs "470-630". A SHARED numeric
        # hardness range/value is the same hardness → MATCH (strict, as Hardness is
        # category A). A DIFFERENT range shares nothing → stays MISMATCH.
        mapped_cores = _hardness_cores(mapped_norm)
        source_cores = _hardness_cores(extracted_norm)
        if mapped_cores and (mapped_cores & source_cores):
            return ValueCompareResult(
                result=MatchResult.MATCH,
                detail="same hardness value (unit/order normalized)",
                field_category=category,
                strict_exact_match=True,
                normalized_mapped=mapped_norm,
                normalized_extracted=extracted_norm,
            )
        return generic

    def _compare_material(
        self,
        mapped_norm: str,
        extracted_norm: str,
        category: str,
    ) -> ValueCompareResult:
        generic = _compare_generic_text(mapped_norm, extracted_norm, category)
        if generic.result != MatchResult.MISMATCH:
            return generic

        # Fix B: the catalog canonical legitimately adds a variant — source "1.2343"
        # canonicalises to "1.2343 ESU". When mapped and source carry the SAME DIN
        # Werkstoffnummer it is the same material → MATCH. A DIFFERENT number still
        # mismatches (DIN numbers are unique material ids) → no false-green.
        mapped_nr = _werkstoff_number(mapped_norm)
        source_nr = _werkstoff_number(extracted_norm)
        if mapped_nr and source_nr and mapped_nr == source_nr:
            # strict_exact_match=True: the DIN Werkstoffnummer is THE unique material
            # identity, so a shared number is an exact identity match (Material is a
            # category-A field and needs this to be GREEN-eligible). A different
            # number never reaches here.
            return ValueCompareResult(
                result=MatchResult.MATCH,
                detail="same werkstoffnummer (catalog variant)",
                field_category=category,
                strict_exact_match=True,
                normalized_mapped=mapped_norm,
                normalized_extracted=extracted_norm,
            )
        return generic

    def _compare_integer(
        self,
        mapped_norm: str,
        extracted_norm: str,
        category: str,
    ) -> ValueCompareResult:
        left = _parse_int(mapped_norm)
        right = _parse_int(extracted_norm)
        if left is None or right is None:
            return ValueCompareResult(
                result=MatchResult.UNCERTAIN,
                detail="integer parse failed",
                field_category=category,
                strict_exact_match=False,
                normalized_mapped=mapped_norm,
                normalized_extracted=extracted_norm,
            )

        if left == right:
            return ValueCompareResult(
                result=MatchResult.MATCH,
                detail="exact numeric match",
                field_category=category,
                strict_exact_match=True,
                normalized_mapped=str(left),
                normalized_extracted=str(right),
            )

        return ValueCompareResult(
            result=MatchResult.MISMATCH,
            detail=f"numeric mismatch ({left} != {right})",
            field_category=category,
            strict_exact_match=False,
            normalized_mapped=str(left),
            normalized_extracted=str(right),
        )

    def _compare_decimal(
        self,
        mapped_norm: str,
        extracted_norm: str,
        category: str,
    ) -> ValueCompareResult:
        left = _parse_decimal(mapped_norm)
        right = _parse_decimal(extracted_norm)
        if left is None or right is None:
            return ValueCompareResult(
                result=MatchResult.UNCERTAIN,
                detail="decimal parse failed",
                field_category=category,
                strict_exact_match=False,
                normalized_mapped=mapped_norm,
                normalized_extracted=extracted_norm,
            )

        if abs(left - right) <= 1e-3:
            return ValueCompareResult(
                result=MatchResult.MATCH,
                detail="exact numeric match",
                field_category=category,
                strict_exact_match=True,
                normalized_mapped=f"{left:.6f}",
                normalized_extracted=f"{right:.6f}",
            )

        return ValueCompareResult(
            result=MatchResult.MISMATCH,
            detail=f"numeric mismatch ({left:.6f} != {right:.6f})",
            field_category=category,
            strict_exact_match=False,
            normalized_mapped=f"{left:.6f}",
            normalized_extracted=f"{right:.6f}",
        )

    def _compare_boolean(
        self,
        mapped_norm: str,
        extracted_norm: str,
        category: str,
    ) -> ValueCompareResult:
        left = _parse_boolean(mapped_norm, self._true_values, self._false_values)
        right = _parse_boolean(extracted_norm, self._true_values, self._false_values)
        if left is None or right is None:
            return ValueCompareResult(
                result=MatchResult.UNCERTAIN,
                detail="boolean parse failed",
                field_category=category,
                strict_exact_match=False,
                normalized_mapped=mapped_norm,
                normalized_extracted=extracted_norm,
            )

        if left == right:
            return ValueCompareResult(
                result=MatchResult.MATCH,
                detail="exact boolean match",
                field_category=category,
                strict_exact_match=True,
                normalized_mapped=str(left),
                normalized_extracted=str(right),
            )

        return ValueCompareResult(
            result=MatchResult.MISMATCH,
            detail="boolean mismatch",
            field_category=category,
            strict_exact_match=False,
            normalized_mapped=str(left),
            normalized_extracted=str(right),
        )

    def _compare_alias(
        self,
        target_field: str,
        mapped_norm: str,
        extracted_norm: str,
        category: str,
    ) -> ValueCompareResult:
        mapped_canonical = self._canonicalize(target_field, mapped_norm)
        extracted_canonical = self._canonicalize(target_field, extracted_norm)

        if (
            mapped_canonical
            and extracted_canonical
            and mapped_canonical == extracted_canonical
        ):
            strict_exact = mapped_norm == extracted_norm
            detail = (
                "exact normalized match"
                if strict_exact
                else "semantic equivalent via alias canonicalization"
            )
            return ValueCompareResult(
                result=MatchResult.MATCH,
                detail=detail,
                field_category=category,
                strict_exact_match=strict_exact,
                normalized_mapped=mapped_canonical,
                normalized_extracted=extracted_canonical,
            )

        # Canonicals differ (or are unknown to the catalog): fall back to the
        # generic text compare so two formatting variants of the SAME unknown
        # value ("Meusburger GmbH" vs "Meusburger-GmbH") still MATCH via relaxed
        # normalization instead of being hard-vetoed as MISMATCH. Two values
        # that canonicalize to DIFFERENT catalog entries cannot pass this:
        # the relaxed cores of distinct names differ as well.
        generic = _compare_generic_text(mapped_norm, extracted_norm, category)
        if generic.result == MatchResult.MATCH:
            return generic

        return ValueCompareResult(
            result=MatchResult.MISMATCH,
            detail="canonical values differ",
            field_category=category,
            strict_exact_match=False,
            normalized_mapped=mapped_canonical or mapped_norm,
            normalized_extracted=extracted_canonical or extracted_norm,
        )

    def _compare_detail_number(
        self,
        mapped_norm: str,
        extracted_norm: str,
        category: str,
        extraction_confidence: float | None,
    ) -> ValueCompareResult:
        generic_result = _compare_generic_text(mapped_norm, extracted_norm, category)
        if generic_result.result == MatchResult.MATCH:
            return generic_result

        mapped_main = _extract_detail_number_main(mapped_norm)
        extracted_main = _extract_detail_number_main(extracted_norm)
        if (
            extraction_confidence is not None
            and extraction_confidence >= 0.90
            and mapped_main
            and extracted_main
        ):
            mapped_number = _parse_int(mapped_main)
            extracted_number = _parse_int(extracted_main)
            if mapped_number is not None and extracted_number is not None:
                if mapped_number == extracted_number:
                    normalized = str(mapped_number)
                    return ValueCompareResult(
                        result=MatchResult.MATCH,
                        detail="detail number main position match",
                        field_category=category,
                        strict_exact_match=True,
                        normalized_mapped=normalized,
                        normalized_extracted=normalized,
                    )
            elif mapped_main == extracted_main:
                return ValueCompareResult(
                    result=MatchResult.MATCH,
                    detail="detail number main position match",
                    field_category=category,
                    strict_exact_match=True,
                    normalized_mapped=mapped_main,
                    normalized_extracted=extracted_main,
                )

        return generic_result

    def _compare_customer_part_number(
        self,
        mapped_norm: str,
        extracted_norm: str,
        category: str,
        extraction_confidence: float | None,
        extraction_reason: str | None,
        document_text_layer: str | None,
    ) -> ValueCompareResult:
        if not extracted_norm:
            if self._customer_part_number_verified_via_text_layer(
                mapped_norm,
                extraction_confidence,
                extraction_reason,
                document_text_layer,
                base_result=MatchResult.UNCERTAIN,
            ):
                return _customer_part_number_text_match_result(category, mapped_norm)

            return ValueCompareResult(
                result=MatchResult.UNCERTAIN,
                detail="missing independent PDF extraction",
                field_category=category,
                strict_exact_match=False,
                normalized_mapped=mapped_norm,
                normalized_extracted=extracted_norm,
            )

        if not mapped_norm:
            return ValueCompareResult(
                result=MatchResult.MISMATCH,
                detail="mapped value empty but PDF value present",
                field_category=category,
                strict_exact_match=False,
                normalized_mapped=mapped_norm,
                normalized_extracted=extracted_norm,
            )

        generic_result = _compare_generic_text(mapped_norm, extracted_norm, category)
        if generic_result.result == MatchResult.MATCH:
            if (
                extraction_confidence is not None
                and extraction_confidence >= 0.90
                and extraction_reason == "global_text_row_anchor"
            ):
                return _customer_part_number_text_match_result(category, mapped_norm)
            return generic_result

        if self._customer_part_number_verified_via_text_layer(
            mapped_norm,
            extraction_confidence,
            extraction_reason,
            document_text_layer,
            base_result=generic_result.result,
        ):
            return _customer_part_number_text_match_result(category, mapped_norm)

        return generic_result

    def _compare_quantity_field(
        self,
        mapped_norm: str,
        extracted_norm: str,
        category: str,
        extraction_confidence: float | None,
    ) -> ValueCompareResult:
        generic_result = _compare_generic_text(mapped_norm, extracted_norm, category)
        if generic_result.result == MatchResult.MATCH:
            return generic_result
        if extraction_confidence is None or extraction_confidence < 0.90:
            return generic_result

        expected_int = _parse_quantity_int(mapped_norm)
        extracted_int = _parse_quantity_int(extracted_norm)
        if expected_int is None or extracted_int is None:
            return generic_result

        if expected_int == extracted_int:
            normalized = str(expected_int)
            return ValueCompareResult(
                result=MatchResult.MATCH,
                detail="quantity semantic integer match",
                field_category=category,
                strict_exact_match=True,
                normalized_mapped=normalized,
                normalized_extracted=normalized,
            )

        return ValueCompareResult(
            result=MatchResult.MISMATCH,
            detail=f"numeric mismatch ({expected_int} != {extracted_int})",
            field_category=category,
            strict_exact_match=False,
            normalized_mapped=str(expected_int),
            normalized_extracted=str(extracted_int),
        )

    def _compare_dimension_field(
        self,
        mapped_norm: str,
        extracted_norm: str,
        category: str,
        extraction_confidence: float | None,
        target_field: str = "",
    ) -> ValueCompareResult:
        direct_result = self._compare_decimal(mapped_norm, extracted_norm, category)
        if direct_result.result == MatchResult.MATCH:
            return direct_result

        # MATCH outcomes require a high-confidence extraction; MISMATCH outcomes
        # (numeric contradictions) are deterministic content evidence and apply at
        # ANY confidence — UNCERTAIN would let the text path promote a wrongly
        # assigned dimension component to GREEN (dimension_split is a verified
        # method there).
        high_confidence = (
            extraction_confidence is not None and extraction_confidence >= 0.90
        )

        mapped_core = _normalize_dimension_core(mapped_norm)
        extracted_core = _normalize_dimension_core(extracted_norm)
        left = _parse_decimal(mapped_core)
        right = _parse_decimal(extracted_core)
        if left is not None and right is not None:
            if abs(left - right) <= 1e-3:
                if high_confidence:
                    return ValueCompareResult(
                        result=MatchResult.MATCH,
                        detail="dimension semantic numeric match",
                        field_category=category,
                        strict_exact_match=True,
                        normalized_mapped=f"{left:.6f}",
                        normalized_extracted=f"{right:.6f}",
                    )
            elif len(_dimension_numeric_tokens(extracted_norm)) <= 1:
                # Both sides are genuine scalars and disagree — definitive mismatch.
                # Combined strings ("2300x2080x563") take the positional check below.
                return ValueCompareResult(
                    result=MatchResult.MISMATCH,
                    detail=f"dimension numeric mismatch ({left:.6f} != {right:.6f})",
                    field_category=category,
                    strict_exact_match=False,
                    normalized_mapped=f"{left:.6f}",
                    normalized_extracted=f"{right:.6f}",
                )

        # Fix A: a single component split from a COMBINED source string (e.g. the
        # "2080" of Y/L from "2300x2080x563") matches the numeric token AT ITS
        # POSITION in the source — X/D=1st, Y/L=2nd, Z=3rd. Positional (not mere
        # containment): a wrongly-assigned component is a definitive MISMATCH at
        # any confidence; the MATCH direction stays gated on high confidence.
        component_index = _DIMENSION_COMPONENT_INDEX.get(target_field)
        mapped_number = _parse_decimal(mapped_norm)
        if component_index is not None and mapped_number is not None:
            tokens = _dimension_numeric_tokens(extracted_norm)
            if len(tokens) >= 2 and component_index < len(tokens):
                if abs(mapped_number - tokens[component_index]) <= 1e-3:
                    if high_confidence:
                        return ValueCompareResult(
                            result=MatchResult.MATCH,
                            detail="dimension component matches source position",
                            field_category=category,
                            strict_exact_match=True,
                            normalized_mapped=f"{mapped_number:.6f}",
                            normalized_extracted=extracted_norm,
                        )
                else:
                    return ValueCompareResult(
                        result=MatchResult.MISMATCH,
                        detail=(
                            "dimension component contradicts source position "
                            f"({mapped_number:g} != {tokens[component_index]:g})"
                        ),
                        field_category=category,
                        strict_exact_match=False,
                        normalized_mapped=f"{mapped_number:.6f}",
                        normalized_extracted=extracted_norm,
                    )

        return direct_result

    def _compare_description(
        self,
        mapped_norm: str,
        extracted_norm: str,
        category: str,
        extraction_confidence: float | None,
    ) -> ValueCompareResult:
        generic_result = _compare_generic_text(mapped_norm, extracted_norm, category)
        if generic_result.result == MatchResult.MATCH:
            return generic_result
        if extraction_confidence is None or extraction_confidence < 0.90:
            return generic_result

        mapped_core = _normalize_description_core(mapped_norm)
        extracted_core = _normalize_description_core(extracted_norm)
        if _description_cores_match(mapped_core, extracted_core):
            return ValueCompareResult(
                result=MatchResult.MATCH,
                detail="description semantic core match",
                field_category=category,
                strict_exact_match=True,
                normalized_mapped=mapped_core,
                normalized_extracted=extracted_core,
            )

        return generic_result

    def _canonicalize(self, target_field: str, value: str) -> str:
        if not value:
            return ""
        field_lookup = self._alias_lookup.get(target_field, {})
        return field_lookup.get(value, value)

    def _customer_part_number_verified_via_text_layer(
        self,
        mapped_norm: str,
        extraction_confidence: float | None,
        extraction_reason: str | None,
        document_text_layer: str | None,
        base_result: MatchResult,
    ) -> bool:
        """BUG-004: token-bounded, length-guarded document presence check.

        The old check concatenated the WHOLE document into one alphanumeric
        string and looked for a substring — hits across token/cell/row
        boundaries, and short part numbers matched everywhere. Now the part
        number must appear as a whole token sequence (flexible separators,
        hard alnum boundaries) and carry at least 6 core characters. This is
        still document-global (not row-locked), which is why the resulting
        match is no longer reported as strict_exact (no GREEN for category A).
        """
        if extraction_confidence is None or extraction_confidence < 0.90:
            return False
        if (
            extraction_reason != "no_coordinate_match"
            and base_result != MatchResult.MISMATCH
        ):
            return False

        expected_core = _normalize_customer_part_number_core(mapped_norm)
        if len(expected_core) < 6:
            return False

        from src.scoring.pdf_value_extractor import _build_anchor_pattern

        pattern = _build_anchor_pattern(mapped_norm)
        return bool(pattern and pattern.search(document_text_layer or ""))


def _field_category(target_field: str) -> str:
    if target_field in _CATEGORY_A_FIELDS:
        return "A"
    if target_field in _CATEGORY_B_FIELDS:
        return "B"
    return "C"


@lru_cache(maxsize=1)
def _load_validation_rules() -> dict:
    path = _CONFIG_DIR / "master_data" / "validation_rules.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _build_alias_lookup(rules: dict) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}

    field_to_rule_key = {
        "Nitriding type": "nitriding_types",
        "Coating": "coatings",
        "Parts Group": "parts_groups",
        "Manufacturer": "manufacturers",
    }

    for field_name, rule_key in field_to_rule_key.items():
        rule_block = rules.get(rule_key, {})
        field_lookup: dict[str, str] = {}

        aliases = rule_block.get("aliases", {})
        if isinstance(aliases, dict):
            for canonical, variants in aliases.items():
                canonical_norm = _normalize_text(canonical)
                if canonical_norm:
                    field_lookup[canonical_norm] = canonical_norm
                for variant in variants or []:
                    variant_norm = _normalize_text(variant)
                    if variant_norm:
                        field_lookup[variant_norm] = canonical_norm

        groups = rule_block.get("groups", {})
        if isinstance(groups, dict):
            for group_code in groups:
                group_norm = _normalize_text(group_code)
                if group_norm:
                    field_lookup[group_norm] = group_norm

        canonical_values = rule_block.get("canonical_values", [])
        if isinstance(canonical_values, list):
            for value in canonical_values:
                canonical_norm = _normalize_text(value)
                if canonical_norm:
                    field_lookup[canonical_norm] = canonical_norm

        lookup[field_name] = field_lookup

    return lookup


def _normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value))
    normalized = normalized.replace("\u00a0", " ")
    normalized = " ".join(normalized.strip().split())
    return normalized.casefold()


def _normalize_relaxed(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value)


def _value_present_in_row(mapped_norm: str, row_norm: str) -> bool:
    """True when the mapped value occurs as a token-bounded sequence in the row.

    Reuses the part-number anchor pattern (flexible separators, hard alnum
    boundaries) so e.g. "distanzleiste es" matches inside the full row line but a
    short value cannot match across token boundaries.
    """
    from src.scoring.pdf_value_extractor import _build_anchor_pattern

    pattern = _build_anchor_pattern(mapped_norm)
    return bool(pattern and pattern.search(row_norm or ""))


def _is_strong_row_identity(value: str) -> bool:
    """GREEN via row-containment only for a sufficiently distinctive token.

    Requires >= 6 alphanumeric core characters. Short or 2-3 digit values
    (dimensions, counts) recur across a BOM row, so their presence is not
    row-scoped identity proof — those stay UNCERTAIN (YELLOW), never GREEN.
    """
    return len(_normalize_relaxed(value)) >= 6


def _normalize_customer_part_number_core(value: str) -> str:
    return _normalize_relaxed(_normalize_text(value))


def _compare_generic_text(
    mapped_norm: str,
    extracted_norm: str,
    category: str,
) -> ValueCompareResult:
    strict_exact = mapped_norm == extracted_norm
    if strict_exact:
        return ValueCompareResult(
            result=MatchResult.MATCH,
            detail="exact normalized match",
            field_category=category,
            strict_exact_match=True,
            normalized_mapped=mapped_norm,
            normalized_extracted=extracted_norm,
        )

    relaxed_mapped = _normalize_relaxed(mapped_norm)
    relaxed_extracted = _normalize_relaxed(extracted_norm)
    if relaxed_mapped == relaxed_extracted:
        return ValueCompareResult(
            result=MatchResult.MATCH,
            detail="semantic equivalent (format normalization)",
            field_category=category,
            strict_exact_match=False,
            normalized_mapped=relaxed_mapped,
            normalized_extracted=relaxed_extracted,
        )

    return ValueCompareResult(
        result=MatchResult.MISMATCH,
        detail="normalized values differ",
        field_category=category,
        strict_exact_match=False,
        normalized_mapped=mapped_norm,
        normalized_extracted=extracted_norm,
    )


_HARDNESS_UNIT_RE = re.compile(r"(?i)\b(hrc|hrb|hb|hv|rc|n\s*/?\s*mm.?|rm)\b|±|\+/-")
_HARDNESS_RANGE_RE = re.compile(r"\d+(?:[.,]\d+)?\s*-\s*\d+(?:[.,]\d+)?")
_HARDNESS_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _hardness_cores(value: str) -> set[str]:
    """Numeric hardness cores (ranges preferred, else numbers), units/order stripped.

    "31-35 HRC" → {"31-35"}; "HRC 31-35" → {"31-35"}; "1.2311 / 0.2-0.3" → {"0.2-0.3"}.
    """
    stripped = _HARDNESS_UNIT_RE.sub(" ", value)
    ranges = {r.replace(" ", "") for r in _HARDNESS_RANGE_RE.findall(stripped)}
    if ranges:
        return ranges
    return set(_HARDNESS_NUMBER_RE.findall(stripped))


def _werkstoff_number(value: str) -> str:
    """First DIN Werkstoffnummer in the value, normalised to dot form (or '')."""
    match = re.search(r"(\d)[.\-](\d{4})", value)
    return f"{match.group(1)}.{match.group(2)}" if match else ""


def _dimension_numeric_tokens(value: str) -> list[float]:
    """Numeric tokens of a (possibly combined) dimension string, in order.

    "2300x2080x563" → [2300.0, 2080.0, 563.0]; "ø289x980" → [289.0, 980.0].
    """
    tokens: list[float] = []
    for raw in re.findall(r"\d+(?:[.,]\d+)?", value):
        number = _parse_decimal(raw)
        if number is not None:
            tokens.append(number)
    return tokens


def _normalize_dimension_core(value: str) -> str:
    cleaned = value.replace("ø", "")
    cleaned = cleaned.replace("⌀", "")
    cleaned = re.sub(r"\bmm\b", "", cleaned)
    cleaned = re.sub(r"\bzu\b", "", cleaned)
    cleaned = re.sub(r"\s+", "", cleaned)
    return re.sub(r"[^0-9,\.\-+]", "", cleaned)


def _normalize_description_core(value: str) -> str:
    cleaned = re.sub(r"\bdin\s*[-_/]*\s*", "din", value)
    cleaned = re.sub(r"[\s\-_]+", "", cleaned)
    return re.sub(r"[^a-z0-9]", "", cleaned)


def _description_cores_match(mapped_core: str, extracted_core: str) -> bool:
    if not mapped_core or not extracted_core:
        return False
    if mapped_core == extracted_core:
        return True
    shorter, longer = sorted((mapped_core, extracted_core), key=len)
    return len(shorter) >= 12 and shorter in longer


def _extract_detail_number_main(value: str) -> str:
    if not value:
        return ""
    head, *_tail = re.split(r"[-.]", value, maxsplit=1)
    return head.strip()


def _customer_part_number_text_match_result(
    category: str,
    mapped_norm: str,
) -> ValueCompareResult:
    normalized = _normalize_customer_part_number_core(mapped_norm)
    return ValueCompareResult(
        result=MatchResult.MATCH,
        detail="customer part number verified via global pdf text layer",
        field_category=category,
        # BUG-004: document-global presence is NOT row-locked identity proof.
        # MATCH keeps the cell out of MISMATCH/RED, but without strict_exact the
        # category-A gate withholds GREEN — the reviewer confirms.
        strict_exact_match=False,
        normalized_mapped=normalized,
        normalized_extracted=normalized,
    )


def _parse_int(value: str) -> int | None:
    cleaned = value.replace(" ", "")
    match = re.fullmatch(r"[-+]?\d+", cleaned)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


# BUG-002 (sibling): a quantity is a bare integer with at most a known unit
# suffix. Mixed tokens ("4x10", "M12") must not collapse into integers.
_QUANTITY_TOKEN_RE = re.compile(
    r"[-+]?(\d{1,4})(?:[.,]0+)?(?:\s*(?:stk|stck|pcs|pc|ea|x))?",
    re.IGNORECASE,
)


def _parse_quantity_int(value: str) -> int | None:
    if not value:
        return None

    match = _QUANTITY_TOKEN_RE.fullmatch(value.strip())
    if not match:
        return None

    try:
        return int(match.group(1))
    except ValueError:
        return None


def _parse_decimal(value: str) -> float | None:
    cleaned = value.replace(" ", "").replace(",", ".")
    match = re.fullmatch(r"[-+]?\d*\.?\d+", cleaned)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_boolean(
    value: str, true_values: set[str], false_values: set[str]
) -> bool | None:
    if value in true_values:
        return True
    if value in false_values:
        return False
    return None
