"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Database,
  FileJson,
  Pencil,
  Plus,
  RotateCw,
  Save,
  Server,
  Settings2,
  Trash2,
  X,
  type LucideIcon,
} from "lucide-react";

import { DashboardShell } from "@/components/dashboard/dashboard-shell";
import { StatusBadge } from "@/components/dashboard/status-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  createMaterial,
  deleteMaterial,
  getMasterData,
  getSettingsConfig,
  getSystemInfo,
  listMasterData,
  reloadSettings,
  saveMasterData,
  saveSettingsOverrides,
  updateMaterial,
  type MasterDataSummary,
  type MaterialEntry,
} from "@/lib/api";
import { EINSTELLUNGEN_ROUTE } from "@/lib/routes";
import { cn } from "@/lib/utils";

type TabKey = "config" | "materials" | "json" | "system";
type CatalogKey = MasterDataSummary["catalog"];

type MaterialDraft = {
  canonical: string;
  werkstoff_nr: string;
  din_name: string;
  category: string;
  aliases: string;
  hardness_min: string;
  hardness_max: string;
  typical_use: string;
};

const emptyDraft: MaterialDraft = {
  canonical: "",
  werkstoff_nr: "",
  din_name: "",
  category: "",
  aliases: "",
  hardness_min: "",
  hardness_max: "",
  typical_use: "",
};

const tabs: Array<{ key: TabKey; label: string; icon: LucideIcon }> = [
  { key: "config", label: "Konfiguration", icon: Settings2 },
  { key: "materials", label: "Materialien", icon: Database },
  { key: "json", label: "JSON-Stammdaten", icon: FileJson },
  { key: "system", label: "System", icon: Server },
];

const dateFmt = new Intl.DateTimeFormat("de-DE", {
  dateStyle: "medium",
  timeStyle: "short",
});

function formatTimestamp(value: number | null | undefined): string {
  return value ? dateFmt.format(new Date(value * 1000)) : "-";
}

function asMaterials(value: unknown): MaterialEntry[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is MaterialEntry =>
      Boolean(item) && typeof item === "object" && typeof (item as MaterialEntry).canonical === "string"
  );
}

function draftFromMaterial(material: MaterialEntry): MaterialDraft {
  const hardness = material.typical_hardness_hrc;
  return {
    canonical: material.canonical || "",
    werkstoff_nr: material.werkstoff_nr || "",
    din_name: material.din_name || "",
    category: material.category || "",
    aliases: (material.aliases || []).join(", "),
    hardness_min: Array.isArray(hardness) ? String(hardness[0]) : "",
    hardness_max: Array.isArray(hardness) ? String(hardness[1]) : "",
    typical_use: material.typical_use || "",
  };
}

function materialFromDraft(draft: MaterialDraft): MaterialEntry {
  const aliases = draft.aliases
    .split(",")
    .map((alias) => alias.trim())
    .filter(Boolean);
  const min = Number(draft.hardness_min);
  const max = Number(draft.hardness_max);
  const hasHardness = draft.hardness_min.trim() !== "" && draft.hardness_max.trim() !== "";

  return {
    canonical: draft.canonical.trim(),
    werkstoff_nr: draft.werkstoff_nr.trim(),
    din_name: draft.din_name.trim(),
    category: draft.category.trim(),
    aliases,
    typical_hardness_hrc: hasHardness && Number.isFinite(min) && Number.isFinite(max) ? [min, max] : null,
    typical_use: draft.typical_use.trim(),
  };
}

function TextArea({ className, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "min-h-64 w-full resize-y rounded-lg border border-input bg-background px-3 py-2 font-mono text-xs leading-5 text-foreground outline-none transition-colors placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-60",
        className
      )}
      {...props}
    />
  );
}

function SectionTitle({ icon: Icon, title, subtitle }: { icon: LucideIcon; title: string; subtitle: string }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
      <div>
        <h2 className="text-base font-semibold text-foreground">{title}</h2>
        <p className="mt-0.5 text-sm text-muted-foreground">{subtitle}</p>
      </div>
      <Icon className="h-5 w-5 text-muted-foreground" />
    </div>
  );
}

function InputField({ label, value, onChange, required = false }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <Input value={value} onChange={(event) => onChange(event.target.value)} required={required} className="h-9" />
    </label>
  );
}

