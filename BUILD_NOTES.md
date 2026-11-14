# BUILD_NOTES: Scheme Hunter v0.5 (slices 0–5 + hackathon environment)

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
| **v0.5 · Slice 4:** `app/tracker/store.py` (`DbTrackerStore`), `create_application` + `transition_application` tools, `/applications`, `/applications/{id}`, `/timeline`, diary-style Ravi/Fatima histories | LLD §22 slice 4 |
| **v0.5 · Slice 5:** `docs/pipeline.py`, `docs/requirements.py`, `check_document` + `list_required_documents` tools, `/documents` (+ decrypt-in-memory download), 20 specimen fixtures | LLD §22 slice 5 |
| **`schedule()` and `create_pending()` are real now** (rows in `jobs` / `pending_actions`) | The tracker's effects need them; slices 6 and 7 build on them |
| **`AGENTS.md`** added | Cost rules and repo map for future sessions |

## 1. Status

| Slice | Done when (LLD §22) | Status |
|---|---|---|
| 0 | infra | ✅ code + config + deploy script. **Not yet deployed:** I have no VPS access from here (you run `make deploy`) |
| 1 | rules 30/30 | ✅ 30/30 + branch coverage + new when/then missing-fact tests |
| 2 | demo-reset < 10 s, idempotent | ✅ 0.4 s; now also loads artifacts |
| 3 | 3 phrasings → same matches; every FIT has a citation | ✅ with FakeClaude. "Every FIT has a citation" is asserted **for schemes whose pages exist**; without pages the explanation is stored `source_status: "pending"` and the UI shows a grey **"source pending"** badge. The three-phrasings check is covered at the engine level (same facts → same matches); a real-model phrasing test needs the API, so it's in TODO T16 |
| 4 | illegal transitions raise; timeline reads like a diary | ✅ every legal transition through the DB store (30 pairs), illegal ones leave no row, ref_number and docs guards, effects → jobs/pending actions, demo clock `days=1` → +1 min, Brain creates Meena's application after a FIT (FakeClaude). Seeded diaries are checked for raw state codes in both languages |
| 5 | rates reported; mask test green | ✅ 20/20 fixtures: **10 OK, 6 RETAKE, 4 PROBLEM**; no 12-digit number reaches `documents`, `events`, `messages`, `profile_answers`, `jobs` or the case file; duplicate upload returns the existing row; passbook name mismatch → PROBLEM; decrypted bytes never written to disk. **Caveat:** these rates test our code (decision rules, masking, name match) against what FakeClaude is told it sees. They say nothing about real vision accuracy; that needs one live run (T19) |

Stubbed and returning 501 until built: worker/handlers (6),
approvals/Telegram (7), interpret_response (8), report/prefill (9), admin jobs/actions (10).

### Test results (FakeClaude for every test, no network; Python 3.12.16 via uv, Postgres via `pgserver`, Windows 11)

```
$ DATABASE_URL=postgresql://… python -m pytest -q
329 passed, 2 skipped, 1 failed
  SKIPPED tests/clock/test_claim.py: slice 6
  SKIPPED tests/e2e/test_happy_path.py: slices 3-9
  FAILED  tests/test_scripts.py::test_smtp_password_value_in_tree_fails   (Windows only: see §3 item 47)
$ ruff check . && ruff format --check .   → All checks passed
$ make check-secrets                      → check-secrets: ok
```

Before this session (v0.3, Linux): 261 passed, 3 skipped. This session adds 69 tests.

New suites (v0.5): `tests/tracker/test_store.py`, `tests/e2e/test_tracker_pages.py`, `tests/docs/test_pipeline.py`, `tests/e2e/test_documents_routes.py`.

Suites from v0.3:
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

