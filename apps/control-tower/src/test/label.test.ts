import { describe, expect, it } from "vitest";
import type { ArchiveCollection, ArchiveUpdate } from "@/lib/archive";
import { LABEL_MAX, displayTitle, normalizeLabel, setLabel, subTitle, withLabel } from "@/lib/label";
import { matchesQuery, selectorRows } from "@/lib/selector";
import type { PocSummary } from "@/lib/types";

function fakeColl(matched: number) {
  const calls: { filter: { poc_id: string }; update: ArchiveUpdate }[] = [];
  const coll: ArchiveCollection = {
    async updateOne(filter, update) {
      calls.push({ filter, update });
      return { matchedCount: matched };
    },
  };
  return { coll, calls };
}

describe("setLabel — the nickname write", () => {
  it("sets a trimmed ui_label and a timestamp", async () => {
    const { coll, calls } = fakeColl(1);
    const res = await setLabel(coll, "poc_1", "  Tiffin   demo  ", "2026-09-26T00:00:00Z");
    expect(res).toEqual({ ok: true, matched: 1, label: "Tiffin demo" });
    expect(calls[0].filter).toEqual({ poc_id: "poc_1" });
    expect(calls[0].update).toEqual({ $set: { ui_label: "Tiffin demo", ui_label_at: "2026-09-26T00:00:00Z" } });
  });

  it("caps at 80 characters", async () => {
    const { coll, calls } = fakeColl(1);
    const res = await setLabel(coll, "poc_1", "x".repeat(200));
    expect(res.label).toHaveLength(LABEL_MAX);
    expect((calls[0].update.$set?.ui_label as string).length).toBe(80);
  });

  it("an empty (or whitespace) label $unsets both fields", async () => {
    const { coll, calls } = fakeColl(1);
    await setLabel(coll, "poc_1", "   ");
    expect(calls[0].update).toEqual({ $unset: { ui_label: "", ui_label_at: "" } });
  });

  it("only ever touches ui_label / ui_label_at", async () => {
    for (const label of ["Nick", ""]) {
      const { coll, calls } = fakeColl(1);
      await setLabel(coll, "poc_1", label);
      const touched = Object.keys({ ...(calls[0].update.$set ?? {}), ...(calls[0].update.$unset ?? {}) });
      expect(touched.sort()).toEqual(["ui_label", "ui_label_at"]);
    }
  });

  it("reports ok=false when no poc matches", async () => {
    const { coll } = fakeColl(0);
    expect((await setLabel(coll, "missing", "x")).ok).toBe(false);
  });

  it("normalizeLabel rejects non-strings", () => {
    expect(normalizeLabel(42)).toBe("");
    expect(normalizeLabel(null)).toBe("");
  });
});

describe("display helpers + selector search", () => {
  const nick: PocSummary = { poc_id: "poc_a", title: "DailyDabba", status: "spec_ready", versions: {}, created_at: "2026-09-26T08:00:00Z", updated_at: "x", ui_label: "Tiffin" };
  const plain: PocSummary = { poc_id: "poc_b", title: "Kirana", status: "spec_ready", versions: {}, created_at: "2026-09-26T09:00:00Z", updated_at: "x" };

  it("shows the nickname with the agent title beneath", () => {
    expect(displayTitle(nick)).toBe("Tiffin");
    expect(subTitle(nick)).toBe("DailyDabba");
    expect(displayTitle(plain)).toBe("Kirana");
    expect(subTitle(plain)).toBeUndefined();
  });

  it("selector search matches the nickname too", () => {
    expect(matchesQuery(nick, "tiff")).toBe(true);
    expect(matchesQuery(nick, "dabba")).toBe(true);
    expect(selectorRows([nick, plain], "tiffin").map((p) => p.poc_id)).toEqual(["poc_a"]);
  });

  it("withLabel applies / clears locally", () => {
    expect(withLabel([plain], "poc_b", " New ")[0].ui_label).toBe("New");
    expect("ui_label" in withLabel([nick], "poc_a", "")[0]).toBe(false);
  });
});
