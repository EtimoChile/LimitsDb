set define on
set serveroutput on
whenever sqlerror continue

DROP TABLE "GL"."CST_MASTER" cascade constraints;
DROP TABLE "GL"."DSP_DET" cascade constraints;
DROP TABLE "GL"."DSP_HEAD" cascade constraints;
DROP TABLE "GL"."INV_DET" cascade constraints;
DROP TABLE "GL"."INV_HEAD" cascade constraints;
DROP TABLE "GL"."PRO_MASTER" cascade constraints;
--------------------------------------------------------
--  DDL for Table CST_MASTER
--------------------------------------------------------
whenever sqlerror exit failure rollback

  CREATE TABLE "GL"."CST_MASTER" 
   (	"CST_ID" NUMBER(*,0), 
	"CST_TYPE" VARCHAR2(1 CHAR), 
	"CST_ID_COUNTRY" VARCHAR2(2 CHAR), 
	"CST_ID_TYPE" VARCHAR2(1 CHAR), 
	"CST_ID_COD" VARCHAR2(40 CHAR), 
	"CST_FIRST_NAME" VARCHAR2(50 CHAR), 
	"CST_SECOND_NAME" VARCHAR2(50 CHAR), 
	"CST_PAT_SURNAME" VARCHAR2(50 CHAR), 
	"CST_MAT_SURNAME" VARCHAR2(50 CHAR), 
	"CST_NAME" VARCHAR2(200 CHAR)
   ) ;
--------------------------------------------------------
--  DDL for Table DSP_DET
--------------------------------------------------------

  CREATE TABLE "GL"."DSP_DET" 
   (	"DSD_ID" NUMBER(*,0), 
	"DSP_ID" NUMBER, 
	"IND_ID" NUMBER(*,0), 
	"DSD_AMOUNT" NUMBER
   ) ;
--------------------------------------------------------
--  DDL for Table DSP_HEAD
--------------------------------------------------------

  CREATE TABLE "GL"."DSP_HEAD" 
   (	"DSP_ID" NUMBER(*,0), 
	"CST_ID" NUMBER(*,0), 
	"DSP_DATE" DATE, 
	"DSP_STS" VARCHAR2(1), 
	"DSP_STS_DATE" DATE
   ) ;
--------------------------------------------------------
--  DDL for Table INV_DET
--------------------------------------------------------

  CREATE TABLE "GL"."INV_DET" 
   (	"IND_ID" NUMBER(*,0), 
	"INV_ID" NUMBER(*,0), 
	"PRO_ID" NUMBER, 
	"IND_AMOUNT" NUMBER, 
	"IND_SUBTOTAL" NUMBER
   ) ;
--------------------------------------------------------
--  DDL for Table INV_HEAD
--------------------------------------------------------

  CREATE TABLE "GL"."INV_HEAD" 
   (	"INV_ID" NUMBER(*,0), 
	"CST_ID" NUMBER(*,0), 
	"INV_DATE" DATE, 
	"INV_STS" VARCHAR2(1 CHAR), 
	"INV_STS_DATE" DATE
   ) ;
--------------------------------------------------------
--  DDL for Table PRO_MASTER
--------------------------------------------------------

  CREATE TABLE "GL"."PRO_MASTER" 
   (	"PRO_ID" NUMBER(*,0), 
	"PRO_NAME" VARCHAR2(50 CHAR), 
	"PRO_DESC" VARCHAR2(1000 CHAR), 
	"PRO_UNIT" VARCHAR2(8 CHAR), 
	"PRO_PRICE_CURR" VARCHAR2(3 CHAR), 
	"PRO_PRICE" NUMBER
   ) ;
--------------------------------------------------------
--  DDL for Index INV_DET_PK
--------------------------------------------------------

  CREATE UNIQUE INDEX "GL"."INV_DET_PK" ON "GL"."INV_DET" ("IND_ID") 
  ;
--------------------------------------------------------
--  DDL for Index INV_HEAD_PK
--------------------------------------------------------

  CREATE UNIQUE INDEX "GL"."INV_HEAD_PK" ON "GL"."INV_HEAD" ("INV_ID") 
  ;