### v0.5: slice 4 (tracker)
33. **`DbTrackerStore`** lives in `app/tracker/store.py` and imports only `app.db`, `app.events`, `app.clock.schedule`, `app.guard.actions` (no AI; `test_boundaries` stays green). It joins the caller's transaction.
34. **`schedule()`** (in `app/clock/schedule.py`) is now `schedule(conn, type, case_id, application_id, days_from_now, at, payload, scale)`; it is still the only place `DEMO_CLOCK_SCALE` is applied. `at` is absolute and is **not** scaled.
35. **`next_window`** (REJECTED → RETRY_NEXT_CYCLE, MISSED) = now + 30 days / scale for every scheme, because only PMEGP declares a `window` and it is `rolling`. Constant `NEXT_WINDOW_DAYS`. Replace with real window dates when schemes have them (T20).
36. **`create_pending()`** (`guard/actions.py`) now takes `conn` first and inserts a row; duplicates are dropped by the idempotency key (sha256 of kind, case, application, sorted-JSON payload). The rest of the Guard lifecycle is still slice 7.
37. **`create_application`** refuses anything the rules engine didn't mark FIT. If an open application for the scheme already exists it returns that one (`already_exists: true`) instead of a second row. "Open" excludes CLOSED, MISSED, RETRY_NEXT_CYCLE and SANCTIONED, so a retry cycle can start a new row.
38. **`transition_application`** always acts as `agent`. An illegal move returns `{"error", "allowed_next_for_agent"}` so the model can adapt; application ids from another case look like "no such application".
39. **Pages:** `/applications` shows a parcel-tracking road (ELIGIBLE → … → SANCTIONED). Off-road states (QUERY_RAISED, REJECTED, MISSED…) are inserted as the current stop. `/applications/{id}` shows only events tied to that application. Another case's id is a 404, never a 403. `/timeline` is `select … order by created_at` in the cookie/case language.
40. **Seeded diaries:** Ravi's and Fatima's state-change events were rewritten as sentences ("Asked Ravi for his papers: Aadhaar, photo, passbook.") instead of `A → B`. A test fails if an `UPPER_CASE_STATE` name appears in any diary line in either language. Hindi lines are my drafting: V6 (a Hindi speaker checks) now also covers them.

