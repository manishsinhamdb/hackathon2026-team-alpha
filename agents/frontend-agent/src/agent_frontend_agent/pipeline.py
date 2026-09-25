"""Pure generation functions for the Frontend Agent (Spec §6.6).

From the spec front matter (user stories with data-testids) and api_contract.yaml, generate a
Vite + React 18 + TypeScript app: one page per user story carrying EXACTLY the declared data-testids,
an api client with one function per operationId reading VITE_API_BASE_URL, built to static dist/ for nginx.

Functions take plain dicts and return plain dicts (see scripts/gen_golden_check.py). The LLM is injected in tests.
Output shape is ``{"files": {"<relative path>": "<content>", ...}}``.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any

import yaml
from langchain_core.messages import HumanMessage, SystemMessage

from poc_contracts import ContractError, schema_for, validate

COMPONENT = "frontend"

REQUIRED_FILES = ("index.html", "vite.config.ts", "tsconfig.json", "package.json",
                  "component.manifest.json", "src/main.tsx", "src/api/client.ts",
                  "src/vite-env.d.ts", "FRONTEND_README.md")


class LLMOutputInvalid(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors)[:2000])


# --- golden frontend, verbatim, used as the one-shot exemplar ----------------
GOLDEN_MAIN_TSX = r'''import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route, Link, Navigate } from "react-router-dom";
import { CategoriesPage } from "./pages/CategoriesPage";
import { ProductPage } from "./pages/ProductPage";
import { DashboardPage } from "./pages/DashboardPage";

const style = `body{font-family:system-ui,sans-serif;margin:0;background:#f7f7f5;color:#1a1a1a}nav{background:#0f5132;color:#fff;padding:12px 20px;display:flex;gap:20px;align-items:center}nav a{color:#fff;text-decoration:none;font-weight:600}main{max-width:960px;margin:0 auto;padding:20px}h1{font-size:1.4rem}h2{font-size:1.1rem;margin-top:24px}ul{list-style:none;padding:0;margin:0}li{background:#fff;border:1px solid #e3e3e0;border-radius:8px;padding:10px 14px;margin-bottom:8px;display:flex;justify-content:space-between;gap:12px}li a{color:#0f5132;text-decoration:none;font-weight:600}.muted{color:#666;font-size:.9rem}.chips{display:flex;flex-wrap:wrap;gap:8px}.chip{background:#fff;border:1px solid #cfd8d3;border-radius:999px;padding:6px 12px;cursor:pointer}.chip.on{background:#0f5132;color:#fff;border-color:#0f5132}.err{color:#b00020}`;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <style>{style}</style>
    <BrowserRouter>
      <nav>
        <span>Kirana Basket POC</span>
        <Link to="/categories" data-testid="nav-categories">Browse</Link>
        <Link to="/dashboard" data-testid="nav-dashboard">Dashboard</Link>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/categories" replace />} />
          <Route path="/categories" element={<CategoriesPage />} />
          <Route path="/products/:sku" element={<ProductPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
        </Routes>
      </main>
    </BrowserRouter>
  </React.StrictMode>
);
'''

GOLDEN_CLIENT_TS = r'''// Generated from api_contract.yaml — one function per operationId. Base URL from VITE_API_BASE_URL at build time.
const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api";

export interface Product { sku: string; name: string; category: string; brand: string; price_inr: number; }
export interface Category { name: string; count: number; }
export interface Recommendation extends Product { orders_together: number; }
export interface TopProduct extends Product { units: number; }

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) { const body = await r.json().catch(() => ({})); throw new Error(body?.error?.message ?? `HTTP ${r.status}`); }
  return r.json() as Promise<T>;
}

export const getHealth = () => get<{ status: string; db: string }>("/health");
export const listCategories = () => get<{ items: Category[] }>("/categories");
export const listProducts = (category?: string, limit = 50) =>
  get<{ items: Product[]; next_cursor: string | null }>(`/products?${new URLSearchParams({ ...(category ? { category } : {}), limit: String(limit) })}`);
export const getProduct = (sku: string) => get<Product>(`/products/${encodeURIComponent(sku)}`);
export const getRecommendations = (sku: string, limit = 10) => get<{ sku: string; items: Recommendation[] }>(`/products/${encodeURIComponent(sku)}/recommendations?limit=${limit}`);
export const getTopProducts = (days = 30, limit = 10) => get<{ days: number; items: TopProduct[] }>(`/dashboard/top-products?days=${days}&limit=${limit}`);
'''

GOLDEN_CATEGORIES_TSX = r'''// us-02: Shopper browses products by category with prices
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listCategories, listProducts, Category, Product } from "../api/client";

const inr = (n: number) => `₹${n.toLocaleString("en-IN")}`;

export function CategoriesPage() {
  const [cats, setCats] = useState<Category[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [products, setProducts] = useState<Product[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { listCategories().then((r) => { setCats(r.items); if (r.items[0]) setSelected(r.items[0].name); }).catch((e) => setErr(e.message)); }, []);
  useEffect(() => { if (!selected) return; listProducts(selected, 50).then((r) => setProducts(r.items)).catch((e) => setErr(e.message)); }, [selected]);

  return (
    <div>
      <h1>Browse by category</h1>
      {err && <p className="err">{err}</p>}
      <div className="chips" data-testid="us-02-category-list">
        {cats.map((c) => (
          <button key={c.name} className={`chip${c.name === selected ? " on" : ""}`} data-testid="us-02-category-item" onClick={() => setSelected(c.name)}>
            {c.name} <span className="muted">({c.count})</span>
          </button>
        ))}
      </div>
      <h2>{selected ?? ""}</h2>
      <ul data-testid="us-02-product-list">
        {products.map((p) => (
          <li key={p.sku} data-testid="us-02-product-item">
            <span><Link to={`/products/${p.sku}`}>{p.name}</Link> <span className="muted">{p.brand}</span></span>
            <span>{inr(p.price_inr)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
'''

GOLDEN_PRODUCT_TSX = r'''// us-01: Shopper sees "also bought" recommendations on a product page
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getProduct, getRecommendations, Product, Recommendation } from "../api/client";

const inr = (n: number) => `₹${n.toLocaleString("en-IN")}`;

export function ProductPage() {
  const { sku = "" } = useParams();
  const [product, setProduct] = useState<Product | null>(null);
  const [recos, setRecos] = useState<Recommendation[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setProduct(null); setRecos([]); setErr(null);
    Promise.all([getProduct(sku), getRecommendations(sku, 10)])
      .then(([p, r]) => { setProduct(p); setRecos(r.items); })
      .catch((e) => setErr(e.message));
  }, [sku]);

  if (err) return <p className="err">{err}</p>;
  if (!product) return <p className="muted">Loading…</p>;
  return (
    <div>
      <p className="muted"><Link to="/categories">← Browse</Link></p>
      <h1 data-testid="us-01-product-name">{product.name}</h1>
      <p className="muted">{product.brand} · {product.category} · {inr(product.price_inr)}</p>
      <h2>Customers also bought</h2>
      <ul data-testid="us-01-reco-list">
        {recos.map((r) => (
          <li key={r.sku} data-testid="us-01-reco-item">
            <span><Link to={`/products/${r.sku}`}>{r.name}</Link> <span className="muted">bought together in {r.orders_together} orders</span></span>
            <span>{inr(r.price_inr)}</span>
          </li>
        ))}
      </ul>
      {recos.length === 0 && <p className="muted">No order history for this product yet.</p>}
    </div>
  );
}
'''

GOLDEN_DASHBOARD_TSX = r'''// us-03: Category manager sees top-selling products over the last 30 days
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getTopProducts, TopProduct } from "../api/client";

export function DashboardPage() {
  const [items, setItems] = useState<TopProduct[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { getTopProducts(30, 10).then((r) => setItems(r.items)).catch((e) => setErr(e.message)); }, []);
  return (
    <div>
      <h1>Top products — last 30 days</h1>
      {err && <p className="err">{err}</p>}
      <ul data-testid="us-03-top-list">
        {items.map((p, i) => (
          <li key={p.sku} data-testid="us-03-top-item">
            <span>{i + 1}. <Link to={`/products/${p.sku}`}>{p.name}</Link> <span className="muted">{p.category}</span></span>
            <span>{p.units} units</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
'''

GOLDEN_INDEX_HTML = r'''<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Kirana Basket POC</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
'''

GOLDEN_VITE_CONFIG = ('import { defineConfig } from "vite";\n'
                      'import react from "@vitejs/plugin-react";\n'
                      'export default defineConfig({ plugins: [react()], build: { outDir: "dist" } });\n')

GOLDEN_FE_TSCONFIG = ('{ "compilerOptions": { "target": "ES2022", "lib": ["ES2022", "DOM", "DOM.Iterable"], '
                      '"module": "ESNext", "moduleResolution": "bundler",\n'
                      '    "jsx": "react-jsx", "strict": true, "skipLibCheck": true, "noEmit": true, '
                      '"isolatedModules": true, "types": ["vite/client"] }, "include": ["src"] }\n')

GOLDEN_VITE_ENV = '/// <reference types="vite/client" />\n'

GOLDEN_FE_PKG = """{
  "name": "poc-frontend",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "scripts": { "dev": "vite", "build": "tsc -b && vite build" },
  "dependencies": { "react": "^18.3.1", "react-dom": "^18.3.1", "react-router-dom": "^6.26.0" },
  "devDependencies": { "@types/react": "^18.3.0", "@types/react-dom": "^18.3.0", "@vitejs/plugin-react": "^4.3.0", "typescript": "^5.6.0", "vite": "^5.4.0" }
}
"""

GOLDEN_FE_MANIFEST = """{"component": "frontend", "runtime": "node20", "workdir": "frontend",
 "entrypoints": {"install": ["npm install --no-audit --no-fund"], "build": ["npm run build"]},
 "env": {"required": ["VITE_API_BASE_URL"]}, "static_dir": "dist",
 "publish": {"type": "nginx_static", "api_proxy": {"path": "/api", "upstream": "http://127.0.0.1:8080"}}}
