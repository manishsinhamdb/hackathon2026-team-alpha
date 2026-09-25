// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { StageStep } from "@/components/Stepper";
import type { StageCell } from "@/lib/types";

// A fixed "now" so a running run's elapsed time is deterministic.
const NOW = Date.parse("2026-09-25T17:00:00Z");

afterEach(() => cleanup());

function renderStep(stage: StageCell) {
  return render(<StageStep stage={stage} nowMs={NOW} isLast={false} />);
}

describe("StageStep render states", () => {
  it("renders a not-started run cell", () => {
    const { container, getByText } = renderStep({ key: "code", label: "Code", kind: "run", status: "not_started" });
    expect(getByText("Code")).toBeTruthy();
    expect(getByText("Not started")).toBeTruthy();
    expect(container.querySelector('[data-status="not_started"]')).toBeTruthy();
  });

  it("renders a running run cell with a live elapsed timer", () => {
    const { getByText } = renderStep({
      key: "code",
      label: "Code",
      kind: "run",
      status: "running",
      run_id: "run_live",
      started_at: "2026-09-25T16:59:00Z", // 60s before NOW
    });
    // "Running · 1m 0s"
    expect(getByText(/Running/)).toBeTruthy();
    expect(getByText("1m 0s")).toBeTruthy();
    expect(getByText("run_live")).toBeTruthy();
  });

  it("renders a succeeded run cell with a final duration", () => {
    const { getByText } = renderStep({
      key: "draft",
      label: "Draft",
      kind: "run",
      status: "succeeded",
      run_id: "run_ok",
      duration_ms: 347_000, // 5m 47s
    });
    expect(getByText(/Done/)).toBeTruthy();
    expect(getByText("5m 47s")).toBeTruthy();
  });

  it("renders a failed run cell with the error reason", () => {
    const { getByText } = renderStep({
      key: "deploy",
      label: "Deploy",
      kind: "run",
      status: "failed",
      run_id: "run_bad",
      error: { code: "ATLAS_403", message: "network access list" },
    });
    expect(getByText(/Failed/)).toBeTruthy();
    expect(getByText(/ATLAS_403/)).toBeTruthy();
    expect(getByText(/network access list/)).toBeTruthy();
  });

  it("renders an approved gate cell with version and approver", () => {
    const { getByText, container } = renderStep({
      key: "spec_approved",
      label: "Spec approved",
      kind: "gate",
      status: "succeeded",
      approvedVersion: "v003",
      approvedBy: "u_cc",
    });
    expect(getByText("v003")).toBeTruthy();
    expect(getByText("u_cc")).toBeTruthy();
    expect(container.querySelector('[data-kind="gate"]')).toBeTruthy();
  });

  it("renders a not-started gate as awaiting approval", () => {
    const { getByText } = renderStep({ key: "code_approved", label: "Code approved", kind: "gate", status: "not_started" });
    expect(getByText("awaiting approval")).toBeTruthy();
  });
});
