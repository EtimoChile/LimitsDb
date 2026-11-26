-- =========================================================
-- Statistics for the GL test model on PostgreSQL
-- PostgreSQL counterpart of examples/oracle/gl_stats.sql.
-- Row counts by status, with the min/max status date of each group, for one
-- database at a time: source and history are different databases, so run the
-- script once on each and compare. Run it before and after an ILM execution to
-- compare both snapshots.
--
-- Variables (both required):
--   owner  schema that holds the tables (gl on source, glhst on history)
--   env    label printed in the ENV column (SOURCE or HISTORY)
--
--   psql -h host -d source_db  -U gl    -v owner=gl    -v env=SOURCE  -f examples/postgresql/gl_stats.sql
--   psql -h host -d history_db -U glhst -v owner=glhst -v env=HISTORY -f examples/postgresql/gl_stats.sql
--
-- Output: gl_stats_<env>_YYYYMMDDHH24MI.log in the current directory (and not on
-- the screen). If a history table does not exist yet (e.g. before the first
-- run), its section reports an error and the script continues.
-- =========================================================

\set QUIET on
\pset footer off
\pset null ''

SELECT lower(:'env') || '_' || to_char(localtimestamp, 'YYYYMMDDHH24MI') AS p_run_ts \gset
\o gl_stats_:p_run_ts.log

\echo =========================================================
\echo  ILM statistics
SELECT ' Run at        : ' || to_char(localtimestamp(0), 'YYYY-MM-DD HH24:MI:SS') AS info
UNION ALL
SELECT ' Environment   : ' || :'env'
UNION ALL
SELECT ' Owner         : ' || :'owner'
UNION ALL
SELECT ' Database      : ' || current_database();
\echo =========================================================

-- ---------------------------------------------------------
\echo
\echo === inv_head by inv_sts (N=new, P=paid, C=cancelled, A=active) ===
SELECT :'env' AS env, inv_sts AS status, count(*) AS rows_cnt,
       to_char(min(inv_sts_date), 'YYYY-MM-DD') AS min_date,
       to_char(max(inv_sts_date), 'YYYY-MM-DD') AS max_date
FROM   :"owner".inv_head
GROUP  BY inv_sts
ORDER  BY 2;

\echo
\echo === inv_det by parent inv_head.inv_sts (dates: parent inv_sts_date) ===
SELECT :'env' AS env, h.inv_sts AS status, count(*) AS rows_cnt,
       to_char(min(h.inv_sts_date), 'YYYY-MM-DD') AS min_date,
       to_char(max(h.inv_sts_date), 'YYYY-MM-DD') AS max_date
FROM   :"owner".inv_det d
JOIN   :"owner".inv_head h ON h.inv_id = d.inv_id
GROUP  BY h.inv_sts
ORDER  BY 2;

-- ---------------------------------------------------------
\echo
\echo === dsp_head by dsp_sts (N=new, S=shipped, D=delivered) ===
SELECT :'env' AS env, dsp_sts AS status, count(*) AS rows_cnt,
       to_char(min(dsp_sts_date), 'YYYY-MM-DD') AS min_date,
       to_char(max(dsp_sts_date), 'YYYY-MM-DD') AS max_date
FROM   :"owner".dsp_head
GROUP  BY dsp_sts
ORDER  BY 2;

\echo
\echo === dsp_det by parent dsp_head.dsp_sts (dates: parent dsp_sts_date) ===
SELECT :'env' AS env, h.dsp_sts AS status, count(*) AS rows_cnt,
       to_char(min(h.dsp_sts_date), 'YYYY-MM-DD') AS min_date,
       to_char(max(h.dsp_sts_date), 'YYYY-MM-DD') AS max_date
FROM   :"owner".dsp_det d
JOIN   :"owner".dsp_head h ON h.dsp_id = d.dsp_id
GROUP  BY h.dsp_sts
ORDER  BY 2;

\echo
\echo === dsp_det by related invoice status (dsp_det -> inv_det -> inv_head.inv_sts; dates: inv_sts_date) ===
SELECT :'env' AS env, h.inv_sts AS status, count(*) AS rows_cnt,
       to_char(min(h.inv_sts_date), 'YYYY-MM-DD') AS min_date,
       to_char(max(h.inv_sts_date), 'YYYY-MM-DD') AS max_date
FROM   :"owner".dsp_det d
JOIN   :"owner".inv_det i ON i.ind_id = d.ind_id
JOIN   :"owner".inv_head h ON h.inv_id = i.inv_id
GROUP  BY h.inv_sts
ORDER  BY 2;

-- ---------------------------------------------------------
\echo
\echo === Total rows per table (dates: inv_sts_date / dsp_sts_date; details use the parent's, outer join) ===
SELECT :'env' AS env, 'inv_head' AS table_name, count(*) AS rows_cnt,
       to_char(min(t.inv_sts_date), 'YYYY-MM-DD') AS min_date,
       to_char(max(t.inv_sts_date), 'YYYY-MM-DD') AS max_date
FROM   :"owner".inv_head t
UNION ALL
SELECT :'env', 'inv_det', count(*),
       to_char(min(h.inv_sts_date), 'YYYY-MM-DD'),
       to_char(max(h.inv_sts_date), 'YYYY-MM-DD')
FROM   :"owner".inv_det t
LEFT JOIN :"owner".inv_head h ON h.inv_id = t.inv_id
UNION ALL
SELECT :'env', 'dsp_head', count(*),
       to_char(min(t.dsp_sts_date), 'YYYY-MM-DD'),
       to_char(max(t.dsp_sts_date), 'YYYY-MM-DD')
FROM   :"owner".dsp_head t
UNION ALL
SELECT :'env', 'dsp_det', count(*),
       to_char(min(h.dsp_sts_date), 'YYYY-MM-DD'),
       to_char(max(h.dsp_sts_date), 'YYYY-MM-DD')
FROM   :"owner".dsp_det t
LEFT JOIN :"owner".dsp_head h ON h.dsp_id = t.dsp_id;

\echo
\echo === Done ===
\o
