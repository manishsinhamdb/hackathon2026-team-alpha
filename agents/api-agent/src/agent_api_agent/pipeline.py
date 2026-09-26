"""Pure generation functions for the API Agent (Spec §6.5).

Three modes:
  - "contract": produce api_contract.yaml (OpenAPI 3.1) from the spec + schema + query patterns.
  - "code":     produce the Express + Mongoose + TypeScript backend that implements the contract.
  - "repair":   regenerate the backend fixing only what a FailureReport indicates.

Functions take plain dicts and return plain dicts so they can run without the SDK, cloud or platform DB
(see scripts/gen_golden_check.py). The LLM is injected (``llm=``) in tests.
Output shape is always ``{"files": {"<relative path>": "<content>", ...}}``; for "contract" the single
file is ``api_contract.yaml``.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from typing import Any

import yaml
from langchain_core.messages import HumanMessage, SystemMessage

from poc_contracts import ContractError, schema_for, validate

from .typecheck import known_fix_hints, typecheck_backend

COMPONENT = "backend"

BACKEND_REQUIRED = ("src/server.ts", "src/models.ts", "src/routes.ts", "tsconfig.json",
                    "package.json", "component.manifest.json", "BACKEND_README.md")


class LLMOutputInvalid(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors)[:2000])


# --- golden artefacts, verbatim, used as one-shot exemplars ------------------
GOLDEN_CONTRACT = r'''openapi: 3.1.0
info:
  title: Kirana Basket recommendations POC API
  version: 1.0.0
  x-poc-id: poc_01K5ZGF1XTVREG0000000000A1
  x-spec-version: v001
servers:
  - url: /api
paths:
  /health:
    get:
      operationId: getHealth
      x-user-story-ids: []
      responses:
        "200":
          description: Service and database are reachable
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Health" }
        "503":
          description: Database not connected
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Error" }
  /categories:
    get:
      operationId: listCategories
      x-user-story-ids: [us-02]
      responses:
        "200":
          description: Distinct categories with product counts
          content:
            application/json:
              schema:
                type: object
                required: [items]
                properties:
                  items:
                    type: array
                    items: { $ref: "#/components/schemas/Category" }
  /products:
    get:
      operationId: listProducts
      x-user-story-ids: [us-02]
      parameters:
        - { name: category, in: query, required: false, schema: { type: string } }
        - { name: limit, in: query, required: false, schema: { type: integer, minimum: 1, maximum: 100, default: 50 } }
        - { name: cursor, in: query, required: false, schema: { type: string }, description: Opaque cursor from a previous page }
      responses:
        "200":
          description: Products, sorted by name
          content:
            application/json:
              schema: { $ref: "#/components/schemas/ProductPage" }
  /products/{sku}:
    get:
      operationId: getProduct
      x-user-story-ids: [us-01]
      parameters:
        - { name: sku, in: path, required: true, schema: { type: string } }
      responses:
        "200":
          description: One product
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Product" }
        "404":
          description: Unknown SKU
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Error" }
  /products/{sku}/recommendations:
    get:
      operationId: getRecommendations
      x-user-story-ids: [us-01]
      parameters:
        - { name: sku, in: path, required: true, schema: { type: string } }
        - { name: limit, in: query, required: false, schema: { type: integer, minimum: 1, maximum: 20, default: 10 } }
      responses:
        "200":
          description: Products bought together with this SKU in the last 90 days, most frequent first
          content:
            application/json:
              schema:
                type: object
                required: [sku, items]
                properties:
                  sku: { type: string }
                  items:
                    type: array
                    items: { $ref: "#/components/schemas/Recommendation" }
        "404":
          description: Unknown SKU
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Error" }
  /dashboard/top-products:
    get:
      operationId: getTopProducts
      x-user-story-ids: [us-03]
      parameters:
        - { name: days, in: query, required: false, schema: { type: integer, minimum: 1, maximum: 365, default: 30 } }
        - { name: limit, in: query, required: false, schema: { type: integer, minimum: 1, maximum: 50, default: 10 } }
      responses:
        "200":
          description: Top products by units sold in the window
          content:
            application/json:
              schema:
                type: object
                required: [days, items]
                properties:
                  days: { type: integer }
                  items:
                    type: array
                    items: { $ref: "#/components/schemas/TopProduct" }
components:
  schemas:
    Health:
      type: object
      required: [status, db]
      properties:
        status: { type: string, enum: [ok] }
        db: { type: string, enum: [connected] }
    Error:
      type: object
      required: [error]
      properties:
        error:
          type: object
          required: [code, message]
          properties:
            code: { type: string }
            message: { type: string }
    Category:
      type: object
      required: [name, count]
      properties:
        name: { type: string }
        count: { type: integer }
    Product:
      description: Maps to schema_design.json collection `products`
      type: object
      required: [sku, name, category, brand, price_inr]
      properties:
        sku: { type: string }
        name: { type: string }
        category: { type: string }
        brand: { type: string }
        price_inr: { type: number }
    ProductPage:
      type: object
      required: [items]
      properties:
        items:
          type: array
          items: { $ref: "#/components/schemas/Product" }
        next_cursor: { type: [string, "null"] }
    Recommendation:
      allOf:
        - $ref: "#/components/schemas/Product"
        - type: object
          required: [orders_together]
          properties:
            orders_together: { type: integer, description: Number of orders containing both SKUs }
    TopProduct:
      allOf:
        - $ref: "#/components/schemas/Product"
        - type: object
          required: [units]
          properties:
            units: { type: integer }
'''

GOLDEN_SERVER_TS = r'''import express, { Request, Response, NextFunction } from "express";
import mongoose from "mongoose";
import { api } from "./routes.js";

const uri = process.env.MONGODB_URI;
if (!uri) { console.error("MONGODB_URI is required"); process.exit(2); }
const port = parseInt(process.env.PORT || "8080", 10);

const app = express();
app.use(express.json());
app.use((req, _res, next) => { console.log(JSON.stringify({ t: new Date().toISOString(), m: req.method, p: req.originalUrl })); next(); });
app.use("/api", api);
app.use((_req, res) => res.status(404).json({ error: { code: "NOT_FOUND", message: "no such route" } }));
app.use((err: Error, _req: Request, res: Response, _next: NextFunction) => {
  console.error(err);
  res.status(500).json({ error: { code: "INTERNAL", message: err.message } });
});

mongoose.connect(uri, { serverSelectionTimeoutMS: 10000 })
  .then(() => { app.listen(port, () => console.log(`backend listening on ${port}`)); })
  .catch((e) => { console.error("mongo connect failed:", e.message); process.exit(1); });
'''

GOLDEN_MODELS_TS = r'''import mongoose, { Schema } from "mongoose";

export interface Product { sku: string; name: string; category: string; brand: string; price_inr: number; created_at: Date; }
export interface OrderItem { sku: string; qty: number; unit_price_inr: number; }
export interface Order { order_id: string; customer_id: string; ordered_at: Date; items: OrderItem[]; total_inr: number; }

const productSchema = new Schema<Product>({
  sku: { type: String, required: true, unique: true },
  name: { type: String, required: true },
  category: { type: String, required: true },
  brand: { type: String, required: true },
  price_inr: { type: Number, required: true },
  created_at: { type: Date, required: true },
}, { collection: "products", versionKey: false });
productSchema.index({ category: 1, name: 1 });

const orderSchema = new Schema<Order>({
  order_id: { type: String, required: true, unique: true },
  customer_id: { type: String, required: true },
  ordered_at: { type: Date, required: true },
  items: [{ sku: String, qty: Number, unit_price_inr: Number }],
  total_inr: { type: Number, required: true },
}, { collection: "orders", versionKey: false });
orderSchema.index({ "items.sku": 1, ordered_at: -1 });
orderSchema.index({ ordered_at: -1 });

export const ProductModel = mongoose.model<Product>("Product", productSchema);
export const OrderModel = mongoose.model<Order>("Order", orderSchema);
'''

GOLDEN_ROUTES_TS = r'''import { Router, Request, Response, NextFunction } from "express";
import mongoose from "mongoose";
import { ProductModel, OrderModel } from "./models.js";

export const api = Router();
const PROJECT = { _id: 0, sku: 1, name: 1, category: 1, brand: 1, price_inr: 1 };
const daysAgo = (d: number) => new Date(Date.now() - d * 86400000);
const clamp = (v: unknown, lo: number, hi: number, dflt: number) => { const n = parseInt(String(v ?? ""), 10); return Number.isFinite(n) ? Math.max(lo, Math.min(hi, n)) : dflt; };
const wrap = (fn: (req: Request, res: Response) => Promise<unknown>) => (req: Request, res: Response, next: NextFunction) => fn(req, res).catch(next);

// GET /api/health — 200 only when a DB round-trip succeeds (§6.5)
api.get("/health", wrap(async (_req, res) => {
  try {
    await mongoose.connection.db!.admin().ping();
    res.json({ status: "ok", db: "connected" });
  } catch {
    res.status(503).json({ error: { code: "DB_UNAVAILABLE", message: "database ping failed" } });
  }
}));

// GET /api/categories — listCategories (us-02)
api.get("/categories", wrap(async (_req, res) => {
  const items = await ProductModel.aggregate([{ $group: { _id: "$category", count: { $sum: 1 } } }, { $sort: { _id: 1 } },
    { $project: { _id: 0, name: "$_id", count: 1 } }]);
  res.json({ items });
}));

// GET /api/products?category=&limit=&cursor= — listProducts (us-02, qp-02)
api.get("/products", wrap(async (req, res) => {
  const limit = clamp(req.query.limit, 1, 100, 50);
  const q: Record<string, unknown> = {};
  if (req.query.category) q.category = String(req.query.category);
  if (req.query.cursor) q.name = { $gt: Buffer.from(String(req.query.cursor), "base64url").toString() };
  const docs = await ProductModel.find(q, PROJECT).sort({ name: 1, sku: 1 }).limit(limit + 1).lean();
  const items = docs.slice(0, limit);
  const next_cursor = docs.length > limit ? Buffer.from(items[items.length - 1].name).toString("base64url") : null;
  res.json({ items, next_cursor });
}));

// GET /api/products/:sku — getProduct (us-01)
api.get("/products/:sku", wrap(async (req, res) => {
  const p = await ProductModel.findOne({ sku: req.params.sku }, PROJECT).lean();
  if (!p) return res.status(404).json({ error: { code: "NOT_FOUND", message: `unknown sku ${req.params.sku}` } });
  res.json(p);
}));

// GET /api/products/:sku/recommendations — getRecommendations (us-01, qp-01)
api.get("/products/:sku/recommendations", wrap(async (req, res) => {
  const sku = req.params.sku;
  const limit = clamp(req.query.limit, 1, 20, 10);
  if (!(await ProductModel.exists({ sku }))) return res.status(404).json({ error: { code: "NOT_FOUND", message: `unknown sku ${sku}` } });
  const items = await OrderModel.aggregate([
    { $match: { "items.sku": sku, ordered_at: { $gte: daysAgo(90) } } },
    { $unwind: "$items" },
    { $match: { "items.sku": { $ne: sku } } },
    { $group: { _id: "$items.sku", orders_together: { $sum: 1 } } },
    { $sort: { orders_together: -1, _id: 1 } },
    { $limit: limit },
    { $lookup: { from: "products", localField: "_id", foreignField: "sku", as: "product" } },
    { $unwind: "$product" },
    { $project: { _id: 0, orders_together: 1, sku: "$product.sku", name: "$product.name", category: "$product.category", brand: "$product.brand", price_inr: "$product.price_inr" } },
  ]);
  res.json({ sku, items });
}));

// GET /api/dashboard/top-products?days=&limit= — getTopProducts (us-03, qp-03)
api.get("/dashboard/top-products", wrap(async (req, res) => {
  const days = clamp(req.query.days, 1, 365, 30);
  const limit = clamp(req.query.limit, 1, 50, 10);
  const items = await OrderModel.aggregate([
    { $match: { ordered_at: { $gte: daysAgo(days) } } },
    { $unwind: "$items" },
    { $group: { _id: "$items.sku", units: { $sum: "$items.qty" } } },
    { $sort: { units: -1, _id: 1 } },
    { $limit: limit },
    { $lookup: { from: "products", localField: "_id", foreignField: "sku", as: "product" } },
    { $unwind: "$product" },
    { $project: { _id: 0, units: 1, sku: "$product.sku", name: "$product.name", category: "$product.category", brand: "$product.brand", price_inr: "$product.price_inr" } },
  ]);
  res.json({ days, items });
}));
'''

GOLDEN_TSCONFIG = ('{ "compilerOptions": { "target": "ES2022", "module": "NodeNext", "moduleResolution": "NodeNext", '
                   '"outDir": "dist", "rootDir": "src",\n'
                   '    "strict": true, "esModuleInterop": true, "skipLibCheck": true, "types": ["node"] }, "include": ["src"] }\n')

GOLDEN_BACKEND_PKG = """{
  "name": "poc-backend",
  "version": "1.0.0",
  "private": true,
  "scripts": { "build": "tsc -p tsconfig.json", "start": "node dist/server.js" },
  "dependencies": { "express": "^4.21.0", "mongoose": "^8.7.0" },
  "devDependencies": { "@types/express": "^4.17.21", "@types/node": "^20.14.0", "typescript": "^5.6.0" }
}
"""

GOLDEN_BACKEND_MANIFEST = """{"component": "backend", "runtime": "node20", "workdir": "backend",
 "entrypoints": {"install": ["npm install --no-audit --no-fund"], "build": ["npm run build"], "start": ["node dist/server.js"],
                 "healthcheck": {"type": "http", "url": "http://localhost:8080/api/health", "expect": 200}},
 "env": {"required": ["MONGODB_URI", "PORT"], "optional": []}, "ports": [8080]}
