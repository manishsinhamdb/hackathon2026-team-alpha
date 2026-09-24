"""Test Agent pipeline — pure functions for each test step (Spec §6.8).
No LLM calls; no cloud calls (those happen in main.py tools). Each function takes
plain Python values and returns plain Python values; the tool wrappers serialize to JSON."""
from __future__ import annotations

import json
import re
import time
import urllib.request
import urllib.error
from typing import Any


# ---------------------------------------------------------------------------
# Contract parsing
# ---------------------------------------------------------------------------

def parse_operations(contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract [{operationId, method, path, params, x_user_story_ids}] from an OpenAPI 3.1 contract."""
    ops = []
    for path, methods in contract.get("paths", {}).items():
        for method, op_def in methods.items():
            if not isinstance(op_def, dict):
                continue
            if method.upper() not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                continue
            params = []
            for p in op_def.get("parameters", []):
                params.append({"name": p["name"], "in": p["in"], "required": p.get("required", False)})
            ops.append({
                "operationId": op_def.get("operationId", f"{method.upper()}_{path}"),
                "method": method.upper(),
                "path": path,
                "params": params,
                "x_user_story_ids": op_def.get("x-user-story-ids", []),
            })
    return ops


# ---------------------------------------------------------------------------
# Plan generation
# ---------------------------------------------------------------------------

def generate_plan(
    user_stories: list[dict[str, Any]],
    success_criteria: list[dict[str, Any]],
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build test_plan.json from spec user stories, success criteria, and contract operations."""
    api_smoke = [
        {
            "id": f"api-{op['operationId']}",
            "operationId": op["operationId"],
            "method": op["method"],
            "path": op["path"],
            "params": op.get("params", []),
            "expect": [200],
        }
        for op in operations
    ]

    journeys = [
        {"id": us["id"], "title": us.get("title", us["id"]), "testids": us.get("testids", [])}
        for us in user_stories
    ]

    criteria = []
    for sc in success_criteria:
        stmt = sc["statement"]
        automatable = sc.get("automatable", False)
        if not automatable:
            criteria.append({
                "id": sc["id"], "statement": stmt, "automatable": False,
                "mapped_operation_id": None, "p95_target_ms": None,
            })
            continue
        # p95 criterion: statement must contain "p95" AND "<N> ms"
        p95_match = re.search(r"(\d[\d\s]*)\s*ms", stmt) if "p95" in stmt.lower() else None
        if p95_match:
            raw = p95_match.group(1).replace(" ", "")
            target_ms = int(raw)
            stmt_lower = stmt.lower()
            # Strip curly braces from statement for path matching
            stmt_stripped = stmt_lower.replace("{", "").replace("}", "")
            mapped_op = None
            # Sort by path length descending so longer/more-specific paths match before shorter ones
            for op in sorted(operations, key=lambda o: len(o["path"]), reverse=True):
                op_path = op["path"].lower().replace("{", "").replace("}", "")
                if op["operationId"].lower() in stmt_stripped or op_path in stmt_stripped:
                    mapped_op = op["operationId"]
                    break
            criteria.append({
                "id": sc["id"], "statement": stmt, "automatable": True,
                "mapped_operation_id": mapped_op, "p95_target_ms": target_ms,
            })
        else:
            criteria.append({
                "id": sc["id"], "statement": stmt, "automatable": True,
                "mapped_operation_id": None, "p95_target_ms": None,
            })

    return {"api_smoke": api_smoke, "journeys": journeys, "criteria": criteria}


# ---------------------------------------------------------------------------
# HTTP helpers (used by api smoke tests — runs in Tool Pod)
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: int = 10) -> tuple[int, Any]:
    """Returns (http_status, body_as_python_obj). Body parsed as JSON if possible."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="ignore")
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8", errors="ignore")
            body = json.loads(raw)
        except Exception:
            body = {}
        return e.code, body
    except Exception as e:
        return 0, {"error": str(e)}


def _pluralize(name: str) -> str:
    return name if name.endswith("s") else name + "s"


def resolve_path_param(
    api_url: str,
    param_name: str,
    operations: list[dict[str, Any]],
    attempts: list[dict[str, Any]] | None = None,
) -> str | None:
    """Find a concrete value for `param_name` by calling a GET list endpoint that returns items containing it.
    Searches each response's JSON recursively for the first list of dicts that has `param_name` as a key
    (so `{"items":[{"sku":...}]}` resolves). Also probes the same-name pluralised endpoint as a fallback.
    Each probe is appended to `attempts` (url, http_status, found) so callers can surface why resolution failed."""
    if attempts is None:
        attempts = []
    # GET endpoints with no required path params (list endpoints), most specific path first.
    candidate_urls: list[str] = []
    for op in sorted(operations, key=lambda o: len(o["path"]), reverse=True):
        if op["method"] != "GET":
            continue
        if any(p["in"] == "path" and p.get("required") for p in op.get("params", [])):
            continue
        u = api_url.rstrip("/") + op["path"]
        if u not in candidate_urls:
            candidate_urls.append(u)
    # Pluralised same-name endpoint fallback (e.g. productId -> /products), even if not in the contract.
    for extra in (_pluralize(param_name), param_name):
        u = api_url.rstrip("/") + "/" + extra
        if u not in candidate_urls:
            candidate_urls.append(u)

    for url in candidate_urls:
        try:
            status, body = _http_get(url)
        except Exception as e:
            attempts.append({"url": url, "error": str(e)[:200], "found": False})
            continue
        val = _find_first_in_list(body, param_name) if status == 200 else None
        attempts.append({"url": url, "http_status": status, "found": val is not None})
        if val is not None:
            return str(val)
    return None


def _find_first_in_list(obj: Any, key: str) -> Any:
    """Recursively search obj for the first list of dicts that contains `key`; return the value."""
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict) and key in item:
                return item[key]
        for item in obj:
            v = _find_first_in_list(item, key)
            if v is not None:
                return v
    elif isinstance(obj, dict):
        for v in obj.values():
            found = _find_first_in_list(v, key)
            if found is not None:
                return found
    return None


# ---------------------------------------------------------------------------
# API smoke runner
# ---------------------------------------------------------------------------

def run_api_smoke_tests(
    api_url: str,
    health_url: str,
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    """Execute all api_smoke entries plus health check and p95 criteria. Returns results list."""
    results: list[dict[str, Any]] = []
    operations = {e["operationId"]: e for e in plan["api_smoke"]}

    # Health check first
    t0 = time.perf_counter()
    status, body = _http_get(health_url)
    dur = int((time.perf_counter() - t0) * 1000)
    health_ok = status == 200 and isinstance(body, dict) and body.get("db") == "connected"
    results.append({
        "id": "api-getHealth",
        "kind": "api_smoke",
        "status": "passed" if health_ok else "failed",
        "duration_ms": dur,
        **({"suspected_component": "backend", "evidence": {"http_status": status, "error": str(body)}} if not health_ok else {}),
    })

    # Resolve path params cache
    param_cache: dict[str, str | None] = {}
    param_attempts: dict[str, list[dict[str, Any]]] = {}
    _all_ops = list(operations.values())

    def _resolve(pname: str) -> str | None:
        if pname not in param_cache:
            attempts: list[dict[str, Any]] = []
            param_cache[pname] = resolve_path_param(api_url, pname, _all_ops, attempts)
            param_attempts[pname] = attempts
        return param_cache[pname]

    for entry in plan["api_smoke"]:
        if entry["operationId"] == "getHealth":
            continue  # already done above

        path = entry["path"]
        path_params = [p for p in entry.get("params", []) if p.get("in") == "path"]

        # Build URL by resolving path params
        resolved = True
        unresolved_param = None
        for pp in path_params:
            pname = pp["name"]
            val = _resolve(pname)
            if val is None:
                resolved = False
                unresolved_param = pname
                break
            path = path.replace("{" + pname + "}", val)

        if not resolved:
            # Couldn't seed a concrete value — surface the resolver's probes so the failure is diagnosable.
            results.append({
                "id": entry["id"], "kind": "api_smoke", "status": "skipped",
                "evidence": {
                    "error": f"could not resolve path param {{{unresolved_param}}} from any list endpoint",
                    "resolver_attempts": param_attempts.get(unresolved_param, []),
                },
            })
            continue

        url = api_url.rstrip("/") + path
        t0 = time.perf_counter()
        http_status, resp_body = _http_get(url)
        dur = int((time.perf_counter() - t0) * 1000)
        ok = http_status in entry["expect"] or http_status in (200, 201, 204)
        r: dict[str, Any] = {"id": entry["id"], "kind": "api_smoke", "status": "passed" if ok else "failed", "duration_ms": dur}
        if not ok:
            r["suspected_component"] = "backend"
            r["evidence"] = {"http_status": http_status, "error": str(resp_body)[:500]}
        results.append(r)

    # p95 latency tests for mapped criteria
    p95_results: dict[str, dict[str, Any]] = {}
    for criterion in plan.get("criteria", []):
        op_id = criterion.get("mapped_operation_id")
        target = criterion.get("p95_target_ms")
        if not op_id or not target:
            continue
        if op_id in p95_results:
            continue
        entry = operations.get(op_id)
        if not entry:
            continue
        path = entry["path"]
        path_params = [p for p in entry.get("params", []) if p.get("in") == "path"]
        resolved = True
        for pp in path_params:
            pname = pp["name"]
            val = _resolve(pname)
            if val is None:
                resolved = False
                break
            path = path.replace("{" + pname + "}", val)
        if not resolved:
            p95_results[op_id] = {"p95_ms": None, "skipped": True}
            continue
        url = api_url.rstrip("/") + path
        durations = []
        for _ in range(20):
            t0 = time.perf_counter()
            _http_get(url)
            durations.append((time.perf_counter() - t0) * 1000)
        durations.sort()
        p95_ms = int(durations[int(len(durations) * 0.95)])
        ok = p95_ms <= target
        p95_results[op_id] = {
            "criterion_id": criterion["id"],
            "p95_ms": p95_ms,
            "target_ms": target,
            "status": "passed" if ok else "failed",
        }

    # Attach p95 results to the matching smoke entry
    for r in results:
        op_id = next((e["operationId"] for e in plan["api_smoke"] if e["id"] == r["id"]), None)
        if op_id and op_id in p95_results and not p95_results[op_id].get("skipped"):
            r["measured"] = {"p95_ms": p95_results[op_id]["p95_ms"]}

    return results


# ---------------------------------------------------------------------------
# Playwright spec generator
# ---------------------------------------------------------------------------

ARTIFACTS_DIR = "/opt/poc-test/artifacts"


def generate_js_spec(plan: dict[str, Any], base_url: str) -> str:
    """Generate a single Playwright spec file — one test per journey.

    Generic traversal driven only by the spec's testids: load the SPA root, then walk it the way
    the golden frontend is wired — the top nav is `[data-testid^="nav-"]` links, and a detail page is
    reached by clicking the first `<a>` inside an element whose data-testid ends with `-item`
    (e.g. /categories -> a `us-02-product-item` link -> /products/:sku). Data loads via fetch, so we
    wait for network idle and for at least one testid after every navigation before collecting.
    Each journey then asserts every one of its declared testids was seen somewhere in the walk."""
    lines = [
        "// AUTO-GENERATED by test-agent — do not edit",
        "const { test, expect } = require('@playwright/test');",
        f"const BASE_URL = process.env.BASE_URL || {json.dumps(base_url)};",
        "",
        "async function settle(page) {",
        "  await page.waitForLoadState('networkidle', {timeout: 15000}).catch(() => {});",
        "  await page.waitForSelector('[data-testid]', {timeout: 15000}).catch(() => {});",
        "}",
        "async function collect(page, seen) {",
        "  const ids = await page.$$eval('[data-testid]', els => els.map(e => e.getAttribute('data-testid')));",
        "  ids.forEach(t => seen.add(t));",
        "}",
        "async function visitFirstDetail(page, seen) {",
        "  const detail = await page.$('[data-testid$=\"-item\"] a');",
        "  if (!detail) return;",
        "  try { await detail.click(); } catch (e) { return; }",
        "  await settle(page);",
        "  await collect(page, seen);",
        "  await page.goBack().catch(() => {});",
        "  await settle(page);",
        "}",
        "async function traverse(page, seen) {",
        "  await page.goto(BASE_URL);",
        "  await settle(page);",
        "  await collect(page, seen);",
        "  await visitFirstDetail(page, seen);",  # landing page (/categories) -> product page
        "  const navCount = (await page.$$('[data-testid^=\"nav-\"]')).length;",
        "  for (let i = 0; i < navCount; i++) {",
        "    const links = await page.$$('[data-testid^=\"nav-\"]');",
        "    if (i >= links.length) break;",
        "    try { await links[i].click(); } catch (e) { continue; }",
        "    await settle(page);",
        "    await collect(page, seen);",
        "    await visitFirstDetail(page, seen);",
        "  }",
        "}",
        "",
    ]

    for journey in plan.get("journeys", []):
        jid = journey["id"]
        title = journey.get("title", jid)
        testids_json = json.dumps(journey.get("testids", []))

        lines += [
            f"test({json.dumps(title + ' (' + jid + ')')}, async ({{ page }}) => {{",
            "  const seen = new Set();",
            "  const consoleErrors = [];",
            "  const failedRequests = [];",
            "  page.on('console', msg => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });",
            "  page.on('response', resp => { if (resp.status() >= 400) failedRequests.push({url: resp.url(), status: resp.status()}); });",
            "",
            "  await traverse(page, seen);",
            "",
            f"  const expected = {testids_json};",
            "  const missing = expected.filter(tid => !seen.has(tid));",
            "  if (missing.length > 0) {",
            f"    await page.screenshot({{path: {json.dumps(ARTIFACTS_DIR + '/' + jid + '.png')}, fullPage: true}}).catch(() => {{}});",
            "    const payload = {missing, seen: [...seen], url: page.url(), consoleErrors, failedRequests};",
            "    throw new Error('MISSING_TESTIDS ' + JSON.stringify(payload));",
            "  }",
            "});",
            "",
        ]

    return "\n".join(lines)


def generate_playwright_config() -> str:
    return """\
