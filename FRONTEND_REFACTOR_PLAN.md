# FRONTEND_REFACTOR_PLAN — Review-Dashboard neu aufstellen

**Stand:** 2026-06-01
**Symptome (User):** extrem langsame UI, Zeilen lassen sich nicht löschen,
unhandlich/schwer bedienbar, UX nicht gut.
**Verdikt:** Die Datenschicht/Garantien sind solide. Das Problem ist die
**Präsentations-/Interaktionsschicht** — ein 1600-Zeilen-Monolith mit einer
**nicht-virtualisierten HTML-Tabelle**. Das ist ein gezielter Frontend-Refactor,
kein Backend-Umbau.
**Hinweis:** `frontend/` ist ein **Git-Submodul** (eigene Commits/Pointer).

---

## 1. Diagnose (Ursachen mit Beleg)

### 1.1 Langsam — die Kernursache
`result-table.tsx` rendert eine **eigene `<table>`** mit **allen sichtbaren Zeilen ×
30 Spalten**. Jede Zelle ist ein `<button>` mit Handlern.
- ZF: 599 × 30 = **~18.000** DOM-Knoten. Mercedes: 2852 × 30 = **~85.000**. → Browser
  ächzt, Scrollen/Editieren ruckelt.
- **Keine Virtualisierung.** `ag-grid-community` ist als Dependency installiert, aber
  **ungenutzt** — die Lösung liegt im Projekt schon bereit.
- **Undo deep-cloned alles:** `cloneGridRows(rowData)` bei *jedem* Edit (Snapshot) →
  O(Zeilen×Spalten) pro Tastendruck.
- Viele `useMemo`/Lookups (`baseCellLookup`, `currentCellLookup`, `openCells` …)
  werden bei jeder Änderung neu aufgebaut.

### 1.2 Zeilen löschen — fehlt komplett
Kein Lösch-/Ausschließen-/Hinzufügen von Zeilen. `lib/api.ts` hat **keinen** Endpoint
dafür (`saveEditedCells` ändert nur Zellwerte). Der Reviewer kann eine Junk-/Footer-
Zeile, die durchgerutscht ist, nicht entfernen.

### 1.3 Unhandlich / UX
- **30 Spalten, ~73 % neutral/leer** (Schaufler-Zielfelder, die die Quelle nicht
  liefert) → ewiges Horizontal-Scrollen, kein Fokus.
- **Monolith:** ein Component mit 15+ `useState`, eigener Tastatur-Navigation, eigenem
  Popover-Positioning, eigenem Undo/Redo, eingebettetem PDF-Viewer — fragil und schwer
  zu erweitern.
- **PDF-Pane** klein + die pdfjs/Next-Baustelle (separat).

### 1.4 Wartbarkeit
1600 Zeilen, ein Component. Edit-State als `Map` + Snapshot-Undo ist fehleranfällig.

---

## 2. Zielarchitektur

| Bereich | Heute | Ziel |
|---|---|---|
| Grid | eigene `<table>`, kein Virtual | **ag-grid-community** (Virtualisierung, natives Edit/Keyboard/Sort/Filter/Pin/Row-Ops) |
| State | 15+ useState + Map + Snapshots | schlanker Reducer/Store (`useReducer` oder `zustand`) für Edits/Selektion/Undo; ag-grid-Transaktionen als Quelle |
| Komponenten | 1 Monolith | `ReviewWorkspace` → `SourcePane` + `ReviewGrid` + `ReviewToolbar` + `CellDetailPanel` + `StatusBar`; Hooks `useJobResult`, `useCellEdits`, `usePdfHighlight` |
| Zeilen-Ops | keine | löschen/ausschließen/wiederherstellen (+ optional hinzufügen) — Backend + Audit + Export |
| Spalten | alle 30 fix | neutrale/leere standardmäßig aus, Gruppen, Pin, Toggle, Status-Filter |

**ag-grid-community reicht** (Virtualisierung, Inline-Edit, Keyboard, Sortieren,
Filtern, Spalten-Pin, Range-Selektion). Grouping/Pivot wäre Enterprise — nicht nötig.

---

## 3. Phasenplan (priorisiert nach Schmerz)

### R1 — Virtualisiertes Grid (ag-grid) — **größter Hebel, zuerst**
- Eigene `<table>` durch `<AgGridReact>` ersetzen. Spalten aus `result.columns`
  (Vorlagen-Layout), Zeilen aus `result.rows`.
- **Zell-Renderer** für Ampelfarbe + „bearbeitet"-Markierung; **Zell-Editor** inline.
- Edits über ag-grid `onCellValueChanged` → in den Edits-Store → `PATCH /cells`
  (kritischen Pfad **unverändert** lassen).
- Tastatur, Sortieren, Filter, Spalten-Pin: **nativ von ag-grid** (ersetzt ~600 Zeilen
  eigene Logik).
- **Akzeptanz:** Mercedes (2852 Zeilen) scrollt/editiert flüssig; Speichern+Export
  unverändert korrekt.
- **Risiko:** mittel-hoch — Edit/Undo/Selektion auf ag-grid-APIs portieren. Mitigation:
  schrittweise, Save→Export-Flow zuerst durchtesten.

