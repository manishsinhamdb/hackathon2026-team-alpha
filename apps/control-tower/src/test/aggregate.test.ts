import { describe, expect, it } from "vitest";
import {
  aggregatePoc, filterCounts, matchesFilter, selectPocs, summarizeToday, toPocSummary,
  type RawPoc, type RawResource, type RawRun, type RawTask,
} from "@/lib/aggregate";
import type { PocSummary } from "@/lib/types";

const NOW = Date.parse("2026-09-25T17:00:00Z");

function stage(detail: ReturnType<typeof aggregatePoc>, key: string) {
  const s = detail.stages.find((x) => x.key === key);
  if (!s) throw new Error(`no stage ${key}`);
  return s;
}

describe("toPocSummary", () => {
  it("projects the list fields", () => {
    const p: RawPoc = {
      poc_id: "poc_1", title: "T", status: "spec_ready", owner_user_id: "u",
      current_versions: { spec: "v003" }, created_at: "a", updated_at: "b",
    };
    expect(toPocSummary(p)).toEqual({
      poc_id: "poc_1", title: "T", status: "spec_ready", versions: { spec: "v003", code: undefined },
      created_at: "a", updated_at: "b", ui_archived: false,
    });
  });
});

describe("aggregatePoc — spec_ready POC (the DailyDabba target shape)", () => {
  const poc: RawPoc = {
    poc_id: "poc_01M3CHJMABWPXT6XP182RHSCEK",
    title: "DailyDabba Order Analytics",
    status: "spec_ready",
    owner_user_id: "u",
    current_versions: { spec: "v003" },
    approvals: [],
    created_at: "2026-09-25T15:05:10Z",
    updated_at: "2026-09-25T16:15:33Z",
  };
  const runs: RawRun[] = [
    // earlier draft that asked a clarification question
    {
      run_id: "run_early", poc_id: poc.poc_id, stage: "draft", status: "succeeded",
      started_at: "2026-09-25T16:05:08Z", ended_at: "2026-09-25T16:06:07Z",
      outputs: { needs_clarification: true, round: 2, questions: ["What's the deadline?"] },
    },
    // the later, finalized draft (spec v003)
    {
      run_id: "run_01M3CN9ANTM0D1CCBYZR1E0BDX", poc_id: poc.poc_id, stage: "draft", status: "succeeded",
      started_at: "2026-09-25T16:09:59Z", ended_at: "2026-09-25T16:15:46Z",
      outputs: { spec_version: "v003", user_story_count: 4 },
    },
  ];

  const detail = aggregatePoc(poc, runs, [], [], NOW);

  it("shows the latest draft run as succeeded", () => {
    const draft = stage(detail, "draft");
    expect(draft.status).toBe("succeeded");
    expect(draft.run_id).toBe("run_01M3CN9ANTM0D1CCBYZR1E0BDX");
  });

  it("shows spec_approved and code as not started", () => {
    expect(stage(detail, "spec_approved").status).toBe("not_started");
    expect(stage(detail, "code").status).toBe("not_started");
    expect(stage(detail, "deploy").status).toBe("not_started");
    expect(stage(detail, "teardown").status).toBe("not_started");
  });

  it("has no pending clarification (latest draft finalized a spec)", () => {
    expect(detail.clarification).toBeNull();
  });

  it("reports zero active cloud resources", () => {
    expect(detail.cloudResources.activeCount).toBe(0);
  });

  it("orders runs newest-first with computed durations", () => {
    expect(detail.runs[0].run_id).toBe("run_01M3CN9ANTM0D1CCBYZR1E0BDX");
    expect(detail.runs[0].duration_ms).toBe(347_000); // 16:09:59 -> 16:15:46
  });
});

describe("aggregatePoc — pending clarification", () => {
  const poc: RawPoc = {
    poc_id: "poc_c", title: "Vague", status: "drafting", owner_user_id: "u",
    approvals: [], created_at: "a", updated_at: "b",
  };
  const runs: RawRun[] = [
    {
      run_id: "run_q", poc_id: "poc_c", stage: "draft", status: "succeeded",
      started_at: "2026-09-25T10:00:00Z", ended_at: "2026-09-25T10:01:00Z",
      outputs: { needs_clarification: true, round: 1, questions: [{ id: "q1-1", question: "Which entities?" }] },
    },
  ];
  it("surfaces the questions and round", () => {
    const detail = aggregatePoc(poc, runs, [], [], NOW);
    expect(detail.clarification).not.toBeNull();
    expect(detail.clarification?.round).toBe(1);
    expect(detail.clarification?.questions).toHaveLength(1);
    expect(stage(detail, "draft").status).toBe("succeeded");
  });
});

