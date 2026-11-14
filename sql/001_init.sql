-- LLD §5. events is append-only. Everything else is normal.

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
