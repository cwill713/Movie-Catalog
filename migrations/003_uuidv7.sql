-- 003_uuidv7.sql
-- Switch primary keys from UUIDv4 to UUIDv7, and fix the root cause of the
-- ordering bug that ADR-004a describes.
--
-- UUIDv7 keeps the UUID format and uniqueness guarantees but puts a 48-bit
-- millisecond timestamp in the leading bits, so keys sort in creation order.
-- That gives ordered index inserts instead of scattered ones, and makes
-- "order by id" meaningful.
--
-- Postgres 18 ships uuidv7() natively. This database is 17.6, so we define it.
-- WHEN SUPABASE MOVES TO POSTGRES 18: drop this function and the built-in takes
-- over - the column defaults reference the name, not this implementation.
--
-- Second change: created_at defaults move from now() to clock_timestamp().
-- now() returns the TRANSACTION start time, so every row inserted in one
-- transaction got a byte-identical created_at - which is what broke the sort
-- order during the SQLite migration. clock_timestamp() returns the real
-- instant, so bulk inserts get distinct, ascending timestamps.

begin;

create or replace function uuidv7() returns uuid
as $$
  -- Take a random v4 uuid, overlay the top 6 bytes with the current unix
  -- timestamp in milliseconds, then set the version nibble to 7.
  select encode(
    set_bit(
      set_bit(
        overlay(
          uuid_send(gen_random_uuid())
          placing substring(
            int8send(floor(extract(epoch from clock_timestamp()) * 1000)::bigint)
            from 3
          )
          from 1 for 6
        ),
        52, 1
      ),
      53, 1
    ),
    'hex'
  )::uuid;
$$ language sql volatile;

comment on function uuidv7() is
  'UUIDv7 (time-ordered). Shim for Postgres < 18; drop once the native function is available.';

-- --- primary keys -----------------------------------------------------------
alter table households       alter column id set default uuidv7();
alter table profiles         alter column id set default uuidv7();
alter table watch_entries    alter column id set default uuidv7();
alter table entry_ratings    alter column id set default uuidv7();
alter table recommendations  alter column id set default uuidv7();
alter table chat_sessions    alter column id set default uuidv7();
alter table chat_messages    alter column id set default uuidv7();

-- --- timestamps -------------------------------------------------------------
alter table titles           alter column created_at set default clock_timestamp();
alter table titles           alter column updated_at set default clock_timestamp();
alter table households       alter column created_at set default clock_timestamp();
alter table profiles         alter column created_at set default clock_timestamp();
alter table watch_entries    alter column created_at set default clock_timestamp();
alter table watch_entries    alter column updated_at set default clock_timestamp();
alter table entry_ratings    alter column created_at set default clock_timestamp();
alter table entry_ratings    alter column updated_at set default clock_timestamp();
alter table entry_tags       alter column created_at set default clock_timestamp();
alter table dismissals       alter column created_at set default clock_timestamp();
alter table recommendations  alter column generated_at set default clock_timestamp();
alter table chat_sessions    alter column created_at set default clock_timestamp();
alter table chat_messages    alter column created_at set default clock_timestamp();

-- The updated_at trigger has the same now() problem.
create or replace function set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = clock_timestamp();
  return new;
end;
$$;

commit;
