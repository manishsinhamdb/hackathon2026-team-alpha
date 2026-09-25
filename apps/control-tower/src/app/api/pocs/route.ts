// GET /api/pocs -> recent POCs (id, title, status, versions, updated_at). READ-ONLY.
import { NextResponse } from "next/server";
import { getDb } from "@/lib/mongo";
import { toPocSummary, type RawPoc } from "@/lib/aggregate";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  try {
    const db = await getDb();
    const docs = await db
      .collection<RawPoc>("pocs")
      .find({}, { projection: { _id: 0 } })
      .sort({ updated_at: -1 })
      .limit(50)
      .toArray();
    return NextResponse.json({ pocs: docs.map(toPocSummary) });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: msg }, { status: 502 });
  }
}
