// The Artefacts card (Round 5, item 2): which S3 objects under `pocs/{poc_id}/` to show, grouped by stage,
// and the key validation that gates the presigned "open" link. Pure — the S3 client is injected behind the
// tiny `ArtifactStore` interface so tests use a fake (src/test/artifacts.test.ts); the real AWS SDK client
// lives in src/lib/s3.ts (server-only). AWS credentials never reach the browser: the browser only ever gets
// a short-lived presigned GET URL for ONE validated key.
import type { RawPoc, RawRun } from "./aggregate";

export type ArtifactStage = "input" | "spec" | "code" | "deploy" | "test";

export interface ArtifactItem {
  key: string; // full S3 key (pocs/{poc_id}/...)
  name: string; // the path below the version/run segment, e.g. "poc_spec.md" or "backend/component.manifest.json"
  group?: string; // the version (vNNN) or run id segment, when the layout has one
  size?: number;
  last_modified?: string;
}

export interface ArtifactGroup {
  stage: ArtifactStage;
  label: string;
  items: ArtifactItem[];
}

export interface ArtifactList {
  poc_id: string;
  s3: boolean; // false -> keys were derived from the DB documents; "open" is disabled
  groups: ArtifactGroup[];
  note?: string; // why s3 is false (never a secret)
}

// The narrow S3 surface the BFF needs. Implemented by src/lib/s3.ts with @aws-sdk/client-s3.
export interface ArtifactStore {
  list(prefix: string): Promise<Array<{ key: string; size?: number; last_modified?: string }>>;
  presign(key: string, expiresSec: number): Promise<string>;
}

export const PRESIGN_TTL_SEC = 600; // 10 min, read-only GetObject — also the hard cap (clampPresignTtl)

// The expiry the real presigner uses: never longer than PRESIGN_TTL_SEC, never below 1 s.
export function clampPresignTtl(expiresSec: number): number {
  return Number.isFinite(expiresSec) ? Math.max(1, Math.min(PRESIGN_TTL_SEC, Math.floor(expiresSec))) : PRESIGN_TTL_SEC;
}
const MAX_PER_STAGE = 60;

const STAGE_ORDER: ArtifactStage[] = ["spec", "code", "deploy", "test", "input"];
const STAGE_LABEL: Record<ArtifactStage, string> = {
  spec: "Spec",
  code: "Code",
  deploy: "Deploy",
  test: "Tests",
  input: "Input",
};

const POC_ID_RE = /^poc_[A-Za-z0-9]+$/;

export function pocPrefix(pocId: string): string {
  return `pocs/${pocId}/`;
}

// A key the BFF may presign: must sit under pocs/{pocId}/, contain no "..", no backslashes, no empty path
// segments and no control characters. The poc id itself must look like a poc id (no path tricks).
export function isSafeArtifactKey(pocId: string, key: unknown): key is string {
  if (typeof key !== "string" || !POC_ID_RE.test(pocId)) return false;
  if (key.length > 1024) return false;
  const prefix = pocPrefix(pocId);
  if (!key.startsWith(prefix) || key.length === prefix.length) return false;
  if (key.includes("..") || key.includes("\\") || key.includes("//")) return false;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(key)) return false;
  return true;
}

// Classify one key into a stage row (or null to hide it). The layouts the agents write:
//   input/transcript.txt                         -> input
//   spec/vNNN/<file>                             -> spec  (poc_spec.md, schema_design.json, query_patterns.json…)
//   code/vNNN/<file>                             -> code  (bundle.tar.gz, poc.manifest.json, api_contract.yaml)
//   code/vNNN/<component>/component.manifest.json -> code  (per-coder manifest; generated sources are hidden)
//   deploy/<run>/<file>                          -> deploy (deployment.json; step logs are hidden)
//   test/<run>/<file>, test/<run>/artifacts/playwright-report.json -> test (screenshots are hidden)
export function classifyKey(pocId: string, key: string): { stage: ArtifactStage; item: ArtifactItem } | null {
  if (!isSafeArtifactKey(pocId, key)) return null;
  const parts = key.slice(pocPrefix(pocId).length).split("/");
  const [top, seg, ...rest] = parts;
  const tail = rest.join("/");
  switch (top) {
    case "input":
      return parts.length === 2 ? { stage: "input", item: { key, name: seg } } : null;
    case "spec":
      return rest.length === 1 ? { stage: "spec", item: { key, name: tail, group: seg } } : null;
    case "code":
      if (rest.length === 1 || (rest.length === 2 && rest[1] === "component.manifest.json"))
        return { stage: "code", item: { key, name: tail, group: seg } };
      return null;
    case "deploy":
      return rest.length === 1 ? { stage: "deploy", item: { key, name: tail, group: seg } } : null;
    case "test":
      if (rest.length === 1 || tail === "artifacts/playwright-report.json")
        return { stage: "test", item: { key, name: tail, group: seg } };
      return null;
    default:
      return null;
  }
}

