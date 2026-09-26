import { describe, expect, it } from "vitest";
import {
  PRESIGN_TTL_SEC, clampPresignTtl, classifyKey, groupArtifacts, isSafeArtifactKey, keysFromDb, listArtifacts, openArtifact,
  type ArtifactStore,
} from "@/lib/artifacts";
import type { RawRun } from "@/lib/aggregate";

const ID = "poc_01M3CHJMABWPXT6XP182RHSCEK";
const P = `pocs/${ID}/`;

// A fake S3 store: a fixed key list, records presign calls, optionally fails.
function fakeStore(keys: string[], opts: { failList?: boolean } = {}) {
  const presigned: Array<{ key: string; ttl: number }> = [];
  const store: ArtifactStore = {
    async list(prefix) {
      if (opts.failList) throw new Error("AccessDenied");
      return keys.filter((k) => k.startsWith(prefix)).map((key) => ({ key, size: 10 }));
    },
    async presign(key, ttl) {
      presigned.push({ key, ttl });
      return `https://s3.example/${key}?X-Amz-Expires=${ttl}&X-Amz-Signature=x`;
    },
  };
  return { store, presigned };
}

describe("isSafeArtifactKey", () => {
  it("accepts keys under pocs/{id}/", () => {
    expect(isSafeArtifactKey(ID, `${P}spec/v003/poc_spec.md`)).toBe(true);
  });
  it("rejects other POCs, traversal, empty segments, the bare prefix and non-strings", () => {
    expect(isSafeArtifactKey(ID, "pocs/poc_OTHER/spec/v001/poc_spec.md")).toBe(false);
    expect(isSafeArtifactKey(ID, `${P}../poc_OTHER/x`)).toBe(false);
    expect(isSafeArtifactKey(ID, `${P}spec/..`)).toBe(false);
    expect(isSafeArtifactKey(ID, `${P}spec//x`)).toBe(false);
    expect(isSafeArtifactKey(ID, `${P}a\\b`)).toBe(false);
    expect(isSafeArtifactKey(ID, P)).toBe(false);
    expect(isSafeArtifactKey(ID, `/${P}x`)).toBe(false);
    expect(isSafeArtifactKey(ID, null)).toBe(false);
    expect(isSafeArtifactKey("poc_x/../y", "pocs/poc_x/../y/z")).toBe(false);
  });
});

describe("classifyKey / groupArtifacts", () => {
  const keys = [
    `${P}input/transcript.txt`,
    `${P}spec/v001/poc_spec.md`,
    `${P}spec/v003/poc_spec.md`,
    `${P}spec/v003/schema_design.json`,
    `${P}code/v001/bundle.tar.gz`,
    `${P}code/v001/poc.manifest.json`,
    `${P}code/v001/api_contract.yaml`,
    `${P}code/v001/backend/component.manifest.json`,
    `${P}code/v001/backend/src/app.py`, // generated source — hidden
    `${P}deploy/run_D/deployment.json`,
    `${P}deploy/run_D/logs/install.stdout.txt`, // step log — hidden
    `${P}test/run_T/test_report.json`,
    `${P}test/run_T/artifacts/playwright-report.json`,
    `${P}test/run_T/artifacts/j1.png`, // screenshot — hidden
    `${P}repairs/run_X/t/failure.json`, // not a stage — hidden
  ];

  it("hides generated sources, logs and screenshots", () => {
    expect(classifyKey(ID, `${P}code/v001/backend/src/app.py`)).toBeNull();
    expect(classifyKey(ID, `${P}deploy/run_D/logs/install.stdout.txt`)).toBeNull();
    expect(classifyKey(ID, `${P}test/run_T/artifacts/j1.png`)).toBeNull();
    expect(classifyKey(ID, `${P}code/v001/poc.manifest.json`)).toEqual({
      stage: "code", item: { key: `${P}code/v001/poc.manifest.json`, name: "poc.manifest.json", group: "v001" },
    });
  });

  it("groups in stage order, newest version first", () => {
    const groups = groupArtifacts(ID, keys.map((key) => ({ key })));
    expect(groups.map((g) => g.stage)).toEqual(["spec", "code", "deploy", "test", "input"]);
    const spec = groups[0].items.map((i) => `${i.group}/${i.name}`);
    expect(spec).toEqual(["v003/poc_spec.md", "v003/schema_design.json", "v001/poc_spec.md"]);
    expect(groups[1].items.map((i) => i.name)).toEqual([
      "api_contract.yaml", "backend/component.manifest.json", "bundle.tar.gz", "poc.manifest.json",
    ]);
    expect(groups[2].items.map((i) => i.name)).toEqual(["deployment.json"]);
    expect(groups[3].items.map((i) => i.name)).toEqual(["artifacts/playwright-report.json", "test_report.json"]);
  });
});

