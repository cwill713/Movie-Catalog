-- 001_initial_schema.sql
-- Phase 2: the full schema from docs/SPEC.md §5.
--
-- Tables for later phases (embeddings, chat, recommendations) are created now
-- because columns are cheap and migrations mid-project are not. What is
-- deliberately deferred:
--   * RLS policies          -> Phase 5, when auth exists
--   * HNSW index on titles  -> Phase 6, built after the bulk load (much faster)
--   * profiles.auth_user_id -> populated in Phase 5 at signup

begin;

create extension if not exists vector;
create extension if not exists pg_trgm;

-- ---------------------------------------------------------------------------
-- updated_at maintenance
-- ---------------------------------------------------------------------------
create or replace function set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- titles - the catalog. IMDb dumps supply the structured columns, OMDb the
-- prose and artwork. Empty until Phase 3.
-- ---------------------------------------------------------------------------
create table titles (
    imdb_id         text primary key,
    title_type      text not null check (title_type in
                      ('movie', 'tvSeries', 'tvMiniSeries', 'tvMovie')),
    primary_title   text not null,
    original_title  text,
    start_year      int,
    end_year        int,
    runtime_minutes int,
    genres          text[] not null default '{}',
    imdb_rating     numeric(3,1),
    imdb_votes      int,

    -- OMDb enrichment (Phase 4)
    plot            text,
    director        text,
    writer          text,
    actors          text[],
    content_rating  text,
    released_on     date,
    language        text,
    country         text,
    awards          text,
    poster_url      text,
    metascore       int,
    rt_score        int,
    omdb_fetched_at timestamptz,
    omdb_status     text,          -- 'ok' | 'not_found' | 'error'; null = not tried

    -- embeddings (Phase 6)
    embedding       halfvec(768),
    embedding_text  text,

    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),

    search_tsv tsvector generated always as (
        to_tsvector('english',
            coalesce(primary_title, '') || ' ' || coalesce(original_title, ''))
    ) stored
);

create index titles_genres_idx      on titles using gin (genres);
create index titles_search_idx      on titles using gin (search_tsv);
create index titles_title_trgm_idx  on titles using gin (primary_title gin_trgm_ops);
create index titles_votes_idx       on titles (imdb_votes desc nulls last);
create index titles_year_idx        on titles (start_year);
create index titles_type_idx        on titles (title_type);
-- Partial indexes driving the enrich/embed scripts' "what's left?" queries
create index titles_need_omdb_idx  on titles (imdb_votes desc) where omdb_fetched_at is null;
create index titles_need_embed_idx on titles (imdb_votes desc) where embedding is null;

create trigger titles_updated_at before update on titles
    for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- households + profiles
--
-- profiles.id is our own uuid, NOT auth.users.id. auth_user_id links to
-- Supabase Auth at signup. Keeping them separate means profiles can exist
-- before anyone has an account, which is what Phase 2 needs to migrate the
-- existing library.
-- ---------------------------------------------------------------------------
create table households (
    id         uuid primary key default gen_random_uuid(),
    name       text not null,
    created_at timestamptz not null default now()
);

create table profiles (
    id                 uuid primary key default gen_random_uuid(),
    household_id       uuid not null references households(id) on delete cascade,
    auth_user_id       uuid unique,          -- -> auth.users.id, set in Phase 5
    display_name       text not null,
    taste_embedding    halfvec(768),
    taste_refreshed_at timestamptz,
    created_at         timestamptz not null default now()
);

create index profiles_household_idx on profiles (household_id);

-- ---------------------------------------------------------------------------
-- watch_entries - household-level "we've seen this"
--
-- imdb_id is nullable so a title missing from the catalog can still be logged;
-- manual_* carry the details in that case. The existing five movies arrive
-- this way and get linked to real tconsts in Phase 3.
-- ---------------------------------------------------------------------------
create table watch_entries (
    id            uuid primary key default gen_random_uuid(),
    household_id  uuid not null references households(id) on delete cascade,
    imdb_id       text references titles(imdb_id) on delete set null,

    manual_title  text,
    manual_year   int,
    manual_genres text[] not null default '{}',

    added_by      uuid references profiles(id) on delete set null,
    watched_on    date,
    rewatch_count int not null default 0,
    status        text not null default 'watched'
                    check (status in ('watched', 'want_to_watch')),
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),

    constraint watch_entries_identified
        check (imdb_id is not null or manual_title is not null)
);

create unique index watch_entries_unique_title_idx
    on watch_entries (household_id, imdb_id) where imdb_id is not null;
create index watch_entries_household_idx on watch_entries (household_id);

create trigger watch_entries_updated_at before update on watch_entries
    for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- entry_ratings - per-person opinion. A row means that person watched it;
-- rating and review are each optional.
-- ---------------------------------------------------------------------------
create table entry_ratings (
    id               uuid primary key default gen_random_uuid(),
    entry_id         uuid not null references watch_entries(id) on delete cascade,
    profile_id       uuid not null references profiles(id) on delete cascade,
    rating           numeric(3,1) check (rating >= 0 and rating <= 10),
    review           text,
    review_embedding halfvec(768),
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),
    unique (entry_id, profile_id)
);

create index entry_ratings_profile_idx on entry_ratings (profile_id);

create trigger entry_ratings_updated_at before update on entry_ratings
    for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- entry_tags - free-form mood tags, no fixed vocabulary
-- ---------------------------------------------------------------------------
create table entry_tags (
    entry_id   uuid not null references watch_entries(id) on delete cascade,
    profile_id uuid not null references profiles(id) on delete cascade,
    tag        text not null,
    created_at timestamptz not null default now(),
    primary key (entry_id, profile_id, tag)
);

create index entry_tags_tag_idx on entry_tags (tag);

-- ---------------------------------------------------------------------------
-- dismissals - "not interested", so recommendations stop resurfacing it
-- ---------------------------------------------------------------------------
create table dismissals (
    household_id uuid not null references households(id) on delete cascade,
    imdb_id      text not null references titles(imdb_id) on delete cascade,
    dismissed_by uuid references profiles(id) on delete set null,
    reason       text,
    created_at   timestamptz not null default now(),
    primary key (household_id, imdb_id)
);

-- ---------------------------------------------------------------------------
-- recommendations - precomputed home-page shelves (Phase 7)
-- ---------------------------------------------------------------------------
create table recommendations (
    id            uuid primary key default gen_random_uuid(),
    household_id  uuid not null references households(id) on delete cascade,
    imdb_id       text not null references titles(imdb_id) on delete cascade,
    shelf_key     text not null,
    seed_imdb_id  text references titles(imdb_id) on delete set null,
    score         numeric,
    reason        text,
    rank          int,
    generated_at  timestamptz not null default now()
);

create index recommendations_shelf_idx
    on recommendations (household_id, shelf_key, rank);

-- ---------------------------------------------------------------------------
-- chat (Phase 8). retrieved_imdb_ids keeps every RAG answer auditable.
-- ---------------------------------------------------------------------------
create table chat_sessions (
    id           uuid primary key default gen_random_uuid(),
    household_id uuid not null references households(id) on delete cascade,
    profile_id   uuid references profiles(id) on delete set null,
    title        text,
    created_at   timestamptz not null default now()
);

create table chat_messages (
    id                 uuid primary key default gen_random_uuid(),
    session_id         uuid not null references chat_sessions(id) on delete cascade,
    role               text not null check (role in ('user', 'assistant', 'system')),
    content            text not null,
    retrieved_imdb_ids text[] not null default '{}',
    created_at         timestamptz not null default now()
);

create index chat_messages_session_idx on chat_messages (session_id, created_at);

commit;
