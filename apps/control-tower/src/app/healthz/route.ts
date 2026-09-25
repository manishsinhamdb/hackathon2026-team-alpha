// Liveness probe for Docker / Kanopy. Cheap and dependency-free: it must not touch the DB or the platform
// so an outage there doesn't take the pod out of rotation.
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export function GET(): Response {
  return NextResponse.json({ status: "ok", service: "control-tower", ts: new Date().toISOString() });
}