describe("aggregatePoc — fully deployed then torn down", () => {
  const poc: RawPoc = {
    poc_id: "poc_full", title: "Kirana", status: "torn_down", owner_user_id: "u",
    current_versions: { spec: "v001", code: "v001" },
    approvals: [
      { stage: "spec_approved", version: "v001", approved_by: "u_cc", at: "2026-09-25T13:16:27Z", implicit: false },
      { stage: "code_approved", version: "v001", approved_by: "u_cc", at: "2026-09-25T13:28:00Z", implicit: true },
    ],
    created_at: "a", updated_at: "b",
    last_test: { run_id: "run_test", passed: 11, failed: 1 },
  };
  const runs: RawRun[] = [
    { run_id: "run_code", poc_id: "poc_full", stage: "code", status: "succeeded", started_at: "2026-09-25T13:17:14Z", ended_at: "2026-09-25T13:25:05Z", outputs: { code_version: "v001" } },
    {
      run_id: "run_deploy", poc_id: "poc_full", stage: "deploy", status: "succeeded",
      started_at: "2026-09-25T13:40:00Z", ended_at: "2026-09-25T13:55:00Z",
      outputs: {
        instance_id: "i-05aa06fcc80206350",
        ttl_expires_at: "2026-09-25T18:16:25Z",
        urls: {
          app: "http://ec2-13-206-97-22.ap-south-1.compute.amazonaws.com/",
          api: "http://ec2-13-206-97-22.ap-south-1.compute.amazonaws.com/api",
          health: "http://ec2-13-206-97-22.ap-south-1.compute.amazonaws.com/api/health",
        },
      },
    },
    { run_id: "run_test", poc_id: "poc_full", stage: "test", status: "succeeded", started_at: "2026-09-25T13:56:00Z", ended_at: "2026-09-25T13:58:00Z", outputs: { passed: 11, failed: 1, skipped: 0, not_automatable: 2, suspected_component: "frontend" } },
    { run_id: "run_teardown", poc_id: "poc_full", stage: "teardown", status: "succeeded", started_at: "2026-09-25T14:30:00Z", ended_at: "2026-09-25T14:32:00Z" },
  ];
  const tasks: RawTask[] = [
    { task_id: "t2", run_id: "run_code", seq: 2, agent: "data_seeding_agent", tool: "generate_seed", mode: "code", status: "succeeded" },
    { task_id: "t1", run_id: "run_code", seq: 1, agent: "api_agent", tool: "generate_api", mode: "contract", status: "succeeded" },
  ];
  const resources: RawResource[] = [
    { type: "ec2_instance", resource_id: "i-05aa06fcc80206350", poc_id: "poc_full", status: "released", ttl_expires_at: "2026-09-25T18:16:25Z" },
    { type: "secret", resource_id: "arn:...", poc_id: "poc_full", status: "released" },
  ];

  const detail = aggregatePoc(poc, runs, tasks, resources, NOW);

  it("marks every gate and run stage complete", () => {
    expect(stage(detail, "spec_approved").status).toBe("succeeded");
    expect(stage(detail, "spec_approved").approvedBy).toBe("u_cc");
    expect(stage(detail, "code").status).toBe("succeeded");
    expect(stage(detail, "code_approved").status).toBe("succeeded");
    expect(stage(detail, "deploy").status).toBe("succeeded");
    expect(stage(detail, "test").status).toBe("succeeded");
    expect(stage(detail, "teardown").status).toBe("succeeded");
  });

  it("extracts the deployment URLs and TTL from the deploy run outputs", () => {
    expect(detail.deployment?.app).toContain("compute.amazonaws.com");
    expect(detail.deployment?.api).toContain("/api");
    expect(detail.deployment?.health).toContain("/api/health");
    expect(detail.deployment?.instance_id).toBe("i-05aa06fcc80206350");
    expect(detail.deployment?.ttl_expires_at).toBe("2026-09-25T18:16:25Z");
  });

  it("summarises the test report", () => {
    expect(detail.test).toMatchObject({ passed: 11, failed: 1, not_automatable: 2, suspected_component: "frontend", total: 14 });
  });

  it("sorts tasks by seq under their run", () => {
    const codeTasks = detail.tasksByRun["run_code"];
    expect(codeTasks.map((t) => t.seq)).toEqual([1, 2]);
  });

  it("counts zero active resources once everything is released", () => {
    expect(detail.cloudResources.activeCount).toBe(0);
  });
});

