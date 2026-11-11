-- 008_premise_columns.sql
-- Add a one-sentence "premise" per title and its embedding, for hybrid search.
--
-- Why: vague queries ("an ordinary teacher turns into a criminal after a bad
-- diagnosis") rank the target poorly against the stored text, which is title +
-- cast + plot. A short, name-free premise embeds closer to how people describe
-- a story. On an 18-query test (5,000-title sample) averaging the distance to
-- both vectors put 17 of 18 targets in the top 10, against 14 for the current
-- vector alone.
--
-- Additive and nullable: the existing `embedding` column is untouched, and
-- titles with no plot simply keep `premise` / `premise_embedding` null (search
-- falls back to the main vector for them). The columns are filled by
-- scripts/db_premise.py; the index comes later in 009, after the bulk fill, for
-- the same reason as ADR-019 (building a graph row by row re-links it each time).

alter table titles add column if not exists premise           text;
alter table titles add column if not exists premise_embedding halfvec(1024);
