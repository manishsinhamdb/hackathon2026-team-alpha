// Pure aggregation of the platform DB documents into the read models the UI renders.
// No I/O, no server-only import — deterministic given its inputs (a `nowMs` is injected so elapsed
// times for still-running runs are testable). See src/test/aggregate.test.ts.

import type {
  ClarificationView, CloudResourceView, CoderRowView, DeploymentView, PocDetail, PocSummary,
  RunDisplayStatus, RunHistoryRow, RunView, StageCell, StageCellStatus, TaskView, TestReportView,
  TodaySummary,
} from "./types";
import { isAbandoned, lastBeatAt, retryLabel } from "./runHealth";

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
  ui_archived?: boolean; // set only by this UI's archive route; agents ignore it
  ui_label?: string; // set only by this UI's label route (the POC nickname); agents ignore it
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
  execution_id?: string; // usually absent in the current schema; passed through if present
  updated_at?: string;
  heartbeat_at?: string; // touched every <=30 s by a live durable stage (Round 5); absent on older runs
  executions?: number; // how many executions have driven this run (resume / hand-over)
  continuations?: Array<{ at?: string; reason?: string; execution?: number | string }>;
}

export interface RawTask {
  task_id: string;
  run_id: string;
  seq: number;
  agent: string;
  tool: string;
  mode?: string;
  component?: string; // "contract" | "seed" | "backend" | "frontend" | "assemble" — stable key (Round 5)
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
    created_at: p.created_at,
    updated_at: p.updated_at,
    ui_archived: p.ui_archived === true,
    ...(p.ui_label ? { ui_label: p.ui_label } : {}),
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

function toRunView(r: RawRun, tasks: RawTask[], nowMs: number): RunView {
  const out = (r.outputs ?? {}) as Record<string, unknown>;
  const urls = (out.urls ?? {}) as { app?: unknown };
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
    execution_id: r.execution_id,
    abandoned: isAbandoned(r, nowMs),
    last_beat_at: lastBeatAt(r),
    executions: typeof r.executions === "number" ? r.executions : undefined,
    handovers: Array.isArray(r.continuations) ? r.continuations.length : 0,
    has_progress: retryLabel(r.steps ?? [], tasks.filter((t) => t.run_id === r.run_id)) === "Continue",
    ...(out.needs_clarification ? { needs_clarification: true } : {}),
    ...(typeof out.test_passed === "boolean" ? { test_passed: out.test_passed } : {}),
    ...(typeof urls.app === "string" ? { app_url: urls.app } : {}),
  };
}

function runVersion(run: RawRun): string | undefined {
  const out = run.outputs ?? {};
  const v = out.spec_version ?? out.code_version ?? out.version;
  return typeof v === "string" ? v : undefined;
}