### v0.5: slice 5 (documents)
41. **Required papers:** `docs/requirements.py` resolves `<doc>_if_<cond>` names. `caste_cert_if_special` → needed when `category` is SC, ST or OBC (not "special" in the engine's sense, which also includes all women); `education_cert_if_cost_gt_10l` → `project_cost` > ₹10 lakh. Unknown conditionals are dropped, not guessed. Schemes whose YAML has no `documents:` default to `[aadhaar, passbook]` (curator TODO T1–T4).
42. **DOCS_READY guard** (`required_docs_ok`): every user-supplied paper is uploaded with status OK. `project_report` is excluded because we generate it in slice 9, after DOCS_READY.
43. **Status rules (§12.3 + one addition):** RETAKE if not readable or issues include `cut_off`, `blur` or `too_dark` (the LLD listed only `cut_off`; the demo says "blurry → retake"). `glare` alone stays OK. PROBLEM on wrong type or name mismatch (token_set_ratio < 85 against the profile name and other OK documents).
44. **Vision** only for aadhaar and passbook, forced schema `document_check_result`, `max_tokens` 1200, `MODEL_REASON`, image downscaled to a long edge ≤ 1568 px. **An aadhaar/passbook sent as a PDF is RETAKE** ("send a photo"), never OK without a check. photo, quotation, caste certificate and the rest get a basic "opens correctly" check, no model call.
45. **Upload route:** each upload with a vision check opens a `brain_runs` row (`trigger=upload`) so its cost is metered and shows in `/admin/runs`. Over budget (`deferral_reason`), the paper is stored as PENDING and the vision call waits (the `check_document` tool can run it later). Rate limit 20 uploads/min per case. Dedupe is on the sha256 of the original bytes.
46. **Specimen fixtures:** 20 images, each stamped "SPECIMEN / DEMO" with placeholder text only (no realistic IDs), made by `scripts/make_specimen_docs.py`. Each carries a "SAMPLE #nn" line so no two share bytes (identical images would dedupe and skip the vision call). `labels.yaml` records what FakeClaude returns per image. Three images carry a full 12-digit number in the model output, in three formats, to prove the code mask works even if the model ignores the prompt.
47. **Windows test environment:** the box has no Docker, Postgres or Python 3.12, so I used `uv` (Python 3.12.16) and the `pgserver` wheel (embedded Postgres) in `.venv`. Known Windows-only failure: `test_smtp_password_value_in_tree_fails` runs `scripts/check_secrets.sh` via `sh` with a Windows temp path, and the script doesn't find the planted password. It passes on Linux (v0.3 notes). `PYTHONUTF8=1` is needed for `estimate_costs.py` output on a cp1252 console.
48. **`estimate_costs.py`** now prints the image-token line (4000×3000 photo → 1568×1176 → ≈ 2,459 tokens by the w×h/750 rule) and the journey uses image + 500 tokens per vision check. It is arithmetic on the real downscale, not a measurement (T13/T19). Journey ≈ $0.36, demo ≈ $0.17 (unchanged to the cent).

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

- **T19:** one live vision run on real specimen photos (≈ $0.02 each) to get real RETAKE/PROBLEM/correct rates and a measured image-token count. Not part of pytest.
- **T20:** real `window` dates for schemes, then replace `NEXT_WINDOW_DAYS`.
- **T21:** `docs/LLD.md` is truncated (stops after §0); re-export it from Notion. `docs/HLD.md` has a duplicated block and every heading numbered `1.`.
- **T22:** Linux CI (or the VPS) must run the full suite once, since the Windows run has the T-script failure in §3 item 47.
- **T23:** the `/documents` page has no "retake" shortcut or camera preview; it is a plain form that works without JS.

## 5. What the next prompt should ask for: slices 6 + 7

> Build **slices 6 and 7** from the LLD on top of v0.5. Keep FakeClaude for every test, the budget guards,
> and `tests/test_boundaries.py` green. Read AGENTS.md first.
>
> **Slice 6 (Alarm Clock):**
> - `app/clock/worker.py`: claim loop with `FOR UPDATE SKIP LOCKED` (LLD §10.2), backoff `[60, 600, 3600, 6h, 24h]`,
>   DEAD after 5 attempts + curator alert, sweeper resetting RUNNING > 30 min every 10 min. `WORKER_THREADS=1`.
> - `app/clock/handlers.py`: `status_check`, `chase`, `doc_nudge`, `deadline_reminders`, `retry_cycle`, `brain_run`
>   (this is where deferred runs from the budget guard finally execute), `retention_cleanup`, plus `rate_limits`
>   cleanup (T17). `offer_domain` stays flag-gated and stubbed. Templated messages go through
>   `app/mouth/templates.py` (no LLM) and become pending actions, never direct sends.
> - `app/status/`: `UserForwardsChecker` and `ManualChecker` per LLD §14.
> - Use `schedule()` and `DbTrackerStore` (already real). Ravi's seeded `chase` job must fire ~2 minutes after
>   `make demo-reset` and draft a follow-up for approval.
> - Tests: two workers never double-claim; backoff sequence; sweeper; DEAD + alert; demo-clock scaling through a
>   real handler run; deferred `brain_run` executes; `tests/clock/test_claim.py` un-skipped.
>
> **Slice 7 (Guard + Mouth):**
> - `app/guard/actions.py`: `approve`, `reject`, `execute`, `expire` (7 days), FAILED after 3 executor tries;
>   `executed_at` is set **before** calling the provider (a crash can't resend). Short codes already exist.
> - `draft_message` tool: forced schema → scanner → pending action (≤ 2 rewrites, then PROBLEM for the curator).
>   `request_submit` tool (state → AWAITING_HUMAN_SUBMIT, `instruct_submit` pending action already created by the
>   tracker effect; do not create a second one).
> - `/approvals` page + `POST /approvals/{id}/approve|reject` (session must own the case, CSRF), and
>   `/admin/actions`.
> - `app/mouth/telegram.py`: send, send_document, webhook with `X-Telegram-Bot-Api-Secret-Token`, `/start <token>`
>   binding, `YES|NO <code>`. `app/mouth/email.py` already works; bank/officer mail CCs the user and the subject
>   carries the ref number.
> - Tests: YES twice → one send; "guaranteed" bounced and rewritten; duplicate webhook ignored via
>   `messages.external_id`; unknown chat id → "open your link first"; expired actions; a FAILED send is visible in
>   Approvals and Admin.
>
> Update BUILD_NOTES (§0, §3, §4, §5 prompt for slices 8 + 9, §6 cost log), README, and paste the pytest summary.

## 6. Cost log

| Session | Claude API credit spent | Notes |
|---|---|---|
| v0.5 (slices 4 + 5) | **$0.00** | Every test uses FakeClaude, and a real client cannot be constructed under pytest, so no API call was made. `scripts/estimate_costs.py` is arithmetic only. The model cost of the build session itself is not visible from inside it; check the console. |

Projected spend from here: one live vision rehearsal (T19) ≈ $0.02 per image; one full persona journey ≈ $0.36; one demo run-through ≈ $0.17.

## 7. Appendix: files

- **Tree vs the File Structure page:** everything on the page exists (`init.py` written as `__init__.py`), plus the v0.3 additions in §3 item 31 and, in v0.5, `app/tracker/store.py`, `app/docs/requirements.py`, `templates/partials/{steps,events}.html`.
- **Not included:**
  - the guideline PDFs (T5)
  - `data/` (runtime only)
  - `.git/` (the zip is a source snapshot; `git init` is up to you)
