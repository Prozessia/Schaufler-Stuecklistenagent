# Backlog Status und Agenten-Kontext

Stand: 2026-06-04  
Zweck: Diese Datei ist der gemeinsame Arbeitsstand fuer KI-Agenten. `backlog.md` beschreibt, was zu tun ist. Diese Datei beschreibt, was bereits getan wurde, was gerade laeuft, was blockiert ist und welches Kontextwissen der naechste Agent braucht.

## Status-Legende

- `TODO`: Noch nicht begonnen.
- `IN_PROGRESS`: Ein Agent arbeitet aktiv daran.
- `DONE`: Implementiert, validiert und dokumentiert.
- `BLOCKED`: Nicht fortsetzbar, weil Info, Entscheidung, Secret, Infrastruktur oder Fachreview fehlt.
- `SKIPPED`: Bewusst nicht umgesetzt; Entscheidung und Begruendung muessen dokumentiert sein.

## Agenten-Arbeitsprotokoll

Vor jedem Ticket:

1. Dieses Dokument lesen.
2. Passendes Ticket in `backlog.md` lesen.
3. Status in der Tabelle unten von `TODO` auf `IN_PROGRESS` setzen.
4. Im Abschnitt `Agent Context Log` eine Startnotiz mit Ticket-ID, Ziel und Annahmen eintragen.

Nach jedem Ticket:

1. Status auf `DONE`, `BLOCKED` oder `SKIPPED` setzen.
2. In der Tabelle `Last Update`, `Evidence` und `Notes` aktualisieren.
3. Im `Agent Context Log` zusammenfassen:
   - geaenderte Dateien
   - wichtige Entscheidungen
   - ausgefuehrte Validierung und Ergebnis
   - offene Risiken oder Folgearbeiten
4. Wenn ein Ticket neue dauerhafte Repo-Konventionen erzeugt, diese auch in einer passenden Repo-Memory oder Doku festhalten.

## Aktueller Programmstatus

- Gesamtstatus: `NOT_STARTED`
- Naechstes empfohlenes Ticket: `B001 - Secret Hygiene .env.example`
- Letztes abgeschlossenes Ticket: `-`
- Letzte Validierung: `-`
- Wichtige Blocker: Azure-/Infra-Entscheidungen fuer spaetere Tickets noch offen; fuer Phase 0 nicht blockierend.

## Ticket Board

