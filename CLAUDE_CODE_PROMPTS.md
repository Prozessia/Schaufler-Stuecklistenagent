# Claude Code — Startanweisungen

## Schritt 1: Projekt initialisieren

Erstelle ein neues Verzeichnis, kopiere die CLAUDE.md rein, und lege den Stücklisten-Ordner unter `data/input/` ab:

```bash
mkdir schaufler-bom-mapper
cd schaufler-bom-mapper
# CLAUDE.md hierhin kopieren
# Stücklisten-Ordner nach data/input/ kopieren
# Schaufler-Vorlage nach config/target_template.xlsx kopieren
```

## Schritt 2: Erster Prompt an Claude Code

Kopiere diesen Prompt als erste Nachricht in Claude Code:

---

Lies bitte zuerst die CLAUDE.md Datei im Projektroot. Dann starte mit PHASE 1 — Datenanalyse.

Analysiere den kompletten Ordner `data/input/` systematisch. Gehe dabei folgendermaßen vor:

1. **Inventar erstellen**: Liste alle Dateien auf, gruppiert nach Kunde/Unterordner. Zeige Dateiname, Format, Dateigröße.

2. **Zielschema analysieren**: Öffne die Schaufler-Vorlage und dokumentiere jede Spalte mit Bezeichnung, Datentyp, Beispielwerten und ob Pflicht/Optional.

3. **Jede Stückliste einzeln analysieren**: Öffne jede Datei und extrahiere:
   - Sprache der Spaltenüberschriften
   - Exakte Spaltenbezeichnungen
   - Erste 3 Zeilen als Beispielwerte
   - Besonderheiten (Merged Cells, Multi-Header, Bilder, Fußnoten)
   - Bei PDFs: Text-PDF oder Scan? Tabellen-Qualität?

4. **Mapping-Matrix**: Erstelle eine Tabelle die zeigt, welche Quell-Spalte (pro Kunde) auf welche Ziel-Spalte (Schaufler) gemappt werden sollte.

5. **Schwierigkeits-Bewertung**: Bewerte pro Kunde/Datei die Mapping-Schwierigkeit (einfach/mittel/schwer) mit Begründung.

6. **Parsing-Strategie empfehlen**: Welcher Parser für welches Format? Wo brauchen wir Vision-Fallback?

Schreibe den vollständigen Report nach `data/analysis/data_analysis_report.md`.

Wichtig: Nimm dir Zeit. Öffne JEDE Datei. Überspringe nichts. Die Qualität dieser Analyse bestimmt die Qualität des gesamten Projekts.

---

## Schritt 3: Nach der Analyse — Phase 2 starten

Nachdem Claude Code den Analyse-Report erstellt hat, prüfe ihn kurz und starte dann Phase 2:

---

Gut, die Analyse ist abgeschlossen. Lies den Report in `data/analysis/data_analysis_report.md` und starte jetzt mit PHASE 2 — Ingestion & Parsing.

Basierend auf den Erkenntnissen aus der Analyse:

1. Erstelle die Projektstruktur wie in CLAUDE.md definiert
2. Implementiere `requirements.txt` mit allen nötigen Dependencies
3. Implementiere Layer 1 in dieser Reihenfolge:
   a. `src/ingestion/file_router.py`
   b. `src/ingestion/excel_parser.py`
   c. `src/ingestion/pdf_parser.py`
   d. `src/ingestion/structure_normalizer.py`
4. Schreibe Tests in `tests/test_ingestion/` die JEDE Datei aus `data/input/` als Testcase verwenden
5. Lasse alle Tests laufen und finde die Parsing-Erfolgsrate pro Format

Wenn ein Parser bei einer bestimmten Datei versagt, implementiere einen Fallback. Ziel: 100% der Dateien müssen erfolgreich geparst werden — notfalls mit Vision-Fallback.

---

## Schritt 4: Phase 3 — Mapping

---

Parsing funktioniert. Jetzt PHASE 3 — Schema-Mapping.

1. Implementiere `src/mapping/schema_registry.py` — lade das Zielschema aus `config/target_schema.json`
2. Erstelle den Prompt-Template in `prompts/column_mapping.txt` — optimiert für Werkzeugbau-Kontext, mehrsprachig, mit Few-Shot-Beispielen aus der Mapping-Matrix
3. Implementiere `src/mapping/llm_column_mapper.py` — Azure OpenAI API Call mit strukturiertem JSON-Output
4. Implementiere `src/mapping/mapping_validator.py` — regelbasierte Prüfung
5. Teste gegen JEDE Stückliste und vergleiche mit der manuellen Mapping-Matrix
6. Berechne die Mapping-Genauigkeit: Wie viele Spalten werden korrekt zugeordnet?

