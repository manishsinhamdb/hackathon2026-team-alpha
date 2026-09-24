"""Pure generation functions for the Draft Agent (Spec §6.2).

Three LLM-backed stages, each a plain function that takes/returns plain dicts so it can run
without the SDK, cloud or platform DB (see scripts/draft_check.py). The LLM is injected (``llm=``)
in tests.

  - analyze(...)          -> {"extraction": {...}, "missing": [...]}   (JSON-only extraction)
  - generate_questions()  -> [{question_id, field, question, why_it_matters, suggestions}]  (<=5)
  - generate_artifacts()  -> {"files": {poc_spec.md, schema_design.json, query_patterns.json},
                              "assumptions": [...], "user_story_count": n, "poc_definitions": {...}}

The golden fixture (fixtures/golden/spec/v001) is embedded verbatim below as the one-shot
exemplar, exactly as the API/Frontend/Seeding agents embed theirs.
"""
from __future__ import annotations

import json
import re
from typing import Any

import yaml
from langchain_core.messages import HumanMessage, SystemMessage

from poc_contracts import ContractError, validate

# The Draft Agent caps its own clarification loop at 3 rounds (task spec: ask while round < 3);
# the platform guardrail max_clarification_rounds is 5.
MAX_CLARIFICATION_ROUNDS = 3

# Required requirement fields (§6.2). Absence of any of these puts the field in `missing`.
# database_choice is collected by the Chat Agent, not by this analysis (task §BEHAVIOUR step 2).
REQUIRED_FIELDS = (
    "title",
    "business_objective",
    "target_users",
    "user_stories",
    "success_criteria",
    "data_entities",
    "timeline_constraints",
)

STACK = {"backend": "node-express", "frontend": "react-vite", "database": "mongodb"}

# Fixed §8.2 section order → (heading, key in the LLM's `sections` object).
SPEC_SECTIONS = (
    ("Executive summary", "executive_summary"),
    ("Business objective", "business_objective"),
    ("Users and user stories", "users_and_user_stories"),
    ("Functional scope", "functional_scope"),
    ("High-level architecture", "high_level_architecture"),
    ("Data model summary", "data_model_summary"),
    ("Key queries", "key_queries"),
    ("API surface (high level)", "api_surface"),
    ("Non-functional requirements", "non_functional_requirements"),
    ("Success criteria", "success_criteria_notes"),
    ("Assumptions and open questions", "assumptions_open_questions"),
    ("Timeline and constraints", "timeline_constraints"),
)


class LLMOutputInvalid(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors)[:2000])


# --- golden fixture, verbatim, used as the one-shot exemplar -----------------
GOLDEN_SPEC_MD = r'''---
poc_id: poc_01K5ZGF1XTVREG0000000000A1
spec_version: v001
title: Kirana Basket — live "also bought" recommendations
stack: {backend: node-express, frontend: react-vite, database: mongodb}
user_stories:
  - id: us-01
    title: Shopper sees "also bought" recommendations on a product page
    actor: shopper
    acceptance: ["at least 5 recommendations render for a product with order history", "recommendation API responds under 300 ms p95"]
    testids: [us-01-product-name, us-01-reco-list, us-01-reco-item]
  - id: us-02
    title: Shopper browses products by category with prices
    actor: shopper
    acceptance: ["category list renders", "selecting a category lists its products with price in INR"]
    testids: [us-02-category-list, us-02-category-item, us-02-product-list, us-02-product-item]
  - id: us-03
    title: Category manager sees top-selling products over the last 30 days
    actor: category manager
    acceptance: ["dashboard lists top 10 products by units sold", "counts match an aggregation over orders"]
    testids: [us-03-top-list, us-03-top-item]
success_criteria:
  - id: sc-01
    statement: GET /api/products/{sku}/recommendations p95 latency under 300 ms with 5 000 products and 8 000 orders
    automatable: true
  - id: sc-02
    statement: Every product with at least one order returns >= 5 recommendations
    automatable: true
  - id: sc-03
    statement: Top-products dashboard counts equal the orders aggregation
    automatable: true
  - id: sc-04
    statement: Founders are convinced the recommendations make sense
    automatable: false
seed_requirements: {max_docs_per_collection: 10000, realism: "Indian online grocery, en-IN locale, INR prices"}
assumptions:
  - "Synthetic data is acceptable; shapes follow the customer's Postgres/MySQL tables"
  - "No authentication in the POC"
  - "Co-purchase is computed from the last 90 days of orders"
approved: false
---

# Executive summary
Kirana Basket, an online grocery marketplace for tier-2 cities in Karnataka and Tamil Nadu, wants live "customers also bought" recommendations on every product page, served from order history in MongoDB instead of a four-hour nightly batch across Postgres and MySQL.

# Business objective
Increase basket size by turning a dead-end product page into a discovery surface, and prove that co-purchase recommendations can be served live from operational data.
'''