"""

GOLDEN_BACKEND_ENV = "MONGODB_URI=<your-mongodb-connection-string>\nPORT=8080\n"

GOLDEN_BACKEND_README = (
    "# Backend\nExpress + Mongoose, TypeScript compiled to `dist/`. Listens on `PORT` (8080) under `/api`. "
    "Reads MONGODB_URI from env only. Implements every operation in `../api_contract.yaml`. "
    'Errors are `{error:{code,message}}`. `/api/health` returns 200 only after a successful DB ping.\n'
)


def golden_contract_example() -> str:
    return json.dumps({"files": {"api_contract.yaml": GOLDEN_CONTRACT}})


def golden_backend_example() -> str:
    return json.dumps({"files": {
        "src/server.ts": GOLDEN_SERVER_TS,
        "src/models.ts": GOLDEN_MODELS_TS,
        "src/routes.ts": GOLDEN_ROUTES_TS,
        "tsconfig.json": GOLDEN_TSCONFIG,
        "package.json": GOLDEN_BACKEND_PKG,
        "component.manifest.json": GOLDEN_BACKEND_MANIFEST,
        ".env.example": GOLDEN_BACKEND_ENV,
        "BACKEND_README.md": GOLDEN_BACKEND_README,
    }})


CONTRACT_SYSTEM = """You are the API Agent of an automated POC builder. From a POC spec (front matter with user stories),
a MongoDB schema design and query patterns you author an OpenAPI 3.1 contract that the backend and frontend both implement.

