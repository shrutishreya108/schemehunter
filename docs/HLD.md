# Scheme Hunter: High-Level Design

> Everything below uses one rule: AI does the language, code does the truth. Claude talks, reads, and writes. Plain code decides who qualifies, does the math, keeps the dates, and remembers. That rule shows up in every section.
> 

## 1. What we're building (and not building)

**Goal.** A helper that takes a small entrepreneur from "I have an idea and need money" to "my scheme applications are filed, tracked, chased, and retried," with the human pressing every important button.

**Not building (say this out loud in the pitch):**

```yaml
It never submits a form by itself. Government portals need the person's phone OTP anyway.
```

- It never promises money. Strongest sentence allowed: "You qualify to apply."
- It never handles payments.
- One state, one kind of entrepreneur, 5–8 schemes for the hackathon. Everything is built so adding a scheme is adding one file.

## 2. Who touches it

- **Entrepreneur (Meena).** Uses her phone. Hindi or English. Low patience for forms.
- **Helper (optional, strong inclusion point).** An NGO worker or Common Service Centre operator who helps many Meenas. One login, many cases. The agent does the chasing so one helper can handle 50 people instead of 5.
- **Curator (you).** Maintains the scheme rule book.
- **Outside world.** Scheme portals, bank branches, nodal officers. They don't know an agent exists; they get normal emails and normal forms.

## 3. The big picture

```
        Meena's phone                          You
 (website on her domain / WhatsApp)        (admin page)
             │                                  │
             ▼                                  ▼
┌─────────────────────────────────────────────────────────┐
│  FRONT DOOR   web app + message inbox                   │
└───────────────┬──────────────────────────────┬──────────┘
                │ "user did something"         │ shows
                ▼                              ▼
┌───────────────────────────┐      ┌──────────────────────┐
│  BRAIN  (Claude agent)    │◀────▶│  MEMORY  (Postgres)  │
│  reads case, picks tools  │      │  case, docs, apps,   │
└───────────────┬───────────┘      │  events, approvals   │
                │ calls            └──────────▲───────────┘
                ▼                             │
┌───────────────────────────┐                 │
│  HANDS  (tools)           │                 │
│  rules engine, math,      │                 │
│  PDF maker, doc reader,   │                 │
│  portal checker, drafter  │                 │
└───────────────┬───────────┘                 │
                │ wants to send / submit      │
                ▼                             │
┌───────────────────────────┐      ┌──────────┴───────────┐
│  GUARD  (approval gate)   │─────▶│  MOUTH  (email /     │
│  human says yes first     │      │  WhatsApp / Telegram)│
└───────────────────────────┘      └──────────────────────┘
                ▲
                │ "it's time to check / remind / chase"
┌───────────────┴───────────┐      ┌──────────────────────┐
│  ALARM CLOCK  (scheduler) │      │  RULE BOOK           │
│  wakes up daily           │      │  scheme files + PDFs │
└───────────────────────────┘      └──────────────────────┘
```

All of this runs on one GoDaddy VPS. The website lives on a GoDaddy domain.

Nine parts. Each gets a section.

## 4. Front Door (what Meena sees)

A mobile website on your GoDaddy domain. No app store. Five screens:

