"""Pure generation functions for the Data Seeding Agent (Spec §6.4).

These take plain dicts and return plain dicts so they can be exercised without the SDK,
without cloud, and without the platform DB (see scripts/gen_golden_check.py). The only
side effect is the LLM call, which is injected (``llm=``) in tests.

Output shape for every generation: ``{"files": {"<relative path>": "<content>", ...}}`` whose
``component.manifest.json`` validates as a ``component_manifest``.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from poc_contracts import ContractError, schema_for, validate

COMPONENT = "seed"

# Files every seed component must contain.
REQUIRED_FILES = ("seed.js", "package.json", "component.manifest.json", "SEED_README.md")


class LLMOutputInvalid(Exception):
    """Raised when the model's output cannot be parsed/validated after one retry."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors)[:2000])


# --- the golden seed component, verbatim, used as the one-shot example -------
GOLDEN_SEED_JS = r'''// Golden seed script — deterministic (seed 42), idempotent (drop & recreate), respects SEED_MAX_DOCS.
// Prints a single JSON summary line on completion; exits non-zero on failure. Reads MONGODB_URI from env.
import { MongoClient } from "mongodb";

const uri = process.env.MONGODB_URI;
if (!uri) { console.error("MONGODB_URI is required"); process.exit(2); }
const MAX = Math.max(1, Math.min(parseInt(process.env.SEED_MAX_DOCS || "10000", 10), 100000));
const N_PRODUCTS = Math.min(5000, MAX);
const N_ORDERS = Math.min(8000, MAX);

// mulberry32 PRNG — same numbers every run
let s = 42 >>> 0;
const rand = () => { s = (s + 0x6D2B79F5) >>> 0; let t = s; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
const pick = (a) => a[Math.floor(rand() * a.length)];
const int = (lo, hi) => lo + Math.floor(rand() * (hi - lo + 1));

const CATEGORIES = {
  "Staples": ["Atta", "Rice", "Toor Dal", "Moong Dal", "Sugar", "Poha", "Rava", "Maida"],
  "Oils & Ghee": ["Sunflower Oil", "Groundnut Oil", "Coconut Oil", "Ghee", "Mustard Oil"],
  "Spices": ["Turmeric Powder", "Chilli Powder", "Garam Masala", "Sambar Powder", "Rasam Powder", "Cumin Seeds", "Mustard Seeds"],
  "Dairy": ["Milk 1L", "Curd 500g", "Paneer 200g", "Butter 100g", "Cheese Slices"],
  "Snacks": ["Murukku", "Mixture", "Banana Chips", "Namkeen", "Biscuits", "Khakhra"],
  "Beverages": ["Filter Coffee Powder", "Tea Powder", "Tender Coconut Water", "Buttermilk", "Fruit Juice"],
  "Fruits & Vegetables": ["Onion 1kg", "Tomato 1kg", "Potato 1kg", "Banana Dozen", "Coconut", "Curry Leaves", "Coriander"],
  "Personal Care": ["Soap", "Shampoo", "Toothpaste", "Hair Oil", "Face Wash"],
  "Household": ["Detergent", "Dishwash Bar", "Floor Cleaner", "Phenyl", "Garbage Bags"],
  "Bakery": ["Bread", "Buns", "Rusk", "Cake Slice"],
  "Frozen & Ready": ["Idli Batter", "Dosa Batter", "Frozen Peas", "Ready Chapati"],
  "Baby & Health": ["Diapers", "Baby Wipes", "ORS", "Vitamin C"],
};
const BRANDS = ["Aashirvaad", "Fortune", "MTR", "Nandini", "Aachi", "Haldiram's", "Amul", "Tata", "Nilgiris", "iD", "Patanjali", "24 Mantra", "Sakthi", "Anil", "Dabur"];
const SIZES = ["", " 250g", " 500g", " 1kg", " 2kg", " 5kg", " Pack of 2", " Family Pack"];

function products() {
  const out = [];
  const cats = Object.keys(CATEGORIES);
  for (let i = 0; i < N_PRODUCTS; i++) {
    const category = cats[i % cats.length];
    const base = pick(CATEGORIES[category]);
    out.push({ sku: `SKU-${100001 + i}`, name: `${pick(BRANDS)} ${base}${pick(SIZES)}`.trim(), category, brand: BRANDS[int(0, BRANDS.length - 1)],
      price_inr: int(10, 2000), created_at: new Date(Date.UTC(2026, 0, 1 + (i % 200))) });
  }
  return out;
}

function orders(prods) {
  // 60 "basket clusters" so co-purchase is non-trivial; every order draws 2–8 items, most from one cluster
  const clusters = Array.from({ length: 60 }, () => Array.from({ length: int(6, 12) }, () => pick(prods)));
  const out = [];
  const now = Date.now();
  for (let i = 0; i < N_ORDERS; i++) {
    const cluster = pick(clusters);
    const n = int(2, 8);
    const seen = new Set(); const items = [];
    while (items.length < n) {
      const p = rand() < 0.8 ? pick(cluster) : pick(prods);
      if (seen.has(p.sku)) continue;
      seen.add(p.sku);
      items.push({ sku: p.sku, qty: int(1, 4), unit_price_inr: p.price_inr });
    }
    out.push({ order_id: `ORD-${500001 + i}`, customer_id: `CUST-${int(1000, 6000)}`,
      ordered_at: new Date(now - int(0, 120) * 86400000 - int(0, 86399) * 1000), items,
      total_inr: items.reduce((t, it) => t + it.qty * it.unit_price_inr, 0) });
  }
  return out;
}

const client = new MongoClient(uri);
try {
  await client.connect();
  const db = client.db(); // database name comes from the URI path
  await db.collection("products").drop().catch(() => {});
  await db.collection("orders").drop().catch(() => {});
  const prods = products();
  await db.collection("products").insertMany(prods, { ordered: false });
  await db.collection("products").createIndexes([{ key: { sku: 1 }, unique: true }, { key: { category: 1, name: 1 } }]);
  const ords = orders(prods);
  for (let i = 0; i < ords.length; i += 1000) await db.collection("orders").insertMany(ords.slice(i, i + 1000), { ordered: false });
  await db.collection("orders").createIndexes([{ key: { order_id: 1 }, unique: true }, { key: { "items.sku": 1, ordered_at: -1 } }, { key: { ordered_at: -1 } }]);
  const summary = { products: await db.collection("products").countDocuments(), orders: await db.collection("orders").countDocuments() };
  console.log(JSON.stringify(summary));
} catch (e) {
  console.error("seed failed:", e.message);
  process.exit(1);
} finally {
  await client.close();
}
'''

