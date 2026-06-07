"use client";

import type { ReactNode } from "react";
import {
  ArrowLeft,
  Columns3,
  Download,
  FileText,
  Filter,
  Keyboard,
  List,
  RotateCcw,
  RotateCw,
  Save,
} from "lucide-react";

import { cn } from "@/lib/utils";

export interface TopBarProps {
  fileName: string;
  pageLabel: string;
  kpis: { green: number; yellow: number; red: number };
  onBack: () => void;
  onToggleLayout: () => void;
  onOpenList: () => void;
  onOpenFilter: () => void;
  onUndo: () => void;
  onRedo: () => void;
  onSave: () => void;
  onExport: () => void;
  onShowShortcuts?: () => void;
}

interface IconButtonProps {
  label: string;
  onClick: () => void;
  children: ReactNode;
  accent?: boolean;
}

function IconButton({ label, onClick, children, accent = false }: IconButtonProps) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className={cn(
        "inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border transition-all duration-150",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60 focus-visible:ring-offset-2 focus-visible:ring-offset-transparent",
        accent
          ? "border-brand/50 bg-brand text-white shadow-[0_10px_24px_rgba(42,111,176,0.26)] hover:bg-brand-hover"
          : "border-white/10 bg-white/6 text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] hover:bg-white/12"
      )}
    >
      {children}
    </button>
  );
}

interface KpiChipProps {
  label: string;
  value: number;
  dotClassName: string;
}

function KpiChip({ label, value, dotClassName }: KpiChipProps) {
  return (
    <div className="flex items-center gap-2.5 whitespace-nowrap text-sm text-white">
      <span className={cn("h-2.5 w-2.5 rounded-full", dotClassName)} />
      <span className="text-white/62">{label}</span>
      <span className="font-semibold tabular-nums text-white">{value}</span>
    </div>
  );
}

export function TopBar({
  fileName,
  pageLabel,
  kpis,
  onBack,
  onToggleLayout,
  onOpenList,
  onOpenFilter,
  onUndo,
  onRedo,
  onSave,
  onExport,
  onShowShortcuts,
}: TopBarProps) {
  return (
    <header className="brand-industrial-panel fixed inset-x-0 top-0 z-50 h-16 border-b border-white/10 shadow-[0_18px_40px_rgba(15,23,42,0.28)] backdrop-blur-xl">
      <div className="h-full overflow-x-auto">
        <div className="relative grid h-16 min-w-[1180px] grid-cols-[1fr_auto_1fr] items-center gap-4 px-4 sm:px-5">
          <div className="flex min-w-0 items-center gap-2 whitespace-nowrap">
            <IconButton label="Zurueck" onClick={onBack}>
              <ArrowLeft className="h-[18px] w-[18px]" />
            </IconButton>

            <IconButton label="Layout umschalten" onClick={onToggleLayout} accent>
              <Columns3 className="h-[18px] w-[18px]" />
            </IconButton>

            <div className="ml-1 flex min-w-0 items-center gap-2.5 text-sm">
              <FileText className="h-4 w-4 shrink-0 text-white/58" />
              <span className="truncate font-semibold text-white">{fileName}</span>
              <span className="text-white/28">&middot;</span>
              <span className="shrink-0 text-white/58">{pageLabel}</span>
            </div>
          </div>

          <div className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">
            <div className="pointer-events-auto flex h-10 items-center gap-3 rounded-full border border-white/10 bg-white/6 px-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] backdrop-blur-md">
              <KpiChip label="Gruen" value={kpis.green} dotClassName="bg-emerald-400" />
              <KpiChip label="Gelb" value={kpis.yellow} dotClassName="bg-amber-400" />
              <KpiChip label="Rot" value={kpis.red} dotClassName="bg-rose-500" />
            </div>
          </div>

          <div className="col-start-3 flex items-center justify-end gap-2 whitespace-nowrap">
            <IconButton label="Liste oeffnen" onClick={onOpenList}>
              <List className="h-[18px] w-[18px]" />
            </IconButton>
            <IconButton label="Filter umschalten" onClick={onOpenFilter}>
              <Filter className="h-[18px] w-[18px]" />
            </IconButton>
            <IconButton label="Rueckgaengig" onClick={onUndo}>
              <RotateCcw className="h-[18px] w-[18px]" />
            </IconButton>
            <IconButton label="Wiederholen" onClick={onRedo}>
              <RotateCw className="h-[18px] w-[18px]" />
            </IconButton>
            <IconButton label="Speichern" onClick={onSave}>
              <Save className="h-[18px] w-[18px]" />
            </IconButton>
            {onShowShortcuts && (
              <IconButton label="Tastaturkuerzel (F1)" onClick={onShowShortcuts}>
                <Keyboard className="h-[18px] w-[18px]" />
              </IconButton>
            )}

            <button
              type="button"
              aria-label="Export"
              onClick={onExport}
              className="ml-1 inline-flex h-11 shrink-0 items-center gap-2 rounded-[14px] bg-brand px-5 text-sm font-semibold text-white shadow-[0_12px_28px_rgba(42,111,176,0.28)] transition-all duration-150 hover:bg-brand-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60 focus-visible:ring-offset-2 focus-visible:ring-offset-transparent"
            >
              <Download className="h-[18px] w-[18px]" />
              Export
            </button>
          </div>
        </div>
      </div>
    </header>
  );
}

export default TopBar;
