# fixtures

Golden fixtures so every agent can be built and tested without the others (Spec §10.2, §10.5).

| Path | Used by |
|---|---|
| `transcripts/recsys_meeting.txt` | Draft Agent acceptance (must draft) |
| `transcripts/vague_meeting.txt` | Draft Agent acceptance (must ask ≥ 2 clarifying questions) |
| `golden/POC_ID` | The golden `poc_id` (`poc_01K5ZGF1XTVREG0000000000A1`) |
| `golden/spec/v001/` | Hand-written `poc_spec.md`, `schema_design.json`, `query_patterns.json`, `clarifications.json` — validate against `poc_contracts` |
| `golden/code/v001/` | `api_contract.yaml` (OpenAPI 3.1) + `seed/`, `backend/`, `frontend/` each with `component.manifest.json`, + `poc.manifest.json`. Backend compiles, frontend builds, all 11 `data-testid`s present, guardrail scan clean |
| `golden/docker-compose.yml` | Local stand-in for a deployed golden POC on http://localhost:8088 (Test Agent development) |
| `conversation_happy_path.json` | Chat Agent acceptance script |

The domain is Indian online grocery ("Kirana Basket"): 5 000 products across 12 categories, 8 000 orders with co-purchase clusters, INR prices.

**Give a transcript a timeline / deadline.** `timeline_constraints` is one of the fields the Draft Agent
checks for completeness, so a transcript that never states one triggers a clarification round on it. Include
a demo deadline in the meeting text — e.g. *"we want to demo this in two weeks"* — and the Draft Agent drafts
straight through (the DailyDabba order-analytics transcript, which omitted a deadline, asked a round-2
question "what's the target deadline?"; answering "demo in two weeks" produced the spec). `recsys_meeting.txt`
already carries enough detail to draft in one pass; `vague_meeting.txt` is intentionally missing fields
(including a timeline) so it must ask ≥ 2 clarifying questions.

Load the golden spec + code into S3 with the loader in `scripts/` (added with the Coding Orchestrator) so the Deploy Agent can be exercised with no coding agents involved.