--------------------------------------------------------
--  DDL for Index INV_HEAD_I1
--------------------------------------------------------

  CREATE INDEX "GL"."INV_HEAD_I1" ON "GL"."INV_HEAD" ("CST_ID") 
  ;
--------------------------------------------------------
--  DDL for Index DSP_HEAD_PK
--------------------------------------------------------

  CREATE UNIQUE INDEX "GL"."DSP_HEAD_PK" ON "GL"."DSP_HEAD" ("DSP_ID") 
  ;
--------------------------------------------------------
--  DDL for Index DSP_DET_PK
--------------------------------------------------------

  CREATE UNIQUE INDEX "GL"."DSP_DET_PK" ON "GL"."DSP_DET" ("DSD_ID") 
  ;
--------------------------------------------------------
--  DDL for Index CST_MASTER_PK
--------------------------------------------------------

  CREATE UNIQUE INDEX "GL"."CST_MASTER_PK" ON "GL"."CST_MASTER" ("CST_ID") 
  ;
--------------------------------------------------------
--  DDL for Index PRO_MASTER_PK
--------------------------------------------------------

  CREATE UNIQUE INDEX "GL"."PRO_MASTER_PK" ON "GL"."PRO_MASTER" ("PRO_ID") 
  ;
--------------------------------------------------------
--  Constraints for Table PRO_MASTER
--------------------------------------------------------

  ALTER TABLE "GL"."PRO_MASTER" ADD CONSTRAINT "PRO_MASTER_PK" PRIMARY KEY ("PRO_ID")
  USING INDEX  ENABLE;
  ALTER TABLE "GL"."PRO_MASTER" MODIFY ("PRO_PRICE" NOT NULL ENABLE);
  ALTER TABLE "GL"."PRO_MASTER" MODIFY ("PRO_PRICE_CURR" NOT NULL ENABLE);
  ALTER TABLE "GL"."PRO_MASTER" MODIFY ("PRO_UNIT" NOT NULL ENABLE);
  ALTER TABLE "GL"."PRO_MASTER" MODIFY ("PRO_DESC" NOT NULL ENABLE);
  ALTER TABLE "GL"."PRO_MASTER" MODIFY ("PRO_NAME" NOT NULL ENABLE);
  ALTER TABLE "GL"."PRO_MASTER" MODIFY ("PRO_ID" NOT NULL ENABLE);
--------------------------------------------------------
--  Constraints for Table CST_MASTER
--------------------------------------------------------

  ALTER TABLE "GL"."CST_MASTER" MODIFY ("CST_MAT_SURNAME" NOT NULL ENABLE);
  ALTER TABLE "GL"."CST_MASTER" MODIFY ("CST_PAT_SURNAME" NOT NULL ENABLE);
  ALTER TABLE "GL"."CST_MASTER" MODIFY ("CST_NAME" NOT NULL ENABLE);
  ALTER TABLE "GL"."CST_MASTER" ADD CONSTRAINT "CST_MASTER_PK" PRIMARY KEY ("CST_ID")
  USING INDEX  ENABLE;
  ALTER TABLE "GL"."CST_MASTER" MODIFY ("CST_SECOND_NAME" NOT NULL ENABLE);
  ALTER TABLE "GL"."CST_MASTER" MODIFY ("CST_FIRST_NAME" NOT NULL ENABLE);
  ALTER TABLE "GL"."CST_MASTER" MODIFY ("CST_ID_COD" NOT NULL ENABLE);
  ALTER TABLE "GL"."CST_MASTER" MODIFY ("CST_ID_TYPE" NOT NULL ENABLE);
  ALTER TABLE "GL"."CST_MASTER" MODIFY ("CST_ID_COUNTRY" NOT NULL ENABLE);
  ALTER TABLE "GL"."CST_MASTER" MODIFY ("CST_TYPE" NOT NULL ENABLE);
  ALTER TABLE "GL"."CST_MASTER" MODIFY ("CST_ID" NOT NULL ENABLE);
