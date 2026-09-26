import { describe, expect, it } from "vitest";
import { CANNED, SEEN_KEY, claimUnseen, detectTransitions, shouldAutoCheckIn, type Transition } from "@/lib/transitions";
import type { PocDetail, RunView } from "@/lib/types";

const POC = "poc_01ABC";

function run(p: Partial<RunView> & Pick<RunView, "run_id" | "stage" | "status">): RunView {
  return { duration_ms: null, steps: [], abandoned: false, handovers: 0, has_progress: false, ...p };
}

function detail(runs: RunView[], extra: Partial<PocDetail> = {}, pocId = POC): PocDetail {
  return {
    poc: { poc_id: pocId, title: "DailyDabba", status: "spec_ready", owner_user_id: "u", current_versions: { spec: "v002", code: "v001" }, tags: [], created_at: "x", updated_at: "x" },
    approvals: [], runs, tasksByRun: {}, stages: [], coders: [], runHistory: [],
    clarification: null, deployment: null, test: null, cloudResources: { activeCount: 0, items: [] },
    ...extra,
  };
}

const sends = (t: Transition) => t.actions.filter((a) => a.kind === "send").map((a) => (a.kind === "send" ? a.message : ""));

describe("detectTransitions — baseline", () => {
  it("never fires on the first poll (prev null) or across a POC switch", () => {
    const next = detail([run({ run_id: "r1", stage: "draft", status: "succeeded" })]);
    expect(detectTransitions(null, next)).toEqual([]);
    expect(detectTransitions(detail([run({ run_id: "r1", stage: "draft", status: "running" })], {}, "poc_other"), next)).toEqual([]);
  });

  it("no change -> no notice", () => {
    const d = detail([run({ run_id: "r1", stage: "draft", status: "succeeded" })]);
    expect(detectTransitions(d, d)).toEqual([]);
  });
});

