"use client";

import { useCallback, useState } from "react";
import { Upload, FileUp, X, AlertCircle } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

interface UploadDropzoneProps {
  onUpload: (file: File, customer?: string) => void;
  isUploading: boolean;
}

const ACCEPTED_TYPES = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-excel",
  "text/csv",
];

const ACCEPTED_EXTENSIONS = [".pdf", ".xlsx", ".xls", ".csv"];

function isAcceptedFile(file: File): boolean {
  const ext = file.name.toLowerCase().slice(file.name.lastIndexOf("."));
  return ACCEPTED_EXTENSIONS.includes(ext) || ACCEPTED_TYPES.includes(file.type);
}

export function UploadDropzone({ onUpload, isUploading }: UploadDropzoneProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [customer, setCustomer] = useState("");

  const handleFile = useCallback(
    (file: File) => {
      setError(null);
      if (!isAcceptedFile(file)) {
        setError("Nicht unterstütztes Format. Erlaubt: PDF, Excel, CSV");
        return;
      }
      if (file.size > 50 * 1024 * 1024) {
        setError("Datei zu groß (max. 50 MB)");
        return;
      }
      setSelectedFile(file);
    },
    []
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    },
    [handleFile]
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) handleFile(file);
      e.target.value = "";
    },
    [handleFile]
  );

  const handleUploadClick = () => {
    if (selectedFile) {
      onUpload(selectedFile, customer.trim() || undefined);
    }
  };

  const clearFile = () => {
    setSelectedFile(null);
    setError(null);
  };

  return (
    <Card
      className={cn(
        "rounded-[1.75rem] border-2 border-dashed transition-all duration-200",
        isDragging && "border-brand bg-brand/5 shadow-[0_22px_48px_rgba(42,111,176,0.14)]",
        error && "border-destructive/40",
        !isDragging && !error && "border-[var(--line-subtle)] hover:border-brand/35"
      )}
    >
      <CardContent className="p-6 sm:p-8">
        <div
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          className="flex flex-col items-center gap-5 text-center"
        >
          {!selectedFile ? (
            <>
              <p className="brand-kicker text-[var(--brand)]">Upload</p>
              <div className="rounded-[1.5rem] bg-[var(--surface-subtle)] p-4 ring-1 ring-[var(--line-subtle)]">
                <Upload className="h-8 w-8 text-[var(--brand)]" />
              </div>
              <div>
                <p className="text-2xl font-bold tracking-[-0.03em] text-[var(--ink-900)]">
                  Stückliste hochladen
                </p>
                <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
                  Drag & Drop oder klicken — PDF, Excel, CSV (max. 50 MB)
                </p>
              </div>
              <div className="flex flex-wrap justify-center gap-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--text-secondary)]">
                <span className="rounded-full border border-[var(--line-subtle)] bg-[var(--surface-subtle)] px-3 py-1.5">PDF</span>
                <span className="rounded-full border border-[var(--line-subtle)] bg-[var(--surface-subtle)] px-3 py-1.5">Excel</span>
                <span className="rounded-full border border-[var(--line-subtle)] bg-[var(--surface-subtle)] px-3 py-1.5">CSV</span>
                <span className="rounded-full border border-[var(--line-subtle)] bg-[var(--surface-subtle)] px-3 py-1.5">max. 50 MB</span>
              </div>
              <div className="w-full max-w-xs">
                <Input
                  type="text"
                  placeholder="Kunde (optional)"
                  value={customer}
                  onChange={(e) => setCustomer(e.target.value)}
                  maxLength={100}
                  disabled={isUploading}
                  className="h-10 text-sm"
                />
              </div>
              <label className="cursor-pointer">
                <input
                  type="file"
                  className="hidden"
                  accept=".pdf,.xlsx,.xls,.csv"
                  onChange={handleInputChange}
                  disabled={isUploading}
                />
                <span className={cn(
                  "inline-flex h-11 items-center justify-center rounded-2xl px-5 text-sm font-semibold transition-colors",
                  "bg-brand text-white shadow-[0_12px_28px_rgba(42,111,176,0.22)] hover:bg-brand-hover",
                  isUploading && "pointer-events-none opacity-50"
                )}>
                  <FileUp className="mr-2 h-4 w-4" />
                  Datei auswählen
                </span>
              </label>
            </>
          ) : (
            <>
              <p className="brand-kicker text-[var(--brand)]">Bereit</p>
              <div className="rounded-[1.5rem] bg-[var(--surface-subtle)] p-4 ring-1 ring-[var(--line-subtle)]">
                <FileUp className="h-8 w-8 text-[var(--brand)]" />
              </div>
              <div className="flex items-center gap-2">
                <p className="text-lg font-semibold text-[var(--ink-900)]">{selectedFile.name}</p>
                {!isUploading && (
                  <button
                    onClick={clearFile}
                    className="rounded-full p-1 text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-subtle)] hover:text-[var(--brand)]"
                  >
                    <X className="h-4 w-4" />
                  </button>
                )}
              </div>
              <p className="text-sm text-[var(--text-secondary)]">
                {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
              </p>
              <div className="w-full max-w-xs">
                <Input
                  type="text"
                  placeholder="Kunde (optional)"
                  value={customer}
                  onChange={(e) => setCustomer(e.target.value)}
                  maxLength={100}
                  disabled={isUploading}
                  className="h-10 text-sm"
                />
              </div>
              <Button
                onClick={handleUploadClick}
                disabled={isUploading}
                className="mt-2 h-11 rounded-2xl px-5 text-sm font-semibold"
              >
                {isUploading ? (
                  <>
                    <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                    Wird verarbeitet...
                  </>
                ) : (
                  <>
                    <Upload className="mr-2 h-4 w-4" />
                    Verarbeitung starten
                  </>
                )}
              </Button>
            </>
          )}

          {error && (
            <div className="mt-2 flex items-center gap-2 text-sm text-destructive">
              <AlertCircle className="h-4 w-4" />
              {error}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
