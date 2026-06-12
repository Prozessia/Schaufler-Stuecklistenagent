# BACKLOG.md — Stücklistenagent Production-Readiness Review

**Erstellt:** 2026-06-11 · **Branch:** `deploy/vps-v1` · **Methode:** Vollständige Code-Lektüre aller `src/`-Module, Konfiguration, Deployment-Dateien, Frontend, Stammdaten-Abgleich gegen `config/target_template.xlsx`, Testlauf der Suite, Git-Historien-Prüfung.

> Hinweis: Das frühere Planungs-Backlog (B001–B100) wurde unverändert nach `backlog_v1_planning.md` verschoben; `backlog_status.md` referenziert es weiterhin.

**Kontext:** Zero-False-Positive-Vertrag — GRÜN muss 100 % sicher sein. Jeder Pfad, der ein falsches GRÜN erzeugen kann, ist mit ⚠️ ZERO-FALSE-POSITIVE RISK markiert.

**Architektur-Befund vorab (wichtig für die Einordnung):** Die implementierte Architektur weicht vom kommunizierten "Triple-Lock" ab. Es gibt zwei getrennte Pfade:
- **Text-Pfad (RB-1, `coordinate_table.py`):** deterministische Koordinaten-Rekonstruktion für born-digital PDFs. Hier ist das Design solide (Band-Identität als Zeilen-ID, Vollständigkeits-Anker vor jedem LLM-Call). GRÜN entsteht praktisch nur hier.
- **Vision-Pfad (`pdf_parser.py`):** Dual-Extraction + Koordinaten-Crosscheck. GRÜN ist hier an den Counter-Check gebunden — der in Produktion **abgeschaltet** ist (`enable_counter_check: false`), d. h. Scans liefern faktisch kein GRÜN.
- **Lock 2b (OpenDataLoader)** existiert nur auf einem unmerged Feature-Branch, nicht im Deploy-Code.

**Testlauf (2026-06-11):** 286 Tests kollektiert, Suite ist **nicht grün** — 3 Failures:
1. `test_parse_all.py::test_has_rows[GF/STL_08.05.13.pdf]` — Legacy-Parser extrahiert 0 Zeilen (rotierte Matrix; LLM-loser Testpfad → TEST-002).
2. + 3. `test_job_source_route.py::test_source_route_streams_uploaded_file` / `::test_source_route_blocks_paths_outside_upload_dir` — beide 401 statt 200/403: die Auth-Defaults (`login_enabled=True`) leaken in die Tests; die Tests setzen keinen Auth-Kontext (→ TEST-003).
Restliche 262 Tests passed (87 Ingestion-E2E + 175 Unit/Integration, 5.5 s).

---

## UMSETZUNGSSTAND (2026-06-12)

Implementiert und durch die Suite abgedeckt (Reihenfolge der Umsetzung):

| Datum | Tickets | Ergebnis |
|---|---|---|
| 2026-06-11 | **DATA-003** (Stammdaten-Import: 356 Werkstoffe, 179 Hersteller, 17 Teilegruppen + Manufacturer-Matching exakt-only) | größter GREEN-Hebel; `scripts/import_stammdaten.py` idempotent |
| 2026-06-11 | **BUG-003, BUG-009, BUG-013, TEST-002, TEST-003** | Veto-Stripping entfernt; `_parse_decimal` fullmatch + positionale Dimension-MISMATCHes; NEUTRAL nur bei leerem Rohwert (`empty_non_required_as_yellow: true`); Suite grün |
| 2026-06-12 | **BUG-001, BUG-002, BUG-004, BUG-006, BUG-007, BUG-008** (Evidenz-Härtung) | Anchor-Fallback token-gebunden + nie allein GREEN (beide Pfade); Mengen ohne Bestätigungs-Bias (genau 1 Kandidat); CPN-Dokumentprüfung token-gebunden, ≥6 Zeichen, kein strict_exact; 0.95-Generic-Bypass entfernt (Identität nur per strict-exact-Evidenz); UNCERTAIN-GREEN nur mit Master-Data; Boost 0.92→0.89; Fuzzy aus GREEN-Whitelist |
| 2026-06-12 | **BUG-005 (funktional), BUG-010, BUG-012, BUG-015, BUG-018, BUG-019, BUG-020, BUG-021** | `validate_contract` + CONTRACT_DEVIATION-Warnungen + /settings/system; numerische Token-Vergleiche dezimaltreu; vision_verifier-Regexes repariert; Position "1.0"→"1"/"007"→"7"; CSV-Parser; .xls blockiert (400); Legacy-Fallback setzt immer vision_fallback_reason; PLAUS:-Flags cappen GREEN auf YELLOW |
| 2026-06-12 | **BUG-011, BUG-014, BUG-016, BUG-017** | Vorkommens-Zähler → Unterdeckungs-Synthese (T-007 auf Vision sichtbar); beide Demo-Overrides entfernt (Mismatch = RED); Dual-Pairing per SequenceMatcher + row_count_delta; per-Seite-Strukturcheck mit Re-Detect ab Seite 2 |
| 2026-06-12 | **SEC-002, SEC-003, SEC-004, SEC-005, SEC-006** | Default-Admin default aus + Warnung; CSRF Double-Submit serverseitig; Rate-Limit/Lockout/compare_digest; Magic-Byte-Check; Container non-root + HEALTHCHECK + .dockerignore + /docs abschaltbar |
| offen | **SEC-001** | Rotation/History-Purge nur durch Betreiber — Anleitung: docs/runbook_sec001_key_rotation.md |

Offene Tickets: SEC-001 (User-Aktion), OPS-001…008, ARCH-001…005, PERF-001/002, DATA-001/002/004, FE-001…003, TEST-001.

---

## TEIL 1: TECHNISCHES BACKLOG

### Kritische Zero-False-Positive-Tickets

---

### BUG-001: Check-2-Fallback "global_text_row_anchor" gibt den Prüfwert als "unabhängige Extraktion" zurück

**Priority:** Critical
**Effort:** L
**Area:** pdf_value_extractor / pipeline

