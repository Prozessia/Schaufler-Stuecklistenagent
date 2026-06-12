const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/** Read a cookie value by name from document.cookie (client-side only). */
function getCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(
    new RegExp("(?:^|; )" + name.replace(/([.*+?^=!:${}()|[\]/\\])/g, "\\$1") + "=([^;]*)")
  );
  return match ? decodeURIComponent(match[1]) : null;
}

const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

// Auth is carried exclusively by the httpOnly session cookie (credentials:
// "include"). The API key is a server-side secret and must NEVER be exposed
// through NEXT_PUBLIC_*, which would inline it into the client bundle.
// CSRF: the backend uses the Double-Submit-Cookie pattern — every mutating
// request must include the csrf_token cookie value as an X-CSRF-Token header.
async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const method = (init?.method ?? "GET").toUpperCase();
  const headers = new Headers(init?.headers);

  if (MUTATING_METHODS.has(method)) {
    const csrfToken = getCookie("csrf_token");
    if (csrfToken) {
      headers.set("X-CSRF-Token", csrfToken);
    }
  }

  return fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
}

export interface AuthUserResponse {
  username: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface JobStatus {
  job_id: string;
  status: "pending" | "processing" | "completed" | "failed";
  filename: string;
  customer: string;
  progress: number;
  error: string | null;
  queue_position: number | null;
}

// GET /jobs returns this richer projection (Phase 0): JobStatus + denormalized
// counters + timestamps, served without parsing the audit blob.
export interface JobSummary extends JobStatus {
  created_at: number;
  updated_at: number;
  total_rows: number;
  total_cells: number;
  green_count: number;
  yellow_count: number;
  red_count: number;
  neutral_count: number;
  manual_confirmed_count: number;
  completeness_guaranteed: boolean;
  expected_position_count: number;
  archived: boolean;
}

export interface StatsOverview {
  total_jobs: number;
  completed_jobs: number;
  total_rows: number;
  total_cells: number;
  green: number;
  yellow: number;
  red: number;
  neutral: number;
  manual_confirmed: number;
  automation_rate: number;
  corrections: number;
  minutes_per_row: number;
  estimated_time_saved_hours: number;
  status_counts: Record<string, number>;
}

export interface StatsTimeseriesPoint {
  period: string;
  jobs: number;
  rows: number;
  automation_rate: number;
}

export interface StatsCustomerRow {
  customer: string;
  jobs: number;
  rows: number;
  green: number;
  yellow: number;
  red: number;
  automation_rate: number;
  corrections: number;
}

export interface StatsCorrections {
  total: number;
  by_field: Array<{ field: string; count: number }>;
  by_customer: Array<{ customer: string; count: number }>;
  by_month: Array<{ period: string; count: number }>;
}

export interface SettingsConfigResponse {
  app_config_yaml: string;
  overrides_yaml: string;
  overrides_exists: boolean;
  last_reload_at: number | null;
  paths: {
    app_config: string;
    overrides: string;
  };
}

export interface SettingsReloadResponse {
  ok: boolean;
  reloaded_at: number;
  cleared_caches: string[];
}

export interface MasterDataSummary {
  catalog: "materials" | "units" | "validation_rules";
  filename: string;
  exists: boolean;
  updated_at: number | null;
  entry_count: number;
}

export interface MasterDataResponse {
  catalog: MasterDataSummary["catalog"];
  filename: string;
  content: Record<string, unknown>;
}

export interface MaterialEntry {
  canonical: string;
  werkstoff_nr?: string;
  din_name?: string;
  category?: string;
  aliases?: string[];
  typical_hardness_hrc?: [number, number] | null;
  typical_use?: string;
  [key: string]: unknown;
}

export interface SystemInfoResponse {
  app_version: string;
  python_version: string;
  platform: string;
  project_root: string;
  last_reload_at: number | null;
  jobs: {
    total: number;
    active: number;
    archived: number;
    completed: number;
  };
  corrections: number;
  files: Record<string, boolean>;
  azure_openai: {
    azure_openai_endpoint_configured: boolean;
    azure_openai_key_configured: boolean;
    azure_openai_api_version: string;
    deployment_main: string;
    deployment_mini: string;
  };
}

export interface SourceLocation {
  page: number | null;
  bbox: number[] | null;
  text: string;
  match_type: string;
}

export interface CellResult {
  row_index: number;
  target_field: string;
  target_column: string;
  raw_value: string | null;
  transformed_value: string | null;
  method: string;
  score: number;
  classification: "green" | "yellow" | "red" | "neutral" | "manual_confirmed";
  final_status: "green" | "yellow" | "red" | "neutral" | "manual_confirmed";
  value_match_result: "match" | "mismatch" | "uncertain" | "not_applicable";
  value_match_detail: string;
  field_category: string;
  green_evidence: string[];
  blocking_errors: string[];
  hard_vetoes: string[];
  reasoning: string;
  source_location: SourceLocation | null;
}

export interface RowResult {
  row_index: number;
  cells: CellResult[];
  worst_classification: "green" | "yellow" | "red" | "neutral" | "manual_confirmed";
  /** Lossless footer/header/note detection — advisory only (row is never dropped). */
  non_data?: boolean;
  non_data_reasons?: string[];
}

export interface TemplateColumnResult {
  field: string;
  column: string;
  header_label: string;
  header_lines: string[];
  width: number | null;
  type: string;
  required: boolean;
  horizontal_alignment: string | null;
  vertical_alignment: string | null;
}

export interface TemplateMetaSectionResult {
  key: string;
  label: string;
  value: string;
  start_column: string;
  end_column: string;
  label_row: number;
  value_row: number;
  label_horizontal_alignment: string | null;
  label_vertical_alignment: string | null;
  value_horizontal_alignment: string | null;
  value_vertical_alignment: string | null;
}

export interface TemplateDefaultCellResult {
  field: string;
  column: string;
  value: string;
  horizontal_alignment: string | null;
  vertical_alignment: string | null;
}

export interface TemplateLayoutResult {
  title: string;
  sheet_name: string;
  header_row: number;
  data_start_row: number;
  freeze_panes: string | null;
  header_height: number | null;
  data_row_height: number | null;
  title_row: number;
  spacer_row: number;
  default_row: number;
  default_row_height: number | null;
  meta_sections: TemplateMetaSectionResult[];
  default_cells: TemplateDefaultCellResult[];
}

export interface JobResult {
  job_id: string;
  filename: string;
  customer: string;
  total_rows: number;
  total_cells: number;
  green_count: number;
  yellow_count: number;
  red_count: number;
  neutral_count: number;
  manual_confirmed_count: number;
  green_pct: number;
  yellow_pct: number;
  red_pct: number;
  neutral_pct: number;
  manual_confirmed_pct: number;
  // ZDL-1: completeness guarantee for the dashboard banner.
  completeness_guaranteed: boolean;
  completeness_reason: string;
  // ARCH-003: explains why a source type cannot produce GREEN (Excel/CSV).
  green_policy_note: string;
  expected_position_count: number;
  guard_basis: string;
  // R3: row indices the reviewer deliberately excluded (skipped from rows + export).
  excluded_rows: number[];
  target_fields: string[];
  columns: TemplateColumnResult[];
  template: TemplateLayoutResult;
  rows: RowResult[];
}

export interface CellEditPayload {
  row_index: number;
  target_field: string;
  corrected_value: string;
}

export interface UploadResponse {
  job_id: string;
  filename: string;
  message: string;
}

export async function uploadFile(file: File, customer?: string): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  if (customer && customer.trim()) {
    form.append("customer", customer.trim());
  }
  const res = await apiFetch("/upload", { method: "POST", body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Upload failed");
  }
  return res.json();
}

export async function retryJob(jobId: string): Promise<JobStatus> {
  const res = await apiFetch(`/jobs/${jobId}/retry`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Retry fehlgeschlagen");
  }
  return res.json();
}

export async function login(payload: LoginRequest): Promise<AuthUserResponse> {
  const res = await apiFetch("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Login failed");
  }