function runCell(key: string, label: string, run: RawRun | undefined, nowMs: number, tasks: RawTask[] = []): StageCell {
  if (!run) return { key, label, kind: "run", status: "not_started" };
  const abandoned = isAbandoned(run, nowMs);
  return {
    key,
    label,
    kind: "run",
    status: abandoned ? "abandoned" : runStatusToCell(run.status),
    run_id: run.run_id,
    started_at: run.started_at,
    ended_at: run.ended_at,
    duration_ms: durationMs(run.started_at, run.ended_at, nowMs),
    error: run.error ?? null,
    version: runVersion(run),
    ...(typeof run.executions === "number" && run.executions > 1 ? { executions: run.executions } : {}),
    ...(abandoned
      ? {
          last_beat_at: lastBeatAt(run),
          retry_label: retryLabel(run.steps ?? [], tasks.filter((t) => t.run_id === run.run_id)),
        }
      : {}),
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

function buildStages(poc: RawPoc, runsDesc: RawRun[], tasks: RawTask[], nowMs: number): StageCell[] {
  const draft = latestByStage(runsDesc, "draft");
  const code = latestByStage(runsDesc, "code");
  const deploy = latestByStage(runsDesc, "deploy");
  const test = latestByStage(runsDesc, "test");
  const teardown = latestByStage(runsDesc, "teardown");

  const teardownCell = runCell("teardown", "Torn down", teardown, nowMs, tasks);
  // A POC can be marked torn_down even if we didn't capture a teardown run row.
  if (teardownCell.status === "not_started" && poc.status === "torn_down") {
    teardownCell.status = "succeeded";
  }

  return [
    runCell("draft", "Draft", draft, nowMs, tasks),
    gateCell("spec_approved", "Spec approved", poc, "spec_approved"),
    runCell("code", "Code", code, nowMs, tasks),
    gateCell("code_approved", "Code approved", poc, "code_approved"),
    runCell("deploy", "Deploy", deploy, nowMs, tasks),
    runCell("test", "Tests", test, nowMs, tasks),
    teardownCell,
  ];
}

// The five coder rows of the Code run card, in order.
const CODER_ROWS: Array<{ key: string; label: string }> = [
  { key: "contract", label: "API contract" },
  { key: "seed", label: "Seed data" },
  { key: "backend", label: "Backend" },
  { key: "frontend", label: "Frontend" },
  { key: "assemble", label: "Assemble bundle" },
];

// Which coder row a task belongs to. The orchestrator now stamps a stable `tasks.component` on every task
// (Round 5) — use it first. Legacy tasks (no component) fall back to the real shapes the orchestrator wrote:
// the contract is {agent api_agent, tool generate_api, mode contract}; the backend is the SAME agent/tool
// with mode code (or repair) — which is why the old "backend" substring match never hit.
export function coderKeyForTask(t: Pick<RawTask, "agent" | "tool" | "mode" | "component">): string | undefined {
  if (t.component) return CODER_ROWS.some((r) => r.key === t.component) ? t.component : undefined;
  const agent = (t.agent ?? "").replace(/-/g, "_");
  if (t.mode === "contract") return "contract";
  if (agent === "api_agent" && (t.mode === "code" || t.mode === "repair")) return "backend";
  if (agent === "data_seeding_agent") return "seed";
  if (agent === "frontend_agent") return "frontend";
  if (/^assemble/i.test(t.tool ?? "")) return "assemble";
  return undefined;
}

function coderStatus(taskStatus: string | undefined): CoderRowView["status"] {
  if (taskStatus === "succeeded") return "done";
  if (taskStatus === "running" || taskStatus === "in_progress") return "running";
  return "queued";
}

// Build the coder progress rows for the latest code run. Empty when there is no code run (card hidden).
function buildCoders(code: RawRun | undefined, tasks: RawTask[], nowMs: number): CoderRowView[] {
  if (!code) return [];
  const codeTasks = tasks.filter((t) => t.run_id === code.run_id);
  const running = code.status === "running" || code.status === "queued";
  return CODER_ROWS.map((row) => {
    // Several tasks can match one row when a coder re-fired on resume — the newest (highest seq) wins.
    const task = codeTasks
      .filter((t) => coderKeyForTask(t) === row.key)
      .reduce<RawTask | undefined>((best, t) => (!best || t.seq > best.seq ? t : best), undefined);
    if (!task) {
      // No task row yet: infer from the run — a finished run implies everything ran, else queued.
      const status: CoderRowView["status"] = running ? "queued" : code.status === "succeeded" ? "done" : "queued";
      return { key: row.key, label: row.label, status };
    }
    const status = coderStatus(task.status);
    return {
      key: row.key,
      label: row.label,
      status,
      duration_ms: task.duration_ms ?? (status === "running" ? durationMs(code.started_at, undefined, nowMs) : null),
    };
  });
}

function displayStatus(r: RawRun, nowMs: number): RunDisplayStatus {
  if (isAbandoned(r, nowMs)) return "abandoned";
  if (r.stage === "draft" && r.status === "succeeded" && r.outputs?.needs_clarification) return "questions";
  switch (r.status) {
    case "succeeded": return "succeeded";
    case "failed": return "failed";
    case "cancelled": return "cancelled";
    case "waiting_user": return "waiting_user";
    case "queued": return "queued";
    default: return "running";
  }
}

const STAGE_LABELS: Record<RunView["stage"], string> = {
  draft: "Draft", code: "Code", deploy: "Deploy", test: "Tests", teardown: "Teardown",
};

function buildRunHistory(runsDesc: RawRun[], nowMs: number): RunHistoryRow[] {
  return runsDesc.map((r) => ({
    stage: r.stage,
    stageLabel: STAGE_LABELS[r.stage] ?? r.stage,
    run_id: r.run_id,
    display: displayStatus(r, nowMs),
    started_at: r.started_at,
    duration_ms: durationMs(r.started_at, r.ended_at, nowMs),
    ...(typeof r.executions === "number" && r.executions > 1 ? { executions: r.executions } : {}),
  }));
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
  const code = latestByStage(runsDesc, "code");
  const deploy = latestByStage(runsDesc, "deploy");
  const test = latestByStage(runsDesc, "test");

  return {
    poc: {
      poc_id: poc.poc_id,
      title: poc.title,
      ...(poc.ui_label ? { ui_label: poc.ui_label } : {}),
      status: poc.status,
      owner_user_id: poc.owner_user_id,
      current_versions: { spec: poc.current_versions?.spec, code: poc.current_versions?.code },
      tags: poc.tags ?? [],
      created_at: poc.created_at,
      updated_at: poc.updated_at,
    },
    approvals: poc.approvals ?? [],
    runs: runsDesc.map((r) => toRunView(r, tasks, nowMs)),
    tasksByRun,
    stages: buildStages(poc, runsDesc, tasks, nowMs),
    coders: buildCoders(code, tasks, nowMs),
    runHistory: buildRunHistory(runsDesc, nowMs),
    clarification: buildClarification(draft),
    deployment: buildDeployment(poc, deploy),
    test: buildTest(poc, test),
    cloudResources: { activeCount: active.length, items },
  };
}

/* ---- Library helpers (pure) ----------------------------------------------------------------------- */

export type LibraryFilter = "active" | "deployed" | "torn_down" | "archived";

// A POC counts as "active" when it is neither torn down/failed terminal nor archived — i.e. still in play.
function isTerminal(status: string): boolean {
  return status === "torn_down" || status === "failed" || status === "code_failed";
}

export function matchesFilter(p: PocSummary, filter: LibraryFilter): boolean {
  const archived = p.ui_archived === true;
  switch (filter) {
    case "archived": return archived;
    case "torn_down": return !archived && p.status === "torn_down";
    case "deployed": return !archived && (p.status === "deployed" || p.status === "tested" || p.status === "testing");
    case "active": return !archived && !isTerminal(p.status);
  }
}

export function filterCounts(pocs: PocSummary[]): Record<LibraryFilter, number> {
  return {
    active: pocs.filter((p) => matchesFilter(p, "active")).length,
    deployed: pocs.filter((p) => matchesFilter(p, "deployed")).length,
    torn_down: pocs.filter((p) => matchesFilter(p, "torn_down")).length,
    archived: pocs.filter((p) => matchesFilter(p, "archived")).length,
  };
}

// Filter by the active chip + a free-text query (title, nickname or poc id), then sort by created desc.
export function selectPocs(
  pocs: PocSummary[],
  filter: LibraryFilter,
  query: string,
): PocSummary[] {
  const q = query.trim().toLowerCase();
  return pocs
    .filter((p) => matchesFilter(p, filter))
    .filter(
      (p) =>
        !q ||
        p.title.toLowerCase().includes(q) ||
        p.poc_id.toLowerCase().includes(q) ||
        (p.ui_label ?? "").toLowerCase().includes(q),
    )
    .sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0));
}

