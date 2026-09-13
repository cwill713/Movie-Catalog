-- 004_rls.sql
-- Row-level security, enforced for real.
--
-- The trap this avoids: the app connects as `postgres`, which owns every table
-- AND has rolbypassrls = true. Policies written without changing that would
-- pass review, look secure, and enforce nothing at all.
--
-- Supabase ships an `authenticated` role that does NOT bypass RLS and that
-- `postgres` is allowed to SET ROLE to. So at request time the app does:
--
--     set local role authenticated;
--     set local app.current_profile_id = '<uuid>';
--
-- and the policies below read that setting. `set local` is scoped to the
-- transaction, so a pooled connection cannot leak one user's identity into the
-- next request.
--
-- Admin scripts (ingest, enrichment, embeddings) deliberately stay as
-- `postgres` and bypass all of this - they maintain the shared catalog and have
-- no user context.

begin;

-- ---------------------------------------------------------------------------
-- Identity helpers
--
-- SECURITY DEFINER on the household lookup is required, not decorative: it
-- reads `profiles`, which is itself behind RLS, and a policy that queries a
-- protected table recurses infinitely without it.
-- ---------------------------------------------------------------------------
create or replace function app_current_profile() returns uuid
language sql stable as $$
    select nullif(current_setting('app.current_profile_id', true), '')::uuid
$$;

create or replace function app_current_household() returns uuid
language sql stable security definer set search_path = public as $$
    select household_id from profiles where id = app_current_profile()
$$;

comment on function app_current_household() is
  'SECURITY DEFINER so RLS policies can call it without recursing through profiles.';

-- ---------------------------------------------------------------------------
-- Privileges. Nothing is granted by default; `authenticated` gets exactly what
-- the app needs and no more.
-- ---------------------------------------------------------------------------
grant usage on schema public to authenticated;

-- The catalog is shared and read-only for users; only admin scripts write it.
grant select on titles to authenticated;
grant select on households, profiles to authenticated;

grant select, insert, update, delete on
    watch_entries, entry_ratings, entry_tags, dismissals,
    chat_sessions, chat_messages
to authenticated;

grant select on recommendations to authenticated;

-- ---------------------------------------------------------------------------
-- Policies
-- ---------------------------------------------------------------------------
alter table households      enable row level security;
alter table profiles        enable row level security;
alter table watch_entries   enable row level security;
alter table entry_ratings   enable row level security;
alter table entry_tags      enable row level security;
alter table dismissals      enable row level security;
alter table recommendations enable row level security;
alter table chat_sessions   enable row level security;
alter table chat_messages   enable row level security;

-- Your own household, and the people in it.
create policy household_is_mine on households
    for select using (id = app_current_household());

create policy profiles_in_my_household on profiles
    for select using (household_id = app_current_household());

-- Watch entries: shared across the household. Either person can log a title
-- and either can correct it.
create policy watch_entries_mine on watch_entries
    for all using (household_id = app_current_household())
    with check (household_id = app_current_household());

-- Ratings: READ the whole household's, WRITE only your own.
--
-- Seeing the other person's score is the point - it is what makes a joint
-- recommendation possible. Editing theirs is not.
create policy entry_ratings_read on entry_ratings
    for select using (
        exists (select 1 from watch_entries we
                 where we.id = entry_ratings.entry_id
                   and we.household_id = app_current_household())
    );

create policy entry_ratings_write_own on entry_ratings
    for insert with check (profile_id = app_current_profile());

create policy entry_ratings_update_own on entry_ratings
    for update using (profile_id = app_current_profile())
        with check (profile_id = app_current_profile());

create policy entry_ratings_delete_own on entry_ratings
    for delete using (profile_id = app_current_profile());

-- Tags follow the same rule: read the household's, write your own.
create policy entry_tags_read on entry_tags
    for select using (
        exists (select 1 from watch_entries we
                 where we.id = entry_tags.entry_id
                   and we.household_id = app_current_household())
    );

create policy entry_tags_write_own on entry_tags
    for insert with check (profile_id = app_current_profile());

create policy entry_tags_delete_own on entry_tags
    for delete using (profile_id = app_current_profile());

-- Dismissals and recommendations are household-wide.
create policy dismissals_mine on dismissals
    for all using (household_id = app_current_household())
    with check (household_id = app_current_household());

create policy recommendations_mine on recommendations
    for select using (household_id = app_current_household());

-- Chat: a session belongs to the household; messages follow their session.
create policy chat_sessions_mine on chat_sessions
    for all using (household_id = app_current_household())
    with check (household_id = app_current_household());

create policy chat_messages_mine on chat_messages
    for all using (
        exists (select 1 from chat_sessions cs
                 where cs.id = chat_messages.session_id
                   and cs.household_id = app_current_household())
    )
    with check (
        exists (select 1 from chat_sessions cs
                 where cs.id = chat_messages.session_id
                   and cs.household_id = app_current_household())
    );

commit;
