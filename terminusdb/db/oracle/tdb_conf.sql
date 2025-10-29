DROP TABLE "TDB"."TDB_CONF";
--------------------------------------------------------
--  DDL for Sequence TDB_CNF_ID
--------------------------------------------------------

drop sequence "TDB"."TDB_CNF_ID";
CREATE SEQUENCE "TDB"."TDB_CNF_ID";
--------------------------------------------------------
--  DDL for Table TDB_CONF
--------------------------------------------------------

CREATE TABLE "TDB"."TDB_CONF" 
(
    "CNF_ID" NUMBER, 
    "CNF_SOURCE_OWNER" VARCHAR2(50 BYTE), 
    "CNF_HISTORY_OWNER" VARCHAR2(50 BYTE), 
    "CNF_TABLE" VARCHAR2(50 BYTE), 
    "CNF_RETAIN_MONTHS_SOURCE" NUMBER, 
    "CNF_RETAIN_MONTHS_HISTORY" NUMBER, 
    "CNF_EXEC_DAY" VARCHAR2(10 BYTE), 
    "CNF_FRECUENCY" VARCHAR2(10 BYTE), 
    "CNF_IS_ACTIVE" CHAR(1 BYTE), 
    "CNF_PURGE_DATE_EXPR" VARCHAR2(100 BYTE), 
    "CNF_ADDITIONAL_FILTER_EXPR" VARCHAR2(4000 BYTE), 
    "CNF_SOURCE_ORPHAN_PURGE" CHAR(1 BYTE), 
    "CNF_ORPHAN_CHECK_COLUMN" VARCHAR2(4000 BYTE), 
    "CNF_HAS_LOB_COLUMNS" CHAR(1 BYTE), 
    "CNF_REFERENCING_TABLES" VARCHAR2(100 BYTE), 
    "CNF_JOIN_EXPR" VARCHAR2(4000 BYTE), 
    "CNF_HINT_EXPR" VARCHAR2(4000 BYTE), 
    "CNF_LONG_COLUMNS" VARCHAR2(4000 BYTE)
);

--------------------------------------------------------
--  DDL for Index CNF_CONF_PK
--------------------------------------------------------

CREATE UNIQUE INDEX "TDB"."CNF_CONF_PK" ON "TDB"."TDB_CONF" ("CNF_ID");

--------------------------------------------------------
--  DDL for Index CNF_CONF_I1
--------------------------------------------------------

CREATE INDEX "TDB"."CNF_CONF_I1" ON "TDB"."TDB_CONF" ("CNF_SOURCE_OWNER", "CNF_TABLE");

--------------------------------------------------------
--  Constraints for Table TDB_CONF
--------------------------------------------------------

ALTER TABLE "TDB"."TDB_CONF" ADD CONSTRAINT "CNF_CONF_PK" PRIMARY KEY ("CNF_ID") USING INDEX;

--------------------------------------------------------
--  DDL for Trigger TDB_CNF_ID_CR
--------------------------------------------------------

CREATE OR REPLACE EDITIONABLE TRIGGER "TDB"."TDB_CNF_CR" 
    before insert on "TDB_CONF" 
    for each row 
begin  
    if inserting then 
        if :NEW."CNF_ID" is null then 
            select TDB_CNF_ID.nextval into :NEW."CNF_ID" from dual; 
        end if; 
    end if; 
end;
/
ALTER TRIGGER "TDB"."TDB_CNF_CR" ENABLE;

