"""Import the full Schaufler Stammdaten from the Excel template into the runtime catalogs.

Source of truth: config/target_template.xlsx, sheet "Stammdaten" (columns:
Werkstoff, Härte, Nitrier-Art, Nitrier-Tiefe, Beschichtung, Hersteller, Teilegruppe).

Targets (DATA-003):
  - config/master_data/materials.json      ← 363 Werkstoffe (curated entries kept, new ones added)
  - config/master_data/validation_rules.json
      parts_groups.groups                  ← union of existing + 15 template codes
      nitriding_types.canonical_values     ← union with 18 template values
      coatings.canonical_values            ← union with 29 template values
      manufacturers.canonical_values       ← NEW: 181 Hersteller (deduped)

Idempotent: re-running produces no further changes. Prints a diff report.
Curated entries are never modified; template values already covered by an
existing canonical/alias/werkstoff_nr/din_name are skipped (no alias collisions).
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "config" / "target_template.xlsx"
MATERIALS = ROOT / "config" / "master_data" / "materials.json"
RULES = ROOT / "config" / "master_data" / "validation_rules.json"

_PLAIN_NR = re.compile(r"^\d\.\d{4}$")

# Column index → logical name (template layout: value columns are 0,2,4,6,8,10,12)
_COLS = {
    0: "werkstoff",
    4: "nitrier_art",
    8: "beschichtung",
    10: "hersteller",
    12: "teilegruppe",
}


def _normalize(value: str) -> str:
    """Mirror master_data_matcher._normalize so coverage checks agree with runtime."""
    normalized = unicodedata.normalize("NFKC", value).lower().strip()
    return re.sub(r"\s+", " ", normalized)


def _read_template() -> dict[str, list[str]]:
    wb = openpyxl.load_workbook(TEMPLATE, read_only=True)
    ws = wb["Stammdaten"]
    out: dict[str, list[str]] = {name: [] for name in _COLS.values()}
    seen: dict[str, set[str]] = {name: set() for name in _COLS.values()}
    for row in ws.iter_rows(min_row=3, values_only=True):
        for idx, name in _COLS.items():
            value = row[idx] if idx < len(row) else None
            if value in (None, ""):
                continue
            text = str(value).strip()
            key = _normalize(text)
            if not key or key in seen[name]:
                continue  # template carries a few duplicates (GGG50, Omron, RUD)
            seen[name].add(key)
            out[name].append(text)
    wb.close()
    return out


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _import_materials(template_werkstoffe: list[str]) -> int:
    data = _load_json(MATERIALS)
    materials = data.setdefault("materials", [])

    covered: set[str] = set()
    plain_nr_taken: set[str] = set()
    for material in materials:
        for key in ("canonical", "werkstoff_nr", "din_name"):
            value = material.get(key)
            if value:
                covered.add(_normalize(str(value)))
        for alias in material.get("aliases", []) or []:
            covered.add(_normalize(str(alias)))
        if material.get("werkstoff_nr"):
            plain_nr_taken.add(_normalize(str(material["werkstoff_nr"])))

    added = 0
    for raw in template_werkstoffe:
        key = _normalize(raw)
        if key in covered:
            continue
        entry: dict = {
            "canonical": raw,
            "aliases": [],
            "source": "stammdaten_template_import",
        }
        # werkstoff_nr only for the EXACT plain N.NNNN form and only when no other
        # entry already owns that number — variants ("1.2343 ESU VGN") stay
        # alias-only so the werkstoff_nr → canonical map keeps a single owner.
        if _PLAIN_NR.match(raw) and key not in plain_nr_taken:
            entry["werkstoff_nr"] = raw
            plain_nr_taken.add(key)
        materials.append(entry)
        covered.add(key)
        added += 1

    if added:
        _save_json(MATERIALS, data)
    print(f"materials.json: +{added} (total {len(materials)})")
    return added


def _union_canonical_values(block: dict, values: list[str]) -> int:
    existing = block.setdefault("canonical_values", [])
    known = {_normalize(v) for v in existing}
    for canonical, aliases in (block.get("aliases") or {}).items():
        known.add(_normalize(canonical))
        for alias in aliases or []:
            known.add(_normalize(alias))
    added = 0
    for value in values:
        if _normalize(value) not in known:
            existing.append(value)
            known.add(_normalize(value))
            added += 1
    return added


def _import_rules(template: dict[str, list[str]]) -> int:
    data = _load_json(RULES)
    changed = 0

    groups = data.setdefault("parts_groups", {}).setdefault("groups", {})
    existing_codes = {code.upper() for code in groups}
    for code in template["teilegruppe"]:
        if code.upper() not in existing_codes:
            groups[code.upper()] = "Aus Stammdaten-Vorlage importiert"
            existing_codes.add(code.upper())
            changed += 1
    print(f"parts_groups: {len(groups)} Codes ({sorted(existing_codes)})")

    nit_added = _union_canonical_values(
        data.setdefault("nitriding_types", {}), template["nitrier_art"]
    )
    coat_added = _union_canonical_values(
        data.setdefault("coatings", {}), template["beschichtung"]
    )
    print(f"nitriding_types: +{nit_added}; coatings: +{coat_added}")
    changed += nit_added + coat_added

    manufacturers = data.setdefault(
        "manufacturers",
        {
            "description": "Hersteller-Katalog aus der Schaufler Stammdaten-Vorlage. "
            "Matching ist exakt/normalisiert — bewusst KEIN Fuzzy (Zero-False-Positive).",
            "canonical_values": [],
            "aliases": {},
        },
    )
    man_added = _union_canonical_values(manufacturers, template["hersteller"])
    print(
        f"manufacturers: +{man_added} "
        f"(total {len(manufacturers['canonical_values'])})"
    )
    changed += man_added

    if changed:
        _save_json(RULES, data)
    return changed


def main() -> int:
    if not TEMPLATE.exists():
        print(f"Template fehlt: {TEMPLATE}", file=sys.stderr)
        return 1
    template = _read_template()
    print(
        "Template gelesen: "
        + ", ".join(f"{k}={len(v)}" for k, v in template.items())
    )
    _import_materials(template["werkstoff"])
    _import_rules(template)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
