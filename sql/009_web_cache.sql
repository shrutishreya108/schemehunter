create table if not exists web_cache (
    url text primary key,
    scheme_id text,
    title text,
    text text,
    content_sha256 text,
    fetched_at timestamptz,
    last_status int,
    error text
);