| Ticket | Status | Last Update | Evidence | Notes |
|---|---|---|---|---|
| B001 - Secret Hygiene `.env.example` | TODO | - | - | Startticket fuer Phase 0. |
| B002 - Default Admin deaktivieren | TODO | - | - | Nach B001. |
| B003 - CSRF-Schutz fuer Cookie-Auth | TODO | - | - | Nach B002. |
| B004 - Rate Limit per User/IP | TODO | - | - | Nach B002. |
| B005 - Login Lockout | TODO | - | - | Nach B002/B004. |
| B006 - Settings RBAC | TODO | - | - | Nach B002. |
| B007 - MIME/Magic-Validation | TODO | - | - | Vor Parser-Vertrag. |
| B008 - CSV wirklich parsen | TODO | - | - | Upload-Vertrag reparieren. |
| B009 - XLS Legacy korrekt behandeln | TODO | - | - | Produktentscheidung noetig: blocken oder konvertieren. |
| B010 - Source Cell Provenance Excel | TODO | - | - | Grundlage fuer Excel-Gruen-Policy. |
| B011 - Config Schema Validation | TODO | - | - | Fail-fast fuer Config. |
| B012 - Dependency Pinning | TODO | - | - | Vor CI/Security-Scans. |
| B013 - CI Pipeline | TODO | - | - | Basis fuer Release-Gates. |
| B014 - SAST/Dependency Scans | TODO | - | - | Nach CI. |
| B015 - Container Hardening | TODO | - | - | Vor Produktivdeploy. |
| B016 - Separate Demo/Test Data | TODO | - | - | Mit Container Hardening. |
| B017 - Health Checks Deep | TODO | - | - | Readiness fuer Deploy. |
| B018 - Gold Set mit Feld-Level Labels | TODO | - | - | Start Phase 1. |
| B019 - Benchmark Harness | TODO | - | - | Nach Gold Set. |
| B020 - Table Structure Metrics | TODO | - | - | Nach Benchmark. |
| B021 - False-Green Canary Suite | TODO | - | - | Kernqualitaetsgate. |
| B022 - Scan Under-Extraction Test | TODO | - | - | Scan-Risiko sichtbar machen. |
| B023 - Customer-Independent Eval Split | TODO | - | - | Overfit-Schutz. |
| B024 - Model/Prompt Eval Matrix | TODO | - | - | Modell-/Prompt-Vergleich. |
| B025 - Prompt Registry mit Versionen | TODO | - | - | Reproduzierbarkeit. |
| B026 - Parser Failure Taxonomy | TODO | - | - | Fehlerklassen fuer Betrieb. |
| B027 - Synthetic BOM Generator | TODO | - | - | Breitere Testvarianz. |
| B028 - Contract Tests API/Frontend | TODO | - | - | API-Frontend-Drift verhindern. |
| B029 - Playwright E2E | TODO | - | - | Browser-Workflow absichern. |
| B030 - Performance Budget Test | TODO | - | - | Grosse BOMs absichern. |
| B031 - Generic Canonical Document Model | TODO | - | - | Start Phase 2. |
| B032 - Parser Registry | TODO | - | - | Nach Document Model. |
| B033 - Layout-aware Table Graph | TODO | - | - | Strukturtreue Tabellen. |
| B034 - Field Policy Matrix | TODO | - | - | Green Gate konfigurierbar. |
| B035 - Multi-Sheet Excel Strategy | TODO | - | - | Excel-Varianz. |
| B036 - Excel Merged-Cell Propagation | TODO | - | - | Merged Cells. |
| B037 - Language Detection | TODO | - | - | Multilingual-Metriken. |
| B038 - Parser Ensemble Voting | TODO | - | - | Parser-Konflikte erkennen. |
| B039 - DOCX/HTML/EML Parser | TODO | - | - | Neue Formate nach CDM. |
| B040 - Image Input | TODO | - | - | Nach OCR/Scan-Policy. |
| B041 - ZIP/Bulk Upload | TODO | - | - | Batch-Verarbeitung. |
| B042 - Multi-attachment Job Model | TODO | - | - | EML/ZIP-Komplexitaet. |
| B043 - Excel Deterministic Green Policy Option | TODO | - | - | Nur nach Provenance/Eval. |
| B044 - No-Green Modes pro Quelle | TODO | - | - | UX-Transparenz. |
| B045 - Multi-Format Upload-Vertrag dokumentieren | TODO | - | - | Produktversprechen synchronisieren. |
| B046 - Azure Document Intelligence Layout Spur | TODO | - | - | Start Phase 3, Azure-Entscheidung noetig. |
| B047 - OCR Fallback lokal | TODO | - | - | Optional, nach Docker-Entscheidung. |
| B048 - Unabhaengige Scan-Zaehlspur | TODO | - | - | Scan-Vollstaendigkeit. |
| B049 - Adaptive Dual Extraction | TODO | - | - | Kosten/Qualitaet. |
| B050 - Countercheck fuer Borderline Green | TODO | - | - | Konservative Gruen-Freigabe. |
| B051 - Reranker fuer Source Anchors | TODO | - | - | P2 nach Retrieval. |
| B052 - Safe Prompt Logging | TODO | - | - | Reproduzierbarkeit mit Redaction. |
| B053 - PII Detection/Minimization | TODO | - | - | Datenschutz-Policy noetig. |
| B054 - Robust File Quarantine | TODO | - | - | AV/Zielplattform abhaengig. |
| B055 - Handwriting/ICR Policy | TODO | - | - | Nur bei Bedarf. |
| B056 - Structured Outputs JSON Schema | TODO | - | - | Start Phase 4. |
| B057 - Tool/Function Calling fuer Mapping | TODO | - | - | Nach Structured Outputs. |
| B058 - Multi-Model Routing | TODO | - | - | Nach Eval Matrix. |
| B059 - LLM Call Cache | TODO | - | - | Nach Prompt Hashing. |
| B060 - LLM Cost Accounting | TODO | - | - | Betriebskosten. |
| B061 - Retrieval fuer Korrekturen | TODO | - | - | Nach Feedback Labels. |
| B062 - Stammdaten Semantic Search | TODO | - | - | Masterdata-Policy. |
| B063 - Hybrid Retrieval fuer Dokumentkontext | TODO | - | - | Nach CDM. |
| B064 - Multimodale Layout Retrieval Option | TODO | - | - | Nur wenn Eval Nutzen zeigt. |
| B065 - Query Rewriting / Multi-Hop Retrieval | TODO | - | - | Nach Retrieval. |
| B066 - Agentic Orchestration optional | TODO | - | - | Nicht fuer automatische Gruen-Entscheidung. |
| B067 - Unit System Engine | TODO | - | - | Start Phase 5. |
| B068 - Manufacturer Catalog | TODO | - | - | Stammdaten noetig. |
| B069 - Governance fuer Green Schwellen | TODO | - | - | Nach RBAC/Audit. |
| B070 - Structured Output Post-Processing | TODO | - | - | Nach Structured Outputs. |
| B071 - Completeness Verdict erweitern | TODO | - | - | Export-/Scan-Guard. |
| B072 - Citation Coverage Metric | TODO | - | - | Provenance-Metrik. |
| B073 - Explanation Export | TODO | - | - | P2 Report. |
| B074 - Output Schema Versioning | TODO | - | - | Schema-Reproduzierbarkeit. |
| B075 - Release Quality Gate | TODO | - | - | Nach CI/Benchmark. |
| B076 - OpenTelemetry Tracing | TODO | - | - | Start Phase 6. |
| B077 - Metrics + Dashboards | TODO | - | - | Nach Tracing. |
| B078 - Drift Detection | TODO | - | - | Nach Metrics/Feedback. |
| B079 - Dead Letter / Retry UI | TODO | - | - | Nach Fehlerklassen. |
| B080 - External Worker Option | TODO | - | - | Bei Skalierungsbedarf. |
| B081 - Blob Storage statt Local Files | TODO | - | - | Azure Storage Entscheidung. |
| B082 - PostgreSQL statt SQLite Option | TODO | - | - | Bei Concurrency/Analytics. |
| B083 - Admin Audit Log | TODO | - | - | Nach RBAC. |
| B084 - Source-Provenance Detail Panel | TODO | - | - | Start Phase 7. |
| B085 - Uncertainty Clustering | TODO | - | - | Review-Beschleunigung. |
| B086 - Review SLA/Kanban | TODO | - | - | Produktworkflow. |
| B087 - Human Feedback Labels | TODO | - | - | Lernsignal. |
| B088 - Active Learning Queue | TODO | - | - | Nach Feedback Labels. |
| B089 - Documentation Refresh | TODO | - | - | Phasenbegleitend. |
| B090 - Data Retention Jobs | TODO | - | - | Explizite Compliance-Ergaenzung. |
| B091 - PII/Secret Redaction Logs | TODO | - | - | Nach Logging/Redaction. |
| B092 - Entra ID / OIDC | TODO | - | - | Entra-Setup noetig. |
| B093 - Release/Versioning | TODO | - | - | Nach CI. |
| B094 - Backup/Restore Runbook | TODO | - | - | Nach Storage/DB-Entscheidung. |
| B095 - Deployment IaC | TODO | - | - | Zielplattform noetig. |
| B096 - API Pagination for Result | TODO | - | - | Nach Contract Tests. |
| B097 - Audit Blob Normalization | TODO | - | - | Nach DB-Entscheidung. |
| B098 - Streaming Progress Events | TODO | - | - | Optional nach Observability. |
| B099 - ADRs fuer Kernentscheidungen | TODO | - | - | Phasenbegleitend. |
| B100 - Programmatic Ticket Tracking Export | TODO | - | - | Optional fuer Issue-Import. |

