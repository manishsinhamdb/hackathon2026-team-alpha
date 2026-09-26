// GET /api/sessions/:id/poc -> best-effort POC for a UI session (Round 4, item 4).
//
// See src/lib/sessionPoc.ts for why this is a fallback (the DB has no session_id ↔ poc_id link). It returns
// the newest non-archived POC owned by the UI user, or {poc:null}. The client uses it only on first load
// when nothing is selected; the session-correct path is client-side new-POC-during-turn detection.
import { NextResponse } from "next/server";
import { getDb } from "@/lib/mongo";
import { getServerConfig } from "@/lib/env";
import { toPocSummary, type RawPoc } from "@/lib/aggregate";
import { resolveSessionPoc } from "@/lib/sessionPoc";

export const dynamic = "force-dynamic";

export async function GET(_req: Request, ctx: { params: Promise<{ id: string }> }): Promise<Response> {
  const { id } = await ctx.params;
  if (!id) return NextResponse.json({ error: "session id required" }, { status: 400 });
  try {
    const cfg = getServerConfig();
    const db = await getDb();
    const raw = await db
      .collection<RawPoc>("pocs")
      .find({ owner_user_id: cfg.uiUserId }, { projection: { _id: 0 } })
      .toArray();
    const poc = resolveSessionPoc(raw.map(toPocSummary));
    return NextResponse.json({ poc });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("[sessions/poc] failed:", msg);
    return NextResponse.json({ poc: null });
  }
}
