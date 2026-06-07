"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
} from "react";
// Type-only import is erased at build time, so it does NOT evaluate pdfjs here.
import type { DocumentProps, PageProps } from "react-pdf";

interface ReactPdfModule {
  Document: ComponentType<DocumentProps>;
  Page: ComponentType<PageProps>;
}

// Stable reference (module scope) so the Document doesn't reload every render.
// withCredentials is forwarded to pdfjs' getDocument so the session cookie is
// sent with cross-origin PDF requests.
const PDF_OPTIONS = { withCredentials: true } as const;

interface PdfSourceViewerProps {
  url: string;
  /** 1-based page number to render. */
  page: number;
  /** Source bbox [x0, y0, x1, y1] in PDF points (PyMuPDF top-left origin). */
  highlightBbox?: number[] | null;
  /** Whether the highlight belongs to the currently rendered page. */
  highlightOnThisPage?: boolean;
  reloadKey?: number;
  onDocumentLoad?: (numPages: number) => void;
  onError?: (message: string) => void;
}

/**
 * Renders a single PDF page on a canvas and overlays a highlight rectangle at the
 * exact source location of the selected cell. The bbox comes from the scorer's
 * deterministic source_location (RB-1) — page + [x0,y0,x1,y1] in PDF points.
 *
 * react-pdf (and its bundled pdfjs ESM) is imported at RUNTIME inside a try/catch:
 * pdfjs' pdf.mjs can throw synchronously during webpack module evaluation
 * ("Object.defineProperty called on non-object"), which a React error boundary
 * cannot catch. Loading it via import() turns that into a rejected promise we handle
 * gracefully — the parent then falls back to the iframe viewer.
 */
export function PdfSourceViewer({
  url,
  page,
  highlightBbox,
  highlightOnThisPage,
  reloadKey,
  onDocumentLoad,
  onError,
}: PdfSourceViewerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const highlightRef = useRef<HTMLDivElement | null>(null);
  const [containerWidth, setContainerWidth] = useState(640);
  // Store the page's intrinsic width (PDF points) and derive the scale on every
  // render, so the highlight stays aligned when the pane is resized.
  const [originalWidth, setOriginalWidth] = useState(0);
  const [pdfModule, setPdfModule] = useState<ReactPdfModule | null>(null);

  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  // Load react-pdf once, guarded. A pdfjs eval failure degrades to the iframe.
  useEffect(() => {
    let cancelled = false;
    import("react-pdf")
      .then((rp) => {
        rp.pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
        if (!cancelled) {
          setPdfModule({ Document: rp.Document, Page: rp.Page });
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          const message =
            error instanceof Error
              ? error.message
              : "PDF-Renderer konnte nicht geladen werden.";
          onErrorRef.current?.(message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const element = containerRef.current;
    if (!element || typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver(() => {
      const next = element.clientWidth - 24;
      if (next > 0) {
        setContainerWidth(next);
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const handlePageLoad = useCallback((loadedPage: { originalWidth: number }) => {
    if (loadedPage.originalWidth > 0) {
      setOriginalWidth(loadedPage.originalWidth);
    }
  }, []);

  const highlightStyle = useMemo(() => {
    const scale = originalWidth > 0 ? containerWidth / originalWidth : 0;
    if (
      !highlightOnThisPage ||
      !highlightBbox ||
      highlightBbox.length !== 4 ||
      scale <= 0
    ) {
      return null;
    }
    const [x0, y0, x1, y1] = highlightBbox;
    return {
      left: `${x0 * scale}px`,
      top: `${y0 * scale}px`,
      width: `${Math.max(x1 - x0, 1) * scale}px`,
      height: `${Math.max(y1 - y0, 1) * scale}px`,
    };
  }, [highlightBbox, highlightOnThisPage, containerWidth, originalWidth]);

  // Scroll the highlight into view whenever it changes.
  useEffect(() => {
    if (highlightStyle && highlightRef.current) {
      highlightRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [highlightStyle, page]);

  if (!pdfModule) {
    return (
      <div ref={containerRef} className="pdf-source-viewer">
        <div className="pdf-source-loading">PDF-Renderer wird geladen…</div>
      </div>
    );
  }

  const { Document, Page } = pdfModule;

  return (
    <div ref={containerRef} className="pdf-source-viewer">
      <Document
        key={`${url}-${reloadKey ?? 0}`}
        file={url}
        // Pass the session cookie with the PDF fetch. Without withCredentials,
        // pdfjs fetches cross-origin without credentials and the request is
        // rejected (401), forcing the iframe fallback for every document.
        options={PDF_OPTIONS}
        loading={<div className="pdf-source-loading">PDF wird geladen…</div>}
        onLoadSuccess={({ numPages }) => onDocumentLoad?.(numPages)}
        onLoadError={(error) =>
          onError?.(error?.message || "PDF konnte nicht geladen werden.")
        }
      >
        <div className="pdf-source-page-wrap">
          <Page
            pageNumber={page}
            width={containerWidth}
            renderTextLayer={false}
            renderAnnotationLayer={false}
            onLoadSuccess={handlePageLoad}
            onRenderError={(error) =>
              onError?.(error?.message || "PDF-Seite konnte nicht gerendert werden.")
            }
          />
          {highlightStyle && (
            <div
              ref={highlightRef}
              className="pdf-source-highlight"
              style={highlightStyle}
            />
          )}
        </div>
      </Document>
    </div>
  );
}

export default PdfSourceViewer;
