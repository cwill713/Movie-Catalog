# Architecture Decision Records

One entry per significant decision: what we were choosing between, what we
picked, what it costs us, and what would make us change our minds.

The point of writing these down is that six months from now the code shows
*what* we did but not *why*, and "why" is the part that's expensive to
reconstruct. An entry is never deleted — if a decision is reversed, its status
changes to **Superseded** and a new entry explains what replaced it.

**Status key:** Accepted · Open · Superseded · Deprecated

> **This file is public.** It must contain no personal data — no names, no
> usernames, no hardware details, no household specifics. Refer to people by
> role ("the owner"), and describe machines by capability ("a consumer GPU with
> 16 GB VRAM") rather than by model. Enforced by
> `tests/test_decisions_doc.py`.

| # | Decision | Status |
|---|---|---|
| [001](#adr-001) | Exclude TMDB; use IMDb dumps + OMDb | Accepted |
| [002](#adr-002) | Supabase Postgres over SQLite | Accepted |
| [003](#adr-003) | Raw SQL with psycopg3 rather than an ORM | Accepted |
| [004](#adr-004) | UUIDv7 primary keys | Accepted |
| [005](#adr-005) | Ollama locally, hosted API when deployed | Accepted |
| [006](#adr-006) | Ratings stored separately from watch entries | Accepted |
| [007](#adr-007) | Retrieval happens outside the LLM | Accepted |
| [008](#adr-008) | Jinja now, React later | Accepted |
| [009](#adr-009) | Archive untracked files rather than delete | Accepted |
| [010](#adr-010) | Keep the spec out of the public repo | Accepted |

---

## ADR-001

### Exclude TMDB; build the catalog from the IMDb dumps plus OMDb

**Status:** Accepted · 2026-09-12

**Context.** The app needs a catalog of movies and TV shows we haven't seen.
TMDB is the obvious choice — it has a genuinely good discovery API with
`popular`, `similar`, and genre browsing, which is exactly what a recommender
wants. OMDb has no discovery endpoint at all: you can only look up a title you
can already name. The IMDb non-commercial dumps have no plot text.

**Decision.** Do not use TMDB. Build the catalog from the IMDb dumps (structure
and the candidate universe) and enrich it with OMDb (plot, cast, posters).

**Why.** TMDB's API Terms of Use prohibit "training or validating a machine
learning or artificial intelligence system (including large language models and
Chatbots) using TMDB content," and separately prohibit use "in connection with
interactive query-response systems including large language models (LLM),
artificial intelligence, or any other machine learning based interactive
query-response systems or chatbots."

This project is both of those things — the vector store is a collected dataset,
and the chatbot is an interactive query-response system. Being non-commercial is
not an exemption.

IMDb's datasets are licensed for "personal and non-commercial use" and
explicitly permit holding local copies. OMDb is CC BY-NC 4.0. Both fit.

**Consequences.**
- No discovery API, so the candidate pool comes from a bulk load rather than a
  live query. This turns out to be *better* for our purposes: filtering 65k
  local rows in SQL is faster and free compared with paging an external API.
- Two ingest steps to build and maintain instead of one API call.
- Binding constraints: non-commercial forever, no redistribution of the data,
  and attribution in the app footer.
- Any future data source gets a licence check recorded here *before* code is
  written.

**Revisit if.** The project ever needs to become commercial — at which point
both data sources have to be replaced, not just re-licensed.

---

## ADR-002

### Supabase Postgres instead of local SQLite

**Status:** Accepted · 2026-09-12

**Context.** The app ran on a local SQLite file. The recommender needs vector
similarity search over tens of thousands of embeddings, and the app needs to
serve two people, eventually from a deployed host.

**Decision.** Move to Postgres on Supabase, with pgvector.

**Why.** SQLite has no native vector type and no pgvector equivalent. Supabase
gives Postgres, pgvector, auth, and row-level security in one free tier, and
Postgres is what this would run on in production anyway.

**Consequences.**
- Free tier caps at **500 MB**, which is the binding constraint on how many
  titles the catalog can hold. See the sizing table in `SPEC.md` §5.1.
- Free projects **pause after 1 week of inactivity** — a real annoyance for an
  app two people use occasionally. Unresolved; options are a weekly cron ping,
  living with the unpause click, or $25/mo Pro.
- The app now needs network access to run, where SQLite needed nothing.
- We get RLS for free, which is how Phase 5 enforces per-household privacy.

**Revisit if.** The 500 MB cap forces the catalog smaller than is useful, or the
idle-pause becomes intolerable. Either points to Supabase Pro rather than to a
different database.

---

## ADR-003

### Write SQL by hand with psycopg3 rather than use an ORM

**Status:** Accepted · 2026-09-12

**Context.** Moving to Postgres meant rewriting the data layer, which was the
natural moment to decide whether to adopt SQLAlchemy. The original spec assumed
we would. An ORM maps database rows to Python objects so you work with
`entry.manual_title` instead of writing `SELECT`/`UPDATE` statements yourself.

**Decision.** Use psycopg3 with hand-written, parameterised SQL behind a
repository layer (`app/repositories/`). No SQLAlchemy, no Alembic.

**Why.**

1. **Practising SQL is an explicit goal.** The owner wants the reps of writing
   queries by hand. An ORM removes exactly that practice — which is its selling
   point for most teams and its disadvantage here.
2. **Understanding N+1 by being able to see it.** The classic ORM failure is
   looping over parent rows and touching a related collection, firing one query
   per iteration — 24,001 queries where one JOIN would do. It's invisible in ORM
   code and obvious in raw SQL, where you'd physically see a query inside a loop.
   Writing the JOINs by hand builds the instinct for spotting it later.
3. **The hard queries stay raw either way.** The Phase 7 recommender blends
   cosine distance with a quality prior over pgvector. That is hand-written SQL
   under SQLAlchemy too, so the ORM adds nothing where the difficulty actually
   is.
4. **Continuity.** The existing codebase was already raw SQL. Introducing
   sessions, the identity map, and lazy-vs-eager loading mid-project adds
   concepts without solving a problem we have.

**Consequences.**
- *Good:* every query that runs is visible in the source. No generated SQL to
  reverse-engineer, no lazy-loading surprises, no `DetachedInstanceError`.
- *Good:* one less large dependency, and faster — no mapping layer.
- *Bad:* more repetitive code. Row-to-dict mapping is written by hand
  (`_row_to_movie`).
- *Bad:* no Alembic, so migrations are a hand-rolled runner
  (`scripts/migrate.py`, ~60 lines) with a `schema_migrations` table. It works
  and it's simple, but it won't autogenerate migrations from model changes.
- *Bad:* SQLAlchemy is named constantly in Python backend job ads, and this
  project won't demonstrate it. Mitigated by being able to explain *why* — which
  is a stronger interview answer than having used it once.
- No relationship navigation: `entry.ratings` doesn't exist, the JOIN is written
  out.

**Revisit if.** Write paths get complex enough that hand-mapping becomes a
genuine source of bugs, or migrations start being painful to hand-write. The
cost of switching rises with each phase — cheapest now, moderate after Phase 3,
significant after Phase 7.

---

## ADR-004

### UUID primary keys on user-facing tables

**Status:** Accepted · 2026-09-13 *(v4 in migration 001, moved to v7 in 003)*

**Context.** The old SQLite schema used autoincrementing integers. Supabase Auth
issues UUIDs for users, so `profiles` must be UUID-keyed regardless; the choice
is whether the other tables match it.

**Decision.** UUID primary keys, using **UUIDv7** (`migrations/003_uuidv7.sql`).
Migration 001 originally used `gen_random_uuid()` (v4); 003 replaced it.

**Why.**
- Mixing integer and UUID keys across one schema is a constant papercut.
- Sequential integers leak information: `/movie-edit/5` reveals roughly how many
  entries exist.
- No collisions if entries are ever created from more than one source.

**Consequences.**
- URLs went from `/movie-edit/5` to
  `/movie-edit/289f410e-878c-4702-8a59-2291c8ce6fdd`.
- Harder to type by hand when debugging.
- **UUIDv4 is random, so it carries no ordering** — and that directly caused a
  bug. See ADR-004a below.

### ADR-004a — the ordering bug this caused

`scripts/migrate_sqlite_data.py` inserted all five rows in a single transaction.
In Postgres, **`now()` returns the transaction start time, not the current
instant**, so every row received a byte-identical `created_at`. The default sort
(`created_at desc, id desc`) fell through to its tiebreak — a random UUIDv4 —
and the list order scrambled.

Fixed by staggering `created_at` by each row's original SQLite id, so
newest-first reproduces the old ordering exactly. Verified against the old app.

That fix works but is a workaround. **UUIDv7 would have made the bug
impossible**: v7 encodes a timestamp in its leading bits, so v7 keys sort
chronologically and `order by id` means "oldest first" for free. Postgres 18
ships `uuidv7()`; we're on 17.6, so it would need a small generator function.

(For the record: `clock_timestamp()` is the function that returns the true
wall-clock instant inside a transaction.)

### Resolution — UUIDv7, and a proper fix for the root cause

Two changes in `migrations/003_uuidv7.sql`:

**1. UUIDv7 keys.** v7 keeps the UUID format and uniqueness guarantees but puts
a 48-bit millisecond timestamp in the leading bits, so keys sort in creation
order. `order by id` is now meaningful, and index inserts append rather than
scatter.

Postgres 18 ships `uuidv7()` natively; this database is 17.6, so the migration
defines a SQL function of the same name. **When the platform reaches Postgres
18, drop the function** — the column defaults reference the name, not the
implementation, so the built-in takes over with no further change.

Chosen over integers because Supabase Auth issues UUIDs for users, so `profiles`
must be UUID-keyed regardless, and mixing key types across one schema is a
constant papercut.

**2. `clock_timestamp()` instead of `now()` for timestamp defaults.** This is
the actual root cause from ADR-004a, and fixing it removed the workaround
entirely — `migrate_sqlite_data.py` no longer staggers `created_at`, because
rows inserted in order now receive genuinely ascending timestamps.

The previous fix treated the symptom. v7 alone would also have treated the
symptom: the real defect was a timestamp default that returns the same value for
every row in a transaction.

**Verified.** The shim's version nibble is 7 and its variant nibble is in 8–b;
the encoded timestamp matches the server clock to within a millisecond (compare
against the *server* clock, not the client's — a drifting local clock produces
false failures); five rows inserted in one transaction sort correctly by id and
receive five distinct timestamps. The five existing entries were re-migrated
from the untouched SQLite source so every key in the database is v7.
`tests/test_uuidv7.py` pins all of it.

**Revisit if.** The platform reaches Postgres 18 — at which point this is a
one-line `drop function` rather than a decision.

---

## ADR-005

### Ollama locally; swap to a hosted API only when deployed

**Status:** Accepted · 2026-09-12

**Context.** The chatbot and the embeddings both need a model. The dev machine
has a consumer GPU with 16 GB of VRAM, which runs 12–14B models comfortably.
Learning how the AI pieces fit together is an explicit goal of the project.

**Decision.** Put the model behind an `LLMProvider` interface with `embed()` and
`chat()`. Run Ollama locally. Switch the implementation to a hosted API at
deploy time. **Never deploy Ollama.**

**Why.** Local is free and transparent — you can watch each step. But a GPU VM
costs $200–400/month, which is absurd for two users. The saving grace is that
embeddings are generated **once**, offline, and stored in Postgres, so they cost
nothing at request time. Only chat needs a model when deployed, and for two
people that is roughly $2–4/month on Claude Haiku.

**Consequences.**
- Nothing above the interface knows which provider is in use; switching is a
  config change, not a rewrite.
- Local and hosted models will produce different quality prose. Retrieval is a
  separate stage and is unaffected.
- If we ever re-embed the catalog with a different model, every stored vector
  has to be regenerated.

**Revisit if.** Local model quality proves too weak to be worth developing
against, in which case develop against the hosted API directly.

---

## ADR-006

### Store ratings separately from watch entries

**Status:** Accepted · 2026-09-12

**Context.** The old schema had one `rating` column on the movie row. The app is
for two people who will disagree about films.

**Decision.** `watch_entries` records the household-level fact that a title was
seen. `entry_ratings` records one row per person per entry, carrying their
rating, their review text, and its embedding.

**Why.** A single column cannot express "he gave it a 9, she gave it a 6" — and
that disagreement is precisely the signal that makes joint movie-night
recommendations work. Recommending for two people means scoring candidates
against each taste profile separately, which is impossible if the two opinions
were averaged at write time.

**Consequences.**
- Every read needs a JOIN, and the repository filters to the current profile.
- Enables the household scoring rule (rank by the *minimum* of the two scores —
  the title neither person dislikes — rather than by the average, which finds
  mediocre middle ground).
- A rating row's existence means "this person watched it"; the rating value
  itself is nullable, so watched-but-unrated is representable.

---

## ADR-007

### Retrieval happens outside the LLM

**Status:** Accepted · 2026-09-12

**Context.** The chatbot recommends titles from a catalog of tens of thousands.
The obvious naive design is to give the model tools and let it search.

**Decision.** Retrieve with SQL and pgvector in three stages, then hand the model
a fixed context block. The LLM only explains; it never searches.

1. SQL filter (~65k → ~300): exclude watched and dismissed, apply hard filters
2. Vector rerank (~300 → ~20): cosine distance blended with a quality prior
3. LLM (~20 → shown): one sentence of reasoning per title

**Why.** The model can only name titles present in the context block, so it
cannot hallucinate a film that doesn't exist — the single most embarrassing
failure mode for a recommender. It's also far cheaper and faster, and each stage
can be tested on its own. Stage 1 needs no model at all.

**Consequences.**
- Recommendation quality is mostly a *retrieval* problem, not a prompting one.
  Tuning happens in SQL and in the embedding text, not in the prompt.
- The model cannot surface something outside the catalog even when that would be
  the right answer.
- Small local models are adequate, because the hard thinking already happened in
  stages 1 and 2.

---

## ADR-008

### Keep Jinja templates now; rewrite in React later

**Status:** Accepted · 2026-09-12

**Context.** The UI is server-rendered Jinja with vanilla JS and no build step.
React dominates job postings, and the project doubles as interview preparation.

**Decision.** Stay on Jinja through the data and recommendation phases. Rewrite
in React as its own phase (Phase 9), against the stable REST API.

**Why.** Learning embeddings and RAG while also fighting a build toolchain is two
hard things at once. The RAG pipeline is the part that differentiates this
project; a React rewrite of a CRUD screen is table stakes. Learning React
against an API you built yourself is much easier than learning both together —
and committing v1 and v2 separately makes the git history show the progression.

**Consequences.**
- Some template work gets thrown away in Phase 9. Accepted deliberately.
- The API has to be genuinely complete before the rewrite, which is good
  discipline anyway.

---

## ADR-009

### Archive untracked files rather than deleting them

**Status:** Accepted · 2026-09-12

**Context.** Phase 1 cleanup targeted a dead `main.py`, a tutorial app, two
broken Dockerfiles, planning notes, and an abandoned Node experiment. The
`.gitignore` used a whitelist (`/*` then explicit `!` allows), so **none of them
had ever been committed** — git could not bring them back.

**Decision.** Move them to `../Netflix Project - Archive/` with a README
explaining each item, rather than deleting. Only regenerable artifacts
(`node_modules/`, `__pycache__/`) were deleted outright.

**Why.** "Delete" on an untracked file is permanent. The repo gets equally clean
either way, and the archive costs nothing.

**Consequences.** A folder of dead code sits beside the repo until manually
removed. Deliberate — deleting it is a one-line decision available any time.

---

## ADR-010

### Keep `docs/SPEC.md` out of the public repository

**Status:** Accepted · 2026-09-12

**Context.** This repository is public. The spec contains details the owner
prefers to keep off a public repo.

**Decision.** `/docs/` is gitignored. The spec lives on disk only and has never
appeared in any commit.

**Why.** Owner's preference. Nothing in it is sensitive, but once pushed to a
public repo it is in the history even if later edited out.

**Consequences.**
- The spec is **not backed up and not version-controlled**. If the file is lost
  it is gone. Worth a copy outside the repo, or a private gist.
- This file is under the same rule, though it contains no personal detail — it
  could be published as-is if wanted.

**Revisit if.** A scrubbed public version is wanted for the portfolio. The
licensing analysis and cost modelling are the parts worth showing, and neither
needs personal detail.
