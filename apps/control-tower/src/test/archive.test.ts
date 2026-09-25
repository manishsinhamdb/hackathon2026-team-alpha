import { describe, expect, it } from "vitest";
import { setArchived, type ArchiveCollection, type ArchiveUpdate } from "@/lib/archive";

// A fake pocs collection that records the updateOne call and reports a match count.
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

describe("setArchived — the only DB write", () => {
  it("archives with ui_archived=true and a timestamp", async () => {
    const { coll, calls } = fakeColl(1);
    const res = await setArchived(coll, "poc_1", true, "2026-09-26T00:00:00Z");
    expect(res).toEqual({ ok: true, matched: 1, archived: true });
    expect(calls).toHaveLength(1);
    expect(calls[0].filter).toEqual({ poc_id: "poc_1" });
    expect(calls[0].update).toEqual({ $set: { ui_archived: true, ui_archived_at: "2026-09-26T00:00:00Z" } });
  });

  it("restores by unsetting the timestamp and clearing the flag", async () => {
    const { coll, calls } = fakeColl(1);
    const res = await setArchived(coll, "poc_1", false, "2026-09-26T00:00:00Z");
    expect(res.archived).toBe(false);
    expect(calls[0].update).toEqual({ $set: { ui_archived: false }, $unset: { ui_archived_at: "" } });
  });

  it("reports ok=false when the poc id does not match", async () => {
    const { coll } = fakeColl(0);
    const res = await setArchived(coll, "missing", true);
    expect(res.ok).toBe(false);
    expect(res.matched).toBe(0);
  });

  it("only ever touches ui_archived / ui_archived_at (never platform fields)", async () => {
    const { coll, calls } = fakeColl(1);
    await setArchived(coll, "poc_1", true);
    const touched = Object.keys({ ...(calls[0].update.$set ?? {}), ...(calls[0].update.$unset ?? {}) });
    expect(touched.sort()).toEqual(["ui_archived", "ui_archived_at"]);
  });
});