  return res.json();
}

export async function getCurrentUser(): Promise<AuthUserResponse> {
  const res = await apiFetch("/auth/me");
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Unauthorized");
  }
  return res.json();
}

export async function logout(): Promise<void> {
  const res = await apiFetch("/auth/logout", { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Logout failed");
  }
}

export interface JobListParams {
  q?: string;
  status?: string;
  includeArchived?: boolean;
}

export async function listJobs(params: JobListParams = {}): Promise<JobSummary[]> {
  const sp = new URLSearchParams();
  if (params.q) sp.set("q", params.q);
  if (params.status) sp.set("status", params.status);
  if (params.includeArchived) sp.set("include_archived", "true");
  const qs = sp.toString();
  const res = await apiFetch(`/jobs${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error("Failed to list jobs");
  return res.json();
}

export async function getStatsOverview(): Promise<StatsOverview> {
  const res = await apiFetch("/stats/overview");
  if (!res.ok) throw new Error("Failed to load statistics overview");
  return res.json();
}

export async function getStatsTimeseries(
  bucket: "week" | "month" = "month"
): Promise<StatsTimeseriesPoint[]> {
  const res = await apiFetch(`/stats/timeseries?bucket=${bucket}`);
  if (!res.ok) throw new Error("Failed to load statistics timeseries");
  return res.json();
}

export async function getStatsByCustomer(): Promise<StatsCustomerRow[]> {
  const res = await apiFetch("/stats/by-customer");
  if (!res.ok) throw new Error("Failed to load customer statistics");
  return res.json();
}

export async function getStatsCorrections(): Promise<StatsCorrections> {
  const res = await apiFetch("/stats/corrections");
  if (!res.ok) throw new Error("Failed to load correction statistics");
  return res.json();
}

export async function getSettingsConfig(): Promise<SettingsConfigResponse> {
  const res = await apiFetch("/settings/config");
  if (!res.ok) throw new Error("Failed to load settings config");
  return res.json();
}

export async function saveSettingsOverrides(content: string): Promise<void> {
  const res = await apiFetch("/settings/config/overrides", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Overrides konnten nicht gespeichert werden");
  }
}

export async function reloadSettings(): Promise<SettingsReloadResponse> {
  const res = await apiFetch("/settings/reload", { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Reload fehlgeschlagen");
  }
  return res.json();
}

export async function listMasterData(): Promise<MasterDataSummary[]> {
  const res = await apiFetch("/settings/master-data");
  if (!res.ok) throw new Error("Failed to load master-data summary");
  return res.json();
}

export async function getMasterData(
  catalog: MasterDataSummary["catalog"]
): Promise<MasterDataResponse> {
  const res = await apiFetch(`/settings/master-data/${catalog}`);
  if (!res.ok) throw new Error("Failed to load master data");
  return res.json();
}

export async function saveMasterData(
  catalog: MasterDataSummary["catalog"],
  content: Record<string, unknown>
): Promise<void> {
  const res = await apiFetch(`/settings/master-data/${catalog}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Stammdaten konnten nicht gespeichert werden");
  }
}

export async function createMaterial(material: MaterialEntry): Promise<void> {
  const res = await apiFetch("/settings/master-data/materials", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ material }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Material konnte nicht angelegt werden");
  }
}

