import { describe, expect, it } from "vitest";
import { fmtSelectorTime, matchesQuery, selectorRows, specLabel } from "@/lib/selector";
import type { PocSummary } from "@/lib/types";

function mk(over: Partial<PocSummary>): PocSummary {
  return {
    poc_id: "poc_X",
    title: "Untitled",
    status: "spec_ready",
    versions: {},
    created_at: "2026-09-26T08:31:00Z",
    updated_at: "2026-09-26T08:31:00Z",
    ...over,
  };
}

describe("fmtSelectorTime", () => {
  it('formats as "d Mon HH:MM" (24h, local)', () => {
    // No trailing Z, so it parses and formats in the runner's local time deterministically.
    expect(fmtSelectorTime("2026-09-26T08:31:00")).toBe("26 Sep 08:31");
    expect(fmtSelectorTime("2026-12-01T14:05:00")).toBe("1 Dec 14:05");
  });
  it("handles missing / invalid input", () => {
    expect(fmtSelectorTime(undefined)).toBe("—");
    expect(fmtSelectorTime("nonsense")).toBe("nonsense");
  });
});

describe("specLabel", () => {
  it('renders "spec vNNN" or empty', () => {
    expect(specLabel(mk({ versions: { spec: "v003" } }))).toBe("spec v003");
    expect(specLabel(mk({ versions: {} }))).toBe("");
  });
});

describe("matchesQuery", () => {
  const p = mk({ title: "DailyDabba lunch delivery", poc_id: "poc_01ABCXYZ" });
  it("is case-insensitive on the title", () => {
    expect(matchesQuery(p, "daily")).toBe(true);
    expect(matchesQuery(p, "LUNCH")).toBe(true);
  });
  it("matches the poc id substring", () => {
    expect(matchesQuery(p, "abcx")).toBe(true);
  });
  it("empty query matches everything", () => {
    expect(matchesQuery(p, "  ")).toBe(true);
  });
  it("no match returns false", () => {
    expect(matchesQuery(p, "zzz")).toBe(false);
  });
});

describe("selectorRows", () => {
  const older = mk({ poc_id: "poc_old", title: "Older", created_at: "2026-09-20T10:00:00Z" });
  const newer = mk({ poc_id: "poc_new", title: "Newer", created_at: "2026-09-26T10:00:00Z" });
  const mid = mk({ poc_id: "poc_mid", title: "Middle", created_at: "2026-09-23T10:00:00Z" });
  const archived = mk({ poc_id: "poc_arch", title: "Archived one", created_at: "2026-09-27T10:00:00Z", ui_archived: true });

  it("hides archived POCs", () => {
    const rows = selectorRows([older, archived, newer], "");
    expect(rows.map((p) => p.poc_id)).toEqual(["poc_new", "poc_old"]);
  });

  it("sorts newest first", () => {
    const rows = selectorRows([older, newer, mid], "");
    expect(rows.map((p) => p.poc_id)).toEqual(["poc_new", "poc_mid", "poc_old"]);
  });

  it("filters by query (title or id) before sorting", () => {
    const rows = selectorRows([older, newer, mid], "old");
    expect(rows.map((p) => p.poc_id)).toEqual(["poc_old"]);
  });

  it("pins the followed POC to the top even if it isn't newest", () => {
    const rows = selectorRows([older, newer, mid], "", "poc_old");
    expect(rows.map((p) => p.poc_id)).toEqual(["poc_old", "poc_new", "poc_mid"]);
  });

  it("does not surface a followed POC that fails the filter", () => {
    const rows = selectorRows([older, newer, mid], "new", "poc_old");
    expect(rows.map((p) => p.poc_id)).toEqual(["poc_new"]);
  });
});
