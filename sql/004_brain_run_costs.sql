-- v0.3: cost accounting for the $100 Claude credit.
alter table brain_runs add column if not exists model text;
alter table brain_runs add column if not exists cache_write_tokens int;
alter table brain_runs add column if not exists estimated_cost_usd numeric(10, 6) not null default 0;
create index if not exists brain_runs_started on brain_runs (started_at);
create index if not exists brain_runs_case_started on brain_runs (case_id, started_at);
