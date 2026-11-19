alter table source_documents add column if not exists source_kind text not null default 'pdf';
