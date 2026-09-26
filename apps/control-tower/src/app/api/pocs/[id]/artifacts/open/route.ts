// GET /api/pocs/:id/artifacts/open?key=pocs/{id}/... -> a short-lived (10 min) presigned, read-only S3
// GetObject URL for ONE key. The key must start with pocs/{id}/ and contain no "..". Returns JSON
// {url, expires_in}; with &redirect=1 it 302s straight to the URL (so a plain <a target=_blank> works
// without popup blockers). The AWS key pair never leaves the server.
import { NextResponse } from "next/server";
import { openArtifact } from "@/lib/artifacts";
import { getArtifactStore } from "@/lib/s3";

export const dynamic = "force-dynamic";

export async function GET(req: Request, ctx: { params: Promise<{ id: string }> }): Promise<Response> {
  const { id } = await ctx.params;
  const url = new URL(req.url);
  const key = url.searchParams.get("key");
  try {
    const res = await openArtifact(getArtifactStore(), id, key);
    if (!res.ok) return NextResponse.json({ error: res.error }, { status: res.status });
    if (url.searchParams.get("redirect") === "1") {
      return new Response(null, { status: 302, headers: { Location: res.url, "Cache-Control": "no-store" } });
    }
    return NextResponse.json({ url: res.url, expires_in: res.expires_in }, { headers: { "Cache-Control": "no-store" } });
  } catch (err) {
    console.error("[artifacts/open] presign failed:", err instanceof Error ? err.message : String(err));
    return NextResponse.json({ error: "Couldn’t create a download link." }, { status: 502 });
  }
}
