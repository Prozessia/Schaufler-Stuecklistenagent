"use client";

import { useCallback, useEffect, useMemo, useRef, type MouseEvent as ReactMouseEvent } from "react";
import { AgGridReact } from "ag-grid-react";
import {
  AllCommunityModule,
  ModuleRegistry,
  type CellClassParams,
  type CellKeyDownEvent,
  type CellValueChangedEvent,
  type ColDef,
  type GridApi,
  type GridReadyEvent,
  type IRowNode,
  type ValueGetterParams,
} from "ag-grid-community";

import type {
  CellEditPayload,
  CellResult,
  JobResult,
  TemplateColumnResult,
} from "@/lib/api";

// ag-grid v34 requires explicit module registration (once).
ModuleRegistry.registerModules([AllCommunityModule]);

export type GridRow = {
  __rowIndex: number;
  __sheetRow: number;
  __cells: Record<string, CellResult>;
  /** Lossless footer/header/note flag — advisory, the row is still present. */
  __nonData: boolean;
  __nonDataReasons: string[];
  /** flat field -> display value, what ag-grid edits */
  [field: string]: unknown;
};

interface ReviewGridProps {
  result: JobResult;
  columns: TemplateColumnResult[];
  /** Show only rows that contain a yellow/red cell. */
  reviewOnly: boolean;
  onCellEdit: (edit: CellEditPayload) => void;
  onCellSelect: (cell: CellResult | null, sheetRow: number) => void;
  onGridApi?: (api: GridApi<GridRow>) => void;
  /** Reviewer pressed Entf on selected/focused rows — request exclusion. */
  onRequestDelete?: (rowIndices: number[]) => void;
  /** When false, columns that are empty/neutral across all rows are hidden. */
  showAllColumns: boolean;
  /** Fields that carry content in at least one row (value or non-neutral). */
  populatedFields: Set<string>;
  /** Multi-cell selection mode: click toggles a cell, drag selects a range. */
  selectionMode?: boolean;
  /** Selected cell keys (`${rowIndex}:${field}`) for highlighting. */
  selectedCellKeys?: Set<string>;
  /** Replace the multi-selection (click toggles one, drag adds a rectangle). */
  onSelectionChange?: (next: Set<string>) => void;
}

/** Resolve the ag-grid cell under a DOM node to its `${rowId}:${colId}` parts. */
function cellCoordsFromTarget(
  target: HTMLElement | null,
  api: GridApi<GridRow> | null
): { rowId: string; colId: string } | null {
  const cellEl = target?.closest(".ag-cell");
  const rowEl = target?.closest(".ag-row");
  if (!cellEl || !rowEl) return null;
  const colId = cellEl.getAttribute("col-id");
  if (!colId || colId.startsWith("ag-Grid")) return null;
  let rowId = rowEl.getAttribute("row-id");
  if (rowId == null) {
    const ri = rowEl.getAttribute("row-index");
    const node = ri != null ? api?.getDisplayedRowAtIndex(Number(ri)) : null;
    rowId = node?.id ?? null;
  }
  return rowId != null ? { rowId, colId } : null;
}

function displayValue(cell?: CellResult | null): string {
  if (!cell) return "";
  return cell.transformed_value ?? cell.raw_value ?? "";
}

function excelColIndex(column: string): number {
  let index = 0;
  for (const ch of column.trim().toUpperCase()) {
    const code = ch.charCodeAt(0);
    if (code >= 65 && code <= 90) index = index * 26 + (code - 64);
  }
  return index;
}

function buildRows(result: JobResult, columns: TemplateColumnResult[]): GridRow[] {
  return result.rows.map((row, position) => {
    const cells: Record<string, CellResult> = {};
    for (const cell of row.cells) cells[cell.target_field] = cell;

    const gridRow: GridRow = {
      __rowIndex: row.row_index,
      __sheetRow: (result.template?.data_start_row ?? 1) + position,
      __cells: cells,
      __nonData: row.non_data ?? false,
      __nonDataReasons: row.non_data_reasons ?? [],
    };
    for (const column of columns) {
      gridRow[column.field] = displayValue(cells[column.field]);
    }
    return gridRow;
  });
}

