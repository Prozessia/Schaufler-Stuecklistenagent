"use client";

import { useQuery } from "@tanstack/react-query";
import { Activity, CheckCircle2, Database, FileSpreadsheet, PencilLine } from "lucide-react";

import { DashboardShell } from "@/components/dashboard/dashboard-shell";
import { StatusBadge } from "@/components/dashboard/status-badge";
import {
  getStatsByCustomer,
  getStatsCorrections,
  getStatsOverview,
  getStatsTimeseries,
  type StatsCustomerRow,
  type StatsTimeseriesPoint,
} from "@/lib/api";
import { STATISTIK_ROUTE } from "@/lib/routes";

const percentFmt = new Intl.NumberFormat("de-DE", {
  style: "percent",
  maximumFractionDigits: 1,
});

const numberFmt = new Intl.NumberFormat("de-DE");

function pct(value: number): string {
  return percentFmt.format(value || 0);
}

function maxOf(values: number[]): number {
  return Math.max(1, ...values);
}

function KpiCard({
  label,
  value,
  detail,
  icon: Icon,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  icon: typeof FileSpreadsheet;
  tone: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            {label}
          </p>
          <p className="mt-1 text-3xl font-bold tracking-tight text-foreground tabular-nums">
            {value}
          </p>
        </div>
        <div className={`rounded-xl bg-muted/60 p-2.5 ${tone}`}>
          <Icon className="h-5 w-5" />
        </div>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">{detail}</p>
    </div>
  );
}

