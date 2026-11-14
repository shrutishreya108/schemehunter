-- Example of a later migration (File Structure page). Harmless and repeatable:
-- lets the seeder / scheme watcher look up schemes by spec_hash cheaply.
create index if not exists schemes_spec_hash_idx on schemes (spec_hash);
