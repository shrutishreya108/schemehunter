-- Slice 7: executor retries are counted per pending action (FAILED after 3 tries, LLD §11).
alter table pending_actions add column if not exists attempts int not null default 0;