GOLDEN_SCHEMA_JSON = r'''{
  "collections": [
    {
      "name": "products",
      "description": "Catalogue items",
      "fields": [
        {"name": "sku", "type": "string", "required": true, "example": "SKU-100001"},
        {"name": "name", "type": "string", "required": true, "example": "Aashirvaad Atta 5 kg"},
        {"name": "category", "type": "string", "required": true, "example": "Staples"},
        {"name": "brand", "type": "string", "required": true, "example": "Aashirvaad"},
        {"name": "price_inr", "type": "decimal", "required": true, "example": 289},
        {"name": "created_at", "type": "date", "required": true}
      ],
      "indexes": [{"keys": {"sku": 1}, "unique": true}, {"keys": {"category": 1, "name": 1}}],
      "seed": {"count": 5000, "generator_hints": "Indian grocery brands and products across 12 categories; INR prices 10-2000"}
    },
    {
      "name": "orders",
      "description": "Customer orders with embedded line items",
      "fields": [
        {"name": "order_id", "type": "string", "required": true, "example": "ORD-500001"},
        {"name": "customer_id", "type": "string", "required": true, "example": "CUST-2041"},
        {"name": "ordered_at", "type": "date", "required": true},
        {"name": "items", "type": "array<object>", "required": true},
        {"name": "total_inr", "type": "decimal", "required": true}
      ],
      "indexes": [{"keys": {"order_id": 1}, "unique": true}, {"keys": {"items.sku": 1, "ordered_at": -1}}, {"keys": {"ordered_at": -1}}],
      "relationships": [{"field": "items.sku", "references": "products.sku", "embed_or_reference": "reference"}],
      "seed": {"count": 8000, "generator_hints": "2-8 items per order, last 120 days, co-purchase clusters so recommendations are non-trivial"}
    }
  ]
}'''

GOLDEN_QP_JSON = r'''{
  "patterns": [
    {"id": "qp-01", "user_story_ids": ["us-01"], "name": "Also-bought recommendations for a SKU",
     "description": "Products that co-occur with the given SKU in orders from the last 90 days, ranked by number of orders",
     "collections": ["orders", "products"],
     "pseudocode": "match orders with items.sku = X and ordered_at >= now-90d -> unwind items -> match items.sku != X -> group by items.sku count -> sort desc -> limit 10 -> lookup products",
     "expected_latency_ms_p95": 300,
     "supporting_indexes": [{"collection": "orders", "keys": {"items.sku": 1, "ordered_at": -1}}]},
    {"id": "qp-02", "user_story_ids": ["us-02"], "name": "Products by category",
     "description": "All products in a category sorted by name, paginated",
     "collections": ["products"],
     "pseudocode": "find category = C -> sort name -> limit/cursor",
     "expected_latency_ms_p95": 100,
     "supporting_indexes": [{"collection": "products", "keys": {"category": 1, "name": 1}}]},
    {"id": "qp-03", "user_story_ids": ["us-03"], "name": "Top products last 30 days",
     "description": "Top 10 products by units sold in the last 30 days",
     "collections": ["orders", "products"],
     "pseudocode": "match ordered_at >= now-30d -> unwind items -> group by sku sum qty -> sort desc -> limit 10 -> lookup products",
     "expected_latency_ms_p95": 300,
     "supporting_indexes": [{"collection": "orders", "keys": {"ordered_at": -1}}]}
  ]
}'''


# =============================================================================
# Prompts
# =============================================================================

