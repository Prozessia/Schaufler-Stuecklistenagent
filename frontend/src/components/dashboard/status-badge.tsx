import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type StatusTone = "green" | "yellow" | "red" | "blue" | "neutral";

const TONE_STYLES: Record<StatusTone, string> = {
  green: "bg-emerald-500/10 text-emerald-600 ring-emerald-500/20",
  yellow: "bg-amber-500/10 text-amber-600 ring-amber-500/25",
  red: "bg-red-500/10 text-red-600 ring-red-500/20",
  blue: "bg-[hsl(var(--primary))]/10 text-[hsl(var(--primary))] ring-[hsl(var(--primary))]/20",
  neutral: "bg-muted text-muted-foreground ring-border",
};

interface StatusBadgeProps {
  tone: StatusTone;
  children: ReactNode;
  className?: string;
}

export function StatusBadge({ tone, children, className }: StatusBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        TONE_STYLES[tone],
        className
      )}
    >
      {children}
    </span>
  );
}