function rowHasOpenCell(row: GridRow): boolean {
  return Object.values(row.__cells).some(
    (cell) => cell.classification === "yellow" || cell.classification === "red"
  );
}

export function ReviewGrid({
  result,
  columns,
  reviewOnly,
  onCellEdit,
  onCellSelect,
  onGridApi,
  onRequestDelete,
  showAllColumns,
  populatedFields,
  selectionMode = false,
  selectedCellKeys,
  onSelectionChange,
}: ReviewGridProps) {
  const apiRef = useRef<GridApi<GridRow> | null>(null);
  const reviewOnlyRef = useRef(reviewOnly);
  reviewOnlyRef.current = reviewOnly;
  const onRequestDeleteRef = useRef(onRequestDelete);
  onRequestDeleteRef.current = onRequestDelete;
  const selectionModeRef = useRef(selectionMode);
  selectionModeRef.current = selectionMode;
  const selectedKeysRef = useRef(selectedCellKeys);
  selectedKeysRef.current = selectedCellKeys;
  const onSelectionChangeRef = useRef(onSelectionChange);
  onSelectionChangeRef.current = onSelectionChange;
  // Drag-to-select state: anchor cell, the selection snapshot at drag start,
  // and whether the pointer actually moved (drag) vs. a plain click.
  const dragRef = useRef<{
    anchor: { rowId: string; colId: string };
    base: Set<string>;
    dragged: boolean;
  } | null>(null);

  const orderedRowIds = useCallback((): string[] => {
    const ids: string[] = [];
    apiRef.current?.forEachNodeAfterFilterAndSort((node) => {
      if (node.id != null) ids.push(node.id);
    });
    return ids;
  }, []);

  const orderedColIds = useCallback(
    (): string[] =>
      (apiRef.current?.getAllDisplayedColumns() ?? [])
        .map((col) => col.getColId())
        .filter((id) => !id.startsWith("ag-Grid")),
    []
  );

  const rectangleKeys = useCallback(
    (a: { rowId: string; colId: string }, b: { rowId: string; colId: string }): string[] => {
      const rows = orderedRowIds();
      const cols = orderedColIds();
      const r1 = rows.indexOf(a.rowId);
      const r2 = rows.indexOf(b.rowId);
      const c1 = cols.indexOf(a.colId);
      const c2 = cols.indexOf(b.colId);
      if (r1 < 0 || r2 < 0 || c1 < 0 || c2 < 0) return [];
      const keys: string[] = [];
      for (let r = Math.min(r1, r2); r <= Math.max(r1, r2); r += 1) {
        for (let c = Math.min(c1, c2); c <= Math.max(c1, c2); c += 1) {
          keys.push(`${rows[r]}:${cols[c]}`);
        }
      }
      return keys;
    },
    [orderedRowIds, orderedColIds]
  );

  // End a drag on mouseup anywhere; a non-dragged press toggles the single cell.
  useEffect(() => {
    const onUp = () => {
      const state = dragRef.current;
      dragRef.current = null;
      if (!state || !selectionModeRef.current) return;
      if (!state.dragged) {
        const key = `${state.anchor.rowId}:${state.anchor.colId}`;
        const next = new Set(selectedKeysRef.current ?? []);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        onSelectionChangeRef.current?.(next);
      }
      document.body.classList.remove("workspace-resize-active");
    };
    window.addEventListener("mouseup", onUp);
    return () => window.removeEventListener("mouseup", onUp);
  }, []);

  const onGridMouseDown = useCallback((event: ReactMouseEvent) => {
    if (!selectionModeRef.current || event.button !== 0) return;
    const cell = cellCoordsFromTarget(event.target as HTMLElement, apiRef.current);
    if (!cell) return;
    event.preventDefault(); // suppress native text selection while dragging
    dragRef.current = {
      anchor: cell,
      base: new Set(selectedKeysRef.current ?? []),
      dragged: false,
    };
  }, []);

  const onGridMouseOver = useCallback(
    (event: ReactMouseEvent) => {
      const state = dragRef.current;
      if (!selectionModeRef.current || !state || event.buttons !== 1) return;
      const cell = cellCoordsFromTarget(event.target as HTMLElement, apiRef.current);
      if (!cell) return;
      state.dragged = true;
      const next = new Set(state.base);
      for (const key of rectangleKeys(state.anchor, cell)) next.add(key);
      onSelectionChangeRef.current?.(next);
    },
    [rectangleKeys]
  );

  const rowData = useMemo(() => buildRows(result, columns), [result, columns]);

  const columnDefs = useMemo<ColDef<GridRow>[]>(() => {
    const sorted = [...columns].sort(
      (a, b) => excelColIndex(a.column) - excelColIndex(b.column)
    );
    const dataCols: ColDef<GridRow>[] = sorted.map((column) => ({
      headerName: column.column || column.field,
      headerTooltip: column.header_label || column.field,
      field: column.field,
      editable: true,
      // Hide columns that are empty/neutral across all rows unless the user opted
      // into the full layout. Required (key) fields stay pinned + always visible.
      hide: !showAllColumns && !column.required && !populatedFields.has(column.field),
      pinned: column.required ? "left" : undefined,
      minWidth: 90,
      width: column.width ? Math.max(96, Math.round(column.width * 7 + 12)) : 140,
      valueGetter: (params: ValueGetterParams<GridRow>) =>
        params.data ? (params.data[column.field] as string) : "",
      cellClassRules: {
        "ag-cell-green": (p: CellClassParams<GridRow>) =>
          p.data?.__cells[column.field]?.classification === "green",
        "ag-cell-manual": (p: CellClassParams<GridRow>) =>
          p.data?.__cells[column.field]?.classification === "manual_confirmed",
        "ag-cell-yellow": (p: CellClassParams<GridRow>) =>
          p.data?.__cells[column.field]?.classification === "yellow",
        "ag-cell-red": (p: CellClassParams<GridRow>) =>
          p.data?.__cells[column.field]?.classification === "red",
        "ag-cell-multiselect": (p: CellClassParams<GridRow>) =>
          !!selectedKeysRef.current?.has(`${p.data?.__rowIndex}:${column.field}`),
      },
      tooltipValueGetter: (p) => {
        const base = p.data?.__cells[column.field]?.reasoning || "";
        if (p.data?.__nonData) {
          const note = `Mögliche Kopf-/Fußzeile oder Notiz – bitte prüfen (${p.data.__nonDataReasons.join(
            ", "
          )})`;
          return base ? `${note}\n${base}` : note;
        }
        return base || undefined;
      },
    }));
    return dataCols;
  }, [columns, showAllColumns, populatedFields]);

  // ag-grid v34 row-selection (object API). Adds a managed checkbox column on the
  // left; the string "multiple" API + per-column checkboxSelection is deprecated and
  // does not register selections, which is why row delete appeared to do nothing.
  const rowSelection = useMemo(
    () =>
      ({
        mode: "multiRow",
        checkboxes: true,
        headerCheckbox: true,
        enableClickSelection: false,
      }) as const,
    []
  );
  const selectionColumnDef = useMemo<ColDef<GridRow>>(
    () => ({ pinned: "left", width: 44, maxWidth: 44, resizable: false }),
    []
  );

  const defaultColDef = useMemo<ColDef<GridRow>>(
    () => ({
      sortable: true,
      filter: true,
      resizable: true,
      singleClickEdit: false,
    }),
    []
  );

  // Lossless footer/header/note rows: tint the row so the reviewer notices it.
  // The row stays fully present and editable — this is advisory only.
  const rowClassRules = useMemo(
    () => ({
      "review-grid-row--non-data": (p: { data?: GridRow }) =>
        Boolean(p.data?.__nonData),
    }),
    []
  );

  const onGridReady = useCallback(
    (event: GridReadyEvent<GridRow>) => {
      apiRef.current = event.api;
      onGridApi?.(event.api);
    },
    [onGridApi]
  );

  // External filter: review-only shows rows with at least one yellow/red cell.
  const isExternalFilterPresent = useCallback(() => reviewOnlyRef.current, []);
  const doesExternalFilterPass = useCallback(
    (node: IRowNode<GridRow>) => (node.data ? rowHasOpenCell(node.data) : true),
    []
  );

  useEffect(() => {
    apiRef.current?.onFilterChanged();
  }, [reviewOnly]);

  // Repaint highlight when the multi-selection changes (refreshes visible cells).
  useEffect(() => {
    apiRef.current?.refreshCells({ force: true });
  }, [selectedCellKeys]);

  // Entf on the grid requests exclusion of the selected rows (or, if none are
  // selected, the focused row). Ignored while a cell is being edited.
  const onCellKeyDown = useCallback((event: CellKeyDownEvent<GridRow>) => {
    const kbEvent = event.event as KeyboardEvent | null;
    if (!kbEvent || kbEvent.key !== "Delete") return;
    if (event.api.getEditingCells().length > 0) return;

    const selected = event.api
      .getSelectedNodes()
      .map((node) => node.data?.__rowIndex)
      .filter((index): index is number => typeof index === "number");
    const rows =
      selected.length > 0
        ? selected
        : event.data
          ? [event.data.__rowIndex]
          : [];
    if (rows.length > 0) onRequestDeleteRef.current?.(rows);
  }, []);

  const onCellValueChanged = useCallback(
    (event: CellValueChangedEvent<GridRow>) => {
      const field = event.colDef.field;
      if (!field || !event.data) return;
      const corrected = String(event.newValue ?? "");

      // Optimistically reflect the manual override locally (colour + value).
      // Replace the cell with a clone instead of mutating it in place — the
      // original object is owned by the React Query cache.
      const cell = event.data.__cells[field];
      if (cell) {
        event.data.__cells[field] = {
          ...cell,
          transformed_value: corrected,
          classification: "manual_confirmed",
          final_status: "manual_confirmed",
          method: "manual_override",
        };
      }
      event.api.refreshCells({ rowNodes: [event.node], columns: [field], force: true });

      onCellEdit({
        row_index: event.data.__rowIndex,
        target_field: field,
        corrected_value: corrected,
      });
    },
    [onCellEdit]
  );

  return (
    <div
      className={`ag-theme-quartz review-grid${selectionMode ? " review-grid--selecting" : ""}`}
      onMouseDown={onGridMouseDown}
      onMouseOver={onGridMouseOver}
    >
      <AgGridReact<GridRow>
        theme="legacy"
        rowData={rowData}
        columnDefs={columnDefs}
        defaultColDef={defaultColDef}
        rowClassRules={rowClassRules}
        onGridReady={onGridReady}
        getRowId={(p) => String(p.data.__rowIndex)}
        rowHeight={28}
        headerHeight={32}
        animateRows={false}
        suppressColumnVirtualisation={false}
        isExternalFilterPresent={isExternalFilterPresent}
        doesExternalFilterPass={doesExternalFilterPass}
        rowSelection={rowSelection}
        selectionColumnDef={selectionColumnDef}
        onCellKeyDown={onCellKeyDown}
        onCellValueChanged={onCellValueChanged}
        undoRedoCellEditing
        undoRedoCellEditingLimit={50}
        onCellClicked={(e) => {
          // Selection is handled by the drag handlers; here we only keep the
          // PDF source in sync with the clicked cell.
          const field = e.colDef.field;
          const cell = field && e.data ? e.data.__cells[field] ?? null : null;
          onCellSelect(cell, e.data?.__sheetRow ?? 0);
        }}
        stopEditingWhenCellsLoseFocus
        enableCellTextSelection
        tooltipShowDelay={400}
      />
    </div>
  );
}

export default ReviewGrid;