function sameUtcDay(a: number, b: number): boolean {
  const da = new Date(a), db = new Date(b);
  return da.getUTCFullYear() === db.getUTCFullYear() && da.getUTCMonth() === db.getUTCMonth() && da.getUTCDate() === db.getUTCDate();
}

// The library "Today" card. `runs` are all runs across POCs; `activeResources` is the active cloud count.
export function summarizeToday(
  pocs: RawPoc[],
  runs: RawRun[],
  activeResources: number,
  nowMs: number = Date.now(),
): TodaySummary {
  const drafted = pocs.filter((p) => {
    const c = Date.parse(p.created_at);
    return !Number.isNaN(c) && sameUtcDay(c, nowMs);
  }).length;

  const createdByPoc = new Map(pocs.map((p) => [p.poc_id, Date.parse(p.created_at)]));
  const testedToday = runs.filter((r) => {
    if (r.stage !== "test" || r.status !== "succeeded" || !r.ended_at) return false;
    const e = Date.parse(r.ended_at);
    return !Number.isNaN(e) && sameUtcDay(e, nowMs);
  });
  const testedPocs = new Set(testedToday.map((r) => r.poc_id));

  // Most recent transcript(created)->tested duration among today's tested runs.
  let latest: { ended: number; dur: number } | null = null;
  for (const r of testedToday) {
    const created = createdByPoc.get(r.poc_id);
    const ended = Date.parse(r.ended_at!);
    if (created === undefined || Number.isNaN(created)) continue;
    if (!latest || ended > latest.ended) latest = { ended, dur: ended - created };
  }

  return {
    drafted,
    deployedTested: testedPocs.size,
    cloudLive: activeResources,
    transcriptToTestedMs: latest ? latest.dur : null,
  };
}