describe("keysFromDb (no S3 credentials)", () => {
  const runs: RawRun[] = [
    { run_id: "run_c", poc_id: ID, stage: "code", status: "succeeded",
      outputs: { contract_key: `${P}code/v002/api_contract.yaml`, bundle_key: `${P}code/v002/bundle.tar.gz`,
        manifest_key: `${P}code/v002/poc.manifest.json`, component_keys: { backend: `${P}code/v002/backend/` } } },
    { run_id: "run_d", poc_id: ID, stage: "deploy", status: "succeeded",
      outputs: { deployment_key: `${P}deploy/run_d/deployment.json`, test_report_key: `${P}test/run_t/test_report.json` } },
    { run_id: "run_evil", poc_id: ID, stage: "test", status: "succeeded", outputs: { report_key: "pocs/poc_OTHER/test/x/test_report.json" } },
  ];
  const poc = { poc_id: ID, current_versions: { spec: "v003", code: "v002" } };

  it("derives the canonical spec/code keys + every run output key, dropping foreign keys", () => {
    const groups = groupArtifacts(ID, keysFromDb(poc, runs).map((key) => ({ key })));
    const all = groups.flatMap((g) => g.items.map((i) => i.key));
    expect(all).toContain(`${P}spec/v003/poc_spec.md`);
    expect(all).toContain(`${P}spec/v003/query_patterns.json`);
    expect(all).toContain(`${P}code/v002/bundle.tar.gz`);
    expect(all).toContain(`${P}code/v002/backend/component.manifest.json`);
    expect(all).toContain(`${P}deploy/run_d/deployment.json`);
    expect(all).toContain(`${P}test/run_t/test_report.json`);
    expect(all.some((k) => k.includes("poc_OTHER"))).toBe(false);
    expect(new Set(all).size).toBe(all.length); // de-duped
  });

  it("listArtifacts: no store -> s3:false with DB keys", async () => {
    const list = await listArtifacts({ poc, runs, store: null });
    expect(list.s3).toBe(false);
    expect(list.note).toBe("S3 access not configured on the server");
    expect(list.groups.length).toBeGreaterThan(0);
  });

  it("listArtifacts: store -> s3:true from the listing; a failing list falls back", async () => {
    const { store } = fakeStore([`${P}spec/v003/poc_spec.md`, "pocs/poc_OTHER/spec/v001/poc_spec.md"]);
    const ok = await listArtifacts({ poc, runs, store });
    expect(ok.s3).toBe(true);
    expect(ok.groups).toHaveLength(1);
    expect(ok.groups[0].items[0].key).toBe(`${P}spec/v003/poc_spec.md`);
    const bad = await listArtifacts({ poc, runs, store: fakeStore([], { failList: true }).store });
    expect(bad.s3).toBe(false);
  });
});

describe("clampPresignTtl", () => {
  it("caps the real presigner at 10 minutes", () => {
    expect(clampPresignTtl(600)).toBe(600);
    expect(clampPresignTtl(3600)).toBe(600);
    expect(clampPresignTtl(0)).toBe(1);
    expect(clampPresignTtl(Number.NaN)).toBe(600);
  });
});

describe("openArtifact", () => {
  it("presigns one validated key for 10 minutes", async () => {
    const { store, presigned } = fakeStore([]);
    const res = await openArtifact(store, ID, `${P}spec/v003/poc_spec.md`);
    expect(res.ok).toBe(true);
    expect(PRESIGN_TTL_SEC).toBe(600);
    if (res.ok) {
      expect(res.expires_in).toBe(600);
      expect(res.url).toContain("X-Amz-Expires=600");
    }
    expect(presigned).toEqual([{ key: `${P}spec/v003/poc_spec.md`, ttl: PRESIGN_TTL_SEC }]);
  });
  it("rejects a key outside the POC (400) without presigning, and 503s without S3", async () => {
    const { store, presigned } = fakeStore([]);
    expect(await openArtifact(store, ID, `${P}../poc_OTHER/x`)).toEqual({ ok: false, status: 400, error: "invalid artifact key" });
    expect(presigned).toHaveLength(0);
    const none = await openArtifact(null, ID, `${P}spec/v003/poc_spec.md`);
    expect(none.ok).toBe(false);
    if (!none.ok) expect(none.status).toBe(503);
  });
});
