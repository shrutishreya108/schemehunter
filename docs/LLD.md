# Scheme Hunter: Low-Level Design v1.1 (7-day demo build)
## 0. Status and changes from v1.0
This version is scoped to what ships in 7 days and is demoable at every step. Changes from the earlier LLD:

Access is a per-case secret link, not magic-link login. Helper role deferred.
Telegram + email only. WhatsApp adapter stubbed, not built.
Status checking is user_forwards and manual only. Playwright scraper stubbed, not built.
Five schemes, one state. Two document types get vision checks (Aadhaar, bank passbook).
Added: demo clock, make demo-reset, seed data inventory, three demo personas, rollout slices, demo script, cut list.
Admin is one password-protected table page. Backups and retention are cron lines.
Rule that governs every section: AI does the language, code does the truth. Claude talks, reads, drafts, explains. Code decides eligibility, does money math, keeps dates and state, and holds the approval gate.

## 1. Scope
In (demo build)

Onboarding chat (Hindi/English) → eligibility via rules engine → cited explanations
Document upload with vision checks, masking, name matching, encrypted storage
Project report PDF with computed numbers; prefill pack for one scheme (PMEGP)
Application tracker (state machine) with append-only timeline
Scheduler: status checks (via forwarded messages), chase, nudges, deadline reminders, retry next cycle
Guard: every outbound message and every "submit now" instruction is human-approved
Telegram (user channel) and email (banks/officers, CC user)
Deployed on a GoDaddy VPS under a GoDaddy domain with HTTPS
Out (stubbed or deferred)

Autonomous portal submission (never), payments (never), promises of money (never)
Portal scraping, WhatsApp, helper/NGO role, voice input, scheme-change watcher, GoDaddy domain offer (feature flag, stretch)
## 2. Stack
Python 3.12 · FastAPI · Jinja2 + HTMX · Postgres 16 · Anthropic Python SDK · WeasyPrint · rapidfuzz · cryptography · Docker Compose · Caddy

## 3. Repository layout