const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: '.',
  timeout: 90000,
  reporter: [['json', { outputFile: '/opt/poc-test/report.json' }]],
  use: { video: 'off', trace: 'off', screenshot: 'only-on-failure' },
});
"""


# ---------------------------------------------------------------------------
# Browser test runner (SSM)
# ---------------------------------------------------------------------------

def run_browser_tests(
    poc_id: str,
    run_id: str,
    ctx: dict[str, Any],
    plan: dict[str, Any],
    scope: Any,
) -> list[dict[str, Any]]:
    """Run Playwright journeys on the POC's EC2 instance via SSM. Returns browser results list."""
    from poc_infra_tools import ssm
    from poc_shared_tools.config import get_config

    instance_id = ctx["instance_id"]
    base_url = ctx["base_url"]
    bucket = get_config().s3_bucket

    # Filter journeys by scope
    journeys = plan.get("journeys", [])
    if scope == "smoke":
        journeys = []
    elif isinstance(scope, list):
        journeys = [j for j in journeys if j["id"] in scope]

    if not journeys:
        return []

    js_spec = generate_js_spec({"journeys": journeys}, base_url)
    pw_config = generate_playwright_config()
    pkg_json = json.dumps({"private": True, "devDependencies": {"@playwright/test": "1.47.2"}}, indent=2)

    report_key = f"pocs/{poc_id}/test/{run_id}/artifacts/playwright-report.json"

    # Chromium system libraries for Amazon Linux 2023 (dnf). `playwright install --with-deps` only
    # knows apt-get, so on AL2023 it fails ("apt-get: command not found"); we install these explicitly
    # and then download just the browser binary with a bare `playwright install chromium`.
    al2023_deps = (
        "nss nspr atk at-spi2-atk at-spi2-core cups-libs libdrm libXcomposite libXdamage "
        "libXext libXfixes libXrandr libgbm mesa-libgbm libxcb libxkbcommon pango cairo "
        "alsa-lib gtk3 dbus-libs"
    )

    all_cmds = [
        # Playwright's browser cache: pin it under the workdir so it does not depend on $HOME (empty under SSM).
        "export PLAYWRIGHT_BROWSERS_PATH=/opt/poc-test/ms-playwright",
        "mkdir -p /opt/poc-test/artifacts && cd /opt/poc-test",
        "cat > /opt/poc-test/package.json <<'PKGJSON'\n" + pkg_json + "\nPKGJSON",
        "cat > /opt/poc-test/journeys.spec.js <<'SPECEOF'\n" + js_spec + "\nSPECEOF",
        "cat > /opt/poc-test/playwright.config.js <<'CFGEOF'\n" + pw_config + "\nCFGEOF",
        # npm deps (idempotent; log kept off the capped stdout).
        "if [ ! -d node_modules/@playwright ]; then npm install >/opt/poc-test/npm.log 2>&1 "
        "&& echo NPM_INSTALLED || { echo NPM_FAILED; tail -15 /opt/poc-test/npm.log; }; else echo NPM_CACHED; fi",
        # Chromium system libraries (dnf, idempotent).
        f"dnf install -y {al2023_deps} >/opt/poc-test/dnf.log 2>&1 && echo DEPS_OK "
        "|| { echo DEPS_PARTIAL; grep -iE 'no match|error' /opt/poc-test/dnf.log | head -5; }",
        # Chromium browser binary (bare install; cached under PLAYWRIGHT_BROWSERS_PATH).
        "if ls /opt/poc-test/ms-playwright/chromium-*/chrome-linux/chrome >/dev/null 2>&1; then echo CHROMIUM_CACHED; "
        "else npx playwright install chromium >/opt/poc-test/pw.log 2>&1 "
        "&& echo CHROMIUM_INSTALLED || { echo CHROMIUM_FAILED; tail -20 /opt/poc-test/pw.log; }; fi",
        # Run the journeys against the deployed public URL (hostname, never IP literal).
        "echo '--- RUN ---'",
        f"BASE_URL={base_url} npx playwright test journeys.spec.js "
        "--config=/opt/poc-test/playwright.config.js 2>&1 | tail -30 || true",
        # Upload the report + any failure screenshots for the FailureReport (instance role now allows test/*).
        f"aws s3 cp /opt/poc-test/report.json s3://{bucket}/{report_key} --region ap-south-1 "
        "&& echo REPORT_UPLOADED || echo REPORT_UPLOAD_FAILED",
        f"aws s3 cp /opt/poc-test/artifacts s3://{bucket}/pocs/{poc_id}/test/{run_id}/artifacts/ "
        "--recursive --region ap-south-1 2>/dev/null || true",
        "echo REPORT_EXISTS=$(test -f /opt/poc-test/report.json && echo yes || echo no)",
    ]

    # First run installs npm + chromium (3-5 min); subsequent runs are cached. run_script polls to
    # timeout_s + 120s, comfortably inside the agent's 15-minute budget.
    r = ssm.run_script(
        instance_id, poc_id, run_id, "playwright_run",
        all_cmds,
        env={},
        timeout_s=600,
        workdir="/opt/poc-test",
    )

    pw_report = _fetch_pw_report(instance_id, poc_id, run_id, report_key)
    return _map_playwright_results(pw_report, journeys, poc_id, run_id)


