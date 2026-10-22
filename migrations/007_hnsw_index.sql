-- 007_hnsw_index.sql
-- Create a HNSW index on the titles table for fast vector similarity search.

-- The build is slow; lift the role's statement timeout. Parallel workers share
-- memory through /dev/shm, which is too small on this instance (error 53100),
-- so build single-process.
set statement_timeout = 0;
set max_parallel_maintenance_workers = 0;
set maintenance_work_mem = '128MB';

CREATE INDEX IF NOT EXISTS titles_hnsw_index
    ON titles
    USING hnsw (embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 200);
