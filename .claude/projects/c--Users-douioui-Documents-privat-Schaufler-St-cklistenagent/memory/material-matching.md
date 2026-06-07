---
name: material-matching
description: How Werkstoff/Material is matched and made GREEN — catalog-independent format recognition, not catalog growth
metadata:
  type: project
---

The materials catalog (`config/master_data/materials.json`, 14 entries) is **Schaufler's OWN master data** for canonicalisation ("how does Schaufler name this material?"), NOT a coverage list. It must NOT grow per incoming customer BOM — that would be endless and break the format-agnostic principle (CLAUDE.md). The user explicitly rejected catalog-per-customer growth.

**The real lever (implemented, committed):** a structurally valid DIN Werkstoffnummer (`\d.\d{4}`, after dash→dot) that is NOT in the catalog is recognised AS ITSELF — method `master_data:werkstoff_nr_format`, conf 0.92, in `MaterialCatalog.match` after fuzzy, before final no_match. GREEN means "value correctly transferred from source", not "material in catalog"; on the deterministic text path the value is read exactly, so greening `1.0037` is legitimate without a catalog entry.

Key wiring:
- Green-eligible ONLY on the text path: `green_gate._TEXT_PATH_METHODS` contains `master_data:werkstoff_nr_format`; Vision stays catalog-bound (misread risk).
- `ensemble_scorer._method_quality_score` maps it to 0.90 → rule_score 0.975 ≥ verify_green_threshold 0.90 (without this it was stuck at 0.875 = default 0.50 quality → yellow).
- M2: standalone dot-swallowed numbers (`12343`→1.2343, `10116G`→1.0116) via anchored `^[12]\d{4}[A-Za-z]?$` — STANDALONE only, because ~700 norm refs (`STAHL EN 10088-2-`) would otherwise become false materials (1.0088). Verified 0 norm conversions across 18 POC PDFs.
- M1: `din_name` auto-indexed into the alias map.

Result: +692 real Werkstoffnummer matches across the 16 material-column POC files, 0 false-green, 0 norm conversions. End-to-end green: ZF Material 68→141, Magna 38→119, TCG 2→8.

**Still no_match (separate issues, NOT material-matching):** complex fused Mercedes cells where the Werkstoffnummer is glued to the DIN name (`1.273840CrMnNiMo...` → `\b\d.\d{4}\b` finds no word boundary) — an extraction/column-quality issue. And genuine non-materials (norm parts, treatments) correctly stay no_match→yellow. See [[rebuild-root-cause]].