def _fetch_pw_report(instance_id: str, poc_id: str, run_id: str, report_key: str) -> dict[str, Any]:
    """Read the Playwright JSON report. Primary source is S3 (uploaded by the instance role); if that
    is missing we re-fetch it over a clean SSM stdout (no install noise to blow the 24 000-char cap)."""
    from poc_shared_tools import s3 as s3t
    try:
        return json.loads(s3t.get_text(report_key))
    except Exception:
        pass
    # Fallback: cat the report on its own so install logs don't crowd it out of the capped stdout.
    try:
        from poc_infra_tools import ssm
        r = ssm.run_script(
            instance_id, poc_id, run_id, "playwright_report",
            ["echo '=====REPORT====='", "cat /opt/poc-test/report.json 2>/dev/null || echo '{}'"],
            timeout_s=120,
        )
        stdout = r.get("stdout", "")
        if "=====REPORT=====" in stdout:
            after = stdout.split("=====REPORT=====", 1)[1].strip()
            return json.loads(after)
    except Exception:
        pass
    return {}


def _map_playwright_results(
    pw_report: dict[str, Any],
    journeys: list[dict[str, Any]],
    poc_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    """Map Playwright JSON report to our browser_results format."""
    # Build lookup: test title -> suite result
    test_results: dict[str, dict[str, Any]] = {}
    for suite in pw_report.get("suites", []):
        for spec in suite.get("specs", []):
            title = spec.get("title", "")
            tests = spec.get("tests", [])
            if tests:
                t = tests[0]
                test_results[title] = {
                    "ok": spec.get("ok", False),
                    "duration": t.get("results", [{}])[0].get("duration", 0),
                    "errors": [e.get("message", "") for e in t.get("results", [{}])[0].get("errors", [])],
                    "attachments": t.get("results", [{}])[0].get("attachments", []),
                }

    results = []
    for journey in journeys:
        jid = journey["id"]
        title = journey.get("title", jid) + f" ({jid})"
        tr = test_results.get(title, {})
        passed = tr.get("ok", False) if tr else False
        duration_ms = int(tr.get("duration", 0))
        errors = tr.get("errors", []) if tr else []

        result: dict[str, Any] = {
            "id": jid,
            "kind": "user_story",
            "status": "passed" if passed else "failed",
            "duration_ms": duration_ms,
        }

        if not passed:
            raw_error = errors[0] if errors else "test failed"
            payload = _parse_missing_payload(raw_error)
            failed_reqs = payload.get("failedRequests", []) if payload else []
            console_errors = payload.get("consoleErrors", []) if payload else []
            # A 5xx seen during the journey points at the backend, otherwise it is the frontend.
            suspected = "backend" if any(fr.get("status", 0) >= 500 for fr in failed_reqs) else "frontend"
            evidence: dict[str, Any] = {"error": raw_error[:500]}
            if payload and payload.get("missing"):
                evidence["error"] = "missing testids: " + ", ".join(payload["missing"])
            if console_errors:
                evidence["console_errors"] = [str(c)[:300] for c in console_errors]
            evidence["failed_requests"] = failed_reqs
            result["suspected_component"] = suspected
            result["evidence"] = evidence
            # Screenshot artifact (only produced on failure; uploaded to S3 alongside the report).
            result["artifacts"] = {"screenshot": f"pocs/{poc_id}/test/{run_id}/artifacts/{jid}.png"}

        results.append(result)

    return results


def _parse_missing_payload(error_message: str) -> dict[str, Any]:
    """Extract the JSON diagnostics the spec throws as `MISSING_TESTIDS {...}` (single line, no newlines)."""
    marker = "MISSING_TESTIDS "
    if marker not in error_message:
        return {}
    after = error_message.split(marker, 1)[1].splitlines()[0].strip()
    try:
        obj = json.loads(after)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def assemble_report(
    poc_id: str,
    run_id: str,
    deployment_run_id: str,
    ctx: dict[str, Any],
    api_results: list[dict[str, Any]],
    browser_results: list[dict[str, Any]],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Build test_report.json (§8.8) from api_results + browser_results + criteria evaluation."""
    all_results = list(api_results) + list(browser_results)

    # Criteria evaluation
    p95_by_op: dict[str, int | None] = {}
    for r in api_results:
        op_id = next((e["operationId"] for e in plan["api_smoke"] if e["id"] == r["id"]), None)
        if op_id and r.get("measured", {}).get("p95_ms") is not None:
            p95_by_op[op_id] = r["measured"]["p95_ms"]

    sc_results = []
    for criterion in plan.get("criteria", []):
        cid = criterion["id"]
        if not criterion.get("automatable"):
            sc_results.append({"id": cid, "status": "not_automatable"})
            continue
        op_id = criterion.get("mapped_operation_id")
        target = criterion.get("p95_target_ms")
        if op_id and target and op_id in p95_by_op and p95_by_op[op_id] is not None:
            p95_ms = p95_by_op[op_id]
            status = "passed" if p95_ms <= target else "failed"
            sc_results.append({"id": cid, "status": status, "measured": {"p95_ms": p95_ms}})
        else:
            # automatable but unmapped (no p95) → not_automatable
            sc_results.append({"id": cid, "status": "not_automatable"})

    # Summary counts
    passed = sum(1 for r in all_results if r["status"] == "passed")
    failed = sum(1 for r in all_results if r["status"] == "failed")
    skipped = sum(1 for r in all_results if r["status"] == "skipped")
    not_auto = sum(1 for sc in sc_results if sc["status"] == "not_automatable")
    total = len(all_results)
    duration_ms = sum(r.get("duration_ms", 0) for r in all_results)

    # Strip non-schema fields from results before returning
    clean_results = []
    for r in all_results:
        cr: dict[str, Any] = {"id": r["id"], "kind": r["kind"], "status": r["status"]}
        if "duration_ms" in r:
            cr["duration_ms"] = r["duration_ms"]
        # Failed results MUST carry attribution + evidence (schema); skipped keep evidence when present
        # (e.g. the resolver's probe attempts) so an unresolvable path param is diagnosable.
        if r["status"] in ("failed", "skipped"):
            if "suspected_component" in r:
                cr["suspected_component"] = r["suspected_component"]
            if "evidence" in r:
                cr["evidence"] = r["evidence"]
            if "artifacts" in r:
                cr["artifacts"] = r["artifacts"]
        clean_results.append(cr)

    return {
        "poc_id": poc_id,
        "run_id": run_id,
        "deployment_run_id": deployment_run_id,
        "base_url": ctx.get("base_url", "http://localhost:80"),
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "not_automatable": not_auto,
            "duration_ms": duration_ms,
        },
        "results": clean_results,
        "success_criteria": sc_results,
    }