describe("detectTransitions — each kind", () => {
  it("draft succeeded -> spec_ready with Show spec / Build it (uses the nickname)", () => {
    const prev = detail([run({ run_id: "r1", stage: "draft", status: "running" })]);
    const next = detail([run({ run_id: "r1", stage: "draft", status: "succeeded" })]);
    next.poc.ui_label = "Tiffin";
    const [t] = detectTransitions(prev, next);
    expect(t.kind).toBe("spec_ready");
    expect(t.id).toBe(`${POC}:r1:spec_ready`);
    expect(t.body).toContain("Tiffin");
    expect(sends(t)).toEqual([CANNED.spec, CANNED.build]);
  });

  it("draft with questions -> questions (focus the composer)", () => {
    const prev = detail([run({ run_id: "r1", stage: "draft", status: "running" })]);
    const next = detail([run({ run_id: "r1", stage: "draft", status: "succeeded", needs_clarification: true })]);
    const [t] = detectTransitions(prev, next);
    expect(t.kind).toBe("questions");
    expect(t.actions).toEqual([{ kind: "focus", label: "Answer in chat" }]);
  });

  it("a run that started and finished between polls still fires", () => {
    const [t] = detectTransitions(detail([]), detail([run({ run_id: "c1", stage: "code", status: "succeeded" })]));
    expect(t.kind).toBe("code_ready");
    expect(sends(t)).toEqual([CANNED.deploy]);
  });

  it("deploy succeeded without tests -> deployed with Open App + Tear it down", () => {
    const prev = detail([run({ run_id: "d1", stage: "deploy", status: "running" })]);
    const next = detail([run({ run_id: "d1", stage: "deploy", status: "succeeded", app_url: "http://1.2.3.4:3000" })]);
    const [t] = detectTransitions(prev, next);
    expect(t.kind).toBe("deployed");
    expect(t.actions[0]).toEqual({ kind: "link", label: "Open App", href: "http://1.2.3.4:3000" });
    expect(sends(t)).toEqual([CANNED.teardown]);
  });

  it("deploy + its in-run e2e tests fold into ONE tested notice (a test failure mid-deploy is not a separate notice)", () => {
    const prev = detail([
      run({ run_id: "d1", stage: "deploy", status: "running", started_at: "2026-09-26T10:00:00Z" }),
      run({ run_id: "t1", stage: "test", status: "running", started_at: "2026-09-26T10:05:00Z" }),
    ]);
    const mid = detail([
      run({ run_id: "d1", stage: "deploy", status: "running", started_at: "2026-09-26T10:00:00Z" }),
      run({ run_id: "t1", stage: "test", status: "failed", started_at: "2026-09-26T10:05:00Z" }),
    ]);
    expect(detectTransitions(prev, mid)).toEqual([]);
    const done = detail(
      [
        run({ run_id: "d1", stage: "deploy", status: "succeeded", started_at: "2026-09-26T10:00:00Z" }),
        run({ run_id: "t1", stage: "test", status: "failed", started_at: "2026-09-26T10:05:00Z" }),
      ],
      { deployment: { app: "http://app" }, test: { passed: 3, failed: 1, total: 4 } as PocDetail["test"] },
    );
    const out = detectTransitions(mid, done);
    expect(out.map((t) => t.kind)).toEqual(["tested"]);
    expect(out[0].tone).toBe("amber");
    expect(out[0].body).toContain("3 passed, 1 failed of 4");
  });

  it("deploy with outputs.test_passed -> tested (success)", () => {
    const prev = detail([run({ run_id: "d1", stage: "deploy", status: "running" })]);
    const next = detail([run({ run_id: "d1", stage: "deploy", status: "succeeded", test_passed: true })]);
    const [t] = detectTransitions(prev, next);
    expect(t.kind).toBe("tested");
    expect(t.tone).toBe("success");
  });

  it("a standalone test run -> tested; teardown -> torn_down with Summary", () => {
    const prev = detail([run({ run_id: "t2", stage: "test", status: "running" }), run({ run_id: "x1", stage: "teardown", status: "running" })]);
    const next = detail([run({ run_id: "t2", stage: "test", status: "succeeded" }), run({ run_id: "x1", stage: "teardown", status: "succeeded" })]);
    const out = detectTransitions(prev, next);
    expect(out.map((t) => t.kind)).toEqual(["tested", "torn_down"]);
    expect(sends(out[1])).toEqual([CANNED.summary]);
  });

  it("failed -> Retry with the exact retry message; cancelled -> nothing", () => {
    const prev = detail([run({ run_id: "c1", stage: "code", status: "running" }), run({ run_id: "c2", stage: "code", status: "running" })]);
    const next = detail([
      run({ run_id: "c1", stage: "code", status: "failed", error: { code: "CODE_RUN_FAILED", message: "backend failed" } }),
      run({ run_id: "c2", stage: "code", status: "cancelled" }),
    ]);
    const out = detectTransitions(prev, next);
    expect(out).toHaveLength(1);
    expect(out[0].kind).toBe("failed");
    expect(out[0].body).toBe("CODE_RUN_FAILED: backend failed");
    expect(sends(out[0])).toEqual([`Retry the code stage for ${POC} (continue run c1 if it can be resumed).`]);
  });

  it("abandoned fires once per execution, labelled Continue when there is progress", () => {
    const alive = detail([run({ run_id: "c1", stage: "code", status: "running", executions: 1 })]);
    const dead = detail([run({ run_id: "c1", stage: "code", status: "running", abandoned: true, has_progress: true, executions: 1 })]);
    const [t] = detectTransitions(alive, dead);
    expect(t.kind).toBe("abandoned");
    expect(t.id).toBe(`${POC}:c1:abandoned:1`);
    expect(t.actions[0].label).toBe("Continue");
    expect(detectTransitions(dead, dead)).toEqual([]);
    // Resumed (execution 2) and abandoned again -> a new notice id.
    const dead2 = detail([run({ run_id: "c1", stage: "code", status: "running", abandoned: true, executions: 2 })]);
    const [t2] = detectTransitions(dead, dead2);
    expect(t2.id).toBe(`${POC}:c1:abandoned:2`);
    expect(t2.actions[0].label).toBe("Retry");
  });
});

describe("claimUnseen / shouldAutoCheckIn", () => {
  function memStore() {
    const m = new Map<string, string>();
    return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), m };
  }
  const t = (id: string) => ({ id }) as Transition;

  it("dedupes per id across calls and persists the seen-set", () => {
    const s = memStore();
    expect(claimUnseen([t("a"), t("b"), t("a")], s).map((x) => x.id)).toEqual(["a", "b"]);
    expect(claimUnseen([t("a"), t("c")], s).map((x) => x.id)).toEqual(["c"]);
    expect(JSON.parse(s.m.get(SEEN_KEY)!)).toEqual(["a", "b", "c"]);
  });

  it("survives corrupt storage and caps the seen-set", () => {
    const s = memStore();
    s.setItem(SEEN_KEY, "{not json");
    expect(claimUnseen([t("a")], s)).toHaveLength(1);
    claimUnseen(Array.from({ length: 400 }, (_, i) => t(`x${i}`)), s);
    expect(JSON.parse(s.m.get(SEEN_KEY)!)).toHaveLength(300);
  });

  it("auto check-in only when there is something fresh and the session is free", () => {
    expect(shouldAutoCheckIn([t("a")], { inFlight: false, queued: false })).toBe(true);
    expect(shouldAutoCheckIn([t("a")], { inFlight: true, queued: false })).toBe(false);
    expect(shouldAutoCheckIn([t("a")], { inFlight: false, queued: true })).toBe(false);
    expect(shouldAutoCheckIn([], { inFlight: false, queued: false })).toBe(false);
  });
});
