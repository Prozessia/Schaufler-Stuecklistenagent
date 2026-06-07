# CLAUDE.md — BOM-Mapper: KI-gestütztes Stücklisten-Mapping für Schaufler

## Projektübersicht

Dieses Projekt entwickelt ein **KI-gestütztes System** für SCHAUFLER Tooling, das beliebige Kunden-Stücklisten (BOM — Bill of Materials) — egal welches Format, welche Sprache, welche Struktur — in die interne Schaufler Excel-Vorlage überführt.

**Unternehmen:** SCHAUFLER Tooling GmbH & Co. KG (Werkzeugbau, Aluminium-Druckgussformen)
**Agentur:** Prozessia
**LLM-Backend:** Microsoft Azure OpenAI (GPT-4o) — DSGVO-konform, EU-Rechenzentrum

> **Kein Multi-Tenant-System.** Das Projekt wird pro Unternehmen als eigene Instanz gebaut und deployed. Für einen neuen Kunden wird eine neue Instanz aufgesetzt und konfiguriert. Es gibt keine Mandantenverwaltung, keine Tenant-IDs, kein dynamisches Umschalten.

### Kernproblem

Schaufler erhält Stücklisten von Kunden in **völlig unterschiedlichen Formaten**: PDF, Excel, CSV — auf Deutsch, Englisch, Französisch, Italienisch, Tschechisch, Chinesisch, etc. Jeder Kunde hat sein eigenes Schema, seine eigene Nomenklatur. Die manuelle Übertragung in die Schaufler-Vorlage dauert ca. 2h+ pro Stückliste.

### Format-Agnostischer Ansatz (KRITISCH)

**Das System darf NICHT auf die 19 Beispiel-Stücklisten im Testordner trainiert sein.**

Die Dateien in `data/input/PDF_POC/` (Audi, FCA, Ford, GF, Linamar, Ljunghaell, Magna, Mercedes, Scania, TCG, ZF) sind **nur Testdaten**. Das System muss mit **jedem Format** klarkommen — auch Formate die noch nie gesehen wurden.

Prinzipien:
- **Kein Hardcoding** von Kunden-Formaten, Spaltennamen oder Tabellenstrukturen
- **LLM-First für Mapping**: Das LLM erkennt semantisch, was jede Spalte bedeutet — unabhängig von Sprache oder Benennung
- **Robustes Parsing**: Mehrere Parsing-Strategien mit automatischem Fallback (Tabellen-Extraktion → Text-Parsing → Vision-Fallback)
- **Heuristiken statt Regeln**: Header-Erkennung, Datenbereich-Erkennung etc. müssen generisch funktionieren — nicht auf bestimmte Zeilen festgelegt
- **Neue Kunden = 0 Code-Änderungen**: Wenn morgen ein neuer Kunde ein PDF auf Japanisch schickt, muss das System es verarbeiten können

### DSGVO & Datenschutz

- **LLM:** Azure OpenAI GPT-4o in EU-Region (West Europe) — Daten verlassen die EU nicht
- **Azure Data Processing Agreement** deckt Art. 28 DSGVO ab
- **Opt-out** von Model-Training: Azure OpenAI nutzt Kundendaten NICHT für Modelltraining
- **Keine Drittanbieter-APIs** — alles über Azure-Infrastruktur
- Stücklisten-Daten werden nur für die Verarbeitung an Azure gesendet, nicht gespeichert

### Architektur-Überblick (5 Layer)

```
Layer 1: Ingestion & Parsing      → Dateien einlesen, Tabellen extrahieren (format-agnostisch)
Layer 2: Semantisches Mapping     → Quell-Spalten auf Zielschema mappen (LLM-basiert, jede Sprache)
Layer 3: Wert-Transformation      → Zellwerte normalisieren, Stammdaten abgleichen
Layer 4: Confidence-Scoring       → Dreifach-Validierung, Ampel-System (Grün/Gelb/Rot)
Layer 5: Review-Dashboard & Export → Mitarbeiter-UI, Excel-Export, Feedback-Loop
```

