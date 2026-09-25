// GET /api/pocs/:id -> aggregated POC state (poc + runs + tasks + cloud_resources + deployment + test +
// clarification). This is the document the pipeline board polls every 10 s. READ-ONLY.
import { NextResponse } from "next/server";
import { getDb } from "@/lib/mongo";
import { aggregatePoc, type RawPoc, type RawResource, type RawRun, type RawTask } from "@/lib/aggregate";

export const dynamic = "force-dynamic";

export async function GET(_req: Request, ctx: { params: Promise<{ id: string }> }): Promise<Response> {
  const { id } = await ctx.params;
  if (!id) return NextResponse.json({ error: "poc id required" }, { status: 400 });
  try {
    const db = await getDb();
    const poc = await db.collection<RawPoc>("pocs").findOne({ poc_id: id }, { projection: { _id: 0 } });
    if (!poc) return NextResponse.json({ error: `no poc ${id}` }, { status: 404 });

    const runs = await db
      .collection<RawRun>("runs")
      .find({ poc_id: id }, { projection: { _id: 0 } })
      .toArray();
    const runIds = runs.map((r) => r.run_id);
    const tasks = runIds.length
      ? await db
          .collection<RawTask>("tasks")
          .find({ run_id: { $in: runIds } }, { projection: { _id: 0 } })
          .toArray()
      : [];
    const resources = await db
      .collection<RawResource>("cloud_resources")
      .find({ poc_id: id }, { projection: { _id: 0 } })
      .toArray();

    const detail = aggregatePoc(poc, runs, tasks, resources, Date.now());
    return NextResponse.json(detail);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: msg }, { status: 502 });
  }
}
