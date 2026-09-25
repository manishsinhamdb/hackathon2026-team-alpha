// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { StageStep } from "@/components/Stepper";
import type { StageCell } from "@/lib/types";

// A fixed "now" so a running run's elapsed time is deterministic.
const NOW = Date.parse("2026-09-25T17:00:00Z");

afterEach(() => cleanup());

function renderStep(stage: StageCell, teardownHint?: string) {
  return render(<StageStep stage={stage} nowMs={NOW} isLast={false} teardownHint={teardownHint} />);
}

describe("StageStep state mapping", () => {
  it("not-started run cell shows its hint and data-status", () => {
    const { container } = renderStep({ key: "code", label: "Code", kind: "run", status: "not_started" });
    const cell = container.querySelector('[data-status="not_started"]');
    expect(cell).toBeTruthy();
    expect(cell?.getAttribute("data-kind")).toBe("run");
    expect(container.textContent).toContain("Code");
    expect(container.textContent).toContain("coders"); // HINTS["code"]
  });

  it("running run cell shows a live elapsed timer", () => {
    const { container, getByText } = renderStep({
      key: "code", label: "Code", kind: "run", status: "running",
      run_id: "run_live", started_at: "2026-09-25T16:59:00Z", // 60s before NOW
    });
    expect(getByText("Code")).toBeTruthy();
    expect(container.querySelector('[data-status="running"]')).toBeTruthy();
    expect(container.textContent).toContain("running · 1m 00s");
  });

  it("succeeded run cell shows the final duration and version", () => {
    const { container } = renderStep({
      key: "draft", label: "Draft", kind: "run", status: "succeeded",
      run_id: "run_ok", duration_ms: 347_000, version: "v003", // 5m 47s
    });
    expect(container.querySelector('[data-status="succeeded"]')).toBeTruthy();
    expect(container.textContent).toContain("5m 47s · v003");
  });

  it("failed run cell shows the error code", () => {
    const { container } = renderStep({
      key: "deploy", label: "Deploy", kind: "run", status: "failed",
      run_id: "run_bad", error: { code: "ATLAS_403", message: "network access list" },
    });
    expect(container.querySelector('[data-status="failed"]')).toBeTruthy();
    expect(container.textContent).toContain("failed · ATLAS_403");
  });

  it("approved gate cell shows 'gate · by <initials> <time>'", () => {
    const { container } = renderStep({
      key: "spec_approved", label: "Spec approved", kind: "gate", status: "succeeded",
      approvedVersion: "v003", approvedBy: "u_cc", ended_at: "2026-09-25T13:16:00Z",
    });
    const cell = container.querySelector('[data-kind="gate"]');
    expect(cell).toBeTruthy();
    expect(container.textContent).toContain("gate · by UC");
  });

  it("not-started gate cell shows 'gate'", () => {
    const { container } = renderStep({ key: "code_approved", label: "Code approved", kind: "gate", status: "not_started" });
    const cell = container.querySelector('[data-status="not_started"][data-kind="gate"]');
    expect(cell).toBeTruthy();
    expect(container.textContent).toContain("gate");
  });

  it("teardown not-started cell uses the live resource hint", () => {
    const { container } = renderStep({ key: "teardown", label: "Torn down", kind: "run", status: "not_started" }, "0 active");
    expect(container.textContent).toContain("0 active");
  });
});
