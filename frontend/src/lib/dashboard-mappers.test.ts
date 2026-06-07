import { describe, expect, it } from "vitest";

import type { JobStatus } from "@/lib/api";
import { buildKpis, jobToRecentFile, statusToLabel } from "@/lib/dashboard-mappers";

function job(overrides: Partial<JobStatus> = {}): JobStatus {
  return {
    job_id: "j1",
    status: "completed",
    filename: "bom.pdf",
    customer: "ZF",
    progress: 1,
    error: null,
    ...overrides,
  };
}

describe("statusToLabel", () => {
  it("maps backend statuses to traffic-light labels", () => {
    expect(statusToLabel("completed")).toBe("Fertig");
    expect(statusToLabel("processing")).toBe("In Prüfung");
    expect(statusToLabel("pending")).toBe("Neu");
    expect(statusToLabel("failed")).toBe("Neu");
  });
});

describe("jobToRecentFile", () => {
  it("forces 100% progress for completed jobs", () => {
    const file = jobToRecentFile(job({ status: "completed", progress: 0.3 }));
    expect(file.progressPercent).toBe(100);
    expect(file.status).toBe("Fertig");
    expect(file.description).toBeUndefined();
  });

  it("rounds in-flight progress to a percentage", () => {
    const file = jobToRecentFile(job({ status: "processing", progress: 0.426 }));
    expect(file.progressPercent).toBe(43);
    expect(file.status).toBe("In Prüfung");
  });

  it("surfaces the error message for failed jobs", () => {
    const file = jobToRecentFile(
      job({ status: "failed", progress: 0, error: "parser crashed" })
    );
    expect(file.description).toBe("Fehlgeschlagen: parser crashed");
  });

  it("falls back to a placeholder customer", () => {
    expect(jobToRecentFile(job({ customer: "" })).customer).toBe("Unbekannt");
  });
});

describe("buildKpis", () => {
  it("counts jobs by state", () => {
    const jobs: JobStatus[] = [
      job({ status: "completed" }),
      job({ status: "completed" }),
      job({ status: "processing" }),
      job({ status: "pending" }),
      job({ status: "failed" }),
    ];
    const [total, open, finished, failed] = buildKpis(jobs);
    expect(total.value).toBe("5");
    expect(open.value).toBe("2"); // processing + pending
    expect(finished.value).toBe("2");
    expect(failed.value).toBe("1");
  });

  it("handles an empty job list", () => {
    expect(buildKpis([]).map((k) => k.value)).toEqual(["0", "0", "0", "0"]);
  });
});
