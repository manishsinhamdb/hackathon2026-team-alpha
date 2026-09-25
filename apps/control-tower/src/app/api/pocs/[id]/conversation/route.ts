// GET /api/pocs/:id/conversation -> the stored conversation for a POC (recovered on load so the chat pane
// can show prior turns). The conversations collection is keyed by poc_id + seq. READ-ONLY.
import { NextResponse } from "next/server";
import { getDb } from "@/lib/mongo";
import type { ConversationMessage } from "@/lib/types";

export const dynamic = "force-dynamic";

interface RawConversation {
  poc_id: string;
  seq: number;
  role: string;
  content: string;
  run_id?: string;
}

export async function GET(_req: Request, ctx: { params: Promise<{ id: string }> }): Promise<Response> {
  const { id } = await ctx.params;
  if (!id) return NextResponse.json({ error: "poc id required" }, { status: 400 });
  try {
    const db = await getDb();
    const docs = await db
      .collection<RawConversation>("conversations")
      .find({ poc_id: id }, { projection: { _id: 0, seq: 1, role: 1, content: 1, run_id: 1 } })
      .sort({ seq: 1 })
      .limit(500)
      .toArray();
    const messages: ConversationMessage[] = docs.map((d) => ({
      seq: d.seq,
      role: d.role,
      content: d.content,
      run_id: d.run_id,
    }));
    return NextResponse.json({ messages });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: msg }, { status: 502 });
  }
}
