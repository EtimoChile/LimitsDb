-- =========================================================
-- Statistics for the GL test model
-- Row counts by status, with the min/max status date of each group, for source
-- (GL) and history (GLHST, through a DB link).
-- Run it before and after an ILM execution to compare both snapshots.
--
-- Run it connected to the SOURCE database as a user that can read GL and use
-- the history DB link:
--   sqlplus user/pass@source @examples/oracle/gl_stats.sql
--
-- Output: gl_stats_YYYYMMDDHH24MI.log in the current directory.
-- =========================================================

set define on
set verify off
set feedback off
set heading on
set pagesize 200
set linesize 200
set trimspool on
set colsep ' | '
whenever sqlerror continue

-- ---- Parameters ----
define P_SRC_OWNER    = GL
define P_HIST_OWNER   = GLHST
-- Name of the DB link, created in the source database, that points to history
define P_HIST_DBLINK  = LDB_HIST

-- Log file name with the run timestamp (YYYYMMDDHH24MI)
column RUN_TS new_value P_RUN_TS noprint
select to_char(sysdate, 'YYYYMMDDHH24MI') as RUN_TS from dual;

spool gl_stats_&P_RUN_TS..log

prompt =========================================================
prompt  ILM statistics
select ' Run at        : ' || to_char(sysdate, 'YYYY-MM-DD HH24:MI:SS') as info from dual
union all
select ' Source owner  : &P_SRC_OWNER'  from dual
union all
select ' History owner : &P_HIST_OWNER (dblink &P_HIST_DBLINK)' from dual;
prompt =========================================================

column ENV     format a8
column STATUS  format a8
column ROWS_CNT format 999,999,999,990
column MIN_DATE format a10
column MAX_DATE format a10

-- Note: if a history table does not exist yet (e.g. before the first run), its section
-- reports an ORA- error and the script continues.

-- ---------------------------------------------------------
prompt
prompt === INV_HEAD by INV_STS (N=new, P=paid, C=cancelled, A=active) ===
select 'SOURCE' as ENV, INV_STS as STATUS, count(*) as ROWS_CNT,
       to_char(min(INV_STS_DATE), 'YYYY-MM-DD') as MIN_DATE,
       to_char(max(INV_STS_DATE), 'YYYY-MM-DD') as MAX_DATE
from   &P_SRC_OWNER..INV_HEAD
group  by INV_STS
union all
select 'HISTORY', INV_STS, count(*),
       to_char(min(INV_STS_DATE), 'YYYY-MM-DD'),
       to_char(max(INV_STS_DATE), 'YYYY-MM-DD')
from   &P_HIST_OWNER..INV_HEAD@&P_HIST_DBLINK
group  by INV_STS
order  by 1 desc, 2;

prompt
prompt === INV_DET by parent INV_HEAD.INV_STS (dates: parent INV_STS_DATE) ===
select 'SOURCE' as ENV, h.INV_STS as STATUS, count(*) as ROWS_CNT,
       to_char(min(h.INV_STS_DATE), 'YYYY-MM-DD') as MIN_DATE,
       to_char(max(h.INV_STS_DATE), 'YYYY-MM-DD') as MAX_DATE
from   &P_SRC_OWNER..INV_DET d
join   &P_SRC_OWNER..INV_HEAD h on h.INV_ID = d.INV_ID
group  by h.INV_STS
union all
select 'HISTORY', h.INV_STS, count(*),
       to_char(min(h.INV_STS_DATE), 'YYYY-MM-DD'),
       to_char(max(h.INV_STS_DATE), 'YYYY-MM-DD')
from   &P_HIST_OWNER..INV_DET@&P_HIST_DBLINK d
join   &P_HIST_OWNER..INV_HEAD@&P_HIST_DBLINK h on h.INV_ID = d.INV_ID
group  by h.INV_STS
order  by 1 desc, 2;

-- ---------------------------------------------------------
prompt
prompt === DSP_HEAD by DSP_STS (N=new, S=shipped, D=delivered) ===
select 'SOURCE' as ENV, DSP_STS as STATUS, count(*) as ROWS_CNT,
       to_char(min(DSP_STS_DATE), 'YYYY-MM-DD') as MIN_DATE,
       to_char(max(DSP_STS_DATE), 'YYYY-MM-DD') as MAX_DATE
from   &P_SRC_OWNER..DSP_HEAD
group  by DSP_STS
union all
select 'HISTORY', DSP_STS, count(*),
       to_char(min(DSP_STS_DATE), 'YYYY-MM-DD'),
       to_char(max(DSP_STS_DATE), 'YYYY-MM-DD')
from   &P_HIST_OWNER..DSP_HEAD@&P_HIST_DBLINK
group  by DSP_STS
order  by 1 desc, 2;

prompt
prompt === DSP_DET by parent DSP_HEAD.DSP_STS (dates: parent DSP_STS_DATE) ===
select 'SOURCE' as ENV, h.DSP_STS as STATUS, count(*) as ROWS_CNT,
       to_char(min(h.DSP_STS_DATE), 'YYYY-MM-DD') as MIN_DATE,
       to_char(max(h.DSP_STS_DATE), 'YYYY-MM-DD') as MAX_DATE
