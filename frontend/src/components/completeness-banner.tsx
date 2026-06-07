"use client";

import { ShieldCheck, ShieldAlert } from "lucide-react";
import type { JobResult } from "@/lib/api";

interface CompletenessBannerProps {
  result: JobResult;
}

/**
 * ZDL-1: honestly communicates whether output completeness is guaranteed.
 * Scanned / Vision-only PDFs cannot be guaranteed (the position set is derived
 * from the extraction itself), so the operator must be told — not lulled.
 */
export function CompletenessBanner({ result }: CompletenessBannerProps) {
  const guaranteed = result.completeness_guaranteed;

  const styles = guaranteed
    ? "border-[hsl(var(--status-green))]/40 bg-[hsl(var(--status-green))]/10 text-foreground"
    : "border-[hsl(var(--status-yellow))]/50 bg-[hsl(var(--status-yellow))]/10 text-foreground";

  const Icon = guaranteed ? ShieldCheck : ShieldAlert;
  const iconColor = guaranteed
    ? "text-[hsl(var(--status-green))]"
    : "text-[hsl(var(--status-yellow))]";

  const title = guaranteed
    ? "Vollständigkeit abgesichert"
    : "Vollständigkeit NICHT garantiert — manuelle Prüfung nötig";

  const reason =
    result.completeness_reason ||
    (guaranteed
      ? "Positions-Set gegen den PDF-Text-Layer abgesichert."
      : "Kein unabhängiger Anker für die Positions-Vollständigkeit.");

  return (
    <div
      className={`flex items-start gap-3 rounded-lg border p-4 ${styles}`}
      role="status"
    >
      <Icon className={`mt-0.5 h-5 w-5 shrink-0 ${iconColor}`} />
      <div className="space-y-1">
        <p className="text-sm font-semibold">{title}</p>
        <p className="text-xs text-muted-foreground">{reason}</p>
        {result.expected_position_count > 0 && (
          <p className="text-xs text-muted-foreground">
            Erwartete Positionen: {result.expected_position_count}
            {result.guard_basis ? ` · Basis: ${result.guard_basis}` : ""}
          </p>
        )}
      </div>
    </div>
  );
}