"""

GOLDEN_FE_README = (
    "# Frontend\nVite + React 18 + TypeScript. `src/api/client.ts` mirrors api_contract.yaml (one function per "
    "operationId). Reads VITE_API_BASE_URL at build time (default `/api`). One route per user story, each carrying "
    "the data-testids declared in the spec. Builds to static `dist/` for nginx.\n"
)


def golden_example() -> str:
    return json.dumps({"files": {
        "index.html": GOLDEN_INDEX_HTML,
        "vite.config.ts": GOLDEN_VITE_CONFIG,
        "tsconfig.json": GOLDEN_FE_TSCONFIG,
        "package.json": GOLDEN_FE_PKG,
        "component.manifest.json": GOLDEN_FE_MANIFEST,
        "src/main.tsx": GOLDEN_MAIN_TSX,
        "src/vite-env.d.ts": GOLDEN_VITE_ENV,
        "src/api/client.ts": GOLDEN_CLIENT_TS,
        "src/pages/CategoriesPage.tsx": GOLDEN_CATEGORIES_TSX,
        "src/pages/ProductPage.tsx": GOLDEN_PRODUCT_TSX,
        "src/pages/DashboardPage.tsx": GOLDEN_DASHBOARD_TSX,
        "FRONTEND_README.md": GOLDEN_FE_README,
    }})


SYSTEM = """You are the Frontend Agent of an automated POC builder. From the spec's user stories (each with
data-testids) and the OpenAPI contract you generate a Vite + React 18 + TypeScript app.