---

## Projektstruktur

```
bom-mapper/
├── CLAUDE.md                              # Diese Datei
├── config/                                # KONFIGURATION (pro Instanz anpassbar)
│   ├── app_config.yaml                    # Hauptkonfiguration (Schwellwerte, Pfade, etc.)
│   ├── target_schema.json                 # Zielschema als JSON (Schaufler-Spalten)
│   ├── target_template.xlsx               # Excel-Vorlage (CadCam_Stuecklistenvorlage V191)
│   └── master_data/                       # Stammdaten
│       ├── materials.json                 # Werkstoff-Katalog
│       ├── units.json                     # Einheiten-Mapping
│       └── validation_rules.json          # Validierungsregeln
├── data/
│   ├── input/                             # Kunden-Stücklisten (Testdaten)
│   │   └── PDF_POC/                       # Beispiel-Stücklisten verschiedener Kunden
│   ├── analysis/                          # Ergebnisse der Datenanalyse (Phase 1)
│   │   └── data_analysis_report.md
│   ├── learned_mappings/                  # Gelernte Kunden-Schemata (wachsen mit der Zeit)
│   │   └── corrections.jsonl              # Gesammelte Korrekturen als Few-Shot-Quelle
│   └── test_outputs/                      # Testausgaben für Validierung
├── src/
│   ├── core/                              # KERN-MODULE
│   │   ├── __init__.py
│   │   ├── models.py                      # Pydantic-Datenmodelle
│   │   ├── schema_definition.py           # Pydantic-Modelle für Zielschema
│   │   └── interfaces.py                  # Abstrakte Interfaces (Parser, Mapper, Scorer)
│   ├── llm/                               # LLM-ABSTRAKTIONSSCHICHT
│   │   ├── __init__.py
│   │   ├── base.py                        # Abstraktes LLM-Interface
│   │   ├── azure_openai.py                # Azure OpenAI GPT-4o Implementation
│   │   ├── prompt_manager.py              # Prompt-Templates laden & rendern
│   │   └── token_tracker.py               # Token-Usage & Kosten tracken
│   ├── ingestion/                         # Layer 1: Parsing (FORMAT-AGNOSTISCH)
│   │   ├── __init__.py
│   │   ├── file_router.py                 # Dateityp-Erkennung & Routing
│   │   ├── excel_parser.py                # Excel/CSV Parsing (generisch)
│   │   ├── pdf_parser.py                  # PDF-Tabellen-Extraktion (multi-strategy)
│   │   └── structure_normalizer.py        # Einheitliches JSON-Format
│   ├── mapping/                           # Layer 2: Schema-Mapping (LLM-basiert)
│   │   ├── __init__.py
│   │   ├── schema_registry.py             # Zielschema aus Config laden
│   │   ├── llm_column_mapper.py           # LLM-basiertes Spalten-Mapping (jede Sprache)
│   │   └── mapping_validator.py           # Regelbasierte Mapping-Validierung
│   ├── transform/                         # Layer 3: Wert-Transformation
│   │   ├── __init__.py
│   │   ├── value_transformer.py           # Wert-Normalisierung & Einheiten
│   │   ├── master_data_matcher.py         # Stammdaten-Abgleich
│   │   └── cross_validator.py             # Plausibilitäts-Checks
│   ├── scoring/                           # Layer 4: Confidence
│   │   ├── __init__.py
│   │   ├── ensemble_scorer.py             # Dreifach-Scoring
│   │   ├── threshold_manager.py           # Ampel-Schwellwerte (konfigurierbar)
│   │   └── audit_trail.py                 # Entscheidungs-Dokumentation
│   ├── export/                            # Layer 5: Export
│   │   ├── __init__.py
│   │   ├── excel_exporter.py              # Template-basierter Export
│   │   └── feedback_store.py              # Korrekturen speichern
│   ├── api/                               # FastAPI Backend
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── routes/
│   │   │   ├── upload.py
│   │   │   ├── jobs.py
│   │   │   └── feedback.py
│   │   └── models/
│   └── config.py                          # Config laden aus config/app_config.yaml
├── frontend/                              # React Review-Dashboard
│   ├── src/
│   └── package.json
├── tests/
│   ├── test_ingestion/
│   ├── test_mapping/
│   ├── test_transform/
│   ├── test_scoring/
│   └── test_integration/
├── scripts/
│   ├── analyze_input_data.py              # Phase 1: Datenanalyse-Skript
│   └── run_benchmark.py                   # Benchmark gegen manuelle Ergebnisse
├── prompts/                               # LLM-Prompt-Templates
│   ├── column_mapping.txt
│   ├── value_normalization.txt
│   └── counter_check.txt
├── requirements.txt
├── docker-compose.yml
└── .env.example
```

