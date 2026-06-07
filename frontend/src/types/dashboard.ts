export type DashboardFileStatus = "Neu" | "In Prüfung" | "Fertig";

export type DashboardKpiTone = "default" | "warning" | "success" | "brand";

export interface DashboardKpi {
  id: string;
  value: string;
  label: string;
  tone: DashboardKpiTone;
}

export interface DashboardRecentFile {
  id: string;
  fileName: string;
  description?: string;
  customer: string;
  rows?: number;
  progressPercent: number;
  status: DashboardFileStatus;
}

export interface DashboardData {
  greetingName: string;
  kpis: DashboardKpi[];
  recentFiles: DashboardRecentFile[];
}