// Newest version/run first (vNNN and ULID run ids both sort lexically), then by name.
function compareItems(a: ArtifactItem, b: ArtifactItem): number {
  const g = (b.group ?? "").localeCompare(a.group ?? "");
  return g !== 0 ? g : a.name.localeCompare(b.name);
}

// Group a flat key list into the ordered stage groups the card renders (empty stages omitted, de-duped).
export function groupArtifacts(
  pocId: string,
  objects: Array<{ key: string; size?: number; last_modified?: string }>,
): ArtifactGroup[] {
  const byStage = new Map<ArtifactStage, Map<string, ArtifactItem>>();
  for (const o of objects) {
    const c = classifyKey(pocId, o.key);
    if (!c) continue;
    const m = byStage.get(c.stage) ?? new Map<string, ArtifactItem>();
    m.set(o.key, { ...c.item, size: o.size, last_modified: o.last_modified });
    byStage.set(c.stage, m);
  }
  return STAGE_ORDER.filter((s) => byStage.has(s)).map((stage) => ({
    stage,
    label: STAGE_LABEL[stage],
    items: [...byStage.get(stage)!.values()].sort(compareItems).slice(0, MAX_PER_STAGE),
  }));
}

function str(v: unknown): string | undefined {
  return typeof v === "string" && v ? v : undefined;
}

// The keys we can name WITHOUT S3 access, from the DB documents: runs.outputs (spec_key/keys.*,
// contract_key, bundle_key, manifest_key, component_keys.*, deployment_key, test_report_key/report_key)
// plus pocs.current_versions -> the canonical spec/code file names, and the transcript.
export function keysFromDb(poc: Pick<RawPoc, "poc_id" | "current_versions"> & { deployment?: unknown }, runs: RawRun[]): string[] {
  const id = poc.poc_id;
  const base = pocPrefix(id);
  const keys: string[] = [`${base}input/transcript.txt`];
  const spec = poc.current_versions?.spec;
  if (spec) {
    for (const f of ["poc_spec.md", "schema_design.json", "query_patterns.json"]) keys.push(`${base}spec/${spec}/${f}`);
  }
  const code = poc.current_versions?.code;
  if (code) {
    for (const f of ["bundle.tar.gz", "poc.manifest.json", "api_contract.yaml"]) keys.push(`${base}code/${code}/${f}`);
  }
  const dep = (poc.deployment ?? {}) as Record<string, unknown>;
  if (str(dep.deployment_key)) keys.push(dep.deployment_key as string);
  for (const r of runs) {
    const o = (r.outputs ?? {}) as Record<string, unknown>;
    for (const f of ["spec_key", "contract_key", "bundle_key", "manifest_key", "deployment_key", "test_report_key", "report_key"]) {
      const k = str(o[f]);
      if (k) keys.push(k);
    }
    for (const bag of [o.keys, o.component_keys]) {
      if (bag && typeof bag === "object") for (const v of Object.values(bag as Record<string, unknown>)) {
        const k = str(v);
        if (k) keys.push(k);
      }
    }
  }
  // Component keys may be prefixes ("…/code/v001/backend/"): point at the component manifest instead.
  return keys.map((k) => (k.endsWith("/") ? `${k}component.manifest.json` : k));
}

// The list route's logic: S3 listing when a store is configured, else (or on an S3 failure) the DB-derived
// keys with s3:false so the UI disables "open".
export async function listArtifacts(args: {
  poc: Pick<RawPoc, "poc_id" | "current_versions"> & { deployment?: unknown };
  runs: RawRun[];
  store: ArtifactStore | null;
}): Promise<ArtifactList> {
  const { poc, runs, store } = args;
  const fallback = (note: string): ArtifactList => ({
    poc_id: poc.poc_id,
    s3: false,
    note,
    groups: groupArtifacts(poc.poc_id, keysFromDb(poc, runs).map((key) => ({ key }))),
  });
  if (!store) return fallback("S3 access not configured on the server");
  try {
    const objects = await store.list(pocPrefix(poc.poc_id));
    return { poc_id: poc.poc_id, s3: true, groups: groupArtifacts(poc.poc_id, objects) };
  } catch {
    return fallback("S3 listing failed on the server");
  }
}

// The open route's logic: validate, then presign ONE key (GetObject only) for 10 minutes.
export async function openArtifact(
  store: ArtifactStore | null,
  pocId: string,
  key: unknown,
): Promise<{ ok: true; url: string; expires_in: number } | { ok: false; status: number; error: string }> {
  if (!isSafeArtifactKey(pocId, key)) return { ok: false, status: 400, error: "invalid artifact key" };
  if (!store) return { ok: false, status: 503, error: "S3 access not configured on the server" };
  const url = await store.presign(key, PRESIGN_TTL_SEC);
  return { ok: true, url, expires_in: PRESIGN_TTL_SEC };
}
