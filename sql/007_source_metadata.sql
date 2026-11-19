-- Official source documents and the evidence that verifies each rule (reliability milestone 1, section 3).
-- Every metadata column is nullable on purpose: a missing value stays NULL and is shown as "unavailable".
create table if not exists source_documents (
  id text primary key,                      -- e.g. "pmegp-guidelines-2024"; written in the source file
  scheme_id text references schemes(id) on delete cascade,
  title text,
  official_url text,
  issuing_authority text,
  publication_date date,
  effective_date date,
  retrieval_date date,
  version text,
  sha256 text,                              -- of the local file; computed by the sync, never typed by hand
  local_path text,
  synced_at timestamptz
);

create table if not exists rule_evidence (
  scheme_id text not null references schemes(id) on delete cascade,
  rule_id text not null,                    -- the rule's id in the scheme YAML, or "benefit"
  source_id text not null references source_documents(id) on delete cascade,
  page int,
  passage text,                             -- the supporting text, copied from the source
  status text not null check (status in ('verified', 'unverified')),
  last_verified date,
  primary key (scheme_id, rule_id, source_id)
);

-- Explicit consent for an AI check of an Aadhaar or passbook photo (reliability milestone 1, section 4).
alter table documents add column if not exists vision_consent boolean not null default false;