export async function updateMaterial(
  canonical: string,
  material: MaterialEntry
): Promise<void> {
  const res = await apiFetch(`/settings/master-data/materials/${encodeURIComponent(canonical)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ material }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Material konnte nicht gespeichert werden");
  }
}

export async function deleteMaterial(canonical: string): Promise<void> {
  const res = await apiFetch(`/settings/master-data/materials/${encodeURIComponent(canonical)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Material konnte nicht geloescht werden");
  }
}

export async function getSystemInfo(): Promise<SystemInfoResponse> {
  const res = await apiFetch("/settings/system");
  if (!res.ok) throw new Error("Failed to load system info");
  return res.json();
}

export async function deleteJob(
  jobId: string,
  opts: { purge?: boolean } = {}
): Promise<void> {
  const res = await apiFetch(`/jobs/${jobId}${opts.purge ? "?purge=true" : ""}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Löschen fehlgeschlagen");
  }
}

export async function restoreJob(jobId: string): Promise<void> {
  const res = await apiFetch(`/jobs/${jobId}/restore`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Wiederherstellen fehlgeschlagen");
  }
}

export async function getJobStatus(jobId: string): Promise<JobStatus> {
  const res = await apiFetch(`/jobs/${jobId}`);
  if (!res.ok) throw new Error("Failed to get job status");
  return res.json();
}

export async function getJobResult(jobId: string): Promise<JobResult> {
  // The 2026-06-03 backend returns the full JobResult (rows inline) from
  // /result; there is no separate paginated rows endpoint.
  const res = await apiFetch(`/jobs/${jobId}/result`);
  if (!res.ok) throw new Error("Failed to get result");
  return res.json();
}

// NOTE: corrections are persisted server-side as a side effect of
// saveEditedCells (PATCH /cells already calls the feedback store). A separate
// POST /feedback call from the client would double-record every correction, so
// it is intentionally not wired here.

export async function saveEditedCells(
  jobId: string,
  edits: CellEditPayload[]
): Promise<JobResult> {
  const res = await apiFetch(`/jobs/${jobId}/cells`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(edits),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to save edited cells");
  }

  return res.json();
}

export async function setRowExclusion(
  jobId: string,
  rowIndices: number[],
  excluded: boolean,
  reason = "excluded by user"
): Promise<JobResult> {
  const res = await apiFetch(`/jobs/${jobId}/rows`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ row_indices: rowIndices, excluded, reason }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to update row exclusion");
  }

  return res.json();
}

export function getExportUrl(jobId: string): string {
  return `${API_BASE}/jobs/${jobId}/export`;
}

export function getSourceUrl(jobId: string): string {
  return `${API_BASE}/jobs/${jobId}/source`;
}