### R2 — Spalten-Management (Entrümpeln)
- Spalten, die über **alle** Zeilen nur `neutral`/leer sind, **per Default ausblenden**;
  Toggle „Alle Felder". Pflichtfelder (Pos./Benennung/Werkstoff/Maße) pinnen.
- **Status-Filter** (nur Gelb/Rot — gibt es als View-Mode schon, ag-grid macht es nativ).
- Spalten-Sichtbarkeit pro Nutzer im `localStorage` merken (frontend-only).

### R3 — Zeilen-Operationen (explizit gefordert)
- **Frontend:** Zeile löschen/ausschließen (Kontextmenü + `Entf`-Taste + Bulk),
  Wiederherstellen; optional Zeile hinzufügen.
- **Backend (neu):** `DELETE /jobs/{id}/rows/{rowIndex}` bzw. `PATCH …/rows` mit
  `excluded`-Flag → Audit markiert die Zeile als **manuell ausgeschlossen** (mit
  Grund/Timestamp), **Export überspringt sie**, Zähler aktualisieren.
- **Zero-Data-Loss bleibt gewahrt:** Ausschließen ist eine **explizite, im Audit
  protokollierte Nutzeraktion** — kein *stiller* Verlust. Der Export-Guard muss
  ausgeschlossene Zeilen als „bewusst entfernt" akzeptieren (nicht als Verlust werten).
- **Akzeptanz:** Reviewer löscht eine Footer-/Junk-Zeile → verschwindet aus Grid +
  Export, erscheint im Audit-Trail als „ausgeschlossen durch Nutzer".

### R4 — Review-Flow & UX
- **Review-Queue:** Gelb/Rot der Reihe nach; **Fortschritt** „X von Y geprüft" + Balken.
- **Keyboard-first:** Übernehmen / Korrigieren / Überspringen / Löschen per Taste;
  Bulk (Zeile/Spalte/Sichtbare — existiert, in ag-grid übernehmen).
- **CellDetailPanel** (statt schwebendes Popover): Quellwert→transformiert als **Diff**,
  Begründung, Score-Balken, Quell-Snippet, „Zur Quelle"-Button.
- **Garantie-Banner** prominent (`completeness-banner` existiert): bei „nicht garantiert"
  (Scan/Vision) deutlich warnen.

### R5 — Quell-PDF-Pane stabilisieren
- Die pdfjs/Next-Baustelle sauber lösen (rohes pdfjs ODER `legacy`-Build), **exakte
  Bbox-Hervorhebung** (P0) zuverlässig; iframe-Fallback bleibt als Sicherheitsnetz.

### R6 — Entkopplung & Tests
- Monolith in die Komponenten/Hooks aus §2 zerlegen.
- **Tests:** RTL/Playwright für den kritischen Pfad (laden → editieren → löschen →
  speichern → exportieren). **Perf-Budget** mit Mercedes als Benchmark.

---

## 4. Backend-Änderungen (minimal, additiv)
- **Zeilen-Ausschluss:** `DELETE /jobs/{id}/rows/{rowIndex}` → setzt im Audit
  `CellAudit`/Row-Marker `excluded=True` + Grund; `recalculate_audit_summary` + Export
  (`excel_exporter`) überspringen ausgeschlossene Zeilen; Export-Guard zählt sie nicht
  als Verlust.
- (Optional) `POST /jobs/{id}/rows` zum Hinzufügen.
- Bestehende `PATCH /cells` + Export bleiben unverändert.

---

## 5. Risiken & Prinzipien
- **Kritischen Pfad nicht brechen:** `PATCH /cells` → Audit → Export funktioniert und
  ist getestet — Parität halten.
- **ag-grid-Migration ist das Hochrisiko-Stück:** inkrementell, Save/Export zuerst
  absichern, Keyboard/Undo-Parität sicherstellen.
- **Zeilen-Löschen = explizit + auditiert** (kein stiller Verlust; Zero-Data-Loss-Vertrag
  bleibt — er verbietet *stillen* Verlust, nicht bewusste Nutzeraktionen).
- **Submodul-Workflow:** Frontend committen + Pointer im Hauptrepo bumpen.
- **Messen:** Mercedes (2852 Zeilen) als Performance-Benchmark vor/nach R1.

---

## 6. Reihenfolge & Aufwand (grob, ehrlich)
1. **R1 ag-grid** (Performance) — der eigentliche Schmerz. **3–5 Tage.**
2. **R3 Zeilen-Ops** (explizit gefordert) — Backend + Frontend. **1–2 Tage.**
3. **R2 Spalten entrümpeln** — **0,5–1 Tag.**
4. **R4 Review-Flow/UX** — **2–3 Tage.**
5. **R5 PDF-Pane** — **1–2 Tage** (pdfjs-Baustelle).
6. **R6 Entkopplung/Tests** — laufend.

**Empfehlung:** **R1 zuerst** (löst „extrem langsam" + bringt natives Keyboard/Sort/
Filter gratis), dann **R3** (Zeilen löschen). Danach R2/R4 für die UX-Politur.
