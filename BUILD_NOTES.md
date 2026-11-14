# BUILD_NOTES: Scheme Hunter v0.3 (slices 0–3 + hackathon environment)

Sources: Notion **HLD - Hackathon**, **File Structure**, **LLD v1.1**; curator decisions in the v0.2 and v0.3
briefs. v0.3 = Part A (decisions confirmed) + Part B (GoDaddy/Titan/$100-credit environment) + Part C (slice 3).

## 0. What changed since v0.2, and why

| Change | Why |
|---|---|
| **Resend deleted → SMTP via Titan** (`app/mouth/email.py`, smtplib, STARTTLS 587 → SSL 465 fallback) | Organizer checklist requires a mailbox on our domain and SMTP sending; one provider, no extra account |
| **Public contact form** `/contact` (`app/routes/contact.py`) | Checklist item 6: form → Titan mailbox over SMTP |
| **`scripts/deploy.sh`, `docs/GODADDY_CHECKLIST.md`, `make deploy`** | Checklist items 1–4; repeatable deploys to the VPS |
| **Caddy reads `{$DOMAIN}` from `.env`**; compose: one uvicorn worker, worker `WORKER_THREADS=1`, `restart: unless-stopped` everywhere | Fit the 4 vCPU / 8 GB VPS; no hand-editing the Caddyfile |
| **Claude budget controls:** price table, `brain_runs.estimated_cost_usd`, `DAILY_BUDGET_USD` (replaces `DAILY_TOKEN_BUDGET`), `MAX_RUNS_PER_CASE_PER_DAY`, `/admin/runs`, `scripts/estimate_costs.py` | $100 total credit for development, tests and demo |
| **FakeClaude for every test** (`tests/fake_claude.py`, autouse fixture) | Never spend credit in tests; constructing a real client raises under pytest |
| **Seeded artifacts** (`seed/artifacts/`): Ravi's PMEGP explanation, Fatima's rejection analysis, Meena's report narrative; Fatima's `brain_run` job removed | The demo spends credit only on live onboarding and live document checks |
| **`make check-secrets`** (`scripts/check_secrets.sh`) | Checklist item 7; runs automatically before `make deploy` |
| **Slice 3:** `claude.py`, `runner.py`, 5 tools, `/chat`, `/schemes`, `/answers`, sessions/CSRF/rate limits, language toggle | LLD §22 slice 3 |
| **Migrations 003/004:** `rate_limits`, `artifacts`, `messages`/`events` default to `clock_timestamp()`, brain_runs cost columns | Contact/link rate limits, artifacts, true ordering within one transaction, cost accounting |
| **Engine:** `missing_for()` walks only the branches that made a rule UNKNOWN | Part A: an unknown `when` asks for the `when` fact, not the `then` fact |

## 1. Status

| Slice | Done when (LLD §22) | Status |
|---|---|---|
| 0 | infra | ✅ code + config + deploy script. **Not yet deployed:** I have no VPS access from here (you run `make deploy`) |
| 1 | rules 30/30 | ✅ 30/30 + branch coverage + new when/then missing-fact tests |
| 2 | demo-reset < 10 s, idempotent | ✅ 0.4 s; now also loads artifacts |
| 3 | 3 phrasings → same matches; every FIT has a citation | ✅ with FakeClaude. "Every FIT has a citation" is asserted **for schemes whose pages exist**; without pages the explanation is stored `source_status: "pending"` and the UI shows a grey **"source pending"** badge. The three-phrasings check is covered at the engine level (same facts → same matches); a real-model phrasing test needs the API, so it's in TODO T16 |

Stubbed and returning 501 until built: applications/timeline (slice 4), documents (5), worker/handlers (6),
approvals/Telegram (7), interpret_response (8), report/prefill (9), admin jobs/actions (10).

### Test results (FakeClaude for every test, no network; Python 3.12.3, Postgres 16)

```
$ python -m pytest -q                                   # no services
222 passed, 42 skipped in 2.2s
$ DATABASE_URL=postgresql://… python -m pytest -q       # with Postgres
261 passed, 3 skipped in 6.9s
  SKIPPED tests/clock/test_claim.py: slice 6
  SKIPPED tests/docs/test_pipeline.py: slice 5
  SKIPPED tests/e2e/test_happy_path.py: slices 3-9
$ ruff check . && ruff format --check .   → All checks passed
$ make check-secrets                      → check-secrets: ok
```