### Konfiguration (config/app_config.yaml)

> Einzige Datei die pro Instanz angepasst wird. Alles andere ist Code.

```yaml
# config/app_config.yaml
company:
  name: "SCHAUFLER Tooling GmbH & Co. KG"
  industry: "Werkzeugbau / Aluminium-Druckguss"

target:
  schema_file: "config/target_schema.json"
  template_file: "config/target_template.xlsx"
  template_sheet: "Stückliste"          # Sheet-Name in der Excel-Vorlage
  header_row: 5                          # Zeile der Spaltenüberschriften
  data_start_row: 7                      # Erste Datenzeile

scoring:
  green_threshold: 0.90
  yellow_threshold: 0.50
  enable_counter_check: true
  conservative_mode: true                # Lieber mehr Gelb als falsches Grün

master_data:
  materials_file: "config/master_data/materials.json"
  units_file: "config/master_data/units.json"
  validation_rules_file: "config/master_data/validation_rules.json"

domain_context: |
  Werkzeugbauunternehmen das Aluminium-Druckgussformen fertigt.
  Typische Stücklisten-Inhalte: Formplatten, Schieber, Kerne, Einsätze,
  Normalien, Heißkanäle. Materialien: Warmarbeitsstähle (1.2343, 1.2344),
  Aluminium (AlSi9Cu3), Kupferlegierungen, Beryllium-Kupfer.
```

### Zielschema-Definition (config/target_schema.json)

```json
{
  "schema_version": "1.0",
  "fields": [
    {
      "name": "Position",
      "column_in_template": "A",
      "type": "integer",
      "required": true,
      "description": "Laufende Positionsnummer",
      "validation": {"min": 1, "max": 9999}
    },
    {
      "name": "Artikelnummer",
      "column_in_template": "B",
      "type": "string",
      "required": true,
      "description": "Eindeutige Artikelnummer des Bauteils",
      "validation": {"pattern": "^[A-Z0-9\\-]+$"}
    },
    {
      "name": "Benennung",
      "column_in_template": "C",
      "type": "string",
      "required": true,
      "description": "Bezeichnung/Name des Bauteils",
      "aliases": ["Bezeichnung", "Benennung", "Description", "Désignation", "名称"]
    },
    {
      "name": "Werkstoff",
      "column_in_template": "D",
      "type": "string",
      "required": false,
      "description": "Material/Werkstoff des Bauteils",
      "aliases": ["Material", "Werkstoff", "Matériau", "材料"],
      "master_data_lookup": "materials"
    }
  ]
}
```

### Neue Instanz für ein anderes Unternehmen

Für ein neues Unternehmen wird eine **komplett neue Instanz** deployed:
1. Repository klonen
2. `config/app_config.yaml` anpassen (Firmenname, Branche, Domain-Kontext)
3. `config/target_template.xlsx` durch die Excel-Vorlage des neuen Unternehmens ersetzen
4. `config/target_schema.json` an die neue Vorlage anpassen
5. `config/master_data/` mit firmenspezifischen Stammdaten füllen
6. Deployen — fertig

---

## Entwicklungsphasen

### PHASE 1: Datenanalyse (ZUERST — vor jeder Implementierung)