function Info({ label, value, compact = false }: { label: string; value: string; compact?: boolean }) {
  return (
    <div className={compact ? "flex items-center justify-between gap-3 py-1" : "rounded-lg border border-border/70 p-3"}>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="break-all text-sm font-medium text-foreground">{value}</p>
    </div>
  );
}

export default function EinstellungenPage() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<TabKey>("config");
  const [overridesText, setOverridesText] = useState("");
  const [selectedCatalog, setSelectedCatalog] = useState<CatalogKey>("units");
  const [jsonText, setJsonText] = useState("");
  const [materialSearch, setMaterialSearch] = useState("");
  const [draft, setDraft] = useState<MaterialDraft>(emptyDraft);
  const [editingCanonical, setEditingCanonical] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const configQuery = useQuery({
    queryKey: ["settings", "config"],
    queryFn: getSettingsConfig,
    refetchOnWindowFocus: false,
  });
  const masterSummaryQuery = useQuery({
    queryKey: ["settings", "master-data"],
    queryFn: listMasterData,
    refetchOnWindowFocus: false,
  });
  const materialsQuery = useQuery({
    queryKey: ["settings", "master-data", "materials"],
    queryFn: () => getMasterData("materials"),
    refetchOnWindowFocus: false,
  });
  const selectedCatalogQuery = useQuery({
    queryKey: ["settings", "master-data", selectedCatalog],
    queryFn: () => getMasterData(selectedCatalog),
    refetchOnWindowFocus: false,
  });
  const systemQuery = useQuery({
    queryKey: ["settings", "system"],
    queryFn: getSystemInfo,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    if (configQuery.data) setOverridesText(configQuery.data.overrides_yaml);
  }, [configQuery.data]);

  useEffect(() => {
    if (selectedCatalogQuery.data) {
      setJsonText(JSON.stringify(selectedCatalogQuery.data.content, null, 2));
    }
  }, [selectedCatalogQuery.data]);

  const invalidateSettings = () => {
    queryClient.invalidateQueries({ queryKey: ["settings"] });
    queryClient.invalidateQueries({ queryKey: ["stats"] });
  };

  const saveOverridesMutation = useMutation({
    mutationFn: () => saveSettingsOverrides(overridesText),
    onSuccess: () => {
      setMessage("Overrides gespeichert.");
      invalidateSettings();
    },
  });

  const reloadMutation = useMutation({
    mutationFn: reloadSettings,
    onSuccess: () => {
      setMessage("Runtime-Caches neu geladen.");
      invalidateSettings();
    },
  });

  const saveJsonMutation = useMutation({
    mutationFn: () => {
      const parsed = JSON.parse(jsonText) as Record<string, unknown>;
      return saveMasterData(selectedCatalog, parsed);
    },
    onSuccess: () => {
      setMessage("Stammdaten gespeichert.");
      invalidateSettings();
    },
  });

  const saveMaterialMutation = useMutation({
    mutationFn: () => {
      const material = materialFromDraft(draft);
      return editingCanonical ? updateMaterial(editingCanonical, material) : createMaterial(material);
    },
    onSuccess: () => {
      setMessage(editingCanonical ? "Material gespeichert." : "Material angelegt.");
      setDraft(emptyDraft);
      setEditingCanonical(null);
      invalidateSettings();
    },
  });

  const deleteMaterialMutation = useMutation({
    mutationFn: (canonical: string) => deleteMaterial(canonical),
    onSuccess: () => {
      setMessage("Material geloescht.");
      invalidateSettings();
    },
  });

  const materials = useMemo(
    () => asMaterials(materialsQuery.data?.content.materials),
    [materialsQuery.data]
  );
  const filteredMaterials = useMemo(() => {
    const needle = materialSearch.trim().toLowerCase();
    if (!needle) return materials;
    return materials.filter((material) =>
      [
        material.canonical,
        material.werkstoff_nr,
        material.din_name,
        material.category,
        material.typical_use,
        ...(material.aliases || []),
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle))
    );
  }, [materials, materialSearch]);

  const busy =
    saveOverridesMutation.isPending ||
    reloadMutation.isPending ||
    saveJsonMutation.isPending ||
    saveMaterialMutation.isPending ||
    deleteMaterialMutation.isPending;

  const mutationError =
    saveOverridesMutation.error ||
    reloadMutation.error ||
    saveJsonMutation.error ||
    saveMaterialMutation.error ||
    deleteMaterialMutation.error;

  const saveJson = () => {
    try {
      JSON.parse(jsonText);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "JSON ist ungueltig.");
      return;
    }
    saveJsonMutation.mutate();
  };

  return (
    <DashboardShell currentPath={EINSTELLUNGEN_ROUTE}>
      <div className="mx-auto max-w-7xl px-5 py-8 sm:px-6 lg:px-10">
        <header className="border-b border-border pb-5">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[hsl(var(--primary))]">
            Einstellungen
          </p>
          <div className="mt-1 flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight text-foreground">
                Admin-Konfiguration
              </h1>
              <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
                Overrides, Stammdaten und Systemstatus fuer die lokale Schaufler-Instanz.
              </p>
            </div>
            <Button type="button" variant="outline" onClick={() => reloadMutation.mutate()} disabled={busy} className="h-9">
              <RotateCw className={cn("h-4 w-4", reloadMutation.isPending && "animate-spin")} />
              Reload
            </Button>
          </div>
        </header>

        <div className="mt-5 flex flex-wrap gap-2">
          {tabs.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => setTab(item.key)}
                className={cn(
                  "inline-flex h-9 items-center gap-2 rounded-lg border px-3 text-sm transition-colors",
                  tab === item.key
                    ? "border-[hsl(var(--primary))]/40 bg-[hsl(var(--primary))]/10 text-[hsl(var(--primary))]"
                    : "border-border bg-card text-muted-foreground hover:text-foreground"
                )}
              >
                <Icon className="h-4 w-4" />
                {item.label}
              </button>
            );
          })}
        </div>

        {(message || mutationError) && (
          <div
            className={cn(
              "mt-4 rounded-lg border px-4 py-3 text-sm",
              mutationError
                ? "border-destructive/30 bg-destructive/5 text-destructive"
                : "border-emerald-500/30 bg-emerald-500/5 text-emerald-700"
            )}
          >
            {mutationError instanceof Error ? mutationError.message : message}
          </div>
        )}

        {tab === "config" && (
          <div className="mt-6 grid gap-6 xl:grid-cols-2">
            <section className="overflow-hidden rounded-xl border border-border bg-card">
              <SectionTitle icon={Settings2} title="Basis-Konfiguration" subtitle={configQuery.data?.paths.app_config || "config/app_config.yaml"} />
              <div className="p-5">
                <TextArea value={configQuery.data?.app_config_yaml || ""} readOnly className="min-h-[520px] opacity-80" />
              </div>
            </section>

            <section className="overflow-hidden rounded-xl border border-border bg-card">
              <SectionTitle icon={Pencil} title="Overrides-YAML" subtitle={configQuery.data?.paths.overrides || "config/overrides.yaml"} />
              <div className="p-5">
                <TextArea
                  value={overridesText}
                  onChange={(event) => setOverridesText(event.target.value)}
                  placeholder={'scoring:\n  green_threshold: 0.92\n'}
                  className="min-h-[456px]"
                />
                <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                  <p className="text-xs text-muted-foreground">Letzter Reload: {formatTimestamp(configQuery.data?.last_reload_at)}</p>
                  <Button type="button" onClick={() => saveOverridesMutation.mutate()} disabled={busy} className="h-9">
                    <Save className="h-4 w-4" />
                    Overrides speichern
                  </Button>
                </div>
              </div>
            </section>
          </div>
        )}

        {tab === "materials" && (
          <div className="mt-6 grid gap-6 xl:grid-cols-[380px_1fr]">
            <section className="overflow-hidden rounded-xl border border-border bg-card">
              <SectionTitle icon={Plus} title={editingCanonical ? "Material bearbeiten" : "Material anlegen"} subtitle="materials.json mit Validierung" />
              <form
                className="space-y-3 p-5"
                onSubmit={(event) => {
                  event.preventDefault();
                  saveMaterialMutation.mutate();
                }}
              >
                <InputField label="Canonical" value={draft.canonical} onChange={(value) => setDraft((d) => ({ ...d, canonical: value }))} required />
                <InputField label="Werkstoff-Nr." value={draft.werkstoff_nr} onChange={(value) => setDraft((d) => ({ ...d, werkstoff_nr: value }))} />
                <InputField label="DIN-Name" value={draft.din_name} onChange={(value) => setDraft((d) => ({ ...d, din_name: value }))} />
                <InputField label="Kategorie" value={draft.category} onChange={(value) => setDraft((d) => ({ ...d, category: value }))} />
                <InputField label="Aliases, kommagetrennt" value={draft.aliases} onChange={(value) => setDraft((d) => ({ ...d, aliases: value }))} />
                <div className="grid grid-cols-2 gap-2">
                  <InputField label="HRC min" value={draft.hardness_min} onChange={(value) => setDraft((d) => ({ ...d, hardness_min: value }))} />
                  <InputField label="HRC max" value={draft.hardness_max} onChange={(value) => setDraft((d) => ({ ...d, hardness_max: value }))} />
                </div>
                <InputField label="Typischer Einsatz" value={draft.typical_use} onChange={(value) => setDraft((d) => ({ ...d, typical_use: value }))} />
                <div className="flex gap-2 pt-2">
                  <Button type="submit" disabled={busy || !draft.canonical.trim()} className="h-9 flex-1">
                    <Save className="h-4 w-4" />
                    {editingCanonical ? "Speichern" : "Anlegen"}
                  </Button>
                  {editingCanonical && (
                    <Button
                      type="button"
                      variant="outline"
                      disabled={busy}
                      onClick={() => {
                        setEditingCanonical(null);
                        setDraft(emptyDraft);
                      }}
                      className="h-9"
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  )}
                </div>
              </form>
            </section>

            <section className="overflow-hidden rounded-xl border border-border bg-card">
              <div className="flex flex-col gap-3 border-b border-border px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h2 className="text-base font-semibold text-foreground">Materialkatalog</h2>
                  <p className="text-sm text-muted-foreground">{filteredMaterials.length} von {materials.length} Eintraegen</p>
                </div>
                <Input value={materialSearch} onChange={(event) => setMaterialSearch(event.target.value)} placeholder="Material suchen..." className="h-9 sm:w-72" />
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[860px] text-sm">
                  <thead>
                    <tr className="border-b border-border text-left text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/80">
                      <th className="h-11 px-5">Canonical</th>
                      <th className="h-11 px-3">Werkstoff</th>
                      <th className="h-11 px-3">DIN</th>
                      <th className="h-11 px-3">Kategorie</th>
                      <th className="h-11 px-3">Aliases</th>
                      <th className="h-11 px-5 text-right">Aktionen</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredMaterials.slice(0, 120).map((material) => (
                      <tr key={material.canonical} className="border-b border-border/60 last:border-0">
                        <td className="px-5 py-3 font-medium text-foreground">{material.canonical}</td>
                        <td className="px-3 py-3 text-foreground">{material.werkstoff_nr || "-"}</td>
                        <td className="px-3 py-3 text-muted-foreground">{material.din_name || "-"}</td>
                        <td className="px-3 py-3 text-muted-foreground">{material.category || "-"}</td>
                        <td className="px-3 py-3 text-muted-foreground">{material.aliases?.length || 0}</td>
                        <td className="px-5 py-3">
                          <div className="flex justify-end gap-1">
                            <button
                              type="button"
                              title="Bearbeiten"
                              onClick={() => {
                                setEditingCanonical(material.canonical);
                                setDraft(draftFromMaterial(material));
                              }}
                              className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                            >
                              <Pencil className="h-4 w-4" />
                            </button>
                            <button
                              type="button"
                              title="Loeschen"
                              disabled={busy}
                              onClick={() => {
                                if (window.confirm(`${material.canonical} wirklich loeschen?`)) {
                                  deleteMaterialMutation.mutate(material.canonical);
                                }
                              }}
                              className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
                            >
                              <Trash2 className="h-4 w-4" />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        )}

        {tab === "json" && (
          <section className="mt-6 overflow-hidden rounded-xl border border-border bg-card">
            <div className="flex flex-col gap-3 border-b border-border px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-base font-semibold text-foreground">JSON-Stammdaten</h2>
                <p className="text-sm text-muted-foreground">Direkter Editor mit JSON-Validierung und Backup.</p>
              </div>
              <select
                value={selectedCatalog}
                onChange={(event) => setSelectedCatalog(event.target.value as CatalogKey)}
                className="h-9 rounded-lg border border-input bg-card px-3 text-sm text-foreground focus-visible:border-ring focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              >
                <option value="units">units.json</option>
                <option value="validation_rules">validation_rules.json</option>
                <option value="materials">materials.json</option>
              </select>
            </div>
            <div className="p-5">
              <TextArea value={jsonText} onChange={(event) => setJsonText(event.target.value)} className="min-h-[560px]" />
              <div className="mt-4 flex justify-end">
                <Button type="button" onClick={saveJson} disabled={busy} className="h-9">
                  <Save className="h-4 w-4" />
                  JSON speichern
                </Button>
              </div>
            </div>
          </section>
        )}

        {tab === "system" && (
          <div className="mt-6 grid gap-6 xl:grid-cols-[1fr_420px]">
            <section className="overflow-hidden rounded-xl border border-border bg-card">
              <SectionTitle icon={Server} title="System-Info" subtitle="Runtime, Jobs und Azure-Konfiguration ohne Secrets" />
              <div className="grid gap-4 p-5 sm:grid-cols-2">
                <Info label="App-Version" value={systemQuery.data?.app_version || "-"} />
                <Info label="Python" value={systemQuery.data?.python_version || "-"} />
                <Info label="Plattform" value={systemQuery.data?.platform || "-"} />
                <Info label="Projektpfad" value={systemQuery.data?.project_root || "-"} />
                <Info label="Jobs aktiv" value={String(systemQuery.data?.jobs.active ?? "-")} />
                <Info label="Jobs archiviert" value={String(systemQuery.data?.jobs.archived ?? "-")} />
                <Info label="Abgeschlossen" value={String(systemQuery.data?.jobs.completed ?? "-")} />
                <Info label="Korrekturen" value={String(systemQuery.data?.corrections ?? "-")} />
              </div>
            </section>

            <section className="overflow-hidden rounded-xl border border-border bg-card">
              <SectionTitle icon={Database} title="Dateien und Dienste" subtitle="Existenzpruefung und LLM-Konfigurationsstatus" />
              <div className="space-y-4 p-5">
                <div className="space-y-2">
                  {Object.entries(systemQuery.data?.files || {}).map(([key, ok]) => (
                    <div key={key} className="flex items-center justify-between gap-3 rounded-lg border border-border/70 px-3 py-2">
                      <span className="text-sm text-foreground">{key}</span>
                      <StatusBadge tone={ok ? "green" : "red"}>{ok ? "ok" : "fehlt"}</StatusBadge>
                    </div>
                  ))}
                </div>
                <div className="rounded-lg border border-border/70 p-3">
                  <p className="mb-3 text-sm font-medium text-foreground">Azure OpenAI</p>
                  <Info label="Endpoint" value={systemQuery.data?.azure_openai.azure_openai_endpoint_configured ? "konfiguriert" : "fehlt"} compact />
                  <Info label="API-Key" value={systemQuery.data?.azure_openai.azure_openai_key_configured ? "konfiguriert" : "fehlt"} compact />
                  <Info label="API-Version" value={systemQuery.data?.azure_openai.azure_openai_api_version || "-"} compact />
                  <Info label="Main Deployment" value={systemQuery.data?.azure_openai.deployment_main || "-"} compact />
                  <Info label="Mini Deployment" value={systemQuery.data?.azure_openai.deployment_mini || "-"} compact />
                </div>
                <div className="rounded-lg border border-border/70 p-3">
                  <p className="mb-3 text-sm font-medium text-foreground">Stammdaten</p>
                  {(masterSummaryQuery.data || []).map((item) => (
                    <Info
                      key={item.catalog}
                      label={item.filename}
                      value={`${item.entry_count} Eintraege - ${formatTimestamp(item.updated_at)}`}
                      compact
                    />
                  ))}
                </div>
              </div>
            </section>
          </div>
        )}
      </div>
    </DashboardShell>
  );
}
