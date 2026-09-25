// The ONE write this UI makes to the platform DB: set/unset `pocs.ui_archived` (+ `ui_archived_at`).
// Agents ignore the field; the workspace selector hides archived POCs and the library's Archived filter
// shows them. Kept as a pure function over a minimal collection interface so it is unit-testable with a
// fake DB (see src/test/archive.test.ts) — the route wires in the real Mongo collection.

export interface ArchiveUpdate {
  $set?: Record<string, unknown>;
  $unset?: Record<string, unknown>;
}

export interface ArchiveCollection {
  updateOne(
    filter: { poc_id: string },
    update: ArchiveUpdate,
  ): Promise<{ matchedCount: number }>;
}

export async function setArchived(
  coll: ArchiveCollection,
  pocId: string,
  archived: boolean,
  nowIso: string = new Date().toISOString(),
): Promise<{ ok: boolean; matched: number; archived: boolean }> {
  const update: ArchiveUpdate = archived
    ? { $set: { ui_archived: true, ui_archived_at: nowIso } }
    : { $set: { ui_archived: false }, $unset: { ui_archived_at: "" } };
  const res = await coll.updateOne({ poc_id: pocId }, update);
  return { ok: res.matchedCount > 0, matched: res.matchedCount, archived };
}
