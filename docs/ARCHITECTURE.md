# Architecture

A private movie and TV catalog that learns what its users like and recommends
what to watch next — as home-page shelves and through a chatbot that can explain
its suggestions.

This document covers the design and the reasoning behind it. Individual
decisions, with their trade-offs and the conditions that would reverse them, are
recorded as ADRs in [`DECISIONS.md`](DECISIONS.md).

---

## Three layers

1. **Catalog** — tens of thousands of movies and TV shows from the IMDb
   non-commercial datasets, enriched with plot, cast, and poster artwork from
   OMDb.
2. **Personal log** — what the household has watched, who watched it, and what
   each person thought, in prose as well as numbers.
3. **Recommender** — a retrieval pipeline matching taste against the catalog,
   surfaced both as browsable shelves and as a conversational interface.

---

## Data sources

The obvious choice for a recommender is TMDB, which has a genuinely good
discovery API. **It is deliberately not used here.** TMDB's API Terms of Use
prohibit using their content "in connection with interactive query-response
systems including large language models (LLM), artificial intelligence, or any
other machine learning based interactive query-response systems or chatbots,"
and separately prohibit collecting it into datasets for ML training or
validation. This project is both of those things, and being non-commercial is
not an exemption. See ADR-001.

What is used instead:

| Source | Provides | Licence |
|---|---|---|
| IMDb non-commercial datasets | Title, type, year, runtime, genres, rating, vote count | Personal, non-commercial use; local copies permitted; no redistribution |
| OMDb | Plot, cast, director, content rating, awards, poster URL, Rotten Tomatoes and Metacritic scores | CC BY-NC 4.0 |

The two are complementary, and the split matters. The IMDb dumps give a large
structured candidate universe but **no prose** — and prose is what a semantic
search needs to embed. OMDb supplies that prose but has **no discovery
endpoint**: you can only look up a title you can already name. Neither is
sufficient alone.

A consequence worth noting: because there is no discovery API, candidates come
from a local bulk load rather than a live query. That turns out to be *better*
for this use case — filtering tens of thousands of local rows in SQL is faster
and free compared with paging an external API.

---

## System shape

```
                ┌──────────────────────────────────────┐
                │  Browser                             │
                │  shelves · catalog · chat            │
                └──────────────┬───────────────────────┘
                               │  REST + SSE
                ┌──────────────▼───────────────────────┐
                │  FastAPI                             │
                │  routes/ → services/ → repositories/ │
                └───┬──────────────┬───────────────┬───┘
                    │              │               │
           ┌────────▼──────┐ ┌─────▼──────┐ ┌──────▼───────┐
           │ Postgres      │ │ LLMProvider│ │ OMDb client  │
           │ + pgvector    │ │ local ⇄    │ │ (enrichment, │
           │ + auth + RLS  │ │ hosted API │ │  offline)    │
           └───────────────┘ └────────────┘ └──────────────┘

  Offline pipeline:
    IMDb dumps → ingest → titles → OMDb enrich → embed → pgvector
```

**Layering rule:** routes never touch SQL. `routes/` handles HTTP, `services/`
holds business logic, `repositories/` owns data access. That boundary is what
makes the recommender testable without a web server.

Data access is hand-written parameterised SQL over psycopg3 rather than an ORM —
the reasoning, including what would reverse it, is in ADR-003.

---

## Identity and isolation

The app is invite-only: accounts are created by a script using the service key,
and there is no registration route. Sign-in posts credentials to Supabase Auth
and receives an ES256 JWT, which is verified against the project's JWKS on every
request and carried in an `httpOnly`, `SameSite=Lax` cookie. Passwords are
stored by Supabase as per-user-salted bcrypt hashes; this application never
persists or logs them.

Data isolation is enforced by Postgres row-level security rather than by
application code alone - but making that real took more than writing policies.

**The application connects as the table owner, which bypasses RLS.** Postgres
skips row-level security for a table's owner, so policies alone would have
looked correct and enforced nothing, silently. Each request therefore switches
to a role that does not bypass RLS, and carries the profile in a
transaction-scoped setting that the policies read:

```sql
set local role authenticated;
set local app.current_profile_id = '<uuid>';
```

`SET LOCAL` rather than `SET` matters because connections are pooled: a
session-scoped setting would leave one request's identity on the connection for
the next borrower to inherit.

That identity is bound by **pure ASGI middleware**, not a dependency and not
`BaseHTTPMiddleware`. Both of those run the downstream code in a separate task
whose context was copied too early, so the value never arrives - a failure that
is completely silent. See ADR-016.