REM INSERTING into TDB.TDB_CONF
SET DEFINE OFF;
Insert into TDB.TDB_CONF (CNF_SOURCE_OWNER,CNF_HISTORY_OWNER,CNF_TABLE,CNF_RETAIN_MONTHS_SOURCE,CNF_RETAIN_MONTHS_HISTORY,CNF_EXEC_DAY,CNF_FRECUENCY,CNF_IS_ACTIVE,CNF_PURGE_DATE_EXPR,CNF_ADDITIONAL_FILTER_EXPR,CNF_SOURCE_ORPHAN_PURGE,CNF_ORPHAN_CHECK_COLUMN,CNF_HAS_LOB_COLUMNS,CNF_REFERENCING_TABLES,CNF_JOIN_EXPR,CNF_HINT_EXPR,CNF_LONG_COLUMNS) values ('GL','GLHST','INV_HEAD','3','11',null,'D','Y','INV_DSP_DATE','A.INV_STATUS=''D''',null,null,'N',null,null,'full(A)',null);
Insert into TDB.TDB_CONF (CNF_SOURCE_OWNER,CNF_HISTORY_OWNER,CNF_TABLE,CNF_RETAIN_MONTHS_SOURCE,CNF_RETAIN_MONTHS_HISTORY,CNF_EXEC_DAY,CNF_FRECUENCY,CNF_IS_ACTIVE,CNF_PURGE_DATE_EXPR,CNF_ADDITIONAL_FILTER_EXPR,CNF_SOURCE_ORPHAN_PURGE,CNF_ORPHAN_CHECK_COLUMN,CNF_HAS_LOB_COLUMNS,CNF_REFERENCING_TABLES,CNF_JOIN_EXPR,CNF_HINT_EXPR,CNF_LONG_COLUMNS) values ('GL','GLHST','INV_HEAD','1','5',null,'D','Y','INV_DATE','A.INV_STATUS=''A''',null,null,'N',null,null,'full(A)',null);
Insert into TDB.TDB_CONF (CNF_SOURCE_OWNER,CNF_HISTORY_OWNER,CNF_TABLE,CNF_RETAIN_MONTHS_SOURCE,CNF_RETAIN_MONTHS_HISTORY,CNF_EXEC_DAY,CNF_FRECUENCY,CNF_IS_ACTIVE,CNF_PURGE_DATE_EXPR,CNF_ADDITIONAL_FILTER_EXPR,CNF_SOURCE_ORPHAN_PURGE,CNF_ORPHAN_CHECK_COLUMN,CNF_HAS_LOB_COLUMNS,CNF_REFERENCING_TABLES,CNF_JOIN_EXPR,CNF_HINT_EXPR,CNF_LONG_COLUMNS) values ('GL','GLHST','INV_DET',null,null,null,null,null,null,null,'Y','INV_ID B','N','INV_HEAD','JOIN INV_HEAD ON B.INV_ID=A.INV_ID','full(A) full(B) use_hash(A, B)',null);
Insert into TDB.TDB_CONF (CNF_SOURCE_OWNER,CNF_HISTORY_OWNER,CNF_TABLE,CNF_RETAIN_MONTHS_SOURCE,CNF_RETAIN_MONTHS_HISTORY,CNF_EXEC_DAY,CNF_FRECUENCY,CNF_IS_ACTIVE,CNF_PURGE_DATE_EXPR,CNF_ADDITIONAL_FILTER_EXPR,CNF_SOURCE_ORPHAN_PURGE,CNF_ORPHAN_CHECK_COLUMN,CNF_HAS_LOB_COLUMNS,CNF_REFERENCING_TABLES,CNF_JOIN_EXPR,CNF_HINT_EXPR,CNF_LONG_COLUMNS) values ('GL','GLHST','DSP_HEAD','3','11',null,'D','Y','DSP_DATE','A.DSP_STATUS=''D''',null,null,'N',null,null,'full(A)',null);
Insert into TDB.TDB_CONF (CNF_SOURCE_OWNER,CNF_HISTORY_OWNER,CNF_TABLE,CNF_RETAIN_MONTHS_SOURCE,CNF_RETAIN_MONTHS_HISTORY,CNF_EXEC_DAY,CNF_FRECUENCY,CNF_IS_ACTIVE,CNF_PURGE_DATE_EXPR,CNF_ADDITIONAL_FILTER_EXPR,CNF_SOURCE_ORPHAN_PURGE,CNF_ORPHAN_CHECK_COLUMN,CNF_HAS_LOB_COLUMNS,CNF_REFERENCING_TABLES,CNF_JOIN_EXPR,CNF_HINT_EXPR,CNF_LONG_COLUMNS) values ('GL','GLHST','DSP_HEAD','1','5',null,'D','Y','DSP_DATE','A.DSP_STATUS=''A''',null,null,'N',null,null,'full(A)',null);
Insert into TDB.TDB_CONF (CNF_SOURCE_OWNER,CNF_HISTORY_OWNER,CNF_TABLE,CNF_RETAIN_MONTHS_SOURCE,CNF_RETAIN_MONTHS_HISTORY,CNF_EXEC_DAY,CNF_FRECUENCY,CNF_IS_ACTIVE,CNF_PURGE_DATE_EXPR,CNF_ADDITIONAL_FILTER_EXPR,CNF_SOURCE_ORPHAN_PURGE,CNF_ORPHAN_CHECK_COLUMN,CNF_HAS_LOB_COLUMNS,CNF_REFERENCING_TABLES,CNF_JOIN_EXPR,CNF_HINT_EXPR,CNF_LONG_COLUMNS) values ('GL','GLHST','DSP_DET',null,null,null,null,null,null,null,'Y','B.DSP_ID, C.IND_ID','N','DSP_HEAD, INV_DET','JOIN DSP_HEAD B ON B.DSP_ID=A.DSP_ID JOIN INV_DET C ON C.IND_ID = A.IND_ID','full(A) full(B) full(C) use_hash(A, B, C)',null);

