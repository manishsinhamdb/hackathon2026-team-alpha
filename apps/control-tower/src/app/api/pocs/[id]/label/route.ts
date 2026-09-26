// POST /api/pocs/:id/label -> set/unset the POC nickname pocs.ui_label (+ ui_label_at). Body: { label }.
// Trimmed, <= 80 chars; an empty label unsets both fields. This is the UI's second (and last) DB write —
// agents ignore the field; everything else stays read-only.
import { NextResponse } from "next/server";
import { getDb } from "@/lib/mongo";
import type { ArchiveCollection } from "@/lib/archive";
import { setLabel } from "@/lib/label";

export const dynamic = "force-dynamic";

export async function POST(req: Request, ctx: { params: Promise<{ id: string }> }): Promise<Response> {
  const { id } = await ctx.params;
  if (!id) return NextResponse.json({ error: "poc id required" }, { status: 400 });
  let label: unknown = "";
  try {
    const body = await req.json();
    label = body?.label ?? "";
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }
  if (typeof label !== "string") return NextResponse.json({ error: "label must be a string" }, { status: 400 });
  try {
    const db = await getDb();
    const coll = db.collection("pocs") as unknown as ArchiveCollection;
    const res = await setLabel(coll, id, label);
    if (!res.ok) return NextResponse.json({ error: `no poc ${id}` }, { status: 404 });
    return NextResponse.json({ poc_id: id, ui_label: res.label });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: msg }, { status: 502 });
  }
}
