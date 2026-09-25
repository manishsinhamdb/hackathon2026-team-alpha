// POST /api/pocs/:id/archive -> set/unset pocs.ui_archived. Body: { archived: boolean }. This is the ONLY
// write the UI makes to the platform DB (agents ignore the field). Everything else stays read-only.
import { NextResponse } from "next/server";
import { getDb } from "@/lib/mongo";
import { setArchived, type ArchiveCollection } from "@/lib/archive";

export const dynamic = "force-dynamic";

export async function POST(req: Request, ctx: { params: Promise<{ id: string }> }): Promise<Response> {
  const { id } = await ctx.params;
  if (!id) return NextResponse.json({ error: "poc id required" }, { status: 400 });
  let archived = true;
  try {
    const body = await req.json();
    archived = body?.archived !== false; // default to archiving; explicit false restores
  } catch {
    /* empty body -> archive */
  }
  try {
    const db = await getDb();
    const coll = db.collection("pocs") as unknown as ArchiveCollection;
    const res = await setArchived(coll, id, archived);
    if (!res.ok) return NextResponse.json({ error: `no poc ${id}` }, { status: 404 });
    return NextResponse.json({ poc_id: id, ui_archived: res.archived });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: msg }, { status: 502 });
  }
}
