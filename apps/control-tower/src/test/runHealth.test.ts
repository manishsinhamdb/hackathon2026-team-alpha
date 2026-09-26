import { describe, expect, it } from "vitest";
import { ABANDON_MS, isAbandoned, lastBeatAt, retryLabel, retryMessage } from "@/lib/runHealth";

const NOW = Date.parse("2026-09-26T12:00:00Z");
const ago = (s: number) => new Date(NOW - s * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");

describe("isAbandoned — the 180 s heartbeat rule", () => {
  it("running with a fresh heartbeat is alive", () => {
    expect(isAbandoned({ status: "running", started_at: ago(3600), heartbeat_at: ago(20) }, NOW)).toBe(false);
  });

  it("running with a heartbeat older than 180 s is abandoned (queued too)", () => {
    expect(isAbandoned({ status: "running", started_at: ago(3600), heartbeat_at: ago(181) }, NOW)).toBe(true);
    expect(isAbandoned({ status: "queued", started_at: ago(400) }, NOW)).toBe(true);
  });

  it("exactly 180 s is still alive (strictly older than)", () => {
    expect(ABANDON_MS).toBe(180_000);
    expect(isAbandoned({ status: "running", heartbeat_at: ago(180) }, NOW)).toBe(false);
  });

  it("uses the NEWEST of heartbeat_at / updated_at / started_at (older runs have no heartbeat)", () => {
    expect(isAbandoned({ status: "running", started_at: ago(3600), updated_at: ago(30) }, NOW)).toBe(false);
    expect(isAbandoned({ status: "running", started_at: ago(3600), heartbeat_at: ago(600), updated_at: ago(10) }, NOW)).toBe(false);
    expect(lastBeatAt({ status: "running", started_at: ago(50), heartbeat_at: ago(600), updated_at: ago(100) })).toBe(ago(50));
  });

  it("failed with error.code ABANDONED is abandoned; other failures are not", () => {
    expect(isAbandoned({ status: "failed", error: { code: "ABANDONED" } }, NOW)).toBe(true);
    expect(isAbandoned({ status: "failed", error: { code: "CODE_RUN_FAILED" } }, NOW)).toBe(false);
  });

  it("terminal / waiting runs and runs with no timestamps are never abandoned", () => {
    expect(isAbandoned({ status: "succeeded", started_at: ago(9999) }, NOW)).toBe(false);
    expect(isAbandoned({ status: "waiting_user", started_at: ago(9999) }, NOW)).toBe(false);
    expect(isAbandoned({ status: "running" }, NOW)).toBe(false);
  });
});

describe("retryMessage / retryLabel", () => {
  it("is exactly the message the chat agent understands", () => {
    expect(retryMessage("code", "poc_01ABC", "run_01XYZ")).toBe(
      "Retry the code stage for poc_01ABC (continue run run_01XYZ if it can be resumed).",
    );
    expect(retryMessage("test", "poc_1", "run_2")).toBe("Retry the test stage for poc_1 (continue run run_2 if it can be resumed).");
  });

  it("Continue once any task or step is done, else Retry", () => {
    expect(retryLabel([], [])).toBe("Retry");
    expect(retryLabel([{ status: "running" }], [{ status: "failed" }])).toBe("Retry");
    expect(retryLabel([{ status: "succeeded" }], [])).toBe("Continue");
    expect(retryLabel([], [{ status: "succeeded" }])).toBe("Continue");
  });
});