ANALYZE_SYSTEM = """You are the Draft Agent of an automated POC builder. From a meeting transcript (plus any
answers the user has already given to earlier clarifying questions) you extract the structured requirements
needed to design a proof-of-concept web application, and you decide which REQUIRED fields the material does
not yet settle.

Extract these fields:
- title: a short product title for the POC.
- business_objective: the outcome the POC must prove.
- target_users: who uses it (roles / devices).
- user_stories: 3 to 7 concrete stories, each a short sentence naming the actor and what they see/do.
- success_criteria: measurable acceptance targets (latency, counts, correctness). Prefer numbers.
- data_entities: the main data entities WITH rough volumes (e.g. "5000 products", "8000 orders") and their key fields.
- timeline_constraints: deadline, region, platform constraints.
- integrations (optional), nfr (optional), out_of_scope (optional).

Then produce `missing`: the subset of these REQUIRED fields that the transcript does NOT settle well enough
to design against — one or more of: title, business_objective, target_users, user_stories, success_criteria,
data_entities, timeline_constraints. A field is missing if it is absent, vague, or lacks the numbers a
designer would need (e.g. success_criteria with no measurable target, or data_entities with no volumes).
If everything required is present and concrete, `missing` is [].

Return ONE JSON object and nothing else — no prose, no markdown fences — of shape:
{"extraction": {"title": "...", "business_objective": "...", "target_users": "...",
                "user_stories": ["...", "..."], "success_criteria": ["..."], "data_entities": ["..."],
                "timeline_constraints": "...", "integrations": ["..."], "nfr": ["..."], "out_of_scope": ["..."]},
 "missing": ["success_criteria", "data_entities"]}
"""

QUESTIONS_SYSTEM = """You are the Draft Agent of an automated POC builder. Some required requirement fields are
still missing after reading the transcript. Ask the user focused clarifying questions — one per missing field,
most important first, at most 5 total. Each question must be answerable in a sentence or two and must include
2 to 4 concrete example answers to make it easy to reply.

Return ONE JSON object and nothing else — no prose, no markdown fences — of shape:
{"questions": [{"field": "success_criteria",
                "question": "What measurable target defines success, and on what data volume?",
                "why_it_matters": "The success criteria drive the automated tests and the seed volume.",
                "suggestions": ["p95 latency < 300 ms with 5,000 products", "at least 5 results per page"]}]}
Only ask about fields in the provided `missing` list.
"""

ARTIFACTS_SYSTEM = """You are the Draft Agent of an automated POC builder. From the extracted requirements you
design the three artifacts the coding stage needs for a React/Vite + Node/Express + MongoDB POC:
a poc_spec front matter + narrative, a schema_design, and query_patterns.

Hard rules:
- user_stories: 3 to 7 stories. ids us-01, us-02, ... (two digits). Each has title, actor, acceptance (>=1),
  and testids named "us-NN-<kebab>" (lowercase, hyphens) that a frontend can implement (>=1 per story).
- success_criteria: ids sc-01, sc-02, ... Each has a statement and an `automatable` boolean.
  At least TWO success criteria MUST be automatable (measurable by an automated test).
- seed_requirements: {max_docs_per_collection (<= 10000), realism (a short phrase)}.
- assumptions: everything you filled in that the user did not state — list them plainly.
- schema_design.collections: each collection has name (lowercase snake_case), description, fields
  ([{name, type, required}]), indexes ([{keys, unique?}]), optional relationships, and
  seed {count (<= 10000), generator_hints}. Provide at least 2 collections with realistic volumes.
- query_patterns.patterns: ids qp-01, qp-02, ... Each maps to one or more EXISTING user_story_ids and has
  name, description, collections (>=1), pseudocode, and optional aggregation_sketch / expected_latency_ms_p95 /
  supporting_indexes. Provide one pattern per user story at minimum.
- sections: narrative markdown (a few sentences each) for every key in the example's `sections`.
- poc_definitions: a compact RAG summary object {poc_summary, poc_goal, poc_success_criteria, poc_domain,
  poc_key_entities, poc_user_stories}.
- NEVER put a MongoDB connection string, password or URI anywhere in the output.

Return ONE JSON object and nothing else — no prose, no markdown fences — of shape:
{"title": "...", "user_stories": [...], "success_criteria": [...], "seed_requirements": {...},
 "assumptions": [...], "schema_design": {"collections": [...]}, "query_patterns": {"patterns": [...]},
 "sections": {"executive_summary": "...", "business_objective": "...", "users_and_user_stories": "...",
              "functional_scope": "...", "high_level_architecture": "...", "data_model_summary": "...",
              "key_queries": "...", "api_surface": "...", "non_functional_requirements": "...",
              "success_criteria_notes": "...", "assumptions_open_questions": "...", "timeline_constraints": "..."},
 "poc_definitions": {"poc_summary": "...", "poc_goal": "...", "poc_success_criteria": "...",
                     "poc_domain": "...", "poc_key_entities": "...", "poc_user_stories": "..."}}
"""