New suites:
- `tests/brain/test_runner.py`: step cap, budget guard, per-case cap, error-return path, tool crash in a savepoint, finish_run required, API failure, `/admin/runs` totals.
- `tests/brain/test_tools.py`: dispatch errors, coercion, match, explain cited/pending/cached/guard-fallback, ask_user, finish_run rewrites.
- `tests/brain/test_claude.py`: caching layout, routing, max_tokens, citations parsing, cost maths.
- `tests/e2e/test_onboarding.py`: Meena's 3 turns over HTTP, the adversarial script, session/CSRF, the `/answers` fallback, the language toggle.
- `tests/test_contact.py`: fake SMTP server, honeypot, validation, 5/hour per IP, SMTP down.
- `tests/mouth/test_email.py`: aiosmtpd, STARTTLS→SSL fallback, the password never leaks.
- `tests/test_scripts.py`: check-secrets, estimate_costs, deploy guard.

## 2. How to run the tests locally

```sh
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"                   # includes aiosmtpd (local fake SMTP server)
python -m pytest -q                       # pure tests; DB tests skip
export DATABASE_URL=postgresql://app:pw@localhost:5432/schemehunter
python -m pytest -q                       # + DB tests in a throwaway database (role needs CREATEDB)
```

No test can reach Anthropic: `tests/conftest.py` installs FakeClaude for every test and replaces both
`anthropic.Anthropic` and our `AnthropicClient` with functions that raise. Scripts live in
`tests/fixtures/claude/*.yaml` (`meena_onboarding`, `adversarial`); responses are queued per purpose
(`loop`, `explain`, `explain_cited`, `scan`).

## 3. Decisions and assumptions

### Part A: curator decisions confirmed (no code change unless stated)
- **A1 Udyogini OBC income cap:** kept ("general" = not SC/ST); stays `verified: false` until confirmed.
- **A2 Mudra:** keeps non-farm + `agri_allied`. The sector rule's source_note is now *"Non-farm income-generating enterprises. PMMY extended to activities allied to agriculture in FY2016-17 (dairy, poultry, beekeeping etc.); crop loans excluded."* No micro-enterprise fact.
- **A3 Personas:** Fatima's `loan_amount` and Meena's unknown loan facts are kept.
- **A4 Engine:** `not_in`, UNKNOWN-only missing facts and three-valued `special_category` are kept.
  - **Code change:** `missing_for()` now follows only the branch that made a rule UNKNOWN.
  - New test: an unknown `when` fact is in `missing_facts`, and the `then` fact is not (`test_unknown_when_asks_for_when_fact_not_then_fact`).
- **A5 Scanner name-blanking:** kept; it blanks exact Rule Book names and aliases only, in both scripts. New test: "we guarantee approval" still trips, including right next to a blanked CGTMSE name or Hindi scheme name.
- **A6:** backups via host cron and the ruff pin are both kept.

### Still in force from v0.2 (unchanged)
- `init.py` → `__init__.py`.
- `001_init.sql` is verbatim LLD §5.
- Deterministic demo ids and tokens.
- Tracker store injection.
- Finance DSCR definition.
- Idempotency key normalisation.
- Masking also handles hyphens.

The full v0.2 reasoning is in git history: `v0.2 baseline` commit, BUILD_NOTES §3 items 1–33.

### v0.3: email and contact form
1. **SMTP security choices:**
   - Default `SMTP_PORT=587` with STARTTLS.
   - On a non-auth failure it retries **implicit SSL on 465**. Auth failures don't fall back, so a wrong password isn't tried twice.
   - `SMTP_PORT=465` uses SSL directly.
   - `SMTP_SECURITY=none` is plain SMTP, for the local test server only.
2. **The password is excluded from `repr` and from error text, and logs carry no email addresses** (LLD §17). Tested.
3. **`send()` returns the Message-ID** and accepts `reply_to`, which the contact form uses. The Channel signature `(to, text, attachments)` is unchanged; the extras are keyword-only.
4. **Contact form behaviour:**
   - A filled honeypot gets the thank-you page, but nothing is stored or sent.
   - The rate limit (5/hour per IP) counts only submissions that pass validation.
   - The row is stored **before** sending. If SMTP fails, the row stays `processed=false` and the user still sees thanks. A curator view of unsent messages is TODO T10.
