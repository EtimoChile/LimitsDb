-- =========================================================
-- GL test model on PostgreSQL (9.5 or later): DDL and synthetic data loader.
-- PostgreSQL counterpart of examples/oracle/gl.sql. Names are lower case, the
-- default case of PostgreSQL. Run it with psql:
--
--   psql "postgresql://admin:pass@host/source_db" -v gl_password=SECRET -f examples/postgresql/gl.sql
--
-- Use a direct connection (on Neon, the host without -pooler): the script relies on session state (SET ROLE).
-- Connect as an administrator (e.g. neondb_owner on Neon: it needs CREATEROLE).
-- The script creates the login role gl (or resets its password to gl_password,
-- the one to put in secrets.json as source_password), makes it the owner of the
-- schema gl and creates and loads everything as gl, so the tables belong to it.
-- The history owner (glhst) is created by examples/postgresql/glhst.sql, in the history database.
-- Tables: cst_master, pro_master, inv_head, inv_det, dsp_head, dsp_det
--
-- Business rules enforced by the generated data
--   Statuses
--     inv_sts: N = new (draft), A = active (confirmed, awaiting delivery),
--              P = paid, C = cancelled
--     dsp_sts: N = new (created when the invoice is confirmed),
--              S = shipped, D = delivered
--   Invoice <-> dispatch
--     R1. A dispatch ships lines of one single invoice (all its dsp_det lines
--         point to inv_det lines of that invoice) and takes its customer.
--     R2. Only confirmed invoices are dispatched: invoices N and C have no
--         dispatches.
--     R3. Delivery is made against payment: a dispatch is D if and only if its
--         invoice is P (every dispatch of a paid invoice is D), and both
--         close on the same day (dsp_sts_date = inv_sts_date). While the
--         invoice is A its dispatches are N or S.
--   Dates (document dates do not take part in ILM; status dates do)
--     R4. inv_date <= inv_sts_date (N: same day; A: up to 7 days later;
--         C: up to 30 days later; P: 5 to 45 days later).
--     R5. A dispatch is created on or after inv_date; for an A invoice, on or
--         after inv_sts_date (up to 7 days later); for a P invoice, on or
--         before its closing day.
--     R6. dsp_date <= dsp_sts_date (N: same day; S: 1 to 10 days later).
--     R7. No date is later than today.
-- Requires p_inv_lines >= p_invoices and an even number of invoices.
-- =========================================================

\set ON_ERROR_STOP on
\set QUIET on

-- ---- Parameters (adjust volumes) ----
\set p_customers   10000
\set p_products    2000
\set p_invoices    200000
\set p_inv_lines   800000
\set p_dispatches  150000
\set p_dsp_lines   300000
-- Date range for inv/dsp: from p_months_back months ago up to today
\set p_months_back 24

\echo === Creating user and schema ===
-- The password travels as a session setting, never inside a statement text
SET ldb.gl_password = :'gl_password';

DO $$
BEGIN
  BEGIN
    EXECUTE format('CREATE ROLE gl LOGIN PASSWORD %L', current_setting('ldb.gl_password'));
  EXCEPTION WHEN duplicate_object THEN
    EXECUTE format('ALTER ROLE gl LOGIN PASSWORD %L', current_setting('ldb.gl_password'));
  END;
END
$$;
RESET ldb.gl_password;

-- Lets the administrator act as gl (needed when it is not a superuser)
GRANT gl TO CURRENT_USER;
CREATE SCHEMA IF NOT EXISTS gl AUTHORIZATION gl;
ALTER SCHEMA gl OWNER TO gl;

SET ROLE gl;

\echo === Creating tables ===

DROP TABLE IF EXISTS gl.dsp_det CASCADE;
DROP TABLE IF EXISTS gl.dsp_head CASCADE;
DROP TABLE IF EXISTS gl.inv_det CASCADE;
DROP TABLE IF EXISTS gl.inv_head CASCADE;
DROP TABLE IF EXISTS gl.pro_master CASCADE;
DROP TABLE IF EXISTS gl.cst_master CASCADE;