describe("aggregatePoc — a running run shows live elapsed", () => {
  it("computes elapsed from started_at to nowMs when not ended", () => {
    const poc: RawPoc = { poc_id: "poc_r", title: "R", status: "coding", owner_user_id: "u", approvals: [], created_at: "a", updated_at: "b" };
    const runs: RawRun[] = [
      { run_id: "run_running", poc_id: "poc_r", stage: "code", status: "running", started_at: "2026-09-25T16:59:00Z" },
    ];
    const detail = aggregatePoc(poc, runs, [], [], NOW);
    const code = stage(detail, "code");
    expect(code.status).toBe("running");
    expect(code.duration_ms).toBe(60_000); // 16:59:00 -> 17:00:00
  });

  it("counts active resources with a danger badge scenario", () => {
    const poc: RawPoc = { poc_id: "poc_a", title: "A", status: "deployed", owner_user_id: "u", approvals: [], created_at: "a", updated_at: "b" };
    const resources: RawResource[] = [
      { type: "ec2_instance", resource_id: "i-1", poc_id: "poc_a", status: "active", ttl_expires_at: "2026-09-25T20:00:00Z" },
    ];
    const detail = aggregatePoc(poc, [], [], resources, NOW);
    expect(detail.cloudResources.activeCount).toBe(1);
    expect(detail.cloudResources.items[0].resource_id).toBe("i-1");
  });
});

describe("aggregatePoc — Code run coder rows", () => {
  const poc: RawPoc = { poc_id: "poc_c", title: "C", status: "coding", owner_user_id: "u", approvals: [], created_at: "a", updated_at: "b" };
  const runs: RawRun[] = [
    { run_id: "run_code", poc_id: "poc_c", stage: "code", status: "running", started_at: "2026-09-25T16:57:00Z" },
  ];
  const tasks: RawTask[] = [
    { task_id: "t1", run_id: "run_code", seq: 1, agent: "api_agent", tool: "generate_api", mode: "contract", status: "succeeded", duration_ms: 38_000 },
    { task_id: "t2", run_id: "run_code", seq: 2, agent: "data_seeding_agent", tool: "generate_seed", mode: "seed", status: "succeeded", duration_ms: 82_000 },
    { task_id: "t3", run_id: "run_code", seq: 3, agent: "api_agent", tool: "generate_api", mode: "backend", status: "running" },
  ];

  it("maps tasks onto contract/seed/backend/frontend/assemble in order", () => {
    const detail = aggregatePoc(poc, runs, tasks, [], NOW);
    expect(detail.coders.map((c) => c.key)).toEqual(["contract", "seed", "backend", "frontend", "assemble"]);
    const byKey = Object.fromEntries(detail.coders.map((c) => [c.key, c.status]));
    expect(byKey.contract).toBe("done");
    expect(byKey.seed).toBe("done");
    expect(byKey.backend).toBe("running");
    expect(byKey.frontend).toBe("queued");
    expect(byKey.assemble).toBe("queued");
  });

  it("has no coder rows when there is no code run", () => {
    const detail = aggregatePoc(poc, [], [], [], NOW);
    expect(detail.coders).toEqual([]);
  });
});

