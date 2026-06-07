"use client";

import { Plus, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface DashboardTopHeaderProps {
  subtitle?: string;
  searchValue: string;
  onSearchChange: (value: string) => void;
  onCreateNew: () => void;
}

export function DashboardTopHeader({
  subtitle,
  searchValue,
  onSearchChange,
  onCreateNew,
}: DashboardTopHeaderProps) {
  return (
    <header className="flex flex-col gap-4 border-b border-border pb-5 lg:flex-row lg:items-end lg:justify-between">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[hsl(var(--primary))]">
          Uebersicht
        </p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-foreground">
          Stuecklisten-Review
        </h1>
        {subtitle && <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>}
      </div>

      <div className="flex w-full items-center gap-2.5 sm:w-auto">
        <div className="relative flex-1 sm:w-72">
          <label htmlFor="dashboard-search" className="sr-only">
            Datei suchen
          </label>
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            id="dashboard-search"
            value={searchValue}
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder="Datei oder Kunde suchen..."
            className="h-10 pl-9"
          />
        </div>

        <Button type="button" onClick={onCreateNew} className="h-10 shrink-0">
          <Plus className="mr-1.5 h-4 w-4" />
          Neue Stueckliste
        </Button>
      </div>
    </header>
  );
}
