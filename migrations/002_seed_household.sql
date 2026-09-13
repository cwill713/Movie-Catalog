-- 002_seed_household.sql
-- One household and one profile, with fixed UUIDs so the app has something to
-- reference before auth exists.
--
-- In Phase 5, signing up sets profiles.auth_user_id on this row rather than
-- creating a second profile - that keeps the existing ratings attached to the
-- right person instead of orphaning them.

begin;

insert into households (id, name)
values ('00000000-0000-0000-0000-000000000001', 'Home')
on conflict (id) do nothing;

insert into profiles (id, household_id, display_name)
values (
    '00000000-0000-0000-0000-000000000002',
    '00000000-0000-0000-0000-000000000001',
    'Christian'
)
on conflict (id) do nothing;

commit;