Hard rules:
- Vite + React 18 + react-router-dom v6. index.html mounts /src/main.tsx into <div id="root">.
- tsconfig.json includes types ["vite/client"] and jsx "react-jsx"; add src/vite-env.d.ts with the vite/client ref.
- src/main.tsx sets up BrowserRouter with a nav bar; each nav link carries data-testid="nav-<route>".
- src/api/client.ts has ONE exported function per operationId in the contract, reads the base URL from
  import.meta.env.VITE_API_BASE_URL (default "/api"), and NEVER hard-codes a host.
- ONE page component per user story. Each page's elements carry EXACTLY the data-testids declared for that story —
  same strings, no more, no fewer. A list container gets the "-list" testid; each list item gets the "-item" testid.
  List items that link to a detail page MUST be elements whose data-testid ends with "-item" and that contain an <a>
  (use react-router <Link>).
- Read all config from environment (VITE_API_BASE_URL). No authentication unless the spec demands it.
- package.json pins versions; build script "tsc -b && vite build"; installs run with `npm install --no-audit --no-fund`.
- component.manifest.json declares static_dir "dist" and publish nginx_static proxying /api to http://127.0.0.1:8080.
- Keep the file set small and readable.

Return ONE JSON object and nothing else — no prose, no markdown fences — of shape {"files": {...}}.
component.manifest.json must satisfy this JSON Schema (component "frontend"):
%s
"""


def _system_message() -> SystemMessage:
    return SystemMessage(content=SYSTEM % json.dumps(schema_for("component_manifest")))


def all_testids(spec: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for story in spec.get("user_stories", []) or []:
        for t in story.get("testids", []) or []:
            ids.append(t)
    return ids


def _human_message(inputs: dict[str, Any], mode: str, failure: dict[str, Any] | None,
                   previous_source: dict[str, str] | None) -> HumanMessage:
    spec = inputs.get("spec", {})
    testids = all_testids(spec)
    parts = [
        "Generate the `frontend` component for this POC.",
        "\n## user_stories (each element must carry EXACTLY these data-testids)\n"
        + json.dumps(spec.get("user_stories", []), indent=2),
        "\n## All required data-testids (every one must appear in the built app):\n" + json.dumps(testids),
        "\n## api_contract.yaml (one client function per operationId)\n" + inputs.get("contract_yaml", ""),
        "\n## Worked example — the golden frontend for a different POC (Kirana Basket). Match its structure, routing, "
        "nav testids, api client and testid placement; adapt pages, routes and client functions to THIS spec and "
        "contract:\n" + golden_example(),
    ]
    if mode == "repair" and failure is not None:
        parts.append("\n## THIS IS A REPAIR. FailureReport:\n" + json.dumps(failure, indent=2))
        if previous_source:
            parts.append("\n## Previous source (fix ONLY what the failure indicates; keep the manifest entrypoints, "
                         "env variable names and every data-testid unchanged):\n" + json.dumps({"files": previous_source}))
        parts.append("\nAlso add a REPAIR_NOTES.md file describing what you changed and why.")
    parts.append("\nReturn ONE JSON object {\"files\": {...}} only.")
    return HumanMessage(content="\n".join(parts))


def build_messages(inputs: dict[str, Any], mode: str = "code", failure: dict[str, Any] | None = None,
                   previous_source: dict[str, str] | None = None) -> list:
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


_DEFAULT_FRONTEND_README = (
    "# Frontend\n\nVite + React 18 + TypeScript single-page app. `npm ci && npm run build` produces `dist/`.\n"
    "The API base URL is relative (`/api`) so the backend that serves the build also serves the API.\n"
    "One route per user story; each page carries the `data-testid`s declared in the spec.\n"
)


def _backfill_docs(files: dict[str, str]) -> dict[str, str]:
    """Backfill trivial, build-irrelevant docs the LLM occasionally omits (e.g. FRONTEND_README.md), so a
    missing README never fails an otherwise-valid generation. Never overwrites content the model produced."""
    files.setdefault("FRONTEND_README.md", _DEFAULT_FRONTEND_README)
    return files


def validate_files(files: dict[str, str], testids: list[str]) -> list[str]:
    errors: list[str] = []
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
    blob = "\n".join(files.values())
    missing = [t for t in testids if t not in blob]
    if missing:
        errors.append(f"these required data-testids do not appear anywhere in the app: {missing}")
    return errors


def _usage(resp: Any) -> dict[str, int]:
    m = getattr(resp, "usage_metadata", None) or {}
    return {"input_tokens": int(m.get("input_tokens", 0)), "output_tokens": int(m.get("output_tokens", 0))}


def generate(inputs: dict[str, Any], mode: str = "code", failure: dict[str, Any] | None = None,
             previous_source: dict[str, str] | None = None, llm: Any = None) -> tuple[dict[str, str], dict[str, int]]:
    if llm is None:
        from .llm import build_llm
        llm = build_llm(temperature=0)
    testids = all_testids(inputs.get("spec", {}))
    messages = build_messages(inputs, mode, failure, previous_source)
    usage = {"input_tokens": 0, "output_tokens": 0}
    last_errors: list[str] = []
    for _ in range(2):
        resp = llm.invoke(messages)
        u = _usage(resp)
        usage = {k: usage[k] + u[k] for k in usage}
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        try:
            obj = parse_output(text)
            _backfill_docs(obj["files"])
            errs = validate_files(obj["files"], testids)
            if not errs:
                return obj["files"], usage
            last_errors = errs
        except (ValueError, json.JSONDecodeError) as e:
            last_errors = [str(e)]
        messages = messages + [HumanMessage(content="Your previous output was rejected:\n- "
                               + "\n- ".join(last_errors) + "\nReturn ONE corrected JSON object {\"files\": {...}} only.")]
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
                    producer: str = "frontend_agent") -> dict[str, Any]:
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
