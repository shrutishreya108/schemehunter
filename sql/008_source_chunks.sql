-- Retrieval index over guideline page text (app/rag). Rebuilt from rulebook/sources/<scheme>/pages by sync_sources.
create table if not exists source_chunks (
    id uuid primary key default gen_random_uuid(),
    scheme_id text not null,
    page int not null,
    ord int not null,
    text text not null,
    content_sha256 text not null,
    tsv tsvector generated always as (to_tsvector('english', text)) stored,
    unique (scheme_id, page, ord)
);
create index if not exists source_chunks_tsv on source_chunks using gin (tsv);
