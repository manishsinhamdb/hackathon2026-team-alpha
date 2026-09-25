// Pure aggregation of the platform DB documents into the read models the UI renders.
// No I/O, no server-only import — deterministic given its inputs (a `nowMs` is injected so elapsed
// times for still-running runs are testable). See src/test/aggregate.test.ts.

import type {
  ClarificationView, CloudResourceView, DeploymentView, PocDetail, PocSummary,
  RunView, StageCell, StageCellStatus, TaskView, TestReportView,
} from "./types";

// Loose shapes for the raw Mongo documents (metadata.py / poc_contracts). Only the fields the UI reads.
export interface RawPoc {
  poc_id: string;
  title: string;
  status: PocDetail["poc"]["status"];
  owner_user_id: string;
  current_versions?: { spec?: string; code?: string };
  approvals?: Array<{ stage: string; version: string; approved_by: string; at: string; implicit: boolean }>;
  tags?: string[];
  created_at: string;
  updated_at: string;
  deployment?: {
    run_id?: string;
    ec2_instance_id?: string;
    public_url?: string;
    ttl_expires_at?: string;
  };
  last_test?: { run_id?: string; passed?: number; failed?: number };
}

export interface RawRun {
  run_id: string;
  poc_id: string;
  stage: RunView["stage"];
  status: RunView["status"];
  started_at?: string;
  ended_at?: string;
  current_step?: string;
  error?: { code: string; message: string; component?: string } | null;
  steps?: Array<{ name: string; status: string }>;
  outputs?: Record<string, unknown>;
}

export interface RawTask {
  task_id: string;
  run_id: string;
  seq: number;
  agent: string;
  tool: string;
  mode?: string;
  status: string;
  duration_ms?: number | null;
}

export interface RawResource {
  type: string;
  resource_id: string;
  poc_id: string;
  status: string;
  ttl_expires_at?: string;
}

export function toPocSummary(p: RawPoc): PocSummary {
  return {
    poc_id: p.poc_id,
    title: p.title,
    status: p.status,
    versions: { spec: p.current_versions?.spec, code: p.current_versions?.code },
    updated_at: p.updated_at,
  };
}

function durationMs(started?: string, ended?: string, nowMs?: number): number | null {
  if (!started) return null;
  const start = Date.parse(started);
  if (Number.isNaN(start)) return null;
  const end = ended ? Date.parse(ended) : (nowMs ?? Date.now());
  if (Number.isNaN(end)) return null;
  return Math.max(0, end - start);
}

// Runs newest-first by started_at (fallback created_at). Stable for equal timestamps.
function sortRunsDesc(runs: RawRun[]): RawRun[] {
  return [...runs].sort((a, b) => (Date.parse(b.started_at ?? "") || 0) - (Date.parse(a.started_at ?? "") || 0));
}

function latestByStage(runsDesc: RawRun[], stage: RunView["stage"]): RawRun | undefined {
  return runsDesc.find((r) => r.stage === stage);
}

function runStatusToCell(status: RunView["status"]): StageCellStatus {
  switch (status) {
    case "succeeded": return "succeeded";
    case "failed": return "failed";
    case "cancelled": return "cancelled";
    case "waiting_user": return "waiting_user";
    default: return "running"; // queued | running
  }
}

function toRunView(r: RawRun, nowMs: number): RunView {
  return {
    run_id: r.run_id,
    stage: r.stage,
    status: r.status,
    started_at: r.started_at,
    ended_at: r.ended_at,
    duration_ms: durationMs(r.started_at, r.ended_at, nowMs),
    current_step: r.current_step,
    error: r.error ?? null,
    steps: (r.steps ?? []).map((s) => ({ name: s.name, status: s.status })),
  };
}

function runCell(key: string, label: string, run: RawRun | undefined, nowMs: number): StageCell {
  if (!run) return { key, label, kind: "run", status: "not_started" };
  return {
    key,
    label,
    kind: "run",
    status: runStatusToCell(run.status),
    run_id: run.run_id,
    started_at: run.started_at,
    ended_at: run.ended_at,
    duration_ms: durationMs(run.started_at, run.ended_at, nowMs),
    error: run.error ?? null,
  };
}

function gateCell(key: string, label: string, poc: RawPoc, gateStage: "spec_approved" | "code_approved"): StageCell {
  const approvals = poc.approvals ?? [];
  // The most recent approval for this gate, if any.
  const match = [...approvals].reverse().find((a) => a.stage === gateStage);
  if (!match) return { key, label, kind: "gate", status: "not_started" };
  return {
    key,
    label,
    kind: "gate",
    status: "succeeded",
    approvedBy: match.approved_by,
    approvedVersion: match.version,
    ended_at: match.at,
  };
}