- **Talk to me.** Chat-style onboarding, Hindi/English toggle, optional voice input (the browser's own speech-to-text, free). Questions come one at a time.
- **My papers.** Camera upload. Each paper shows a green tick, a yellow "retake" or a red "problem" with a one-line reason.
- **My schemes.** Cards: scheme name, what you get, "why you fit" in two lines, "why you don't" for the ones that failed, each with a "see the rule" link that opens the exact paragraph.
- **My applications.** Parcel-tracking style. Applied → Under review → Bank asked for a paper → Approved. One row per scheme.
- **Needs you.** The approvals inbox. "I drafted this email to the bank. Send?" "Press submit on the PMEGP portal, here's your pre-filled pack." Big buttons.

Plus a Timeline view: everything the agent did and every time it waited. This is your demo screen.

Also an inbox for messages coming in: Meena forwards the bank's SMS, or types "done, number is 4471." The Brain reads these.

**Tech:** FastAPI serving HTML with HTMX (one language for the whole project, no separate frontend build), or Next.js if you're comfortable. PWA manifest so it installs like an app.

## 5. Rule Book (the scheme knowledge)

Two layers.

**Layer 1: one structured file per scheme.** Written by hand by you. Example shape:

```yaml
id: pmegp
name: Prime Minister's Employment Generation Programme
level: central
authority: KVIC
benefit: { type: subsidy, percent_rural_special: 35, percent_urban_general: 15, ... }
eligibility:
  age_min: 18
  new_business_only: true
  project_cost_max: { manufacturing: 5000000, service: 2000000 }
  education_min_if_cost_over: { threshold: 1000000, level: "8th pass" }
documents: [aadhaar, photo, caste_cert_if_applicable, project_report, quotation, ...]
apply_mode: portal          # portal | bank | office
portal_url: https://...
status_check: user_forwards  # scrape | user_forwards | manual
window: rolling              # or dates
source: { file: pmegp_guidelines.pdf, pages: [3, 7, 12] }
last_verified: 2026-11-14
```

**Layer 2: the official guideline PDFs.** Stored as-is. Claude reads them only to explain and cite, never to decide.

Why two layers: the structured file lets plain code decide eligibility the same way every time. The PDF lets Claude point at the real sentence when Meena asks "why?"

A weekly scheme watcher job fetches each guideline page, hashes it, and emails you if it changed. Schemes change quietly; this is how you find out.

## 6. Brain (the Claude agent)

**What a "run" is.** The Brain never runs forever. It wakes up for one job, does it in a handful of steps, writes everything to Memory, and stops. Two things wake it:

- Meena did something (sent a message, uploaded a paper, pressed approve).
- The Alarm Clock says it's time (check status, remind, chase).

**What it sees on waking.** Not the whole history. A compact case file that code assembles: profile, current matches, each application's state, last 10 events, and the task. Keeps context small, cost low, behavior predictable.

**How it works.** Claude with tool use, in a loop: read case file → decide → call a tool → read result → decide again → finish with a summary. Hard cap of 12 steps per run. If it hits the cap, it writes "I got stuck here" to the timeline and stops.

**Rules in its system prompt (the ones that matter):**

- Only name schemes that exist in the Rule Book.
- Never say eligible/not eligible yourself; call the rules engine and explain its answer.
- Every "you qualify because" must carry a citation.
- Never promise approval or money.
- Anything that leaves the system (email, submission) goes to the Guard, never sent directly.
- Speak in the user's language, at a simple reading level.

**Claude features used.** Tool use (the whole loop). Vision (reading papers). Citations over the guideline PDFs (the "see the rule" links). Structured outputs with JSON schemas (so document checks and drafts come back as fields, not prose). Prompt caching on the system prompt + Rule Book, because that's the same every run and it's where most of your tokens would go.

**Models.** Sonnet for reasoning runs. Haiku for cheap classification (what kind of message is this, what kind of document is this). Documents with names and numbers go to Sonnet; misreading an Aadhaar costs more than the tokens saved.

## 7. Hands (the tools the Brain can call)

| Tool | Who really does the work | What it does |
| --- | --- | --- |
| get_case / update_profile | code | Load the case file, save answers |
| match_schemes | code (rules engine) | Profile × every scheme file → fits / doesn't fit, with the rule that decided it |
| explain_match | Claude + citations | Turn a rule result into two plain sentences with the source paragraph |
| check_document | Claude vision + code | Vision: what paper is this, is it readable, what name/address is on it. Code: fuzzy-match names across all papers, flag mismatches |
| build_project_report | code for numbers, Claude for words | Cost table, revenue, break-even, repayment schedule computed; Claude writes the narrative around the numbers; PDF out |
| prefill_form | code | Map profile → that scheme's form fields → pre-filled PDF or copy-paste pack with step list |
| create_application | code | New row in the tracker, state DISCOVERED |
| check_status | code, three modes | See section 9 |
| interpret_response | Claude | Read a rejection letter, query, or forwarded SMS; extract state, reason, missing paper, deadline, reference number |
| draft_message | Claude | Write the email/WhatsApp; saved as pending, not sent |
| request_approval | code | Puts an item in Meena's "Needs you" inbox |
| schedule | code | Set an alarm (check in 1 day, remind at T-7, retry next cycle) |
| log_event | code | Append to the timeline |

Note which rows say "code." Eligibility, money math, dates, and name matching are never Claude's call.

## 8. Tracker (the life of one application)

Every application is a small state machine. Every arrow writes an event.

```
DISCOVERED → ELIGIBLE → DOCS_PENDING → DOCS_READY → FORM_READY
→ AWAITING_HUMAN_SUBMIT → SUBMITTED → UNDER_REVIEW
├→ QUERY_RAISED → DOCS_PENDING (loop back)
├→ SANCTIONED (done)
└→ REJECTED → ANALYZED → ┬→ RETRY_NEXT_CYCLE (alarm set)
└→ CLOSED (not fixable)
ELIGIBLE/DOCS_* ──deadline passed──▶ MISSED → RETRY_NEXT_CYCLE
```

**Who moves it:**

- Meena: uploads papers, presses submit, pastes the reference number.
- Brain: after reading a response ("bank asked for income proof" → QUERY_RAISED).
- Clock: deadline passed → MISSED; 10 days no movement → "chase" run.

The Tracker is the heart. The Timeline is just the Tracker's diary shown nicely.

## 9. Checking status in the real world (the honest part)

Portals differ. Each scheme file declares one of three modes:

- **scrape:** a public status page exists. A headless browser (Playwright) on the VPS checks it daily. Fragile; build for one scheme only, with a fallback.
- **user_forwards:** status arrives by SMS or email to Meena, or behind her OTP login. The agent asks her to forward the SMS or paste what the screen says. interpret_response reads it. This is the default and it works for every portal.
- **manual:** offline schemes (bank branch, district office). The agent drafts follow-up emails/letters on a schedule and asks Meena what she heard.

Design for all three, ship user_forwards and manual fully, scrape for one scheme if time allows.

## 10. Guard (why this is safe to call "autonomous")

A tiny but non-negotiable piece. Any action that touches the outside world becomes a pending action row: what, to whom, the exact text, why. It executes only when a human presses approve (web button, or replies YES on WhatsApp/Telegram).

**Covered:** sending any email, anything to a bank or officer, "submit now" instructions, buying a domain. Each pending action has an idempotency key so a double-tap can't send twice.

**Second guard, in code:** every outgoing text is scanned for banned promise words ("guaranteed," "you will receive," "approved") and for scheme names not in the Rule Book. Fail → the Brain is asked to rewrite.

## 11. Alarm Clock (the scheduler)

A worker process that wakes every few minutes, reads a jobs table (run_at, type, case_id), and runs what's due. No Redis, no queue system; Postgres is enough.

**Job types:**

- Daily status check per SUBMITTED application.
- Chase: no movement for N days → draft a follow-up → Guard.
- Deadline reminders at T-7 and T-2 for ELIGIBLE/DOCS_PENDING.
- Gentle document nudges, max one per day per person.
- Next-cycle retry.
- Weekly scheme watcher.
- Nightly backup.

If a job fails, it retries with growing waits (1 min, 10 min, 1 hour), then emails you.

## 12. Memory (the data model)

| Table | Holds |
| --- | --- |
| users | login, role (entrepreneur/helper/curator), language |
| cases | one per entrepreneur; linked helper; status; consent record |
| profile_answers | key/value answers, with source (typed, spoken, read from a document) |
| documents | type, file pointer, check result, extracted fields, masked ID numbers |
| schemes | mirror of the Rule Book files, with last_verified |
| matches | case × scheme: fits?, deciding rule, citation refs |
| applications | the Tracker: scheme, state, reference number, deadline |
| events | append-only timeline: who, what, when, link to any artifact |
| pending_actions | the Guard queue: payload, status, approved_by, idempotency key |
| messages | everything in and out, with channel |
| jobs | the Alarm Clock |

events is append-only. It's your audit log, your debugging tool, and your demo at the same time.

## 13. Vault (handling very sensitive papers)

Aadhaar, caste certificates, bank passbooks. Treat them as the most sensitive thing you've ever stored.

- Files encrypted at rest on the VPS (encrypted volume or per-file encryption with a key in the environment, never in the repo).
- Never store a full Aadhaar number. Store the last four digits. Redact the number in any extracted text before saving.
- Documents go to Claude for reading, then only the extracted fields are kept; the API doesn't train on your data by default, and you say this in the consent screen.
- Consent at onboarding in Hindi, in plain words: what you collect, why, how long, how to delete. This lines up with India's DPDP law, which is phasing in now.
- Retention: delete documents 90 days after a case closes. One scheduled job.
- Access: entrepreneur sees own case; helper sees linked cases only; you see metadata, not document images, unless debugging with consent.
- Logs never contain document contents or names.

## 14. Mouth (messaging)

- **To Meena:** WhatsApp Cloud API if approved in time, else Telegram (free, five minutes) and email. Short messages, one ask each.
- **From Meena:** she forwards SMS/email or types. The Brain's interpret_response turns it into state changes.
- **To banks/officers:** email only, from a business mailbox on your domain, always "on behalf of Meena," always CC Meena, always after Guard approval. Sending mail straight from a VPS lands in spam; use a transactional email provider's free tier.

## 15. Deployment (the GoDaddy part)

One VPS, Docker Compose, five containers:

```
caddy      → HTTPS automatically (Let's Encrypt), reverse proxy
web        → FastAPI: pages, API, message webhooks
worker     → Alarm Clock + Brain runs
postgres   → Memory
playwright → headless browser for the one scraped portal (optional)
```

Your GoDaddy domain's A record points at the VPS. Caddy gets the certificate on first boot.

**Nice extra for judges:** when Meena's first application is sanctioned, the agent offers to register her business domain through the GoDaddy Domains API and publishes a one-page site for her business. Approval-gated, like everything else.

**Backups:** nightly pg_dump, encrypted, copied off the box.

## 16. When things break

- **Claude API down or rate-limited:** runs queue in jobs, retry with backoff. Meena sees "I'll get back to you shortly," not an error.
- **Scraper breaks:** that scheme flips to user_forwards automatically and tells Meena to forward the SMS.
- **Message fails to deliver:** retry three times, then it appears in your admin page.
- **Brain goes in circles:** 12-step cap, writes "stuck" event, you get an email.
- **Daily token budget hit:** non-urgent runs wait until tomorrow; urgent (deadline) runs proceed.

## 17. How you prove it works

- **Rules engine:** 30 made-up profiles × every scheme, expected answers hand-labeled from the guidelines. Must be 100%. This is the test you show when a judge asks "does the AI decide eligibility?" (Answer: no, this does.)
- **Document reader:** 20 photos, clear/blurry/mismatched-name mix. Report correct, "retake," and wrong rates.
- **Project report math:** unit tests on the spreadsheet logic.
- **Safety:** feed the drafter adversarial prompts; confirm the banned-phrase scanner catches promises and invented schemes.
- **End to end:** three real people during build week. Their timelines are the demo.

## 18. Build order for 7 days

1. Rule Book files for 5–8 schemes + rules engine + its 30-profile test suite.
2. Onboarding chat + matching screen + citations.
3. Document upload, vision check, name matching, Vault.
4. Project report (math + narrative + PDF) and form prefill packs.
5. Tracker + Alarm Clock + Guard + messaging.
6. Real users. Inbound message reading. Timeline screen.
7. Admin page, failure paths, backups, demo script.

If you fall behind, cut in this order: the scraped portal, the domain-registration extra, voice input. Never cut the Guard, the citations, or the timeline. Those three are the design.
