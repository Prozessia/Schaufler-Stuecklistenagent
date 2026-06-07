"use client";

import type { ComponentType } from "react";
import { useEffect, useState } from "react";
import Link from "next/link";
import {
  BarChart3,
  CheckSquare2,
  ChevronLeft,
  ChevronRight,
  Files,
  LayoutDashboard,
  LogOut,
  Menu,
  Settings,
  X,
} from "lucide-react";

import { cn } from "@/lib/utils";

export interface DashboardNavItem {
  label: string;
  href: string;
  icon: ComponentType<{ className?: string }>;
}

export interface DashboardNavGroup {
  gruppe: string;
  items: DashboardNavItem[];
}

export function buildDashboardNavGroups(routes: {
  dashboard: string;
  stuecklisten: string;
  statistik: string;
  einstellungen: string;
}): DashboardNavGroup[] {
  return [
    {
      gruppe: "Arbeitsbereich",
      items: [
        { label: "Uebersicht", href: routes.dashboard, icon: LayoutDashboard },
        { label: "Stuecklisten", href: routes.stuecklisten, icon: Files },
      ],
    },
    {
      gruppe: "System",
      items: [
        { label: "Statistik", href: routes.statistik, icon: BarChart3 },
        { label: "Einstellungen", href: routes.einstellungen, icon: Settings },
      ],
    },
  ];
}

function initialsOf(username: string): string {
  const trimmed = username.trim();
  return trimmed ? trimmed.slice(0, 2).toUpperCase() : "–";
}

interface SidebarContentProps {
  groups: DashboardNavGroup[];
  currentPath: string;
  username: string;
  collapsed: boolean;
  onLogout: () => void;
  onToggleCollapsed?: () => void;
  onNavigate?: () => void;
}

function SidebarContent({
  groups,
  currentPath,
  username,
  collapsed,
  onLogout,
  onToggleCollapsed,
  onNavigate,
}: SidebarContentProps) {
  return (
    <div className="flex h-full flex-col border-r border-border bg-card">
      {/* Brand */}
      <div className="flex h-[60px] items-center gap-2.5 px-4">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[hsl(var(--primary))] text-white">
          <CheckSquare2 className="h-[18px] w-[18px]" />
        </span>
        {!collapsed && (
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold tracking-tight text-foreground">
              Stuecklistenagent
            </p>
            <p className="truncate text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              Schaufler Tooling
            </p>
          </div>
        )}
      </div>

      <div className="mx-3 h-px bg-border" />

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-3 py-4">
        {groups.map((group, gi) => (
          <div key={group.gruppe} className={cn(gi > 0 && "mt-6")}>
            {!collapsed && (
              <p className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground/70">
                {group.gruppe}
              </p>
            )}
            {collapsed && gi > 0 && <div className="mx-2 mb-3 h-px bg-border/60" />}
            <div className="space-y-0.5">
              {group.items.map((item) => {
                const Icon = item.icon;
                const isActive = currentPath === item.href;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    aria-current={isActive ? "page" : undefined}
                    onClick={onNavigate}
                    title={collapsed ? item.label : undefined}
                    className={cn(
                      "group relative flex items-center gap-3 rounded-lg px-3 py-2 text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--primary))]/40",
                      collapsed && "justify-center",
                      isActive
                        ? "bg-[hsl(var(--primary))]/10 text-[hsl(var(--primary))]"
                        : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
                    )}
                  >
                    {isActive && (
                      <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-[hsl(var(--primary))]" />
                    )}
                    <Icon className="h-[18px] w-[18px] shrink-0" />
                    {!collapsed && <span>{item.label}</span>}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className="mx-3 h-px bg-border" />

      {/* User + collapse */}
      <div className="p-3">
        {collapsed ? (
          <button
            type="button"
            onClick={onLogout}
            aria-label="Abmelden"
            title={`${username} – Abmelden`}
            className="mb-1 flex h-9 w-full items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted/60 hover:text-destructive"
          >
            <LogOut className="h-[18px] w-[18px]" />
          </button>
        ) : (
          <div className="mb-2 flex items-center gap-2.5 rounded-lg bg-muted/40 px-2.5 py-2">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[hsl(var(--primary))] text-[11px] font-semibold text-white">
              {initialsOf(username)}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-medium text-foreground">{username}</p>
              <p className="truncate text-[10px] text-muted-foreground">Schaufler Tooling</p>
            </div>
            <button
              type="button"
              onClick={onLogout}
              aria-label="Abmelden"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-destructive"
            >
              <LogOut className="h-3.5 w-3.5" />
            </button>
          </div>
        )}

        {onToggleCollapsed && (
          <button
            type="button"
            onClick={onToggleCollapsed}
            aria-label={collapsed ? "Navigation ausklappen" : "Navigation einklappen"}
            className="flex h-8 w-full items-center justify-center rounded-lg text-muted-foreground/70 transition-colors hover:bg-muted/60 hover:text-foreground"
          >
            {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </button>
        )}
      </div>
    </div>
  );
}

interface DashboardSidebarProps {
  groups: DashboardNavGroup[];
  currentPath: string;
  username: string;
  mobileMenuOpen: boolean;
  onOpenMenu: () => void;
  onCloseMenu: () => void;
  onLogout: () => void;
}

export function DashboardSidebar({
  groups,
  currentPath,
  username,
  mobileMenuOpen,
  onOpenMenu,
  onCloseMenu,
  onLogout,
}: DashboardSidebarProps) {
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem("sidebar-collapsed");
    if (stored != null) setCollapsed(stored === "true");
  }, []);

  const toggleCollapsed = () => {
    setCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem("sidebar-collapsed", String(next));
      return next;
    });
  };

  return (
    <>
      {/* Desktop */}
      <aside
        className={cn(
          "hidden h-screen shrink-0 transition-[width] duration-200 lg:block",
          collapsed ? "w-[68px]" : "w-[248px]"
        )}
      >
        <SidebarContent
          groups={groups}
          currentPath={currentPath}
          username={username}
          collapsed={collapsed}
          onLogout={onLogout}
          onToggleCollapsed={toggleCollapsed}
        />
      </aside>

      {/* Mobile top bar */}
      <div className="sticky top-0 z-30 flex items-center justify-between gap-3 border-b border-border bg-card px-4 py-2.5 lg:hidden">
        <div className="flex items-center gap-2.5">
          <button
            type="button"
            onClick={onOpenMenu}
            aria-label="Navigation oeffnen"
            aria-expanded={mobileMenuOpen}
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-border text-muted-foreground transition-colors hover:text-foreground"
          >
            <Menu className="h-5 w-5" />
          </button>
          <p className="text-sm font-semibold text-foreground">Stuecklistenagent</p>
        </div>
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-[hsl(var(--primary))] text-white">
          <CheckSquare2 className="h-[18px] w-[18px]" />
        </span>
      </div>

      {/* Mobile drawer */}
      {mobileMenuOpen && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true">
          <button
            type="button"
            className="absolute inset-0 bg-black/40"
            onClick={onCloseMenu}
            aria-label="Navigation schliessen"
          />
          <div className="absolute inset-y-0 left-0 w-[260px] max-w-[82vw] shadow-xl">
            <div className="relative h-full">
              <button
                type="button"
                onClick={onCloseMenu}
                aria-label="Schliessen"
                className="absolute right-2 top-3 z-10 flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:text-foreground"
              >
                <X className="h-4 w-4" />
              </button>
              <SidebarContent
                groups={groups}
                currentPath={currentPath}
                username={username}
                collapsed={false}
                onLogout={onLogout}
                onNavigate={onCloseMenu}
              />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
