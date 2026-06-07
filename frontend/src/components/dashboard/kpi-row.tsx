import { AlertTriangle, CheckCircle2, Clock, Files, type LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import type { DashboardKpi } from "@/types/dashboard";

interface KpiMeta {
  Icon: LucideIcon;
  border: string;
  icon: string;
}

// Border + icon carry the status colour; the number itself stays neutral
// (foreground) — the same monochrome-with-status-accent language as the
// reference KPI cards.
const META: Record<string, KpiMeta> = {
  total: {
    Icon: Files,
    border: "border-[hsl(var(--primary))]/30",
    icon: "text-[hsl(var(--primary))]",
  },
  open: {
    Icon: Clock,
    border: "border-amber-500/40",
    icon: "text-amber-500",
  },
  finished: {
    Icon: CheckCircle2,
    border: "border-emerald-500/40",
    icon: "text-emerald-600",
  },
  failed: {
    Icon: AlertTriangle,
    border: "border-red-500/40",
    icon: "text-red-500",
  },
};

const FALLBACK: KpiMeta = {
  Icon: Files,
  border: "border-border",
  icon: "text-muted-foreground",
};

interface DashboardKpiRowProps {
  items: DashboardKpi[];
}

export function DashboardKpiRow({ items }: DashboardKpiRowProps) {
  return (
    <section aria-label="Kennzahlen" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {items.map((item) => {
        const meta = META[item.id] ?? FALLBACK;
        const Icon = meta.Icon;
        return (
          <div
            key={item.id}
            className={cn("rounded-xl border bg-card p-5 transition-colors", meta.border)}
          >
            <div className="flex items-start justify-between">
              <div className="space-y-1">
                <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  {item.label}
                </p>
                <p className="text-3xl font-bold tabular-nums tracking-tight text-foreground">
                  {item.value}
                </p>
              </div>
              <div className="rounded-xl bg-muted/60 p-2.5">
                <Icon className={cn("h-5 w-5", meta.icon)} />
              </div>
            </div>
          </div>
        );
      })}
    </section>
  );
}