schemehunter/
Makefile                      # up, migrate, seed, demo-reset, test
docker-compose.yml  Caddyfile  .env.example
app/
main.py  config.py  db.py  access.py  i18n.py  seed.py
brain/
runner.py  casefile.py  prompts/system.md
tools/**init**.py          # TOOLS list + dispatch()
tools/<one file per tool>.py
rules/engine.py  rules/facts.py  rules/cli.py
tracker/states.py
clock/worker.py  clock/handlers.py
guard/actions.py  guard/scanner.py
docs/pipeline.py  docs/vault.py
finance/project_report.py
forms/prefill.py  forms/mappings/pmegp.yaml
status/base.py  status/user_forwards.py  status/manual.py  status/scrape.py (stub)
mouth/base.py  mouth/telegram.py  mouth/email.py  mouth/whatsapp.py (stub)
templates/  static/  locales/hi.json  locales/en.json
rulebook/
schemes/{stand_up_india,pmegp,cgtmse,mudra,udyogini_ka}.yaml
sources/<scheme_id>/guidelines.pdf
sources/<scheme_id>/pages/NN.txt
seed/
personas.yaml  histories/*.yaml  portal_messages.yaml  contacts.yaml  costs/*.yaml
conversations/meena.md
sql/001_init.sql ...
tests/rules/  tests/docs/fixtures/  tests/finance/  tests/tracker/  tests/guard/  tests/clock/  tests/e2e/
## 4. Configuration (.env)

APP_BASE_URL=https://schemehunter.example.in
DATABASE_URL=postgresql://app:***@postgres:5432/schemehunter
SESSION_SECRET=<random 32+ bytes>
VAULT_KEY=<32 bytes base64>                 # encrypts documents
ADMIN_PASSWORD=<random>
ANTHROPIC_API_KEY=...
MODEL_REASON=<newest Sonnet>                 # e.g. claude-sonnet-4-5
MODEL_CHEAP=<newest Haiku>                   # e.g. claude-haiku-4-5
DAILY_TOKEN_BUDGET=3000000
DEMO_CLOCK_SCALE=1                           # 1 = real time; 1440 = 1 day → 1 minute
TELEGRAM_BOT_TOKEN=...  TELEGRAM_WEBHOOK_SECRET=...
EMAIL_PROVIDER_KEY=...  EMAIL_FROM=help@schemehunter.example.in
CURATOR_ALERT_EMAIL=you@...
FEATURE_DOMAIN_OFFER=false  GODADDY_API_KEY=  GODADDY_API_SECRET=
RETENTION_DAYS=90
Secrets live only in .env on the VPS. Never in git.

## 5. Database schema
events is append-only. Everything else is normal.

sql

create table cases (
id uuid primary key default gen_random_uuid(),
display_name text,                              -- first name only, for greetings
language text not null default 'hi',
state_code text,                                -- 'KA'
access_token_hash text unique not null,         -- sha256 of the secret link token
pin_hash text,                                  -- optional 4-digit PIN
telegram_chat_id text unique, email text,
state text not null default 'ONBOARDING',       -- ONBOARDING|ACTIVE|CLOSED
consent_version text, consent_at timestamptz,
created_at timestamptz not null default now()
);

create table profile_answers (
case_id uuid references cases(id) on delete cascade,
key text, value jsonb,
source text not null,                           -- typed|document|derived|seed
updated_at timestamptz not null default now(),
primary key (case_id, key)
);

create table documents (
id uuid primary key default gen_random_uuid(),
case_id uuid not null references cases(id) on delete cascade,
doc_type text,                                  -- aadhaar|passbook|photo|quotation|caste_cert|other
file_path text not null, sha256 text not null, mime text not null,
check_status text not null default 'PENDING',   -- PENDING|OK|RETAKE|PROBLEM
check_result jsonb, extracted jsonb,            -- masked fields only
created_at timestamptz not null default now(),
unique (case_id, sha256)
);

create table schemes (
id text primary key, name jsonb not null, spec jsonb not null,
spec_hash text not null, last_verified date
);

create table matches (
case_id uuid references cases(id) on delete cascade, scheme_id text references schemes(id),
result text not null,                           -- FIT|NO_FIT|NEEDS_INFO
rule_results jsonb not null, missing_facts jsonb, benefit jsonb,
explanation jsonb,                              -- {hi, en, citations:[{page, cited_text}]}
computed_at timestamptz not null default now(),
primary key (case_id, scheme_id)
);

create table applications (
id uuid primary key default gen_random_uuid(),
case_id uuid not null references cases(id) on delete cascade,
scheme_id text not null references schemes(id),
state text not null, ref_number text, deadline date, cycle text,
last_movement_at timestamptz not null default now(),
created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);

create table events (
id bigserial primary key,
case_id uuid not null, application_id uuid,
actor text not null,                            -- user|agent|clock|system
type text not null,
summary jsonb not null,                         -- {"hi": "...", "en": "..."}
payload jsonb,
created_at timestamptz not null default now()
);
create index on events (case_id, created_at);

create table pending_actions (
id uuid primary key default gen_random_uuid(),
short_code char(4) not null,                    -- shown in Telegram: "YES 4471"
case_id uuid not null, application_id uuid,
kind text not null,                             -- send_email|send_chat|instruct_submit|register_domain
payload jsonb not null,
status text not null default 'PENDING',         -- PENDING|APPROVED|EXECUTED|REJECTED|FAILED|EXPIRED
idempotency_key text unique not null,
created_at timestamptz not null default now(),
decided_at timestamptz, executed_at timestamptz, error text
);
create unique index on pending_actions (case_id, short_code) where status = 'PENDING';

create table messages (
id uuid primary key default gen_random_uuid(),
case_id uuid, direction text not null,          -- in|out
channel text not null,                          -- telegram|email|web
external_id text unique,                        -- provider id; stops duplicate webhooks
from_addr text, to_addr text, body text, attachments jsonb,
processed boolean not null default false,
created_at timestamptz not null default now()
);

create table jobs (
id bigserial primary key,
type text not null, case_id uuid, application_id uuid, payload jsonb,
run_at timestamptz not null,
status text not null default 'PENDING',         -- PENDING|RUNNING|DONE|DEAD
attempts int not null default 0, last_error text, locked_at timestamptz,
created_at timestamptz not null default now()
);
create index on jobs (status, run_at);

create table brain_runs (
id uuid primary key default gen_random_uuid(),
case_id uuid, trigger text, task text, steps int, status text, error text,
input_tokens int, output_tokens int, cache_read_tokens int,
started_at timestamptz, ended_at timestamptz
);

create table schema_migrations (version text primary key, applied_at timestamptz default now());
## 6. Rule Book and rules engine
### 6.1 Scheme file (one YAML per scheme)
yaml

id: pmegp
name: { en: "PM Employment Generation Programme", hi: "प्रधानमंत्री रोजगार सृजन कार्यक्रम" }
level: central
authority: KVIC
benefit: { type: subsidy }                  # subsidy | loan | guarantee
apply_mode: portal                          # portal | bank | office
portal_url: https://www.kviconline.gov.in/pmegpeportal/
status_mode: user_forwards                  # user_forwards | manual | scrape(stub)
window: rolling                             # or { opens: 2026-12-01, closes: 2026-12-31 }
documents: [aadhaar, photo, passbook, project_report, quotation, caste_cert_if_special, education_cert_if_cost_gt_10l]
source_pdf: guidelines.pdf
last_verified: 2026-11-14

eligibility:
all:
- { id: age,      fact: age,            op: gte, value: 18,  page: 3 }
- { id: new_only, fact: business_stage, op: eq,  value: new, page: 3 }
- id: cost_cap
page: 4
any:
- all: [ {fact: sector, op: eq, value: manufacturing}, {fact: project_cost, op: lte, value: 5000000} ]
- all: [ {fact: sector, op: eq, value: service},       {fact: project_cost, op: lte, value: 2000000} ]
- id: education
page: 4
when: { fact: project_cost, op: gt, value: 1000000 }
then: { fact: education_level, op: gte_level, value: "8th" }

benefit_calc:
subsidy_percent:
- when: { all: [ {fact: area, op: eq, value: rural}, {fact: special_category, op: eq, value: true} ] }
value: 35
- when: { fact: area, op: eq, value: rural }
value: 25
- when: { fact: special_category, op: eq, value: true }
value: 25
- default: 15
own_contribution_percent:
- when: { fact: special_category, op: eq, value: true }
value: 5
- default: 10
CGTMSE is modeled as benefit.type: guarantee, apply_mode: bank (it rides on a bank loan). Stand-Up India is loan, portal. Mudra is loan, bank. Udyogini (Karnataka) is subsidy, office.

### 6.2 Facts
python

def build_facts(answers: dict) -> dict:
f = dict(answers)
f["age"] = years_between(f.get("dob"), today()) if f.get("dob") else None
f["special_category"] = f.get("gender") == "female" or f.get("category") in {"SC", "ST"}
return f
### 6.3 Evaluator
python

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"
EDU_ORDER = ["none", "5th", "8th", "10th", "12th", "graduate"]
OPS = {"eq": lambda a,b: a==b, "in": lambda a,b: a in b, "gt": lambda a,b: a>b,
"gte": lambda a,b: a>=b, "lte": lambda a,b: a<=b,
"gte_level": lambda a,b: EDU_ORDER.index(a) >= EDU_ORDER.index(b)}

def eval_node(node, facts):
if "all" in node:  return combine_all([eval_node(n, facts) for n in node["all"]])
if "any" in node:  return combine_any([eval_node(n, facts) for n in node["any"]])
if "when" in node:
c = eval_node(node["when"], facts)
if c == FAIL: return PASS
if c == UNKNOWN: return UNKNOWN
return eval_node(node["then"], facts)
v = facts.get(node["fact"])
if v is None: return UNKNOWN
return PASS if OPS[node["op"]](v, node["value"]) else FAIL

def combine_all(rs): return FAIL if FAIL in rs else (UNKNOWN if UNKNOWN in rs else PASS)
def combine_any(rs): return PASS if PASS in rs else (UNKNOWN if UNKNOWN in rs else FAIL)

def evaluate(scheme, facts):
per_rule = {r["id"]: eval_node(r, facts) for r in scheme["eligibility"]["all"]}
overall = combine_all(list(per_rule.values()))
return {
"result": {"PASS": "FIT", "FAIL": "NO_FIT", "UNKNOWN": "NEEDS_INFO"}[overall],
"rules": per_rule,
"missing_facts": sorted({l["fact"] for l in leaves(scheme) if facts.get(l["fact"]) is None}),
"benefit": calc_benefit(scheme["benefit_calc"], facts),
}
NEEDS_INFO drives the next question.
profiles.yaml
holds 30 profiles with expected result and deciding rule per scheme; must be 100%.

## 7. Brain (agent)
### 7.1 Run lifecycle

trigger (chat message | approval | job) → run_brain(case_id, task, trigger)

1. case_file = build_case_file(case_id)
2. messages = [user: "TASK: ...\n\nCASE FILE: {json}"]
3. loop ≤ 12 steps: call Claude → run tool calls → append results → until finish_run
4. write brain_runs (steps, tokens, status)
### 7.2 Case file (what Claude sees; never images, never full ID numbers, never full history)
python

{
"today": "2026-11-16", "language": "hi", "state_code": "KA",
"profile": {...},                                   # ~20 keys
"missing_facts": [...],
"documents": [{"id","doc_type","check_status","issue"}],
"matches": [{"scheme_id","result","benefit_summary"}],
"applications": [{"id","scheme_id","state","ref_number","deadline","days_since_movement"}],
"pending_actions": [{"id","kind","one_line"}],
"recent_events": ["..." x 10],
}
### 7.3 System prompt (`system.md`), in order
Role: follow-through assistant for Indian government scheme applications.
Hard rules, verbatim:
Only mention schemes in the Rule Book list below.
Never decide eligibility yourself. Call match_schemes; explain its result.
"You qualify" sentences come only from explain_match, which carries citations.
Never promise approval, sanction, or money. Allowed: "you qualify to apply."
Never send anything. Use draft_message; a human approves.
One question at a time. User's language. Simple words.
Always end with finish_run.
Tool order policy (match → explain → documents → report → pack → request_submit).
Rule Book summary (id, name, benefit one-liner, apply mode) — cached block.
### 7.4 Claude API usage
python

SYSTEM_BLOCKS = [
{"type": "text", "text": SYSTEM_TEXT},
{"type": "text", "text": RULEBOOK_SUMMARY, "cache_control": {"type": "ephemeral"}},
]
TOOLS[-1]["cache_control"] = {"type": "ephemeral"}        # caches all tool definitions

resp = client.messages.create(model=MODEL_REASON, max_tokens=2000,
system=SYSTEM_BLOCKS, tools=TOOLS, messages=messages)
Structured results: define a tool with the JSON schema and force it: tool_choice={"type": "tool", "name": "..."}.
Citations: explain_match passes per-page text documents with "citations": {"enabled": true}; response blocks carry cited_text and the document title (which encodes the page).
Vision: image content block with base64 JPEG/PNG.
Caching: system + tools + guideline pages are static → cached; the case file is per run → not cached.
### 7.5 The loop
python

def run_brain(case_id, task, trigger):
run = start_run(case_id, task, trigger)
ctx = Ctx(case_id=case_id, run_id=run.id)
messages = [{"role": "user", "content": f"TASK: {task}\n\nCASE FILE:\n{dumps(build_case_file(case_id))}"}]
finished = False
for _ in range(12):
resp = call_claude(messages); add_usage(run, resp.usage)
messages.append({"role": "assistant", "content": resp.content})
if resp.stop_reason != "tool_use": break
results = []
for b in resp.content:
if b.type != "tool_use": continue
out = dispatch(b.name, b.input, ctx)                     # validated; exceptions → {"error": "..."}
log_event(case_id, "agent", "TOOL_CALLED", payload={"tool": b.name, "ok": "error" not in out})
results.append({"type": "tool_result", "tool_use_id": b.id, "content": dumps(out)})
finished |= (b.name == "finish_run")
messages.append({"role": "user", "content": results})
if finished: break
if not finished:
log_event(case_id, "system", "RUN_STUCK", bilingual("I got stuck; will try again later")); alert_curator(run)
end_run(run)
### 7.6 Model routing and budget
Call	Model
Agent loop, explain_match, document vision	MODEL_REASON
Inbound message intent, outbound unknown-scheme check	MODEL_CHEAP
DAILY_TOKEN_BUDGET checked before every run; over budget, only deadline-related runs proceed, others re-queue for tomorrow.

## 8. Tools
Each tool: SCHEMA + handle(input, ctx). Side-effect tools never act; they create pending actions.

Tool	Input	Does	Returns
update_profile	{key, value}	upsert profile_answers	ok
match_schemes	{}	evaluate() for all schemes in state + central; upsert matches	per-scheme result, deciding rules, missing facts, benefit
explain_match	{scheme_id}	citations call over page texts; store explanation	{hi, en, citations}
ask_user	{question_hi, question_en, expected_fact}	record question; shown in chat	ok
list_required_documents	{scheme_id}	resolve conditionals against facts	[{doc_type, have, status}]
check_document	{document_id}	§12 pipeline	{doc_type_detected, status, issues, fields_masked}
build_project_report	{scheme_id}	§13.1; PDF stored as document	{pdf_document_id, totals}
prefill_form	{application_id}	§13.2	{pack_document_id, steps}
create_application	{scheme_id}	row DISCOVERED → ELIGIBLE if FIT	{application_id}
transition_application	{application_id, to_state, reason}	§9 transition()	ok / error
interpret_response	{message_id} or {text}	forced schema below; apply if confident and legal	result or clarification
draft_message	{purpose, audience, application_id, points[]}	forced schema → scanner → pending action	{pending_action_id, short_code}
request_submit	{application_id, pack_document_id}	pending action instruct_submit; state → AWAITING_HUMAN_SUBMIT	ok
schedule	{type, days_from_now, application_id?}	insert job (demo clock applied)	{job_id}
finish_run	{user_message, internal_note, next_check_in_days}	ends run; message shown to user	ok
Forced schemas:

json

// interpret_response
{"intent": "status_update|query_raised|rejected|sanctioned|question|approval_reply|other",
"application_id_guess": "uuid|null", "ref_number": "string|null", "reason": "string|null",
"missing_docs": ["string"], "deadline": "YYYY-MM-DD|null", "confidence": 0.0}
// draft_message
{"channel": "email|telegram", "to": "string", "subject": "string|null", "body": "string", "language": "hi|en"}
// document_check_result → see §12.2
Apply rule for interpret_response: state changes only if confidence >= 0.7 and the transition is legal; otherwise return {"needs_clarification": true, "question": "..."}.

## 9. Tracker
python

TRANSITIONS = {
("DISCOVERED","ELIGIBLE"):              (["agent"],         [sched("deadline_reminders")]),
("ELIGIBLE","DOCS_PENDING"):            (["agent"],         [sched("doc_nudge", days=1)]),
("DOCS_PENDING","DOCS_READY"):          (["agent"],         []),                  # guard: all required docs OK
("DOCS_READY","FORM_READY"):            (["agent"],         []),
("FORM_READY","AWAITING_HUMAN_SUBMIT"): (["agent"],         [pending("instruct_submit")]),
("AWAITING_HUMAN_SUBMIT","SUBMITTED"):  (["user"],          [sched("status_check", days=1)]),  # guard: ref_number
("SUBMITTED","UNDER_REVIEW"):           (["agent","clock"], [sched("status_check", days=3), sched("chase", days=10)]),
("UNDER_REVIEW","QUERY_RAISED"):        (["agent"],         [sched("doc_nudge", days=1)]),
("QUERY_RAISED","DOCS_PENDING"):        (["agent"],         []),
("DOCS_PENDING","RESUBMITTED"):         (["user"],          [sched("status_check", days=1)]),
("RESUBMITTED","UNDER_REVIEW"):         (["agent","clock"], []),
("UNDER_REVIEW","SANCTIONED"):          (["agent","user"],  [sched("offer_domain", days=0)]),   # flag-gated
("UNDER_REVIEW","REJECTED"):            (["agent","user"],  [sched("brain_run", task="analyze rejection")]),
("REJECTED","RETRY_NEXT_CYCLE"):        (["agent"],         [sched("retry_cycle", at=next_window)]),
("REJECTED","CLOSED"):                  (["agent","user"],  []),
("<any pre-submit>","MISSED"):          (["clock"],         [sched("retry_cycle", at=next_window)]),
}

def transition(app, to, actor, reason, payload=None):
rule = lookup(app.state, to)
if not rule or actor not in rule.actors: raise IllegalTransition(app.state, to, actor)
run_guards(app, to)
db.execute("update applications set state=%s, last_movement_at=now(), updated_at=now() where id=%s", (to, app.id))
log_event(app.case_id, actor, "STATE_CHANGED", summary(app, to, reason), payload, application_id=app.id)
for effect in rule.effects: effect(app)
The timeline page is select * from events where case_id=? order by created_at.

## 10. Alarm Clock
### 10.1 Demo clock
python

def schedule(type, days_from_now=0, at=None, **kw):
run_at = at or now() + timedelta(days=days_from_now) / settings.DEMO_CLOCK_SCALE
insert jobs(type, run_at, ...)
Applied in exactly one place. Production: DEMO_CLOCK_SCALE=1. Demo: 1440 (1 day → 1 minute). Say so on stage.

### 10.2 Claim and loop
sql

update jobs set status='RUNNING', locked_at=now(), attempts=attempts+1
where id = (select id from jobs where status='PENDING' and run_at <= now()
order by run_at for update skip locked limit 1)
returning *;
python

BACKOFF = [60, 600, 3600, 6*3600, 24*3600]
while True:
job = claim_next_job()
if not job: sleep(5); continue
try: HANDLERSjob.type; mark(job, "DONE")
except Exception as e:
if job.attempts >= 5: mark(job, "DEAD", error=str(e)); alert_curator(job)
else: reschedule(job, now() + BACKOFF[job.attempts-1], error=str(e))

# sweeper every 10 min: RUNNING older than 30 min → PENDING

### 10.3 Handlers (MVP)
Type	Behavior	Next
status_check	checker by status_mode; text → interpret_response → transition	+1 day (+3 if no movement)
chase	if days_since_movement >= 10 → run_brain("draft follow-up for application X")	+7 days
deadline_reminders	templated message at T-7 and T-2 if not submitted	once each
doc_nudge	templated message listing missing docs, max once per 24h	daily until DOCS_READY
retry_cycle	clone application for next window, state ELIGIBLE, reuse docs	—
brain_run	run_brain(case_id, payload.task, "clock")	—
offer_domain	only if FEATURE_DOMAIN_OFFER; pending action with 3 available names via GoDaddy Domains API	—
retention_cleanup	delete document files for CLOSED cases older than RETENTION_DAYS	daily (cron)
## 11. Guard
Lifecycle: PENDING → APPROVED → EXECUTED; or REJECTED; or EXPIRED after 7 days (agent informed); FAILED after 3 executor retries.

Idempotency: idempotency_key = sha256(kind | case_id | application_id | normalized_body); unique column; duplicate inserts dropped. Executor sets executed_at before calling the provider so a crash can't resend.

Short code: 4 digits, unique among PENDING actions per case; used in Telegram replies YES 4471 / NO 4471.

Scanner (every outbound body before it becomes a pending action):

python

BANNED = [r"\bguarantee", r"\bwill be approved", r"\byou will (get|receive)\b",
r"निश्चित", r"पक्का", r"गारंटी", r"ज़रूर मिलेगा"]
def scan(body):
hits = [p for p in BANNED if re.search(p, body, re.I)]
unknown = unknown_scheme_mentions(body)   # MODEL_CHEAP lists scheme names; subtract Rule Book names + aliases
return {"ok": not hits and not unknown, "hits": hits, "unknown_schemes": unknown}
Fail → reasons returned to Claude for rewrite, max 2; then saved as PROBLEM for the curator.

Endpoints: POST /approvals/{id}/approve|reject (session must own the case); Telegram YES|NO <code>.

## 12. Documents and Vault
### 12.1 Pipeline

POST /documents (multipart; doc_type optional)

1. size ≤ 10 MB; mime ∈ {jpeg, png, pdf}; strip EXIF (Pillow re-save)
2. sha256; existing (case_id, sha256) → return existing row
3. encrypt → /data/vault/<case_id>/<doc_id>.bin ; insert documents(PENDING)
4. if doc_type ∈ {aadhaar, passbook}: vision check inline (user is waiting); else basic readability only → OK
### 12.2 Vision call (forced tool document_check_result)
json

{"doc_type_detected": "aadhaar|pan|passbook|caste_cert|photo|quotation|education_cert|other",
"readable": true, "quality_issues": ["blur","glare","cut_off","too_dark"],
"fields": {"name_native": "", "name_latin": "", "dob": "YYYY-MM-DD|null",
"address_line": "", "district": "", "id_last4": "", "bank_name": "", "ifsc": ""},
"confidence": 0.0}
Prompt instructs: "Return only the last four digits of any ID number." Code additionally masks: re.sub(r"\b\d{4}\s?\d{4}\s?(\d{4})\b", r"XXXX XXXX \1", s) over every string field. Two layers.

### 12.3 Decision
python

if not readable or "cut_off" in issues:           status = "RETAKE"
elif detected != expected:                        status = "PROBLEM"   # "looks like PAN, not Aadhaar"
elif name_mismatch(case, fields):                 status = "PROBLEM"   # token_set_ratio < 85 vs other docs
else:                                             status = "OK"
### 12.4 Vault
python

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
def encrypt(b): n = os.urandom(12); return n + AESGCM(KEY).encrypt(n, b, None)
def decrypt(blob): return AESGCM(KEY).decrypt(blob[:12], blob[12:], None)
GET /documents/{id}/file: access check → decrypt in memory → stream with Cache-Control: no-store. Nothing decrypted touches disk.

## 13. Project report and prefill
### 13.1 Money math (`project_report.py`)
Inputs as facts: capex[], working_capital, own_pct, subsidy_pct (from benefit_calc), annual_rate, tenure_months, units_per_month, price, variable_cost, fixed_costs, growth_pct.

python

total_cost   = sum(capex) + working_capital
subsidy      = total_cost * subsidy_pct / 100
own          = total_cost * own_pct / 100
loan         = total_cost - subsidy - own
r            = annual_rate / 12 / 100
emi          = loan * r * (1+r)**n / ((1+r)**n - 1)
cm_per_unit  = price - variable_cost
break_even   = fixed_costs / cm_per_unit                      # units per month
profit_m     = units * cm_per_unit - fixed_costs
dscr         = (profit_m + interest_part(emi, loan, r)) / emi  # ≥ 1.5 comfortable
table        = project_3_years(units, growth_pct, ...)
Claude writes narrative sections only (business description, market, location) via a text-only forced schema. Template
project_report.html
embeds Noto Sans Devanagari; if rendering fights you, ship English.

### 13.2 Prefill (PMEGP only)
yaml

# forms/mappings/pmegp.yaml

fields:
applicant_name:  { from: name_latin }
date_of_birth:   { from: dob, format: "DD/MM/YYYY" }
social_category: { from: category, map: { SC: "SC", ST: "ST", OBC: "OBC", GEN: "General" } }
project_cost:    { from: total_cost, format: inr }
unit_address:    { join: [address_line, district, state_name], sep: ", " }
steps:

- { hi: "पोर्टल खोलें और 'Online Application' चुनें", en: "Open the portal and choose 'Online Application'" }
Output: an HTML→PDF "pack" mirroring the form's sections plus the step list. The official PDF is not edited.
## 14. Status checkers
python

class StatusChecker(Protocol):
def check(self, app) -> CheckResult      # kind: "text" | "nothing" | "ask_user"

class UserForwardsChecker:
def check(self, app):
m = latest_unprocessed_inbound(app.case_id, mentions=app.ref_number or app.scheme_id)
return CheckResult("text", m.body, message_id=m.id) if m else CheckResult("nothing")

class ManualChecker:
def check(self, app): return CheckResult("nothing")      # chase handles follow-ups

class ScrapeChecker:                                           # stub; raises NotImplemented
...
status_check handler: text → interpret_response → transition; nothing after 2 checks → templated "please forward the latest SMS from the portal."

## 15. Mouth
python

class Channel(Protocol):
def send(self, to: str, text: str, attachments: list[Path] = ()) -> str   # provider message id
Telegram: sendMessage / sendDocument. Webhook POST /webhooks/telegram verifies X-Telegram-Bot-Api-Secret-Token. Linking: the case page shows a deep link t.me/<bot>?start=<case_link_token>; /start <token> binds telegram_chat_id.
Email: provider HTTP API from EMAIL_FROM; bank/officer recipients always CC the user; subject carries the ref number.
Inbound normalization: verify → insert messages (duplicate external_id ignored) → resolve case by telegram_chat_id (unknown → "please open your link first") → ^(YES|NO)\s+\d{4}$ → Guard; else enqueue brain_run("interpret inbound message <id>").
## 16. Front Door
### 16.1 Access
Each case has a secret link https://<domain>/c/<token> (32 random bytes, base64url; only sha256(token) stored). Opening it sets a signed session cookie bound to case_id (Secure; HttpOnly; SameSite=Lax, 30 days).
Optional 4-digit PIN (pin_hash) asked once per device.
Rotating the link: POST /c/rotate invalidates the old token.
Curator: /admin/* behind HTTP basic auth with ADMIN_PASSWORD.
Rate limits (Postgres counter): /c/* 20/min per IP, webhooks 60/min.
CSRF token in session for all HTMX posts.
### 16.2 Routes

GET  /c/{token}                 → set session, redirect /
GET  /                          dashboard: next action, applications summary
GET  /chat     POST /chat       sync run ≤ 60 s; else "working on it", result pushed via Telegram
GET  /documents  POST /documents  GET /documents/{id}/file
GET  /schemes                   matches + citation popovers
GET  /applications  GET /applications/{id}
GET  /approvals  POST /approvals/{id}/approve|reject
GET  /timeline
POST /answers                   form fallback for ask_user
POST /webhooks/telegram
GET  /admin/runs  /admin/jobs  /admin/actions      (basic auth)
GET  /healthz
### 16.3 Language
Static UI strings from locales/{hi,en}.json. Code-generated summaries stored as {"hi","en"}; Claude output in cases.language.

## 17. Events, logging, admin
log_event() is the only writer to events. Types: PROFILE_UPDATED, DOC_UPLOADED, DOC_CHECKED, MATCHED, STATE_CHANGED, DRAFT_CREATED, ACTION_APPROVED, ACTION_REJECTED, MESSAGE_SENT, MESSAGE_RECEIVED, STATUS_CHECKED, REMINDER_SENT, TOOL_CALLED, RUN_STUCK.
payload holds ids and small facts only; never names or document text.
Logs: structlog JSON with a redact filter (emails, phones, 12-digit numbers).
Admin pages: plain tables over brain_runs, jobs (DEAD), pending_actions (PROBLEM).
## 18. Deployment and ops
yaml

# docker-compose.yml

services:
caddy:    { image: caddy:2, ports: ["80:80","443:443"], volumes: ["./Caddyfile:/etc/caddy/Caddyfile","caddy_data:/data"] }
web:      { build: ., command: uvicorn app.main:app --host 0.0.0.0 --port 8000, env_file: .env,
volumes: ["vault:/data/vault"], depends_on: [postgres] }
worker:   { build: ., command: python -m app.clock.worker, env_file: .env,
volumes: ["vault:/data/vault","backups:/backups"], depends_on: [postgres] }
postgres: { image: postgres:16, environment: { POSTGRES_DB: schemehunter, POSTGRES_USER: app, POSTGRES_PASSWORD: "${PG_PASSWORD}" },
volumes: ["pgdata:/var/lib/postgresql/data"] }
volumes: { pgdata: {}, vault: {}, backups: {}, caddy_data: {} }

# Caddyfile

schemehunter.example.in {
reverse_proxy web:8000
encode gzip
header { Strict-Transport-Security "max-age=31536000"; X-Content-Type-Options nosniff }
}
DNS: GoDaddy A record → VPS IP. Caddy fetches the certificate on first boot.
Migrations: sql/NNN_*.sql applied in order at web startup, tracked in schema_migrations.
Cron on the VPS host (two lines): nightly pg_dump -Fc | age -r <pubkey> > /backups/$(date +%F).dump.age + copy off-box; daily make retention-cleanup.
make demo-reset: truncate all tables → load schemes, personas, histories, jobs → set DEMO_CLOCK_SCALE → under 10 seconds.
## 19. Failure handling
Situation	Behavior
Claude API error / rate limit	SDK retries 3×; job re-queued with backoff; user sees "I'll get back to you shortly"
Tool raises	error returned to Claude as tool result; it adapts or asks
12-step cap	RUN_STUCK event, curator alert, polite user message
Provider send fails	FAILED after 3 tries; visible in Approvals and Admin
Duplicate webhook	messages.external_id unique → ignored
Double approve	idempotency key → one send
Daily budget hit	non-deadline runs wait until tomorrow
Worker crash mid-job	sweeper resets RUNNING > 30 min
Hindi PDF font failure	English PDF
## 20. Tests

tests/rules      30 profiles × 5 schemes → result + deciding rule; 100% required
tests/finance    EMI, break-even, DSCR, subsidy split vs hand-calculated values
tests/tracker    all legal transitions pass; illegal raise; ref_number guard enforced
tests/guard      banned phrases hi/en caught; invented scheme names caught; idempotency dedupes
tests/docs       20 fixtures → expected status/type; mask leaves no 12-digit number anywhere
tests/clock      two workers never double-claim; backoff sequence; demo clock scaling
tests/e2e        onboarding → FIT → docs → pack → SUBMITTED → forwarded SMS → QUERY_RAISED → re-upload → RESUBMITTED
15 portal_messages fixtures → expected intents
## 21. Seed data and personas
Data	Count	File
Scheme YAMLs	5	rulebook/schemes/
Guideline page texts	~60	rulebook/sources/*/pages/
Test profiles with expectations	30	
profiles.yaml
Personas	3	
personas.yaml
Backdated event histories	Ravi ~15, Fatima ~10	seed/histories/
Specimen document images (clearly marked "SPECIMEN / DEMO"; never realistic fake IDs)	20	tests/docs/fixtures/
Portal SMS/email texts with expected intents	15	
portal_messages.yaml
"Bank" and "nodal officer" contacts (your own inboxes)	3	
contacts.yaml
Cost assumptions + quotations	3	seed/costs/
Scripted onboarding conversation	1	
meena.md
yaml