GOLDEN_PACKAGE_JSON = """{
  "name": "poc-seed",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "scripts": { "seed": "node seed.js" },
  "dependencies": { "mongodb": "^6.9.0" }
}
"""

GOLDEN_MANIFEST = """{"component": "seed", "runtime": "node20", "workdir": "seed",
 "entrypoints": {"install": ["npm install --no-audit --no-fund"], "seed": ["node seed.js"]},
 "env": {"required": ["MONGODB_URI", "SEED_MAX_DOCS"]},
 "expected_output": "json_summary_last_line"}
"""

GOLDEN_README = (
    "# Seed\n"
    "Deterministic (mulberry32, seeded from schema_design.seed_requirements.deterministic_seed), "
    "idempotent (drop & recreate), respects SEED_MAX_DOCS and each collection's seed.count. "
    'Prints one JSON summary line `{"<collection>": n, ...}`. Reads MONGODB_URI (database name from the URI path).\n'
)


def golden_example() -> str:
    """The golden seed component as the model sees it (one-shot exemplar)."""
    return json.dumps({
        "files": {
            "seed.js": GOLDEN_SEED_JS,
            "package.json": GOLDEN_PACKAGE_JSON,
            "component.manifest.json": GOLDEN_MANIFEST,
            "SEED_README.md": GOLDEN_README,
        }
    })


