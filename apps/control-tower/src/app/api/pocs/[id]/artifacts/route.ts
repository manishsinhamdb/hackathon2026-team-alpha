// GET /api/pocs/:id/artifacts -> the POC's S3 artefacts grouped by stage (spec / code / deploy / test /
// input). With S3 credentials on the server it lists pocs/{id}/; without them it derives the keys from the
// DB documents (runs.outputs + pocs.current_versions) and returns s3:false so the UI disables "open".
// READ-ONLY (DB and S3).
import { NextResponse } from "next/server";
import { getDb } from "@/lib/mongo";
import type { RawPoc, RawRun } from "@/lib/aggregate";
import { listArtifacts } from "@/lib/artifacts";
import { getArtifactStore } from "@/lib/s3";

export const dynamic = "force-dynamic";

export async function GET(_req: Request, ctx: { params: Promise<{ id: string }> }): Promise<Response> {
  const { id } = await ctx.params;
  if (!id) return NextResponse.json({ error: "poc id required" }, { status: 400 });
  try {
    const db = await getDb();
    const poc = await db
      .collection<RawPoc>("pocs")
      .findOne({ poc_id: id }, { projection: { _id: 0, poc_id: 1, current_versions: 1, deployment: 1 } });
    if (!poc) return NextResponse.json({ error: `no poc ${id}` }, { status: 404 });
    const runs = await db
      .collection<RawRun>("runs")
      .find({ poc_id: id }, { projection: { _id: 0, run_id: 1, poc_id: 1, stage: 1, status: 1, outputs: 1 } })
      .toArray();
    const store = getArtifactStore();
    const list = await listArtifacts({ poc, runs, store });
    if (store && !list.s3) console.error("[artifacts] S3 list failed; served DB-derived keys");
    return NextResponse.json(list);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: msg }, { status: 502 });
  }
}