5. **No CSRF token on `/contact`:** there is no session to protect; spam is handled by the honeypot and rate limit.
6. **Client IP** is the first `X-Forwarded-For` hop (`TRUST_PROXY=true`). This relies on Caddy replacing a client-supplied XFF, which is Caddy's default when `trusted_proxies` isn't set (T11 verify). Uvicorn also runs with `--proxy-headers`.

### v0.3: deployment
7. **Caddy** uses `{$DOMAIN}`; `DOMAIN` was added to `.env.example`. The compose file fails fast if `DOMAIN` is missing.
8. **`deploy.sh`** expects the repo already cloned at `/srv/schemehunter` with `.env` present. It does `ff-only` pulls, runs migrations explicitly (they also run at startup; both are idempotent), and retries `/healthz` up to 10 times.
9. **The checklist doc contains no `gddy` syntax.** Each CLI step states its intent and says to find the command with `gddy --help` / `gddy tree`.
10. **Titan SMTP host:** `smtpout.secureserver.net` (GoDaddy relay), with `smtp.titan.email` as the alternative. Which one works depends on how the mailbox was provisioned; the checklist has a one-line test-email command to find out.

### v0.3: Claude budget
11. **Price table** (USD per million tokens, `app/config.py`):

    | Model | Input | Output | Cache write | Cache read |
    |---|---|---|---|---|
    | Sonnet 4.5 | 3.00 | 15.00 | 3.75 | 0.30 |
    | Haiku 4.5 | 1.00 | 5.00 | 1.25 | 0.10 |

    **Verify on platform.claude.com before the demo (T12).** Unknown model ids are billed at the most expensive known rates, so cost estimates err high.
12. **"Today"** for the budget and per-case caps is the **Asia/Kolkata** calendar day (`BUDGET_TZ`).
13. **Over budget, or over the per-case cap:**
    - The run is recorded as `DEFERRED` (cost 0) and a `brain_run` job is queued for tomorrow at 00:05 IST.
    - The worker that would run it is slice 6, so for now these jobs just wait.
    - The per-case cap counts every non-deferred run, including STUCK and ERROR ones.
    - `deadline_related=True` bypasses both caps (LLD §7.6).
14. **Every Claude call during a run is metered into that run** (loop steps, explain_match, the Haiku scanner). There are no Claude calls outside runs yet.
15. **`SCAN_WITH_MODEL=true`:** each outbound text gets the regex check plus one Haiku call, at about $0.0002 each. Set it to `false` to save even that; the regex fallback still catches "… Yojana/Scheme" names.
16. **Cost estimates:** `scripts/estimate_costs.py` uses planning token counts, not measurements, and labels them as such. Current output is **≈ $0.36 per full persona journey** and **≈ $0.17 per demo run-through** (≈ 590 rehearsals on $100). Replace the counts with real numbers from `/admin/runs` after the first real runs (T13).
17. **Seeded explanation:** Ravi's explanation has the citation *fields*, but `citations: []` and `source_status: "pending"`, because no guideline PDFs exist yet. **I will not invent guideline quotes.** Once the PDFs and pages land, regenerate it with one live `explain_match` (≈ $0.03, T14).
    - Seeded explanations are stamped with the scheme file's hash, so `explain_match` serves them from cache.
    - Editing `pmegp.yaml` invalidates the stamp automatically.
18. **Meena's report narrative** lives in the new `artifacts` table (kind `report_narrative`). Slice 9's `build_project_report` must read the artifact before calling Claude (T15).

### v0.3: slice 3 behaviour
19. **`explain_match` paths:**
    - **Pages present:** a citations call over the pages named in the scheme's rules, or all pages (up to 20) if no rule has a page.
    - **No pages:** a forced-schema Claude call without citations, stored as `source_status: "pending"`.
    - **Guard scan:** a generated explanation that fails the scan is replaced by a fixed, promise-free sentence and flagged `scan_failed`.
    - **Cache:** reused when the result and the scheme hash are unchanged.
20. **finish_run is strictly required** (LLD §7.5). If the model ends with plain text, the run is STUCK.
21. **Guard rewrites:**
    - `finish_run`: 2 rewrites, then a safe fallback message plus a `TOOL_CALLED ok=false` event carrying the guard details.
    - `ask_user`: a blocked question is returned as an error, which counts toward the 12-step cap.
