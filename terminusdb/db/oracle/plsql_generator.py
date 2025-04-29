from terminusdb.core.utils import get_logger


def generate_plsql_block(config, prod_owner, hist_owner, table_name, process_date, hint_expr,
        query_expr, table_columns, other_cols_exprs, other_cols_alias, referencing_tables, has_lob, cnf_months_keep_hist_max):
    #Generates the PL/SQL block to process a table according to the configuration
    logger = get_logger()
    logger.debug(f"Generating PL/SQL block for table {prod_owner}.{table_name} with process date {process_date}")
    plsql = f"""declare
    l_prod_owner varchar2(50) := '{prod_owner}';
    l_hist_owner varchar2(50) := '{hist_owner}';
    l_action varchar2(10) := '{config.action}';
    l_mode varchar2(10) := '{config.mode}';
    l_message varchar2(200) := case when l_mode = 'ALL' then null else 'QUERY ONLY' end;
    l_table_name varchar2(50) := '{table_name}';
    l_process_date date := to_date('{process_date}', 'YYYYMMDD');
    l_referencing_tables t_referencing_tables := t_referencing_tables({", ".join([f"'{rt[0]}.{rt[1]}'" for rt in referencing_tables])});
    l_process_start date;
    l_record_count pls_integer := 0;
    l_plsql clob := {"null" if config.print_process else ":plsql_code"};
    l_sqlcode number := null;
    l_out_message varchar2(200) := null;
    """
    if not has_lob:
        plsql += f"""
    l_chunk_size pls_integer := {config.chunk_size};
    l_chunk_start date;
    cursor c_records is
        select /*+ {hint_expr} */ A.rowid,
        {", ".join(["A.*"] + other_cols_exprs)}
        {query_expr};
    type t_records is table of c_records%rowtype index by pls_integer;
    r_rec t_records;
    """

    plsql += """
begin
    l_process_start := sysdate;
    check_save_status(l_prod_owner, l_table_name, l_process_date, l_action, 'TSTART', l_process_start, null, null, l_message, 0, l_plsql, l_sqlcode, l_out_message);
    if l_sqlcode is not null then
        raise_application_error(l_sqlcode, l_out_message);
    end if;
    check_referencing_tables(l_referencing_tables, l_process_date);
    commit;
    """

    if not has_lob:
        plsql += """
    open c_records;
    loop
        l_chunk_start := sysdate;
        check_save_status(l_prod_owner, l_table_name, l_process_date, l_action, 'CSTART', null, l_chunk_start, null, l_message, 0, null, l_sqlcode, l_out_message);
        fetch c_records bulk collect into r_rec limit l_chunk_size;
        if r_rec.count > 0 then
            if (l_mode = 'ALL') then
        """
        if config.action == "MANT_PROD" and cnf_months_keep_hist_max > 0:
            plsql += f"""
                for i in 1 .. r_rec.count loop
                    insert into {table_name.lower()}@hist ({", ".join(table_columns + other_cols_alias)})
                    values ({", ".join([f"r_rec(i).{col}" for col in table_columns + other_cols_alias])});
                end loop;
            """
        plsql += f"""
                forall i in 1 .. r_rec.count
                    delete from {table_name.lower()} where rowid = r_rec(i).rowid;
            end if;
            l_record_count := l_record_count + r_rec.count;
            check_save_status(l_prod_owner, l_table_name, l_process_date, l_action, 'CEND', null, l_chunk_start, sysdate, l_message, r_rec.count, null, l_sqlcode, l_out_message);
            commit;
        else
            exit;
        end if;
    end loop;
    close c_records;
    """
    else:
        plsql += f"""
    if (l_mode = 'ALL') then
        insert into {table_name.lower()}@hist ({", ".join(table_columns + other_cols_alias)})
        select /*+ {hint_expr} */
        {", ".join([f"a.{col}" for col in table_columns] + other_cols_exprs)}
        {query_expr};
        delete from {table_name.lower()} where rowid in
        (select /*+ {hint_expr} */ a.rowid
        {query_expr});
        l_record_count := sql%rowcount;
    else
        select /*+ {hint_expr} */ count(*) into l_record_count
        {query_expr};
    end if;
    """

    plsql += """
    check_save_status(l_prod_owner, l_table_name, l_process_date, l_action, 'TEND', l_process_start, null, sysdate, l_message, l_record_count, null, l_sqlcode, l_out_message);
    commit;
exception
    when others then
        rollback;
        check_save_status(l_prod_owner, l_table_name, l_process_date, l_action, 'ERROR', l_process_start, null, sysdate, sqlerrm, l_record_count, null, l_sqlcode, l_out_message);
        commit;
        if l_sqlcode not in (-20003) then
            raise;
        end if;
end;"""
    return plsql