# Atlas Search / Vector Search indexes (2026-09-26, medicine finder): a POC whose query patterns use $search /
# $searchMeta / $vectorSearch needs those indexes, and nothing else creates them. The seed owns them because it
# drops and re-inserts the collections (a drop also drops the collection's search indexes). The helper below is
# given to the model verbatim; it is idempotent (creates only missing names, tolerates the post-drop
# IndexAlreadyExists race) and waits until every index is queryable. Proven on the `pov` cluster (M30) with a
# readWrite-scoped POC user: createSearchIndexes is allowed and both kinds turn queryable in ~25 s.
SEARCH_INDEX_HELPER_JS = r"""// Atlas Search / Vector Search indexes — idempotent. Call AFTER the collections are re-inserted (a drop removes
// a collection's search indexes). Creates each missing index by name, then waits until all are queryable.
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function liveSearchIndex(db, collection, name) {
  const found = await db.collection(collection).listSearchIndexes(name).toArray();
  return found.find((ix) => ix.status !== "DELETING") || null;
}
async function ensureSearchIndexes(db, specs, timeoutMs = 240000) {
  const t0 = Date.now();
  for (const { collection, name, type, definition } of specs) {
    while (!(await liveSearchIndex(db, collection, name))) {
      try {
        await db.collection(collection).createSearchIndexes([{ name, type, definition }]);
      } catch (e) {
        // 68 IndexAlreadyExists: the dropped collection's index is still being removed — retry until it is gone
        if (e.code !== 68 || Date.now() - t0 > timeoutMs) throw e;
        await sleep(5000);
      }
    }
  }
  for (;;) {
    const pending = [];
    for (const { collection, name } of specs) {
      const ix = await liveSearchIndex(db, collection, name);
      if (!ix || !ix.queryable) pending.push(`${collection}.${name}`);
    }
    if (!pending.length) return true;
    if (Date.now() - t0 > timeoutMs) {
      console.error(`warning: search indexes not queryable after ${timeoutMs} ms: ${pending.join(", ")}`);
      return false;
    }
    await sleep(5000);
  }
}
"""

_SEARCH_STAGE_RE = re.compile(r"\$(vectorSearch|searchMeta|search)\b")
_INDEX_NAME_RE = re.compile(r"""index\s*:\s*['"]([A-Za-z0-9_\-]+)['"]""")


def search_index_requirements(query_patterns: dict[str, Any] | None) -> list[dict[str, str]]:
    """The Atlas Search / Vector Search indexes the query patterns name: [{name, kind, collection}], kind
    "vectorSearch" when the name is used by a $vectorSearch stage, else "search". A search pattern that names no
    index needs the index called "default"."""
    out: dict[str, dict[str, str]] = {}
    for p in (query_patterns or {}).get("patterns", []) or []:
        text = " ".join(str(p.get(k, "")) for k in ("pseudocode", "description", "aggregation_sketch"))
        stages = set(_SEARCH_STAGE_RE.findall(text))
        if not stages:
            continue
        collection = (p.get("collections") or [""])[0]
        names = _INDEX_NAME_RE.findall(text) or ["default"]
        for n in names:
            # a name used next to $vectorSearch is a vector index; the search-only stages share a search index
            vec = "vectorSearch" in stages and (stages == {"vectorSearch"} or re.search(
                r"\$vectorSearch\s*:\s*\{[^}]*index\s*:\s*['\"]" + re.escape(n), text) is not None)
            kind = "vectorSearch" if vec else "search"
            cur = out.get(n)
            if cur is None or (kind == "vectorSearch" and cur["kind"] != "vectorSearch"):
                out[n] = {"name": n, "kind": kind, "collection": collection}
    return sorted(out.values(), key=lambda r: r["name"])


def validate_search_indexes(files: dict[str, str], required: list[dict[str, str]]) -> list[str]:
    """The seed must create every required search index, idempotently, with the driver's createSearchIndexes."""
    if not required:
        return []
    src = files.get("seed.js", "")
    errors: list[str] = []
    if "createSearchIndexes" not in src or "listSearchIndexes" not in src:
        errors.append("seed.js must create the Atlas Search / Vector Search indexes with the ensureSearchIndexes helper "
                      "(listSearchIndexes + createSearchIndexes) after re-inserting the collections")
    for r in required:
        if f'"{r["name"]}"' not in src and f"'{r['name']}'" not in src:
            errors.append(f'seed.js does not create the {r["kind"]} index "{r["name"]}" on {r["collection"]}')
    if any(r["kind"] == "vectorSearch" for r in required) and not re.search(r"""type\s*:\s*['"]vectorSearch['"]""", src):
        errors.append('seed.js must create its vector index with type: "vectorSearch" (fields: vector + filter)')
    try:
        ver = json.loads(files.get("package.json", "{}")).get("dependencies", {}).get("mongodb", "")
        major = int(re.sub(r"[^0-9.]", "", ver).split(".")[0] or 0)
    except (json.JSONDecodeError, ValueError, AttributeError):
        major = 0
    if major and major < 6:
        errors.append("package.json must pin mongodb >= 6 (createSearchIndexes)")
    return errors


