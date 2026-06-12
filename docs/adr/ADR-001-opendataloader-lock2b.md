# ADR-001 — Lock 2b (OpenDataLoader) wird zurückgestellt

**Status:** Beschlossen (2026-06-12) · **Ticket:** ARCH-001 · **Bezug:** `feature/opendataloader-evaluation` (Evaluation, kein Integrationscode)

## Kontext

Das Produktversprechen nannte einen "Lock 2b: OpenDataLoader-Fallback mit 50k-Token-Cap".
Auf dem Branch `feature/opendataloader-evaluation` liegt eine vollständige Evaluation
(`evaluation/RESULTS.md`, 11 POC-PDFs, strukturelle Proxy-Scores) — aber keine Integration.

**Evaluations-Befund (Kurzfassung):**
- Für das *textuelle* Strukturs-Signal ist OpenDataLoader PyMuPDF klar überlegen
  (+67 % Proxy-Score, 11/11 PDFs) — speziell bei Tabellenstruktur und Zeilenintegrität.
- Es ist **kein Ersatz**: der koordinatenbasierte Anker (Bounding-Boxes für RB-1,
  Lock 2, source_locations) bleibt PyMuPDF-Sache.
- Kosten der Integration: Java-/JRE-Abhängigkeit im Docker-Deployment, Latenz bis
  87 s/Datei, Token-Bloat bei breiten Tabellen (ZF-Fall) → harte Caps zwingend.
- Der GREEN-Raten-Effekt ist **unquantifiziert** (Proxy-Scores, keine gelabelte
  Ground-Truth) — die Evaluation warnt selbst vor Prozent-Versprechen.

## Entscheidung

Lock 2b wird **zurückgestellt** (nicht gestrichen). Begründung:

1. Der primäre Engpass war nie die Textstruktur-Quelle, sondern Stammdaten-Abdeckung
   (DATA-003, behoben) und Evidenz-Härtung (BUG-001…010, behoben). Erst Produktions-
   daten zeigen, ob die verbleibenden YELLOWs an der Extraktion hängen.
2. Eine JVM-Abhängigkeit + 87 s Latenz für einen unquantifizierten Gewinn widerspricht
   dem "ehrliche Einschätzung"-Versprechen an Schaufler.
3. Die seither umgesetzten Alternativen decken einen Teil des Nutzens ab:
   per-Seite-Strukturcheck mit Re-Detect (BUG-017), Dual-Pairing per Sequence-Alignment
   (BUG-016), Batch-Counter-Check + Yellow-Recheck (PERF-002/ARCH-002).

## Wiedervorlage-Kriterien (alle drei nötig)

1. Ein gelabelter Benchmark (TEST-001-Canary-Korpus) existiert und zeigt, dass ein
   relevanter YELLOW-Anteil auf fehlerhafte Textstruktur-Extraktion zurückgeht.
2. Counter-Check-Batching ist produktiv aktiv (Kostenrahmen bekannt).
3. Deployment-Entscheid für JRE im Backend-Image liegt vor (Betreiber).

**Bei Integration zwingend:** harter Token-/Seiten-Cap (50k) mit PyMuPDF-Fallback,
asynchron/gecacht, Feature-Flag pro Instanz, niemals alleinige GREEN-Evidenz.

## Konsequenzen

- Anforderungs-Audit REQ-020 bleibt "❌ Nicht implementiert" mit Verweis auf dieses ADR.
- Doku/Verkaufsmaterial darf Lock 2b nicht als vorhanden beschreiben.
- Die Evaluation bleibt auf dem Feature-Branch erhalten (`evaluation/` ist gitignored
  auf main; der Branch wird nicht gelöscht).