**Ziel:** Alle Stücklisten im `data/input/` Ordner systematisch analysieren, um die tatsächliche Varianz, Komplexität und Herausforderungen zu verstehen.

**Vorgehen:**
1. Ordnerstruktur inventarisieren: Wie viele Kunden? Wie viele Dateien pro Kunde? Welche Formate?
2. Jede Datei öffnen und analysieren:
   - Dateiformat (PDF, XLS, XLSX, CSV, andere?)
   - Sprache(n) der Spaltenüberschriften
   - Anzahl Spalten und Zeilen
   - Spaltenbezeichnungen (exakt wie in der Datei)
   - Beispielwerte (erste 3 Zeilen pro Spalte)
   - Besonderheiten (Merged Cells, Multi-Header, Fußnoten, eingebettete Bilder, etc.)
3. Zielvorlage analysieren:
   - Alle Spalten der Schaufler-Vorlage mit Datentyp und Beispielwerten dokumentieren
   - Pflichtfelder vs. optionale Felder identifizieren
   - Validierungsregeln ableiten (z.B. Wertebereich, Format-Pattern)
4. Mapping-Matrix erstellen:
   - Tabelle: Quell-Spalte (pro Kunde) → Ziel-Spalte (Schaufler)
   - Schwierigkeit pro Mapping bewerten (direkt, semantisch, transformation nötig, unmöglich)
5. Report erstellen in `data/analysis/data_analysis_report.md`

**Analyse-Report soll enthalten:**
```markdown
# Datenanalyse-Report

## 1. Überblick
- Anzahl Kunden: X
- Anzahl Dateien gesamt: X
- Formate: PDF (X), Excel (X), CSV (X), Sonstige (X)
- Sprachen: DE (X), EN (X), FR (X), ...

## 2. Zielschema (Schaufler-Vorlage)
- [Tabelle mit allen Spalten, Datentyp, Pflicht/Optional, Beispielwerte]

## 3. Analyse pro Kunde
### Kunde A
- Dateien: [Liste]
- Format(e): ...
- Sprache: ...
- Spalten: [exakte Bezeichnungen]
- Beispieldaten: [erste 3 Zeilen]
- Mapping-Schwierigkeit: [Bewertung]
- Besonderheiten: [Merged Cells, Multi-Header, etc.]

### Kunde B
- ...

## 4. Mapping-Matrix
| Schaufler-Feld | Kunde A | Kunde B | Kunde C | ... |
|---|---|---|---|---|
| Artikelnummer | Part No. | Numéro | 品番 | ... |
| Werkstoff | Material | Matériau | - | ... |

## 5. Identifizierte Herausforderungen
- [Liste der konkreten technischen Herausforderungen]

## 6. Empfehlung für Parsing-Strategie
- [Pro Format: welcher Parser, welche Fallbacks]

## 7. Geschätzte Mapping-Schwierigkeit
- Direkt mappbar: X%
- Semantisches Mapping nötig: X%
- Transformation nötig: X%
- Nicht automatisch mappbar: X%
```

---

### PHASE 2: Ingestion & Parsing (Layer 1)

**Ziel:** Robuster Parser, der jede Stückliste aus dem Ordner in ein einheitliches JSON-Format bringt.

**Reihenfolge:**
1. `file_router.py` — Dateityp-Erkennung (python-magic + Extension)
2. `excel_parser.py` — pandas-basiert, mit Handling für:
   - Multi-Header (Header in Zeile 1, 2, oder 3)
   - Merged Cells
   - Mehrere Sheets
   - Leere Zeilen/Spalten am Rand
3. `pdf_parser.py` — Zweistufig:
   - Stufe 1: Camelot (lattice + stream) oder Tabula
   - Stufe 2 (Fallback): Azure OpenAI GPT-4o Vision — Seite als Bild, Tabelle extrahieren
   - Für jede PDF: zuerst prüfen ob Text-PDF oder Scan (OCR nötig?)
4. `structure_normalizer.py` — Output-Format:

```python
{
    "source": {
        "filename": "kunde_a_stueckliste.pdf",
        "customer": "Kunde A",
        "format": "pdf",
        "language_detected": "de",
        "pages": 3
    },
    "headers": ["Pos.", "Artikelnummer", "Benennung", "Werkstoff", ...],
    "rows": [
        {"Pos.": "1", "Artikelnummer": "A-1234", "Benennung": "Formplatte", ...},
        ...
    ],
    "metadata": {
        "total_rows": 47,
        "total_columns": 12,
        "extraction_method": "pymupdf_table",
        "extraction_confidence": 0.92
    }
}
```

**Validierung Phase 2:**
- Jede Datei aus `data/input/` durch den Parser laufen lassen
- Output in `data/test_outputs/` speichern
- Manuell prüfen: Stimmt die Extraktion? Fehlen Daten? Sind Spalten verrutscht?
- Parsing-Erfolgsrate pro Format dokumentieren

---

### PHASE 3: Schema-Mapping (Layer 2)

**Ziel:** LLM-basiertes Spalten-Mapping von Quell-Schema auf Schaufler-Vorlage.

**Reihenfolge:**
1. `schema_registry.py`:
   - Zielschema aus `config/target_schema.json` laden
   - Gelernte Mappings aus `data/learned_mappings/` laden (falls vorhanden)
   - Versionierung: Schema-Änderungen nachvollziehbar

2. `llm_column_mapper.py`:
   - Input: Quell-Spalten + 3-5 Beispielwerte + Zielschema + optional gespeichertes Mapping
   - Output: `{quell_spalte: {ziel_spalte: str, confidence: float, reasoning: str}}`
   - Prompt-Design (in `prompts/column_mapping.txt`):
     - System-Prompt mit Kontext (aus `app_config.yaml → domain_context`)
     - Zielschema als Referenz (aus `target_schema.json`)
     - Few-Shot-Beispiele aus bisherigen erfolgreichen Mappings
     - Strukturierter JSON-Output erzwingen (Azure OpenAI JSON mode)
   - GPT-4o für Mapping (gutes Preis-/Leistungsverhältnis + starke Multilingual-Performance)
   - Bei niedrigem Confidence: zweiten Call mit mehr Kontext

3. `mapping_validator.py`:
   - Pflichtfelder abgedeckt?
   - Keine doppelten Zuordnungen?
   - Datentyp-Kompatibilität (Zahl → Zahl, Text → Text)
   - Warnung wenn Quell-Spalte keinem Zielfeld zugeordnet

**Validierung Phase 3:**
- Mapping für jede Stückliste generieren
- Manuell vergleichen mit der Mapping-Matrix aus Phase 1
- Mapping-Genauigkeit berechnen (korrekt / total)

---

### PHASE 4: Wert-Transformation (Layer 3)

**Ziel:** Zellwerte normalisieren und gegen Stammdaten abgleichen.

**Reihenfolge:**
1. `value_transformer.py`:
   - Maßeinheiten: inch → mm, lbs → kg (pint library)
   - Materialbezeichnungen: Mapping-Tabelle + LLM-Fallback
   - Teilenummern: Format-Normalisierung
   - Textbereinigung: Whitespace, Sonderzeichen, Encoding

2. `master_data_matcher.py`:
   - Stammdaten aus `config/master_data/` laden
   - Materialbezeichnungen, Werkstoffnummern gegen Stammdaten abgleichen
   - Exakter Match → Confidence 1.0
   - Fuzzy Match (Levenshtein < 3) → Confidence 0.7-0.9
   - Embedding-basierte Suche → Confidence 0.5-0.8
   - Kein Match → Confidence 0.0, Quellwert durchreichen

3. `cross_validator.py`:
   - Gewicht plausibel für Material + Dimension?
   - Duplikate in Artikelnummern?
   - Reihenfolge/Hierarchie konsistent?

---

### PHASE 5: Confidence-Scoring (Layer 4)

**Ziel:** Verlässliche Grün/Gelb/Rot-Einordnung durch Ensemble-Scoring.