# seed/personas.yaml

- key: meena # live onboarding
language: hi, state_code: KA, stage: fresh
profile: { name_latin: "Meena Kumari", gender: female, dob: 1991-04-12, category: OBC, sector: service,
business_stage: new, project_cost: 500000, area: urban, education_level: "10th" }
- key: ravi # mid-flight: UNDER_REVIEW, 11 days silent, chase due T+2 min after reset
language: hi, state_code: KA, stage: under_review_silent
profile: { name_latin: "Ravi Kumar", gender: male, dob: 1988-09-02, category: SC, sector: manufacturing,
business_stage: new, project_cost: 1200000, area: rural, education_level: "12th" }
application: { scheme_id: pmegp, ref_number: "PMEGP/KA/2026/04471" }
- key: fatima # REJECTED on fixable reason → analyze → RETRY_NEXT_CYCLE
language: en, state_code: KA, stage: rejected_fixable
profile: { name_latin: "Fatima Begum", gender: female, dob: 1994-01-20, category: GEN, sector: service,
business_stage: new, project_cost: 300000, area: urban, education_level: "graduate" }
application: { scheme_id: mudra, rejection_reason: "income proof not attached" }
yaml

# seed/portal_messages.yaml (3 of 15)

- text: "PMEGP Application PMEGP/KA/2026/04471: Documents required - Income proof. Upload within 7 days."
expect: { intent: query_raised, missing_docs: [income_proof], deadline_days: 7 }
- text: "Your application 04471 has been forwarded to Bank for appraisal."
expect: { intent: status_update }
- text: "Application rejected: project report not as per format."
expect: { intent: rejected }
## 22. Rollout slices (tag each: v0.0 … v0.11)