--------------------------------------------------------
--  Constraints for Table DSP_DET
--------------------------------------------------------

  ALTER TABLE "GL"."DSP_DET" ADD CONSTRAINT "DSP_DET_PK" PRIMARY KEY ("DSD_ID")
  USING INDEX  ENABLE;
  ALTER TABLE "GL"."DSP_DET" MODIFY ("DSD_AMOUNT" NOT NULL ENABLE);
  ALTER TABLE "GL"."DSP_DET" MODIFY ("IND_ID" NOT NULL ENABLE);
  ALTER TABLE "GL"."DSP_DET" MODIFY ("DSP_ID" NOT NULL ENABLE);
  ALTER TABLE "GL"."DSP_DET" MODIFY ("DSD_ID" NOT NULL ENABLE);
--------------------------------------------------------
--  Constraints for Table DSP_HEAD
--------------------------------------------------------

  ALTER TABLE "GL"."DSP_HEAD" ADD CONSTRAINT "DSP_HEAD_PK" PRIMARY KEY ("DSP_ID")
  USING INDEX  ENABLE;
  ALTER TABLE "GL"."DSP_HEAD" MODIFY ("DSP_ID" NOT NULL ENABLE);
--------------------------------------------------------
--  Constraints for Table INV_HEAD
--------------------------------------------------------

  ALTER TABLE "GL"."INV_HEAD" MODIFY ("INV_STS_DATE" NOT NULL ENABLE);
  ALTER TABLE "GL"."INV_HEAD" ADD CONSTRAINT "INV_HEAD_PK" PRIMARY KEY ("INV_ID")
  USING INDEX  ENABLE;
  ALTER TABLE "GL"."INV_HEAD" MODIFY ("INV_ID" NOT NULL ENABLE);
  ALTER TABLE "GL"."INV_HEAD" MODIFY ("INV_STS" NOT NULL ENABLE);
  ALTER TABLE "GL"."INV_HEAD" MODIFY ("INV_DATE" NOT NULL ENABLE);
  ALTER TABLE "GL"."INV_HEAD" MODIFY ("CST_ID" NOT NULL ENABLE);
--------------------------------------------------------
--  Constraints for Table INV_DET
--------------------------------------------------------

  ALTER TABLE "GL"."INV_DET" ADD CONSTRAINT "INV_DET_PK" PRIMARY KEY ("IND_ID")
  USING INDEX  ENABLE;
  ALTER TABLE "GL"."INV_DET" MODIFY ("IND_ID" NOT NULL ENABLE);
--------------------------------------------------------
--  Ref Constraints for Table DSP_DET
--------------------------------------------------------

  ALTER TABLE "GL"."DSP_DET" ADD CONSTRAINT "DSP_DET_FK1" FOREIGN KEY ("DSP_ID")
	  REFERENCES "GL"."DSP_HEAD" ("DSP_ID") ENABLE;
  ALTER TABLE "GL"."DSP_DET" ADD CONSTRAINT "DSP_DET_FK2" FOREIGN KEY ("IND_ID")
	  REFERENCES "GL"."INV_DET" ("IND_ID") ENABLE;
--------------------------------------------------------
--  Ref Constraints for Table DSP_HEAD
--------------------------------------------------------

  ALTER TABLE "GL"."DSP_HEAD" ADD CONSTRAINT "DSP_HEAD_FK1" FOREIGN KEY ("CST_ID")
	  REFERENCES "GL"."CST_MASTER" ("CST_ID") ENABLE;
--------------------------------------------------------
--  Ref Constraints for Table INV_DET
--------------------------------------------------------

  ALTER TABLE "GL"."INV_DET" ADD CONSTRAINT "INV_DET_FK1" FOREIGN KEY ("PRO_ID")
	  REFERENCES "GL"."PRO_MASTER" ("PRO_ID") ENABLE;
--------------------------------------------------------
--  Ref Constraints for Table INV_HEAD
--------------------------------------------------------

  ALTER TABLE "GL"."INV_HEAD" ADD CONSTRAINT "INV_HEAD_FK1" FOREIGN KEY ("CST_ID")
	  REFERENCES "GL"."CST_MASTER" ("CST_ID") ENABLE;

-- =========================================================
-- Synthetic data loader for schema GL (Oracle 12.1.0.2)
-- Tables: CST_MASTER, PRO_MASTER, INV_HEAD, INV_DET, DSP_HEAD, DSP_DET
-- =========================================================

