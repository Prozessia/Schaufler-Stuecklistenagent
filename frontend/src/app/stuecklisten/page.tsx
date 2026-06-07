"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  ChevronLeft,
  ChevronRight,
  Download,
  ExternalLink,
  RotateCcw,
  Search,
  Trash2,
} from "lucide-react";

import { DashboardShell } from "@/components/dashboard/dashboard-shell";
import { StatusBadge, type StatusTone } from "@/components/dashboard/status-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  deleteJob,
  getExportUrl,
  listJobs,
  restoreJob,
  type JobSummary,
} from "@/lib/api";
import { STUECKLISTEN_ROUTE, WORKSPACE_ROUTE } from "@/lib/routes";

const PAGE_SIZE = 15;

const STATUS_META: Record<string, { label: string; tone: StatusTone }> = {
  completed: { label: "Fertig", tone: "green" },
  processing: { label: "In Verarbeitung", tone: "yellow" },
  pending: { label: "Wartet", tone: "blue" },
  failed: { label: "Fehlgeschlagen", tone: "red" },
};

type SortKey = "date" | "customer" | "automation";

const dateFmt = new Intl.DateTimeFormat("de-DE", {
  dateStyle: "medium",
  timeStyle: "short",
});

function automationRate(job: JobSummary): number {
  return job.total_cells > 0 ? job.green_count / job.total_cells : 0;
}

