# AGENTS.md — schemehunter

## What this is

Agent that takes a small Indian entrepreneur from "I need money" to filed, tracked, chased, retried government-scheme applications. GoDaddy + Claude hackathon, 7 days, $100 Claude credit total.

## Principles (non-negotiable)

1. Code decides, Claude talks. Eligibility, money math, dates, state, approvals = code.
2. Humans press real buttons: nothing is sent or submitted without an approved pending_action.
3. Never promise money. Strongest allowed: "you qualify to apply."
4. Only schemes in rulebook/ exist. "You qualify" cites a guideline page when pages exist, else source_status: pending.
5. No PII in logs or events.payload. Only last-4 of any ID is stored.

## Repo map

- app/rules, rulebook/ truth: rules/engine.py (evaluate, missing_for), rulebook/schemes/*.yaml
- app/brain Claude loop: brain/runner.py, brain/claude.py, brain/tools/__init__.py (ENABLED, dispatch)
- app/tracker/states.py TRANSITIONS, transition(), TrackerStore (injected)
- app/clock schedule.py (run_at_for; DEMO_CLOCK_SCALE applies here only), worker.py, handlers.py
- app/guard actions.py, scanner.py
- app/docs pipeline.py, vault.py, masking.py
- app/finance, app/forms project_report.py, forms/mappings/pmegp.yaml
- app/status, app/mouth status/base.py; mouth/email.py (Titan SMTP), mouth/telegram.py
- app/routes, app/templates FastAPI + HTMX; routes/contact.py is the judged contact form
- app/costs.py, app/config.py price table, budget guards
- sql/ 001_init.sql is the schema source of truth; later files additive
- seed/ personas (meena fresh, ravi mid-flight, fatima rejected), histories, artifacts, portal_messages
- tests/ FakeClaude in tests/fake_claude.py (autouse); DB tests skip without DATABASE_URL
- BUILD_NOTES.md §0 changes · §3 decisions · §4 TODO/verify · §5 next prompt · §6 cost log
- docs/GODADDY_CHECKLIST.md, scripts/deploy.sh, scripts/check_secrets.sh, scripts/estimate_costs.py

## Conventions

Python 3.12, type hints, pydantic v2, psycopg 3, ruff pinned. tests/test_boundaries.py: rules/finance/tracker/guard never import anthropic or app.brain. Tools: SCHEMA + handle(); dispatch returns {"error": ...}; side-effect tools create pending_actions. Bilingual summaries {"hi","en"}; numbers formatted in code. Secrets via .env only; `make check-secrets` before every commit.

## How to work (cost rules)

- Read AGENTS.md and BUILD_NOTES §5, then only files the task touches. `sed -n 'a,bp'`, not `cat`, for files > 150 lines.
- After an edit: `pytest -q -x tests/<area>/`. Full `pytest -q` once at the end.
- No clean-folder re-verification. No restating the design. Replies ≤ 10 lines.
- Commit small, push often: "slice N: <one line>". Push before the budget runs out.
- BUILD_NOTES updates in bullets: §0, §3, §4, §5 (next prompt), §6 (this session's cost).
- Ambiguity → smallest reasonable choice, record in §3, continue. Don't stop to ask.

## Slices and done-when

4 Tracker UI + DB store; /applications, /applications/{id}, /timeline; seeded histories read like a diary; illegal transitions raise.
5 Documents: pipeline → vision (FakeClaude) → mask → name match → status; 20 specimen fixtures scored; no 12-digit number survives.
6 Clock: claim loop (skip locked), backoff, sweeper, handlers; two workers never double-claim; deferred brain_run jobs execute.
7 Guard + Mouth: pending_actions, short codes, idempotency, /approvals, Telegram webhook (YES/NO code, /start binding); "YES twice → one send"; "guaranteed" bounced.
8 Inbound: interpret_response, confidence ≥ 0.7 gate, UserForwardsChecker; 15 portal_messages → intents; vague → clarification.
9 Report PDF (use seeded report_narrative artifact first; Devanagari, English fallback), pmegp prefill, request_submit; EMI/break-even to the rupee.
10 Real users: fixes only. 11 Demo hardening: demo-reset re-arms Ravi's chase at T+2 min; 3 clean rehearsals.

## Never cut

Rules engine + tests · citations · Guard · Tracker + timeline · Telegram approval loop · make demo-reset