def _golden_artifacts_example() -> str:
    return (
        "\n## Worked example — the golden Kirana Basket spec (a DIFFERENT POC). Match its structure, "
        "id conventions, testid style and level of detail; adapt everything to THIS POC:\n"
        "### poc_spec.md (front matter + first sections)\n" + GOLDEN_SPEC_MD +
        "\n### schema_design.json collections\n" + GOLDEN_SCHEMA_JSON +
        "\n### query_patterns.json\n" + GOLDEN_QP_JSON
    )


def _prior_qa_block(prior_rounds: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for rnd in prior_rounds or []:
        for q in rnd.get("questions", []):
            ans = q.get("answer")
            if ans:
                lines.append(f"Q ({q.get('field', '?')}): {q.get('question', '')}\nA: {ans}")
    return "\n".join(lines)


# =============================================================================
# Message builders
# =============================================================================

def build_analyze_messages(transcript: str, prior_rounds: list[dict[str, Any]] | None = None) -> list:
    parts = ["## Meeting transcript\n" + transcript]
    qa = _prior_qa_block(prior_rounds or [])
    if qa:
        parts.append("\n## Answers the user already gave to earlier clarifying questions "
                     "(treat these as authoritative and fold them into the extraction):\n" + qa)
    parts.append('\nReturn ONE JSON object {"extraction": {...}, "missing": [...]} only.')
    return [SystemMessage(content=ANALYZE_SYSTEM), HumanMessage(content="\n".join(parts))]


def build_questions_messages(extraction: dict[str, Any], missing: list[str], round_no: int) -> list:
    human = (
        "## What we extracted so far\n" + json.dumps(extraction, indent=2) +
        "\n## Missing required fields (ask ONLY about these)\n" + json.dumps(missing) +
        f"\n\nThis is clarification round {round_no}. Return ONE JSON object "
        '{"questions": [...]} with at most 5 questions.'
    )
    return [SystemMessage(content=QUESTIONS_SYSTEM), HumanMessage(content=human)]


def build_artifacts_messages(extraction: dict[str, Any], errors: list[str] | None = None) -> list:
    parts = [
        "Design the spec artifacts for this POC from the extracted requirements.",
        "\n## Extracted requirements\n" + json.dumps(extraction, indent=2),
        _golden_artifacts_example(),
        '\nReturn ONE JSON object (see the system message shape) only.',
    ]
    if errors:
        parts.append("\n## Your previous output was rejected — fix exactly these problems:\n- "
                     + "\n- ".join(errors))
    return [SystemMessage(content=ARTIFACTS_SYSTEM), HumanMessage(content="\n".join(parts))]


# =============================================================================
# Output parsing / usage
# =============================================================================

def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*\n", "", t)
        t = re.sub(r"\n```\s*$", "", t)
    return t.strip()


def parse_json_object(text: str) -> dict[str, Any]:
    obj = json.loads(_strip_fences(text))
    if not isinstance(obj, dict):
        raise ValueError("LLM output must be a single JSON object")
    return obj


def _usage(resp: Any) -> dict[str, int]:
    m = getattr(resp, "usage_metadata", None) or {}
    return {"input_tokens": int(m.get("input_tokens", 0)), "output_tokens": int(m.get("output_tokens", 0))}


def _accumulate(usage: dict[str, int], resp: Any) -> dict[str, int]:
    u = _usage(resp)
    return {k: usage.get(k, 0) + u[k] for k in ("input_tokens", "output_tokens")}


def _content(resp: Any) -> str:
    return resp.content if isinstance(resp.content, str) else str(resp.content)


# =============================================================================
# Stage 1 — analyze
# =============================================================================

def analyze(transcript: str, prior_rounds: list[dict[str, Any]] | None = None,
            llm: Any = None) -> tuple[dict[str, Any], dict[str, int]]:
    """Extract requirements and the `missing` list. Returns ({"extraction", "missing"}, usage). Retries once."""
    if llm is None:
        from .llm import build_llm
        llm = build_llm(temperature=0)
    messages = build_analyze_messages(transcript, prior_rounds)
    usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    last_error = ""
    for _ in range(2):
        resp = llm.invoke(messages)
        usage = _accumulate(usage, resp)
        try:
            obj = parse_json_object(_content(resp))
            extraction = obj.get("extraction")
            if not isinstance(extraction, dict):
                raise ValueError('missing "extraction" object')
            missing = obj.get("missing", [])
            if not isinstance(missing, list):
                raise ValueError('"missing" must be an array')
            # Only report known required fields as missing.
            missing = [m for m in missing if m in REQUIRED_FIELDS]
            return {"extraction": extraction, "missing": missing}, usage
        except (ValueError, json.JSONDecodeError) as e:
            last_error = str(e)
            messages = messages + [HumanMessage(content=f"Your previous output was invalid: {last_error}. "
                                                'Return ONE JSON object {"extraction": {...}, "missing": [...]} only.')]
    raise LLMOutputInvalid([f"analyze produced no valid JSON: {last_error}"])


# =============================================================================
# Stage 2 — questions
# =============================================================================

_FALLBACK_QUESTIONS = {
    "title": ("What should we call this POC?", "The title anchors the spec and the app.",
              ["Live product recommendations", "Fleet telemetry dashboard"]),
    "business_objective": ("What outcome must the POC prove?", "It frames every design decision.",
                           ["Lift basket size", "Cut a slow batch job to real time"]),
    "target_users": ("Who are the users and on what device?", "Drives the UI and journeys.",
                     ["Shoppers on mobile web", "Ops managers on desktop"]),
    "user_stories": ("What are the 3-5 key things a user should be able to do?",
                     "User stories become the frontend routes and tests.",
                     ["See recommendations on a product page", "Browse by category", "View a dashboard"]),
    "success_criteria": ("What measurable target defines success, and on what data volume?",
                         "Success criteria drive the automated tests and the seed volume.",
                         ["p95 latency < 300 ms with 5,000 records", "at least 5 results per page"]),
    "data_entities": ("What are the main data entities, their key fields, and rough volumes?",
                      "The schema and seeding are designed from the entities and volumes.",
                      ["5,000 products, 8,000 orders", "location pings, delivery status"]),
    "timeline_constraints": ("What is the deadline and are there platform/region constraints?",
                             "Shapes scope and the deployment target.",
                             ["Two-week demo", "AWS Mumbai, real web app"]),
}


def generate_questions(extraction: dict[str, Any], missing: list[str], round_no: int,
                       llm: Any = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Produce <=5 clarifying questions covering the missing fields. IDs are q<round>-<n>.

    Guarantees one question per missing field (LLM text preferred, deterministic fallback otherwise),
    so success_criteria / data_entities coverage is never lost to a bad LLM response.
    """
    if llm is None:
        from .llm import build_llm
        llm = build_llm(temperature=0)
    want = [m for m in missing if m in REQUIRED_FIELDS][:5]
    if not want:
        return [], {"input_tokens": 0, "output_tokens": 0}

    llm_by_field: dict[str, dict[str, Any]] = {}
    usage = {"input_tokens": 0, "output_tokens": 0}
    try:
        resp = llm.invoke(build_questions_messages(extraction, want, round_no))
        usage = _accumulate(usage, resp)
        obj = parse_json_object(_content(resp))
        for q in obj.get("questions", []):
            f = q.get("field")
            if f in want and f not in llm_by_field:
                llm_by_field[f] = q
    except (ValueError, json.JSONDecodeError, AttributeError):
        pass  # fall back to templates below

    questions: list[dict[str, Any]] = []
    for i, field in enumerate(want, start=1):
        q = llm_by_field.get(field)
        if q and q.get("question"):
            question_text = str(q["question"])
            why = str(q.get("why_it_matters") or _FALLBACK_QUESTIONS.get(field, ("", "", []))[1])
            suggestions = q.get("suggestions") or list(_FALLBACK_QUESTIONS.get(field, ("", "", []))[2])
        else:
            question_text, why, suggestions = _FALLBACK_QUESTIONS.get(
                field, (f"Please clarify {field}.", "Needed to design the POC.", []))
        questions.append({
            "question_id": f"q{round_no}-{i}",
            "field": field,
            "question": question_text,
            "why_it_matters": why,
            "suggestions": list(suggestions)[:4],
        })
    return questions, usage


# =============================================================================
# Stage 3 — artifacts
# =============================================================================

def _story_ids(stories: list[dict[str, Any]]) -> set[str]:
    return {s.get("id") for s in stories if isinstance(s, dict)}


def validate_spec_semantics(front_matter: dict[str, Any], query_patterns: dict[str, Any]) -> list[str]:
    """Checks the acceptance rules the JSON schema does not encode (§6.2 / draft acceptance)."""
    errors: list[str] = []
    stories = front_matter.get("user_stories", [])
    n = len(stories)
    if not (3 <= n <= 7):
        errors.append(f"user_stories must number 3-7, got {n}")
    for s in stories:
        if not s.get("testids"):
            errors.append(f"user story {s.get('id')} has no testids")
    automatable = sum(1 for c in front_matter.get("success_criteria", []) if c.get("automatable"))
    if automatable < 2:
        errors.append(f"need >= 2 automatable success criteria, got {automatable}")
    ids = _story_ids(stories)
    for p in query_patterns.get("patterns", []):
        for usid in p.get("user_story_ids", []):
            if usid not in ids:
                errors.append(f"query pattern {p.get('id')} references unknown user story {usid}")
    return errors


def assemble(poc_id: str, spec_version: str, obj: dict[str, Any]) -> dict[str, Any]:
    """Turn the LLM's structured object into the three artifacts + front matter, filling deterministic fields.

    Returns {"front_matter", "schema_design", "query_patterns", "spec_md"}; raises ContractError / ValueError
    if a contract or a semantic rule fails, so generate() can retry once with the errors.
    """
    front_matter = {
        "poc_id": poc_id,
        "spec_version": spec_version,
        "title": str(obj.get("title") or "Untitled POC"),
        "stack": dict(STACK),
        "user_stories": obj.get("user_stories", []),
        "success_criteria": obj.get("success_criteria", []),
        "seed_requirements": _clean_seed_requirements(obj.get("seed_requirements", {})),
        "assumptions": [str(a) for a in obj.get("assumptions", []) if a],
        "approved": False,
    }
    validate("poc_spec_frontmatter", front_matter)

    schema_design = dict(obj.get("schema_design") or {})
    schema_design["database_name"] = poc_id
    schema_design["seed_requirements"] = {
        "max_docs_per_collection": front_matter["seed_requirements"]["max_docs_per_collection"],
        "deterministic_seed": 42,
    }
    cap = schema_design["seed_requirements"]["max_docs_per_collection"]
    for col in schema_design.get("collections", []):
        seed = col.get("seed")
        if isinstance(seed, dict) and isinstance(seed.get("count"), int):
            seed["count"] = min(seed["count"], cap)
    validate("schema_design", schema_design)

    query_patterns = dict(obj.get("query_patterns") or {})
    validate("query_patterns", query_patterns)

    sem = validate_spec_semantics(front_matter, query_patterns)
    if sem:
        raise ValueError("; ".join(sem))

    spec_md = render_spec_md(front_matter, obj.get("sections", {}))
    return {"front_matter": front_matter, "schema_design": schema_design,
            "query_patterns": query_patterns, "spec_md": spec_md}


def _clean_seed_requirements(sr: dict[str, Any]) -> dict[str, Any]:
    max_docs = sr.get("max_docs_per_collection", 10000)
    try:
        max_docs = int(max_docs)
    except (TypeError, ValueError):
        max_docs = 10000
    max_docs = max(1, min(max_docs, 10000))
    out = {"max_docs_per_collection": max_docs}
    realism = sr.get("realism")
    if realism:
        out["realism"] = str(realism)
    return out


def render_spec_md(front_matter: dict[str, Any], sections: dict[str, Any]) -> str:
    fm_yaml = yaml.safe_dump(front_matter, sort_keys=False, allow_unicode=True, width=1000)
    body = []
    for heading, key in SPEC_SECTIONS:
        text = str(sections.get(key) or "").strip() or "_To be refined with the user._"
        body.append(f"# {heading}\n{text}")
    return f"---\n{fm_yaml}---\n\n" + "\n\n".join(body) + "\n"


def _guardrail_scan(spec_md: str, schema_json: str, qp_json: str) -> None:
    """A connection string must never reach the spec (§6.2 / §10.4.5)."""
    blob = spec_md + schema_json + qp_json
    if re.search(r"mongodb(\+srv)?://", blob):
        raise ValueError("a MongoDB connection string leaked into the spec artifacts")


def generate(extraction: dict[str, Any], poc_id: str, spec_version: str,
             llm: Any = None) -> tuple[dict[str, Any], dict[str, int]]:
    """Generate the three artifacts. Returns (result, usage) where result has keys
    files{poc_spec.md, schema_design.json, query_patterns.json}, assumptions, user_story_count, poc_definitions.
    Validates all three contracts; retries once with the validator errors."""
    if llm is None:
        from .llm import build_llm
        llm = build_llm(temperature=0)
    usage = {"input_tokens": 0, "output_tokens": 0}
    errors: list[str] | None = None
    last_errors: list[str] = []
    for _ in range(2):
        resp = llm.invoke(build_artifacts_messages(extraction, errors))
        usage = _accumulate(usage, resp)
        try:
            obj = parse_json_object(_content(resp))
            built = assemble(poc_id, spec_version, obj)
            schema_json = json.dumps(built["schema_design"], indent=2)
            qp_json = json.dumps(built["query_patterns"], indent=2)
            _guardrail_scan(built["spec_md"], schema_json, qp_json)
            files = {
                "poc_spec.md": built["spec_md"],
                "schema_design.json": schema_json,
                "query_patterns.json": qp_json,
            }
            poc_defs = obj.get("poc_definitions") or _derive_poc_definitions(built["front_matter"])
            return {
                "files": files,
                "assumptions": built["front_matter"]["assumptions"],
                "user_story_count": len(built["front_matter"]["user_stories"]),
                "poc_definitions": poc_defs,
                "front_matter": built["front_matter"],
            }, usage
        except ContractError as e:
            last_errors = [f"{p or '$'}: {m}" for p, m in e.errors]
        except (ValueError, json.JSONDecodeError) as e:
            last_errors = [str(e)]
        errors = last_errors
    raise LLMOutputInvalid(last_errors)


def _derive_poc_definitions(fm: dict[str, Any]) -> dict[str, Any]:
    stories = "; ".join(f"{s['id']} {s.get('title', '')}" for s in fm.get("user_stories", []))
    sc = "; ".join(c.get("statement", "") for c in fm.get("success_criteria", []))
    return {
        "poc_summary": fm.get("title", ""),
        "poc_goal": fm.get("title", ""),
        "poc_success_criteria": sc,
        "poc_domain": "",
        "poc_key_entities": "",
        "poc_user_stories": stories,
    }


# =============================================================================
# RAG chunking
# =============================================================================

def chunk_spec_by_section(spec_md: str) -> list[dict[str, str]]:
    """Split poc_spec.md into {section, text} chunks by top-level `# ` headings (front matter dropped)."""
    parts = spec_md.split("---", 2)
    body = parts[2] if len(parts) >= 3 else spec_md
    chunks: list[dict[str, str]] = []
    section = "preamble"
    buf: list[str] = []
    for line in body.splitlines():
        if line.startswith("# "):
            if buf and "".join(buf).strip():
                chunks.append({"section": section, "text": "\n".join(buf).strip()})
            section = line[2:].strip()
            buf = []
        else:
            buf.append(line)
    if buf and "".join(buf).strip():
        chunks.append({"section": section, "text": "\n".join(buf).strip()})
    return chunks