**Das ist der entscheidende Differenzierungsfaktor gegenüber der Konkurrenz!**

1. `ensemble_scorer.py`:
   - Signal 1: LLM-Confidence aus Mapping (0.0-1.0)
   - Signal 2: Regelbasierte Validierung (0.0-1.0)
     - Datentyp korrekt? (+0.2)
     - Wertebereich plausibel? (+0.2)
     - Format-Pattern passt? (+0.2)
     - Stammdaten-Match? (+0.2)
     - Einheiten konsistent? (+0.2)
   - Signal 3: Counter-Check LLM (optional, für gelbe Fälle)
     - Zweiter GPT-4o-mini-Call mit anderer Prompt-Formulierung
     - Stimmt das Ergebnis überein? → Confidence hoch
   - Gewichtung: `final = 0.4 * llm + 0.4 * rules + 0.2 * counter`

2. `threshold_manager.py`:
   - Schwellwerte aus `config/app_config.yaml` laden
   - Grün: final >= green_threshold (default 0.90) → automatisch übernommen
   - Gelb: yellow_threshold <= final < green_threshold → Vorschlag, Review nötig
   - Rot: final < yellow_threshold (default 0.50) → manuell, Quellwert anzeigen
   - Schwellwerte konfigurierbar

3. `audit_trail.py`:
   - Pro Zelle: alle drei Scores, angewandte Regeln, Reasoning
   - Exportierbar als JSON für Nachvollziehbarkeit

---

### PHASE 6: Export & Feedback (Layer 5)

1. `excel_exporter.py`:
   - Vorlage aus `config/target_template.xlsx` laden
   - Werte in die richtigen Zellen schreiben (Mapping aus `target_schema.json`)
   - Formatierung beibehalten
   - Optional: Farbmarkierung (Grün/Gelb/Rot) in separater Spalte
   - Optional: Audit-Sheet als zweites Tab

2. `feedback_store.py`:
   - Korrekturen in `data/learned_mappings/corrections.jsonl` speichern
   - Format: `{quell_wert, korrigierter_wert, quell_kunde, spalte, timestamp}`
   - Als Few-Shot-Beispiele bei nächstem Mapping desselben Kunden nutzen
   - Statistik: Grün/Gelb/Rot-Verteilung über Zeit tracken

---

### PHASE 7: API & Frontend

1. FastAPI Backend:
   - `POST /upload` — Stückliste hochladen
   - `GET /jobs/{id}` — Status abfragen
   - `GET /jobs/{id}/result` — Ergebnis mit Ampel-Bewertung
   - `POST /jobs/{id}/feedback` — Korrekturen einreichen
   - `GET /jobs/{id}/export` — Excel-Download

2. React Frontend (Review-Dashboard):
   - Upload-Bereich (Drag & Drop)
   - Ergebnis-Tabelle mit Ampel-Farben
   - Grüne Zeilen collapsed, Gelb/Rot expanded
   - Inline-Edit für Korrekturen
   - Export-Button
   - Statistik-Anzeige (Grün/Gelb/Rot-Verteilung)

---

## Technische Richtlinien

### Python-Standards
- Python 3.12+
- Type Hints überall
- Pydantic für Datenmodelle
- pytest für Tests
- Ruff für Linting/Formatting
- Async wo sinnvoll (API, LLM-Calls)

### LLM-Usage (Azure OpenAI — DSGVO-konform)
- **GPT-4o** (via Azure OpenAI, Region: West Europe) für Mapping und Transformation
- **GPT-4o-mini** (via Azure OpenAI) für Counter-Checks und einfache Validierungen (Kosten)
- **GPT-4o mit Vision** für PDF-Fallback (Tabellen aus Bildern extrahieren)
- Alle Calls über `src/llm/azure_openai.py` — abstrahiert hinter Interface `src/llm/base.py`
- Structured Output (JSON mode / response_format) erzwingen
- Retry mit Exponential Backoff
- Token-Usage tracken und loggen (pro Job)
- Prompts in separaten Template-Dateien, nicht hardcoded
- **LLM-Provider ist austauschbar** — durch das Interface kann später auf andere Provider gewechselt werden

