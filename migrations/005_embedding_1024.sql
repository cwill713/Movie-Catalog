-- 005_embedding_1024.sql
-- Widen every embedding column from halfvec(768) to halfvec(1024). Closes T12.
--
-- Why: the schema guessed 768 before a model was chosen. The installed model is
-- `mxbai-embed-large`, which returns 1024 dimensions - verified by calling it,
-- not assumed. `nomic-embed-text` (the 768 alternative) is not installed at all.
-- A vector's length is fixed by the model that produced it; writing a 1024-dim
-- vector into a halfvec(768) column fails outright, so the two numbers have to
-- agree before any embedding run.
--
-- Why now, and why this is free: all three columns are empty (0 of 37,605
-- titles, 0 of 2 profiles, 0 of 8 ratings). There is no data to convert, so no
-- USING clause and no rewrite - this is a catalog change. ADR-005 warns that
-- changing the model means regenerating every vector; that cost applies to
-- changing course *after* a run, which is precisely why this happens first.
--
-- Why all three and not just `titles`: Phase 6 only needs the catalog, but
-- vectors from one model are not comparable with another's, and leaving
-- profiles/entry_ratings at 768 only hides the same mismatch until Phase 7.
--
-- `titles_need_embed_idx` has `embedding is null` in its predicate, so Postgres
-- rebuilds it as part of the type change. Both are empty; nothing to do here.

alter table titles        alter column embedding        type halfvec(1024);
alter table profiles      alter column taste_embedding  type halfvec(1024);
alter table entry_ratings alter column review_embedding type halfvec(1024);
