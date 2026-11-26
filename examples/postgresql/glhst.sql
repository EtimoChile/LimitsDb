-- =========================================================
-- History owner of the GL test model on PostgreSQL (9.5 or later): the login role glhst and the schema glhst
-- that hold the history tables. Counterpart of examples/postgresql/gl.sql, which creates the source owner gl.
-- Oracle's GL and GLHST users come from the DBA instead.
--
-- Run it once in the database that holds the history, which can be the source database or another one,
-- on the same server or on a different one:
--
--   psql "postgresql://admin:pass@host/history_db" -v glhst_password=SECRET -f examples/postgresql/glhst.sql
--
-- Connect as an administrator (e.g. neondb_owner on Neon: it needs CREATEROLE), on a direct connection.
-- The script creates the login role glhst (or resets its password to glhst_password) and makes it the owner of
-- the schema glhst. It is safe to run again. ldb-run --mode PREPARE creates the history tables in that schema.
-- =========================================================

\set ON_ERROR_STOP on
\set QUIET on

\echo === Creating user and schema ===
-- The password travels as a session setting, never inside a statement text
SET ldb.glhst_password = :'glhst_password';

DO $$
BEGIN
  BEGIN
    EXECUTE format('CREATE ROLE glhst LOGIN PASSWORD %L', current_setting('ldb.glhst_password'));
  EXCEPTION WHEN duplicate_object THEN
    EXECUTE format('ALTER ROLE glhst LOGIN PASSWORD %L', current_setting('ldb.glhst_password'));
  END;
  -- The administrator keeps the SET option on glhst (PostgreSQL 16 drops it for the creator of a role): it needs
  -- it to create the history tables and hand their ownership to glhst
  IF current_setting('server_version_num')::integer >= 160000 THEN
    EXECUTE 'GRANT glhst TO CURRENT_USER WITH SET TRUE';
  ELSE
    EXECUTE 'GRANT glhst TO CURRENT_USER';
  END IF;
END
$$;
RESET ldb.glhst_password;

CREATE SCHEMA IF NOT EXISTS glhst AUTHORIZATION glhst;
-- A schema that already existed under another owner ends up owned by glhst as well
ALTER SCHEMA glhst OWNER TO glhst;

\echo === glhst ready ===
