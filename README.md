# Scheme Hunter

Follow-through assistant for Indian government scheme applications: from "I need money for my business" to
applications that are filed, tracked, chased and retried, with the person pressing every important button.
Built for the **GoDaddy Inclusive Innovation Challenge 2026**: Claude does the language, a GoDaddy VPS +
domain + Titan mailbox run the product.

**AI does the language, code does the truth.** Claude talks, reads, drafts and explains. Plain code decides
eligibility (`app/rules`), does the money math (`app/finance`), keeps state (`app/tracker`) and holds the
approval gate (`app/guard`). `tests/test_boundaries.py` enforces that those packages never import the AI.

Status: **v0.9**, slices 0–9 of LLD v1.1 (plus the optional PIN, admin pages and error states) plus the hackathon environment (SMTP via Titan, contact form,
deploy script, Claude budget controls). Built and tested with a fake Claude: tracker, vault, Alarm Clock, approvals (web and Telegram), inbound
message reading, project report and pre-filled pack. **Not yet deployed** and not yet run against the real API.
See `BUILD_NOTES.md` §5 for what is left and `AGENTS.md` for how to work on it.

## GoDaddy launch checklist

The organizers' 7 launch items are judged requirements. Full steps and evidence: **[docs/GODADDY_CHECKLIST.md](docs/GODADDY_CHECKLIST.md)**.

| # | Requirement | Satisfied by |
|---|---|---|
| 1 | VPS reachable by SSH key | ed25519 key, `deploy` user, password login off; `scripts/deploy.sh` uses `BatchMode=yes` |
| 2 | App in Docker, running on the VPS | `Dockerfile`, `docker-compose.yml` (4 services, `restart: unless-stopped`), `make deploy` |
| 3 | Own domain pointed at it | A record `@` → VPS IP (godaddy.com or `gddy`; see the checklist); `DOMAIN` in `.env` |
| 4 | HTTPS working | `Caddyfile`: automatic Let's Encrypt certificate + HSTS |
| 5 | Mailbox on the domain | Titan Business Email `hello@<domain>` (MX/SPF/DKIM from GoDaddy DNS) |
| 6 | Contact form → mailbox over SMTP | `/contact` (`app/routes/contact.py`) → `app/mouth/email.py` (smtplib, STARTTLS/SSL) |
| 7 | Password out of code and repo | `.env` only (git-ignored), `make check-secrets`, password never logged or shown in `repr` |

```sh
VPS=deploy@<vps-ip> DOMAIN=<domain> make deploy     # check-secrets → ssh → pull → build → migrate → /healthz
```

## Claude budget ($100 total credit)

- **Model choice:** Sonnet for the agent loop, explanations and vision; Haiku for classification and the outbound scheme-name scan.
- **Caching:** the system prompt, Rule Book summary, tool definitions and guideline pages are prompt-cached. Only the case file changes per run.
- **Caps:** 12 steps per run; `MAX_RUNS_PER_CASE_PER_DAY=40`; `DAILY_BUDGET_USD=10.00`. Over budget, only deadline runs go ahead; the rest wait until tomorrow.
- **Cost tracking:** every run's estimated cost is stored in `brain_runs`. `/admin/runs` shows the per-run cost and a running total.
- **Expected spend:** `make estimate-costs` prints the expected cost of a full persona journey and of the demo.
- **Demos:** `make demo-reset` loads pre-generated explanations, analysis and narrative, so only live onboarding and live document checks cost credit.
- **Tests:** tests never call the API. `tests/fake_claude.py` serves canned responses, and a real client can't even be constructed under pytest.

## Run locally (no Docker)

```sh
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q                              # FakeClaude; DB tests skip without DATABASE_URL
python -m app.rules.cli --profile meena          # scheme / result / deciding rule / page
python scripts/estimate_costs.py
```

With Postgres 16:

```sh
export DATABASE_URL=postgresql://app:pw@localhost:5432/schemehunter
python -m pytest -q                              # also runs DB tests (throwaway database)
python -m app.seed --reset                       # demo data; prints the three case links
uvicorn app.main:app --reload                    # /c/<token> → /chat, /schemes, /contact, /admin/runs
```

## Run on the VPS

```sh
cp .env.example .env && chmod 600 .env   # fill in DOMAIN, secrets, SMTP_*, ANTHROPIC_API_KEY
make up                                  # caddy + web + worker + postgres; migrations run at web startup
make demo-reset                          # < 10 s; prints case links
make check-secrets                       # before every push
```

## Demo script

LLD v1.1 §23 (≈6 minutes from `make demo-reset`). v0.9 covers the whole script: Meena's live onboarding, document uploads (blurry → retake, clean → masked tick),
Ravi's timeline and his chase firing on the demo clock, the draft in Needs-you, `YES <code>` on Telegram → one email
with Ravi copied, a forwarded SMS moving an application to QUERY_RAISED, Meena's report and pack and the "press
submit" gate, and `/contact`.