-- ---- Parameters (adjust volumes) ----
define P_CUSTOMERS    = 10000
define P_PRODUCTS     = 2000
define P_INVOICES     = 200000
define P_INV_LINES    = 800000
define P_DISPATCHES   = 150000
define P_DSP_LINES    = 300000

-- Date range for INV/DSP
define P_DATE_FROM = "date '2024-01-01'"
define P_DATE_TO   = "date '2026-02-01'"

prompt === Disable FK constraints (GL) ===
begin
  for r in (
    select table_name, constraint_name
    from   all_constraints
    where  owner = 'GL'
    and    constraint_type = 'R'
    and    status = 'ENABLED'
  ) loop
    execute immediate 'alter table GL.'||r.table_name||' disable constraint '||r.constraint_name;
  end loop;
end;
/

prompt === Truncating tables ===

begin
  execute immediate 'truncate table GL.DSP_DET';
  execute immediate 'truncate table GL.DSP_HEAD';
  execute immediate 'truncate table GL.INV_DET';
  execute immediate 'truncate table GL.INV_HEAD';
  execute immediate 'truncate table GL.PRO_MASTER';
  execute immediate 'truncate table GL.CST_MASTER';
exception
  when others then
    dbms_output.put_line('Truncate error: '||sqlerrm);
    raise;
end;
/

prompt === Enable FK constraints (GL) ===
begin
  for r in (
    select table_name, constraint_name
    from   all_constraints
    where  owner = 'GL'
    and    constraint_type = 'R'
  ) loop
    execute immediate 'alter table GL.'||r.table_name||' enable constraint '||r.constraint_name;
  end loop;
end;
/

prompt === Seeding RNG for repeatability ===
begin
  dbms_random.seed(123456);
end;
/

prompt === Loading PRO_MASTER ===
insert /*+ append */ into GL.PRO_MASTER (PRO_ID, PRO_NAME, PRO_DESC, PRO_UNIT, PRO_PRICE_CURR, PRO_PRICE)
select
  level as PRO_ID,
  'PROD-' || to_char(level) as PRO_NAME,
  rpad('DESC ' || to_char(level) || ' ', 50, 'X') as PRO_DESC,
  case mod(level,4)
    when 0 then 'UN'
    when 1 then 'KG'
    when 2 then 'LT'
    else 'PACK'
  end as PRO_UNIT,
  case mod(level,3)
    when 0 then 'CLP'
    when 1 then 'USD'
    else 'EUR'
  end as PRO_PRICE_CURR,
  round(dbms_random.value(100, 500000), 2) as PRO_PRICE
from dual
connect by level <= &P_PRODUCTS;

commit;

prompt === Loading CST_MASTER ===
insert /*+ append */ into GL.CST_MASTER
  (CST_ID, CST_TYPE, CST_ID_COUNTRY, CST_ID_TYPE, CST_ID_COD, CST_FIRST_NAME, CST_SECOND_NAME, CST_PAT_SURNAME, CST_MAT_SURNAME, CST_NAME)
select
  level as CST_ID,
  case mod(level,2) when 0 then 'P' else 'C' end as CST_TYPE,
  case mod(level,5)
    when 0 then 'CL'
    when 1 then 'AR'
    when 2 then 'PE'
    when 3 then 'BR'
    else 'UY'
  end as CST_ID_COUNTRY,
  case mod(level,3) when 0 then 'R' when 1 then 'P' else 'I' end as CST_ID_TYPE,
  -- 40 chars max
  substr('ID-'||to_char(level)||'-'||to_char(trunc(dbms_random.value(100000,999999))),1,40) as CST_ID_COD,
  substr('NAME'||to_char(level),1,50) as CST_FIRST_NAME,
  substr('MID'||to_char(level),1,50)  as CST_SECOND_NAME,
  substr('SURP'||to_char(level),1,50) as CST_PAT_SURNAME,
  substr('SURM'||to_char(level),1,50) as CST_MAT_SURNAME,
  substr('NAME'||level||' MID'||level||' SURP'||level||' SURM'||level,1,200) as CST_NAME
from dual
connect by level <= &P_CUSTOMERS;

commit;