Optimiere den Prompt iterativ bis die Genauigkeit > 90% ist.

---

## Schritt 5: Phase 4 — Transformation

---

Mapping funktioniert. Jetzt PHASE 4 — Wert-Transformation.

1. Implementiere `src/transform/value_transformer.py` — Einheiten, Materialien, Formate
2. Implementiere `src/transform/master_data_matcher.py` — Fuzzy-Matching gegen Schaufler-Stammdaten
3. Implementiere `src/transform/cross_validator.py` — Plausibilitäts-Checks
4. Teste mit realen Daten: Wie viele Werte werden korrekt transformiert?

Fokus auf die häufigsten Transformationen zuerst (aus der Analyse bekannt).

---

## Schritt 6: Phase 5 — Confidence-Scoring

---

Transformation funktioniert. Jetzt PHASE 5 — das Herzstück: Confidence-Scoring.

Das ist der entscheidende Differenzierungsfaktor. Die Konkurrenz ist hieran gescheitert.

1. Implementiere `src/scoring/ensemble_scorer.py` mit den drei Signalen
2. Implementiere `src/scoring/threshold_manager.py` mit konfigurierbaren Schwellwerten
3. Implementiere `src/scoring/audit_trail.py`
4. Teste: Lasse ALLE Stücklisten durchlaufen und zeige die Grün/Gelb/Rot-Verteilung
5. Manuell prüfen: Sind die grünen Zeilen WIRKLICH korrekt? Das ist der wichtigste Test!
6. Wenn grüne Zeilen Fehler enthalten → Schwellwerte anpassen oder Scoring-Logik verbessern

Ziel: 0% Fehler bei grünen Zeilen. Lieber konservativer scoren und mehr gelb/rot haben als falsche grüne.

---

## Schritt 7: Phase 6 — Export

---

Scoring funktioniert. Jetzt PHASE 6 — Export & Feedback.

1. Implementiere `src/export/excel_exporter.py` — Template-basiert mit openpyxl
2. Implementiere `src/export/feedback_store.py`
3. Teste: Generiere für JEDE Stückliste die Schaufler-Excel und prüfe:
   - Stimmt das Format?
   - Sind die Werte in den richtigen Zellen?
   - Funktionieren eventuelle Formeln in der Vorlage noch?

---

## Schritt 8: Phase 7 — API & Frontend

---

Backend-Logik ist komplett. Jetzt PHASE 7 — API und Frontend.

1. Implementiere die FastAPI-Routen wie in CLAUDE.md definiert
2. Implementiere das React Review-Dashboard:
   - Drag & Drop Upload
   - Ergebnis-Tabelle mit Ampel-Farben
   - Grüne Zeilen collapsed, Gelb/Rot expanded
   - Inline-Edit für Korrekturen
   - Export-Button
   - Statistik (Grün/Gelb/Rot-Verteilung)
3. Erstelle docker-compose.yml für das Gesamtsystem



## Tipps für die Arbeit mit Claude Code

- **Format-agnostisch denken.** Die 19 Testdateien sind nur Beispiele. Das System muss mit JEDEM Format klarkommen — auch Formate die noch nie gesehen wurden. Kein Hardcoding von Kunden-Formaten, Spaltennamen oder Tabellenstrukturen.
- **Ein Layer nach dem anderen.** Nicht vorgreifen. Jeder Layer muss funktionieren und getestet sein, bevor der nächste beginnt.
- **Daten zuerst.** Phase 1 (Analyse) ist die wichtigste Phase. Hier werden die Weichen gestellt.
- **Testen mit echten Daten.** Keine Dummy-Daten — immer die echten Schaufler-Stücklisten verwenden.
- **Prompts iterieren.** Der LLM-Prompt für das Mapping wird nicht beim ersten Mal perfekt sein. Iteriere basierend auf den Testergebnissen.
- **Konservativ scoren.** Lieber 40% grün mit 0% Fehlerrate als 70% grün mit 5% Fehlerrate. Die grünen Werte müssen verlässlich sein.
- **Kein Multi-Tenant.** Das System wird pro Unternehmen als eigene Instanz deployed. Keine Mandantenverwaltung, keine Tenant-IDs. Konfigurierbare Werte liegen in `config/app_config.yaml`.