22. **One question per run** is enforced in `ask_user` (LLD rule 6).
23. **The case file gains `recent_messages`** (last 6 chat turns) **and `open_question`**, which stays open until the assistant replies, so the Brain can match "teen lakh" to `loan_amount`. This is an addition to LLD §7.2.
24. **Tool calls run inside a savepoint.** A crashing tool leaves no partial rows, and the error goes back to Claude as a `tool_result` with `is_error: true`.
25. **`/chat`:**
    - Runs the Brain in a 2-thread pool inside the web process and waits up to `CHAT_SYNC_TIMEOUT_S=60`.
    - On timeout the user sees "working on it" and the answer appears on refresh; there's no push until Telegram in slice 7.
    - Rate-limited to 20 messages/min per case.
26. **`/answers`** saves the answer and re-runs the rules engine in code. **It costs no credit.**
27. **Sessions:**
    - A self-signed HMAC cookie (no extra dependency): 30 days, HttpOnly, `Secure` when `APP_BASE_URL` is https, SameSite=Lax.
    - CSRF uses a per-case HMAC token.
    - `/c/*` is limited to 20/min per IP.
    - **The optional PIN is still a stub** (T9).
28. **Routing details:**
    - `/cases/{id}` accepts the case's own session or admin basic auth.
    - `/` redirects to `/chat`.
    - Browsers get a readable HTML page for 401/403/404.
    - The language toggle sets a cookie and `cases.language`. Its redirect keeps only the path, so there's no open redirect (tested).
29. **`messages` and `events` default to `clock_timestamp()`**, because rows written in one agent step tied on `now()` and came back in the wrong order.
30. **check-secrets detection rules:**
    - The Anthropic pattern requires 20+ key characters after `sk-ant-`, so these notes, the script and tests mentioning the prefix don't trip it.
    - Values are checked for `SMTP_PASSWORD` (any length) and for other `*PASSWORD*`/`*SECRET*`/`*KEY*`/`*TOKEN*` entries of 8+ characters.
    - Non-secret `.env` values (such as the domain) appear legitimately elsewhere, so they aren't checked.
31. **New files beyond the File Structure page:**
    - `app/costs.py`, `app/routes/contact.py`, `app/routes/render.py`, `app/brain/tools/facts_vocab.py`
    - templates `contact.html`, `contact_thanks.html`, `partials/chat_messages.html`, `partials/citation_popover.html`
    - `docs/GODADDY_CHECKLIST.md`, `scripts/deploy.sh`, `scripts/check_secrets.sh`, `scripts/estimate_costs.py`
    - `seed/artifacts/`, `tests/fake_claude.py`, `tests/fixtures/claude/`
32. **Dev dependency added:** `aiosmtpd` (fake SMTP server in tests). The `anthropic` SDK was already a runtime dependency and is now used, with `max_retries=3`.

## 4. TODO / verify list

### Curator: guideline verification (unchanged from v0.2)
- **V1–V5:** check every rule for PMEGP, Stand-Up India, Mudra, CGTMSE and Udyogini against the PDFs, then flip `verified: true` and add `page:`.
- **V6:** have a Hindi speaker check the scheme names and benefit lines.
- **T1–T4:** missing fields for Stand-Up India, Mudra, CGTMSE and Udyogini (`portal_url`, `window`, `documents`, `source_pdf`, `authority`, CGTMSE coverage %).
- **T5:** add `rulebook/sources/<id>/guidelines.pdf` and run `make extract-pages`. **This flips explanations from "source pending" to cited.**

### Before the judges look (hackathon)
- **G1 VPS:** do checklist items 1–4 (SSH key, clone, `.env`, `make up`, A record, HTTPS). Evidence commands are in the checklist.
- **G2 Titan:** create `hello@<domain>`, check MX/SPF/DKIM, run the test-email command, then submit `/contact` and show the email arriving.
- **G3 Secrets:** `make check-secrets` must pass on the VPS checkout too.
- **G4 Demo clock:** set `DEMO_CLOCK_SCALE=1440` in `.env` for the demo.

### Placeholders
- **T6:** `htmx.min.js` is still a placeholder. Vendor it; `/chat` already works without JS through normal form posts.
- **T7:** the Devanagari font file is 0 bytes (needed by slice 9).
- **T8:** `seed/contacts.yaml` should point at your inboxes.
- **Persona emails:** not set (needed in slice 7).

