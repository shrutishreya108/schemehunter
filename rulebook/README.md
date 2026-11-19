# Rule Book

One YAML file per scheme in `schemes/`, plus the official guidelines in `sources/<scheme_id>/`.
**Plain code decides eligibility from these files** (`app/rules/engine.py`). Claude never decides; it only
explains the engine's answer and cites the guideline page.

## Adding a scheme

1. Put the official PDF at `sources/<id>/guidelines.pdf` and run `make extract-pages`
   (writes `sources/<id>/pages/01.txt …`; uses `pdftotext -layout` when it is on PATH, else `pypdf`).
2. Write `schemes/<id>.yaml`. The file name must equal `id`.
3. Add test profiles to `tests/rules/profiles.yaml` covering PASS, FAIL and UNKNOWN for every new rule,
   with expected results worked out by hand. `pytest tests/rules` must stay at 100%.
4. When you have checked a rule against the PDF, add a row to `sources/<id>/evidence.yaml` with the page and a
   verbatim passage, and `status: verified`. The test `tests/rules/test_evidence_passages.py` fails if the passage
   is not on that page. The `verified` flag in the scheme file is separate (see below).

## Retrieval (RAG): finds pages, never decides

`app/rag` indexes the extracted page text (`sources/<id>/pages/NN.txt`) in the `source_chunks` table, rebuilt by
every sync (`make seed`, `make demo-reset`). It uses Postgres full-text search, with no embeddings and no model.

- `explain_match` sends at most 4 pages: the pages of verified evidence rows first, then the pages search ranks for
  the result (the scheme name, the deciding rule, and each failing or unknown rule's note and fact names). If search
  finds nothing, the first 6 pages are sent. The page hints in the scheme files are ignored.
- `answer_question` gets up to 4 chunks per scheme. A chunk is labelled verified only when its source is complete and
  its page carries a verified evidence passage; otherwise it is an estimate.
- Search needs every word of the query first; if nothing matches, any word may match.

Curator commands (read only; they need a synced database):

- `make rulebook-suggest SCHEME=pmegp`: for each material rule without verified evidence, the top 3 candidate pages
  with a 300-character excerpt. It writes nothing.
- `make rulebook-report`: per scheme, verified / total material rules, and each verified rule with its page and passage.

Official web pages (fallback, `app/web`): a scheme with no PDF text may list up to 2 official pages in `web.yaml`
(https only; the hosts are the allowlist). Answers read them only when the scheme has no PDF text. Run
`make warm-web` to fetch them and write `web/*.txt` snapshots, which are committed. The VPS serves the snapshots
(`WEB_FETCH_ENABLED=false`). A web page is never a checked rule.

Limits: scanned pages (under 200 characters) are written empty and are not read; table text can extract badly
(the PMEGP subsidy table is checked against the rendered page image, not the text).

## File shape

```yaml
id: pmegp                       # = file name
name: { en: "...", hi: "..." }  # required, both languages
aliases: ["PMEGP"]              # optional: other names; the outbound scanner treats these as known
level: central                  # central | state
state: KA                       # required when level: state (ISO 3166-2:IN code)
authority: KVIC                 # optional
benefit:
  type: subsidy                 # subsidy | loan | guarantee
  text: { en: "...", hi: "..." }  # one-line plain description (optional)
  verified: false
  source_note: "..."
apply_mode: portal              # portal | bank | office
portal_url: https://...         # optional
status_mode: user_forwards      # user_forwards | manual | scrape (scrape is stubbed)
window: rolling                 # or { opens: 2026-12-01, closes: 2026-12-31 }; optional
documents: [aadhaar, photo]     # optional
source_pdf: guidelines.pdf      # optional
last_verified: 2026-11-14       # optional; date the curator last checked the file against the PDF

eligibility:
  all:                          # every top-level rule must pass
    - id: age                   # unique within the scheme; shown as the "deciding rule"
      page: 3                   # optional until verified; becomes the citation
      verified: false           # REQUIRED on every rule
      source_note: "..."        # REQUIRED: where this rule came from
      fact: age                 # then exactly one node: leaf | all | any | when/then
      op: gte
      value: 18

benefit_calc:                   # optional; each key is a list, first matching `when` wins
  subsidy_percent:
    - { when: { fact: area, op: eq, value: rural }, value: 25, verified: false, source_note: "..." }
    - { default: 15, verified: false, source_note: "..." }   # default must be last
```

### Nodes

| Node | Meaning |
|---|---|
| `{fact, op, value}` | leaf comparison |
| `{all: [...]}` | every child passes |
| `{any: [...]}` | at least one child passes |
| `{when: node, then: node}` | if `when` fails the rule passes; otherwise `then` decides |

Operators: `eq`, `in`, `not_in`, `gt`, `gte`, `lte`, `gte_level` (education order:
`none < 5th < 8th < 10th < 12th < graduate`). `in`/`not_in` take a list.

Every result is PASS, FAIL or UNKNOWN (a needed fact is missing). Scheme result: any FAIL → `NO_FIT`;
else any UNKNOWN → `NEEDS_INFO` (the missing facts drive the next question); else `FIT`.
The deciding rule is the first failing rule, or for NEEDS_INFO the first unknown rule.

### The `verified` flag

`verified: false` means "encoded from a secondary source; not yet checked against the guideline PDF."
Every rule and every `benefit_calc` entry carries it, together with `source_note`. The loader refuses a
rule without them. Before the demo, every rule a persona depends on should be `verified: true` with a `page:`.

## Facts the rules use

| Fact | Values |
|---|---|
| `age` | derived from `dob` |
| `gender` | `female` / `male` / ... |
| `category` | `SC` / `ST` / `OBC` / `GEN` |
| `special_category` | derived: woman or SC/ST (unknown if it can't be decided yet) |
| `business_stage` | `new` / `existing` |
| `sector` | `manufacturing` / `service` / `trading` / `agri_allied` / `education_institution` / `shg` |
| `project_cost`, `loan_amount`, `family_income` | rupees (integers) |
| `education_level` | see order above |
| `area` | `rural` / `urban` |
| `in_default`, `previous_tarun_repaid` | `true` / `false` |
| `enterprise_size` | `micro` / `small` / `medium` |
| `special_circumstance` | `widow` / `disabled` / `none` |
| `state_code`, `state_name` | derived from `cases.state_code` |
