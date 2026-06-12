"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import {
  AlertTriangle,
  BoxSelect,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Eye,
  FileText,
  Keyboard,
  RotateCcw,
  Trash2,
} from "lucide-react";
import type { GridApi } from "ag-grid-community";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CompletenessBanner } from "@/components/completeness-banner";
import { ErrorBoundary } from "@/components/error-boundary";
import { TopBar } from "@/components/top-bar";
import { ReviewGrid, type GridRow } from "@/components/review-grid";
import { getSourceUrl } from "@/lib/api";
import type { CellEditPayload, CellResult, JobResult } from "@/lib/api";
import { WORKSPACE_ROUTE } from "@/lib/routes";

/** Which panes the workspace shows. "pdf"/"grid" are the single-pane tab views. */
export type PaneMode = "both" | "pdf" | "grid";

// react-pdf / pdfjs is browser-only — load client-side after mount.
const PdfSourceViewer = dynamic(
  () => import("@/components/pdf-source-viewer").then((m) => m.PdfSourceViewer),
  {
    ssr: false,
    loading: () => <div className="pdf-source-loading">PDF wird geladen…</div>,
  }
);

type ViewMode = "all" | "review";

interface ResultTableProps {
  result: JobResult;
  onSaveEdits: (edits: CellEditPayload[]) => Promise<void>;
  onExcludeRows: (rowIndices: number[], excluded: boolean) => Promise<void>;
  onBack: () => void;
  onExport: () => void;
  isSaving: boolean;
  isExcluding: boolean;
  /** Single-pane tab views ("pdf"/"grid") for a second monitor. */
  paneMode?: PaneMode;
}

function displayValue(cell?: CellResult | null): string {
  if (!cell) return "";
  return cell.transformed_value ?? cell.raw_value ?? "";
}

function isOpen(cell: CellResult): boolean {
  return cell.classification === "yellow" || cell.classification === "red";
}

