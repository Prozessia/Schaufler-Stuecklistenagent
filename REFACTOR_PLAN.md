# REFACTOR_PLAN — BOM-Mapper Datenverlust- & False-Green-Härtung

**Verdikt:** REFACTOR (kein Neustart). Datenverlust-Probleme sitzen konzentriert am
Ingestion-/Export-Rand und sind überwiegend **additiv** behebbar. Scoring-Vertrag,
Stammdaten und Export-Template bleiben erhalten.

**Reihenfolge (verbindlich):** A1 → A2 → A3 → B1 → B2 → B3 → C1 → C2 → C3

**Side-Conditions (gelten immer):**
1. Output-Zeilen ≥ PDF-Positionen, sonst Exception vor File-Write (A2/B3)
2. LLM niemals blockierend im *deterministischen* Datenverlust-Schutz (B2/B3 LLM-frei)
3. Seitenweise Partitionierung, kein globaler Fließtext (A3)
4. Master-Matrix = set(ERP/extrahiert) ∪ set(PDF) als Iterationsbasis (B2)
5. Stammdaten-Validierung gegen Schaufler-Excel-Stammdaten (bestehend, erhalten)

---

## Daten-Contract (Voraussetzung für A2/B2/B3)

Neue typisierte Felder zum Durchreichen der erwarteten Positionsmenge:

- `ParsedBOM.expected_position_ids: list[str]` — alle Pos-IDs aus dem PDF (beide Pfade)
- `TransformationResult.expected_position_ids: list[str]` + `reconciled: bool`
- `BomAuditTrail.expected_position_count: int`

Fluss: `pdf_parser` → `transform_bom` (kopiert) → **Reconciler** (master_set) →
`scorer` (stempelt count) → `exporter` (assertiert).

---

## BLOCK A — Sofort-Fixes (Stunden, geringer Blast-Radius)

- [x] **A1 — Stille Deduplizierung abschalten**
  - Datei/Zeile: `src/ingestion/pdf_parser.py:1784` (`_deduplicate_rows`)
  - Änderung: Dedup-Key von ersten 3 Spalten → vollständige Zeile (alle Spalten)
  - Akzeptanz: 1000-Pos-BOM mit identischen ersten 3 Spalten verliert 0 Zeilen
  - Regressionsrisiko: niedrig
  - Test: `test_dedup_preserves_distinct_rows`

- [x] **A2 — Zero-Data-Loss-Assertion vor File-Write**
  - Datei/Zeile: `src/export/excel_exporter.py:102` (+ `ZeroDataLossError`, Contract-Felder)
  - Änderung: vor `wb.save()` prüfen `distinct_output_positions >= audit.expected_position_count`, sonst `ZeroDataLossError`
  - Akzeptanz: künstlich reduzierter Input → Exception statt stillem Speichern
  - Regressionsrisiko: niedrig–mittel (nur scharf wenn `expected_position_count > 0`)
  - Test: `test_export_raises_on_data_loss`

- [x] **A3 — Vision ≤5 Seiten: Truncation + Partial-Recovery**
  - Datei/Zeile: `src/ingestion/pdf_parser.py:1232` (`_extract_all_pages_via_vision`), `:1398` (`_parse_extraction_response`)
  - Änderung: ≤5-Seiten-Batch-Sonderpfad entfernen → immer seitenweise; `_salvage_partial_rows` bei JSON-Abbruch statt `return []`
  - Akzeptanz: simulierter `max_tokens`-Abbruch → 0 Zeilenverlust (Partial-Recovery)
  - Regressionsrisiko: mittel (mehr Calls/Latenz, 429-Gefahr; durch Concurrency=1 gemildert)
  - Test: `test_partial_json_recovery`

---

## BLOCK B — Strukturelle Fixes (Tage, neue additive Schicht)

- [x] **B1 — Format-agnostische Positions-Regex (konfigurierbar)**
  - Datei/Zeile: `src/scoring/ensemble_scorer.py:64` (`_PDF_POSITION_RE`) + neu `config/pos_patterns.yaml`
  - Änderung: Muster generalisieren (`\d+-\d+ | K-\d+ | [A-Z]-\d+ | \b\d{1,4}\b`), aus YAML laden; breitester Fall zuletzt
  - Akzeptanz: BOM mit fortlaufenden Positionen 1..1000 vollständig erkannt
  - Regressionsrisiko: mittel–hoch → **nie ohne C1 ausliefern** (Phantom-Positionen)
  - Test: `test_sequential_positions_detected`

- [x] **B2 — Reconciliation-Schicht (neue Datei)**
  - Datei: neu `src/reconciliation/position_reconciler.py`; eingehängt in `src/api/pipeline_runner.py:116` (zwischen Transform und Cross-Validate)
  - Änderung: `master_set = set(extrahiert) ∪ set(raw_pdf_positions)`; synthetische MISSING-Zeile je fehlender Position — **auch im Vision-Pfad** (`has_text_layer == False`); Vision: `raw_pdf_positions` aus Vision-Rohzeilen vor Dedup
  - Akzeptanz: 50 verlorene Positionen erscheinen alle als RED/MISSING im Export (Text **und** Vision)
  - Regressionsrisiko: mittel (abhängig von Positions-Spalten-Erkennung)
  - Test: `test_reconciler_reinjects_missing_positions`

