"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { useRouter } from "next/navigation";

import { logout } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth";
import {
  DASHBOARD_ROUTE,
  EINSTELLUNGEN_ROUTE,
  LOGIN_ROUTE,
  STATISTIK_ROUTE,
  STUECKLISTEN_ROUTE,
} from "@/lib/routes";
import { DashboardSidebar, buildDashboardNavGroups } from "@/components/dashboard/sidebar";

interface DashboardShellProps {
  currentPath: string;
  children: ReactNode;
}

export function DashboardShell({ currentPath, children }: DashboardShellProps) {
  const router = useRouter();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const authQuery = useAuthGuard();

  const handleLogout = async () => {
    try {
      await logout();
    } finally {
      router.replace(LOGIN_ROUTE);
    }
  };

  const navGroups = buildDashboardNavGroups({
    dashboard: DASHBOARD_ROUTE,
    stuecklisten: STUECKLISTEN_ROUTE,
    statistik: STATISTIK_ROUTE,
    einstellungen: EINSTELLUNGEN_ROUTE,
  });

  if (authQuery.isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4">
        <div className="rounded-lg border border-border bg-card px-5 py-3 text-sm text-muted-foreground">
          Anmeldung wird geprueft...
        </div>
      </div>
    );
  }

  if (authQuery.isError || !authQuery.data) {
    return null;
  }

  return (
    // Desktop: fixed-height flex so the sidebar always spans the full viewport
    // and only the main column scrolls (the sidebar no longer scrolls away on
    // long pages). Mobile: normal document flow with the sticky top bar.
    <div className="min-h-screen bg-background text-foreground lg:flex lg:h-screen lg:overflow-hidden">
      <DashboardSidebar
        groups={navGroups}
        currentPath={currentPath}
        username={authQuery.data.username}
        mobileMenuOpen={mobileMenuOpen}
        onOpenMenu={() => setMobileMenuOpen(true)}
        onCloseMenu={() => setMobileMenuOpen(false)}
        onLogout={handleLogout}
      />
      <main className="min-w-0 flex-1 lg:h-screen lg:overflow-y-auto">{children}</main>
    </div>
  );
}