Hard rules:
- openapi: 3.1.0; a single server with url "/api"; info includes x-poc-id and x-spec-version from the spec.
- GET /health MUST exist (operationId getHealth), returning a Health object {status:"ok", db:"connected"} on 200.
- One operation per query pattern, plus list/get operations per main collection. Every operation has a unique
  operationId and an x-user-story-ids array (the story ids it serves, [] for health).
- Every path parameter (e.g. {sku}) MUST be declared in that operation's `parameters` with in: path, required: true.
- Pagination via `limit` and an opaque `cursor` query parameter on list endpoints.
- A reusable Error schema {error:{code,message}}; error responses (e.g. 404) reference it.
- Component schemas map to the schema_design collections; keep field names identical to the schema.

Return ONE JSON object and nothing else — no prose, no markdown fences — of shape:
{"files": {"api_contract.yaml": "<the YAML document as a string>"}}
"""

BACKEND_SYSTEM = """You are the API Agent of an automated POC builder. You implement an OpenAPI contract as a
Node.js 20 backend: Express + Mongoose + TypeScript compiled to dist/.

Hard rules:
- TypeScript sources in src/ (server.ts, models.ts, routes.ts); tsconfig.json compiles to dist/ (module NodeNext).
  Intra-project imports use the ".js" extension (NodeNext), e.g. import { api } from "./routes.js".