- [x] **B3 — Absenz → immer ROT**
  - Datei/Zeile: `src/scoring/ensemble_scorer.py:977` (`_append_pdf_only_position_audits` ablösen) + Coverage-Guard am Scoring-Ende; `excel_exporter` nutzt `expected_position_count`
  - Änderung: synthetische Zeilen → Hard-Veto `RECONCILER_MISSING_POSITION` (RED); Coverage-Guard injiziert RED für jede nicht gescorte master_set-Position; `audit.expected_position_count` stempeln
  - Akzeptanz: jede PDF-Position hat ≥ 1 Zeile im Export — keine Lücke
  - Regressionsrisiko: mittel (Eingriff im großen Scorer; rein additiv halten)
  - Test: `test_every_pdf_position_has_a_row`

---

## BLOCK C — Qualitäts-Fixes (Tage)

- [x] **C1 — Geometrischer Kopf-/Fußzeilen-Filter**
  - Datei/Zeile: `src/ingestion/pdf_parser.py:534` (`_extract_text_blocks`)
  - Änderung: Blöcke mit `center_y < 0.08*H` oder `> 0.92*H` aus BOM-Extraktion ausschließen (nur Text-Pfad; Vision via Prompt)
  - Akzeptanz: „Blatt 1 v. 3", „Rev. 3", Seitenzahlen nicht mehr als Pos-/Mengen-Werte
  - Regressionsrisiko: mittel (Schwellen heuristisch → an 19 POC-PDFs gegenprüfen)
  - Test: `test_header_footer_geometrically_excluded`

- [x] **C2 — False-Green Vision-Pfad eindämmen**
  - Datei/Zeile: `src/scoring/green_gate.py:147` / `:233` (`_is_verified_scan`)
  - Änderung: `verified_scan` zusätzlich an Plausibilität binden (`value_plausible`: 0 < qty < 10000, Bezeichnung nicht leer); Alternative: Vision-ohne-Text-Layer auf YELLOW cappen
  - Akzeptanz: identischer Dual-Fehler (z. B. qty=70000) → nicht GREEN
  - Regressionsrisiko: hoch (Kernlogik) → `test_zero_false_positive` als Schutznetz
  - Test: `test_no_false_green_on_implausible_dual_agreement`

- [x] **C3 — Modell-Update**
  - Dateien: `.env` / `.env.example` + `src/llm/azure_openai.py:52-53` (Defaults), Docstring `:17-24`, Kommentar `:150`
  - Änderung: `AZURE_OPENAI_DEPLOYMENT_MAIN=gpt-4.1-mini` (Region Sweden Central); Default + Doku anpassen
  - Akzeptanz: Smoke-Test Text/Vision/Excel grün; Kostenlog zeigt neues Deployment
  - Regressionsrisiko: mittel → **zuletzt** ausrollen (stabiles Modell für A–C-Tests)
  - Test: `test_azure_openai`

---

## Abhängigkeits-Hinweise
- **B1 nie ohne C1** (sonst Phantom-Positionen aus Kopf-/Fußzeilen).
- **A2 erst scharf nach B2/B3** (vorher `expected_position_count == 0`).
- **C3 zuletzt** (Akzeptanztests gegen stabiles Modell).

---

## Status: ABGESCHLOSSEN (alle A1–C3 ✅)

**Tests:** 97 passed (volle Suite) + 128 passed / 3 pre-existing GF-failed / 1 skipped
(Real-PDF `test_parse_all.py`). Keine refactor-bedingte Regression.

### Umsetzungs-Abweichungen / Entscheidungen
- **B1 (Option A, kontext-sicher):** Das breite `\b\d{1,4}\b` ist **nicht** Default —
  auf rohem Text erzeugt es 729 Phantome/Datei und bricht den Zero-FP-Vertrag.
  Default-Muster bleiben spezifisch (`\d+-\d+ | K-\d+ | [A-Z]-\d+`), breites Muster
  per Instanz in `config/pos_patterns.yaml` aktivierbar. Fortlaufende Positionen
  kommen kontext-sicher aus der extrahierten Positions-Spalte (B2-Reconciler).
  Zusätzlich: Layout-Scaffolding-Strip (`ROW NNN:`, `[x=N-M]`) vor dem Scan.
- **C2 (Variante 1):** Plausibilitäts-Gate (`value_plausible`) statt YELLOW-Cap.
- **C3 (Endpoint korrigiert):** Modell auf `gpt-4.1-mini` migriert und per Live-Call
  verifiziert (`resp.model == gpt-4.1-mini`). Der in der Aufgabe genannte Endpoint
  `prozessia-sweden.openai.azure.com` lieferte **401**; funktionierend ist
  `n8n-sweden-prozessia.openai.azure.com` (empirisch bestätigt) — Endpoint dort belassen.

### Offene Sicherheits-/Folgepunkte (nicht Teil des Refactors)
- ⚠️ **Secret im Git:** `.env` mit echtem `AZURE_OPENAI_KEY` ist eingecheckt
  (`git ls-files .env`) → Key rotieren, `.env` aus dem Tracking nehmen (`.gitignore`).
- `DEPLOYMENT_MINI` wurde zu `gpt-4.1-mini` mitgezogen (war `gpt-4o`); kaum genutzt
  (Vision/Counter-Check laufen über `model_main`).
