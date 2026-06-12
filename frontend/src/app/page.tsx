"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import ResultTable from "@/components/result-table";
import { UploadDropzone } from "@/components/upload-dropzone";
import { ProcessingStatus } from "@/components/processing-status";
import { useAuthGuard } from "@/lib/use-auth";
import { useJobPipeline } from "@/lib/use-job-pipeline";
import { AlertCircle, Loader2, Upload } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

function WorkspaceView() {
  const searchParams = useSearchParams();
  const initialJobId = searchParams.get("jobId");
  const viewParam = searchParams.get("view");
  const paneMode = viewParam === "pdf" || viewParam === "grid" ? viewParam : "both";

  const authQuery = useAuthGuard();
  const isAuthed = !!authQuery.data;

  const {
    jobId,
    result,
    upload,
    retry,
    jobStatus,
    jobResult,
    editCells,
    excludeRows,
    jobCompleted,
    jobFailed,
    isProcessing,
    showNewUploadBtn,
    headerStatusLabel,
    handleUpload,
    handleRetry,
    handleExport,
    handleSaveEdits,
    handleExcludeRows,
    handleNewUpload,
  } = useJobPipeline({ enabled: isAuthed, initialJobId });

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

  if (result) {
    return (
      <div className="flex h-screen flex-col overflow-hidden bg-background pt-16">
        <main className="flex min-h-0 flex-1 flex-col gap-3 px-4 pb-4 pt-4 sm:px-5">
          {editCells.isError && (
            <div className="flex shrink-0 items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              <AlertCircle className="h-4 w-4 shrink-0" />
              <span>
                Speichern fehlgeschlagen:{" "}
                {editCells.error instanceof Error ? editCells.error.message : "Unbekannter Fehler"}
              </span>
            </div>
          )}

          {excludeRows.isError && (
            <div className="flex shrink-0 items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              <AlertCircle className="h-4 w-4 shrink-0" />
              <span>
                Zeilenaktion fehlgeschlagen:{" "}
                {excludeRows.error instanceof Error ? excludeRows.error.message : "Unbekannter Fehler"}
              </span>
            </div>
          )}

          <div className="min-h-0 flex-1">
            <ResultTable
              result={result}
              onSaveEdits={handleSaveEdits}
              onExcludeRows={handleExcludeRows}
              onBack={handleNewUpload}
              onExport={handleExport}
              isSaving={editCells.isPending}
              isExcluding={excludeRows.isPending}
              paneMode={paneMode}
            />
          </div>
        </main>
      </div>
    );
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4 py-10">
      <div className="w-full max-w-xl space-y-6">
        <div className="text-center">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[hsl(var(--primary))]">
            BOM Review Workspace
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-foreground">
            Neue Stueckliste hochladen
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">{headerStatusLabel}</p>
        </div>

        {!jobId && (
          <UploadDropzone
            onUpload={(file, customer) => handleUpload(file, customer)}
            isUploading={upload.isPending}
          />
        )}

        {upload.isError && (
          <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            <AlertCircle className="h-4 w-4 shrink-0" />
            Upload fehlgeschlagen: {upload.error.message}
          </div>
        )}

        {isProcessing && jobStatus.data && <ProcessingStatus job={jobStatus.data} />}

        {isProcessing && jobCompleted && !jobResult.data && !jobResult.isError && (
          <Card>
            <CardContent className="flex items-center gap-3 p-6">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              <span className="text-sm text-muted-foreground">Ergebnis wird geladen...</span>
            </CardContent>
          </Card>
        )}

        {jobFailed && jobStatus.data && (
          <ProcessingStatus
            job={jobStatus.data}
            onRetry={handleRetry}
            isRetrying={retry.isPending}
          />
        )}

        {jobResult.isError && (
          <div className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <div>
              <p className="font-medium">Ergebnis konnte nicht geladen werden</p>
              <p className="mt-0.5 text-xs text-destructive/80">
                {jobResult.error instanceof Error ? jobResult.error.message : "Unbekannter Fehler"}
              </p>
            </div>
          </div>
        )}

        {showNewUploadBtn && (
          <div className="flex justify-center">
            <Button type="button" variant="outline" onClick={handleNewUpload} className="h-10">
              <Upload className="mr-2 h-4 w-4" />
              Neue Datei
            </Button>
          </div>
        )}
      </div>
    </main>
  );
}

export default function Home() {
  // useSearchParams() requires a Suspense boundary in the App Router.
  return (
    <Suspense fallback={null}>
      <WorkspaceView />
    </Suspense>
  );
}
