# PMEGP rule audit (before / after)

Written for: the curator and engineers reviewing the PMEGP rules.

Source: `Revisedguidelines07.12.2023.pdf`, O.M. No. PMEGP/UdhyamReg./01/2023, 29 PDF pages, stored as
`rulebook/sources/pmegp/guidelines.pdf` (sha256 computed by sync).
Official URL: https://msme.gov.in/sites/default/files/Revisedguidelines07.12.2023.pdf
Retrieval date: **not recorded yet** (the curator must supply it). The source is therefore incomplete, and no rule
counts as verified. The official URL does not make a rule verified.

Page numbers below are PDF page numbers, not printed page numbers.

## Status

Every rule, benefit and EDP block in `rulebook/schemes/pmegp.yaml` is `verified: false`. Every row in
`rulebook/sources/pmegp/evidence.yaml` is `status: unverified`. Curator confirmation is still needed for each row.

## Changed rules

| Item | Before (HEAD) | After | PDF page | Exact evidence | Status |
|---|---|---|---|---|---|
| Source | `last_verified: 2026-11-14`, LLD v1.1 cited | `source_document: pmegp-om-2023-12-07`; `last_verified` removed | 1 | Title (source.yaml; page not yet checked): "Approval for modifications in PMEGP guidelines making Udyam Registration mandatory for PMEGP units" | unverified |
| Age (`gte 18`) | page 3, "LLD v1.1 §6.1" | page 5, source note records the wording | 5 (§4.1) | "1. Any individual, above 18 years of age" | **Open**: kept as `gte 18` (see ambiguities) |
| New-only | page 3, "LLD v1.1" | page 6 | 6 | "Assistance under the scheme is available only for new projects sanctioned specifically under the PMEGP." | unverified |
| Cost cap | page 4 | page 5 (values unchanged: manufacturing 50 lakh, service 20 lakh) | 5 | "maximum cost ... Manufacturing sector is Rs. 50 lakhs ... Business/Service sector is Rs. 20 lakhs." | unverified |
| Education (VIII pass) | page 4, curator note | page 6; thresholds unchanged (manufacturing > 10 lakh, service > 5 lakh) | 6 | "above Rs.10 lakh in the Manufacturing sector and above Rs. 5 lakh in the Business /Service sector ... at least VIII standard pass" | unverified |
| Subsidy and contribution table | 15/25 general; 25/35 special; contribution 10 general, 5 special | **Unchanged** values. The table was read from the rendered page image (text extraction garbled it). | 4 | General 10%, subsidy urban 15%, rural 25%. Special 05%, subsidy urban 25%, rural 35%. | unverified |
| Special category | `special_category` fact (SC/ST/OBC only) | `pmegp_special_category`: female, SC, ST, OBC or disabled circumstance | 4 | Special category list on page 4 (see ambiguities for the categories not yet covered) | unverified |
| Documents | aadhaar, photo, passbook, project_report, quotation, caste_cert_if_special, education_cert_if_cost_gt_10l | Scheme papers: aadhaar, photo, caste_cert_if_special, **rural_area_cert_if_rural** (new), project_report, education_cert_if_vIII_required. Bank papers (new list `bank_documents`): passbook, quotation | 11 (§11.5–11.6) | "a. Caste Certificate b. Special Category Certificate, wherever required c. Rural Area certificate d. Project Report e. Education/ EDP/Skill Development training certificate" | unverified |
| EDP certificate | in documents, gated the document list | removed from `documents`; kept in the `edp` block as due after sanction | 13 (§11.16) | "within 30 working days of the sanction letter" | unverified |
| EDP block (new) | none | mandatory for subsidy; not mandatory up to Rs. 2 lakh; 5 days up to Rs. 5 lakh, 10 days above; prior 10 days offline / 60 hours online exempts; timing after application | 9, 13, 18 | "EDP training is mandatory for claiming the Margin Money (subsidy). ... No EDP training will be mandatory for projects up to Rs. 2 Lakh." | unverified |

## Ambiguities left open

1. **Age: "above 18" vs `gte 18`.** The guideline says "above 18 years of age". The rule uses `>= 18`, which
   would accept an 18-year-old. Kept unchanged, as instructed, until the curator decides. Recorded in the rule's
   `source_note`.
2. **Special category is only partly covered.** The guideline lists SC, ST, OBC, minorities, women,
   ex-servicemen, transgender, differently abled, NER, aspirational districts, hill and border areas. The profile
   can express female, SC, ST, OBC and disabled. Minorities, ex-servicemen, transgender and the area-based
   categories have no profile field. Applicants in those groups are treated as general, so their subsidy may be
   understated. This is the most important gap.
3. **Rural area certificate condition.** The guideline lists a "Rural Area certificate" but not when it is needed.
   We infer "only for rural projects". This is our inference, not the guideline's wording.
4. **Caste certificate for general category.** Required only for special categories. That reading is inferred from
   the list and not stated as a rule.
5. **Sector mapping.** The guideline says "Business/Service". Trading is not counted as service. This is a decision,
   not sourced text.
6. **EDP exemption cannot be checked.** The profile has no field for prior training, so the exemption is stated
   but not applied.
7. **Bank papers.** Passbook and quotation are not in the guideline text. They are kept as bank requirements
   and are not treated as scheme requirements.
8. **Base guidelines not in repo.** The guideline refers to the 2021 base guidelines (O.M. PMEGP/Policy/09/2021,
   13.05.2022). They are not in the repo, so rules that depend on them remain unchecked.
9. **Retrieval date.** The curator must record it in `source.yaml`. Until then no rule can be verified.

## Test coverage

`tests/rules/test_pmegp_audit.py` covers the subsidy table, the special-category rule, the age wording and its
open note, the unverified flags, the incomplete source, the bank/scheme document split, the rural certificate, the
EDP certificate timing and EDP figures. `tests/docs/test_pipeline.py` and `tests/rules/test_cli_and_personas.py`
were updated to the new document order and education page.

Full suite (`PYTHONUTF8=1`, Postgres on port 52356): 605 passed, 2 skipped, 1 deselected
(`test_smtp_password_value_in_tree_fails`, a known Windows-only case). `make check-secrets` passes.
