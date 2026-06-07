"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";

import { DashboardShell } from "@/components/dashboard/dashboard-shell";
import { DashboardKpiRow } from "@/components/dashboard/kpi-row";
import { RecentFilesPanel } from "@/components/dashboard/recent-files-panel";
import { DashboardTopHeader } from "@/components/dashboard/top-header";
import { getCurrentUser, listJobs } from "@/lib/api";
import { buildKpis, jobToRecentFile } from "@/lib/dashboard-mappers";
import { DASHBOARD_ROUTE, WORKSPACE_ROUTE } from "@/lib/routes";

export default function DashboardPage() {
  const router = useRouter();
  const [searchValue, setSearchValue] = useState("");

  const userQuery = useQuery({
    queryKey: ["auth-me"],
    queryFn: getCurrentUser,
    retry: false,
    refetchOnWindowFocus: false,
  });

  const jobsQuery = useQuery({
    queryKey: ["jobs"],
    queryFn: () => listJobs(),
    refetchOnWindowFocus: false,
  });

  const jobs = useMemo(() => jobsQuery.data ?? [], [jobsQuery.data]);
  const kpis = useMemo(() => buildKpis(jobs), [jobs]);

  // The dashboard is an overview: show only the most recent jobs (full
  // management lives on the Stücklisten page). When searching, show all matches.
  const filteredFiles = useMemo(() => {
    const files = jobs.map(jobToRecentFile);
    const needle = searchValue.trim().toLowerCase();
    if (!needle) return files.slice(0, 8);
    return files.filter(
      (file) =>
        file.fileName.toLowerCase().includes(needle) ||
        file.customer.toLowerCase().includes(needle)
    );
  }, [jobs, searchValue]);

  const username = userQuery.data?.username ?? "";
  const subtitle = username
    ? `Angemeldet als ${username}${jobs.length ? ` · ${jobs.length} Vorgaenge gesamt` : ""}`
    : undefined;

  const handleCreateNew = () => {
    router.push(WORKSPACE_ROUTE);
  };

  const handleOpenFile = (fileId: string) => {
    router.push(`${WORKSPACE_ROUTE}?jobId=${encodeURIComponent(fileId)}`);
  };

  const jobsErrorMessage = jobsQuery.isError
    ? jobsQuery.error instanceof Error
      ? jobsQuery.error.message
      : "Vorgaenge konnten nicht geladen werden."
    : null;

  return (
    <DashboardShell currentPath={DASHBOARD_ROUTE}>
      <div className="mx-auto max-w-7xl px-5 py-8 sm:px-6 lg:px-10">
        <DashboardTopHeader
          subtitle={subtitle}
          searchValue={searchValue}
          onSearchChange={setSearchValue}
          onCreateNew={handleCreateNew}
        />

        <div className="mt-6">
          <DashboardKpiRow items={kpis} />
        </div>

        <div className="mt-6">
          <RecentFilesPanel
            files={filteredFiles}
            onOpenFile={handleOpenFile}
            isLoading={jobsQuery.isLoading}
            errorMessage={jobsErrorMessage}
            emptyMessage={
              searchValue.trim()
                ? "Keine Vorgaenge fuer den aktuellen Suchbegriff gefunden."
                : "Noch keine Stuecklisten verarbeitet. Starten Sie mit „Neue Stueckliste“."
            }
          />
        </div>
      </div>
    </DashboardShell>
  );
}
