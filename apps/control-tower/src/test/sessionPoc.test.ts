import { describe, expect, it } from "vitest";
import { resolveSessionPoc } from "@/lib/sessionPoc";
import type { PocSummary } from "@/lib/types";

function mk(over: Partial<PocSummary>): PocSummary {
  return {
    poc_id: "poc_X",
    title: "T",
    status: "spec_ready",
    versions: {},
    created_at: "2026-09-20T10:00:00Z",
    updated_at: "2026-09-20T10:00:00Z",
    ...over,
  };
}

describe("resolveSessionPoc (item 4 best-effort fallback)", () => {
  it("returns the newest non-archived POC", () => {
    const pocs = [
      mk({ poc_id: "poc_old", title: "Old", created_at: "2026-09-20T10:00:00Z" }),
      mk({ poc_id: "poc_new", title: "New", created_at: "2026-09-26T10:00:00Z" }),
      mk({ poc_id: "poc_mid", title: "Mid", created_at: "2026-09-23T10:00:00Z" }),
    ];
    expect(resolveSessionPoc(pocs)).toEqual({ poc_id: "poc_new", title: "New" });
  });

  it("skips archived POCs even if newer", () => {
    const pocs = [
      mk({ poc_id: "poc_new", title: "New", created_at: "2026-09-26T10:00:00Z" }),
      mk({ poc_id: "poc_arch", title: "Arch", created_at: "2026-09-27T10:00:00Z", ui_archived: true }),
    ];
    expect(resolveSessionPoc(pocs)).toEqual({ poc_id: "poc_new", title: "New" });
  });

  it("returns null when there is nothing to resolve", () => {
    expect(resolveSessionPoc([])).toBeNull();
    expect(resolveSessionPoc([mk({ ui_archived: true })])).toBeNull();
  });
});
