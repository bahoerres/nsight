-- Bootstrap the read-only role used by the nsight MCP server.
-- Run as the healthdash superuser, passing the desired password:
--   docker cp install_role.sql healthdash-postgres:/tmp/
--   docker exec -e RO_PW=... healthdash-postgres psql -U healthdash -d healthdash \
--       -v ro_password="$RO_PW" -f /tmp/install_role.sql
--
-- Idempotent: safe to re-run. Creates the role if missing, otherwise resets
-- the password and re-applies all grants.

-- CREATE if missing. (psql variables don't substitute inside DO $$ ... $$ blocks,
-- so we use SELECT + \gexec to conditionally emit the DDL.)
SELECT format('CREATE ROLE healthdash_ro LOGIN PASSWORD %L', :'ro_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'healthdash_ro')
\gexec

-- ALTER if already present (re-set password, idempotent across re-runs).
SELECT format('ALTER ROLE healthdash_ro WITH LOGIN PASSWORD %L', :'ro_password')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'healthdash_ro')
\gexec

-- 30s ceiling on any single statement. Defense against runaway SELECTs.
ALTER ROLE healthdash_ro SET statement_timeout = '30s';

-- Connect + read everything in public schema. No INSERT/UPDATE/DELETE/DDL grants.
GRANT CONNECT ON DATABASE healthdash TO healthdash_ro;
GRANT USAGE ON SCHEMA public TO healthdash_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO healthdash_ro;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO healthdash_ro;

-- Future tables inherit SELECT automatically.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO healthdash_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON SEQUENCES TO healthdash_ro;

-- Belt-and-suspenders: explicitly revoke anything mutating that might exist.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON ALL TABLES IN SCHEMA public FROM healthdash_ro;
REVOKE CREATE ON SCHEMA public FROM healthdash_ro;
