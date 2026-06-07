"use client";

import { ArrowRight } from "lucide-react";

import { cn } from "@/lib/utils";
import { StatusBadge, type StatusTone } from "@/components/dashboard/status-badge";
import type { DashboardFileStatus, DashboardRecentFile } from "@/types/dashboard";

const statusTone: Record<DashboardFileStatus, StatusTone> = {
  Neu: "blue",
  "In Prüfung": "yellow",
  Fertig: "green",
};

const progressColor: Record<DashboardFileStatus, string> = {
  Neu: "bg-[hsl(var(--primary))]",
  "In Prüfung": "bg-amber-500",
  Fertig: "bg-emerald-500",
};

interface RecentFilesPanelProps {
  files: DashboardRecentFile[];
  onOpenFile: (fileId: string) => void;
  isLoading?: boolean;
  errorMessage?: string | null;
  emptyMessage?: string;
}

export function RecentFilesPanel({
  files,
  onOpenFile,
  isLoading = false,
  errorMessage = null,
  emptyMessage = "Keine Dateien fuer den aktuellen Suchbegriff gefunden.",
}: RecentFilesPanelProps) {
  const showTable = !isLoading && !errorMessage && files.length > 0;

  return (
    <section className="overflow-hidden rounded-xl border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-5 py-3.5">
        <h2 className="text-sm font-semibold text-foreground">Zuletzt verarbeitet</h2>
        {showTable && (
          <span className="text-xs text-muted-foreground">{files.length} Vorgaenge</span>
        )}
      </div>

      {isLoading ? (
        <div className="px-5 py-14 text-center text-sm text-muted-foreground">
          Vorgaenge werden geladen...
        </div>
      ) : errorMessage ? (
        <div className="px-5 py-14 text-center text-sm text-destructive">{errorMessage}</div>
      ) : files.length === 0 ? (
        <div className="px-5 py-14 text-center text-sm text-muted-foreground">{emptyMessage}</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/80">
                <th className="h-11 px-5">Datei</th>
                <th className="h-11 px-3">Kunde</th>
                <th className="h-11 px-3">Status</th>
                <th className="h-11 px-3">Fortschritt</th>
                <th className="h-11 px-5" />
              </tr>
            </thead>
            <tbody>
              {files.map((file) => (
                <tr
                  key={file.id}
                  className="border-b border-border/60 transition-colors last:border-0 hover:bg-[hsl(var(--primary))]/5"
                >
                  <td className="px-5 py-3">
                    <div className="font-mono text-[13px] font-medium text-foreground">
                      {file.fileName}
                    </div>
                    {file.description && (
                      <div className="mt-0.5 text-xs text-muted-foreground">
                        {file.description}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-3 text-foreground">{file.customer}</td>
                  <td className="px-3 py-3">
                    <StatusBadge tone={statusTone[file.status]}>{file.status}</StatusBadge>
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-muted">
                        <div
                          className={cn("h-full rounded-full", progressColor[file.status])}
                          style={{ width: `${file.progressPercent}%` }}
                        />
                      </div>
                      <span className="tabular-nums text-xs text-muted-foreground">
                        {file.progressPercent}%
                      </span>
                    </div>
                  </td>
                  <td className="px-5 py-3 text-right">
                    <button
                      type="button"
                      onClick={() => onOpenFile(file.id)}
                      className="inline-flex items-center gap-1 text-sm font-medium text-[hsl(var(--primary))] transition-colors hover:underline focus-visible:outline-none focus-visible:underline"
                    >
                      Oeffnen
                      <ArrowRight className="h-3.5 w-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
