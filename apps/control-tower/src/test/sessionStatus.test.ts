import { describe, expect, it } from "vitest";
import { busyStatusFor, isBusyStatus } from "@/lib/sessionStatus";
import type { RuntimeSession } from "@/lib/platform";

const sessions: RuntimeSession[] = [
  { session_id: "ct-a", status: "active", last_activity_at: "2026-09-26T08:31:00Z" },
  { session_id: "ct-b", status: "stopping" },
];

describe("isBusyStatus", () => {
  it("treats active and stopping as busy, everything else as free", () => {
    expect(isBusyStatus("active")).toBe(true);
    expect(isBusyStatus("stopping")).toBe(true);
    expect(isBusyStatus("free")).toBe(false);
    expect(isBusyStatus(undefined)).toBe(false);
  });
});

describe("busyStatusFor", () => {
  it("maps requested ids onto their runtime-session status; missing ids are free", () => {
    const r = busyStatusFor(sessions, ["ct-a", "ct-b", "ct-c"]);
    expect(r.sessions["ct-a"]).toEqual({ busy: true, status: "active", lastActivityAt: "2026-09-26T08:31:00Z" });
    expect(r.sessions["ct-b"].busy).toBe(true);
    expect(r.sessions["ct-c"]).toEqual({ busy: false, status: "free", lastActivityAt: undefined });
    expect(r.busyIds.sort()).toEqual(["ct-a", "ct-b"]);
  });

  it("returns empty maps when no ids are requested", () => {
    expect(busyStatusFor(sessions, [])).toEqual({ sessions: {}, busyIds: [] });
  });
});
