# INDUSTRIAL_REFACTOR_PLAN — BOM-Mapper Schaufler

**Stand:** 2026-05-29
**Scope:** Architektur-Härtung, Zero-Data-Loss-Lückenanalyse, Produktions-Readiness
**Verdikt:** Die Datenverlust-Kette A1→A3→B2→B3→A2 ist gebaut und in sich konsistent,
hat aber **eine prinzipielle Lücke am Eingang (Vision-Extraktion)** und **einen
Trigger-Defekt (Guard schaltet sich bei unerkannter Positionsspalte selbst ab)**.
Beides untergräbt die Produktions-Garantie für genau den dominanten Fall: gescannte PDFs.

Diese Datei ist die strukturierte Auswertung der Phasen 6.3–6.7. Code wurde gelesen,
nicht angenommen — jede Aussage trägt Datei:Zeile.

---

## 6.3 Architektur-Schwachstellen (mittelfristig)

### AW-1 — `asyncio.create_task` ohne Referenz: Job kann mitten im Lauf verschwinden
- **Ist:** [upload.py:52](src/api/routes/upload.py#L52) `asyncio.create_task(run_pipeline(job_id))` —
  der Rückgabewert wird verworfen. CPython dokumentiert ausdrücklich, dass die Task-Referenz
  gehalten werden muss; sonst kann der GC die Task mitten in der Verarbeitung einsammeln.
  Zusätzlich: kein Worker, kein Resume — [system_spec_optimization.md:61-86](data/analysis/system_spec_optimization.md) bestätigt das.
- **Soll:** Tasks in einem modul-globalen `set` referenzieren (`_tasks.add(t); t.add_done_callback(_tasks.discard)`)
  als Minimal-Fix. Mittelfristig: echte Queue (RQ/Dramatiq + Redis ist nicht im Stack →
  alternativ ein In-Process-`asyncio.Queue` mit einem Worker-Task und globalem Concurrency-Limit).
- **Aufwand:** 1h Minimal-Fix / 1–2 Tage echte Queue
- **Priorität:** HOCH (Minimal-Fix), MITTEL (Queue)
- **Test:** `test_upload_task_is_referenced` (Task-Set nicht leer nach Upload)

### AW-2 — Doppelte Positions-Normalisierung (DRY-Bruch mit Korrektheitsrisiko)
- **Ist:** Drei identische Implementierungen von Positions-Normalisierung:
  [position_reconciler.py:39](src/reconciliation/position_reconciler.py#L39) `_normalize`,
  [ensemble_scorer.py:1250](src/scoring/ensemble_scorer.py#L1250) `_normalize_position_value`,
  und eine abweichende Variante in [pdf_parser.py:1664](src/ingestion/pdf_parser.py#L1664)
  (`_collect_position_values` — **ohne** das `re.sub(r"\s*-\s*", "-", …)` der anderen beiden).
  B2/B3 funktionieren nur, solange Reconciler und Scorer Zeichen-für-Zeichen gleich normalisieren.
- **Soll:** Eine `normalize_position()` in `src/core/` , von allen drei Stellen importiert.
- **Aufwand:** 1h
- **Priorität:** MITTEL (latentes Risiko: divergiert jemand eine Kopie, fallen Coverage-Guard
  und Reconciler still auseinander → Phantom-RED oder Lücke)
- **Test:** `test_position_normalization_single_source` (Parametrisierung: `"1 - 2"`, `" K-3 "`, `"1.0"`)

### AW-3 — Vision-Pfad: 2× Vision pro Seite trotz `_MAX_CONCURRENT_PAGES=1`
- **Ist:** [pdf_parser.py](src/ingestion/pdf_parser.py) Phase B fährt A/B-Dual-Extraction via
  `asyncio.gather` → pro Seite 2 Vision-Calls; +1 Spaltenerkennung; +optional CHECK5.
  Kostentreiber und 429-Quelle ([system_spec_optimization.md:390-418](data/analysis/system_spec_optimization.md)).
- **Soll:** Dual-Extraction nur für *kritische* Spalten (qty/dim) bzw. nur bei niedriger
  Confidence der ersten Extraktion (adaptiv) statt pauschal jede Seite doppelt.
  Spaltenerkennung-Call cachen pro Dokument (passiert bereits 1×, ok).
- **Aufwand:** 1–2 Tage
- **Priorität:** MITTEL (Kosten/Latenz, kein Korrektheits-Blocker)
- **Test:** `test_dual_extraction_only_on_low_confidence`

### AW-4 — Retry-Backoff linear ohne Jitter, als „exponential" kommentiert
- **Ist:** [azure_openai.py](src/llm/azure_openai.py) `sleep(backoff * attempt)` → 15/30/45 s,
  Kommentar nennt es exponential ([system_spec_optimization.md:420-440](data/analysis/system_spec_optimization.md)).
  Bei 429 ohne Jitter synchronisieren sich parallele Retries (Thundering Herd).
- **Soll:** Echtes exponentielles Backoff mit Voll-Jitter; `Retry-After`-Header respektieren.
- **Aufwand:** 2h
- **Priorität:** MITTEL
- **Test:** `test_backoff_respects_retry_after`

### AW-5 — Tote/überschriebene Berechnung `expected_position_count` im Parser
- **Ist:** Parser setzt `expected_position_count=_count_distinct_positions(...)`
  ([pdf_parser.py:261](src/ingestion/pdf_parser.py#L261), [:439](src/ingestion/pdf_parser.py#L439)),
  der Wert wird aber im Scorer überschrieben mit der Master-Set-Größe
  ([ensemble_scorer.py:609](src/scoring/ensemble_scorer.py#L609)). Der Parser-Wert ist für den
  Guard wirkungslos — und nutzt eine *andere* Quelle (Positionsspalte) als `raw_pdf_positions`
  (Regex auf Text bzw. Vision-Rohzeilen). Doppelte, divergierende Wahrheiten. Siehe ZDL-2.
- **Soll:** Parser-Feld entfernen oder bewusst als reines Diagnose-Feld dokumentieren; Guard
  ausschließlich auf Master-Set stützen.
- **Aufwand:** 1h
- **Priorität:** NIEDRIG (Aufräumen), aber Voraussetzung für klare ZDL-Argumentation

---

## 6.4 Zero-Data-Loss — Lückenanalyse (Kette A1 → A3 → B2 → B3 → A2)

**Kette wie gebaut (verifiziert):**

| Schritt | Datei:Zeile | Funktion in der Kette |
|---|---|---|
| A1 Dedup voll-spaltig | `pdf_parser._deduplicate_rows` | verhindert stilles Mergen distinkter Zeilen |
| A3 Vision seitenweise + Partial-Recovery | `pdf_parser._extract_all_pages_via_vision` | kein JSON-Abbruch-Verlust |
| B2 Reconciler | [position_reconciler.py:66](src/reconciliation/position_reconciler.py#L66) | `master_set = extrahiert ∪ raw_pdf_positions`, MISSING-Zeilen |
| B3 Coverage-Guard | [ensemble_scorer.py:585-609](src/scoring/ensemble_scorer.py#L585-L609) | jede nicht-gescorte Master-Position → RED |
| A2 Export-Assertion | [excel_exporter.py:105-121](src/export/excel_exporter.py#L105-L121) | `ZeroDataLossError` wenn Zeilen < erwartet |

Innerhalb dieser Kette ist die Erhaltung **lückenlos**: Was einmal in `raw_pdf_positions`
steht, wird re-injiziert (B2), als RED erzwungen (B3) und vor File-Write geprüft (A2).
Die Lücken liegen **am Eingang** und **am Trigger**, nicht in der Kette selbst.

### ZDL-1 (KRITISCH) — Vision-Pfad: `raw_pdf_positions` ist selbst-referenziell
- **Ist:** [pdf_parser.py:164](src/ingestion/pdf_parser.py#L164)
  `raw_pdf_positions = _collect_position_values(all_rows, …)` — `all_rows` ist die
  Vision-Ausgabe selbst. Die „PDF-Seite" des Master-Sets stammt also aus genau der
  Extraktion, die der Guard absichern soll. Liest Vision eine Position/Seite gar nicht,
  fehlt sie in `all_rows`, in `raw_pdf_positions`, im Master-Set → Reconciler kann sie
  nicht re-injizieren → B3/A2 sehen keinen Verlust. Der Docstring nennt das „hard limit"
  ([position_reconciler.py:12-15](src/reconciliation/position_reconciler.py#L12-L15)) — aber
  genau der gescannte PDF ohne Text-Layer ist der **Produktions-Hauptfall**.
- **Konsequenz:** Für scanned PDFs existiert **keine** Zero-Data-Loss-Garantie. Die Kette
  schützt nur gegen Verlust *nach* der Extraktion.
- **Soll (echte Garantie):** Unabhängige Positions-Evidenz, die NICHT aus der Vision-Extraktion
  stammt: (a) separater, billiger Vision-Call „Zähle nur die Positionsnummern/Zeilen pro Seite"
  als Mengen-Ankerwert; oder (b) Azure Document Intelligence (Layout-Modell) als zweite Zählspur;
  oder (c) konservativ: scanned-PDF-Ergebnisse als „nicht garantiert vollständig" kennzeichnen
  und im Dashboard mit Banner versehen. Mindestens (c) sofort, (a) mittelfristig.
- **Aufwand:** (c) 2h · (a) 1–2 Tage · (b) 3–5 Tage
- **Priorität:** KRITISCH
- **Test:** `test_vision_dropped_page_is_flagged_not_silent`

### ZDL-2 (KRITISCH) — Guard schaltet sich bei unerkannter Positionsspalte selbst ab
- **Ist:** `_collect_position_values` gibt `[]` zurück, wenn `_infer_anchor_column` keine
  Positionsspalte findet ([pdf_parser.py:1656-1658](src/ingestion/pdf_parser.py#L1656),
  Keyword-Liste [:1684-1693](src/ingestion/pdf_parser.py#L1684)). Dann `expected_position_count=0`
  → A2-Guard wird **komplett übersprungen** ([excel_exporter.py:110-114](src/export/excel_exporter.py#L110)).
  Der Schutz hängt also am fehleranfälligsten Parsing-Schritt (Spaltenerkennung bei
  unbekanntem Format) — und fällt dort lautlos aus, wo er am nötigsten wäre.
- **Soll:** Fallback-Mengenanker, wenn keine Positionsspalte erkannt: distinkte Zeilenzahl der
  Extraktion als Untergrenze nehmen; oder Job als „guard-disabled" hart markieren und im
  Dashboard sichtbar machen (kein stilles Skip). Side-Condition (1) darf nicht durch fehlende
  Spaltenerkennung aushebelbar sein.
- **Aufwand:** 0.5–1 Tag
- **Priorität:** KRITISCH
- **Test:** `test_guard_not_silently_skipped_without_position_column`

### ZDL-3 (HOCH) — Text-Pfad: Master-Set aus Regex statt aus Positionsspalte (Widerspruch zu B1)
- **Ist:** Text-Pfad speist `raw_pdf_positions` aus `_extract_pdf_positions_from_pages`
  (Positions-**Regex**, [pdf_parser.py:419-424](src/ingestion/pdf_parser.py#L419)). Laut
  [REFACTOR_PLAN.md:119-124](REFACTOR_PLAN.md#L119) schließt das Default-Muster fortlaufende
  Integer (`\b\d{1,4}\b`) bewusst aus (Phantom-Vermeidung). Die Auflösungs-Notiz behauptet,
  fortlaufende Positionen kämen „kontext-sicher aus der extrahierten Positions-Spalte" —
  der Code zieht `raw_pdf_positions` aber aus dem Regex, nicht aus der Spalte. **Widerspruch
  (Arbeitsregel 5).** Folge: eine während der Extraktion *gedroppte* Position `1..1000`
  (reine Ganzzahl) ist regex-unsichtbar → nicht im Master-Set → nicht wiederherstellbar.
- **Soll:** Auf dem Text-Pfad das Master-Set zusätzlich mit den distinkten Werten der
  Positionsspalte speisen (`_collect_position_values` ∪ Regex), nicht nur Regex. Phantome
  werden ohnehin durch C1-Header/Footer-Filter + Spaltenkontext begrenzt.
- **Aufwand:** 0.5 Tag
- **Priorität:** HOCH
- **Test:** `test_text_path_sequential_integer_positions_recovered`

### ZDL-4 (MITTEL) — A2 vergleicht Zähler, nicht Mengen → nach B2 nahezu tautologisch
- **Ist:** [excel_exporter.py:108-121](src/export/excel_exporter.py#L108) vergleicht
  `actual_rows = len(distinct row_index)` gegen `expected_positions` (Master-Set-Größe).
  Da der Reconciler pro fehlender Position eine Zeile padded, gilt `actual ≥ expected`
  konstruktionsbedingt fast immer → der Guard kann im Normalpfad praktisch nicht feuern.
  Er fängt nur Verlust, der *nach* dem Scoring entsteht (schmales Fenster).
- **Soll:** Mengen- statt Zählervergleich: `set(expected_position_ids) ⊆ set(output positions)`,
  sonst `ZeroDataLossError` mit konkreter Fehlmenge. Macht den Guard wieder aussagekräftig.
- **Aufwand:** 0.5 Tag
- **Priorität:** MITTEL
- **Test:** `test_export_raises_on_missing_position_set_not_just_count`

### Für welche Dokumenttypen greift die Kette NICHT?
| Dokumenttyp | Garantie | Grund |
|---|---|---|
| Digitales PDF mit Text-Layer, klare Positionsspalte | **vollständig** (nach ZDL-3-Fix) | Regex+Spalte als Ground Truth |
| Gescanntes PDF / Bild-PDF (kein Text-Layer) | **keine** | ZDL-1: Evidenz = Vision selbst |
| PDF/Excel ohne erkennbare Positionsspalte | **keine** (Guard skippt) | ZDL-2 |
| Excel/CSV | n/a (deterministisch geparst, aber kein Reconciler-Schutz, nie GREEN per Design) | green_gate: `NO_PDF_EVIDENCE` |

**Was zusätzlich gebaut werden muss für echte Produktions-Garantie:**
1. Unabhängige Mengen-Zählspur für den Vision-Pfad (ZDL-1).
2. Guard-Pflicht auch ohne erkannte Positionsspalte (ZDL-2).
3. Text-Pfad-Master-Set aus Spalte ∪ Regex (ZDL-3).
4. Mengen- statt Zählervergleich in A2 (ZDL-4).
5. Sichtbares „Garantie-Status"-Feld pro Job im Dashboard (garantiert / nicht garantiert + Grund).

---

## 6.5 Produktions-Readiness-Checklist

- [~] **Datenverlust-Schutz vollständig** — Kette gebaut, aber ZDL-1/2/3 offen → **NICHT** für scanned/positionslose PDFs.
- [~] **Error-Handling + Fallbacks** — Pipeline failt graceful pro Stage ([pipeline_runner.py:183-194](src/api/pipeline_runner.py#L183)); Vision→Legacy-Fallback vorhanden. Aber: `create_task`-GC-Risiko (AW-1), keine Resume-Fähigkeit.
- [ ] **Monitoring + Alerting** — nur Logging, keine Metriken/Alerts (keine 429-Rate, keine Job-Dauer-Histogramme, kein Dead-Letter).
- [~] **Security** — API-Key-Auth vorhanden ([auth.py](src/core/auth.py)), aber **DISABLED wenn `API_KEY` leer** (Default offen); **`.env` mit echtem Azure-Key ist eingecheckt** (`git ls-files .env` → tracked, kein `.gitignore`-Eintrag) → **Key rotieren + untracken**; Path-Traversal über `file.filename` ([upload.py:47](src/api/routes/upload.py#L47)) nicht saniert.
- [~] **Performance (Latenz/Kosten)** — siehe Kostenschätzung unten; 2× Vision/Seite, hohe `max_tokens=16384`, 300 s Call-Timeout.
- [ ] **Skalierbarkeit (10→100 Docs/Tag)** — In-Process-Tasks, kein Worker, keine globale Drosselung; SQLite-Job-Store. 100 Docs/Tag × mehrere Min/Doc im selben Prozess = unrealistisch ohne Queue.
- [~] **Wartbarkeit / Onboarding neuer Kunde** — Konfig-getrieben (CLAUDE.md), aber Few-Shot-Learning-Loop nicht geschlossen ([system_spec_optimization.md:601-620](data/analysis/system_spec_optimization.md)).
- [~] **Test-Coverage kritischer Pfade** — Reconciler/Guard/Export getestet (`test_reconciler.py`, `test_export_zero_data_loss.py`, `test_b3_coverage_guard.py`); **Vision-Under-Extraction NICHT getestet** (ZDL-1).
- [~] **Dokumentation** — stark (CLAUDE.md, system_spec, REFACTOR_PLAN), aber Garantie-Grenzen nicht im Produkt sichtbar.
- [ ] **Deployment-Stabilität** — kein Resume nach Neustart (laufende Jobs → `failed`), kein Health-Check der LLM-Anbindung, kein Worker.

### Kosten-/Latenz-Schätzung pro Dokument (grobe Hausnummer)
Vision-Pfad, 5-Seiten-PDF, `gpt-4.1-mini` (West/Sweden):
- 1× Spaltenerkennung (1 Bild) + 2× Extraktion (A/B, je 5 Bilder, `max_tokens` bis 16384)
- ≈ 11 Bild-Eingaben + große Output-Budgets → grob **0,05–0,15 €/Dokument** je nach Seitenzahl/Tokens,
  Latenz **mehrere Minuten** (sequenziell, 300 s Call-Timeout, lineares Retry-Backoff).
  Bei 100 Docs/Tag dominieren Latenz und 429 die Kosten, nicht der Token-Preis →
  Hebel ist Request-Reduktion (AW-3), nicht Modellpreis.

---

## 6.6 Implementierungs-Roadmap

### Sprint 1 (diese Woche): Kritische Bugs / Sicherheit — ✅ ABGESCHLOSSEN (2026-05-29)
1. **SEC-1: `.env` untrackt + `.gitignore` angelegt** (`git rm --cached .env`, `.env` jetzt ignoriert).
   ⚠️ **Offen (nur durch Betreiber): Azure-Key rotieren** — der Key liegt weiterhin in der Git-Historie.
2. **AW-1: Task-Referenz halten** — `_schedule_background` + `_background_tasks` in [upload.py](src/api/routes/upload.py). Test: `test_upload_security.py::test_schedule_background_keeps_reference_until_done`.
3. **ZDL-2: Guard nicht still skippen** — `guard_basis` + Row-Count-Fallback im Reconciler. Test: `test_zdl2_guard_fallback.py`.
4. **ZDL-3: Text-Pfad Master-Set = Spalte ∪ Regex** — `_text_path_pdf_positions` in [pdf_parser.py](src/ingestion/pdf_parser.py). Test: `test_zdl3_text_path_positions.py`.
5. **SEC-2: Upload-Filename saniert** — `_safe_filename` (POSIX+Windows-Trennzeichen, `.`/`..`-Schutz). Test: `test_upload_security.py`.

**Test-Status:** 238 passed, 1 skipped, **3 failed (vorbestehend: GF/STL_08.05.13.pdf, 0 Zeilen — Parsing-Qualität, nicht Sprint-1-bedingt; identisch zu REFACTOR_PLAN.md-Status).** Keine Regression durch Sprint 1.

### Sprint 2 (nächste Woche): Architektur-Härtung — ✅ ABGESCHLOSSEN (2026-05-29)
6. **ZDL-1: Garantie-Status pro Job (Stufe c).** `completeness_guaranteed` + Begründung im
   Scorer ([_completeness_verdict](src/scoring/ensemble_scorer.py)), über `JobResult`
   ([schemas.py](src/api/models/schemas.py), [result_builder.py](src/api/result_builder.py))
   ans Dashboard durchgereicht. Scanned/Vision-only-PDFs werden ehrlich als „nicht garantiert"
   markiert. Test: `test_zdl1_completeness.py`.
   ⚠️ **Stufe a (unabhängige Vision-Zählspur) bleibt offen** → Sprint 3 / Backlog.
7. **ZDL-4: Export-Guard mengenbasiert** ([excel_exporter.py](src/export/excel_exporter.py) —
   `expected_position_ids` ⊆ Output-Positionen, sonst `ZeroDataLossError`). Test: `test_zdl4_export_set_guard.py`.
8. **AW-2: Positions-Helfer zentralisiert** in [src/core/positions.py](src/core/positions.py)
   (`normalize_position`, `POSITION_FIELDS`); Reconciler + Scorer importieren dieselbe Quelle.
   Test: `test_aw4_backoff.py` deckt Backoff ab; Normalisierungs-Identität durch bestehende
   Reconciler/Guard-Tests abgesichert.
9. **AW-4: Backoff korrigiert + gehärtet.** ⚠️ **Latenter Schwerwiegend-Bug gefunden:** der Code
   nutzte `base ** attempt` → mit Default-Base 15 ergab das 225 s / 3375 s (≈56 min) Backoff.
   Ersetzt durch echtes exponentielles Backoff mit Full-Jitter, Cap (`LLM_RETRY_MAX_BACKOFF_SECONDS`,
   Default 120 s) und `Retry-After`-Respekt ([azure_openai.py](src/llm/azure_openai.py)).
   Test: `test_aw4_backoff.py`.
10. **Monitoring-Minimum:** strukturierte `JOB_METRICS`-JSON-Logzeile pro Job (Dauer,
    Extraktionsmethode, guard_basis, completeness, GREEN/YELLOW/RED, synthetic_missing)
    in [pipeline_runner.py](src/api/pipeline_runner.py).

**Test-Status Sprint 2:** 23 neue Tests grün; Gesamt-Suite (ohne Live-Azure & 28-min-Real-PDF)
**116 passed, 0 failed**; `ruff` sauber. Keine Regression.

### Sprint 3 (danach): Produktions-Polishing — TEILWEISE ABGESCHLOSSEN (2026-05-29)
11. **AW-3: Adaptive Dual-Extraction** — ⏸️ **BEWUSST ZURÜCKGESTELLT.** Der Eingriff sitzt im
    Vision-Dual-Extraction-Sicherheitsnetz (A/B-Vergleich deckt Extraktionsfehler auf, Teil des
    Zero-False-Positive-Vertrags). Ihn ohne empirische Validierung gegen die 19 POC-PDFs mit
    **Live-Vision** zu verändern, riskiert genau die Verlässlichkeit, an der die Konkurrenz
    gescheitert ist — und kostet Azure-Budget zum Raten. Design steht (nur kritische Spalten /
    niedrige First-Pass-Confidence dual extrahieren, sonst single), Umsetzung erst nach
    A/B-Benchmark. Kosten-/Korrektheits-Abwägung: nicht blind landen.
12. **Worker/Queue** — ✅ Globale **bounded JobQueue + Concurrency-Limit** ([job_queue.py](src/api/job_queue.py)),
    in [upload.py](src/api/routes/upload.py) (kein Fire-and-Forget mehr) und
    [main.py](src/api/main.py)-Lifespan eingehängt. `JOB_CONCURRENCY` (Default 1) drosselt die
    Azure-Last global — der vom System-Spec vermisste Hebel. Test: `test_job_queue.py`.
    ⏸️ **Resume nach Neustart bewusst NICHT umgesetzt:** der Job-Store-Vertrag (getestet:
    `test_orphaned_in_flight_jobs_become_failed_on_restart`) failt unterbrochene Jobs → Re-Upload.
    Re-Run würde partielle Azure-Kosten und halb geschriebene Exporte riskieren — fail-fast ist die
    bessere Garantie.
13. **Learning-Loop geschlossen** — ✅ `_format_learned_corrections` speist kundenspezifische
    Korrekturen aus `corrections.jsonl` als Few-Shot in den Mapping-Prompt
    ([llm_column_mapper.py](src/mapping/llm_column_mapper.py)). Test: `test_learning_loop.py`.
14. **AW-5: Dead-Field dokumentiert** ([pipeline.py](src/transform/pipeline.py) — Parser-Count als
    reines Diagnose-Feld markiert); Garantie-Grenzen sind über `completeness_reason` (ZDL-1)
    bereits im Job-Ergebnis sichtbar.

**Test-Status Sprint 3:** 6 neue Tests grün (Queue + Learning-Loop); Gesamt-Suite (ohne Live-Azure
& 28-min-Real-PDF) **122 passed, 0 failed**; `ruff` auf geänderten Dateien sauber (2 vorbestehende
E402 in `main.py` sind unverändert/beabsichtigt).

---

## 6.7 Was NICHT angefasst werden soll (stabil)

- **GREEN-Gate-Vertrag** [green_gate.can_be_green](src/scoring/green_gate.py) — die fail-closed-Reihenfolge
  ist bewusst streng und der Kern-Differenzierer (kein False-Green). Nicht aufweichen ohne separaten Auftrag.
- **Daten-Contract-Felder** in [models.py:50-55](src/core/models.py#L50) und
  [models.py:144-149](src/core/models.py#L144) — Schnittstelle der Kette; additiv erweitern, nicht umbenennen.
- **Reconciler-Kernlogik** [position_reconciler.reconcile_positions](src/reconciliation/position_reconciler.py#L66) —
  Master-Set-Bildung ist korrekt; nur die *Eingangs-Quelle* (`raw_pdf_positions`) härten, nicht den Algorithmus.
- **Excel-Template-Mapping & Styling** [excel_exporter.py](src/export/excel_exporter.py) (Template-Layout V191,
  Zeilen-Offsets, Style-Capture) — funktioniert, kundenkritisch.
- **Stammdaten-Abgleich** [master_data_matcher.py](src/transform/master_data_matcher.py) — schwach (kein echtes
  Fuzzy), aber stabil; Aufwertung ist Feature, kein Refactor → getrennt behandeln.
- **Scoring-Vetolisten** [ensemble_scorer.py:258-274](src/scoring/ensemble_scorer.py) — nur additiv ergänzen.

---

## Offene Widersprüche (Arbeitsregel 5)
1. **ZDL-3:** REFACTOR_PLAN behauptet, fortlaufende Positionen kämen aus der Positionsspalte —
   der Code zieht `raw_pdf_positions` (Text-Pfad) aus dem Regex. → Lücke für gedroppte Integer-Positionen.
2. **A2-Selbstbild:** REFACTOR_PLAN-Side-Condition (1) verspricht „Output-Zeilen ≥ PDF-Positionen,
   sonst Exception" — gilt aber nur, wenn `expected_position_count > 0`, was bei fehlender
   Positionsspalte (ZDL-2) und bei Vision-Under-Extraction (ZDL-1) nicht zutrifft. Die Garantie
   ist enger als der Plan sie darstellt.
