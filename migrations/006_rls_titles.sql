-- 006_rls_titles.sql
-- Turn on row-level security for the two public tables that were missing it.
--
-- Supabase's Security Advisor flagged `public.titles` as "RLS Disabled in
-- Public": anyone holding the project URL could reach it through PostgREST.
-- 004 granted `authenticated` read access to titles but never enabled RLS on
-- it, so the grant was the only fence and it was not a fence at all.
--
-- The catalog is shared and read-only for users, so the policy is simply:
-- signed-in users may read it, nobody else may touch it. Writes come from
-- admin scripts running as `postgres`, which bypass RLS (see 004 header).
--
-- `schema_migrations` is the runner's own ledger. The app never reads it, so
-- it gets RLS with no policies: every non-owner role is denied by default.

alter table titles enable row level security;

create policy titles_read on titles
    for select to authenticated
    using (true);

alter table schema_migrations enable row level security;