export function ResultTable({
  result,
  onSaveEdits,
  onExcludeRows,
  onBack,
  onExport,
  isSaving,
  isExcluding,
  paneMode = "both",
}: ResultTableProps) {
  const [viewMode, setViewMode] = useState<ViewMode>("all");
  const [showAllColumns, setShowAllColumns] = useState(false);
  const [pendingEdits, setPendingEdits] = useState<Map<string, CellEditPayload>>(
    () => new Map()
  );
  const [selectedCell, setSelectedCell] = useState<CellResult | null>(null);
  const [openCount, setOpenCount] = useState(0);
  // Multi-cell selection (custom — ag-grid community has no range selection).
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedCells, setSelectedCells] = useState<Set<string>>(() => new Set());

  const [sourcePaneRatio, setSourcePaneRatio] = useState(0.42);
  const [pdfPage, setPdfPage] = useState(1);
  const [pdfNumPages, setPdfNumPages] = useState(0);
  const [pdfReloadKey, setPdfReloadKey] = useState(0);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [showShortcuts, setShowShortcuts] = useState(false);

  const gridApiRef = useRef<GridApi<GridRow> | null>(null);
  const workspaceRef = useRef<HTMLDivElement | null>(null);
  const isResizingRef = useRef(false);

  const columns = useMemo(
    () =>
      result.columns.length > 0
        ? result.columns
        : result.target_fields.map((field) => ({
            field,
            column: "",
            header_label: field,
            header_lines: [field],
            width: null,
            type: "string",
            required: false,
            horizontal_alignment: null,
            vertical_alignment: null,
          })),
    [result]
  );

  const totalOpen = useMemo(() => {
    let count = 0;
    for (const row of result.rows) {
      for (const cell of row.cells) {
        if (isOpen(cell)) count += 1;
      }
    }
    return count;
  }, [result.rows]);

  // A field carries content if any row has a value or a non-neutral status. Empty
  // Schaufler target fields (the source never delivers them) are hidden by default.
  const populatedFields = useMemo(() => {
    const set = new Set<string>();
    for (const row of result.rows) {
      for (const cell of row.cells) {
        const hasValue = !!(cell.transformed_value ?? cell.raw_value);
        if (hasValue || cell.classification !== "neutral") {
          set.add(cell.target_field);
        }
      }
    }
    return set;
  }, [result.rows]);

  const toggleShowAllColumns = useCallback(() => {
    setShowAllColumns((prev) => {
      const next = !prev;
      localStorage.setItem("bom-show-all-columns", next ? "1" : "0");
      return next;
    });
  }, []);

  // Restore the column-density preference once on mount (SSR-safe).
  useEffect(() => {
    const saved = localStorage.getItem("bom-show-all-columns");
    if (saved != null) setShowAllColumns(saved === "1");
  }, []);

  // Reset transient state when a different job/result loads.
  useEffect(() => {
    setPendingEdits(new Map());
    setSelectedCell(null);
    setOpenCount(totalOpen);
    setSelectionMode(false);
    setSelectedCells(new Set());
    setPdfError(null);
    setPdfPage(1);
    setPdfReloadKey(0);
  }, [result.job_id, totalOpen]);

  const sourceUrl = useMemo(() => getSourceUrl(result.job_id), [result.job_id]);
  const isPdfSource = useMemo(
    () => result.filename.toLowerCase().endsWith(".pdf"),
    [result.filename]
  );
  const selectedSource = selectedCell?.source_location ?? null;

  // Drive the PDF page from the selected cell's source location.
  useEffect(() => {
    if (isPdfSource && selectedSource?.page) {
      setPdfPage(selectedSource.page);
    }
  }, [isPdfSource, selectedSource?.page]);

  const recordEdit = useCallback((edit: CellEditPayload) => {
    setPendingEdits((prev) => {
      const next = new Map(prev);
      next.set(`${edit.row_index}:${edit.target_field}`, edit);
      return next;
    });
    setOpenCount((prev) => Math.max(0, prev - 1));
  }, []);

  const handleCellSelect = useCallback((cell: CellResult | null) => {
    setSelectedCell(cell);
  }, []);

  // Move grid focus to the next open (yellow/red) cell after the current one,
  // respecting the active sort/filter. This is the "review queue".
  const focusNextOpen = useCallback(() => {
    const api = gridApiRef.current;
    if (!api) return;
    const orderedCols = api
      .getAllDisplayedColumns()
      .map((col) => col.getColId())
      .filter((id) => !id.startsWith("ag-Grid-"));

    const nodeRefs: { rowIndex: number; data: GridRow }[] = [];
    api.forEachNodeAfterFilterAndSort((node) => {
      if (node.data && node.rowIndex != null) {
        nodeRefs.push({ rowIndex: node.rowIndex, data: node.data });
      }
    });

    const focused = api.getFocusedCell();
    let startNode = 0;
    let startCol = -1;
    if (focused) {
      startNode = nodeRefs.findIndex((n) => n.rowIndex === focused.rowIndex);
      if (startNode < 0) startNode = 0;
      startCol = orderedCols.indexOf(focused.column.getColId());
    }

    for (let ni = startNode; ni < nodeRefs.length; ni += 1) {
      const { rowIndex, data } = nodeRefs[ni];
      const colStart = ni === startNode ? startCol + 1 : 0;
      for (let ci = colStart; ci < orderedCols.length; ci += 1) {
        const field = orderedCols[ci];
        const cell = data.__cells[field];
        if (cell && isOpen(cell)) {
          api.ensureIndexVisible(rowIndex, "middle");
          api.ensureColumnVisible(field);
          api.setFocusedCell(rowIndex, field);
          api.flashCells({ columns: [field], rowNodes: [api.getRowNode(String(data.__rowIndex))!] });
          handleCellSelect(cell);
          return;
        }
      }
    }
  }, [handleCellSelect]);

  const acceptCell = useCallback(
    (rowIndex: number, field: string) => {
      const api = gridApiRef.current;
      const node = api?.getRowNode(String(rowIndex));
      if (!api || !node?.data) return;
      const cell = node.data.__cells[field];
      if (!cell || !isOpen(cell)) return;
      const value = displayValue(cell);
      // Clone rather than mutate — the cell is owned by the React Query cache.
      node.data.__cells[field] = {
        ...cell,
        transformed_value: value,
        classification: "manual_confirmed",
        final_status: "manual_confirmed",
        method: "manual_override",
      };
      node.data[field] = value;
      api.refreshCells({ rowNodes: [node], columns: [field], force: true });
      recordEdit({ row_index: rowIndex, target_field: field, corrected_value: value });
    },
    [recordEdit]
  );

  const acceptCurrentAndNext = useCallback(() => {
    if (selectedCell && isOpen(selectedCell)) {
      acceptCell(selectedCell.row_index, selectedCell.target_field);
    }
    focusNextOpen();
  }, [acceptCell, focusNextOpen, selectedCell]);

  const handleSave = useCallback(async () => {
    if (pendingEdits.size === 0) return;
    await onSaveEdits(Array.from(pendingEdits.values()));
    setPendingEdits(new Map());
  }, [onSaveEdits, pendingEdits]);

  const excludeRows = useCallback(
    async (rowIndices: number[]) => {
      if (rowIndices.length === 0) return;
      const confirmed = window.confirm(
        `${rowIndices.length} Zeile(n) aus Ergebnis und Export entfernen?\n` +
          "Die Zeilen werden im Audit als bewusst ausgeschlossen protokolliert " +
          "und lassen sich wiederherstellen."
      );
      if (!confirmed) return;
      await onExcludeRows(rowIndices, true);
    },
    [onExcludeRows]
  );

  const handleRestoreExcluded = useCallback(() => {
    if (result.excluded_rows.length === 0) return;
    void onExcludeRows(result.excluded_rows, false);
  }, [onExcludeRows, result.excluded_rows]);

  // Accept every open (yellow/red) cell in the currently focused column at once.
  const acceptColumn = useCallback(() => {
    const api = gridApiRef.current;
    const field = api?.getFocusedCell()?.column.getColId();
    if (!api || !field) {
      window.alert("Bitte zuerst eine Zelle in der gewuenschten Spalte anklicken.");
      return;
    }
    const edits: CellEditPayload[] = [];
    api.forEachNode((node) => {
      const cell = node.data?.__cells[field];
      if (node.data && cell && isOpen(cell)) {
        const value = displayValue(cell);
        node.data.__cells[field] = {
          ...cell,
          transformed_value: value,
          classification: "manual_confirmed",
          final_status: "manual_confirmed",
          method: "manual_override",
        };
        node.data[field] = value;
        edits.push({ row_index: node.data.__rowIndex, target_field: field, corrected_value: value });
      }
    });
    if (edits.length === 0) return;
    api.refreshCells({ columns: [field], force: true });
    edits.forEach(recordEdit);
  }, [recordEdit]);

  // Exclude the checkbox-selected rows (or the focused row when none selected).
  const deleteSelectedRows = useCallback(() => {
    const api = gridApiRef.current;
    if (!api) return;
    let rows = api
      .getSelectedNodes()
      .map((node) => node.data?.__rowIndex)
      .filter((index): index is number => typeof index === "number");
    if (rows.length === 0) {
      const focused = api.getFocusedCell();
      const node = focused ? api.getDisplayedRowAtIndex(focused.rowIndex) : null;
      if (node?.data) rows = [node.data.__rowIndex];
    }
    if (rows.length === 0) {
      window.alert("Bitte Zeilen ueber die Checkboxen auswaehlen oder eine Zelle anklicken.");
      return;
    }
    void excludeRows(rows);
  }, [excludeRows]);

  const toggleSelectionMode = useCallback(() => {
    setSelectionMode((mode) => {
      if (mode) setSelectedCells(new Set());
      return !mode;
    });
  }, []);

  // Accept every selected open cell at once (the multi-field "mark as done").
  const acceptSelected = useCallback(() => {
    const api = gridApiRef.current;
    if (!api || selectedCells.size === 0) return;
    const edits: CellEditPayload[] = [];
    const fields = new Set<string>();
    selectedCells.forEach((key) => {
      const sep = key.indexOf(":");
      const rowIndex = Number(key.slice(0, sep));
      const field = key.slice(sep + 1);
      const node = api.getRowNode(String(rowIndex));
      const cell = node?.data?.__cells[field];
      if (node?.data && cell && isOpen(cell)) {
        const value = displayValue(cell);
        node.data.__cells[field] = {
          ...cell,
          transformed_value: value,
          classification: "manual_confirmed",
          final_status: "manual_confirmed",
          method: "manual_override",
        };
        node.data[field] = value;
        edits.push({ row_index: rowIndex, target_field: field, corrected_value: value });
        fields.add(field);
      }
    });
    setSelectedCells(new Set());
    if (edits.length === 0) return;
    api.refreshCells({ columns: Array.from(fields), force: true });
    edits.forEach(recordEdit);
  }, [recordEdit, selectedCells]);

  // Open one pane (PDF or grid) in its own tab — for a second monitor.
  const openPaneInTab = useCallback(
    (view: "pdf" | "grid") => {
      window.open(
        `${WORKSPACE_ROUTE}?jobId=${encodeURIComponent(result.job_id)}&view=${view}`,
        "_blank",
        "noopener,noreferrer"
      );
    },
    [result.job_id]
  );

  // Keyboard-first review.
  // Global: Ctrl+S save, PageDown/PageUp PDF navigation, F1/? shortcuts modal, Escape close modal.
  // Single-key (N, A): ignored while editing a cell or typing in an input.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // Ctrl/Cmd+S — save pending edits
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (pendingEdits.size > 0 && !isSaving) void handleSave();
        return;
      }

      // F1 / ? — toggle shortcuts modal (always active)
      if (event.key === "F1" || (event.key === "?" && !event.ctrlKey && !event.metaKey)) {
        event.preventDefault();
        setShowShortcuts((v) => !v);
        return;
      }

      // Escape — close shortcuts modal if open
      if (event.key === "Escape" && !event.ctrlKey && !event.metaKey) {
        if (showShortcuts) {
          setShowShortcuts(false);
          return;
        }
      }

      // PageDown / PageUp — PDF page navigation (always active, no modifier)
      if (!event.ctrlKey && !event.metaKey && !event.altKey) {
        if (event.key === "PageDown" && isPdfSource) {
          event.preventDefault();
          setPdfPage((p) => (pdfNumPages > 0 ? Math.min(pdfNumPages, p + 1) : p + 1));
          return;
        }
        if (event.key === "PageUp" && isPdfSource) {
          event.preventDefault();
          setPdfPage((p) => Math.max(1, p - 1));
          return;
        }
      }

      if (event.ctrlKey || event.metaKey || event.altKey) return;

      const target = event.target as HTMLElement | null;
      const tag = target?.tagName;
      const typing =
        tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable;
      const editing = (gridApiRef.current?.getEditingCells().length ?? 0) > 0;
      if (typing || editing) return;

      const key = event.key.toLowerCase();
      if (key === "n") {
        event.preventDefault();
        focusNextOpen();
      } else if (key === "a") {
        event.preventDefault();
        acceptCurrentAndNext();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    acceptCurrentAndNext,
    focusNextOpen,
    handleSave,
    isSaving,
    isPdfSource,
    pdfNumPages,
    pendingEdits.size,
    showShortcuts,
  ]);

  useEffect(() => {
    const onMove = (event: MouseEvent) => {
      if (!isResizingRef.current || !workspaceRef.current) return;
      const bounds = workspaceRef.current.getBoundingClientRect();
      if (bounds.width <= 0) return;
      const ratio = (event.clientX - bounds.left) / bounds.width;
      setSourcePaneRatio(Math.max(0.26, Math.min(0.66, ratio)));
    };
    const onUp = () => {
      isResizingRef.current = false;
      document.body.classList.remove("workspace-resize-active");
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  const displayPdfPage = Math.max(pdfPage, 1);
  const reviewedCount = Math.max(0, totalOpen - openCount);

  // Pane visibility for the split view vs. single-pane tab views.
  const showSource = paneMode !== "grid";
  const showGrid = paneMode !== "pdf";
  const showSplitter = paneMode === "both";
  const gridTemplateColumns =
    paneMode === "both"
      ? `${(sourcePaneRatio * 100).toFixed(2)}% 8px minmax(0, 1fr)`
      : "minmax(0, 1fr)";

  const pageLabel = isPdfSource
    ? pdfNumPages > 0
      ? `Seite ${displayPdfPage}/${pdfNumPages}`
      : `Seite ${displayPdfPage}`
    : "Datei";

  return (
    <div className="workspace-shell">
      <TopBar
        fileName={result.filename}
        pageLabel={pageLabel}
        kpis={{
          green: result.green_count,
          yellow: result.yellow_count,
          red: result.red_count,
        }}
        onBack={onBack}
        onToggleLayout={toggleShowAllColumns}
        onOpenList={() => setViewMode("all")}
        onOpenFilter={() => setViewMode((prev) => (prev === "review" ? "all" : "review"))}
        onUndo={() => gridApiRef.current?.undoCellEditing()}
        onRedo={() => gridApiRef.current?.redoCellEditing()}
        onSave={() => {
          if (pendingEdits.size > 0 && !isSaving) {
            void handleSave();
          }
        }}
        onExport={onExport}
        onShowShortcuts={() => setShowShortcuts(true)}
      />

      {/* ZDL-1: surface the completeness guarantee. Only shown when NOT
          guaranteed — the operator must be warned, not lulled. */}
      {!result.completeness_guaranteed && (
        <div className="shrink-0 px-3 pb-2 pt-3">
          <CompletenessBanner result={result} />
        </div>
      )}

      {/* ARCH-003: Excel/CSV cannot produce GREEN — explain why. */}
      {result.green_policy_note && (
        <div className="shrink-0 px-3 pb-2">
          <div
            className="flex items-start gap-3 rounded-lg border border-[hsl(var(--status-yellow))]/50 bg-[hsl(var(--status-yellow))]/10 p-4 text-foreground"
            role="status"
          >
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-[hsl(var(--status-yellow))]" />
            <p className="text-sm font-medium">{result.green_policy_note}</p>
          </div>
        </div>
      )}

      <div
        ref={workspaceRef}
        className="workspace-main"
        style={{ gridTemplateColumns }}
      >
        {showSource && (
        <section className="workspace-pane workspace-pane--source">
          <header className="workspace-pane-header">
            <div className="workspace-pane-title">
              <FileText className="h-4 w-4" />
              <span>Originaldokument</span>
            </div>
            <div className="workspace-pane-meta">
              <Badge variant="outline">{result.filename}</Badge>
              {totalOpen > 0 && (
                <Badge variant="outline">{reviewedCount} / {totalOpen} geprueft</Badge>
              )}
              {pendingEdits.size > 0 && (
                <Badge variant="outline" className="workspace-badge-unsaved">
                  {pendingEdits.size} ungespeichert
                </Badge>
              )}
              {result.excluded_rows.length > 0 && (
                <Badge variant="outline" className="workspace-badge-excluded">
                  {result.excluded_rows.length} ausgeschlossen
                  <button
                    type="button"
                    className="workspace-badge-action"
                    onClick={handleRestoreExcluded}
                    disabled={isExcluding}
                  >
                    Wiederherstellen
                  </button>
                </Badge>
              )}
              {paneMode === "both" && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => openPaneInTab("pdf")}
                  title="Original in eigenem Tab oeffnen (zweiter Bildschirm)"
                >
                  <ExternalLink className="mr-2 h-4 w-4" />
                  Neuer Tab
                </Button>
              )}
              <Button
                variant="ghost"
                size="sm"
                onClick={() => window.open(sourceUrl, "_blank", "noopener,noreferrer")}
              >
                <Eye className="mr-2 h-4 w-4" />
                Oeffnen
              </Button>
            </div>
          </header>

          <div className="workspace-pane-body">
            {isPdfSource ? (
              <div className="workspace-pdf-viewer">
                <div className="workspace-pdf-toolbar">
                  <div className="workspace-pdf-toolbar-group">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setPdfPage((p) => Math.max(1, p - 1))}
                      aria-label="Vorherige Seite"
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </Button>
                    <input
                      type="number"
                      min={1}
                      value={displayPdfPage}
                      onChange={(event) => {
                        const parsed = Number(event.target.value);
                        if (Number.isFinite(parsed)) {
                          setPdfPage(Math.max(Math.trunc(parsed), 1));
                        }
                      }}
                      className="workspace-pdf-page-input"
                      aria-label="PDF Seite"
                    />
                    <span className="workspace-pdf-page-total">
                      {pdfNumPages > 0 ? `Seite / ${pdfNumPages}` : "Seite"}
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        setPdfPage((p) =>
                          pdfNumPages > 0 ? Math.min(p + 1, pdfNumPages) : p + 1
                        )
                      }
                      disabled={pdfNumPages > 0 && displayPdfPage >= pdfNumPages}
                      aria-label="Naechste Seite"
                    >
                      <ChevronRight className="h-4 w-4" />
                    </Button>
                  </div>
                  <div className="workspace-pdf-toolbar-group">
                    {selectedSource?.page && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setPdfPage(selectedSource.page ?? 1)}
                      >
                        Zur markierten Quelle
                      </Button>
                    )}
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setPdfError(null);
                        setPdfReloadKey((k) => k + 1);
                      }}
                    >
                      <RotateCcw className="mr-2 h-4 w-4" />
                      Neu laden
                    </Button>
                  </div>
                </div>

                <div className="workspace-pdf-canvas-wrap">
                  {pdfError ? (
                    // The pdfjs canvas viewer could not load/render in this
                    // environment — fall back to the browser's native PDF viewer
                    // via an iframe (works now that /source is served inline).
                    // The source highlight is unavailable in this mode.
                    <iframe
                      key={`fallback-${pdfReloadKey}-${displayPdfPage}`}
                      title={`PDF Vorschau ${result.filename}`}
                      src={`${sourceUrl}#page=${displayPdfPage}&view=FitH`}
                      className="workspace-pdf-iframe"
                    />
                  ) : (
                    <ErrorBoundary
                      fallback={
                        <iframe
                          key={`fallback-${pdfReloadKey}-${displayPdfPage}`}
                          title={`PDF Vorschau ${result.filename}`}
                          src={`${sourceUrl}#page=${displayPdfPage}&view=FitH`}
                          className="workspace-pdf-iframe"
                        />
                      }
                    >
                      <PdfSourceViewer
                        url={sourceUrl}
                        page={displayPdfPage}
                        highlightBbox={selectedSource?.bbox}
                        highlightOnThisPage={selectedSource?.page === displayPdfPage}
                        reloadKey={pdfReloadKey}
                        onDocumentLoad={setPdfNumPages}
                        onError={(message) => setPdfError(message)}
                      />
                    </ErrorBoundary>
                  )}
                </div>
              </div>
            ) : (
              <div className="workspace-file-fallback">
                <FileText className="h-8 w-8" />
                <p className="workspace-file-fallback-title">
                  Keine PDF-Vorschau verfuegbar
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => window.open(sourceUrl, "_blank", "noopener,noreferrer")}
                >
                  Datei oeffnen
                </Button>
              </div>
            )}
          </div>
        </section>
        )}

        {showSplitter && (
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="Splitter"
            className="workspace-splitter"
            onMouseDown={(event) => {
              event.preventDefault();
              isResizingRef.current = true;
              document.body.classList.add("workspace-resize-active");
            }}
          />
        )}

        {showGrid && (
        <section className="workspace-pane workspace-pane--target">
          <div className="flex h-full min-h-0 w-full flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card px-2 py-1.5 shadow-sm">
              <Button variant="outline" size="sm" onClick={acceptColumn}>
                <CheckCircle2 className="mr-2 h-4 w-4 text-emerald-600" />
                Spalte uebernehmen
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={acceptCurrentAndNext}
                disabled={!selectedCell || !isOpen(selectedCell)}
              >
                <Check className="mr-2 h-4 w-4" />
                Uebernehmen &amp; weiter
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={deleteSelectedRows}
                disabled={isExcluding}
              >
                <Trash2 className="mr-2 h-4 w-4" />
                Zeile(n) loeschen
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={toggleSelectionMode}
                className={
                  selectionMode
                    ? "border-[hsl(var(--primary))] bg-[hsl(var(--primary))]/10 text-[hsl(var(--primary))]"
                    : undefined
                }
                title="Mehrere Felder anklicken und gemeinsam uebernehmen"
              >
                <BoxSelect className="mr-2 h-4 w-4" />
                Mehrfachauswahl{selectionMode ? " an" : ""}
              </Button>
              {selectionMode && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={acceptSelected}
                  disabled={selectedCells.size === 0}
                >
                  <CheckCircle2 className="mr-2 h-4 w-4 text-emerald-600" />
                  Auswahl uebernehmen ({selectedCells.size})
                </Button>
              )}
              {paneMode === "both" && (
                <Button
                  variant="outline"
                  size="sm"
                  className="ml-auto"
                  onClick={() => openPaneInTab("grid")}
                  title="Tabelle in eigenem Tab oeffnen (zweiter Bildschirm)"
                >
                  <ExternalLink className="mr-2 h-4 w-4" />
                  Neuer Tab
                </Button>
              )}
            </div>
            <div className="min-h-0 flex-1">
              <ReviewGrid
                result={result}
                columns={columns}
                reviewOnly={viewMode === "review"}
                onCellEdit={recordEdit}
                onCellSelect={handleCellSelect}
                onGridApi={(api) => {
                  gridApiRef.current = api;
                }}
                onRequestDelete={(rows) => void excludeRows(rows)}
                showAllColumns={showAllColumns}
                populatedFields={populatedFields}
                selectionMode={selectionMode}
                selectedCellKeys={selectedCells}
                onSelectionChange={setSelectedCells}
              />
            </div>
          </div>
        </section>
        )}
      </div>

      {showShortcuts && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setShowShortcuts(false)}
        >
          <div
            className="w-full max-w-sm rounded-xl border border-border bg-card p-5 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-4 flex items-center gap-2">
              <Keyboard className="h-4 w-4 text-muted-foreground" />
              <h2 className="text-sm font-semibold">Tastaturkürzel</h2>
            </div>
            <table className="w-full text-sm">
              <tbody className="divide-y divide-border">
                {[
                  ["N", "Nächste offene Position"],
                  ["A", "Übernehmen &amp; weiter"],
                  ["PageDown", "Nächste PDF-Seite"],
                  ["PageUp", "Vorherige PDF-Seite"],
                  ["Ctrl+S", "Änderungen speichern"],
                  ["F1 / ?", "Diese Hilfe"],
                  ["Escape", "Schließen"],
                ].map(([key, desc]) => (
                  <tr key={key}>
                    <td className="py-1.5 pr-4">
                      <kbd className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                        {key}
                      </kbd>
                    </td>
                    <td
                      className="py-1.5 text-muted-foreground"
                      dangerouslySetInnerHTML={{ __html: desc }}
                    />
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="mt-4 flex justify-end">
              <Button size="sm" onClick={() => setShowShortcuts(false)}>
                Schließen
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ResultTable;