Four layers have to hold, so no single mistake exposes data: token verification,
the context binding, an application-level guard that raises when no identity is
in scope, and the policies themselves. Maintenance scripts deliberately connect
as the owner and bypass all of it - they tend the shared catalog and have no
user context.

Read and write rules differ where it matters: a household's ratings and reviews
are readable by everyone in it, because that is what makes a joint
recommendation possible, but only their author may edit them.

---

## Data model

| Table | Holds |
|---|---|
| `titles` | The catalog. IMDb structured fields, OMDb prose and artwork, a `halfvec(768)` embedding, and a generated `tsvector` for keyword search. |
| `households` · `profiles` | A household shares a catalog; profiles hold individual taste vectors. |
| `watch_entries` | Household-level record that a title was seen. |
| `entry_ratings` | **Per-person** rating, review text, and review embedding. |
| `entry_tags` | Free-form mood tags — no fixed vocabulary. |
| `dismissals` | "Not interested", so recommendations stop resurfacing a title. |
| `recommendations` | Precomputed shelves. |
| `chat_sessions` · `chat_messages` | Conversation history, including the ids retrieved for each answer. |

Three decisions in that table are load-bearing:

**Ratings are separate from entries.** A single rating column on the entry
cannot express "one person gave it a 9, the other a 6" — and that disagreement
is exactly the signal that makes joint recommendations work. Averaging at write
time destroys it permanently. See ADR-006.

**Entries can exist without a catalog match.** `imdb_id` is nullable, with
`manual_*` columns as a fallback, so an obscure title can still be logged. Reads
coalesce across both, which keeps the API stable as entries are later linked to
real catalog rows.

**Chat messages store what was retrieved.** Every answer records the title ids
that were in its context, which makes a recommendation auditable after the fact
rather than a black box.

### Keys and time

Primary keys are **UUIDv7** — the format encodes a millisecond timestamp in its
leading bits, so keys sort in creation order and index inserts append rather
than scatter. Timestamp defaults use `clock_timestamp()`, not `now()`; `now()`
returns the *transaction* start time, so rows written in one transaction would
otherwise share a timestamp and lose their ordering. See ADR-004.

---

## Recommendation pipeline

**The central decision: the model does not search the catalog.** Retrieval
happens in SQL and pgvector; the model only explains the result.

| Stage | Mechanism | Scale |
|---|---|---|
| 1. Filter | SQL — exclude watched and dismissed, apply genre / rating / year / type constraints | ~65,000 → ~300 |
| 2. Rerank | pgvector cosine distance, blended with a quality prior | ~300 → ~20 |
| 3. Explain | LLM writes one sentence of reasoning per title | ~20 → shown |

```
score = 0.65 × semantic_similarity
      + 0.25 × normalized_rating
      + 0.10 × log_normalized_votes
```

The quality prior matters: without it, a strong semantic match with a poor
rating wins on similarity alone.

**Why retrieval sits outside the model.** It can only name titles present in the
context it was given, so it cannot invent a film that does not exist — the most
embarrassing failure mode a recommender has. It is also far cheaper and faster,
each stage is testable independently, and stage 1 needs no model at all.

The practical consequence is that recommendation quality is mostly a *retrieval*
problem rather than a prompting one. Tuning happens in SQL and in the text
chosen for embedding, not in the prompt.

### Taste vectors

A profile's taste vector is the mean of the embeddings of titles that person
rated, weighted by `(rating − 5) / 5` — so low ratings push *away* from a region
of the space rather than being ignored. Where a written review exists, its
embedding is blended in: what someone says about a film is better signal than
the film's own plot summary.

For household recommendations, the two taste vectors are **not** averaged —
averaging finds mediocre middle ground. Candidates are scored against each
profile separately and ranked by the **minimum** of the two scores: the title
neither person dislikes.

---

## Model hosting

The model sits behind a provider interface:

```python
class LLMProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def chat(self, system: str, messages: list[Message]) -> Iterator[str]: ...
```

Locally that is Ollama. Deployed, it is a hosted API, selected by configuration —
nothing above the interface knows which is in use.

The economics are what drive this. A GPU host costs hundreds of dollars a month,
which is untenable for a household app. But **embeddings are generated once,
offline, and stored in Postgres**, so they cost nothing at request time. Only
chat needs a model when deployed, and at household volume that is a couple of
dollars a month. See ADR-005.

---

## Attribution

Data from the IMDb non-commercial datasets and the OMDb API.
Not endorsed by or affiliated with IMDb.com.