SYSTEM = """You are the Data Seeding Agent of an automated POC builder. From a MongoDB schema design and query
patterns you generate a Node.js 20 seed script that fills a MongoDB database with realistic, deterministic data.

Hard rules (every file must obey):
- Node 20, ESM ("type":"module"), the official `mongodb` driver only (pin the version in package.json).
- Read ALL configuration from environment variables: MONGODB_URI (never a literal connection string anywhere)
  and SEED_MAX_DOCS. The database name comes from the URI path (`client.db()` with no argument).
- Deterministic: seed a PRNG (mulberry32) with schema_design.seed_requirements.deterministic_seed so every run
  produces identical data.
- Idempotent: drop each collection, then insert. Create every index declared in schema_design (unique where declared).
- Atlas Search / Vector Search: when the query patterns use $search, $searchMeta or $vectorSearch, the seed creates
  every search index they name, AFTER the inserts, with the ensureSearchIndexes helper given in the request (copy it
  verbatim; only write the specs). Search index: {collection, name, definition: {mappings: {dynamic: true, fields:
  {<autocompleted field>: [{type: "string"}, {type: "autocomplete", tokenization: "edgeGram", minGrams: 2, maxGrams: 15,
  foldDiacritics: true}]}}}}. Vector index: {collection, name, type: "vectorSearch", definition: {fields: [{type:
  "vector", path, numDimensions: <the exact length of the vectors you generate>, similarity: "cosine"}, {type:
  "filter", path: <each field used in that $vectorSearch filter>}...]}}. `await ensureSearchIndexes(db, SPECS)` before
  printing the summary line; never drop or update a search index.
- Respect SEED_MAX_DOCS as an upper bound and each collection's seed.count. Insert in batches for large volumes.
- Realistic data honouring each collection's generator_hints (locale, value ranges, relationships between collections
  such as order line items referencing product SKUs, and co-occurrence clusters where the hints ask for them).
- On success print EXACTLY ONE final stdout line: a JSON object of {collectionName: insertedCount}. Exit non-zero on error.
- package.json pins dependency versions. Installs run with `npm install --no-audit --no-fund` (no lockfile is shipped).
- Keep the file count small and readable.

You MUST return ONE JSON object and nothing else — no prose, no markdown fences. Its shape is:
{"files": {"seed.js": "...", "package.json": "...", "component.manifest.json": "...", "SEED_README.md": "..."}}
component.manifest.json must satisfy this JSON Schema (component "seed"):
%s
"""


def _system_message() -> SystemMessage:
    return SystemMessage(content=SYSTEM % json.dumps(schema_for("component_manifest")))


def _human_message(inputs: dict[str, Any], mode: str, failure: dict[str, Any] | None,
                   previous_source: dict[str, str] | None) -> HumanMessage:
    parts = [
        "Generate the `seed` component for this POC.",
        "\n## schema_design.json\n" + json.dumps(inputs.get("schema_design", {}), indent=2),
    ]
    if inputs.get("query_patterns"):
        parts.append("\n## query_patterns.json (for realistic co-occurrence)\n"
                     + json.dumps(inputs["query_patterns"], indent=2))
    required = search_index_requirements(inputs.get("query_patterns"))
    if required:
        parts.append("\n## Atlas Search / Vector Search indexes this seed MUST create (after the inserts)\n"
                     + "\n".join(f'- "{r["name"]}" ({r["kind"]}) on collection {r["collection"]}' for r in required)
                     + "\nInclude this helper in seed.js verbatim and call `await ensureSearchIndexes(db, SPECS)`:\n"
                     + SEARCH_INDEX_HELPER_JS)
    parts.append("\n## Worked example — the golden `seed` component for a different POC (Kirana Basket). "
                 "Match its structure, determinism and single-JSON-summary-line convention; adapt collections, "
                 "fields, volumes and realism to THIS schema_design:\n" + golden_example())
    if mode == "repair" and failure is not None:
        parts.append("\n## THIS IS A REPAIR. FailureReport:\n" + json.dumps(failure, indent=2))
        if previous_source:
            parts.append("\n## Previous source (fix ONLY what the failure indicates; keep the manifest entrypoints, "
                         "env variable names and the JSON-summary output contract unchanged):\n"
                         + json.dumps({"files": previous_source}))
        parts.append("\nAlso add a REPAIR_NOTES.md file describing what you changed and why.")
    parts.append("\nReturn ONE JSON object of the shape {\"files\": {...}} only.")
    return HumanMessage(content="\n".join(parts))


def build_messages(inputs: dict[str, Any], mode: str = "code", failure: dict[str, Any] | None = None,
                   previous_source: dict[str, str] | None = None) -> list:
    """The message list sent to the model. Exposed so tests can assert prompt contents."""
    return [_system_message(), _human_message(inputs, mode, failure, previous_source)]


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*\n", "", t)
        t = re.sub(r"\n```\s*$", "", t)
    return t.strip()