### LLM-Abstraktionsschicht

```python
# src/llm/base.py — Abstraktes Interface
from abc import ABC, abstractmethod
from pydantic import BaseModel

class LLMResponse(BaseModel):
    content: str
    tokens_input: int
    tokens_output: int
    model: str
    latency_ms: float

class BaseLLM(ABC):
    @abstractmethod
    async def complete(self, system: str, user: str, json_mode: bool = False) -> LLMResponse:
        ...

    @abstractmethod
    async def complete_with_image(self, system: str, user: str, image_b64: str) -> LLMResponse:
        ...

# src/llm/azure_openai.py — Azure OpenAI Implementation
from openai import AsyncAzureOpenAI

class AzureOpenAILLM(BaseLLM):
    def __init__(self, config):
        self.client = AsyncAzureOpenAI(
            azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
            api_key=config.AZURE_OPENAI_KEY,
            api_version="2024-10-21"
        )
        self.model_main = "gpt-4o"           # Deployment-Name in Azure
        self.model_mini = "gpt-4o-mini"      # Für Counter-Checks
```

### Error Handling
- Jeder Layer muss graceful failen
- PDF-Parsing fehlgeschlagen → GPT-4o Vision-Fallback → Manual-Flag
- LLM-Timeout → Retry → Default zu "Rot"
- Nie silent failen — alles loggen

### Konfiguration (.env)
```
# Azure OpenAI (DSGVO-konform, EU-Region)
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
AZURE_OPENAI_KEY=...
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_DEPLOYMENT_MAIN=gpt-4o
AZURE_OPENAI_DEPLOYMENT_MINI=gpt-4o-mini

# Datenbank
DATABASE_URL=postgresql://...
REDIS_URL=redis://...

# Allgemein
LOG_LEVEL=INFO
MAX_LLM_RETRIES=3
```

---

## Wichtige Kontext-Informationen

### Über Schaufler
- Mittelständisches Werkzeugbauunternehmen
- Fertigt Aluminium-Druckgussformen
- Kunden liefern eigene Stücklisten in eigenen Formaten
- Mitarbeiter übertragen aktuell alles manuell
- Das Thema ist "recht hoch aufgehangen" im Unternehmen
- 4 Mitbewerber sind bereits gescheitert — Hauptgrund: Ergebnisse nicht verlässlich genug

### Was die Konkurrenz falsch gemacht hat
1. Ein Anbieter hat behauptet, mit 8 Beispielen die komplette Varianz abzudecken → unseriös
2. Ergebnisse waren "augenscheinlich inhaltlich falsch"
3. Kein Confidence-System → Mitarbeiter musste ALLES nachprüfen → mehr Arbeit als vorher
4. Cherry-Picking bei der Datenbasis (nur einfache Excel-Daten statt echte PDFs)

### Was der Kunde erwartet
- Ehrliche, realistische Einschätzung
- Selbst 50% No-Touch wäre ein Gewinn — WENN man sich auf die 50% verlassen kann
- Human-in-the-Loop ist akzeptiert und erwartet
- Kein "wir lösen alles"-Versprechen

### Was NICHT im Scope ist
- CAD-Daten analysieren oder mit Stücklisten abgleichen
- Fehler in den Quell-Stücklisten erkennen (Dimensionen vs. Zeichnung)
- 100% automatisierte Verarbeitung ohne Human Review

---

## Befehle

```bash
# Abhängigkeiten installieren
pip install -r requirements.txt

# Phase 1: Datenanalyse
python scripts/analyze_input_data.py --input-dir data/input/ --output data/analysis/

# Tests ausführen
pytest tests/ -v

# API starten
uvicorn src.api.main:app --reload

# Benchmark
python scripts/run_benchmark.py --input-dir data/input/ --expected data/analysis/mapping_matrix.json
```
