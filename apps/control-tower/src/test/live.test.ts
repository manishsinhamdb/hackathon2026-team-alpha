import { describe, expect, it } from "vitest";
import { ACTIVE_POLL_MS, IDLE_POLL_MS, anyRunActive, pocIdIn, pollIntervalMs } from "@/lib/live";
import type { PocDetail, RunView } from "@/lib/types";

function detailWithRuns(statuses: RunView["status"][]): PocDetail {
  const runs = statuses.map((status, i) => ({
    run_id: `run_${i}`,
    stage: "draft" as const,
    status,
    duration_ms: null,
    steps: [],
  }));
  return { runs } as unknown as PocDetail;
}

describe("pocIdIn (auto-follow detection, feature 2)", () => {
  it("finds a poc id embedded in an agent reply", () => {
    expect(pocIdIn("Drafted your POC poc_01M3DPW17ND98R1K9RXV2TYWNR — spec v001 is ready.")).toBe(
      "poc_01M3DPW17ND98R1K9RXV2TYWNR",
    );
  });
  it("returns undefined when the reply names no POC", () => {
    expect(pocIdIn("How's it going? Nothing running yet.")).toBeUndefined();
  });
  it("does not mistake a run id for a poc id", () => {
    expect(pocIdIn("The draft run run_01M3DPWZZPWCEVSZ593A04043F succeeded.")).toBeUndefined();
  });
});

describe("adaptive poll cadence (feature 4)", () => {
  it("polls fast (15s) while any run is running/queued/waiting", () => {
    expect(anyRunActive(detailWithRuns(["succeeded", "running"]))).toBe(true);
    expect(pollIntervalMs(detailWithRuns(["running"]))).toBe(ACTIVE_POLL_MS);
    expect(pollIntervalMs(detailWithRuns(["queued"]))).toBe(ACTIVE_POLL_MS);
    expect(pollIntervalMs(detailWithRuns(["waiting_user"]))).toBe(ACTIVE_POLL_MS);
  });
  it("polls slow (2min) when the POC is idle", () => {
    expect(anyRunActive(detailWithRuns(["succeeded", "failed", "cancelled"]))).toBe(false);
    expect(pollIntervalMs(detailWithRuns(["succeeded"]))).toBe(IDLE_POLL_MS);
    expect(pollIntervalMs(detailWithRuns([]))).toBe(IDLE_POLL_MS);
  });
});
