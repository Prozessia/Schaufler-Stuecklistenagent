"use client";

import { Progress } from "@/components/ui/progress";
import { Card, CardContent } from "@/components/ui/card";
import { Loader2, CheckCircle2, XCircle } from "lucide-react";
import type { JobStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

interface ProcessingStatusProps {
  job: JobStatus;
}

const STAGE_LABELS: Record<string, string> = {
  pending: "In der Warteschlange...",
  processing: "Wird verarbeitet...",
  completed: "Abgeschlossen",
  failed: "Fehlgeschlagen",
};

const STATUS_BADGE_CLASSES: Record<string, string> = {
  pending: "bg-brand/12 text-brand",
  processing: "bg-brand/12 text-brand",
  completed: "bg-[hsl(var(--status-green))]/12 text-[hsl(var(--status-green))]",
  failed: "bg-[hsl(var(--status-red))]/12 text-[hsl(var(--status-red))]",
};

export function ProcessingStatus({ job }: ProcessingStatusProps) {
  const progress = Math.round(job.progress * 100);
  const isActive = job.status === "pending" || job.status === "processing";
  const isFailed = job.status === "failed";
  const isDone = job.status === "completed";

  return (
    <Card className="rounded-[1.75rem]">
      <CardContent className="p-6 sm:p-8">
        <div className="flex flex-col gap-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div className="space-y-3">
              <p className="brand-kicker text-[var(--brand)]">Verarbeitung</p>
              <div className="flex items-start gap-3">
                <div className="mt-1 rounded-[1rem] bg-[var(--surface-subtle)] p-3 ring-1 ring-[var(--line-subtle)]">
                  {isActive && <Loader2 className="h-5 w-5 animate-spin text-[var(--brand)]" />}
                  {isDone && <CheckCircle2 className="h-5 w-5 text-[hsl(var(--status-green))]" />}
                  {isFailed && <XCircle className="h-5 w-5 text-[hsl(var(--status-red))]" />}
                </div>
                <div>
                  <h2 className="text-xl font-bold tracking-[-0.03em] text-[var(--ink-900)] sm:text-2xl">
                    {job.filename}
                  </h2>
                  <p className="mt-2 text-sm text-[var(--text-secondary)]">
                    {STAGE_LABELS[job.status] || job.status}
                  </p>
                </div>
              </div>
            </div>

            <span
              className={cn(
                "inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em]",
                STATUS_BADGE_CLASSES[job.status] ?? "bg-[var(--surface-subtle)] text-[var(--text-secondary)]"
              )}
            >
              {progress}%
            </span>
          </div>

          <div className="brand-subtle-card rounded-[1.5rem] p-4 sm:p-5">
            <div className="mb-3 flex items-center justify-between gap-3 text-sm">
              <span className="font-semibold text-[var(--ink-900)]">Pipeline-Fortschritt</span>
              <span className="font-mono text-[var(--text-secondary)]">{progress}%</span>
            </div>
            <Progress value={progress} className="gap-0" />
            {isFailed && job.error && (
              <p className="mt-3 text-sm text-destructive">{job.error}</p>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