- Read ALL config from environment variables: MONGODB_URI and PORT (default 8080). NEVER a literal connection string.
- Implement EVERY operation in the contract with the SAME path, method and response shape (field names identical).
- GET /api/health returns {"status":"ok","db":"connected"} with 200 ONLY after a successful DB ping; 503 otherwise.
- Errors are JSON {"error":{"code","message"}}. Unknown routes → 404; unexpected errors → 500, both in that shape.
- Mongoose models map to the schema_design collections and declare the same indexes.
- package.json pins versions and has scripts build "tsc -p tsconfig.json" and start "node dist/server.js"; installs
  run with `npm install --no-audit --no-fund` (no lockfile is shipped, so devDependencies like typescript are installed).
- The code MUST compile with `tsc --noEmit -p tsconfig.json` under "strict": true — it is type-checked before it is
  accepted. No implicit any, no untyped catch-variable property access, every import resolvable from package.json.
- Atlas Search / Vector Search pipelines (any pipeline containing $search, $searchMeta or $vectorSearch): mongoose's
  PipelineStage[] type does not describe these stages, so NEVER pass them to Model.aggregate() and never annotate them
  PipelineStage[] or Record<string, unknown>[]. Type them with the official driver's Document type and run them on
  the native collection:
      import type { Document } from "mongodb";
      const pipeline: Document[] = [{ $vectorSearch: { index: "vector_index", path: "embedding", queryVector, numCandidates: 100, limit: 10 } }, { $limit: 5 }];
      const docs = await ProductModel.collection.aggregate(pipeline).toArray();
  and add "mongodb": "^6.9.0" to package.json dependencies. Plain pipelines (no Atlas Search stage) may keep Model.aggregate().
