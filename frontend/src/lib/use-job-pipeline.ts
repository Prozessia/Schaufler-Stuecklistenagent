"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";

import {
  getExportUrl,
  getJobResult,
  getJobStatus,
  retryJob,
  saveEditedCells,
  setRowExclusion,
  uploadFile,
} from "@/lib/api";
import type { CellEditPayload, JobResult } from "@/lib/api";
import { WORKSPACE_ROUTE } from "@/lib/routes";

interface UseJobPipelineArgs {
  /** Gate every network call until the session is confirmed. */
  enabled: boolean;
  /** Deep-link target: open this job directly (from the dashboard). */
  initialJobId?: string | null;
}

/**
 * Owns the upload → status-polling → result → edit/exclude/export lifecycle for
 * one job. Extracted from the workspace page so the page stays a thin view and
 * the data flow is testable in isolation.
 */
export function useJobPipeline({ enabled, initialJobId }: UseJobPipelineArgs) {
  const router = useRouter();
  const [jobId, setJobId] = useState<string | null>(initialJobId ?? null);
  const [result, setResult] = useState<JobResult | null>(null);

  // Follow deep-link changes (e.g. opening another file from the dashboard)
  // without forcing a full remount of the workspace.
  useEffect(() => {
    if (initialJobId && initialJobId !== jobId) {
      setResult(null);
      setJobId(initialJobId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialJobId]);

  const upload = useMutation({
    mutationFn: ({ file, customer }: { file: File; customer?: string }) =>
      uploadFile(file, customer),
    onSuccess: (data) => {
      setResult(null);
      setJobId(data.job_id);
    },
  });

  const retry = useMutation({
    mutationFn: (jid: string) => retryJob(jid),
    onSuccess: () => {
      setResult(null);
    },
  });

  const jobStatus = useQuery({
    queryKey: ["job-status", jobId],
    queryFn: () => getJobStatus(jobId!),
    enabled: enabled && !!jobId && !result,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "completed" || status === "failed") return false;
      return 1500;
    },
  });

  const jobCompleted = jobStatus.data?.status === "completed";
  const jobFailed = jobStatus.data?.status === "failed";

  const jobResult = useQuery({
    queryKey: ["job-result", jobId],
    queryFn: () => getJobResult(jobId!),
    enabled: enabled && !!jobId && jobCompleted && !result,
    retry: 2,
  });

  useEffect(() => {
    if (jobResult.data && !result) {
      setResult(jobResult.data);
    }
  }, [jobResult.data, result]);

  const editCells = useMutation({
    mutationFn: (edits: CellEditPayload[]) => saveEditedCells(jobId!, edits),
    onSuccess: (nextResult) => setResult(nextResult),
  });

  const excludeRows = useMutation({
    mutationFn: ({
      rowIndices,
      excluded,
    }: {
      rowIndices: number[];
      excluded: boolean;
    }) => setRowExclusion(jobId!, rowIndices, excluded),
    onSuccess: (nextResult) => setResult(nextResult),
  });

  const handleUpload = useCallback(
    (file: File, customer?: string) => upload.mutate({ file, customer }),
    [upload]
  );

  const handleRetry = useCallback(() => {
    if (!jobId) return;
    retry.mutate(jobId);
  }, [jobId, retry]);

  const handleExport = useCallback(() => {
    if (!jobId) return;
    window.open(getExportUrl(jobId), "_blank", "noopener,noreferrer");
  }, [jobId]);

  const handleSaveEdits = useCallback(
    async (edits: CellEditPayload[]) => {
      await editCells.mutateAsync(edits);
    },
    [editCells]
  );

  const handleExcludeRows = useCallback(
    async (rowIndices: number[], excluded: boolean) => {
      await excludeRows.mutateAsync({ rowIndices, excluded });
    },
    [excludeRows]
  );

  const handleNewUpload = useCallback(() => {
    setJobId(null);
    setResult(null);
    upload.reset();
    editCells.reset();
    excludeRows.reset();
    // Drop any ?jobId= deep-link so a reload doesn't reopen the old job.
    router.replace(WORKSPACE_ROUTE);
  }, [editCells, excludeRows, router, upload]);

  const isProcessing = !!jobId && !result && !jobFailed && !jobResult.isError;
  const showNewUploadBtn = !!result || jobFailed || jobResult.isError;
  const headerProgressValue = result
    ? 100
    : Math.round((jobStatus.data?.progress ?? 0) * 100);
  const headerStatusLabel = result
    ? "Review bereit"
    : jobId
      ? `${headerProgressValue}% Verarbeitung`
      : "Bereit fuer Upload";

  return {
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
    headerProgressValue,
    headerStatusLabel,
    handleUpload,
    handleRetry,
    handleExport,
    handleSaveEdits,
    handleExcludeRows,
    handleNewUpload,
  };
}
