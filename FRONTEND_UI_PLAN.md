# FRONTEND_UI_PLAN — Review-Dashboard verbessern

**Stand:** 2026-06-01
**Scope:** Das bestehende React/Next.js-Review-Dashboard (`frontend/`) schneller,
vertrauenswürdiger und für große BOMs tauglich machen. **Kein Neubau** — die Basis
ist reif.
**Hinweis:** `frontend/` ist ein **Git-Submodul** (eigene Commits/Push).

---

## 0. Was es schon gibt (Ist-Zustand, verifiziert)

`frontend/src/components/result-table.tsx` ist eine Excel-artige Arbeitsfläche:
- **Split-Pane:** Originaldokument (PDF) links, Vorlagen-Grid rechts, resizable.
- **Fokus-Modus:** „Alle anzeigen" / **„Nur Gelb/Rot"** (genau die schnelle Review).
- **Tastatur:** Pfeile, F2/Doppelklick bearbeiten, Enter bestätigen, Tab/F8 nächste
  offene Zelle, Ctrl/Cmd+Z/Y Undo/Redo, Ctrl+S speichern.
- **Bulk:** Zeile / Spalte / Sichtbare übernehmen → `MANUAL_CONFIRMED`.
- **Persistenz:** Edits → `PATCH /jobs/{id}/cells` → direkt ins Audit-Modell; Export.
- **PDF-Sprung:** bei Zellauswahl springt das PDF zur Quell-**Seite** (`source_location.page`).
- Weitere Komponenten: `upload-dropzone`, `stats-cards`, `completeness-banner`,
  `processing-status`, `inline-edit-cell`.
- Stack: Next.js, ag-grid (vorhanden, aber Grid ist aktuell eine eigene HTML-Tabelle),
  **react-pdf + pdfjs-dist** (vorhanden), shadcn/ui, react-query.

**Die größte ungenutzte Chance:** Der Scorer liefert pro Zelle eine **exakte
Quell-Bbox** (`source_location.bbox = [x0,y0,x1,y1]`, Seite, Text, match_type — in
RB-1 gebaut). Das Frontend nutzt davon nur die **Seite** (PDF im `<iframe>`), nicht
die Bbox. → Die exakte Stelle wird nicht hervorgehoben.

---

## 1. Priorisierte Verbesserungen

### P0 — Exakte Quell-Hervorhebung im PDF (der Vertrauens-/Tempo-Hebel)
**Problem:** Der Reviewer springt zur richtigen Seite, muss die Zelle dort aber
selbst suchen. Bei 2065 Gelb-Zellen kostet das Zeit und Vertrauen.
**Lösung:** Das `<iframe>` durch **pdfjs-Canvas-Rendering** (Deps vorhanden) ersetzen
und über dem Canvas ein **Highlight-Rechteck an `source_location.bbox`** der aktiven
Zelle zeichnen (PDF-Punkte → Canvas-Pixel skalieren). Bei Zellauswahl: Seite laden +
zur Bbox scrollen + Rechteck pulsieren.
- **Dateien:** `result-table.tsx` (PDF-Pane, `selectedSourceLocation`), evtl. neue
  `components/pdf-source-viewer.tsx`. `lib/api.ts` (Typ `source_location.bbox` ergänzen,
  falls noch nicht exponiert).
- **Backend-Check:** stellt das API-Result `source_location.bbox` schon bereit?
  Falls `result_builder` die Bbox weglässt → dort additiv ergänzen (Bbox ist im
  Audit-Modell vorhanden).
- **Risiko:** Koordinaten-Skalierung (PDF 72dpi → Render-Zoom), Rotation. Mitigation:
  pdfjs `viewport.convertToViewportRectangle(bbox)` nutzt die korrekte Transform.
- **Ergebnis:** Reviewer sieht sofort, *wo* der Wert herkommt → Gelb/Rot in Sekunden
  bestätigt.

### P1 — Neutrale Spalten standardmäßig ausblenden (Entrümpeln)
**Problem:** 73 % der Zellen sind `neutral` (ungemappte Schaufler-Felder, die die
Quelle nicht liefert). Sie blähen das 30-Spalten-Grid auf und lenken ab.
**Lösung:** Spalten, die über alle Zeilen nur `neutral`/leer sind, **per Default
einklappen**, mit Toggle „Alle Spalten anzeigen". Optional: Spalten mit Gelb/Rot
zuerst.
- **Dateien:** `result-table.tsx` (`templateColumns`/`visibleColumns` ableiten).
- **Risiko:** niedrig (rein visuell; Export unberührt).