def parse_output(text: str) -> dict[str, Any]:
    t = _strip_fences(text)
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        # The model sometimes trails prose, REPAIR_NOTES, or a second block after the JSON object
        # ("Extra data: line 1 column N"), or prefixes a sentence before it. Decode only the first
        # complete JSON value (starting at the first brace) and ignore anything after it.
        start = t.find("{")
        if start < 0:
            raise
        obj, _end = json.JSONDecoder().raw_decode(t[start:])
    if not isinstance(obj, dict) or "files" not in obj or not isinstance(obj["files"], dict):
        raise ValueError('output must be a JSON object of shape {"files": {"path": "content", ...}}')
    return obj


def validate_files(files: dict[str, str], required_indexes: list[dict[str, str]] | None = None) -> list[str]:
    """Return a list of human-readable problems (empty == valid)."""
    errors: list[str] = validate_search_indexes(files, required_indexes or [])
    for f in REQUIRED_FILES:
        if f not in files:
            errors.append(f"missing required file {f}")
    if "component.manifest.json" in files:
        try:
            manifest = json.loads(files["component.manifest.json"])
        except json.JSONDecodeError as e:
            errors.append(f"component.manifest.json is not valid JSON: {e}")
        else:
            if manifest.get("component") != COMPONENT:
                errors.append(f'component.manifest.json component must be "{COMPONENT}"')
            try:
                validate("component_manifest", manifest)
            except ContractError as e:
                errors.append(f"component.manifest.json contract errors: {e.errors}")
    if "package.json" in files:
        try:
            json.loads(files["package.json"])
        except json.JSONDecodeError as e:
            errors.append(f"package.json is not valid JSON: {e}")
    return errors


def _usage(resp: Any) -> dict[str, int]:
    m = getattr(resp, "usage_metadata", None) or {}
    return {"input_tokens": int(m.get("input_tokens", 0)), "output_tokens": int(m.get("output_tokens", 0))}


def generate(inputs: dict[str, Any], mode: str = "code", failure: dict[str, Any] | None = None,
             previous_source: dict[str, str] | None = None, llm: Any = None) -> tuple[dict[str, str], dict[str, int]]:
    """Generate the seed component. Returns (files, token_usage). Retries once on invalid output."""
    if llm is None:
        from .llm import build_llm
        llm = build_llm(temperature=0)
    messages = build_messages(inputs, mode, failure, previous_source)
    required = search_index_requirements(inputs.get("query_patterns"))
    usage = {"input_tokens": 0, "output_tokens": 0}
    last_errors: list[str] = []
    for attempt in range(2):
        resp = llm.invoke(messages)
        u = _usage(resp)
        usage = {k: usage[k] + u[k] for k in usage}
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        try:
            obj = parse_output(text)
            errs = validate_files(obj["files"], required)
            if not errs:
                return obj["files"], usage
            last_errors = errs
        except (ValueError, json.JSONDecodeError) as e:
            last_errors = [str(e)]
        messages = messages + [
            HumanMessage(content="Your previous output was rejected:\n- " + "\n- ".join(last_errors)
                         + "\nReturn ONE corrected JSON object of shape {\"files\": {...}} only.")
        ]
    raise LLMOutputInvalid(last_errors)


def write_files_to_dir(dest_dir: str, files: dict[str, str]) -> list[str]:
    """Write generated files under dest_dir, creating parent directories. Returns relative paths."""
    written = []
    for rel, content in files.items():
        path = os.path.join(dest_dir, rel)
        os.makedirs(os.path.dirname(path) or dest_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        written.append(rel)
    return written


def write_component(poc_id: str, run_id: str, code_version: str, files: dict[str, str],
                    producer: str = "data_seeding_agent") -> dict[str, Any]:
    """Write the component to a temp dir, guardrail-scan it, upload to S3. Returns {keys, prefix}."""
    from poc_shared_tools import guardrails, s3 as s3t
    from poc_shared_tools.errors import ToolError
    with tempfile.TemporaryDirectory() as tmp:
        write_files_to_dir(tmp, files)
        scan = guardrails.scan_bundle(tmp)
        if not scan["ok"]:
            raise ToolError("GUARDRAIL_VIOLATION", json.dumps(scan["violations"])[:1500])
        prefix = f"pocs/{poc_id}/code/{code_version}/{COMPONENT}/"
        keys = s3t.upload_dir(poc_id, run_id, tmp, prefix.rstrip("/"), producer)
    return {"prefix": prefix, "keys": keys}
