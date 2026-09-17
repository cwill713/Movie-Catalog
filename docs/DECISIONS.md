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
| [010](#adr-010) | Split docs between a public and a private repo | Accepted |
| [011](#adr-011) | Bulk-load the catalog with COPY into a staging table | Accepted |
| [012](#adr-012) | Catalog depth chosen by measurement, not estimate | Accepted |
| [013](#adr-013) | Rank search by word similarity, not string similarity | Accepted |
| [014](#adr-014) | Row-level security via a non-owner role | Accepted |
| [015](#adr-015) | Credential handling, and HTTPS before deployment | Accepted |
| [016](#adr-016) | Bind request identity in pure ASGI middleware | Accepted |

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

### Split the docs between a public and a private repository

**Status:** Accepted · 2026-09-12, extended 2026-09-13

**Context.** This repository is public. The working spec and the session worklog
carry personal detail and conversational context the owner prefers to keep off
it. But the design reasoning itself is the most portfolio-relevant work in the
project, and hiding all of it is a real cost.

Initially everything under `docs/` was simply gitignored, which left both files
**unversioned and existing in exactly one place on one disk**.

**Decision.** Split by audience rather than by folder:

| File | Home |
|---|---|
| `DECISIONS.md` (this file) | Public repo |
| `ARCHITECTURE.md` | Public repo |
| `SPEC.md` | Private repo |
| `WORKLOG.md` | Private repo |

`docs/` is its own git repository with a private remote, so the private pair is
version-controlled and backed up off-machine. The public repo's `.gitignore`
ignores `docs/*` and re-allows only the two published files.

`ARCHITECTURE.md` was written fresh rather than scrubbed from `SPEC.md` — it
carries the design and reasoning without the phase plan, open questions, or
personal framing, so the working spec stays entirely private.

**Why.** The earlier all-or-nothing rule forced a bad trade: publish personal
detail, or publish nothing. Splitting by audience gets the reasoning out where
it can be read while keeping the private material private — and fixes the backup
gap, which was the larger risk.

**Consequences.**
- Published docs must stay free of personal data. Enforced by
  `tests/test_decisions_doc.py`, which scans both for names, usernames, emails,
  hardware models, and repo handles, and separately asserts that each of the four
  files is on the correct side of the line.
- `ARCHITECTURE.md` and `SPEC.md` overlap and can drift. Accepted: the
  architecture doc describes the design as built, the spec plans what is next.
- Two repositories to push to.

**Gotcha worth recording.** A `.gitignore` inside `docs/` is read by the *parent*
repository as well, regardless of which repo owns the folder. Putting the
published-file exclusions there hid them from the public repo too. They belong
in `docs/.git/info/exclude`, which is scoped to one repository.


---

## ADR-011

### Bulk-load the catalog with COPY into a staging table

**Status:** Accepted · 2026-09-13

**Context.** The IMDb dumps hold 12,779,198 titles across two gzipped TSV files.
The catalog needs a filtered subset of them, refreshed whenever the dumps are
re-downloaded.

**Decision.** Stream both files in Python, `COPY` the surviving rows into a temp
staging table, then a single `INSERT ... ON CONFLICT DO UPDATE` into `titles`.

**Why.**
- `COPY` is the fastest way into Postgres by a wide margin; row-by-row
  `INSERT` of tens of thousands of rows over a network round-trip each is
  orders of magnitude slower.
- Staging first means the upsert is one statement in one transaction — the
  catalog is never half-updated.
- `ON CONFLICT DO UPDATE` makes re-running idempotent, and re-running with a
  lower vote threshold adds newly-qualifying titles without disturbing
  existing rows.

**Two details that matter.**

The upsert **only refreshes IMDb-sourced columns.** `plot`, `poster_url`,
`embedding` and the rest of the OMDb and embedding columns are left untouched.
Those are expensive to regenerate — a re-ingest must never silently discard
them.

The ratings file is **filtered while reading**, before the titles pass. Keeping
only entries that clear the vote threshold holds ~40k rows in memory instead of
the full 1.7M.

**Consequences.**
- Scanning all 12.8M rows takes ~9 seconds; the upsert ~1 second.
- Re-ingesting is cheap enough to be routine rather than a maintenance event.
- Episode rows (~77% of the dumps) are filtered out entirely. Episode-level
  tracking would need a separate ingest path.

---

## ADR-012

### Catalog depth chosen by measurement, not estimate

**Status:** Accepted · 2026-09-13 — settled at **2,500 votes / 37,605 titles**

**Context.** Catalog size is capped by the database's 500 MB limit. Planning
estimated ~137 MB for 24k titles fully loaded, which implied 24k was near the
practical ceiling.

**Measured at 24,213 titles**, structured data only:

| | |
|---|---|
| Heap (row data) | 5.2 MB |
| Indexes | 12.1 MB |
| Titles total | 17.3 MB |
| **Per title** | **750 bytes** |

The estimate was wrong in a useful direction. Indexes are more than twice the
row data — seven of them, including two GIN indexes — but the total is far below
what was projected.

**Projected fully loaded** (structured + OMDb prose + a 768-dim `halfvec` +
HNSW index) at roughly 5.2 KB per title:

| Min votes | Titles | Structured | Fully loaded |
|---|---|---|---|
| 25,000 | 8,644 | 6 MB | 44 MB |
| 10,000 | 15,547 | 11 MB | 78 MB |
| **5,000** | **24,213** | **17 MB** | **122 MB** |
| 2,500 | 37,605 | 27 MB | 190 MB |
| 1,000 | 65,166 | 47 MB | 328 MB |
| 500 | 95,374 | 68 MB | 481 MB ✗ |

**Resolved.** Enrichment supplied the missing number: plot text averages **486
characters**, not the 1,400 estimated. A title costs ~1,677 bytes enriched, so
~4.8 KB once a vector and its index are added.

Settled on **>=2,500 votes -> 37,605 titles**, over the more aggressive >=1,000
(65,166, ~62% of the tier). Currently 84 MB / 17%; projected ~196 MB / 39% once
embeddings land. The deeper tier was affordable but left little room for an HNSW
rebuild or a second embedding model, and >=2,500 already triples the per-year
depth of the original >=5,000.

**The one number still unknown** is how large OMDb plot text actually is; 1.4 KB
per title is a guess, and it is the largest uncertain term. That figure lands in
Phase 4.

Because structured rows are so cheap (47 MB for 65k), loading deep and enriching
shallow is available as a middle path: every title becomes searchable and
filterable, while only the most popular tier gets prose and embeddings.

**Revisit if.** The recommender feels like it only knows obvious titles.
Re-ingesting is ~4 seconds, enrichment resumes automatically, and both scripts
are idempotent - so going deeper later costs only the new titles.


---

## ADR-013

### Rank search by word similarity, not whole-string similarity

**Status:** Accepted · 2026-09-13

**Context.** Catalog search ranked by `similarity(primary_title, query)`, which
compares whole strings and therefore penalises long titles. Searching `dragon`
scored a film literally called *Dragon* at 1.00 and *How to Train Your Dragon*
- 908,000 votes against 19,000 - at 0.29.

This looked fine at 24,213 titles. At 37,605 there were enough short matches to
fill the entire first page, and the popular title left the top 60 altogether.
**The bug was always there; more data exposed it.**

**Decision.** Rank by a blend of `word_similarity(query, primary_title)` and a
log-normalised vote count, weighted 70/30.

`word_similarity` scores the query against the best-matching *word* in the
title, so length stops mattering and every title containing the word scores
1.00. Popularity then orders them, which is what someone typing one word wants.

**Two traps, both hit while implementing this.**

*Operator direction.* `a <% b` means "a matches a word inside b". `%>` is its
commutator and takes the arguments the other way round. Using `%>` by mistake
silently returned nonsense - `'shawshak' %> primary_title` matched *Shag*
rather than *The Shawshank Redemption*. No error, just wrong results.

*Index support.* `%>` cannot use the GIN trigram index, and **one unindexable
branch in an `OR` forces a sequential scan of the whole table** - 246 ms against
10 ms. `<%` is indexable, so the final `WHERE` is three indexed branches under a
BitmapOr: `ilike` for substrings, `%` for typos on short titles, and `<%` for
typos on long ones.

That third branch earns its place: `shawshak` scores only 0.231 whole-string
similarity against *The Shawshank Redemption* - under the 0.3 threshold - but
0.750 word similarity. Without it, that search returns nothing at all.

An intermediate "fix" that simply deleted the word-similarity branch restored
the speed and quietly lost that capability. The right answer was the correctly
oriented, indexable operator, not dropping the feature.

**Consequences.**
- Search is 8 ms server-side; the latency a user perceives is network
  round-trip to a hosted database, not query time.
- The 70/30 weighting is a tuning knob, and the same shape of problem returns
  in the recommender, which blends semantic distance with a quality prior.
- Regression tests cover the exact case that broke, plus typo resolution and
  popularity ordering.

**Revisit if.** Search quality complaints. The 70/30 split is a guess that
behaves well, not a measured optimum.


---

## ADR-014

### Enforce row-level security through a non-owner role

**Status:** Accepted · 2026-09-13

**Context.** The app needs per-household data isolation. Postgres row-level
security is the obvious mechanism, and the plan assumed writing policies would
be enough.

It would not have been. The application connects as `postgres`, which **owns
every table and has `rolbypassrls = true`**. Postgres skips RLS entirely for a
table's owner. Policies written without addressing that would have passed
review, looked correct in the migration, and enforced nothing - with no error,
no warning, and no visible symptom. Every household would have seen everything.

**This is the failure mode worth naming: RLS is silent when it does not apply.**

**Decision.** Keep the direct SQL connection, but switch role per request.

Supabase provides an `authenticated` role that does not bypass RLS and that
`postgres` is permitted to `SET ROLE` to. Every request runs:

```sql
set local role authenticated;
set local app.current_profile_id = '<uuid>';
```

`SET LOCAL` is scoped to the **transaction**, not the session. That matters
because connections are pooled: session-scoped settings would leave one
request's identity on the connection for the next borrower to inherit.

Admin scripts (ingest, enrichment, embeddings) call `admin_connection()` and
stay as `postgres` deliberately - they maintain the shared catalog and have no
user context.

**Alternatives rejected.**

*Application-level scoping only* (every query carries `household_id`). Already
in place and still there as belt-and-braces, but it puts the entire security
boundary in application code, where one forgotten `WHERE` is a silent leak.

*Routing reads through PostgREST with the user's JWT.* RLS would apply
automatically, but it abandons the hand-written SQL that ADR-003 exists to
preserve.

**Consequences.**
- Policies genuinely enforce. Demonstrated: an unscoped `UPDATE` or `DELETE`
  from one household reaches only its own rows, and an `INSERT` into another
  household is refused outright.
- `USING` governs reads and which rows a write may touch; `WITH CHECK` governs
  what may be written. Both are needed - `USING` alone would permit inserting a
  row you then could not see.
- `app_current_household()` must be `SECURITY DEFINER`: it reads `profiles`,
  which is itself behind RLS, and would otherwise recurse through its own
  policy forever.
- Ratings and tags split read from write: the household's scores are readable,
  because that is what makes a joint recommendation possible, but only the
  owner may edit their own.
- **The `authenticated` role is shared between users.** Separation comes from
  the session setting, not the role, so the boundary ultimately rests on
  `app/auth.py` setting the profile id only from a verified token. The database
  enforces what it is told; it cannot verify who you are.
- A fourth guard sits above the database: `require_context()` raises when no
  identity is in scope, so a route missing its auth dependency fails loudly
  rather than running as nobody.

**Revisit if.** Supabase ever permits creating and assuming a bespoke role, in
which case a dedicated role per application would be marginally tidier.

---

## ADR-015

### Credential handling, and HTTPS everywhere

**Status:** Accepted · 2026-09-13

**Context.** The app has its own sign-in form rather than redirecting to a
hosted page, so credentials pass through it.

**How passwords are stored.** They are not. Supabase stores a **bcrypt hash**,
per-user salted, at cost factor 10:

```
$2a$10$Vv65goLVbvSTVdJ8NpIF9OrgukPkyNJG4MIZCjBE2FMFp2BuE3LvC
 |   |  \---- 22-char salt ----/\-------- hash --------/
 |   \- cost factor: 2^10 iterations
 \- bcrypt
```

The column is named `encrypted_password`, which is misleading - hashing is
one-way and has no key, so a database dump does not yield plaintext. Nobody,
including us, can recover a password; it can only be reset. The per-user salt
means two accounts with the same password store different values.

**Decision.**

1. The app never stores, logs, or persists a plaintext password. It exists only
   in memory for the moment it is forwarded to Supabase's token endpoint.
2. Passwords never appear in a URL or as a command-line argument.
   `scripts/create_user.py` prompts via `getpass` specifically so they stay out
   of shell history.
3. The session cookie is `httpOnly` (unreadable from JavaScript) and
   `SameSite=Lax`.
4. **HTTPS is a hard prerequisite for deployment.** Locally the form posts over
   plain HTTP, which is acceptable only because `127.0.0.1` never leaves the
   machine. Over the public internet, HTTP means anyone on the path reads the
   password in clear text.

**Deployment checklist - Phase 10 must not ship without all of these:**

- [ ] TLS terminated in front of the app; plain HTTP redirected to HTTPS
- [ ] `ENVIRONMENT` set to something other than `local`, so the session cookie
      becomes `Secure` (the code already keys off this)
- [ ] HSTS header set
- [ ] Verify `secure=True` on the cookie in the deployed response, rather than
      assuming the environment variable took effect

**Consequences.**
- Owning the sign-in form is a small, permanent responsibility. Redirecting to
  Supabase's hosted page would remove it, at the cost of a worse experience.
- The hop that actually crosses the internet - this app to Supabase - is
  already HTTPS. The gap is only between browser and app, and only once
  deployed.

**Revisit if.** The app ever needs to be reachable from outside the local
machine before Phase 10. That is the point at which HTTPS stops being a
checklist item and becomes blocking.


---

### ADR-015a - HTTPS locally too, not just once deployed

**Status:** Accepted · 2026-09-16 · amends the decision above

**What changed.** The original decision treated HTTPS as a Phase 10 deployment
prerequisite and accepted plain HTTP locally, on the grounds that loopback
traffic never leaves the machine. That reasoning still holds for eavesdropping,
but it misses the more likely failure: **a security control that only switches on
in production is a control nobody has ever actually run.**

The cookie flag made this concrete. It read `secure=not _is_local()`, which meant
the deployed configuration - the one that matters - was the single configuration
never exercised. A typo, an unset variable, or an environment string that didn't
match the expected list would all have produced a silently insecure cookie,
discovered in production or not at all.

**Decision.** The app is served over HTTPS everywhere, local development
included, and `secure=True` is set unconditionally. The environment-sniffing
helpers are deleted rather than left inert.

**Locally trusted certificates.** A self-signed certificate produces a browser
interstitial on every session, which trains people to click through security
warnings - a worse habit than the problem it solves. Instead a local certificate
authority is installed into the OS trust store and issues a certificate for
`localhost`, `127.0.0.1` and `::1`. The certificate and its key are gitignored;
the CA's private key lives outside the repository entirely.

**HSTS stays out of local configuration.** HSTS tells a browser never to use
plain HTTP for a host again, and it is scoped to the **hostname**, not the port
or the project. Setting it on `localhost` would pin every other project ever
served from `localhost` on that machine to HTTPS, including ones with no
certificate. It belongs only in the deployed configuration.

**A diagnostic trap worth recording.** Verifying the local certificate with a
command-line client built against the Windows TLS stack fails with
`CERT_TRUST_REVOCATION_STATUS_UNKNOWN`. That is a *revocation* check, not a trust
failure: a locally issued certificate carries no CRL or OCSP URL, so revocation
status cannot be determined, and the default policy refuses. Browsers skip
revocation checking for locally installed roots, so the browser is the
authoritative test here, not the command line.

**Consequences.**
- Running the app locally now requires the certificate files to exist; the
  server will not start without them.
- Local and deployed differ in one line of configuration (HSTS) instead of in
  the transport itself.
- The remaining Phase 10 checklist items are TLS termination with HTTP
  redirected, and HSTS. The cookie item is closed: `secure=True` is
  unconditional and pinned by a test that reads the raw `Set-Cookie` header,
  since a client speaking plain HTTP may drop a `Secure` cookie entirely and a
  cookie-jar assertion could pass while examining nothing.

**Revisit if.** Certificate management becomes a burden for additional
contributors, at which point a documented setup script is the answer rather than
reverting to plain HTTP.

---

## ADR-016

### Bind request identity in pure ASGI middleware

**Status:** Accepted · 2026-09-13

**Context.** Row-level security reads the current profile from a `ContextVar`
(ADR-014). Something has to put it there, once per request, early enough that
every query downstream sees it.

The obvious place is a FastAPI dependency. That does not work, and it fails
silently.

**Two attempts, both broken, both green in CI.**

*Attempt 1 - a dependency.* FastAPI runs sync dependencies and sync endpoints as
**separate threadpool tasks**, each receiving its own copy of the context. A
`ContextVar` set inside a dependency is invisible to the endpoint. Every
authenticated page returned 500 with "no signed-in profile in context".

*Attempt 2 - `@app.middleware("http")`.* Still broken. That decorator builds a
Starlette `BaseHTTPMiddleware`, which **spawns the downstream application as an
anyio task before running the dispatch body**. Tasks copy the context at spawn
time, so setting a variable in the body is already too late. This failure is
environment-dependent: the TestClient masked it, a real uvicorn server did not.

**Decision.** Bind identity in **pure ASGI middleware** - a plain class
implementing `__call__(scope, receive, send)`, installed with `add_middleware`.
Pure ASGI middleware runs in the *same task* as the application it wraps, so
values set there propagate to dependencies, endpoints and repositories alike.

The profile lookup is synchronous (psycopg), so it runs via `run_in_threadpool`
to keep it off the event loop; the ContextVars are then set back in the
request's own context, where they will actually propagate. The resolved profile
is also placed on `scope["state"]` so route dependencies can read it without a
second lookup.

**Consequences.**
- Identity is resolved exactly once per request, before anything else runs.
- `@app.middleware("http")` must not be used for anything context-sensitive in
  this codebase. A test asserts `IdentityMiddleware` is not a
  `BaseHTTPMiddleware` subclass, because that swap looks harmless and silently
  reintroduces the bug.
- Repositories keep their clean signatures - no profile threaded through every
  call - at the cost of relying on machinery that is subtle. The comments
  explain why, because the code alone looks like it could be simpler.

**The wider lesson, which cost more than the bug.** Both failures were invisible
to the test suite because an autouse fixture bound the profile at test level -
supplying out of band exactly what the application was failing to supply. The
suite verified everything except the thing that was broken, and reported 204
passing while every signed-in page was a 500 in the browser.

Tests now start with **no identity**. HTTP tests must obtain it from the real
middleware; tests that call repositories directly opt in explicitly. The fix was
verified by deleting the binding call and confirming 33 tests fail with the
exact production error - not by observing that they passed.

**A green test that should be red is worse than a red test.** A failing test
costs an hour; a false pass costs however long until a person stumbles into it.

**Revisit if.** The app moves to async route handlers throughout, which would
remove the threadpool boundary - though the middleware would still be the right
place.