export default function StuecklistenPage() {
  const router = useRouter();
  const queryClient = useQueryClient();

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("date");
  const [page, setPage] = useState(0);

  const jobsQuery = useQuery({
    queryKey: ["jobs", { includeArchived }],
    queryFn: () => listJobs({ includeArchived }),
    refetchOnWindowFocus: false,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["jobs"] });

  const archiveMut = useMutation({
    mutationFn: (id: string) => deleteJob(id),
    onSuccess: invalidate,
  });
  const purgeMut = useMutation({
    mutationFn: (id: string) => deleteJob(id, { purge: true }),
    onSuccess: invalidate,
  });
  const restoreMut = useMutation({
    mutationFn: (id: string) => restoreJob(id),
    onSuccess: invalidate,
  });

  const busy = archiveMut.isPending || purgeMut.isPending || restoreMut.isPending;

  const filtered = useMemo(() => {
    const all = jobsQuery.data ?? [];
    const needle = search.trim().toLowerCase();
    const rows = all.filter((j) => {
      if (statusFilter && j.status !== statusFilter) return false;
      if (
        needle &&
        !j.filename.toLowerCase().includes(needle) &&
        !j.customer.toLowerCase().includes(needle)
      ) {
        return false;
      }
      return true;
    });
    rows.sort((a, b) => {
      if (sortKey === "customer") return a.customer.localeCompare(b.customer);
      if (sortKey === "automation") return automationRate(b) - automationRate(a);
      return b.created_at - a.created_at;
    });
    return rows;
  }, [jobsQuery.data, search, statusFilter, sortKey]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pageRows = filtered.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);

  const handleArchive = (job: JobSummary) => {
    if (window.confirm(`„${job.filename}“ archivieren? Sie kann jederzeit wiederhergestellt werden.`)) {
      archiveMut.mutate(job.job_id);
    }
  };
  const handlePurge = (job: JobSummary) => {
    if (
      window.confirm(
        `„${job.filename}“ ENDGÜLTIG löschen?\nDie Datenbank-Eintragung und die Dateien (Upload + Export) werden entfernt. Das kann nicht rückgängig gemacht werden.`
      )
    ) {
      purgeMut.mutate(job.job_id);
    }
  };

  const errorMessage = jobsQuery.isError
    ? jobsQuery.error instanceof Error
      ? jobsQuery.error.message
      : "Vorgaenge konnten nicht geladen werden."
    : null;

  return (
    <DashboardShell currentPath={STUECKLISTEN_ROUTE}>
      <div className="mx-auto max-w-7xl px-5 py-8 sm:px-6 lg:px-10">
        <header className="border-b border-border pb-5">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[hsl(var(--primary))]">
            Stuecklisten
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-foreground">
            Verarbeitete Stuecklisten
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {filtered.length} {filtered.length === 1 ? "Vorgang" : "Vorgaenge"}
            {includeArchived ? " (inkl. archivierte)" : ""}
          </p>
        </header>

        {/* Filter bar */}
        <div className="mt-5 flex flex-col gap-2.5 sm:flex-row sm:items-center">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(0);
              }}
              placeholder="Datei oder Kunde suchen..."
              className="h-10 pl-9"
            />
          </div>
          <select
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value);
              setPage(0);
            }}
            className="h-10 rounded-lg border border-input bg-card px-3 text-sm text-foreground focus-visible:border-ring focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            <option value="">Alle Status</option>
            <option value="completed">Fertig</option>
            <option value="processing">In Verarbeitung</option>
            <option value="pending">Wartet</option>
            <option value="failed">Fehlgeschlagen</option>
          </select>
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            className="h-10 rounded-lg border border-input bg-card px-3 text-sm text-foreground focus-visible:border-ring focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            <option value="date">Neueste zuerst</option>
            <option value="customer">Kunde (A–Z)</option>
            <option value="automation">Automatisierungsgrad</option>
          </select>
          <label className="flex h-10 shrink-0 cursor-pointer items-center gap-2 rounded-lg border border-input bg-card px-3 text-sm text-muted-foreground">
            <input
              type="checkbox"
              checked={includeArchived}
              onChange={(e) => {
                setIncludeArchived(e.target.checked);
                setPage(0);
              }}
              className="h-4 w-4 accent-[hsl(var(--primary))]"
            />
            Archivierte
          </label>
        </div>

        {/* Table */}
        <section className="mt-4 overflow-hidden rounded-xl border border-border bg-card">
          {jobsQuery.isLoading ? (
            <div className="px-5 py-14 text-center text-sm text-muted-foreground">
              Vorgaenge werden geladen...
            </div>
          ) : errorMessage ? (
            <div className="px-5 py-14 text-center text-sm text-destructive">{errorMessage}</div>
          ) : filtered.length === 0 ? (
            <div className="px-5 py-14 text-center text-sm text-muted-foreground">
              Keine Stuecklisten gefunden.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/80">
                    <th className="h-11 px-5">Datei</th>
                    <th className="h-11 px-3">Kunde</th>
                    <th className="h-11 px-3">Status</th>
                    <th className="h-11 px-3">Ampel (G/Ge/R)</th>
                    <th className="h-11 px-3">Zeilen</th>
                    <th className="h-11 px-3">Datum</th>
                    <th className="h-11 px-5 text-right">Aktionen</th>
                  </tr>
                </thead>
                <tbody>
                  {pageRows.map((job) => {
                    const meta = STATUS_META[job.status] ?? {
                      label: job.status,
                      tone: "neutral" as StatusTone,
                    };
                    return (
                      <tr
                        key={job.job_id}
                        className="border-b border-border/60 transition-colors last:border-0 hover:bg-[hsl(var(--primary))]/5"
                      >
                        <td className="px-5 py-3">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-[13px] font-medium text-foreground">
                              {job.filename}
                            </span>
                            {job.archived && (
                              <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                                archiviert
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="px-3 py-3 text-foreground">{job.customer || "—"}</td>
                        <td className="px-3 py-3">
                          <StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>
                        </td>
                        <td className="px-3 py-3">
                          {job.total_cells > 0 ? (
                            <span className="tabular-nums text-xs">
                              <span className="text-emerald-600">{job.green_count}</span>
                              <span className="text-muted-foreground"> / </span>
                              <span className="text-amber-600">{job.yellow_count}</span>
                              <span className="text-muted-foreground"> / </span>
                              <span className="text-red-600">{job.red_count}</span>
                            </span>
                          ) : (
                            <span className="text-xs text-muted-foreground">—</span>
                          )}
                        </td>
                        <td className="px-3 py-3 tabular-nums text-foreground">
                          {job.total_rows || "—"}
                        </td>
                        <td className="px-3 py-3 text-muted-foreground">
                          {job.created_at ? dateFmt.format(new Date(job.created_at * 1000)) : "—"}
                        </td>
                        <td className="px-5 py-3">
                          <div className="flex items-center justify-end gap-1">
                            {job.status === "completed" && (
                              <>
                                <button
                                  type="button"
                                  title="Im Workspace oeffnen"
                                  onClick={() =>
                                    router.push(`${WORKSPACE_ROUTE}?jobId=${encodeURIComponent(job.job_id)}`)
                                  }
                                  className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-[hsl(var(--primary))]"
                                >
                                  <ExternalLink className="h-4 w-4" />
                                </button>
                                <a
                                  href={getExportUrl(job.job_id)}
                                  title="Excel-Export"
                                  className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                                >
                                  <Download className="h-4 w-4" />
                                </a>
                              </>
                            )}
                            {job.archived ? (
                              <>
                                <button
                                  type="button"
                                  title="Wiederherstellen"
                                  disabled={busy}
                                  onClick={() => restoreMut.mutate(job.job_id)}
                                  className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
                                >
                                  <RotateCcw className="h-4 w-4" />
                                </button>
                                <button
                                  type="button"
                                  title="Endgueltig loeschen"
                                  disabled={busy}
                                  onClick={() => handlePurge(job)}
                                  className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
                                >
                                  <Trash2 className="h-4 w-4" />
                                </button>
                              </>
                            ) : (
                              <button
                                type="button"
                                title="Archivieren"
                                disabled={busy}
                                onClick={() => handleArchive(job)}
                                className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
                              >
                                <Archive className="h-4 w-4" />
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Pagination */}
        {filtered.length > PAGE_SIZE && (
          <div className="mt-4 flex items-center justify-between">
            <p className="text-xs text-muted-foreground">
              {safePage * PAGE_SIZE + 1}–{Math.min((safePage + 1) * PAGE_SIZE, filtered.length)} von{" "}
              {filtered.length}
            </p>
            <div className="flex items-center gap-1">
              <Button
                variant="outline"
                size="sm"
                disabled={safePage === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                className="h-8"
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <span className="px-2 text-xs tabular-nums text-muted-foreground">
                {safePage + 1} / {pageCount}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={safePage >= pageCount - 1}
                onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
                className="h-8"
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}
      </div>
    </DashboardShell>
  );
}