describe("aggregatePoc — run history display status", () => {
  const poc: RawPoc = { poc_id: "poc_h", title: "H", status: "drafting", owner_user_id: "u", approvals: [], created_at: "a", updated_at: "b" };
  const runs: RawRun[] = [
    { run_id: "run_ok", poc_id: "poc_h", stage: "draft", status: "succeeded", started_at: "2026-09-25T16:00:00Z", ended_at: "2026-09-25T16:05:00Z", outputs: { spec_version: "v003" } },
    { run_id: "run_q", poc_id: "poc_h", stage: "draft", status: "succeeded", started_at: "2026-09-25T15:50:00Z", ended_at: "2026-09-25T15:54:00Z", outputs: { needs_clarification: true } },
    { run_id: "run_run", poc_id: "poc_h", stage: "code", status: "running", started_at: "2026-09-25T16:10:00Z" },
  ];
  it("folds a draft-with-questions into a QUESTIONS row and keeps others", () => {
    const detail = aggregatePoc(poc, runs, [], [], NOW);
    const byId = Object.fromEntries(detail.runHistory.map((r) => [r.run_id, r.display]));
    expect(byId.run_ok).toBe("succeeded");
    expect(byId.run_q).toBe("questions");
    expect(byId.run_run).toBe("running");
    // newest first
    expect(detail.runHistory[0].run_id).toBe("run_run");
  });
});

describe("summarizeToday", () => {
  const NOW_TODAY = Date.parse("2026-09-25T22:00:00Z");
  const pocs: RawPoc[] = [
    { poc_id: "p1", title: "a", status: "tested", owner_user_id: "u", created_at: "2026-09-25T18:00:00Z", updated_at: "b" },
    { poc_id: "p2", title: "b", status: "drafting", owner_user_id: "u", created_at: "2026-09-25T20:00:00Z", updated_at: "b" },
    { poc_id: "p3", title: "c", status: "torn_down", owner_user_id: "u", created_at: "2026-09-24T10:00:00Z", updated_at: "b" }, // yesterday
  ];
  const runs: RawRun[] = [
    { run_id: "r1", poc_id: "p1", stage: "test", status: "succeeded", started_at: "2026-09-25T18:40:00Z", ended_at: "2026-09-25T18:44:00Z" },
  ];
  it("counts drafted-today, deployed&tested-today, live resources, and the transcript->tested duration", () => {
    const t = summarizeToday(pocs, runs, 0, NOW_TODAY);
    expect(t.drafted).toBe(2); // p1 + p2 created today; p3 yesterday
    expect(t.deployedTested).toBe(1); // p1 tested today
    expect(t.cloudLive).toBe(0);
    expect(t.transcriptToTestedMs).toBe(Date.parse("2026-09-25T18:44:00Z") - Date.parse("2026-09-25T18:00:00Z")); // 44 min
  });
});

describe("library filters + sort", () => {
  const mk = (over: Partial<PocSummary>): PocSummary => ({
    poc_id: "p", title: "T", status: "spec_ready", versions: {}, created_at: "2026-09-25T10:00:00Z", updated_at: "b", ...over,
  });
  const pocs: PocSummary[] = [
    mk({ poc_id: "p_new", title: "DailyDabba", status: "coding", created_at: "2026-09-25T20:00:00Z" }),
    mk({ poc_id: "p_old", title: "Kirana", status: "tested", created_at: "2026-09-25T09:00:00Z" }),
    mk({ poc_id: "p_torn", title: "Old torn", status: "torn_down", created_at: "2026-09-25T08:00:00Z" }),
    mk({ poc_id: "p_arch", title: "Hidden", status: "spec_ready", created_at: "2026-09-25T21:00:00Z", ui_archived: true }),
  ];

  it("counts each filter", () => {
    const c = filterCounts(pocs);
    expect(c.active).toBe(2); // coding + tested (not archived, not terminal)
    expect(c.deployed).toBe(1); // tested
    expect(c.torn_down).toBe(1);
    expect(c.archived).toBe(1);
  });

  it("archived POCs never appear in a non-archived filter", () => {
    expect(matchesFilter(pocs[3], "active")).toBe(false);
    expect(matchesFilter(pocs[3], "archived")).toBe(true);
  });

  it("selectPocs filters + searches + sorts newest first", () => {
    const active = selectPocs(pocs, "active", "");
    expect(active.map((p) => p.poc_id)).toEqual(["p_new", "p_old"]); // created desc
    const searched = selectPocs(pocs, "active", "kirana");
    expect(searched.map((p) => p.poc_id)).toEqual(["p_old"]);
    const archived = selectPocs(pocs, "archived", "");
    expect(archived.map((p) => p.poc_id)).toEqual(["p_arch"]);
  });
});