## Agent Context Log

Neue Eintraege immer oben einfuegen.

### 2026-06-04 - Tracking-Datei angelegt

- Agent: GitHub Copilot
- Tickets: Meta-Arbeit, kein Backlog-Ticket abgeschlossen.
- Aenderungen:
  - `backlog_status.md` als gemeinsamer Status- und Kontextspeicher erstellt.
  - `backlog.md` um Pflichtprozess fuer Agenten ergaenzt.
- Validierung:
  - Noch ausstehend nach Erstellung dieser Datei.
- Naechster Schritt:
  - Mit `B001 - Secret Hygiene .env.example` beginnen.

## Architekturentscheidungen und dauerhafte Learnings

- Status und Kontext werden bewusst in Markdown gepflegt, damit Menschen und KI-Agenten ohne zusaetzliches Tooling damit arbeiten koennen.
- `backlog.md` bleibt der Plan. `backlog_status.md` ist der mutable Arbeitsstand.
- Ein Ticket gilt nicht als `DONE`, wenn keine Validierung eingetragen ist.

## Offene Entscheidungen

| Entscheidung | Benoetigt fuer | Status | Notiz |
|---|---|---|---|
| Zielplattform fuer Deploy/IaC | B095 | OPEN | VPS/Traefik, Azure Container Apps, Kubernetes oder VM entscheiden. |
| Azure Document Intelligence erlaubt? | B046, B048 | OPEN | Region, Kosten und Datenschutz klaeren. |
| Entra ID verfuegbar? | B092 | OPEN | Tenant/App Registration/Groups benoetigt. |
| Retention-Fristen | B090 | OPEN | Uploads, Exports, Audits, Feedback getrennt definieren. |
| Legacy-XLS Strategie | B009 | OPEN | Blocken oder Konvertierung per LibreOffice/calamine. |

## Letzte bekannte Validierungsbefehle

```powershell
python -m pytest tests -q
Set-Location frontend
npm test
npm run build
```