from   &P_SRC_OWNER..DSP_DET d
join   &P_SRC_OWNER..DSP_HEAD h on h.DSP_ID = d.DSP_ID
group  by h.DSP_STS
union all
select 'HISTORY', h.DSP_STS, count(*),
       to_char(min(h.DSP_STS_DATE), 'YYYY-MM-DD'),
       to_char(max(h.DSP_STS_DATE), 'YYYY-MM-DD')
from   &P_HIST_OWNER..DSP_DET@&P_HIST_DBLINK d
join   &P_HIST_OWNER..DSP_HEAD@&P_HIST_DBLINK h on h.DSP_ID = d.DSP_ID
group  by h.DSP_STS
order  by 1 desc, 2;

prompt
prompt === DSP_DET by related invoice status (DSP_DET -> INV_DET -> INV_HEAD.INV_STS; dates: INV_STS_DATE) ===
select 'SOURCE' as ENV, h.INV_STS as STATUS, count(*) as ROWS_CNT,
       to_char(min(h.INV_STS_DATE), 'YYYY-MM-DD') as MIN_DATE,
       to_char(max(h.INV_STS_DATE), 'YYYY-MM-DD') as MAX_DATE
from   &P_SRC_OWNER..DSP_DET d
join   &P_SRC_OWNER..INV_DET i on i.IND_ID = d.IND_ID
join   &P_SRC_OWNER..INV_HEAD h on h.INV_ID = i.INV_ID
group  by h.INV_STS
union all
select 'HISTORY', h.INV_STS, count(*),
       to_char(min(h.INV_STS_DATE), 'YYYY-MM-DD'),
       to_char(max(h.INV_STS_DATE), 'YYYY-MM-DD')
from   &P_HIST_OWNER..DSP_DET@&P_HIST_DBLINK d
join   &P_HIST_OWNER..INV_DET@&P_HIST_DBLINK i on i.IND_ID = d.IND_ID
join   &P_HIST_OWNER..INV_HEAD@&P_HIST_DBLINK h on h.INV_ID = i.INV_ID
group  by h.INV_STS
order  by 1 desc, 2;

-- ---------------------------------------------------------
prompt
prompt === Total rows per table (dates: INV_STS_DATE / DSP_STS_DATE; details use the parent's, outer join) ===
column TABLE_NAME format a12
select 'SOURCE' as ENV, 'INV_HEAD' as TABLE_NAME, count(*) as ROWS_CNT,
       to_char(min(t.INV_STS_DATE), 'YYYY-MM-DD') as MIN_DATE,
       to_char(max(t.INV_STS_DATE), 'YYYY-MM-DD') as MAX_DATE
from   &P_SRC_OWNER..INV_HEAD t
union all
select 'SOURCE', 'INV_DET', count(*),
       to_char(min(h.INV_STS_DATE), 'YYYY-MM-DD'),
       to_char(max(h.INV_STS_DATE), 'YYYY-MM-DD')
from   &P_SRC_OWNER..INV_DET t
left join &P_SRC_OWNER..INV_HEAD h on h.INV_ID = t.INV_ID
union all
select 'SOURCE', 'DSP_HEAD', count(*),
       to_char(min(t.DSP_STS_DATE), 'YYYY-MM-DD'),
       to_char(max(t.DSP_STS_DATE), 'YYYY-MM-DD')
from   &P_SRC_OWNER..DSP_HEAD t
union all
select 'SOURCE', 'DSP_DET', count(*),
       to_char(min(h.DSP_STS_DATE), 'YYYY-MM-DD'),
       to_char(max(h.DSP_STS_DATE), 'YYYY-MM-DD')
from   &P_SRC_OWNER..DSP_DET t
left join &P_SRC_OWNER..DSP_HEAD h on h.DSP_ID = t.DSP_ID
union all
select 'HISTORY', 'INV_HEAD', count(*),
       to_char(min(t.INV_STS_DATE), 'YYYY-MM-DD'),
       to_char(max(t.INV_STS_DATE), 'YYYY-MM-DD')
from   &P_HIST_OWNER..INV_HEAD@&P_HIST_DBLINK t
union all
select 'HISTORY', 'INV_DET', count(*),
       to_char(min(h.INV_STS_DATE), 'YYYY-MM-DD'),
       to_char(max(h.INV_STS_DATE), 'YYYY-MM-DD')
from   &P_HIST_OWNER..INV_DET@&P_HIST_DBLINK t
left join &P_HIST_OWNER..INV_HEAD@&P_HIST_DBLINK h on h.INV_ID = t.INV_ID
union all
select 'HISTORY', 'DSP_HEAD', count(*),
       to_char(min(t.DSP_STS_DATE), 'YYYY-MM-DD'),
       to_char(max(t.DSP_STS_DATE), 'YYYY-MM-DD')
from   &P_HIST_OWNER..DSP_HEAD@&P_HIST_DBLINK t
union all
select 'HISTORY', 'DSP_DET', count(*),
       to_char(min(h.DSP_STS_DATE), 'YYYY-MM-DD'),
       to_char(max(h.DSP_STS_DATE), 'YYYY-MM-DD')
from   &P_HIST_OWNER..DSP_DET@&P_HIST_DBLINK t
left join &P_HIST_OWNER..DSP_HEAD@&P_HIST_DBLINK h on h.DSP_ID = t.DSP_ID;

prompt
prompt === Done ===
spool off