CREATE TABLE gl.cst_master (
  cst_id          numeric(10)   NOT NULL,
  cst_type        varchar(1)    NOT NULL,
  cst_id_country  varchar(2)    NOT NULL,
  cst_id_type     varchar(1)    NOT NULL,
  cst_id_cod      varchar(40)   NOT NULL,
  cst_first_name  varchar(50)   NOT NULL,
  cst_second_name varchar(50)   NOT NULL,
  cst_pat_surname varchar(50)   NOT NULL,
  cst_mat_surname varchar(50)   NOT NULL,
  cst_name        varchar(200)  NOT NULL,
  CONSTRAINT cst_master_pk PRIMARY KEY (cst_id)
);

CREATE TABLE gl.pro_master (
  pro_id         numeric(10)    NOT NULL,
  pro_name       varchar(50)    NOT NULL,
  pro_desc       varchar(1000)  NOT NULL,
  pro_unit       varchar(8)     NOT NULL,
  pro_price_curr varchar(3)     NOT NULL,
  pro_price      numeric        NOT NULL,
  CONSTRAINT pro_master_pk PRIMARY KEY (pro_id)
);

CREATE TABLE gl.inv_head (
  inv_id       numeric(10)  NOT NULL,
  cst_id       numeric(10)  NOT NULL,
  inv_date     timestamp(0) NOT NULL,
  inv_sts      varchar(1)   NOT NULL,
  inv_sts_date timestamp(0) NOT NULL,
  CONSTRAINT inv_head_pk PRIMARY KEY (inv_id),
  CONSTRAINT inv_head_fk1 FOREIGN KEY (cst_id) REFERENCES gl.cst_master (cst_id)
);
CREATE INDEX inv_head_i1 ON gl.inv_head (cst_id);

CREATE TABLE gl.inv_det (
  ind_id       numeric(10) NOT NULL,
  inv_id       numeric(10),
  pro_id       numeric,
  ind_amount   numeric,
  ind_subtotal numeric,
  CONSTRAINT inv_det_pk PRIMARY KEY (ind_id),
  CONSTRAINT inv_det_fk1 FOREIGN KEY (pro_id) REFERENCES gl.pro_master (pro_id),
  CONSTRAINT inv_det_fk2 FOREIGN KEY (inv_id) REFERENCES gl.inv_head (inv_id)
);
CREATE INDEX inv_det_i1 ON gl.inv_det (inv_id);

CREATE TABLE gl.dsp_head (
  dsp_id       numeric(10) NOT NULL,
  cst_id       numeric(10),
  dsp_date     timestamp(0),
  dsp_sts      varchar(1),
  dsp_sts_date timestamp(0),
  CONSTRAINT dsp_head_pk PRIMARY KEY (dsp_id),
  CONSTRAINT dsp_head_fk1 FOREIGN KEY (cst_id) REFERENCES gl.cst_master (cst_id)
);

CREATE TABLE gl.dsp_det (
  dsd_id     numeric(10) NOT NULL,
  dsp_id     numeric     NOT NULL,
  ind_id     numeric(10) NOT NULL,
  dsd_amount numeric     NOT NULL,
  CONSTRAINT dsp_det_pk PRIMARY KEY (dsd_id),
  CONSTRAINT dsp_det_fk1 FOREIGN KEY (dsp_id) REFERENCES gl.dsp_head (dsp_id),
  CONSTRAINT dsp_det_fk2 FOREIGN KEY (ind_id) REFERENCES gl.inv_det (ind_id)
);
CREATE INDEX dsp_det_i1 ON gl.dsp_det (dsp_id);
CREATE INDEX dsp_det_i2 ON gl.dsp_det (ind_id);

\echo === Seeding RNG for repeatability ===
SELECT setseed(0.123456);

