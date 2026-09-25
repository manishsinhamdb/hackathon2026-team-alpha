import { describe, expect, it } from "vitest";
import {
  aggregatePoc, toPocSummary,
  type RawPoc, type RawResource, type RawRun, type RawTask,
} from "@/lib/aggregate";

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
      poc_id: "poc_1", title: "T", status: "spec_ready", versions: { spec: "v003", code: undefined }, updated_at: "b",
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