**Problem:**
[pdf_value_extractor.py:320-339](src/scoring/pdf_value_extractor.py#L320-L339) — wenn keine Koordinaten-Location existiert, sucht `_extract_from_document_text_layer` einen Anker (Detail Number / Customer Part Number) im Text-Layer und prüft dann nur, ob der **gemappte Wert als Substring** im ±1-Zeilen-Kontext vorkommt (`_context_contains_expected_value`). Bei Treffer wird `extracted_value = mapped_value` zurückgegeben — Check 2 "extrahiert" also exakt den Wert, den es verifizieren soll, und Check 3 vergleicht den Wert anschließend mit sich selbst (immer MATCH). Zusätzlich: für numerische Werte < 4 Stellen greift die Relaxed-Core-Sperre ([Zeile 532-534](src/scoring/pdf_value_extractor.py#L532)) **nicht** für den ersten Pfad `expected_norm in context_norm` — eine "2" matcht jede 2 im Kontextfenster, auch innerhalb von "3520".

**Impact:**
⚠️ ZERO-FALSE-POSITIVE RISK. Die "unabhängige" zweite Verifikation ist auf diesem Pfad zirkulär. Ein vom LLM falsch zugeordneter Wert, der zufällig irgendwo in den 3 Kontextzeilen vorkommt (Maße, Positionsnummern, Mengen anderer Spalten), gilt als verifiziert — mit Konfidenz ≥ 0.90, was zusätzlich BUG-003 auslöst.

**Suggested fix:**
Substring-Containment durch token-gebundene Prüfung ersetzen (Wortgrenzen, keine Treffer über Zellgrenzen), kurze numerische Werte (< 4 Stellen) auf diesem Pfad grundsätzlich von GREEN ausschließen (nur UNCERTAIN/YELLOW), und `check2_reason=global_text_row_anchor` im Gate explizit als schwache Evidenz behandeln, die nie allein GREEN trägt.

---

### BUG-002: Mengen-Extraktion mit Bestätigungs-Bias ("expected in candidates → expected")

**Priority:** Critical
**Effort:** M
**Area:** pdf_value_extractor

**Problem:**
[pdf_value_extractor.py:341-367](src/scoring/pdf_value_extractor.py#L341-L367) — `_extract_quantity_value` sammelt alle Zahlen nach dem Anker im Kontextfenster und gibt `expected_int` zurück, **wenn er irgendwo unter den Kandidaten ist**. Ein 3-Zeilen-Fenster einer BOM enthält typischerweise Dutzende Zahlen (Maße "400x78x55", Positionen, Werkstoffnummern; `_parse_quantity_int` entfernt zudem alle Nicht-Ziffern: "4x10"→410, "M12"→12).

**Impact:**
⚠️ ZERO-FALSE-POSITIVE RISK. Eine falsch gelesene Stückzahl (z. B. 4 statt 1) wird bestätigt, sobald die 4 zufällig im Kontext vorkommt — bei Design/Spare Count ist das exakt der gefürchtete Fehler (falsche Bestellmenge).

**Suggested fix:**
Nur die Zahl an der erwarteten Spaltenposition akzeptieren (x-Korridor), nicht "irgendwo im Fenster"; `_parse_quantity_int` darf gemischte Tokens nicht zu Ganzzahlen kollabieren; bei mehreren Kandidaten immer UNCERTAIN.

---

### BUG-003: Hochkonfidenter Text-Fallback streicht Lock-2-Vetos (Koordinaten-Mismatch, Spaltenkonflikt)

**Priority:** Critical
**Effort:** S
**Area:** ensemble_scorer

**Problem:**
[ensemble_scorer.py:341-349](src/scoring/ensemble_scorer.py#L341-L349) — wenn `extraction.reason == "global_text_row_anchor"` mit Konfidenz ≥ 0.90 und MATCH, werden die harten Vetos `PDF_COORDINATE_MISMATCH` und `PDF_COLUMN_CONFLICT` **entfernt**. Die präzise Koordinaten-Verifikation (Lock 2) hat einen Widerspruch gefunden — und der deutlich schwächere Volltext-Fallback (BUG-001) überstimmt sie.

**Impact:**
⚠️ ZERO-FALSE-POSITIVE RISK. Genau der Fall "Wert existiert im Dokument, steht aber in der falschen Spalte/Zeile" (Spalten-Bleeding, das Kernrisiko bei BOM-Tabellen) wird entschärft statt eskaliert. In Kombination mit BUG-001 kann eine nachweislich widersprüchliche Zelle GRÜN werden.

**Suggested fix:**
Hierarchie umkehren: ein Koordinaten-Veto (starke, ortsgebundene Evidenz) darf nie von einer ortsungebundenen Volltext-Bestätigung überschrieben werden. Veto-Strippen entfernen; stattdessen YELLOW mit beiden Evidenzen im Audit.

---

### BUG-004: Customer Part Number — "verified via global pdf text layer" per Substring über das gesamte Dokument

**Priority:** Critical
**Effort:** M
**Area:** value_comparator / ensemble_scorer

**Problem:**
[value_comparator.py:649-667](src/scoring/value_comparator.py#L649-L667) — `_customer_part_number_verified_via_text_layer` prüft `expected_core in document_core`, wobei `document_core` das **komplette Dokument** als eine zusammengezogene alphanumerische Zeichenkette ist. Treffer können über Token-/Zellen-/Zeilengrenzen hinweg entstehen; Zeilen-Lokalität wird nicht geprüft. Das Ergebnis wird als `strict_exact_match=True` MATCH zurückgegeben, und [ensemble_scorer.py:304-317](src/scoring/ensemble_scorer.py#L304-L317) setzt darauf `check2_found=True` mit hoher Konfidenz — Kategorie A wird damit GREEN-fähig.

**Impact:**
⚠️ ZERO-FALSE-POSITIVE RISK. Die Teilenummer von Zeile 12 "verifiziert" die Zelle von Zeile 5; bei kundentypisch ähnlichen Teilenummern (gemeinsame Präfixe) entstehen falsche Bestätigungen. Eine vertauschte Teilenummer ist für den Einkäufer kaum erkennbar — Worst Case fürs Vertrauen.

**Suggested fix:**
Verifikation auf die per Anker gematchte Zeile begrenzen (Zeilen-Kontext statt Dokument-Kern), Mindestlänge für den Kern (≥ 6 Zeichen), `strict_exact_match=True` nur bei tokenweisem Treffer in der eigenen Zeile.

---

### BUG-005: Produktions-Config deaktiviert Schutzmechanismen (Counter-Check aus, Schwellen gesenkt)

**Priority:** Critical
**Effort:** S
**Area:** config / scoring

**Problem:**
[config/app_config.yaml:13-24](config/app_config.yaml#L13-L24) weicht vom dokumentierten Sicherheitsdesign ab:
- `enable_counter_check: false` — der Vision-Counter-Check (CHECK5) läuft in Produktion **nie**; der Verified-Scan-GREEN-Pfad ist damit tot (konservativ), aber der Text-Pfad verliert seine letzte unabhängige Bild-Verifikation.
- `conservative_mode: false` (CLAUDE.md: `true`).
- `verify_green_threshold: 0.90` (Code-Default 0.95), `green_extraction_min_confidence: 0.70` (Default 0.80) — JSON-reparierte Extraktionen (Konfidenz-Cap 0.86) bleiben dadurch GREEN-fähig.
- `soft_vetoes_as_yellow: true` — Koordinaten-Mismatches werden für die Klassifikation RED→YELLOW herabgestuft.
- `empty_non_required_as_neutral: true` — siehe BUG-013.

**Impact:**
⚠️ ZERO-FALSE-POSITIVE RISK (kumulativ). Jede einzelne Lockerung ist begründbar; in Summe ist der "Triple-Lock" in Produktion ein Single-Lock mit gesenkten Schwellen — und niemand sieht das im Dashboard.

**Suggested fix:**
Schwellen-Governance: produktive Config beim Start gegen ein "Contract Minimum" validieren (Fail-fast oder lautes Warn-Banner, z. B. wenn counter_check aus UND verify_threshold < 0.95); aktive Abweichungen in `/settings/system` und im Dashboard anzeigen; Änderungen nur per dokumentiertem Review.

---

### BUG-006: Generischer Bypass der Methoden-Verifikation: `transform_confidence >= 0.95` reicht für den GREEN-Methodencheck

**Priority:** Critical
**Effort:** M
**Area:** green_gate / transform pipeline

**Problem:**
[green_gate.py:234-239](src/scoring/green_gate.py#L234-L239) — `_text_path_method_verified` akzeptiert **jede** Transformationsmethode bei `transform_confidence >= 0.95`. Die Transform-Pipeline vergibt aber pauschal 0.95 für `passthrough` und `text_cleanup` bei allen String-Feldern ([pipeline.py:677-695](src/transform/pipeline.py#L677-L695)). Damit ist die Whitelist `_TEXT_PATH_METHODS` wirkungslos: jedes String-Feld gilt als "methodisch verifiziert". Zusätzlich erlaubt der Text-Pfad GREEN bei `value_match_result == UNCERTAIN` ([green_gate.py:217-220](src/scoring/green_gate.py#L217-L220)).

**Impact:**
⚠️ ZERO-FALSE-POSITIVE RISK. Auf dem Text-Pfad genügt: Mapping-Konfidenz ≥ 0.90 (LLM-Selbsteinschätzung bzw. Value-Evidence-Boost, BUG-007) + Regel-Score + vorhandene Extraktion. Da die Check-2-"Extraktion" auf dem deterministischen Pfad per Konstruktion derselbe Text-Layer-Inhalt ist, prüft niemand mehr, ob die **semantische Spaltenzuordnung** stimmt. Ein selbstbewusst falsches Spalten-Mapping (Rohmaß↔Fertigmaß, Bemerkung↔Beschreibung) erzeugt systematisch falsche GRÜNs über die ganze Spalte.

**Suggested fix:**
Den 0.95-Generic-Bypass entfernen (Whitelist exklusiv); `passthrough` nie als verifizierte Methode werten; GREEN bei UNCERTAIN-Match nur für Felder mit Master-Data-/Format-Anker. Flankierend ARCH-005.

---

### BUG-007: Value-Evidence-Boost hebt die Mapping-Konfidenz über die GREEN-Schwelle (0.92 > 0.90)

**Priority:** High
**Effort:** M
**Area:** mapping_validator

**Problem:**
[mapping_validator.py:25-31, 395-417](src/mapping/mapping_validator.py#L395-L417) — wenn ≥ 40 % der Spaltenwerte zum Zieltyp passen, wird die Mapping-Konfidenz auf 0.92 angehoben — knapp über die GREEN-Anforderung `candidate_confidence >= 0.90` des Text-Pfads. Für Material zählt jeder Katalog-/Formatmatch als Evidenz; eine **Norm-Spalte** ("DIN 4957", "EN 10088-2") mit eingebetteten Werkstoffnummern erreicht die 40 % leicht, ohne die Material-Spalte zu sein.

**Impact:**
⚠️ ZERO-FALSE-POSITIVE RISK (in Kombination mit BUG-006): der Boost hebt genau die Schwelle aus, die ein unsicheres LLM-Mapping von GREEN fernhalten soll.

**Suggested fix:**
Boost auf max. 0.89 begrenzen (Evidenz darf YELLOW verbessern, nie GREEN freischalten), oder nur zulassen, wenn LLM-Konfidenz ≥ 0.75 UND ein Header-Keyword-Match dazukommt.

---

### BUG-008: Fuzzy-Master-Data-Matches sind GREEN-fähig (WRatio-Cutoff 85 bei Nitrier-/Beschichtungs-Aliassen)

**Priority:** High
**Effort:** M
**Area:** stammdaten_matching / green_gate

**Problem:**
`fuzzy_alias` und `fuzzy_material` stehen in `_TEXT_PATH_METHODS` ([green_gate.py:22-23](src/scoring/green_gate.py#L22-L23)). Für Nitrier-Arten und Beschichtungen liegt der Fuzzy-Cutoff bei **85** (WRatio mit Partial-Matching, [master_data_matcher.py:24](src/transform/master_data_matcher.py#L24)) — bei kurzen, ähnlichen Fachbegriffen kann der falsche Kanon gewählt und GRÜN werden, wenn der Comparator denselben Fehler kanonisiert. Material hat 95 + Familien-Konflikt-Guard (gut); die Alias-Kataloge nicht.

**Impact:**
⚠️ ZERO-FALSE-POSITIVE RISK (begrenzt auf Nitriding type/Coating — beide Kategorie A; eine falsche Nitrier-Art ist ein fachlich relevanter Wärmebehandlungsfehler).

**Suggested fix:**
Fuzzy-Methoden aus `_TEXT_PATH_METHODS` entfernen (Fuzzy = YELLOW-Vorschlag, nie Auto-GREEN), oder Cutoff für Alias-Kataloge ≥ 95 + token_sort_ratio statt WRatio + Mindestlängen.

---

### BUG-009: `_parse_decimal` nutzt `re.search` — Partial-Token-Matches gelten als "exact numeric match"

**Priority:** High
**Effort:** S
**Area:** value_comparator

**Problem:**
[value_comparator.py:896-904](src/scoring/value_comparator.py#L896-L904) — `_parse_decimal("500x300x200")` liefert 500.0 (erste Zahl); `_compare_decimal("500", "500x300x200")` ist damit MATCH mit `strict_exact_match=True` und Detail "exact numeric match". Für Dimensions X/D bestätigt also **immer die erste Zahl** des Quellstrings; bei Nitriding depth wird "0.2" vs. "0.2-0.3" als exakt gewertet (Range-Information verloren). Y/L und Z sind über den positionsbasierten Fix A abgesichert; X/D umgeht ihn über den direkten Pfad.

**Impact:**
⚠️ ZERO-FALSE-POSITIVE RISK (moderat: meist ist die erste Komponente tatsächlich X/D, aber der "exact"-Anspruch ist falsch etikettiert und Range-Verluste werden als exakt deklariert).

**Suggested fix:**
`fullmatch` statt `search` für den "exact"-Pfad; kombinierte Strings ausschließlich über den positionsbasierten Komponenten-Vergleich (Fix A) matchen lassen.

---

### BUG-010: Koordinaten-Crosscheck normalisiert Punkte weg — "4.5" bestätigt "45"

**Priority:** High
**Effort:** S
**Area:** pymupdf_lock (Vision-Pfad)

**Problem:**
[pdf_parser.py:2359-2364](src/ingestion/pdf_parser.py#L2359-L2364) — `_normalize_token` entfernt alle Nicht-Alphanumerik: "4.5"→"45", "1.2343"→"12343", "1-2"→"12". Beim Token-Match des Koordinaten-Checks bestätigt ein PDF-Wort "45" damit einen Vision-Wert "4.5" (COORDOK) statt einen Mismatch zu erzeugen. `_token_variants` ([Zeile 2401-2403](src/ingestion/pdf_parser.py#L2401)) ist zudem ein Stub, der die versprochenen Dezimal-Varianten nicht baut.

**Impact:**
⚠️ ZERO-FALSE-POSITIVE RISK auf dem Scan-Pfad (aktuell ohne GREEN-Folge, da Counter-Check aus — aber die COORDOK-Flags fließen in Audits und jede künftige Lockerung).

**Suggested fix:**
Numerische Tokens separat normalisieren (Dezimaltrennzeichen vereinheitlichen statt löschen); Mismatch-Erkennung auf Ziffernfolge **mit** Strukturzeichen.

---

### BUG-011: Vision-Pfad — Zeilen-Identität bleibt der Positionswert; doppelte Positionsnummern kollabieren im Master-Set

**Priority:** High
**Effort:** L
**Area:** reconciliation / Vision-Pfad

**Problem:**
RB-1 (Band-Identität) gilt nur für den Text-Pfad. Auf dem Vision-Pfad ist das Master-Set `set(positions)` ([position_reconciler.py:77-95](src/reconciliation/position_reconciler.py#L77-L95)): zwei **verschiedene Teile mit derselben Positionsnummer** (T-007) sind ein einziger Eintrag. Lässt das Vision-Modell eine der Zeilen aus, gilt die Position trotzdem als abgedeckt — der Verlust ist für Reconciler, B3-Coverage-Guard und Export-Assertion unsichtbar. Das trifft auch **digitale** PDFs, die auf den Vision-Fallback laufen (GF-Layout).

**Impact:**
Stiller Zeilenverlust auf dem Vision-Pfad (Zero-Data-Loss-Lücke). Für die Demo: eine fehlende Bestellposition ist genauso vertrauenszerstörend wie ein falsches GRÜN.

**Suggested fix:**
Master-Set auf dem Vision-Pfad als Multiset (Position × Vorkommens-Zähler aus den Raw-Rows) oder Ordinal-Schlüssel `seite×10000+reihenfolge` aus den Raw-Vision-Zeilen; Differenz Rohzeilen vs. emittierte Zeilen immer als RED-Pseudozeile ausweisen.

---

### BUG-012: Counter-Check-Verifier hat defekte Regexes (doppelt escaptes `\\s`/`\\d` in Raw-Strings)

**Priority:** Medium
**Effort:** S
**Area:** vision_verifier

**Problem:**
[vision_verifier.py:313-346, 377-385](src/scoring/vision_verifier.py#L313-L385) — `_safe_json_loads` und `_normalize_for_compare` verwenden `r"...\\s..."` → die Patterns matchen literal `\s`/`\d` statt Whitespace/Ziffern. Folgen: Markdown-Fences werden nicht zuverlässig gestrippt, Trailing-Comma-Reparatur greift nicht, Whitespace-Kollaps und Dezimal-Komma-Harmonisierung im Vergleich sind wirkungslos.

**Impact:**
Nur falsch-negativ (Counter-Check scheitert öfter als nötig → weniger GREEN, verlorene Vision-Kosten). Aber: der Schutzmechanismus ist beim Reaktivieren (BUG-005) teilweise funktionsunfähig — und das fällt nicht auf, weil er in Prod deaktiviert ist.

**Suggested fix:**
Backslashes korrigieren; Unit-Tests für beide Helfer; mit der korrekten `pdf_parser._safe_json_loads`-Implementierung deduplizieren.

---

### BUG-013: `empty_non_required_as_neutral` macht Mapping-Fehlschläge mit vorhandenem Quellwert zu NEUTRAL (Score 1.0)

**Priority:** High
**Effort:** S
**Area:** ensemble_scorer / config

**Problem:**
[ensemble_scorer.py:751-786](src/scoring/ensemble_scorer.py#L751-L786) — bei `empty_non_required_as_neutral: true` (Prod-Config!) wird eine Zelle, deren **Quellwert existiert, aber nicht transformiert werden konnte** (`MAPPING_FAILURE_WITH_SOURCE_VALUE`), als NEUTRAL mit `final_score 1.0` ausgewiesen und aus `total_scored` herausgerechnet. Dashboard/Statistik und Reviewer sehen kein Review-Signal.

**Impact:**
Verlorene Quellinformation wird als "intentional leer" maskiert → Datenverlust am Zellrand, geschönte Automationsquote. Kein falsches GRÜN, aber ein falsches "nichts zu tun".

**Suggested fix:**
NEUTRAL nur bei **leerem Rohwert**. Mapping-Fehlschlag mit Quellwert immer mindestens YELLOW (`empty_non_required_as_yellow` existiert bereits — Default-Empfehlung umdrehen).

---

### BUG-014: Demo-Overrides "frontend_ui_pass_through" und "Detail-Number-Release" stufen RED pauschal auf YELLOW herab

**Priority:** Medium
**Effort:** M
**Area:** ensemble_scorer

**Problem:**
[ensemble_scorer.py:49-61, 351-364, 498-514, 1037-1088](src/scoring/ensemble_scorer.py#L1037-L1088) — zwei hartkodierte Sonderpfade mit "Fix:"-Prosa: (1) Description/Dimensions-Zellen mit Transform-Konfidenz ≥ 0.90 werden von RED auf YELLOW gehoben, (2) Detail-Number-Wert-Mismatches werden "für die UI freigegeben", inklusive Entfernen des `CHECK3_VALUE_MISMATCH`-Vetos. Beides sind Symptom-Patches (mutmaßlich gegen "alles rot" in der Demo), keine begründeten Regeln.

**Impact:**
Echte Wert-Widersprüche (Lock-2/3-Mismatch!) erscheinen als "nur prüfen" statt "falsch" — geschwächte RED-Semantik bei genau den Feldern (Maße, Positionsnummer), in denen Mismatches am gefährlichsten sind.

**Suggested fix:**
Beide Overrides entfernen oder durch eine dokumentierte, konfigurierbare Policy ersetzen; UI-Probleme im Frontend lösen, nicht im Scorer.

---

### BUG-015: `normalize_position` normalisiert "1.0" ≠ "1" nicht (Docstring verspricht es)

**Priority:** Medium
**Effort:** S
**Area:** core/positions / reconciliation

**Problem:**
[positions.py:19-30](src/core/positions.py#L19-L30) — der Docstring nennt `"1.0"` als Beispiel konsistenter Normalisierung, der Code macht nur Upper-Case/Whitespace/Dash-Tightening. Liest der Vision-Pass "1.0" und die Spaltenextraktion "1" (oder Excel liefert "1.0" aus openpyxl-Floats), matchen die Sets nicht → Phantom-MISSING-Zeilen (RED) oder fehlschlagende Export-Guards.

**Impact:**
Falsche RED/MISSING-Zeilen und potenzielle `ZeroDataLossError`-Abbrüche bei vollständigen Exporten (False-Alarm-Richtung, blockiert aber den Download).

**Suggested fix:**
Numerische Positionen kanonisieren ("1.0"→"1", führende Nullen definiert) — an genau einer Stelle, mit Tabellen-Tests.

---

### BUG-016: Dual-Extraction — Zeilen-Pairing per Index/Nächster-Nachbar kann Mismatches systematisch verdecken

**Priority:** Medium
**Effort:** M
**Area:** vision_lock

**Problem:**
[pdf_parser.py:1277-1411](src/ingestion/pdf_parser.py#L1277-L1411) — Zeilen aus Lauf A und B werden per Anker, sonst per Index, sonst per "nächste freie Zeile" gepaart. Verrutscht Lauf B um eine Zeile, vergleicht der Rest paarweise falsche Zeilen — teils Pseudo-Mismatches (konservativ), bei strukturell ähnlichen Zeilen aber auch maskierte echte Differenzen. Identische Halluzinationen beider Läufe sind prinzipbedingt unsichtbar; Extraktion B wird verworfen.

**Impact:**
Geschwächtes Lock-1-Qualitätssignal auf dem Scan-Pfad (DUAL-Flags speisen Vetos und Audits).

**Suggested fix:**
Pairing über Anker + Reihenfolge-Monotonie (Sequence Alignment auf Anker-Sequenzen); ungepaarte Zeilen pauschal als Zeilen-DUAL-Mismatch; Zeilenzahl-Differenz A↔B in die Completeness-Reason.

---

### BUG-017: Phase A erkennt die Spaltenstruktur nur auf Seite 1

**Priority:** Medium
**Effort:** M
**Area:** vision_lock

**Problem:**
[pdf_parser.py:84-102](src/ingestion/pdf_parser.py#L84-L102) — `_detect_columns_via_vision(images[0])`. Mehrseitige BOMs mit abweichender Struktur ab Seite 2 (anderes Formular, zusätzliche Spalten) werden mit dem Seite-1-Schema extrahiert; Werte landen in falschen Keys. (Der Text-Pfad löst das bereits per-Section — nur Vision betroffen.)

**Impact:**
Spaltenversatz auf Folgeseiten → falsche Zuordnungen, die der Koordinaten-Check nicht systematisch fängt.

**Suggested fix:**
Pro Seite ein leichter Struktur-Check (Header erkannt? Spaltenzahl plausibel?); bei Abweichung Phase A für diese Seite wiederholen.

---

### BUG-018: CSV-Upload wird akzeptiert, aber `parse_file` wirft "Unsupported file format"

**Priority:** High
**Effort:** S
**Area:** api / ingestion

**Problem:**
[upload.py:19](src/api/routes/upload.py#L19) erlaubt `.csv`; [file_router.py:14](src/ingestion/file_router.py#L14) mappt `.csv → FileFormat.CSV`; [structure_normalizer.py:34-102](src/ingestion/structure_normalizer.py#L34-L102) behandelt nur EXCEL und PDF → `ValueError`. Jeder CSV-Upload endet als fehlgeschlagener Job mit kryptischer Meldung.

**Impact:**
Kaputter Produktvertrag (UI bewirbt CSV), schlechte Demo-Erfahrung.

**Suggested fix:**
CSV-Parser ergänzen (csv → ParsedBOM analog `parse_excel`) **oder** `.csv` aus `ALLOWED_EXTENSIONS` und dem Frontend entfernen.

---

### BUG-019: `.xls` (Legacy-BIFF) wird akzeptiert, openpyxl kann es nicht lesen

**Priority:** Medium
**Effort:** S
**Area:** api / ingestion

**Problem:**
`.xls` wird als EXCEL geroutet ([file_router.py:11-15](src/ingestion/file_router.py#L11-L15)), aber `openpyxl.load_workbook` unterstützt nur OOXML → Laufzeit-Fail.

**Impact:**
Wie BUG-018: akzeptierter Upload, später kryptischer Fehler.

**Suggested fix:**
Produktentscheidung: blocken mit klarer Meldung ("bitte als .xlsx speichern") oder Konvertierung einbauen.

---

### BUG-020: Legacy-Parser-Fallback auf Text-Layer-PDFs setzt kein `vision_fallback_reason`

**Priority:** Medium
**Effort:** S
**Area:** structure_normalizer / green_gate

**Problem:**
[structure_normalizer.py:93-100](src/ingestion/structure_normalizer.py#L93-L100) — schlägt der primäre Parse-Pfad unerwartet fehl, läuft der **Legacy-Parser**; `vision_fallback_reason` wird nur gesetzt, wenn `has_text_layer == False`. Auf einem Text-Layer-PDF fehlt das Signal; der Green-Gate-Block `VISION_FALLBACK_TO_LEGACY_PARSER` greift nicht. GREEN ist faktisch durch fehlende `source_locations` blockiert — aber implizit statt by design.

**Impact:**
Verlässt sich auf einen Nebeneffekt; jede künftige Legacy-Parser-Änderung (z. B. source_locations ergänzen) öffnet unbemerkt einen GREEN-Pfad ohne Locks. Completeness-Reason ist in diesem Zustand irreführend.

**Suggested fix:**
Explizites Flag `legacy_parser_used` bei jedem Fallback; im Green Gate hart blocken; Completeness-Reason entsprechend.

---

### BUG-021: Post-Validation-Plausibilitäts-Flags erreichen den Scorer nicht (toter Sicherungspfad)

**Priority:** Medium
**Effort:** S
**Area:** vision_lock / ensemble_scorer

**Problem:**
[pdf_parser.py:1605-1630](src/ingestion/pdf_parser.py#L1605-L1630) erzeugt Plausibilitäts-Flags ("possible column bleeding", "dimension digit count inconsistent") mit dem Kommentar "Flagged cells will be capped at RED in the ensemble scorer". Der Scorer konsumiert aber nur `COORDMISS:`/`COORDCOL:`/`DUAL:`-Präfixe ([ensemble_scorer.py:1494-1520](src/scoring/ensemble_scorer.py#L1494-L1520)) — die präfixlosen Flags wirken nirgends.

**Impact:**
Erkannte OCR-Verdachtsfälle haben keinerlei Scoring-Wirkung; der Code-Kommentar ist falsch.

**Suggested fix:**
Eigenes Präfix (`PLAUS:`) + Auswertung als Soft-Veto (YELLOW-Cap) — oder die Prüfung ehrlich als "nur Audit" deklarieren.

---

### Sicherheit

---

### SEC-001: Echter Azure-OpenAI-Key liegt in der Git-Historie

**Priority:** Critical
**Effort:** S
**Area:** secrets / ops

**Problem:**
`git log --all -p -- .env` zeigt einen realen `AZURE_OPENAI_KEY` im Initial-Commit (Datei später entfernt; `.gitignore` heute korrekt). [INDUSTRIAL_REFACTOR_PLAN.md:199](INDUSTRIAL_REFACTOR_PLAN.md#L199) markiert die Rotation selbst als offen.

**Impact:**
Jeder mit Repo-Zugriff (inkl. alter Clones, GitHub-Remote) kann auf die Azure-OpenAI-Ressource zugreifen — Kosten, Datenabfluss, DSGVO-Vorfall.

**Suggested fix:**
Key in Azure sofort rotieren; Historie mit `git filter-repo` bereinigen + Force-Push; gitleaks als CI-Gate.

---

### SEC-002: Default-Login admin/admin funktioniert immer (auch mit gesetztem Passwort)

**Priority:** Critical
**Effort:** S
**Area:** auth

**Problem:**
[auth.py:101-116](src/core/auth.py#L101-L116) — `default_admin_match` akzeptiert admin/admin **zusätzlich** zu den konfigurierten Credentials, solange `LOGIN_ALLOW_DEFAULT_ADMIN` nicht explizit false ist. Default ist `true`, und [.env.deploy.example](.env.deploy.example) setzt es **auch für Produktion** auf `true` (der compose-Kommentar "cookie login (admin/admin)" bestätigt die Praxis). Passwörter werden zudem im Klartext aus Env verglichen.

**Impact:**
Öffentlich erreichbares Produktionssystem (stuecklistenagent.prozessia.space) mit bekanntem Standard-Login: voller Zugriff auf Kunden-Stücklisten, Master-Data-Schreibrechte über /settings, Job-Purge.

**Suggested fix:**
Default auf `false`; Start-Abbruch (oder lautes Banner), wenn Default-Admin in Prod-Konstellation aktiv; deploy-Example korrigieren; Passwort-Hash statt Klartext.

---

### SEC-003: CSRF — Frontend implementiert Double-Submit, Backend validiert nichts

**Priority:** Medium
**Effort:** M
**Area:** auth / api

**Problem:**
[frontend/src/lib/api.ts:17-35](frontend/src/lib/api.ts#L17-L35) sendet `X-CSRF-Token` aus einem `csrf_token`-Cookie und behauptet, das Backend nutze das Double-Submit-Pattern. Das Backend setzt dieses Cookie nie und prüft den Header nirgends ([auth.py](src/core/auth.py), [routes/auth.py](src/api/routes/auth.py)). Schutz ist faktisch nur `SameSite=lax`.

**Impact:**
Mutierende Endpunkte (Zell-Edits, Master-Data-Schreiben, Job-Purge) sind in Subdomain-/Browser-Lücken-Szenarien CSRF-exponiert; gefährlicher ist die falsche Sicherheitsannahme im Code.

**Suggested fix:**
Double-Submit serverseitig implementieren (Cookie beim Login, Header-Pflicht bei POST/PUT/PATCH/DELETE) — das Frontend ist vorbereitet.

---

### SEC-004: Kein Rate-Limit, kein Login-Lockout, API-Key-Vergleich nicht konstantzeitig

**Priority:** Medium
**Effort:** M
**Area:** auth / api

**Problem:**
`/auth/login` ist unbegrenzt brute-forcebar; Upload/Verarbeitung pro Request unlimitiert (Azure-Kosten-DoS durch wiederholte 50-MB-Uploads); [auth.py:176-177](src/core/auth.py#L176-L177) vergleicht den API-Key mit `==` statt `secrets.compare_digest`.

**Impact:**
Bruteforce gegen SEC-002 trivial; ein Angreifer kann beliebige Azure-Kosten erzeugen.

**Suggested fix:**
Rate-Limit (Token-Bucket per IP+User) für /auth/login und /upload; Lockout nach N Fehlversuchen; compare_digest.

---

### SEC-005: Upload validiert nur die Dateiendung (kein Magic-Byte-Check)

**Priority:** Medium
**Effort:** S
**Area:** api / upload

**Problem:**
[upload.py:40-52](src/api/routes/upload.py#L40-L52) prüft Extension und Größe; eine als `.pdf` benannte beliebige Datei läuft bis in PyMuPDF/openpyxl. (Path-Traversal ist sauber gelöst.)

**Impact:**
Unnötige Parser-Angriffsfläche (PyMuPDF-CVEs) auf einem internetöffentlichen Endpoint; verwirrende Fehlerbilder.

**Suggested fix:**
Magic-Bytes gegen die Extension prüfen — die Logik existiert in `detect_format`, sie wird beim Upload nur nicht genutzt.

---

### SEC-006: Backend-Container als root, `/docs`+`/openapi.json` öffentlich, `data/` ins Image gebacken

**Priority:** Medium
**Effort:** M
**Area:** ops / deployment

**Problem:**
[Dockerfile.backend](Dockerfile.backend) — kein `USER`, kein `HEALTHCHECK`, `COPY data/ data/` nimmt lokale jobs.db/Uploads ins Image. [auth.py:148-156](src/core/auth.py#L148-L156) exempts `/docs` und `/openapi.json` von Auth; die Caddy-Route exponiert beide öffentlich.

**Impact:**
API-Oberfläche öffentlich einsehbar; Container-Kompromittierung = root; potenziell Kundendaten im Image.

**Suggested fix:**
Non-root-User + HEALTHCHECK; `data/` per .dockerignore ausschließen (Volume existiert); /docs in Prod deaktivieren oder hinter Auth.

---

### Ops / Konfiguration

---

### OPS-001: Dependencies ungepinnt (nur Untergrenzen, kein Lockfile)

**Priority:** High
**Effort:** S
**Area:** ops

**Problem:**
[requirements.txt](requirements.txt) verwendet ausschließlich `>=`. Jeder Build zieht andere Versionen (openai-SDK-Majors, pymupdf-API-Änderungen) — nicht reproduzierbar.

**Impact:**
"Gestern lief es noch"-Deployments; unkontrollierte Updates.

**Suggested fix:**
pip-compile/uv-Lockfile; Renovate/Dependabot.

---

### OPS-002: `/health` prüft nichts; keine Backend-Healthchecks in Compose

**Priority:** Medium
**Effort:** S
**Area:** ops

**Problem:**
[main.py:79-81](src/api/main.py#L79-L81) liefert statisch ok; [docker-compose.prod.yml](docker-compose.prod.yml) hat für backend/caddy keine healthchecks. Azure-Erreichbarkeit, jobs.db-Schreibbarkeit, Template-/Stammdaten-Existenz werden nie geprüft.

**Impact:**
Toter Worker, volle Disk oder fehlender Azure-Key fallen erst beim nächsten Kunden-Upload auf.

**Suggested fix:**
Deep-Health (DB-Write-Probe, Config/Template/Kataloge vorhanden, optional Azure-Ping gecacht); compose-healthchecks + `depends_on: condition: service_healthy`.

---

### OPS-003: Keine CI-Pipeline

**Priority:** High
**Effort:** M
**Area:** ops

**Problem:**
Kein CI-Workflow im Repo. 286 Tests existieren, werden aber nicht erzwungen (Commit `c85ef95 "r"` zeigt ungeprüftes Pushen).

**Impact:**
Die Zero-False-Positive-Suite schützt nur, wenn sie verpflichtend läuft.

**Suggested fix:**
GitHub Actions: ruff + pytest (LLM-freie Marker) + frontend test/build + gitleaks; Branch-Protection.

---

### OPS-004: Azure nicht erreichbar ⇒ Job-Fail ohne Degradation, Fehlerklassen oder Re-Run

**Priority:** Medium
**Effort:** M
**Area:** pipeline / api

**Problem:**
[pipeline_runner.py:50-66](src/api/pipeline_runner.py#L50-L66) — LLM-Init-/Mapping-Fehler ⇒ `failed`, Nutzer muss manuell neu hochladen (bewusst kein Resume, [job_queue.py](src/api/job_queue.py#L10-L13)). Kein "Azure down"-Hinweis, kein Re-Run-Button (Datei liegt noch in uploads/), keine Unterscheidung transient/permanent.

**Impact:**
Bei Azure-429/Timeout-Phasen sieht der Einkäufer nur rote, fehlgeschlagene Jobs — gefühlte Instabilität.

**Suggested fix:**
Fehlerklassen am Job; "Erneut verarbeiten"-Endpoint (Re-Enqueue derselben Datei); Statusbanner bei gehäuften Azure-Fehlern.

---

### OPS-005: Stammdaten-Dateien fehlen/korrupt ⇒ stiller Leerlauf statt Fail-fast

**Priority:** Medium
**Effort:** S
**Area:** stammdaten_matching

**Problem:**
[master_data_matcher.py:30-35](src/transform/master_data_matcher.py#L30-L35) — fehlende Datei ⇒ Warning + leerer Katalog ⇒ alle Matches "no_match". Korruptes JSON ⇒ unbehandelte Exception beim ersten Match. Kein Start-Check, kein Health-Signal; die Settings-UI kann die Dateien direkt überschreiben.

**Impact:**
Eine versehentlich geleerte materials.json degradiert die GREEN-Rate kommentarlos auf nahe null — sieht aus wie ein Modellproblem.

**Suggested fix:**
Kataloge beim Start laden + validieren (Mindestanzahl, Pydantic-Schema); Ergebnis in /health + /settings/system; Schreib-Endpoints gegen dasselbe Schema validieren.

---

### OPS-006: Kein Test-/Offline-Modus für die Pipeline (WINFORM_TEST_MODE-Äquivalent fehlt)

**Priority:** Medium
**Effort:** M
**Area:** pipeline / ops

**Problem:**
Es gibt keinen Schalter, die Pipeline ohne Azure zu fahren (Mock-LLM, aufgezeichnete Antworten). `parse_file(llm=None)` fällt auf den Legacy-Parser zurück; Mapping/Scoring brauchen Live-Calls. Demos, lokale Entwicklung und CI hängen an Live-Credentials.

**Impact:**
CI kann den E2E-Pfad nicht testen; Kundendemos hängen am Azure-Wetter.

**Suggested fix:**
`LLM_MODE=mock|live` mit deterministischem Fixture-LLM (aufgezeichnete Antworten pro POC-PDF); Mock-Pfad als CI-E2E-Gate.

---

### OPS-007: Modell-/Region-Drift zwischen Code, Doku und Deploy-Beispiel

**Priority:** Low
**Effort:** S
**Area:** ops / docs

**Problem:**
Code-Default `gpt-4.1-mini` ([azure_openai.py:93-94](src/llm/azure_openai.py#L93-L94)); [.env.deploy.example](.env.deploy.example): `gpt-4o`/`gpt-4o-mini` + `2025-01-01-preview`; CLAUDE.md: GPT-4o/West Europe; Zielvorgabe: Sweden Central; [vision_verifier.py:205-210](src/scoring/vision_verifier.py#L205-L210) kommentiert "always uses GPT-4o", nutzt aber model_main. Aus dem Repo ist nicht ablesbar, welches Modell in Prod antwortet.

**Impact:**
Eval-Ergebnisse nicht reproduzierbar; DSGVO-Aussage ("Sweden Central") nicht belegbar.

**Suggested fix:**
Eine Quelle der Wahrheit; Modell + Region in /settings/system und im JOB_METRICS-Log ausweisen.

---

### OPS-008: Unbegrenzte `.bak`-Akkumulation; keine Retention für Uploads/Exports (DSGVO)

**Priority:** Low
**Effort:** S
**Area:** ops

**Problem:**
[settings.py:62-69](src/api/routes/settings.py#L62-L69) legt bei jedem Save ein Backup an, nichts räumt auf. data/uploads und data/exports (Kundendaten!) wachsen unbegrenzt, keine Lösch-/Retention-Policy.

**Impact:**
Disk-Full killt SQLite-Writes; DSGVO-Risiko (Art. 5 Speicherbegrenzung) durch unbegrenzte Aufbewahrung von Kunden-BOMs.

**Suggested fix:**
Retention-Job (konfigurierbar, z. B. 90 Tage), .bak-Rotation (letzte 5), Disk-Füllstand in /health.

---

### Architektur

---

### ARCH-001: Lock 2b (OpenDataLoader-Fallback inkl. 50k-Token-Cap) existiert nicht im Deploy-Code

**Priority:** High
**Effort:** L
**Area:** pipeline

**Problem:**
`grep -ri opendataloader src/ tests/ scripts/` → 0 Treffer auf `deploy/vps-v1`. Die Arbeit liegt auf dem unmerged Branch `feature/opendataloader-evaluation` (Evaluations-Outputs in `evaluation/output/` belegen die Experimente, inkl. ZF). Der versprochene Fallback samt 50k-Token-Cap ist im produktiven Pfad nicht vorhanden.

**Impact:**
Anforderungs-Lücke (siehe Audit). Dokumente, bei denen RB-1 ablehnt und Vision schwach ist, haben keine dritte Strukturquelle; der ZF-Token-Bloat-Schutz ist nirgends implementiert.

**Suggested fix:**
Entscheid herbeiführen: Branch evaluieren → mergen (Token-Cap als harter Guard + Tests) oder Anforderung offiziell streichen und Doku korrigieren.

---

### ARCH-002: "Yellow Recheck" existiert nicht — der Counter-Check prüft nur GREEN-Kandidaten und ist deaktiviert

**Priority:** High
**Effort:** L
**Area:** scoring

**Problem:**
Der einzige Recheck-Mechanismus ([vision_verifier.py](src/scoring/vision_verifier.py)) feuert ausschließlich für Zellen, die das Pre-Gate **bestanden** haben ([ensemble_scorer.py:405](src/scoring/ensemble_scorer.py#L405)) — nie für YELLOW. Ein Pass, der gelbe Zellen mit Zusatzkontext erneut prüft und ggf. hebt, ist nicht implementiert. Zusätzlich enable_counter_check=false (BUG-005).

**Impact:**
Die wirksamste bekannte Maßnahme zur GREEN-Raten-Steigerung **ohne** False-Positive-Risiko fehlt; das Produktversprechen "Yellow Recheck pass" ist unerfüllt.

**Suggested fix:**
Recheck-Pass für YELLOW-Zellen: Vision-Einzelfeld-Check (Scan-Pfad) bzw. erweiterter Kontext-Prompt (Text-Pfad); YELLOW→GREEN nur, wenn zusätzlich alle bestehenden Gates erfüllt sind.

---

### ARCH-003: Excel-Quellen können strukturell nie GRÜN werden — undokumentiert

**Priority:** Medium
**Effort:** S (Doku) / XL (deterministischer Excel-GREEN-Pfad)
**Area:** scoring / product

**Problem:**
[green_gate.py:93-94](src/scoring/green_gate.py#L93-L94) — `if not source_is_pdf: return False, ["NO_PDF_EVIDENCE"]`. Excel-Uploads (deterministisch lesbare Quellen!) sind kategorisch GREEN-unfähig, während der Upload .xlsx prominent akzeptiert. Der Nutzer sieht eine durchgängig gelbe Tabelle ohne Erklärung.

**Impact:**
Paradox: die zuverlässigste Quellart liefert die schlechteste Automationsquote — Erwartungsbruch beim Stakeholder.

**Suggested fix:**
Kurzfristig: No-Green-Hinweis pro Quelle in UI/Export. Mittelfristig: deterministische Excel-GREEN-Policy (Zellwert = Quellwert ist per openpyxl beweisbar; einziges Restrisiko ist die Mapping-Semantik → ARCH-005 als Voraussetzung).

---

### ARCH-004: Feedback-Loop nur viertel-geschlossen — Korrekturen wirken ausschließlich als Few-Shots im Spalten-Mapping

**Priority:** Medium
**Effort:** L
**Area:** feedback_store / learning

**Problem:**
Korrekturen werden gespeichert ([feedback_store.py](src/export/feedback_store.py)) und in den Mapping-Prompt injiziert ([llm_column_mapper.py:138-183](src/mapping/llm_column_mapper.py#L138-L183) — gut). Aber: (1) Wert-Korrekturen landen nie im Master-Data-Matcher (kein Alias-Lernen); (2) `data/learned_mappings/` als per-Kunde-Registry ist ungenutzt; (3) Row-Exclusion-Muster werden nicht generalisiert; (4) Few-Shots sind die letzten 8 ohne Dedupe/Konflikt-Handling. Und: wegen DATA-001 ist `customer` in Produktion leer — der Loop ist faktisch tot.

**Impact:**
"Per-customer learning" ist als Versprechen nur teilweise eingelöst; wiederkehrende Kunden produzieren dieselben YELLOWs erneut.

**Suggested fix:**
Korrektur-Typen routen: Wert-Korrekturen → Alias-Vorschläge für materials.json (Review in Settings-UI), Mapping-Korrekturen → persistiertes per-Kunde-Spaltenmapping (vor dem LLM-Call angewendet), Exclusion-Muster → row_classifier-Hints.

---

### ARCH-005: Keine unabhängige Verifikation der semantischen Spaltenzuordnung (größter systematischer Wrong-GREEN-Vektor)

**Priority:** High
**Effort:** L
**Area:** mapping / scoring

**Problem:**
Locks 2/3 verifizieren, dass der **Wert** an der Quellposition steht und ggf. im Katalog existiert — nicht, dass die **Spalte semantisch richtig gemappt** ist. Einziges Gegengewicht sind die Heuristiken des mapping_validator (Typ-Checks, 2 Swap-Regeln). Ein konfident falsches LLM-Mapping (Rohmaß→Fertigmaß, Lieferanten-Nr→Hersteller-Teilnr.) erfüllt alle Zell-Gates und produziert spaltenweise falsche GRÜNs (vgl. BUG-006/007).

**Impact:**
⚠️ ZERO-FALSE-POSITIVE RISK — der wahrscheinlichste Mechanismus für ein **systematisches** falsches GRÜN (ganze Spalte statt Einzelzelle); exakt das Szenario, an dem die 4 Vorgänger-Anbieter gescheitert sind.

**Suggested fix:**
Zweiter, unabhängiger Mapping-Call (anderes Prompt-Framing/Modell) mit Konsens-Pflicht für GREEN-fähige Spalten; Feld-Policy-Matrix: Kategorie-A-Spalten ohne Master-Data-/Format-Anker bleiben ohne Konsens YELLOW-gedeckelt.

---

### PERF-001: Vision-Pfad seriell (1 Seite gleichzeitig, 2 Calls/Seite, Retries); kein Job-Timeout

**Priority:** Medium
**Effort:** M
**Area:** vision_lock / performance

**Problem:**
`_MAX_CONCURRENT_PAGES = 1` ([pdf_parser.py:50](src/ingestion/pdf_parser.py#L50)) serialisiert Seiten (nur das Dual-Paar läuft parallel). Ein 10-seitiger Scan = ≥ 20 Vision-Calls seriell à 10–30 s + JSON-Retries ⇒ 5–15 Minuten; ein 100-Positionen-BOM über viele Seiten entsprechend mehr. `JOB_CONCURRENCY=1` verstärkt das (1 BOM blockiert die Queue). Kein per-Job-Gesamttimeout — ein hängender Job blockiert den einzigen Worker (LLM-Timeout 300 s × Retries als einziges Limit). Fortschritt springt 0.1→0.3 ohne Seiten-Granularität.

**Impact:**
Demo-/Produktionslatenz; Queue-Stau bei parallelen Uploads.

**Suggested fix:**
Seiten-Parallelität konfigurierbar (2–3 je nach Azure-TPM), per-Job-Timeout mit sauberem failed, Seiten-Fortschritt an job_store melden.

---

### PERF-002: Counter-Check (wenn aktiviert) ruft Vision pro GREEN-Kandidaten-Zelle einzeln und sequenziell

**Priority:** Medium
**Effort:** M
**Area:** scoring / cost

**Problem:**
[ensemble_scorer.py:405-443](src/scoring/ensemble_scorer.py#L405-L443) — ein Call **pro Zelle**, sequenziell im Scoring-Loop. Ein 100-Zeilen-BOM mit 10 GREEN-Kandidaten pro Zeile = bis zu 1000 Vision-Calls. Das ist mutmaßlich der reale Grund, warum enable_counter_check in Prod aus ist — die Kostenstruktur erzwingt das Abschalten des Sicherheitsmechanismus.

**Impact:**
Sicherheits-Feature ökonomisch unbenutzbar → führt direkt zu BUG-005.

**Suggested fix:**
Pro Seite batchen (ein Call verifiziert alle Kandidaten-Felder einer Seite, strukturierter Output pro Feld); alternativ Stichproben-Modus (alle Kategorie-A + N % Sample). Render-Cache existiert bereits.

---

### Frontend / Daten

---


### FE-002: Pipeline-Fehlerzustände im UI unvollständig (Queue-Position, Fehlerklassen, Re-Run)

**Priority:** Low
**Effort:** M
**Area:** frontend

**Problem:**
[use-job-pipeline.ts](frontend/src/lib/use-job-pipeline.ts) kennt nur pending/processing/completed/failed; `error` wird roh angezeigt (englische Stage-Meldungen). Keine Queue-Position (JOB_CONCURRENCY=1!), kein Retry (vgl. OPS-004).

**Impact:**
Bei parallelen Uploads wirkt das System eingefroren ("10 % Verarbeitung" über Minuten).

**Suggested fix:**
Queue-Position exponieren; Fehlerklassen lokalisieren; "Erneut verarbeiten"-Aktion.

---

### FE-003: Zwei verschiedene "GREEN-Quoten" (automation_rate vs. green_pct), beide durch NEUTRAL-Inflation verzerrt

**Priority:** Low
**Effort:** S
**Area:** frontend / stats

**Problem:**
[stats.py:54](src/api/routes/stats.py#L54) rechnet `green/total_cells` (inkl. NEUTRAL), die Audit-Properties `green_pct` auf `total_scored`. Mit `empty_non_required_as_neutral=true` (BUG-013) ist beides zusätzlich geschönt. Zeitersparnis-Formel (`MINUTES_PER_ROW=3`) ist unkalibriert.

**Impact:**
Stakeholder-KPIs nicht konsistent erklärbar — gefährlich, wenn Mahler nachrechnet.

**Suggested fix:**
Eine definierte Quote (green/total_scored) überall; NEUTRAL separat; Zeitersparnis mit Schaufler kalibrieren.

---

### DATA-001: `infer_customer` liefert für echte Uploads "" — per-Kunde-Lernen und Statistik laufen ins Leere

**Priority:** High
**Effort:** M
**Area:** feedback_store / api

**Problem:**
[file_router.py:47-72](src/ingestion/file_router.py#L47-L72) — der Kunde wird aus der Verzeichnisstruktur geraten; produktive Uploads liegen in `data/uploads/` ⇒ `customer=""` für **jeden** echten Job. Damit ist das Few-Shot-Lernen wirkungslos (`_format_learned_corrections`: `if not customer: return ""`), und die Statistik gruppiert alles unter "Unbekannt".

**Impact:**
Der gesamte Lern-Loop ist produktiv tot, obwohl implementiert; Statistikseite zeigt "Unbekannt".

**Suggested fix:**
Kunde als Pflicht-Parameter beim Upload (Dropdown aus Settings-Kundenliste) oder LLM-Erkennung aus dem Dokumentkopf mit Bestätigung; customer am Job editierbar.

---

### DATA-002: Excel-Parser ohne Merged-Cell-Propagation, Single-Sheet-Heuristik, schwache Header-Erkennung

**Priority:** Medium
**Effort:** M
**Area:** ingestion / excel

**Problem:**
[excel_parser.py:113-151](src/ingestion/excel_parser.py#L113-L151) — MergedCells werden `None` (vertikal gemergte Positionsspalten verlieren alle Folgezeilen-Werte); genau ein Sheet wird geparst; Header = "Zeile mit den meisten Text-Zellen in den ersten 20". Auf dem Excel-Pfad gibt es zudem keinen Positions-Anker (`raw_pdf_positions` leer, guard_basis=row_count_fallback) — schwächere Zero-Data-Loss-Netze als beim PDF.

**Impact:**
Excel-BOMs mit gemergten Zellen (häufig) verlieren still Zellwerte.

**Suggested fix:**
Merged-Range-Werte in alle überdeckten Zellen propagieren; Sheets per BOM-Score ranken; Excel-seitigen Vollständigkeits-Anker ergänzen.

---

### DATA-003: Stammdaten-Import aus der Schaufler-Vorlage fehlt — nur 18/363 Werkstoffe, 0/181 Hersteller, 12/15 Teilegruppen aktiv

**Priority:** Critical (für die GREEN-Rate; kein False-Positive-Risiko)
**Effort:** M
**Area:** stammdaten_matching

**Problem:**
Das Sheet `Stammdaten` in [config/target_template.xlsx](config/target_template.xlsx) enthält die vollständigen Kataloge (nachgezählt: **363 Werkstoffe**, **181 Hersteller**, **15 Teilegruppen**, dazu Härte-/Nitrier-/Beschichtungslisten). Die Laufzeit-Kataloge ([materials.json](config/master_data/materials.json): 18 Einträge, [validation_rules.json](config/master_data/validation_rules.json): 12 Teilegruppen, **kein** Hersteller-Katalog) wurden nie daraus befüllt. Hersteller-Matching existiert als Code-Pfad gar nicht (Manufacturer ist nur generischer Kategorie-B-Textvergleich).

**Impact:**
Lock 3 läuft gegen ~5 % des realen Werkstoff-Katalogs: fast alle Material-Zellen enden als `passthrough` (YELLOW) oder hängen am `werkstoff_nr_format`-Notnagel; Teilegruppen D1 u. a. können nie matchen; Hersteller werden nie validiert. **Größter einzelner Hebel für die GREEN-Rate überhaupt.**

**Suggested fix:**
Import-Skript Template-Stammdaten → materials.json / validation_rules.json / neues manufacturers.json (idempotent, mit Diff-Report); Hersteller-Katalog + exakter/normalisierter Match im Matcher + Manufacturer in Transform/Comparator als Master-Data-Feld; CI-Check "Template-Stammdaten == Laufzeit-Kataloge".

---

### DATA-004: `werkstoff_nr_format`/M2 erzeugt GREEN-fähige Material-IDs außerhalb des Katalogs — jede freistehende 5-stellige Zahl wird Werkstoff

**Priority:** Medium
**Effort:** S
**Area:** stammdaten_matching

**Problem:**
[master_data_matcher.py:165-184, 323-334](src/transform/master_data_matcher.py#L165-L184) — eine format-valide DIN-Nummer ohne Katalogeintrag wird mit 0.92 akzeptiert (`werkstoff_nr_format`, GREEN-fähig auf dem Text-Pfad). Der M2-Pfad akzeptiert zusätzlich **jede freistehende 5-stellige Zahl** mit führender 1/2 ("12343"→1.2343) — ein Artikelcode "20283" in einer falsch gemappten Spalte wird zur Werkstoffnummer 2.0283.

**Impact:**
⚠️ ZERO-FALSE-POSITIVE RISK (niedrig-moderat; durch ^…$-Anker und Text-Pfad-Bindung eingegrenzt, aber M2 ist die schwächste Stelle).

**Suggested fix:**
M2 nur akzeptieren, wenn die Spalte per Header **und** Mehrheits-Evidenz Materialspalte ist; nach DATA-003 (voller Katalog) M3/M2 auf YELLOW-Vorschlag zurückstufen.

---

### TEST-001: Keine False-GREEN-Canary-Suite auf Dokumentebene; Suite läuft gegen Code-Defaults statt Prod-Config

**Priority:** High
**Effort:** L
**Area:** tests / quality

**Problem:**
Die Suite (286 Tests, inkl. test_zero_false_positive.py und ZDL-1..4 — gut!) testet Gate-Logik mit konstruierten Inputs. Es fehlt die E2E-Canary-Klasse: präparierte PDFs mit bekannten Fallen (vertauschte Spalten, doppelte Positionsnummern mit unterschiedlichen Teilen, manipulierte Menge, Werkstoff-Tippfehler am Fuzzy-Cutoff) mit der Assertion "diese Zelle darf NIE GREEN sein". Genau die Pfade aus BUG-001…010 hätten Canaries gefangen. Außerdem laufen Tests gegen Code-Defaults — die Lockerungen der echten app_config.yaml (BUG-005) sind für die Suite unsichtbar.

**Impact:**
Regressionsschutz für den Kernvertrag fehlt auf der Ebene, auf der er bricht (Komponenten-Zusammenspiel + Prod-Config).

**Suggested fix:**
Canary-Korpus aus den 19 POC-PDFs (mutierte Kopien mit Ground-Truth); Suite zusätzlich mit produktiver Config fahren; "GREEN-Diff"-Report pro Commit.

---

### TEST-002: Legacy-Parser-E2E-Test schlägt fehl (GF 0 Zeilen) — Suite ist nicht grün

**Priority:** Medium
**Effort:** S
**Area:** tests

**Problem:**
`tests/test_ingestion/test_parse_all.py::test_has_rows[GF/STL_08.05.13.pdf]` schlägt fehl: der LLM-lose Testpfad nutzt den Legacy-Parser, der am rotierten GF-Layout scheitert (0 Zeilen). Im LLM-Betrieb übernimmt der Vision-Fallback — der Test prüft also einen Pfad, der das Dokument nie bedienen soll, und maskiert dabei, dass die Suite "rot" ist.

**Impact:**
Eine dauerhaft rote Suite erzieht zum Ignorieren von Failures (Broken-Windows).

**Suggested fix:**
Test als `xfail(reason="GF braucht Vision-Fallback; Legacy-Parser deckt rotierte Matrix nicht ab")` markieren oder den erwarteten Fallback explizit testen.

---

### TEST-003: `test_job_source_route.py` schlägt mit 401 fehl — Auth-Modulzustand leakt in die Tests

**Priority:** Medium
**Effort:** S
**Area:** tests / auth

**Problem:**
Beide Tests in [tests/test_job_source_route.py](tests/test_job_source_route.py) erwarten 200/403, bekommen aber 401: `_SETTINGS["login_enabled"]` ist modul-global default `True` ([auth.py:15-25](src/core/auth.py#L15-L25)), und die Tests authentifizieren sich nicht bzw. setzen den Zustand nicht zurück. Je nach Test-Reihenfolge/Umgebung (lokale .env!) kippt das Ergebnis — die Suite ist umgebungsabhängig.

**Impact:**
Rote Suite + flaky Auth-Verhalten in Tests untergräbt das CI-Gate (OPS-003), bevor es existiert.

**Suggested fix:**
Auth-Fixture (Login-Session oder `LOGIN_AUTH_ENABLED=false` + `init_auth()` per monkeypatch) für alle API-Tests; `_SETTINGS` nicht als Modul-Global mutieren, sondern über eine reset-bare Struktur.

---

---

## ANFORDERUNGS-AUDIT: Stücklistenagent vs. Lastenheft Rev. 2

> Hinweis: Ein Dokument „Lastenheft Rev. 2" liegt nicht im Repo (docs/ enthält nur BOM-Mapper_Erklaerung.pdf und Pläne). Das Audit prüft gegen die kommunizierten Anforderungen aus dem Review-Briefing + CLAUDE.md + Plandokumenten. Wo das echte Lastenheft abweicht, bitte gegenprüfen.

### BOM PROCESSING

#### REQ-001: PDF-BOM-Upload und Parsing
**Status:** ✅ Vollständig
**Fundstelle:** [upload.py:34-70](src/api/routes/upload.py#L34), [structure_normalizer.py:21-115](src/ingestion/structure_normalizer.py#L21), [coordinate_table.py:155-232](src/ingestion/coordinate_table.py#L155)
**Lücken:** Magic-Byte-Validierung fehlt (SEC-005).

#### REQ-002: Mehrseitige BOMs
**Status:** ✅ Vollständig (Text-Pfad) / ⚠️ Teilweise (Vision)
**Fundstelle:** [coordinate_table.py:322-395](src/ingestion/coordinate_table.py#L322) (per-Section-Layout, Folgeseiten ohne Header), [pdf_parser.py:749-791](src/ingestion/pdf_parser.py#L749)
**Lücken:** Vision-Pfad nutzt nur das Seite-1-Spaltenschema (BUG-017).

#### REQ-003: Gescannte/bildbasierte BOMs (OCR-Fallback)
**Status:** ⚠️ Teilweise
**Fundstelle:** [pdf_parser.py:67-243](src/ingestion/pdf_parser.py#L67) (GPT-Vision-Pfad), [green_gate.py:248-278](src/scoring/green_gate.py#L248)
**Lücken:** Funktioniert als Extraktion, aber GREEN ist auf Scans an den Counter-Check gebunden, der deaktiviert ist (BUG-005) → Scans liefern faktisch 0 % GREEN; Vollständigkeit auf Scans prinzipbedingt nicht garantiert (ehrlich ausgewiesen via completeness_reason — gut).

#### REQ-004: Tabellenstruktur-Erkennung
**Status:** ✅ Vollständig
**Fundstelle:** [coordinate_table.py:280-501](src/ingestion/coordinate_table.py#L280) (Bänder/Korridore/Sections), [pdf_parser.py:486-636](src/ingestion/pdf_parser.py#L486) (Vision Phase A)
**Lücken:** Rotierte/transponierte Layouts (GF) werden vom Text-Pfad korrekt abgelehnt → Vision; Vision selbst bleibt dort schwach.

#### REQ-005: Kopf-/Fußzeilen-Filterung (geometrisch)
**Status:** ✅ Vollständig
**Fundstelle:** [coordinate_table.py:47](src/ingestion/coordinate_table.py#L47) (2 %-Trim + inhaltsbasierte Seitenmarker), [pdf_parser.py:359-410](src/ingestion/pdf_parser.py#L359) (8 %-Margin), [row_classifier.py](src/ingestion/row_classifier.py) (verlustfreies Tagging)
**Lücken:** Zwei verschiedene Margins (2 % vs. 8 %) je Pfad — bewusst, aber undokumentiert.

#### REQ-006: Positionsnummern-Extraktion (alle Formate, auch nicht-ganzzahlig)
**Status:** ⚠️ Teilweise
**Fundstelle:** [config/pos_patterns.yaml](config/pos_patterns.yaml), [ensemble_scorer.py:74-108](src/scoring/ensemble_scorer.py#L74), [pdf_parser.py:1204-1224](src/ingestion/pdf_parser.py#L1204)
**Lücken:** ZDL-3 ist adressiert: Muster konfigurierbar (1-2, K-3, A-12), bare Integers kommen kontextsicher aus der Positionsspalte. Aber: "1.0"≠"1"-Normalisierung fehlt (BUG-015); alphanumerische Sonderformate ("10a", "P.1.2") nur über YAML-Erweiterung pro Instanz.

#### REQ-007: Werkstoff-Extraktion und -Matching
**Status:** ⚠️ Teilweise
**Fundstelle:** [master_data_matcher.py:78-197](src/transform/master_data_matcher.py#L78), [pipeline.py:412-439](src/transform/pipeline.py#L412)
**Lücken:** Matching-Logik gut (Alias/DIN-Nr/Fuzzy mit Familien-Guard), aber der Katalog enthält nur 18 von 363 Werkstoffen (DATA-003) — datenseitig nicht erfüllt.

#### REQ-008: Hersteller-Extraktion und -Matching
**Status:** ❌ Nicht implementiert
**Fundstelle:** — (kein Hersteller-Katalog, kein Matcher-Pfad; Manufacturer nur generischer Textvergleich Kategorie B, [value_comparator.py:34-39](src/scoring/value_comparator.py#L34))
**Lücken:** 181 Hersteller stehen ungenutzt im Template-Stammdaten-Sheet (DATA-003).

#### REQ-009: Teilegruppen-Extraktion und -Matching (15 Codes)
**Status:** ⚠️ Teilweise
**Fundstelle:** [master_data_matcher.py:238-253](src/transform/master_data_matcher.py#L238), [validation_rules.json](config/master_data/validation_rules.json)
**Lücken:** Exakter Code-Match implementiert, aber nur 12 von 15 Codes im Laufzeit-Katalog (u. a. D1 fehlt; Template hat 15).

#### REQ-010: Mengen-Extraktion
**Status:** ✅ Vollständig
**Fundstelle:** [pipeline.py:626-649](src/transform/pipeline.py#L626) (integer_coerce), [value_comparator.py:525-561](src/scoring/value_comparator.py#L525)
**Lücken:** Verifikations-Bias auf dem Anchor-Fallback (BUG-002).

#### REQ-011: Zeichnungsnummern-Extraktion
**Status:** ⚠️ Teilweise
**Fundstelle:** Customer Part Number (Spalte B) als nächstliegendes Feld; "drawing_number" nur als Export-Meta ([excel_exporter.py:310-321](src/export/excel_exporter.py#L310))
**Lücken:** Kein dediziertes Zeichnungsnummern-Feld im Zielschema; die Meta-Zeichnungsnummer wird nirgends aus dem PDF extrahiert.

#### REQ-012: Alle 363 Werkstoffe in Stammdaten
**Status:** ❌ Nicht implementiert (18/363 aktiv)
**Fundstelle:** [config/master_data/materials.json](config/master_data/materials.json); Quelle vorhanden in [config/target_template.xlsx](config/target_template.xlsx) Sheet "Stammdaten"
**Lücken:** DATA-003.

#### REQ-013: Alle 183 Hersteller in Stammdaten
**Status:** ❌ Nicht implementiert (0 aktiv; 181 im Template-Sheet gezählt)
**Fundstelle:** —
**Lücken:** DATA-003 / REQ-008.

### CLASSIFICATION

#### REQ-014: GREEN = 100 % sicher (Zero false positives)
**Status:** ⚠️ Teilweise
**Fundstelle:** [green_gate.py:81-165](src/scoring/green_gate.py#L81) (zentrales Gate — gute Architektur), [ensemble_scorer.py](src/scoring/ensemble_scorer.py)
**Lücken:** Konkrete Durchbruchspfade: BUG-001/002/003/004/006/007/008/009; Prod-Config schwächt zusätzlich (BUG-005). Das Gate-Design ist richtig, die Evidenzquellen dahinter sind teils zirkulär.

#### REQ-015: YELLOW = möglicher Match, Review nötig
**Status:** ✅ Vollständig
**Fundstelle:** [ensemble_scorer.py:451-524](src/scoring/ensemble_scorer.py#L451), Frontend review-grid
**Lücken:** RED→YELLOW-Demo-Overrides verwässern die Semantik (BUG-014); NEUTRAL-Maskierung (BUG-013).

#### REQ-016: RED = kein Match
**Status:** ✅ Vollständig
**Fundstelle:** [ensemble_scorer.py:469-490](src/scoring/ensemble_scorer.py#L469), synthetische MISSING-Zeilen [position_reconciler.py](src/reconciliation/position_reconciler.py)
**Lücken:** —

#### REQ-017: Triple-Lock end-to-end erzwungen
**Status:** ⚠️ Teilweise
**Fundstelle:** Lock-Aufrufe in [pipeline_runner.py:62-156](src/api/pipeline_runner.py#L62)
**Lücken:** Auf dem Text-Pfad werden Lock-2-Vetos teils ignoriert/gestrippt (`_TEXT_PATH_IGNORED_VETOES`, BUG-003); Counter-Check prod-deaktiviert; Lock 2b fehlt (ARCH-001). Positiv: der Lock-Status pro Zelle ist im Audit sichtbar (green_evidence).

#### REQ-018: Lock 1 — Vision (GPT-4.1-mini)
**Status:** ✅ Vollständig
**Fundstelle:** [pdf_parser.py:67-243](src/ingestion/pdf_parser.py#L67), [azure_openai.py:93](src/llm/azure_openai.py#L93) (Default gpt-4.1-mini)
**Lücken:** Deploy-Beispiel konfiguriert gpt-4o (OPS-007).

#### REQ-019: Lock 2 — PyMuPDF-Koordinaten-Verifikation
**Status:** ⚠️ Teilweise
**Fundstelle:** [pdf_parser.py:1716-1908](src/ingestion/pdf_parser.py#L1716) (Vision-Pfad), [coordinate_table.py](src/ingestion/coordinate_table.py) (Text-Pfad: die Extraktion selbst ist koordinatenbasiert)
**Lücken:** Umgehbar: bei fehlender Koordinaten-Location greift der schwache Volltext-Fallback (BUG-001), der Lock-2-Vetos sogar aufheben kann (BUG-003); Punkt-Normalisierung verfälscht Bestätigungen (BUG-010).

#### REQ-020: Lock 2b — OpenDataLoader-Fallback mit 50k-Token-Cap
**Status:** ❌ Nicht implementiert (nur unmerged Branch `feature/opendataloader-evaluation`)
**Fundstelle:** —
**Lücken:** ARCH-001.

#### REQ-021: Lock 3 — deterministisches Stammdaten-Matching
**Status:** ⚠️ Teilweise
**Fundstelle:** [master_data_matcher.py](src/transform/master_data_matcher.py)
**Lücken:** Deterministisch bis auf Fuzzy-Zweige (GREEN-fähig, BUG-008); Katalog zu 5 % befüllt (DATA-003); Format-Notnagel M2/M3 (DATA-004).

#### REQ-022: Yellow-Recheck-Pass
**Status:** ❌ Nicht implementiert
**Fundstelle:** Counter-Check existiert nur für GREEN-Kandidaten ([ensemble_scorer.py:405](src/scoring/ensemble_scorer.py#L405)) und ist deaktiviert
**Lücken:** ARCH-002.

#### REQ-023: Feedback-Store / per-Kunde-Lernen
**Status:** ⚠️ Teilweise
**Fundstelle:** [feedback_store.py](src/export/feedback_store.py), [llm_column_mapper.py:138-183](src/mapping/llm_column_mapper.py#L138) (Few-Shot-Injection), [routes/feedback.py](src/api/routes/feedback.py)
**Lücken:** Produktiv wirkungslos, weil customer="" (DATA-001); nur Mapping-Few-Shots, kein Alias-/Mapping-Registry-Lernen (ARCH-004).

### EXCEL OUTPUT

#### REQ-024: Exaktes Spalten-Mapping der Schaufler-Vorlage
**Status:** ✅ Vollständig
**Fundstelle:** [target_schema.json](config/target_schema.json) (30 Felder A–AD), [excel_exporter.py:98-254](src/export/excel_exporter.py#L98) (Template laden, Stile aus Referenzzeile, Meta-Zeilen)
**Lücken:** —

#### REQ-025: Zell-Level-Farbcodierung (Grün/Gelb/Rot)
**Status:** ✅ Vollständig
**Fundstelle:** [excel_exporter.py:39-51, 226-228](src/export/excel_exporter.py#L39) (+ NEUTRAL-/MANUAL-Farben, Audit-Sheet)
**Lücken:** Zellen ohne Audit-Eintrag bleiben ungefärbt (kosmetisch).

#### REQ-026: Alle Spalten der echten Schaufler-Vorlage
**Status:** ✅ Vollständig
**Fundstelle:** Schema 30 Spalten = Template "Stückliste" (A–AD); [result_builder.py:74-150](src/api/result_builder.py#L74) spiegelt Layout inkl. Header-Zeilen/Defaults in die UI
**Lücken:** —

#### REQ-027: Zero-Loss-Assertion vor Export
**Status:** ✅ Vollständig
**Fundstelle:** [excel_exporter.py:113-184](src/export/excel_exporter.py#L113) — Set-Guard auf Band-IDs (Text-Pfad) bzw. Positions-IDs, Count-Fallback, Reviewer-Exclusions korrekt herausgerechnet, Save wird verweigert
**Lücken:** Auf dem Vision-Pfad schwächer (Positions-Set statt Bänder, BUG-011); Excel-Quellen nur Count-Fallback (DATA-002).

#### REQ-Z1: Zero-Data-Loss-Lücken aus dem Vor-Audit (ZDL-1…4)
**Status:** ✅ Weitgehend behoben
- **ZDL-1 (Vision-Selbstreferenz):** adressiert durch ehrliches `completeness_guaranteed=false` + Begründung ([ensemble_scorer.py:913-950](src/scoring/ensemble_scorer.py#L913)); die Limitierung selbst bleibt prinzipbedingt, ist aber sichtbar.
- **ZDL-2 (Guard-Selbstabschaltung ohne Anker):** behoben — `row_count_fallback` statt Skip ([position_reconciler.py:127-141](src/reconciliation/position_reconciler.py#L127)).
- **ZDL-3 (Integer-only-Regex):** behoben — konfigurierbare Muster + Positionsspalten-Union ([pdf_parser.py:1204-1224](src/ingestion/pdf_parser.py#L1204)); Rest-Lücke BUG-015.
- **ZDL-4 (Export-Set-Guard):** behoben — Set-Vergleich statt Count ([excel_exporter.py:119-166](src/export/excel_exporter.py#L119)), Tests vorhanden.

### UI / FRONTEND

#### REQ-028: PDF-Viewer (ohne JSON-Fehler)
**Status:** ✅ Vollständig
**Fundstelle:** [pdf-source-viewer.tsx](frontend/src/components/pdf-source-viewer.tsx) — react-pdf per Runtime-Import mit Catch; ein pdfjs-Eval-Fehler (der "JSON error" aus dem Feasibility-Meeting) degradiert kontrolliert auf den iframe-Viewer; /source wird inline gestreamt ([jobs.py:168-190](src/api/routes/jobs.py#L168)); Bbox-Highlight aus source_location
**Lücken:** —

#### REQ-029: Excel-Ansicht mit Farbcodierung
**Status:** ✅ Vollständig
**Fundstelle:** [result-table.tsx](frontend/src/components/result-table.tsx) / [review-grid.tsx](frontend/src/components/review-grid.tsx) (ag-grid, Status pro Zelle, Template-Layout aus result_builder)
**Lücken:** —

#### REQ-030: Schaufler-CI (anthrazit, rot, flach, dichte Tabellen)
**Status:** ❌ Nicht implementiert
**Fundstelle:** [globals.css](frontend/src/app/globals.css): Blau #004b87, helles Theme, radius 0.5rem, Schatten; rowHeight 28
**Lücken:** FE-001 — mit Stakeholder klären (evtl. bewusste Neu-Entscheidung), dann Token-Pass.

#### REQ-031: Verarbeitungsstatus / Fortschritt
**Status:** ⚠️ Teilweise
**Fundstelle:** [use-job-pipeline.ts](frontend/src/lib/use-job-pipeline.ts) (Polling 1.5 s, Prozentanzeige), [processing-status.tsx](frontend/src/components/processing-status.tsx)
**Lücken:** Grobe Stufen (0.1/0.3/0.5/0.7/0.9), keine Queue-Position, kein Re-Run (FE-002, OPS-004).

#### REQ-032: Manuelles Override (YELLOW→bestätigt durch Einkäufer)
**Status:** ✅ Vollständig
**Fundstelle:** [cell_edits.py](src/api/cell_edits.py) (MANUAL_CONFIRMED, Audit-Reason, Export-Regeneration), [feedback.py:98-152](src/api/routes/feedback.py#L98), Row-Exclusion mit Provenance (R3)
**Lücken:** Override wird sauber als MANUAL_CONFIRMED geführt, nicht als GREEN — gute Trennung. Kein RBAC (jeder Login darf alles).

### API

#### REQ-033: BOM-Upload-Endpoint — **Status:** ✅ Vollständig — [upload.py](src/api/routes/upload.py) (+ bounded Queue, AW-1)
#### REQ-034: Status-Endpoint — **Status:** ✅ Vollständig — [jobs.py:116-129](src/api/routes/jobs.py#L116)
#### REQ-035: Ergebnis-Download (Excel) — **Status:** ✅ Vollständig — [jobs.py:148-165](src/api/routes/jobs.py#L148)
#### REQ-036: Feedback-Endpoint — **Status:** ✅ Vollständig — [feedback.py](src/api/routes/feedback.py)

### DEPLOYMENT

#### REQ-037: Hetzner/VPS Docker Compose
**Status:** ✅ Vollständig
**Fundstelle:** [docker-compose.prod.yml](docker-compose.prod.yml), [Caddyfile](Caddyfile) (Auto-HTTPS, Same-Origin-Routing), [DEPLOYMENT.md](DEPLOYMENT.md)
**Lücken:** Container-Hardening (SEC-006).

#### REQ-038: Azure OpenAI Sweden Central (DSGVO)
**Status:** ❓ Unklar
**Fundstelle:** Die Region steht nirgends im Repo; der Endpoint kommt aus .env. CLAUDE.md sagt "West Europe", der Auftrag "Sweden Central".
**Lücken:** OPS-007 — Region belegen und in /settings/system ausweisen.

#### REQ-039: Health-Checks
**Status:** ⚠️ Teilweise
**Fundstelle:** [main.py:79-81](src/api/main.py#L79) (statisch), Frontend-Dockerfile-HEALTHCHECK
**Lücken:** OPS-002.

#### REQ-040: Graceful Degradation bei AI-Ausfall
**Status:** ⚠️ Teilweise
**Fundstelle:** Retry/Backoff mit Retry-After-Respekt ([azure_openai.py:158-216](src/llm/azure_openai.py#L158) — gut), sauberes failed + Orphan-Recovery ([job_store.py:191-215](src/api/job_store.py#L191))
**Lücken:** Keine Fehlerklassen, kein Re-Run, kein Statusbanner (OPS-004); kein Offline-Modus (OPS-006).

### Audit-Bilanz

| Status | Anzahl | Anteil |
|---|---|---|
| ✅ Vollständig | 19 | 47.5 % |
| ⚠️ Teilweise | 14 | 35 % |
| ❌ Nicht implementiert | 6 (REQ-008, 012, 013, 020, 022, 030) | 15 % |
| ❓ Unklar | 1 (REQ-038) | 2.5 % |
| **Gesamt** | **40** | |

---

## VERBESSERUNGSANALYSE: Kann man es besser machen?

*(Perspektive: Principal ML Engineer / Systemarchitekt, Stand 2026, Randbedingungen: Zero-False-Positive-Vertrag, Azure Sweden Central / DSGVO, Schaufler-Dokumentenmix aus 19 POC-Kunden.)*

### Teil 1: Schwächen der aktuellen Architektur

**Wo sie versagt oder Unsicherheit produziert:**

1. **Die Verifikation prüft Werte, nicht Bedeutung.** Lock 2 beweist "dieser String steht an dieser Stelle", Lock 3 beweist "dieser String existiert im Katalog". Keiner der Locks beweist "diese Spalte IST die Material-Spalte". Die semantische Zuordnung hängt an einem einzigen LLM-Call plus Heuristiken — das ist die strukturelle Lücke (ARCH-005), und alle gefundenen False-GREEN-Pfade (BUG-001…009) sind letztlich Varianten davon: Evidenz wird ortsungebunden oder zirkulär.
2. **Vision-Extraktion ist fundamental limitiert:** (a) keine Vollständigkeitsgarantie — was das Modell nicht liest, existiert nicht (ZDL-1, ehrlich ausgewiesen); (b) Dual-Extraction fängt nur nicht-korrelierte Fehler — zwei Läufe desselben Modells auf demselben Bild machen korrelierte Fehler (gleiche unscharfe Glyphe → gleiche Fehllesung); (c) Kosten skalieren linear mit Seiten × 2 (+ Counter-Checks).
3. **PyMuPDF-Koordinaten-Verifikation als Lock 2 ist im Prinzip ausreichend für born-digital PDFs** — sie ist dort sogar die *Quelle* (RB-1), nicht nur die Prüfung, was die Selbstreferenz-Frage elegant auflöst. Für Scans existiert sie nicht (kein Text-Layer) — dort ist "Lock 2" faktisch leer und Dual-Extraction der einzige zweite Blick. Die ehrliche Antwort: **auf Scans gibt es derzeit kein belastbares GREEN, und das ist auch richtig so**, solange keine unabhängige OCR-Spur (z. B. lokales OCR als Gegenprobe) existiert.
4. **Deterministisches Stammdaten-Matching als Lock 3 ist der richtige Ansatz** — aber er ist nur so gut wie der Katalog, und der ist zu 5 % befüllt (DATA-003). Der Engpass ist Daten-Operations, nicht Algorithmik.
5. **Theoretisches GREEN-Maximum mit aktueller Architektur:** Born-digital PDFs (~14 von 19 POC-Kunden) × Kategorie-A-Felder mit Anker (Position, Menge, Maße, Werkstoff, Teilegruppe ≈ 60-70 % der befüllten Zellen) → realistisch **50-65 % GREEN auf Text-Pfad-Dokumenten** bei vollem Katalog; Scans 0 % (by design, solange Counter-Check aus). Gewichtet über den Dokumentenmix: **~40-55 % maximal**, was exakt dem "wenn 50 % verlässlich funktionieren, habe ich gewonnen"-Benchmark entspricht.
6. **Hauptursachen für YELLOW/RED heute** (abgeleitet aus Code-Pfaden + Evaluations-Outputs): (1) Stammdaten-Lücken → Material/Hersteller/Teilegruppe fallen auf passthrough (größter Block, behebbar); (2) Excel-Quellen kategorisch ohne GREEN (ARCH-003); (3) leere optionale Felder (NEUTRAL/YELLOW, korrekt); (4) Vision-Pfad ohne Counter-Check → alles ≤ YELLOW; (5) Mapping-Konfidenz < 0.90 bei exotischen Headern. Vision-Lesefehler sind NICHT der Haupttreiber — die meisten Dokumente nehmen den Text-Pfad.

### Teil 2: Alternative Ansätze

#### ALT-01: Fine-tuned GPT-4.1-mini auf 50–100 Schaufler-BOMs
**Expected GREEN rate improvement:** +3–8 % (nur Vision-Pfad-Dokumente)
**Zero-False-Positive risk:** Medium
**Why:** Fine-Tuning verbessert Extraktions-Konsistenz auf bekannten Layouts, aber (a) der Engpass liegt nicht bei der Extraktion, sondern bei Katalog & Verifikation; (b) ein feingetuntes Modell macht *selbstbewusstere* Fehler auf unbekannten Layouts — gefährlich für den Format-agnostischen Anspruch; (c) 50–100 BOMs sind für robustes Vision-Fine-Tuning knapp. Trainingsdaten: pro Seite Bild + Ground-Truth-Zeilen-JSON (aus korrigierten Reviews — fällt als Nebenprodukt des Feedback-Loops an).
**GDPR compatible:** Yes (Azure OpenAI Fine-Tuning in EU-Regionen verfügbar; Datenverarbeitung im Tenant)
**Effort:** XL
**Recommendation:** Do not implement (jetzt)
**Reason:** Falscher Engpass; erst Stammdaten + Recheck, dann mit echten Produktionsdaten neu bewerten.

#### ALT-02: Strukturierte Extraktion statt Vision für born-digital PDFs
**Expected GREEN rate improvement:** bereits realisiert (RB-1) — Restpotenzial +5–10 % durch bessere Korridor-/Header-Heuristiken
**Zero-False-Positive risk:** None
**Why:** Genau das ist die wichtigste bereits getroffene Architekturentscheidung des Systems (coordinate_table.py): Vision nur, wenn kein Text-Layer oder RB-1 ablehnt. Empfehlung im Detail: Vision **nie** für born-digital PDFs verwenden (heute passiert das beim RB-1-Decline, z. B. GF) — stattdessen dort eine zweite deterministische Strategie (Camelot-Lattice / Docling-TableFormer, siehe LIB-02/03) versuchen, bevor Vision rät.
**GDPR compatible:** Yes (lokal)
**Effort:** M
**Recommendation:** Implement now (Restpotenzial)
**Reason:** Deterministisch schlägt probabilistisch überall dort, wo die Daten exakt vorliegen.

#### ALT-03: Fuzzy-Matching mit Konfidenzschwellen statt deterministisch-oder-nichts
**Expected GREEN rate improvement:** +2–5 %
**Zero-False-Positive risk:** High (wenn fuzzy → GREEN), Low (wenn fuzzy → besseres YELLOW)
**Why:** Bereits teilweise eingebaut — und genau dort liegt ein Risiko (BUG-008). Die richtige Rolle von Fuzzy ist **Vorschlags-Generierung**: "1.2343 ESU (95 % ähnlich) — übernehmen?" als YELLOW mit Ein-Klick-Bestätigung. Das senkt Review-Zeit fast so stark wie GREEN, ohne den Vertrag anzufassen.
**GDPR compatible:** Yes
**Effort:** S (Umwidmung bestehender Treffer in Vorschläge)
**Recommendation:** Implement now — aber als YELLOW-Vorschlag, Fuzzy-GREEN abschaffen.
**Reason:** Gleiche Zeitersparnis, null Vertragsrisiko.

#### ALT-04: Vektor-/Embedding-Suche über Stammdaten (363 Werkstoffe + 183 Hersteller)
**Expected GREEN rate improvement:** +1–3 % direkt; größerer Effekt auf Review-Geschwindigkeit
**Zero-False-Positive risk:** High als GREEN-Quelle, None als Ranking für Vorschläge
**Why:** Embeddings fangen Tippfehler/Abkürzungen/Übersetzungen ("X38CrMoV5-1" ↔ "1.2343", "böhler W302" ↔ "1.2344"), die Levenshtein verfehlt. Aber Cosine-Similarity hat keine semantische Garantie — 1.2343 und 1.2344 sind sich *maximal ähnlich und fachlich verschieden*. Daher: nur als Kandidaten-Ranking für YELLOW-Vorschläge, nie als Match-Beweis. Bei 546 Einträgen reicht rapidfuzz praktisch aus; Embeddings lohnen erst bei deutlich größeren Katalogen.
**GDPR compatible:** Yes (Azure-Embeddings oder lokal)
**Effort:** M
**Recommendation:** Implement after v1 (nach DATA-003; vorher überflüssig)
**Reason:** rapidfuzz + voller Katalog deckt 90 % des Nutzens billiger ab.

#### ALT-05: Feedback-Loop als Trainings-/Regelsignal
**Expected GREEN rate improvement:** +10–20 % über 3 Monate (der größte nachhaltige Hebel)
**Zero-False-Positive risk:** Low (wenn Korrekturen → deterministische Regeln statt → Modellvertrauen)
**Why:** Jede YELLOW→bestätigt-Korrektur ist ein Beweisstück: "Alias X meint Kanon Y" (→ materials.json-Alias), "Spalte 'Vergütung' bei Kunde Z ist Material" (→ per-Kunde-Mapping-Registry). Als **deterministische Regel** übernommen, wird derselbe Fall beim nächsten BOM exakt-GREEN — ohne LLM-Vertrauen. Infrastruktur: Korrektur-Routing (ARCH-004), Kunden-Identität (DATA-001), Review-UI für Alias-Vorschläge (Settings-Seite existiert). Geschwindigkeit: Schaufler-Kundenstamm ist klein und wiederkehrend — nach 10–20 BOMs pro Kunde konvergiert das Mapping.
**GDPR compatible:** Yes
**Effort:** L
**Recommendation:** Implement now
**Reason:** Verwandelt menschliche Arbeit einmalig in dauerhafte deterministische GREEN-Quellen — die einzige Verbesserung, die mit der Zeit *von selbst* besser wird.

#### ALT-06: Row-Identity-Redesign (Seite × y-Band als räumliches Ordinal)
**Expected GREEN rate improvement:** 0 % direkt; eliminiert stillen Zeilenverlust (Korrektheit)
**Zero-False-Positive risk:** None (reduziert Risiko)
**Why:** Auf dem Text-Pfad bereits implementiert (RB-1, band_id "p{page}:b{idx}" — genau das geforderte Design). Offen ist der Vision-Pfad (BUG-011): dort Position-als-Identität mit Set-Kollaps. Vision-Zeilen tragen keine echten Koordinaten, aber ein Ordinal (Seite × Lesereihenfolge) plus Multiset-Zählung schließt die T-007-Lücke auch dort.
**GDPR compatible:** Yes
**Effort:** M (nur Vision-Pfad offen)
**Recommendation:** Implement now (Vision-Teil)
**Reason:** Komplettiert eine bereits zu 70 % gebaute, nachweislich richtige Lösung.

#### ALT-07: Two-Pass-Extraktion (erst Struktur, dann Zellen)
**Expected GREEN rate improvement:** +3–7 % auf dem Scan-Pfad
**Zero-False-Positive risk:** Low
**Why:** Phase A/B ist bereits ein grober Two-Pass. Die konsequente Version: Pass 1 ermittelt Tabellen-Bbox + Spaltengrenzen (Vision oder Layout-Modell), Pass 2 extrahiert **zugeschnittene Zellen-/Zeilenbilder** (höhere effektive Auflösung, weniger Kontext-Verwechslung, weniger Spalten-Bleeding). Teurer pro Seite, aber gezielter; kombinierbar mit Batch-Counter-Check (PERF-002).
**GDPR compatible:** Yes
**Effort:** L
**Recommendation:** Implement after v1
**Reason:** Lohnt erst, wenn der Scan-Anteil real signifikant ist (Produktionsdaten abwarten).

#### ALT-08: Hybrid regelbasiert + KI mit automatischer Pfadwahl
**Expected GREEN rate improvement:** bereits realisiert; Rest: +5–10 % durch dritte Strategie vor Vision
**Zero-False-Positive risk:** None
**Why:** Exakt die bestehende Routing-Logik (structure_normalizer: Text-Layer? → RB-1, sonst Vision; RB-1 lehnt selbst ab, wenn unzuverlässig — `_reconstruction_reliable`). Die Erkennung "welcher Ansatz" existiert und funktioniert. Fehlend: die mittlere Stufe (deterministische Tabellen-Extraktoren als zweiter Versuch, LIB-02/03) und Telemetrie, welcher Pfad wie oft mit welchem Ergebnis läuft.
**GDPR compatible:** Yes
**Effort:** M
**Recommendation:** Implement now (dritte Strategie + Pfad-Telemetrie)
**Reason:** Jedes Dokument, das vom Vision- auf einen deterministischen Pfad wandert, gewinnt GREEN-Fähigkeit und verliert Kosten.

### Teil 3: Konkrete Roadmap

**1. Höchster Impact bei null Vertragsrisiko:** **DATA-003 — Stammdaten-Import aus dem Template** (363 Werkstoffe, 181 Hersteller, 15 Teilegruppen). Eine Mittags-Aufgabe mit zweistelligem GREEN-Punkte-Effekt: exakte Katalog-Matches sind die sicherste GREEN-Quelle des Systems, und der Katalog ist heute zu 95 % leer. Direkt danach: BUG-005-Governance (sonst steht das neue GRÜN auf gelockerten Schwellen).

**2. Realistische GREEN-Rate nach 3 Monaten Produktion mit aktivem Feedback-Loop:** **45–60 % der gescorten Zellen auf born-digital PDFs** (Annahmen: voller Katalog, Loop repariert per DATA-001, Alias-/Mapping-Lernen per ARCH-004, wiederkehrende Kunden). Scans bleiben nahe 0 % GREEN, bis Counter-Check-Batching (PERF-002) sie wirtschaftlich macht — dann 20–30 % auf Scans. Über den Mix: **~40–50 %** — der Mahler-Benchmark ist erreichbar, aber nur mit verlässlichem GRÜN (deshalb zuerst die BUG-00x-Schließung).

**3. Theoretisches Maximum für Schaufler-BOMs:** ~**70–75 %** der gescorten Zellen (born-digital, voller Katalog, gelernte Kunden-Mappings, Yellow-Recheck aktiv). Der Rest ist strukturell YELLOW/RED: leere optionale Felder, Freitexte ohne Anker (Special Notes), echte Qualitätsmängel der Quelle, Scans schlechter Qualität. 100 % waren nie das Ziel — Human-in-the-Loop ist einkalkuliert.

**4. Priorisierte Verbesserungen:**
- **Quick Wins (< 1 Woche):** DATA-003 (Stammdaten-Import) · BUG-003/BUG-009/BUG-013 (Kleinst-Patches) · BUG-005 (Config-Governance + Banner) · SEC-001/SEC-002 (Key-Rotation, Default-Admin aus) · TEST-002/003 (Suite grün) · BUG-018 (CSV ehrlich machen)
- **Mittelfristig (1–4 Wochen):** BUG-001/002/004/006/007/008 (Evidenz-Härtung als zusammenhängender Scoring-Sprint, mit Canary-Suite TEST-001 als Abnahme) · DATA-001 + ARCH-004 (Lern-Loop produktiv schalten) · ALT-03 (Fuzzy→Vorschlag) · OPS-001/002/003 (CI, Pinning, Health) · PERF-002 (Counter-Check-Batching) → danach Counter-Check reaktivieren
- **Langfristig (1–3 Monate):** ARCH-005 (Mapping-Konsens) · ARCH-002 (Yellow-Recheck) · ALT-08 (dritte Extraktionsstrategie, LIB-02/03) · BUG-011/ALT-06 (Vision-Zeilen-Identität) · ARCH-003 (Excel-GREEN-Policy) · ARCH-001 (OpenDataLoader-Entscheid)

**5. Daten, die ab Tag 1 der Produktion gesammelt werden sollten:**
- Pro Zelle: finale Klassifikation + alle Evidenzen (existiert: Audit-Trail) **und** das Review-Outcome (bestätigt/korrigiert/ignoriert) — die Verknüpfung Audit×Outcome ist der Goldstandard-Datensatz für jede spätere Modell-/Schwellen-Optimierung.
- Pro Dokument: Pfad (RB-1/Vision/Legacy), Decline-Gründe, Seiten, Dauer, Token (JOB_METRICS existiert — persistieren statt nur loggen).
- Pro Korrektur: Typ (Wert/Mapping/Exclusion), Kunde, Feld — bereits im FeedbackStore, aber customer reparieren (DATA-001).
- "GREEN-Audit-Stichprobe": wöchentlich N zufällige GREEN-Zellen manuell prüfen und die empirische False-Positive-Rate dokumentieren — das ist die Zahl, die Jürgen Mahler am Ende überzeugen oder brechen wird.

### Teil 4: Wettbewerbsvergleich

- **Kommerzielle BOM-/Dokumenten-Tools** (z. B. ERP-Importer, generische IDP-Suiten wie ABBYY/Rossum): stark in Volumen-Standardformaten (Rechnungen), schwach im Werkzeugbau-Long-Tail (CAD-Rahmen-PDFs, rotierte Matrizen, Werkstoff-Semantik). Keines bietet einen zellgenauen Zero-False-Positive-Vertrag mit Audit-Trail — deren Konfidenzen sind kalibrierte Wahrscheinlichkeiten, kein Beweisbegriff.
- **Was SAP/Oracle bauen würden:** Document Information Extraction (SAP BTP DOX) + manuelles Mapping-Customizing pro Kunde — 6-stellige Integrationsprojekte, Schema-zentriert, ohne per-Zelle-Evidenz. Der "neue Kunde = 0 Code" Anspruch wäre dort ein Customizing-Projekt pro Kunde.
- **Prozessias verteidigbarer Vorteil:** (1) das **Evidenz-Modell** — green_evidence/hard_vetoes/source_location pro Zelle ist ein Audit-Artefakt, das Großanbieter nicht nachrüsten können, ohne ihre Pipeline neu zu bauen; (2) die **RB-1-Idee** (deterministische Koordinaten-Rekonstruktion als Quelle UND Beweis); (3) der **Feedback→deterministische-Regel-Loop** (statt Modell-Retraining); (4) Schaufler-Domänenwissen (Werkstoffkunde, Teilegruppen-Semantik) in den Stammdaten. Diese vier konsequent ausgebaut sind schwer kopierbar; das LLM selbst ist es nicht.

---

### Teil 5: Open-Source-Bibliotheken & Tool-Recherche (2024–2026)

*(Nur Python-Dependencies/CLI-Tools; keine lokalen GPU-Modelle als Pflicht — Docling/MinerU laufen optional CPU-only. Sterne/Status per Web-Recherche Juni 2026, vor Einsatz verifizieren.)*

#### LIB-01: docling (IBM / Linux Foundation)
**GitHub:** github.com/docling-project/docling (≈ 37k→61k Stars 2025→2026, sehr aktiv; seit 2026 Linux Foundation)
**What it does:** Dokument-Konvertierung mit Layout-Modell + TableFormer-Tabellenstruktur-Erkennung (97.9 % Zell-Genauigkeit in Benchmarks), Output als strukturiertes JSON/Markdown mit Zell-Koordinaten.
**Our use case:** Dritte Extraktionsstrategie zwischen RB-1-Decline und Vision (ALT-08): born-digital PDFs mit komplexen/rotierten Layouts (GF!), deren Header-Heuristik RB-1 ablehnt. Liefert Tabellen MIT Zellrelationen und Provenance (Seite+Bbox) — kompatibel mit dem bestehenden source_location-Vertrag.
**Replaces:** Vision-Fallback für einen Teil der Decline-Fälle; perspektivisch pdf_parser_legacy.
**Integration point:** Lock 2 / Ingestion (vor Vision)
**Expected improvement:** GF-artige Layouts werden deterministisch statt per Vision verarbeitet → GREEN-fähig + billiger.
**Risk to Zero-False-Positive:** Low — Output ist deterministisch nachprüfbar (Koordinaten gegen PyMuPDF-Wörter verifizieren, wie heute beim Vision-Pfad); das TableFormer-Modell selbst ist ML → Struktur immer gegen Text-Layer gegenchecken.
**Install:** pip install docling
**License:** MIT
**Verdict:** Evaluate (gegen die 19 POC-PDFs + GF als Akzeptanztest)

#### LIB-02: camelot-py
**GitHub:** github.com/camelot-dev/camelot (~3k Stars, aktiv gepflegt; tabula-py wurde Jan 2025 archiviert!)
**What it does:** PDF-Tabellenextraktion mit 5 Parsern — lattice (gezeichnete Linien!), stream, hybrid, network, optional ML; Output als DataFrame mit Zell-Bboxen.
**Our use case:** Viele Werkzeugbau-BOMs haben **vollständig umrandete Tabellen** (CAD-Rahmen) — genau der Lattice-Sweet-Spot. Als zweite deterministische Meinung neben RB-1: stimmen Zeilen-/Spaltenzahl überein → starkes Struktur-Konfidenzsignal; weichen sie ab → YELLOW-Flag auf Strukturebene.
**Replaces:** Ergänzt coordinate_table (RB-1 ist korridorbasiert und kennt keine gezeichneten Linien — Lattice nutzt exakt die Information, die RB-1 ignoriert).
**Integration point:** Lock 2 / Ingestion (Parser-Ensemble)
**Expected improvement:** Korrekte Spaltentrennung bei engen Spalten ohne Whitespace-Lücke; Struktur-Gegenprobe.
**Risk to Zero-False-Positive:** None (deterministisch, zusätzliches Veto-Signal)
**Install:** pip install "camelot-py[base]" (braucht ghostscript für lattice)
**Verdict:** Use now (als Struktur-Gegenprobe), tabula-py NICHT (archiviert)

#### LIB-03: pymupdf4llm
**GitHub:** github.com/pymupdf/RAG (Teil des PyMuPDF-Ökosystems, aktiv)
**What it does:** PyMuPDF-Wrapper, der Seiten als LLM-freundliches Markdown mit erkannten Tabellen ausgibt (0.12 s/Dokument, CPU-only, gleiche Dependency wie heute).
**Our use case:** Besserer `document_text_layer` für die Anchor-Suche des Scorers (heute selbstgebautes ROW-Banding mit x-Tags, die wieder herausgefiltert werden müssen — Quelle der Phantom-Positions-Problematik B1) und als kompakter Prompt-Input statt eigener Layout-Serialisierung.
**Replaces:** `_render_layout_aware_page_text` in pdf_parser.py (Eigenbau).
**Integration point:** Lock 2 / Text-Layer-Erzeugung
**Expected improvement:** Weniger Eigenbau-Wartung, robustere Tabellen-Markdown-Struktur; minimal — RB-1 bleibt die primäre Strukturquelle.
**Risk to Zero-False-Positive:** None
**Install:** pip install pymupdf4llm
**License:** AGPL-3.0 wie PyMuPDF (bereits im Stack — Lizenzlage unverändert; für kommerzielle Distribution prüfen!)
**Verdict:** Evaluate

#### LIB-04: marker / MinerU (PDF→Markdown-Konverter mit ML-Layout)
**GitHub:** github.com/datalab-to/marker (~20k+); github.com/opendatalab/MinerU (höchste Star-Zahl der Kategorie; April 2026 Lizenzwechsel AGPL→Apache-basiert)
**What it does:** Vollpipeline PDF→Markdown/JSON inkl. Tabellen, Formeln, Cross-Page-Table-Merging (MinerU 2.5).
**Our use case:** Begrenzt — beide sind auf Durchsatz/RAG optimiert, nicht auf zellgenaue Provenance. Cross-Page-Table-Merging (MinerU) ist für mehrseitige BOMs interessant.
**Replaces:** nichts Kritisches.
**Integration point:** Lock 2b-Ersatzkandidat (statt OpenDataLoader-Merge, ARCH-001)
**Expected improvement:** Strukturspur für Dokumente, die RB-1 + Camelot + Docling alle verfehlen — kleiner Rest.
**Risk to Zero-False-Positive:** Low (nur als Vorschlags-/Strukturquelle, nie als Beweis)
**Install:** pip install marker-pdf / mineru
**License:** marker GPL-3.0 (!), MinerU custom Apache-basiert — marker-Lizenz für SaaS prüfen.
**Verdict:** Not suitable (marker, Lizenz) / Evaluate (MinerU, als Lock-2b-Kandidat)

#### LIB-05: rapidfuzz
**GitHub:** github.com/rapidfuzz/RapidFuzz (~3k Stars, sehr aktiv, MIT, C++-Kern)
**What it does:** Schnelles Fuzzy-String-Matching (bereits im Stack!).
**Our use case:** Bleibt das richtige Werkzeug — aber Scorer-Wahl korrigieren: `WRatio` (Partial-Matching-Anteile) für Alias-Kataloge durch `token_sort_ratio`/`ratio` ersetzen und `processor=utils.default_process` nutzen (BUG-008); für Hersteller-Matching (neu, DATA-003) `process.extract` mit score_cutoff 92 + Mindestlänge.
**Replaces:** — (Konfigurationskorrektur)
**Integration point:** Lock 3 / Stammdaten
**Expected improvement:** Weniger falsche Fuzzy-Kanonisierung; Hersteller-Vorschläge.
**Risk to Zero-False-Positive:** None (im Vorschlagsmodus, ALT-03)
**Install:** bereits installiert
**License:** MIT
**Verdict:** Use now (Scorer-/Cutoff-Korrektur)

#### LIB-06: pandera
**GitHub:** github.com/unionai-oss/pandera (~3.5k Stars, aktiv; 0.29 Jan 2026)
**What it does:** Leichtgewichtige DataFrame-/Tabellen-Schema-Validierung (12 Dependencies vs. 107 bei great-expectations).
**Our use case:** Strukturvalidierung der extrahierten BOM-Tabelle VOR dem Mapping: "Positionsspalte monoton/eindeutig je Band", "Mengen-Spalte ≥ 60 % integer", "Spaltenzahl konstant je Section" — als deterministisches Frühwarnsignal "Extraktion strukturell kaputt" (heute verstreute Ad-hoc-Checks).
**Replaces:** Teile von _post_validate_extraction / mapping_validator-Typchecks (Konsolidierung).
**Integration point:** Pipeline (zwischen Ingestion und Mapping)
**Expected improvement:** Strukturfehler werden als Dokument-Flag sichtbar statt als 100 Einzel-YELLOWs.
**Risk to Zero-False-Positive:** None (nur zusätzliche Vetos)
**Install:** pip install pandera
**License:** MIT
**Verdict:** Evaluate (great-expectations: Not suitable — zu schwer für diesen Stack)

#### LIB-07: pdfplumber
**GitHub:** github.com/jsvine/pdfplumber (~8k Stars, gepflegt)
**What it does:** Koordinatenbasierte Text-/Tabellen-/Linien-Extraktion mit feiner Kontrolle (chars/words/lines/rects).
**Our use case:** Die `rects`/`lines`-API liefert **gezeichnete Tabellenlinien**, die PyMuPDF-words nicht hergeben — daraus echte Spaltengrenzen statt Korridor-Schätzung für RB-1 (präzisere x-Grenzen = weniger column_boundary-Ambiguität = mehr GREEN-fähige Zellen).
**Replaces:** Ergänzt coordinate_table (nur Linien-Detektion; Wort-Extraktion bleibt PyMuPDF).
**Integration point:** Lock 2 / RB-1-Korridore
**Expected improvement:** Bessere Spaltentrennung bei linierten Tabellen; weniger Boundary-YELLOWs.
**Risk to Zero-False-Positive:** None
**Install:** pip install pdfplumber
**License:** MIT
**Verdict:** Evaluate (alternativ: PyMuPDF page.get_drawings() — gleiche Info ohne neue Dependency; zuerst prüfen!)

#### LIB-08: unstructured
**GitHub:** github.com/Unstructured-IO/unstructured (~12k Stars)
**What it does:** Generische Dokument-Partitionierung für RAG-Pipelines.
**Our use case:** Benchmarks zeigen schwächere Tabellenqualität als Docling; Mehrwert gegenüber dem bestehenden Stack gering, schwere Dependency-Kette.
**Risk to Zero-False-Positive:** —
**Verdict:** Not suitable

#### LIB-09: xlsxwriter vs. openpyxl (Excel-Output)
**What it does / our use case:** xlsxwriter schreibt nur (kann keine bestehenden Templates füllen) — für den Schaufler-Anwendungsfall (vorhandene Vorlage exakt befüllen, Stile erhalten) ist **openpyxl die richtige und einzige Wahl**; der Exporter nutzt sie korrekt (Template laden, Referenz-Stile kopieren, Zellfarben).
**Verdict:** Keep openpyxl (kein Wechsel); Ergänzung: `keep_vba`/Formel-Erhalt testen, falls Schaufler Vorlagen mit Formeln liefert.

#### LIB-10: pdf-diff / diff-pdf (BOM-Versionsvergleich)
**GitHub:** github.com/JoshData/pdf-diff (Python, pdftotext-basiert); vslavik/diff-pdf (visuell)
**What it does:** Text-/visueller Diff zweier PDFs.
**Our use case:** Nicht direkt — besser auf der **strukturierten Ebene** diffen: zwei verarbeitete BOMs als Zeilen-Sets (Band/Position-Schlüssel) vergleichen → "Rev. B vs. Rev. A: 3 Positionen neu, 1 entfernt, 2 Mengen geändert". Die gesamte Infrastruktur (ParsedBOM, Audit) existiert; der Diff ist reine Anwendungslogik.
**Integration point:** Neues Feature auf Audit-Ebene (siehe IDEA-02)
**Risk to Zero-False-Positive:** None
**Verdict:** Eigenbau auf Audit-Ebene statt PDF-Diff-Library

#### LIB-11: jellyfish / langdetect / pint
**What it does:** Phonetik (jellyfish), Spracherkennung (langdetect), Einheiten (pint).
**Our use case:** jellyfish (Phonetik) passt nicht zu Werkstoff-Codes (keine Aussprache-Semantik) — Not suitable. langdetect: nettes Metadatum für Statistik/Mapping-Prompt, kein Genauigkeitshebel — Nice to have. pint: überdimensioniert für mm/inch (eigene Konversion existiert und ist getestet) — Not suitable.
**Verdict:** s. o.

### Teil 6: Unkonventionelle Ansätze

#### IDEA-01: BOM-Hierarchie-Visualisierung (Mermaid/Tree)
**Concept:** Positionen mit Unterpositionen (1, 1.1, 1.2 / Detail-Number-Hierarchien) als einklappbarer Baum neben dem Grid; Ampelfarben aggregiert pro Ast.
**Library/tool:** Frontend-seitig (ag-grid Tree Data — bereits im Stack!); kein Backend-Aufwand außer Hierarchie-Ableitung aus Detail Numbers.
**Value for Schaufler:** Reviewer sieht Baugruppen-Kontext ("alle Schieber-Teile gelb → vermutlich Spalten-Mapping, nicht Einzelfehler") — beschleunigt Muster-Erkennung im Review.
**Effort:** M
**Verdict:** Nice to have

#### IDEA-02: BOM-Versions-Diff (Rev. A → Rev. B)
**Concept:** Kunde schickt aktualisierte Stückliste → System matcht Zeilen über Band-/Positions-Schlüssel + Customer Part Number und zeigt: neu / entfernt / geändert (mit Feld-Diff). Bereits geprüfte, unveränderte Zeilen übernehmen ihren Review-Status — nur das Delta braucht Review.
**Library/tool:** Eigenbau auf ParsedBOM/Audit-Ebene (LIB-10); difflib für Sequence-Alignment der Positionsfolgen.
**Value for Schaufler:** Revisionsschleifen sind im Werkzeugbau der Normalfall; "nur das Delta prüfen" ist möglicherweise mehr Zeitersparnis als jede GREEN-Raten-Steigerung. Starkes Demo-Feature für Mahler.
**Effort:** L
**Verdict:** Worth exploring (nach v1-Härtung das beste neue Feature)

#### IDEA-03: Material↔Hersteller↔Teilegruppe als Konsistenz-Graph
**Concept:** Aus den Template-Stammdaten + bestätigten Reviews ein Kookkurrenz-Wissen aufbauen ("Teilegruppe E kommt nie mit Werkstoff AlSi9Cu3", "Hersteller Meusburger ⇒ Normalie ⇒ Teilegruppe N") und als Cross-Validator-Regeln nutzen.
**Library/tool:** Kein Graph-Framework nötig — dict-basierte Kookkurrenz-Tabellen im cross_validator; optional networkx.
**Value for Schaufler:** Zusätzliche deterministische Plausibilitäts-Vetos (Richtung "mehr Sicherheit", nicht "mehr GREEN") — passt exakt zur Vertragsphilosophie.
**Effort:** M
**Verdict:** Worth exploring

#### IDEA-04: Template-Fingerprinting ("dieses BOM-Format kennen wir")
**Concept:** Layout-Fingerprint pro Dokument (Header-Token-Sequenz + Spalten-x-Profil, normalisiert) → Wiedererkennung "Format = Magna-Stückliste v2" → gespeichertes, menschlich bestätigtes Spalten-Mapping wird OHNE LLM-Call angewendet; nur unbekannte Fingerprints gehen durchs LLM-Mapping.
**Library/tool:** Eigenbau (~100 Zeilen: SHA über normalisierte Header-Tokens + gerundete Spalten-Zentren); Speicherung in data/learned_mappings/ (dafür existiert das Verzeichnis bereits).
**Value for Schaufler:** Wiederkehrende Kundenformate (der Normalfall!) bekommen deterministisches Mapping = die Hauptquelle systematischer Wrong-GREEN-Risiken (ARCH-005) verschwindet für Bestandskunden komplett; zudem schneller und billiger.
**Effort:** M
**Verdict:** Worth exploring — höchster Wert der vier Ideen, direkte Synergie mit ALT-05/ARCH-004

---

## ZUSAMMENFASSUNG

**Tickets gesamt: 52** (21 BUG, 6 SEC, 8 OPS, 5 ARCH, 2 PERF, 4 DATA, 3 FE, 3 TEST)
- **Critical: 9** — BUG-001, BUG-002, BUG-003, BUG-004, BUG-005, BUG-006, SEC-001, SEC-002, DATA-003
- **High: 14** — BUG-007, BUG-008, BUG-009, BUG-010, BUG-011, BUG-013, BUG-018, OPS-001, OPS-003, ARCH-001, ARCH-002, ARCH-005, DATA-001, TEST-001
- **Medium: 25** — BUG-012, BUG-014, BUG-015, BUG-016, BUG-017, BUG-019, BUG-020, BUG-021, SEC-003, SEC-004, SEC-005, SEC-006, OPS-002, OPS-004, OPS-005, OPS-006, ARCH-003, ARCH-004, PERF-001, PERF-002, DATA-002, DATA-004, FE-001, TEST-002, TEST-003
- **Low: 4** — OPS-007, OPS-008, FE-002, FE-003

**Anforderungen: 40 geprüft** — 19 vollständig (47.5 %), 14 teilweise (35 %), 6 nicht implementiert (15 %), 1 unklar (2.5 %).

**Zero-False-Positive-Risiken gefunden: 12** (BUG-001, 002, 003, 004, 005, 006, 007, 008, 009, 010, DATA-004, ARCH-005 — davon 6 Critical).

**Testlauf:** 286 Tests, 283 lauffähig geprüft, **3 Failures** (GF-Legacy-Parser, 2× Auth-Leakage in test_job_source_route).

**Top 5 kritischste Lücken für die Jürgen-Mahler-Demo:**
1. **DATA-003 — Stammdaten zu 95 % leer** (18/363 Werkstoffe, 0 Hersteller, 12/15 Teilegruppen): Die GREEN-Rate ist künstlich niedrig, und genau die Felder, die Mahler zuerst prüft (Werkstoff!), bleiben gelb. Vollständige Daten liegen ungenutzt im eigenen Template.
2. **SEC-002 + SEC-001 — admin/admin auf der öffentlichen Produktions-URL + Azure-Key in der Git-Historie:** Ein einziger neugieriger Besucher vor der Demo genügt, um Vertrauen irreparabel zu beschädigen — und das Thema ist "hoch aufgehangen".
3. **BUG-005 — die Produktiv-Config hat die Schutzschwellen gelockert und den Counter-Check deaktiviert:** Wenn Mahler fragt "wie stellt ihr 100 % sicher?", beschreibt die Antwort derzeit ein System, das so nicht konfiguriert ist.
4. **BUG-001/003/004/006 — zirkuläre bzw. ortsungebundene GREEN-Evidenz:** Die konkreten Pfade, über die ein einziges falsches GRÜN in der Demo entstehen kann — exakt der Fehler, an dem die vier Vorgänger gescheitert sind. Vor jeder Demo mit echten Kundendaten schließen.
5. **DATA-001 + ARCH-004 — der Lern-Loop ist produktiv tot (customer="")**: "Das System lernt aus euren Korrekturen" ist ein Kernversprechen des Angebots und derzeit nicht erlebbar.

**Stärken, die es zu erhalten gilt (ausdrücklich):** das zentrale Green-Gate als Single Source of Truth, die RB-1-Koordinaten-Rekonstruktion mit Band-Identität, der Set-basierte Export-Guard (ZDL-4), die ehrliche Completeness-Auskunft (ZDL-1), der Audit-Trail pro Zelle mit green_evidence/hard_vetoes, Retry-After-respektierendes Backoff und die saubere MANUAL_CONFIRMED-Trennung. Die Architektur ist richtig gedacht — die Lücken liegen in Evidenz-Härtung, Daten-Befüllung und Betriebs-Disziplin.
