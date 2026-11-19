-- Email-only sign-in (replaces the personal link and PIN). A case is created only after its email is verified.
create table if not exists identities (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references cases(id) on delete cascade,
  kind text not null check (kind in ('email')),
  value_norm text not null,
  verified_at timestamptz not null,
  created_at timestamptz not null default clock_timestamp(),
  unique (kind, value_norm)
);
create index if not exists identities_case on identities (case_id);

create table if not exists login_challenges (
  id uuid primary key,
  purpose text not null check (purpose in ('signin','signup','admin')),
  email_norm text not null,
  case_id uuid references cases(id) on delete cascade,   -- set for signin and admin
  code_hash text,                                        -- keyed hash of the 6-digit code; null for admin links
  link_hash text not null unique,                        -- sha256 of the one-tap token
  lang text not null default 'hi',
  attempts int not null default 0,
  created_at timestamptz not null default clock_timestamp(),
  expires_at timestamptz not null,
  consumed_at timestamptz,
  superseded_at timestamptz,
  sent_at timestamptz,
  send_error text
);
create index if not exists login_challenges_email on login_challenges (email_norm, created_at desc);

alter table cases drop column if exists access_token_hash;
alter table cases drop column if exists pin_hash;
alter table cases drop column if exists telegram_chat_id;