- The seed component creates the Atlas Search / Vector Search indexes; the backend never creates or drops indexes of
  that kind. If a search stage fails (index missing or still building) fall back to an equivalent regex/find query.
- Keep the file set small and readable.

Return ONE JSON object and nothing else — no prose, no markdown fences — of shape:
{"files": {"src/server.ts": "...", "src/models.ts": "...", "src/routes.ts": "...", "tsconfig.json": "...",
           "package.json": "...", "component.manifest.json": "...", ".env.example": "...", "BACKEND_README.md": "..."}}
component.manifest.json must satisfy this JSON Schema (component "backend"):
%s
"""


def _contract_system() -> SystemMessage:
    return SystemMessage(content=CONTRACT_SYSTEM)


def _backend_system() -> SystemMessage:
    return SystemMessage(content=BACKEND_SYSTEM % json.dumps(schema_for("component_manifest")))


def _contract_human(inputs: dict[str, Any]) -> HumanMessage:
    parts = [
        "Author api_contract.yaml for this POC.",
        "\n## poc_spec front matter\n" + json.dumps(inputs.get("spec", {}), indent=2),
        "\n## schema_design.json\n" + json.dumps(inputs.get("schema_design", {}), indent=2),
        "\n## query_patterns.json\n" + json.dumps(inputs.get("query_patterns", {}), indent=2),
        "\n## Worked example — the golden contract for a different POC (Kirana Basket). Match its structure "
        "(servers, x-user-story-ids, path parameters, Error schema, pagination); adapt paths, operations and schemas "
        "to THIS spec:\n" + golden_contract_example(),
        "\nReturn ONE JSON object {\"files\": {\"api_contract.yaml\": \"...\"}} only.",
    ]
    return HumanMessage(content="\n".join(parts))


def _backend_human(inputs: dict[str, Any], mode: str, failure: dict[str, Any] | None,
                   previous_source: dict[str, str] | None) -> HumanMessage:
    parts = [
        "Implement the backend for this POC.",
        "\n## api_contract.yaml (authoritative — implement every operation)\n" + inputs.get("contract_yaml", ""),
        "\n## schema_design.json\n" + json.dumps(inputs.get("schema_design", {}), indent=2),
    ]
    if inputs.get("query_patterns"):
        parts.append("\n## query_patterns.json (aggregation sketches)\n" + json.dumps(inputs["query_patterns"], indent=2))
    parts.append("\n## Worked example — the golden backend for a different POC (Kirana Basket). Match its structure, "
                 "health-after-ping behaviour, error shape and manifest; adapt models, routes and aggregations to THIS "
                 "contract and schema:\n" + golden_backend_example())
    if mode == "repair" and failure is not None:
        parts.append("\n## THIS IS A REPAIR. FailureReport:\n" + json.dumps(failure, indent=2))
        hints = known_fix_hints(json.dumps(failure))
        if hints:
            parts.append("\n## Known fix for this failure (apply it):\n- " + "\n- ".join(hints))
        if previous_source:
            parts.append("\n## Previous source (fix ONLY what the failure indicates; keep the api contract, manifest "
                         "entrypoints, env variable names unchanged):\n" + json.dumps({"files": previous_source}))
        parts.append("\nAlso add a REPAIR_NOTES.md file describing what you changed and why.")
    parts.append("\nReturn ONE JSON object {\"files\": {...}} only.")
    return HumanMessage(content="\n".join(parts))


def build_messages(inputs: dict[str, Any], mode: str, failure: dict[str, Any] | None = None,
                   previous_source: dict[str, str] | None = None) -> list:
    if mode == "contract":
        return [_contract_system(), _contract_human(inputs)]
    return [_backend_system(), _backend_human(inputs, mode, failure, previous_source)]


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


def _declared_path_params(op: dict[str, Any]) -> set[str]:
    return {p.get("name") for p in op.get("parameters", []) if p.get("in") == "path"}


def validate_contract(files: dict[str, str]) -> list[str]:
    errors: list[str] = []
    if "api_contract.yaml" not in files:
        return ["missing api_contract.yaml"]
    try:
        doc = yaml.safe_load(files["api_contract.yaml"])
    except yaml.YAMLError as e:
        return [f"api_contract.yaml is not valid YAML: {e}"]
    if not isinstance(doc, dict):
        return ["api_contract.yaml did not parse to a mapping"]
    for key in ("openapi", "paths", "components"):
        if key not in doc:
            errors.append(f"contract missing top-level `{key}`")
    paths = doc.get("paths", {}) or {}
    if "/health" not in paths:
        errors.append("contract has no GET /health")
    for path, methods in paths.items():
        tokens = set(re.findall(r"\{([^}]+)\}", path))
        for method, op in (methods or {}).items():
            if not isinstance(op, dict):
                continue
            if not op.get("operationId"):
                errors.append(f"{method.upper()} {path} missing operationId")
            if "x-user-story-ids" not in op:
                errors.append(f"{method.upper()} {path} missing x-user-story-ids")
            missing = tokens - _declared_path_params(op)
            if missing:
                errors.append(f"{method.upper()} {path} path parameter(s) not declared: {sorted(missing)}")
    return errors


def validate_backend(files: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for f in BACKEND_REQUIRED:
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
    errors.extend(lint_atlas_search_typing(files))
    return errors


_ATLAS_STAGE_RE = re.compile(r"\$(vectorSearch|searchMeta|search)\b")
_DRIVER_DOCUMENT_IMPORT_RE = re.compile(r"import\s+(type\s+)?\{[^}]*\bDocument\b[^}]*\}\s+from\s+[\"']mongodb[\"']")


def lint_atlas_search_typing(files: dict[str, str]) -> list[str]:
    """The pipeline-typing rule, checked statically: a TS source that builds a $search/$searchMeta/$vectorSearch
    stage must type its pipelines with the driver's Document (imported from "mongodb"), must not annotate them
    PipelineStage[], and package.json must depend on mongodb. Catches the medicine-finder TS2769 even when the
    compile gate cannot run."""
    errors: list[str] = []
    uses = [rel for rel, src in files.items() if rel.endswith(".ts") and _ATLAS_STAGE_RE.search(src)]
    for rel in uses:
        src = files[rel]
        if not _DRIVER_DOCUMENT_IMPORT_RE.search(src):
            errors.append(f'{rel} builds an Atlas Search stage but does not `import type {{ Document }} from "mongodb"` '
                          "to type its pipelines as Document[]")
        if "PipelineStage" in src:
            errors.append(f"{rel} uses mongoose PipelineStage with an Atlas Search stage; type the pipeline Document[] "
                          "and run it with Model.collection.aggregate(pipeline).toArray()")
    if uses:
        try:
            deps = json.loads(files.get("package.json", "{}")).get("dependencies", {})
        except json.JSONDecodeError:
            deps = {}
        if "mongodb" not in deps:
            errors.append('package.json must list "mongodb" in dependencies (the Document type for Atlas Search pipelines)')
    return errors


def _usage(resp: Any) -> dict[str, int]:
    m = getattr(resp, "usage_metadata", None) or {}
    return {"input_tokens": int(m.get("input_tokens", 0)), "output_tokens": int(m.get("output_tokens", 0))}


# The api_execute tool has 540 s; one backend LLM call takes ~150 s and the compile gate ~10-60 s, so no new LLM
# attempt starts after GEN_BUDGET_S (worst case ≈ budget + one call + one type-check).
GEN_BUDGET_S = int(os.environ.get("API_GEN_BUDGET_S", "300"))
MAX_ATTEMPTS = 3


def _rejection(errors: list[str], mode: str, typecheck_failed: bool) -> HumanMessage:
    if mode == "contract":
        return HumanMessage(content="Your previous output was rejected:\n- " + "\n- ".join(errors)
                            + '\nReturn ONE corrected JSON object {"files": {"api_contract.yaml": "..."}} only.')
    if typecheck_failed:
        hints = known_fix_hints("\n".join(errors))
        return HumanMessage(content=(
            "Your backend does not compile. `tsc --noEmit -p tsconfig.json` reported:\n- " + "\n- ".join(errors)
            + ("\n\n" + "\n".join(hints) if hints else "")
            + '\nFix every error. Return ONE JSON object {"files": {...}} containing ONLY the files you change '
              "(complete file contents; unchanged files may be omitted)."))
    return HumanMessage(content="Your previous output was rejected:\n- " + "\n- ".join(errors)
                        + '\nReturn ONE corrected JSON object {"files": {...}} only.')


def generate(inputs: dict[str, Any], mode: str, failure: dict[str, Any] | None = None,
             previous_source: dict[str, str] | None = None, llm: Any = None,
             typecheck: Any = None, report: dict[str, Any] | None = None,
             budget_s: float | None = None) -> tuple[dict[str, str], dict[str, int]]:
    """Generate the contract (mode 'contract') or backend (mode 'code'/'repair'). Returns (files, usage).

    Backend output must pass the static checks AND the compile gate (`typecheck`, default typecheck_backend:
    npm install + tsc --noEmit). A compile failure is fed back with any known-fix hint and the model returns only
    the files it changes, merged onto the previous output. `report` (optional) receives {"typecheck": {...},
    "attempts": n} so the caller can record what the gate saw."""
    if llm is None:
        from .llm import build_llm
        llm = build_llm(temperature=0)
    backend = mode != "contract"
    validator = validate_contract if not backend else validate_backend
    # API_TYPECHECK=0 disables the default gate (unit tests); an explicit `typecheck=` always runs.
    default_check = typecheck_backend if os.environ.get("API_TYPECHECK", "1") != "0" else None
    check = (typecheck or default_check) if backend else None
    budget = GEN_BUDGET_S if budget_s is None else budget_s
    t0 = time.time()
    messages = build_messages(inputs, mode, failure, previous_source)
    usage = {"input_tokens": 0, "output_tokens": 0}
    last_errors: list[str] = []
    current: dict[str, str] | None = None
    rep = report if report is not None else {}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if attempt > 1 and time.time() - t0 > budget:
            last_errors = last_errors + [f"generation budget ({budget:.0f}s) exhausted after {attempt - 1} attempt(s)"]
            break
        rep["attempts"] = attempt
        resp = llm.invoke(messages)
        u = _usage(resp)
        usage = {k: usage[k] + u[k] for k in usage}
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        tc_failed = False
        try:
            obj = parse_output(text)
            files = {**(current or {}), **obj["files"]} if backend else obj["files"]
            errs = validator(files)
            if backend:
                current = files
            if not errs and check is not None:
                tc = check(files)
                rep["typecheck"] = {k: tc.get(k) for k in ("status", "errors", "duration_s")}
                if tc.get("status") == "failed":
                    errs, tc_failed = list(tc.get("errors") or ["tsc failed"]), True
            if not errs:
                return files, usage
            last_errors = errs
        except (ValueError, json.JSONDecodeError) as e:
            last_errors = [str(e)]
        messages = messages + [_rejection(last_errors, mode, tc_failed)]
    raise LLMOutputInvalid(last_errors)


def write_files_to_dir(dest_dir: str, files: dict[str, str]) -> list[str]:
    written = []
    for rel, content in files.items():
        path = os.path.join(dest_dir, rel)
        os.makedirs(os.path.dirname(path) or dest_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        written.append(rel)
    return written


def write_component(poc_id: str, run_id: str, code_version: str, files: dict[str, str],
                    producer: str = "api_agent") -> dict[str, Any]:
    """Guardrail-scan and upload the backend component to S3. Returns {prefix, keys}."""
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


def write_contract(poc_id: str, run_id: str, code_version: str, yaml_text: str,
                   producer: str = "api_agent") -> dict[str, Any]:
    """Guardrail-scan and upload api_contract.yaml to the code version root. Returns {key}."""
    from poc_shared_tools import guardrails, s3 as s3t
    from poc_shared_tools.errors import ToolError
    v = guardrails.scan_script(yaml_text, "api_contract.yaml")
    if v:
        raise ToolError("GUARDRAIL_VIOLATION", json.dumps(v)[:1500])
    key = f"pocs/{poc_id}/code/{code_version}/api_contract.yaml"
    s3t.put_object(poc_id, run_id, key, yaml_text, "application/yaml", producer)
    return {"key": key}