# Day	Build	Data	Done when

0	1 am	compose, Caddy, /healthz, migrations, Makefile, DNS	—	green padlock on the domain
1	1	Rule Book ×5, rules/, CLI, tests	schemes, page texts, 30 profiles	pytest tests/rules 30/30
2	1 pm	seed.py, casefile.py, GET /cases/{id}	personas	make demo-reset < 10 s, idempotent
3	2	runner, system prompt, match_schemes, explain_match, ask_user, update_profile, finish_run, /chat, /schemes	Rule Book summary, scripted conversation	3 phrasings → same matches; every FIT has a citation
4	3 am	Tracker, create_application, transition_application, /applications, /timeline	histories for Ravi, Fatima	illegal transitions raise; timeline reads like a diary
5	3	Vault, pipeline, check_document, list_required_documents, /documents	20 specimen fixtures	rates reported; mask test green
6	4 am	worker, handlers (status_check, chase, doc_nudge, deadline_reminders), schedule, demo clock	seeded jobs for Ravi/Meena	no double-claims; chase fires on demo clock
7	4	Guard, scanner, draft_message, /approvals, Telegram, email	contacts, banned phrases	YES twice → one email; "guaranteed" bounced
8	5 am	interpret_response, inbound normalization, UserForwardsChecker	15 portal messages	all 15 intents correct; vague → clarification
9	5	finance, PDF, prefill (PMEGP), build_project_report, prefill_form, request_submit	costs, quotations	EMI/break-even to the rupee; "press submit" gate appears
10	5 pm–6	fixes only; /admin/runs	3 real users	≥ 2 real application IDs; 1 real forwarded SMS moved a state
11	7 (freeze noon)	demo-reset re-arms Ravi's chase; friendly error states; cron lines	showcase snapshot	3 clean rehearsals from reset; backup video recorded
## 23. Demo script (≈6 minutes, from make demo-reset)
Slide: the problem, one number, "AI does language, code does truth."
Meena live: two Hindi answers → cited matches → open a citation.
Documents: blurry specimen → retake; clean → masked tick.
Ravi's timeline (12 days) → chase fires on the demo clock → draft → YES 4471 on the phone → email in the "bank" inbox, Ravi CC'd.
Second phone forwards the portal SMS → QUERY_RAISED → nudge arrives.
Meena's project report PDF → prefill pack → "press submit" approval item.
A real user's timeline (ID masked). Close on tests/rules 30/30: no model decides eligibility.
## 24. Cut list and fallback
Cut in this order if behind: domain offer → voice → Hindi PDF (ship English) → prefill pack → third real user → project report narrative (ship the numbers table).

Never cut: rules engine + tests, citations, Guard, Tracker + timeline, Telegram approval loop, make demo-reset.

Fallback demo if day 5 goes badly: slices 0–8 only. Still agentic, cited, human-gated, with a real timeline. Record the full run on day 6 as the backup video.