-- Date range as literals (today at midnight and p_months_back months before)
SELECT to_char(date_trunc('day', localtimestamp), 'YYYY-MM-DD') AS p_date_to,
       to_char(date_trunc('day', localtimestamp) - (:p_months_back * interval '1 month'), 'YYYY-MM-DD') AS p_date_from
\gset

\echo === Loading pro_master ===
INSERT INTO gl.pro_master (pro_id, pro_name, pro_desc, pro_unit, pro_price_curr, pro_price)
SELECT
  n,
  'PROD-' || n,
  rpad('DESC ' || n || ' ', 50, 'X'),
  CASE n % 4 WHEN 0 THEN 'UN' WHEN 1 THEN 'KG' WHEN 2 THEN 'LT' ELSE 'PACK' END,
  CASE n % 3 WHEN 0 THEN 'CLP' WHEN 1 THEN 'USD' ELSE 'EUR' END,
  round((100 + random() * (500000 - 100))::numeric, 2)
FROM generate_series(1, :p_products) AS n;

\echo === Loading cst_master ===
INSERT INTO gl.cst_master
  (cst_id, cst_type, cst_id_country, cst_id_type, cst_id_cod, cst_first_name, cst_second_name,
   cst_pat_surname, cst_mat_surname, cst_name)
SELECT
  n,
  CASE n % 2 WHEN 0 THEN 'P' ELSE 'C' END,
  CASE n % 5 WHEN 0 THEN 'CL' WHEN 1 THEN 'AR' WHEN 2 THEN 'PE' WHEN 3 THEN 'BR' ELSE 'UY' END,
  CASE n % 3 WHEN 0 THEN 'R' WHEN 1 THEN 'P' ELSE 'I' END,
  substr('ID-' || n || '-' || floor(100000 + random() * 900000)::int, 1, 40),
  substr('NAME' || n, 1, 50),
  substr('MID' || n, 1, 50),
  substr('SURP' || n, 1, 50),
  substr('SURM' || n, 1, 50),
  substr('NAME' || n || ' MID' || n || ' SURP' || n || ' SURM' || n, 1, 200)
FROM generate_series(1, :p_customers) AS n;

\echo === Loading inv_head ===
-- R4/R7: status date derived from the invoice date and the status.
-- Status by inv_id % 4: 0 = N, 1 = P, 2 = C, 3 = A.
INSERT INTO gl.inv_head (inv_id, cst_id, inv_date, inv_sts, inv_sts_date)
SELECT
  s.inv_id,
  s.cst_id,
  s.inv_date,
  s.inv_sts,
  LEAST(
    s.inv_date + (CASE s.inv_sts
                    WHEN 'N' THEN 0
                    WHEN 'A' THEN s.rnd % 8
                    WHEN 'C' THEN s.rnd % 31
                    ELSE 5 + s.rnd % 41
                  END) * interval '1 day',
    :'p_date_to'::timestamp)
FROM (
  SELECT
    n AS inv_id,
    1 + floor(random() * :p_customers)::int AS cst_id,
    :'p_date_from'::timestamp
      + floor(random() * (:'p_date_to'::date - :'p_date_from'::date + 1))::int * interval '1 day' AS inv_date,
    CASE n % 4 WHEN 0 THEN 'N' WHEN 1 THEN 'P' WHEN 2 THEN 'C' ELSE 'A' END AS inv_sts,
    floor(random() * 1000000)::int AS rnd
  FROM generate_series(1, :p_invoices) AS n
  OFFSET 0
) s;

\echo === Loading inv_det ===
-- Lines are spread evenly: line ind_id belongs to invoice (ind_id - 1) % p_invoices + 1.
INSERT INTO gl.inv_det (ind_id, inv_id, pro_id, ind_amount, ind_subtotal)
SELECT
  n,
  (n - 1) % :p_invoices + 1,
  1 + floor(random() * :p_products)::int,
  floor(1 + random() * 19),
  round((100 + random() * (500000 - 100))::numeric, 2)