prompt === Loading INV_HEAD ===
insert /*+ append */ into GL.INV_HEAD (INV_ID, CST_ID, INV_DATE, INV_STS, INV_STS_DATE)
select
  level as INV_ID,
  trunc(dbms_random.value(1, &P_CUSTOMERS + 1)) as CST_ID,
  (&P_DATE_FROM) + trunc(dbms_random.value(0, (&P_DATE_TO) - (&P_DATE_FROM) + 1)) as INV_DATE,
  case mod(level,4)
    when 0 then 'N'  -- new
    when 1 then 'P'  -- paid
    when 2 then 'C'  -- cancelled
    else 'A'         -- active
  end as INV_STS,
  (&P_DATE_FROM) + trunc(dbms_random.value(0, (&P_DATE_TO) - (&P_DATE_FROM) + 1)) as INV_STS_DATE
from dual
connect by level <= &P_INVOICES;

commit;

prompt === Loading INV_DET ===
-- IND_ID is PK. We generate lines and attach them to invoices/products.
insert /*+ append */ into GL.INV_DET (IND_ID, INV_ID, PRO_ID, IND_AMOUNT, IND_SUBTOTAL)
select
  level as IND_ID,
  trunc(dbms_random.value(1, &P_INVOICES + 1)) as INV_ID,
  trunc(dbms_random.value(1, &P_PRODUCTS + 1)) as PRO_ID,
  trunc(dbms_random.value(1, 20)) as IND_AMOUNT,
  -- subtotal roughly proportional (not enforced by constraints)
  round(dbms_random.value(100, 500000), 2) as IND_SUBTOTAL
from dual
connect by level <= &P_INV_LINES;

commit;

prompt === Loading DSP_HEAD ===
insert /*+ append */ into GL.DSP_HEAD (DSP_ID, CST_ID, DSP_DATE, DSP_STS, DSP_STS_DATE)
select
  level as DSP_ID,
  trunc(dbms_random.value(1, &P_CUSTOMERS + 1)) as CST_ID,
  (&P_DATE_FROM) + trunc(dbms_random.value(0, (&P_DATE_TO) - (&P_DATE_FROM) + 1)) as DSP_DATE,
  case mod(level,3)
    when 0 then 'N'
    when 1 then 'S'
    else 'D'
  end as DSP_STS,
  (&P_DATE_FROM) + trunc(dbms_random.value(0, (&P_DATE_TO) - (&P_DATE_FROM) + 1)) as DSP_STS_DATE
from dual
connect by level <= &P_DISPATCHES;

commit;

prompt === Loading DSP_DET ===
-- Must reference DSP_HEAD(DSP_ID) and INV_DET(IND_ID)
insert /*+ append */ into GL.DSP_DET (DSD_ID, DSP_ID, IND_ID, DSD_AMOUNT)
select
  level as DSD_ID,
  trunc(dbms_random.value(1, &P_DISPATCHES + 1)) as DSP_ID,
  trunc(dbms_random.value(1, &P_INV_LINES + 1)) as IND_ID,
  round(dbms_random.value(10, 500000), 2) as DSD_AMOUNT
from dual
connect by level <= &P_DSP_LINES;

commit;

prompt === Gathering stats ===
begin
  dbms_stats.gather_table_stats('GL','PRO_MASTER', cascade => true);
  dbms_stats.gather_table_stats('GL','CST_MASTER', cascade => true);
  dbms_stats.gather_table_stats('GL','INV_HEAD', cascade => true);
  dbms_stats.gather_table_stats('GL','INV_DET', cascade => true);
  dbms_stats.gather_table_stats('GL','DSP_HEAD', cascade => true);
  dbms_stats.gather_table_stats('GL','DSP_DET', cascade => true);
end;
/

prompt === Row counts ===
select 'CST_MASTER' tbl, count(*) cnt from GL.CST_MASTER union all
select 'PRO_MASTER', count(*) from GL.PRO_MASTER union all
select 'INV_HEAD',   count(*) from GL.INV_HEAD union all
select 'INV_DET',    count(*) from GL.INV_DET union all
select 'DSP_HEAD',   count(*) from GL.DSP_HEAD union all
select 'DSP_DET',    count(*) from GL.DSP_DET;

prompt === Done ===
