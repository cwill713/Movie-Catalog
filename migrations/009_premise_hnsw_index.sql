-- 009_premise_hnsw_index.sql
-- HNSW index on titles.premise_embedding, built after the bulk fill by
-- scripts/db_premise.py (same reasoning and settings as 007 / ADR-019).

set statement_timeout = 0;
set max_parallel_maintenance_workers = 0;
set maintenance_work_mem = '128MB';

CREATE INDEX IF NOT EXISTS titles_premise_hnsw_index
    ON titles
    USING hnsw (premise_embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 200);