commit;

create or replace procedure check_save_status(p_owner in varchar2, p_table_name in varchar2, p_process_date in date, p_action in varchar2, p_status in varchar2, p_process_start in date,
    p_chunk_start in date, p_process_end in date, p_message in varchar2, p_rows_processed in varchar2, p_plsql in varchar2, p_sqlcode in out number, p_out_message out varchar2) is
    l_status varchar2(10);
    l_process_start date;
    l_process_date date;
    l_rowid rowid;
    in_use exception;
    pragma exception_init(in_use, -54);
begin
    if nvl(p_sqlcode,0) not in (-20001, -20002, -20003) then
        begin
            select ctl_status, ctl_process_date, ctl_process_start, rowid into l_status, l_process_date, l_process_start, l_rowid
            from tdb_ctl
            where ctl_owner = p_owner and ctl_table_name = p_table_name
            for update nowait;
            if p_status = 'TSTART' and l_status != 'TEND' and (sysdate - l_process_start)*3600*24 < 5 then
                p_sqlcode := -20002;
                p_out_message := 'Table '||p_table_name||' is currently in process without lock';
                return;
            elsif p_status = 'TSTART' and l_status = 'TEND' and l_process_date = p_process_date then
                rollback;
                p_sqlcode := -20003;
                p_out_message := 'Table '||p_table_name||' is already processed for date '||to_char(p_process_date,'YYYYMMDD');
                return;
            end if;
            update tdb_ctl set ctl_process_date = p_process_date, ctl_action = p_action, ctl_status = p_status,
                ctl_process_start = nvl(p_process_start, ctl_process_start), ctl_process_end = p_process_end,
                ctl_rows_processed = decode(p_status, '{TABLE_START}', 0, ctl_rows_processed) + p_rows_processed, ctl_plsql = nvl(p_plsql, ctl_plsql)
            where rowid = l_rowid;
        exception
        when in_use then
            p_sqlcode := -20001;
            p_out_message := 'Table '||p_table_name||' is currently in process';
            return;
        when no_data_found then
            insert into tdb_ctl (ctl_owner, ctl_table_name, ctl_process_date, ctl_action, ctl_status,
                ctl_process_start, ctl_process_end, ctl_rows_processed, ctl_plsql)
            values (p_owner, p_table_name, p_process_date, p_action, p_status,
                l_process_start, null, 0, p_plsql);
        end;
    end if;
    insert into tdb_log (log_id, log_owner, log_table_name, log_process_date, log_action, log_status,
        log_process_start, log_process_end, log_message, log_rows_processed, log_plsql)
    values (tdb_log_id.nextval, p_owner, p_table_name, p_process_date, p_action, p_status,
        nvl(p_chunk_start, p_process_start), p_process_end, p_message, p_rows_processed, p_plsql);
end check_save_status;
/

CREATE OR REPLACE TYPE t_referencing_tables AS TABLE OF VARCHAR2(100);
/
create or replace procedure check_referencing_tables(p_referencing_tables t_referencing_tables, p_process_date date) is
    l_ref_table varchar2(100);
    l_ref_owner varchar2(100);
    l_ref_table_name varchar2(100);
    l_dummy number;
begin
    for i in 1..p_referencing_tables.count loop
        l_ref_table := p_referencing_tables(i);
        l_ref_owner := substr(l_ref_table,1,instr(l_ref_table,'.')-1);
        l_ref_table_name := substr(l_ref_table,instr(l_ref_table,'.')+1);
        begin
            select 1 into l_dummy
            from tdb_ctl
            where ctl_owner = l_ref_owner and ctl_table_name = l_ref_table_name and ctl_process_date = p_process_date and ctl_status='TEND';
        exception
        when no_data_found then
            raise_application_error(-20004,'Referencing table '||l_ref_table||' was not fully processed for date '||to_char(p_process_date,'YYYYMMDD'));
        end;
    end loop;
end check_referencing_tables;
/