function buildStages(poc: RawPoc, runsDesc: RawRun[], nowMs: number): StageCell[] {
  const draft = latestByStage(runsDesc, "draft");
  const code = latestByStage(runsDesc, "code");
  const deploy = latestByStage(runsDesc, "deploy");
  const test = latestByStage(runsDesc, "test");
  const teardown = latestByStage(runsDesc, "teardown");

  const teardownCell = runCell("teardown", "Torn down", teardown, nowMs);
  // A POC can be marked torn_down even if we didn't capture a teardown run row.
  if (teardownCell.status === "not_started" && poc.status === "torn_down") {
    teardownCell.status = "succeeded";
  }

  return [
    runCell("draft", "Draft", draft, nowMs),
    gateCell("spec_approved", "Spec approved", poc, "spec_approved"),
    runCell("code", "Code", code, nowMs),
    gateCell("code_approved", "Code approved", poc, "code_approved"),
    runCell("deploy", "Deploy", deploy, nowMs),
    runCell("test", "Tests", test, nowMs),
    teardownCell,
  ];
}

function buildClarification(draft: RawRun | undefined): ClarificationView | null {
  if (!draft || draft.status !== "succeeded") return null;
  const out = draft.outputs ?? {};
  if (!out.needs_clarification) return null;
  const questions = Array.isArray(out.questions) ? (out.questions as ClarificationView["questions"]) : [];
  return { round: typeof out.round === "number" ? out.round : undefined, questions };
}

function buildDeployment(poc: RawPoc, deploy: RawRun | undefined): DeploymentView | null {
  const out = (deploy?.outputs ?? {}) as Record<string, unknown>;
  const urls = (out.urls ?? {}) as { app?: string; api?: string; health?: string };
  if (urls.app || urls.api || urls.health) {
    return {
      run_id: deploy?.run_id,
      app: urls.app,
      api: urls.api,
      health: urls.health,
      instance_id: typeof out.instance_id === "string" ? out.instance_id : undefined,
      ttl_expires_at: typeof out.ttl_expires_at === "string" ? out.ttl_expires_at : undefined,
    };
  }
  // Fall back to the summary the deploy agent stamps on the POC doc (app URL only).
  const dep = poc.deployment;
  if (dep && (dep.public_url || dep.ec2_instance_id)) {
    return {
      run_id: dep.run_id,
      app: dep.public_url,
      instance_id: dep.ec2_instance_id,
      ttl_expires_at: dep.ttl_expires_at,
    };
  }
  return null;
}

function buildTest(poc: RawPoc, test: RawRun | undefined): TestReportView | null {
  const out = (test?.outputs ?? {}) as Record<string, unknown>;
  const num = (v: unknown): number | undefined => (typeof v === "number" ? v : undefined);
  if (num(out.passed) !== undefined || num(out.failed) !== undefined) {
    const passed = num(out.passed) ?? 0;
    const failed = num(out.failed) ?? 0;
    const skipped = num(out.skipped);
    const notAuto = num(out.not_automatable);
    return {
      run_id: test?.run_id,
      passed,
      failed,
      skipped,
      not_automatable: notAuto,
      total: passed + failed + (skipped ?? 0) + (notAuto ?? 0),
      suspected_component: typeof out.suspected_component === "string" ? out.suspected_component : null,
    };
  }
  const lt = poc.last_test;
  if (lt && (typeof lt.passed === "number" || typeof lt.failed === "number")) {
    const passed = lt.passed ?? 0;
    const failed = lt.failed ?? 0;
    return { run_id: lt.run_id, passed, failed, total: passed + failed };
  }
  return null;
}

export function aggregatePoc(
  poc: RawPoc,
  runs: RawRun[],
  tasks: RawTask[],
  resources: RawResource[],
  nowMs: number = Date.now(),
): PocDetail {
  const runsDesc = sortRunsDesc(runs);

  const tasksByRun: Record<string, TaskView[]> = {};
  for (const t of tasks) {
    (tasksByRun[t.run_id] ??= []).push({
      seq: t.seq,
      agent: t.agent,
      tool: t.tool,
      mode: t.mode,
      status: t.status,
      duration_ms: t.duration_ms ?? null,
    });
  }
  for (const list of Object.values(tasksByRun)) list.sort((a, b) => a.seq - b.seq);

  const active = resources.filter((r) => r.status === "active");
  const items: CloudResourceView[] = active.map((r) => ({
    type: r.type,
    resource_id: r.resource_id,
    ttl_expires_at: r.ttl_expires_at,
  }));

  const draft = latestByStage(runsDesc, "draft");
  const deploy = latestByStage(runsDesc, "deploy");
  const test = latestByStage(runsDesc, "test");

  return {
    poc: {
      poc_id: poc.poc_id,
      title: poc.title,
      status: poc.status,
      owner_user_id: poc.owner_user_id,
      current_versions: { spec: poc.current_versions?.spec, code: poc.current_versions?.code },
      tags: poc.tags ?? [],
      created_at: poc.created_at,
      updated_at: poc.updated_at,
    },
    approvals: poc.approvals ?? [],
    runs: runsDesc.map((r) => toRunView(r, nowMs)),
    tasksByRun,
    stages: buildStages(poc, runsDesc, nowMs),
    clarification: buildClarification(draft),
    deployment: buildDeployment(poc, deploy),
    test: buildTest(poc, test),
    cloudResources: { activeCount: active.length, items },
  };
}
