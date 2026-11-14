-- v0.3: fixed-window rate limits (contact form 5/hour per IP, /c/* 20/min) and pre-generated artifacts
-- (seeded demo explanations / analyses / narratives, so demos don't spend Claude credit).

create table if not exists rate_limits (
  key text not null,
  window_start timestamptz not null,
  count int not null default 0,
  primary key (key, window_start)
);

create table if not exists artifacts (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references cases(id) on delete cascade,
  application_id uuid,
  scheme_id text,
  kind text not null,                             -- rejection_analysis|report_narrative|...
  content jsonb not null,
  source text not null default 'claude',          -- claude|seed
  created_at timestamptz not null default now()
);
create index if not exists artifacts_case_kind on artifacts (case_id, kind);

-- now() is fixed for a whole transaction, so rows written in one agent step would tie on created_at.
-- clock_timestamp() keeps chat messages and timeline events in true order.
alter table messages alter column created_at set default clock_timestamp();
alter table events alter column created_at set default clock_timestamp();