### P1 — Grid-Virtualisierung für große BOMs
**Problem:** Mercedes hat **2852 Zeilen** × 30 Spalten. Das aktuelle Grid ist eine
einfache HTML-`<table>` (alle sichtbaren Zellen im DOM) → ruckelt/lädt langsam.
**Lösung:** Auf **ag-grid** (bereits Dependency) umstellen ODER Zeilen-Virtualisierung.
ag-grid bringt Virtualisierung, Spalten-Pinning, Sortierung, Keyboard nativ.
- **Dateien:** `result-table.tsx` → ag-grid-Integration; die bestehende Edit-/Select-/
  Undo-Logik als ag-grid-Callbacks neu verdrahten.
- **Risiko:** **mittel-hoch** — die ausgefeilte Tastatur/Undo/Popover-Logik müsste
  portiert werden. Alternative bei Zeitdruck: nur **Zeilen-Virtualisierung** der
  bestehenden Tabelle (kleiner Eingriff) statt voller ag-grid-Migration.

### P2 — Konfidenz & Quellwert-Diff sichtbar machen
- Pro Zelle eine dezente **Konfidenz-Leiste** (score) statt nur Hintergrundfarbe.
- Im Popover **Quellwert → transformiert** als Diff hervorheben (z. B. „1.2343" →
  „**1.2343 ESU**", Zusatz markiert). Macht „warum gelb/grün" sofort lesbar.
- **Dateien:** `result-table.tsx` (Popover, Zell-Render).

### P2 — Review-Durchsatz & Fortschritt
- **Fortschritt:** „X von Y offenen Zellen geprüft" + Balken; Filter „nur ungeprüfte".
- **Feld-Queues:** „Alle gelben *Werkstoff*-Zellen nacheinander" (oft schneller als
  zeilenweise, weil gleicher Kontext).
- Die vorhandenen Bulk-Aktionen (Zeile/Spalte/Sichtbare übernehmen) bleiben.

### P2 — Garantie-Status prominent
- `completeness-banner` existiert. Sicherstellen, dass **„Vollständigkeit nicht
  garantiert"** (Vision/Scan-PDFs, ZDL-1) deutlich oben steht — der Reviewer muss
  wissen, wann er *alle* Zeilen gegenprüfen sollte.

---

## 2. Empfohlene Reihenfolge
1. **P0 Quell-Highlight** (größter Wert, isoliert baubar).
2. **P1 Neutrale Spalten ausblenden** (billig, sofort spürbar).
3. **P1 Virtualisierung** (für Mercedes-Größe; ggf. erst leichte Zeilen-Virtualisierung).
4. **P2** Konfidenz/Diff, Fortschritt, Garantie-Banner.

## 3. Sicherheits-/Arbeitsprinzipien
- **Submodul-Workflow:** Änderungen in `frontend/` committen + Submodul-Pointer im
  Hauptrepo aktualisieren.
- **Bestehendes nicht brechen:** Save→`PATCH /cells`→Audit→Export ist der kritische
  Pfad und funktioniert — nicht regressieren. Tastatur-/Undo-Modell beibehalten.
- **Additiv & messbar:** jede Verbesserung einzeln, mit Lint (`npm run lint`) + manuellem
  Smoke-Test (ZF-Job laden, Gelb/Rot durchklicken, speichern, exportieren).
- **Backend-Minimal-Eingriff:** Nur falls die Bbox im API-Result fehlt, `result_builder`
  additiv ergänzen — Audit-Modell trägt sie schon.

## 4. Offene Klärung vor P1-Virtualisierung
ag-grid-Vollmigration vs. leichte Zeilen-Virtualisierung: hängt davon ab, wie groß die
real erwarteten BOMs sind und wie viel Tastatur-Komfort erhalten bleiben muss.
Empfehlung: erst P0+P1(Spalten) liefern, dann anhand echter Pilot-Nutzung über die
Virtualisierungs-Tiefe entscheiden.
