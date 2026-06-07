import type { JobStatus } from "@/lib/api";
import type {
  DashboardFileStatus,
  DashboardKpi,
  DashboardRecentFile,
} from "@/types/dashboard";

/** Map a backend job status to the dashboard's traffic-light label. */
export function statusToLabel(status: JobStatus["status"]): DashboardFileStatus {
  if (status === "completed") return "Fertig";
  if (status === "processing") return "In Prüfung";
  return "Neu"; // pending + failed
}

/** Project a job into the recent-files list shape. */
export function jobToRecentFile(job: JobStatus): DashboardRecentFile {
  return {
    id: job.job_id,
    fileName: job.filename,
    customer: job.customer || "Unbekannt",
    description:
      job.status === "failed" && job.error
        ? `Fehlgeschlagen: ${job.error}`
        : undefined,
    progressPercent:
      job.status === "completed" ? 100 : Math.round((job.progress ?? 0) * 100),
    status: statusToLabel(job.status),
  };
}

/** Derive the four headline KPIs from the full job list. */
export function buildKpis(jobs: JobStatus[]): DashboardKpi[] {
  const open = jobs.filter(
    (j) => j.status === "pending" || j.status === "processing"
  ).length;
  const finished = jobs.filter((j) => j.status === "completed").length;
  const failed = jobs.filter((j) => j.status === "failed").length;

  return [
    { id: "total", value: String(jobs.length), label: "Stuecklisten gesamt", tone: "default" },
    { id: "open", value: String(open), label: "In Bearbeitung / offen", tone: "warning" },
    { id: "finished", value: String(finished), label: "Fertig verarbeitet", tone: "success" },
    { id: "failed", value: String(failed), label: "Fehlgeschlagen", tone: "brand" },
  ];
}
