schemehunter/
├── README.md                          # what it is, how to run, demo script
├── Makefile                           # up, down, logs, migrate, seed, demo-reset, test, extract-pages
├── Dockerfile                         # python:3.12-slim + WeasyPrint deps + Noto Devanagari font
├── docker-compose.yml                 # caddy, web, worker, postgres
├── Caddyfile                          # HTTPS + reverse proxy to web:8000
├── pyproject.toml                     # deps: fastapi, uvicorn, anthropic, psycopg, jinja2, weasyprint,
│                                      #       rapidfuzz, cryptography, pillow, pyyaml, structlog, httpx, pytest
├── .env.example                       # every key from LLD §4, empty values
├── .gitignore                         # .env, /data, .pdf under seed/real, pycache
│
├── app/
│   ├── init.py
│   ├── main.py                        # FastAPI app, routers, startup (migrate), /healthz
│   ├── config.py                      # Settings from .env (pydantic-settings)
│   ├── db.py                          # connection pool, run_migrations(), query helpers
│   ├── access.py                      # /c/{token} → session; PIN; admin basic auth; CSRF; rate limit
│   ├── i18n.py                        # t(key, lang), bilingual() helper
│   ├── seed.py                        # load rulebook + seed/ into DB; demo-reset entry point
│   ├── events.py                      # log_event() — the only writer to events
│   │
│   ├── routes/
│   │   ├── init.py
│   │   ├── pages.py                   # GET /, /chat, /schemes, /applications, /timeline, /approvals, /documents
│   │   ├── chat.py                    # POST /chat, POST /answers
│   │   ├── documents.py               # POST /documents, GET /documents/{id}/file
│   │   ├── approvals.py               # POST /approvals/{id}/approve|reject
│   │   ├── webhooks.py                # POST /webhooks/telegram
│   │   ├── cases.py                   # GET /cases/{id} (case file JSON, slice 2), POST /c/rotate
│   │   └── admin.py                   # GET /admin/runs, /admin/jobs, /admin/actions
│   │
│   ├── brain/
│   │   ├── init.py
│   │   ├── runner.py                  # run_brain(): loop, 12-step cap, usage → brain_runs, budget check
│   │   ├── casefile.py                # build_case_file(case_id)
│   │   ├── claude.py                  # client, SYSTEM_BLOCKS, cache_control, forced_tool_call(), citations_call()
│   │   ├── prompts/
│   │   │   ├── system.md              # role, hard rules, tool policy; {{RULEBOOK_SUMMARY}} slot
│   │   │   ├── explain_match.md
│   │   │   ├── document_check.md      # "return only last 4 digits of any ID"
│   │   │   ├── interpret_response.md
│   │   │   ├── draft_message.md
│   │   │   └── report_narrative.md
│   │   └── tools/
│   │       ├── init.py            # TOOLS list (schemas) + dispatch(name, input, ctx)
│   │       ├── base.py                # Ctx dataclass, ToolError, pydantic validation helper
│   │       ├── update_profile.py
│   │       ├── match_schemes.py
│   │       ├── explain_match.py
│   │       ├── ask_user.py
│   │       ├── list_required_documents.py
│   │       ├── check_document.py
│   │       ├── build_project_report.py
│   │       ├── prefill_form.py
│   │       ├── create_application.py
│   │       ├── transition_application.py
│   │       ├── interpret_response.py
│   │       ├── draft_message.py
│   │       ├── request_submit.py
│   │       ├── schedule.py
│   │       └── finish_run.py
│   │
│   ├── rules/
│   │   ├── init.py
│   │   ├── loader.py                  # read rulebook/schemes/.yaml, hash, validate shape
│   │   ├── facts.py                   # build_facts(): derived facts (age, special_category)
│   │   ├── engine.py                  # eval_node, evaluate, calc_benefit, leaves
│   │   └── cli.py                     # python -m app.rules.cli --profile meena
│   │
│   ├── tracker/
│   │   ├── init.py
│   │   └── states.py                  # TRANSITIONS table, transition(), guards, IllegalTransition
│   │
│   ├── clock/
│   │   ├── init.py
│   │   ├── worker.py                  # claim loop, backoff, sweeper; python -m app.clock.worker
│   │   ├── schedule.py                # schedule() — the one place DEMO_CLOCK_SCALE applies
│   │   ```
│   │   └── handlers.py                # status_check, chase, doc_nudge, deadline_reminders,
│   │                                  # retry_cycle, brain_run, offer_domain, retention_cleanup
│   │
│   ├── guard/
│   │   ├── init.py
│   │   ├── actions.py                 # create_pending(), approve(), reject(), execute(), expire(); short codes
│   │   └── scanner.py                 # BANNED patterns, unknown_scheme_mentions(), scan()
│   │
│   ├── docs/
│   │   ├── init.py
│   │   ├── pipeline.py                # validate → strip EXIF → hash → encrypt → vision → mask → name match → status
│   │   ├── vault.py                   # encrypt(), decrypt(), path_for(case_id, doc_id)
│   │   └── masking.py                 # mask_ids(), name_mismatch()
│   │
│   ├── finance/
│   │   ├── init.py
│   │   └── project_report.py          # compute(): totals, EMI, break-even, DSCR, 3-year table; render_pdf()
│   │
│   ├── forms/
│   │   ├── init.py
│   │   ├── prefill.py                 # apply mapping → pack PDF + steps
│   │   └── mappings/
│   │       └── pmegp.yaml
│   │
│   ├── status/
│   │   ├── init.py
│   │   ├── base.py                    # StatusChecker protocol, CheckResult, get_checker(scheme)
│   │   ├── user_forwards.py
│   │   ├── manual.py
│   │   └── scrape.py                  # stub, raises NotImplementedError
│   │
│   ├── mouth/
│   │   ├── init.py
│   │   ├── base.py                    # Channel protocol, get_channel(name)
│   │   ├── telegram.py                # send, send_document, verify_webhook, parse_update, /start binding
│   │   ├── email.py                   # send via provider API, always CC user for bank/officer
│   │   ├── whatsapp.py                # stub
│   │   └── templates.py               # templated (no-LLM) messages: nudges, reminders, "forward the SMS"
│   │
│   ├── pdf/
│   │   ├── init.py
│   │   └── render.py                  # Jinja HTML → WeasyPrint bytes; font setup; English fallback flag
│   │
│   ├── templates/
│   │   ├── base.html                  # layout, lang toggle, HTMX include
│   │   ├── dashboard.html
│   │   ├── chat.html                  # message list + input; partials/chat_messages.html
│   │   ├── schemes.html               # cards; partials/citation_popover.html
│   │   ├── documents.html             # upload + status list
│   │   ├── applications.html
│   │   ├── application_detail.html    # parcel view + that application's events
│   │   ├── approvals.html             # pending actions with Approve / Reject
│   │   ├── timeline.html
│   │   ├── pin.html                   # optional PIN prompt
│   │   ├── error.html                 # "I'll get back to you shortly"
│   │   ├── admin/
│   │   │   ├── runs.html
│   │   │   ├── jobs.html
│   │   │   └── actions.html
│   │   ├── pdf/
│   │   │   ├── project_report.html
│   │   │   └── prefill_pack.html
│   │   └── email/
│   │       ├── followup_bank.html
│   │       └── followup_officer.html
│   │
│   ├── static/
│   │   ├── app.css
│   │   ├── htmx.min.js
│   │   └── fonts/NotoSansDevanagari-Regular.ttf
│   │
│   └── locales/
│       ├── hi.json
│       └── en.json
│
├── rulebook/
│   ├── README.md                      # how to add a scheme; YAML schema; last_verified policy
│   ├── schemes/
│   │   ├── stand_up_india.yaml
│   │   ├── pmegp.yaml
│   │   ├── cgtmse.yaml
│   │   ├── mudra.yaml
│   │   └── udyogini_ka.yaml
│   └── sources/
│       ├── stand_up_india/
│       │   ├── guidelines.pdf
│       │   └── pages/01.txt … NN.txt  # made by make extract-pages (pdftotext, split per page)
│       ├── pmegp/        (same shape)
│       ├── cgtmse/       (same shape)
│       ├── mudra/        (same shape)
│       └── udyogini_ka/  (same shape)
│
├── seed/
│   ├── personas.yaml                  # meena (fresh), ravi (under_review_silent), fatima (rejected_fixable)
│   ├── histories/
│   │   ├── ravi.yaml                  # ~15 backdated events + seeded jobs (chase at T+2 min)
│   │   └── fatima.yaml                # ~10 events ending REJECTED
│   ├── portal_messages.yaml           # 15 texts with expected intents
│   ├── contacts.yaml                  # "bank branch", "nodal officer" → your inboxes
│   ├── costs/
│   │   ├── meena.yaml
│   │   ├── ravi.yaml
│   │   └── fatima.yaml
│   ├── conversations/
│   │   └── meena.md                   # rehearsal script for live onboarding
│   └── showcase.yaml                  # which personas/stages demo-reset loads; DEMO_CLOCK_SCALE value
│
├── sql/
│   ├── 001_init.sql                   # all tables + indexes from LLD §5
│   └── 002_seed_schemes_hash.sql      # (example of a later migration)
│
├── scripts/
│   ├── extract_pages.sh               # pdftotext -layout per page → rulebook/sources/*/pages/
│   ├── demo_reset.sh                  # truncate → seed → re-arm jobs → print case links
│   ├── backup.sh                      # pg_dump | age → /backups, copy off-box   (cron)
│   ├── retention_cleanup.sh           # calls app job                               (cron)
│   └── make_specimen_docs.py          # Pillow-generated "SPECIMEN / DEMO" fixture images
│
├── tests/
│   ├── conftest.py                    # test DB, fixtures, fake Claude client
│   ├── rules/
│   │   ├── profiles.yaml              # 30 profiles × 5 schemes with expected result + deciding rule
│   │   └── test_rules.py
│   ├── finance/
│   │   └── test_project_report.py
│   ├── tracker/
│   │   └── test_transitions.py
│   ├── guard/
│   │   ├── phrases.yaml               # 20 banned sentences hi/en, 5 invented scheme names
│   │   ├── test_scanner.py
│   │   └── test_idempotency.py
│   ├── docs/
│   │   ├── fixtures/                  # 20 specimen images + labels.yaml
│   │   ├── test_pipeline.py
│   │   └── test_masking.py
│   ├── clock/
│   │   ├── test_claim.py              # two workers, no double-claim
│   │   └── test_demo_clock.py
│   └── e2e/
│       ├── test_inbound.py            # 15 portal messages → intents
│       └── test_happy_path.py         # onboarding → SUBMITTED → SMS → QUERY_RAISED → RESUBMITTED
│
└── data/                              # runtime only, on the VPS volume, git-ignored
├── vault/<case_id>/<doc_id>.bin   # encrypted documents
└── backups/