### Engineering
- **T9:** optional PIN (`access.check_pin`).
- **T10:** an admin view of `messages` with `processed=false` (contact emails that failed to send).
- **T11:** confirm Caddy replaces a client-sent `X-Forwarded-For` (send a spoofed XFF and check the rate-limit key).
- **T12:** check the price table against platform.claude.com.
- **T13:** replace the `estimate_costs.py` token counts with measured ones from `/admin/runs`.
- **T14:** regenerate Ravi's seeded explanation with citations once the PMEGP pages exist (one live call, ≈ $0.03).
- **T15:** slice 9's `build_project_report` must use the seeded `report_narrative` artifact first.
- **T16:** live smoke test with the real API: three phrasings of Meena's answers → same matches, run **once** by hand (≈ $0.30). It's deliberately not part of pytest.
- **T17:** add a cleanup job for old `rate_limits` rows (slice 6 worker).
- **T18:** deferred `brain_run` jobs need the slice 6 worker to run them.

## 5. What the next prompt should ask for: slices 4 + 5

> Build **slices 4 and 5** from the LLD on top of v0.3. Keep FakeClaude for every test, the budget guards,
> and `tests/test_boundaries.py` green.
>
> **Slice 4 (Tracker + timeline):**
> - A DB-backed `TrackerStore` for `app/tracker/states.py`: `save_state` updates `applications`
>   (`last_movement_at`), `log_state_change` writes a `STATE_CHANGED` event, and `run_effect` inserts `jobs`
>   rows via `app.clock.schedule.run_at_for` (DEMO_CLOCK_SCALE applies there only) or creates a pending action
>   for `instruct_submit`. `required_docs_ok` stays False until slice 5's statuses exist.
> - Tools `create_application {scheme_id}` (DISCOVERED → ELIGIBLE if the match is FIT; refuse otherwise) and
>   `transition_application {application_id, to_state, reason}` (actor `agent`; IllegalTransition → `{"error"}`).
>   Add both to `ENABLED`.
> - Pages `/applications` (parcel-tracking rows), `/applications/{id}` (states + that application's events),
>   `/timeline` (`select * from events where case_id = ? order by created_at`, in the user's language), all
>   session-scoped, in Hindi/English. Ravi's and Fatima's seeded histories must read like a diary.
> - Tests: every legal transition through the DB store; illegal transitions raise; the ref_number guard; effects
>   create the right jobs (with DEMO_CLOCK_SCALE=1440, `days=1` → +1 minute); a FakeClaude script where the Brain
>   creates an application for Meena after a FIT.
>
> **Slice 5 (documents + vault):**
> - `scripts/make_specimen_docs.py`: 20 Pillow images visibly stamped "SPECIMEN / DEMO" (clear, blurry, cut-off,
>   wrong type, mismatched name), plus `tests/docs/fixtures/labels.yaml`. Never realistic fake IDs.
> - `app/docs/pipeline.py` per LLD §12: ≤ 10 MB, jpeg/png/pdf, EXIF strip, sha256 dedupe, AES-GCM vault,
>   vision check only for aadhaar/passbook (MODEL_REASON, forced `document_check_result` schema,
>   max_tokens 1200, **downscale images so the long edge is ≤ 1568 px before sending** to cap tokens),
>   `mask_fields` on everything, name match (token_set_ratio < 85 → PROBLEM), status decision per §12.3.
> - Tools `check_document {document_id}` and `list_required_documents {scheme_id}` (resolve the conditional
>   document names against facts); `/documents` upload page (camera input, green/yellow/red with a one-line
>   reason), `GET /documents/{id}/file` (session check, decrypt in memory, `Cache-Control: no-store`).
> - Vision calls are metered into the run like every other call; `estimate_costs.py` gets a measured image-token
>   count line.
> - Tests with FakeClaude vision scripts (no real images sent anywhere): report correct / retake / wrong rates over
>   the 20 fixtures; the mask test proves no 12-digit number reaches the DB or the case file; duplicate upload
>   returns the existing row; a passbook name mismatch → PROBLEM; the decrypted file is never written to disk.
>
> Update BUILD_NOTES (§0 changes, §3 assumptions, §4 TODOs, §5 prompt for slices 6 + 7), README, and paste the
> pytest summary. Deliver `schemehunter-v0.5.zip`.

## 6. Appendix: files

- **Tree vs the File Structure page:** everything on the page exists (`init.py` written as `__init__.py`), plus the v0.3 additions in §3 item 31.
- **Not included:**
  - the guideline PDFs (T5)
  - `data/` (runtime only)
  - `.git/` (the zip is a source snapshot; `git init` is up to you)
