"use client";

import { Construction } from "lucide-react";

import { DashboardShell } from "@/components/dashboard/dashboard-shell";

interface SecondaryPageProps {
  currentPath: string;
  eyebrow: string;
  title: string;
  description: string;
}

export function SecondaryPage({
  currentPath,
  eyebrow,
  title,
  description,
}: SecondaryPageProps) {
  return (
    <DashboardShell currentPath={currentPath}>
      <div className="mx-auto max-w-7xl px-5 py-8 sm:px-6 lg:px-10">
        <header className="border-b border-border pb-5">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[hsl(var(--primary))]">
            {eyebrow}
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-foreground">{title}</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{description}</p>
        </header>

        <div className="mt-6 flex flex-col items-center justify-center rounded-xl border border-border bg-card py-16 text-center">
          <div className="rounded-2xl bg-muted/60 p-5">
            <Construction className="h-8 w-8 text-muted-foreground/60" />
          </div>
          <p className="mt-4 text-sm font-medium text-foreground">Bereich in Vorbereitung</p>
          <p className="mt-1 max-w-md text-sm text-muted-foreground">
            Die Navigation ist verdrahtet. Die Fachansicht wird hier im naechsten Schritt angebunden.
          </p>
        </div>
      </div>
    </DashboardShell>
  );
}