FROM generate_series(1, :p_inv_lines) AS n;

\echo === Loading dsp_head ===
-- R1/R2/R3: dispatch k belongs to the odd invoice 2 * ((k - 1) % n) + 1 (n = number of
-- odd invoices); odd invoices are P (inv_id % 4 = 1) or A (% 4 = 3).
-- R3/R5/R6: dates and status derived from the invoice.
INSERT INTO gl.dsp_head (dsp_id, cst_id, dsp_date, dsp_sts, dsp_sts_date)
SELECT
  v.dsp_id,
  v.cst_id,
  v.dsp_date,
  v.dsp_sts,
  CASE v.dsp_sts
    WHEN 'D' THEN v.inv_sts_date
    WHEN 'N' THEN v.dsp_date
    ELSE LEAST(v.dsp_date + (1 + (v.rnd / 16) % 10) * interval '1 day', :'p_date_to'::timestamp)
  END
FROM (
  SELECT
    d.dsp_id,
    i.cst_id,
    i.inv_sts_date,
    CASE i.inv_sts
      WHEN 'P' THEN 'D'
      ELSE CASE d.rnd % 2 WHEN 0 THEN 'N' ELSE 'S' END
    END AS dsp_sts,
    CASE i.inv_sts
      WHEN 'P' THEN i.inv_date + (d.rnd % ((i.inv_sts_date::date - i.inv_date::date) + 1)) * interval '1 day'
      ELSE LEAST(i.inv_sts_date + ((d.rnd / 2) % 8) * interval '1 day', :'p_date_to'::timestamp)
    END AS dsp_date,
    d.rnd
  FROM (
    SELECT
      n AS dsp_id,
      2 * ((n - 1) % ((:p_invoices + 1) / 2)) + 1 AS inv_id,
      floor(random() * 1000000)::int AS rnd
    FROM generate_series(1, :p_dispatches) AS n
    OFFSET 0
  ) d
  JOIN gl.inv_head i ON i.inv_id = d.inv_id
) v;

\echo === Loading dsp_det ===
-- R1: every line of dispatch dsp_id points to a line of that dispatch's invoice
-- (inv_det lines of invoice v are v, v + p_invoices, v + 2 * p_invoices, ...).
INSERT INTO gl.dsp_det (dsd_id, dsp_id, ind_id, dsd_amount)
SELECT
  l.dsd_id,
  l.dsp_id,
  l.inv_id + :p_invoices * (l.seq % ((:p_inv_lines - l.inv_id) / :p_invoices + 1)),
  round((10 + random() * (500000 - 10))::numeric, 2)
FROM (
  SELECT
    n AS dsd_id,
    (n - 1) % :p_dispatches + 1 AS dsp_id,
    (n - 1) / :p_dispatches AS seq,
    2 * (((n - 1) % :p_dispatches) % ((:p_invoices + 1) / 2)) + 1 AS inv_id
  FROM generate_series(1, :p_dsp_lines) AS n
) l;

\echo === Gathering stats ===
ANALYZE gl.pro_master;
ANALYZE gl.cst_master;
ANALYZE gl.inv_head;
ANALYZE gl.inv_det;
ANALYZE gl.dsp_head;
ANALYZE gl.dsp_det;

\echo === Row counts ===
SELECT 'cst_master' AS tbl, count(*) AS cnt FROM gl.cst_master UNION ALL
SELECT 'pro_master', count(*) FROM gl.pro_master UNION ALL
SELECT 'inv_head',   count(*) FROM gl.inv_head UNION ALL
SELECT 'inv_det',    count(*) FROM gl.inv_det UNION ALL
SELECT 'dsp_head',   count(*) FROM gl.dsp_head UNION ALL
SELECT 'dsp_det',    count(*) FROM gl.dsp_det;

RESET ROLE;
\echo === Done ===
