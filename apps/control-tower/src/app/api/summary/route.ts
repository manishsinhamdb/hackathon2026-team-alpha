// GET /api/summary -> the library "Today" counts, derived from the DB. READ-ONLY.
import { NextResponse } from "next/server";
import { getDb } from "@/lib/mongo";
import { summarizeToday, type RawPoc, type RawRun } from "@/lib/aggregate";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  try {
    const db = await getDb();
    const pocs = await db
      .collection<RawPoc>("pocs")
      .find({}, { projection: { _id: 0, poc_id: 1, created_at: 1, status: 1 } })
      .toArray();
    const testRuns = await db
      .collection<RawRun>("runs")
      .find({ stage: "test" }, { projection: { _id: 0, poc_id: 1, stage: 1, status: 1, started_at: 1, ended_at: 1 } })
      .toArray();
    const activeResources = await db.collection("cloud_resources").countDocuments({ status: "active" });

    const today = summarizeToday(pocs, testRuns, activeResources, Date.now());
    return NextResponse.json({ today });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: msg }, { status: 502 });
  }
}