function DistributionBar({ green, yellow, red, neutral, manual }: {
  green: number;
  yellow: number;
  red: number;
  neutral: number;
  manual: number;
}) {
  const total = green + yellow + red + neutral + manual;
  const parts = [
    { label: "Gruen", value: green, className: "bg-emerald-500" },
    { label: "Gelb", value: yellow, className: "bg-amber-500" },
    { label: "Rot", value: red, className: "bg-red-500" },
    { label: "Manuell", value: manual, className: "bg-sky-500" },
    { label: "Neutral", value: neutral, className: "bg-muted-foreground/35" },
  ];

  return (
    <div>
      <div className="flex h-3 overflow-hidden rounded-full bg-muted">
        {parts.map((part) => (
          <div
            key={part.label}
            title={`${part.label}: ${part.value}`}
            className={part.className}
            style={{ width: `${total ? (part.value / total) * 100 : 0}%` }}
          />
        ))}
      </div>
      <div className="mt-4 grid gap-2 sm:grid-cols-5">
        {parts.map((part) => (
          <div key={part.label} className="rounded-lg border border-border/70 px-3 py-2">
            <p className="text-[11px] text-muted-foreground">{part.label}</p>
            <p className="text-sm font-semibold text-foreground tabular-nums">
              {numberFmt.format(part.value)}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

function TimeseriesChart({ data }: { data: StatsTimeseriesPoint[] }) {
  const maxJobs = maxOf(data.map((d) => d.jobs));
  const points = data.map((d, index) => {
    const x = data.length <= 1 ? 50 : (index / (data.length - 1)) * 100;
    const y = 92 - (d.automation_rate || 0) * 76;
    return `${x},${y}`;
  });

  if (data.length === 0) {
    return <div className="py-16 text-center text-sm text-muted-foreground">Noch keine Zeitreihe.</div>;
  }

  return (
    <div className="space-y-5">
      <div className="h-52 rounded-xl border border-border/70 bg-muted/20 px-4 py-5">
        <div className="flex h-full items-end gap-2">
          {data.map((point) => (
            <div key={point.period} className="flex min-w-0 flex-1 flex-col items-center gap-2">
              <div className="flex h-36 w-full items-end justify-center">
                <div
                  className="w-full max-w-10 rounded-t bg-[hsl(var(--primary))]/70"
                  style={{ height: `${Math.max(6, (point.jobs / maxJobs) * 100)}%` }}
                  title={`${point.period}: ${point.jobs} BOMs`}
                />
              </div>
              <span className="max-w-full truncate text-[10px] text-muted-foreground">
                {point.period}
              </span>
            </div>
          ))}
        </div>
      </div>
      <svg viewBox="0 0 100 100" className="h-24 w-full overflow-visible rounded-xl border border-border/70 bg-card p-3" preserveAspectRatio="none">
        <polyline fill="none" stroke="currentColor" strokeWidth="2" points={points.join(" ")} className="text-emerald-500" />
        {data.map((point, index) => {
          const x = data.length <= 1 ? 50 : (index / (data.length - 1)) * 100;
          const y = 92 - (point.automation_rate || 0) * 76;
          return <circle key={point.period} cx={x} cy={y} r="1.8" className="fill-emerald-500" />;
        })}
      </svg>
      <p className="text-xs text-muted-foreground">
        Balken zeigen verarbeitete BOMs, Linie zeigt Automatisierungsgrad.
      </p>
    </div>
  );
}

function CustomerTable({ rows }: { rows: StatsCustomerRow[] }) {
  if (rows.length === 0) {
    return <div className="py-12 text-center text-sm text-muted-foreground">Noch keine Kundendaten.</div>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-sm">
        <thead>
          <tr className="border-b border-border text-left text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/80">
            <th className="h-11 px-5">Kunde</th>
            <th className="h-11 px-3">BOMs</th>
            <th className="h-11 px-3">Zeilen</th>
            <th className="h-11 px-3">Automatisierung</th>
            <th className="h-11 px-3">Ampel</th>
            <th className="h-11 px-5 text-right">Korrekturen</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.customer} className="border-b border-border/60 last:border-0">
              <td className="px-5 py-3 font-medium text-foreground">{row.customer}</td>
              <td className="px-3 py-3 tabular-nums text-foreground">{row.jobs}</td>
              <td className="px-3 py-3 tabular-nums text-foreground">{row.rows}</td>
              <td className="px-3 py-3">
                <div className="flex items-center gap-2">
                  <div className="h-2 w-28 overflow-hidden rounded-full bg-muted">
                    <div className="h-full bg-emerald-500" style={{ width: `${row.automation_rate * 100}%` }} />
                  </div>
                  <span className="text-xs tabular-nums text-muted-foreground">{pct(row.automation_rate)}</span>
                </div>
              </td>
              <td className="px-3 py-3 text-xs tabular-nums">
                <span className="text-emerald-600">{row.green}</span>
                <span className="text-muted-foreground"> / </span>
                <span className="text-amber-600">{row.yellow}</span>
                <span className="text-muted-foreground"> / </span>
                <span className="text-red-600">{row.red}</span>
              </td>
              <td className="px-5 py-3 text-right tabular-nums text-foreground">{row.corrections}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function StatistikPage() {
  const overviewQuery = useQuery({
    queryKey: ["stats", "overview"],
    queryFn: getStatsOverview,
    refetchOnWindowFocus: false,
  });
  const timeseriesQuery = useQuery({
    queryKey: ["stats", "timeseries", "month"],
    queryFn: () => getStatsTimeseries("month"),
    refetchOnWindowFocus: false,
  });
  const customerQuery = useQuery({
    queryKey: ["stats", "by-customer"],
    queryFn: getStatsByCustomer,
    refetchOnWindowFocus: false,
  });
  const correctionsQuery = useQuery({
    queryKey: ["stats", "corrections"],
    queryFn: getStatsCorrections,
    refetchOnWindowFocus: false,
  });

  const overview = overviewQuery.data;
  const corrections = correctionsQuery.data;
  const loading = overviewQuery.isLoading || timeseriesQuery.isLoading || customerQuery.isLoading;
  const error = overviewQuery.error || timeseriesQuery.error || customerQuery.error || correctionsQuery.error;

  return (
    <DashboardShell currentPath={STATISTIK_ROUTE}>
      <div className="mx-auto max-w-7xl px-5 py-8 sm:px-6 lg:px-10">
        <header className="border-b border-border pb-5">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[hsl(var(--primary))]">
            Statistik
          </p>
          <div className="mt-1 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight text-foreground">
                Auswertung der Verarbeitung
              </h1>
              <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
                Aggregiert aus aktiven Jobs, Summary-Spalten und gespeicherten Korrekturen.
              </p>
            </div>
            {overview && (
              <StatusBadge tone="blue">
                {overview.completed_jobs} / {overview.total_jobs} abgeschlossen
              </StatusBadge>
            )}
          </div>
        </header>

        {loading ? (
          <div className="mt-6 rounded-xl border border-border bg-card px-5 py-14 text-center text-sm text-muted-foreground">
            Statistik wird geladen...
          </div>
        ) : error ? (
          <div className="mt-6 rounded-xl border border-destructive/30 bg-destructive/5 px-5 py-14 text-center text-sm text-destructive">
            Statistik konnte nicht geladen werden.
          </div>
        ) : overview ? (
          <>
            <section className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-3" aria-label="Kennzahlen">
              <KpiCard
                label="Verarbeitete BOMs"
                value={numberFmt.format(overview.completed_jobs)}
                detail={`${numberFmt.format(overview.total_rows)} Zeilen, ${numberFmt.format(overview.total_cells)} Zellen`}
                icon={FileSpreadsheet}
                tone="text-[hsl(var(--primary))]"
              />
              <KpiCard
                label="Automatisierungsgrad"
                value={pct(overview.automation_rate)}
                detail={`${numberFmt.format(overview.green)} gruen uebernommene Zellen`}
                icon={CheckCircle2}
                tone="text-emerald-600"
              />
              <KpiCard
                label="Korrekturen"
                value={numberFmt.format(overview.corrections)}
                detail="Aus dem Feedback-Log fuer den Lernkreislauf"
                icon={PencilLine}
                tone="text-amber-600"
              />
            </section>

            <div className="mt-6 grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
              <section className="rounded-xl border border-border bg-card p-5">
                <div className="mb-5 flex items-center justify-between gap-3">
                  <div>
                    <h2 className="text-base font-semibold text-foreground">Durchsatz und Quote</h2>
                    <p className="text-sm text-muted-foreground">Monatliche BOMs und gruen-Anteil.</p>
                  </div>
                  <Activity className="h-5 w-5 text-muted-foreground" />
                </div>
                <TimeseriesChart data={timeseriesQuery.data ?? []} />
              </section>

              <section className="rounded-xl border border-border bg-card p-5">
                <div className="mb-5 flex items-center justify-between gap-3">
                  <div>
                    <h2 className="text-base font-semibold text-foreground">Statusverteilung</h2>
                    <p className="text-sm text-muted-foreground">Zellklassifikation aller abgeschlossenen Jobs.</p>
                  </div>
                  <Database className="h-5 w-5 text-muted-foreground" />
                </div>
                <DistributionBar
                  green={overview.green}
                  yellow={overview.yellow}
                  red={overview.red}
                  neutral={overview.neutral}
                  manual={overview.manual_confirmed}
                />
              </section>
            </div>

            <div className="mt-6 grid gap-6 xl:grid-cols-[1fr_360px]">
              <section className="overflow-hidden rounded-xl border border-border bg-card">
                <div className="border-b border-border px-5 py-4">
                  <h2 className="text-base font-semibold text-foreground">Nach Kunde</h2>
                  <p className="text-sm text-muted-foreground">Durchsatz, Ampel und Korrekturen je Kunde.</p>
                </div>
                <CustomerTable rows={customerQuery.data ?? []} />
              </section>

              <section className="rounded-xl border border-border bg-card p-5">
                <h2 className="text-base font-semibold text-foreground">Korrekturfelder</h2>
                <p className="text-sm text-muted-foreground">Top-Felder aus corrections.jsonl.</p>
                <div className="mt-4 space-y-3">
                  {(corrections?.by_field ?? []).length === 0 ? (
                    <p className="py-8 text-center text-sm text-muted-foreground">Noch keine Korrekturen.</p>
                  ) : (
                    corrections?.by_field.map((item) => {
                      const max = maxOf((corrections?.by_field ?? []).map((x) => x.count));
                      return (
                        <div key={item.field}>
                          <div className="mb-1 flex justify-between gap-3 text-xs">
                            <span className="truncate text-foreground">{item.field}</span>
                            <span className="tabular-nums text-muted-foreground">{item.count}</span>
                          </div>
                          <div className="h-2 overflow-hidden rounded-full bg-muted">
                            <div className="h-full bg-amber-500" style={{ width: `${(item.count / max) * 100}%` }} />
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </section>
            </div>
          </>
        ) : null}
      </div>
    </DashboardShell>
  );
}
