print("__name__=", __name__)
if __name__ == "__main__":
    import re
    import oracledb
    from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
    parallel_max = 10  # Ajusta a tu necesidad
    dsn = "leon.etimo.cl:1521/alpha"
    user = "tdb"
    password = "etm1tdb"
    action = "MANT_PROD" # "MANT_PROD" o "MANT_HIST"
    mode = "ALL" # "ALL" o "QUERY ONLY"
    chunk_size = 100000 # Tamaño del chunk para el procesamiento
    use_added_cols = True # Si es True, agrega columnas adicionales a la tabla de destino
    execute = True  # Si es True, ejecuta el bloque PL/SQL, si es False, solo lo imprime


def nvl(value, default):
    return default if value is None else value

def process_table(owner, table_name, plsql_code, referencing_tables, process_date, dsn, user, password):
    """Worker: ejecuta el bloque PL/SQL para una tabla"""
    print(f"Processing table {owner}.{table_name}...")
    try:
        conn = oracledb.connect(user=user, password=password, dsn=dsn)
        print(f"Connected to {dsn} as {user}")
        print("conn=", conn)
        cursor = conn.cursor()
        print(f"Executing PL/SQL for {owner}.{table_name}...")
        # Intentar marcar TSTART
        p_sqlcode = cursor.var(oracledb.NUMBER)
        p_out_message = cursor.var(oracledb.STRING)
        cursor.callproc('check_save_status', [
            owner, table_name, process_date, 'MANT_PROD', 'TSTART',
            None, None, None, None, 0, plsql_code, p_sqlcode, p_out_message
        ])
        print(f"check_save_status: {p_sqlcode.getvalue()}")
        if p_sqlcode.getvalue() is not None:
            return (owner, table_name, 'SKIPPED', p_sqlcode.getvalue(), p_out_message.getvalue())
        # Verificar dependencias con check_referencing_tables
        if referencing_tables:
            ref_table_array = cursor.arrayvar(oracledb.STRING, [f"{r[0]}.{r[1]}" for r in referencing_tables])
            cursor.callproc('check_referencing_tables', [ref_table_array, process_date])
        # Ejecutar el bloque PL/SQL
        cursor.execute(plsql_code)
        conn.commit()
        return (owner, table_name, 'TEND', None, None)
    except oracledb.DatabaseError as e:
        error, = e.args
        return (owner, table_name, 'ERROR', error.code, error.message)
    finally:
        cursor.close()
        conn.close()

def all_status_tend(tables_cnf, process_date, conn):
    """Verifica si todas las tablas están en TEND para la fecha de proceso"""
    cursor = conn.cursor()
    for (owner, table_name) in tables_cnf.keys():
        cursor.execute("""
            SELECT ctl_status FROM tdb_ctl
            WHERE ctl_owner = :1 AND ctl_table_name = :2 AND ctl_process_date = TO_DATE(:3, 'YYYYMMDD')
        """, [owner, table_name, process_date])
        result = cursor.fetchone()
        if not result or result[0] != 'TEND':
            cursor.close()
            return False
    cursor.close()
    return True

def execute(tables_cnf, process_date, connection):
    processes = []
    active_tables = set()
    with ProcessPoolExecutor(max_workers=parallel_max) as executor:
        while True:
            launched = False
            # Mientras haya cupo y tablas listas, lanzar procesos
            while len(processes) < parallel_max:
                for (owner, table_name), table_info in tables_cnf.items():
                    if (owner, table_name) in active_tables:
                        continue  # ya está corriendo
                    # Lanzar proceso sin verificar dependencias en Python (se hará en Oracle)
                    print(f"Launching process for {owner}.{table_name}...")
                    future = executor.submit(
                        process_table, owner, table_name, table_info['plsql'],
                        table_info['referencing_tables'], process_date, dsn, user, password
                    )
                    processes.append(future)
                    active_tables.add((owner, table_name))
                    launched = True
                    break  # lanzar solo uno por iteración
                if not launched:
                    break  # no hay más tablas disponibles por ahora
            if processes:
                # Esperar que termine al menos un proceso
                done, _ = wait(processes, return_when=FIRST_COMPLETED)
                for d in done:
                    owner, table_name, status, sqlcode, message = d.result()
                    print(f"Table {owner}.{table_name} finished with status {status}")
                    if status == 'ERROR':
                        print(f"  Error {sqlcode}: {message}")
                    elif status == 'SKIPPED':
                        print(f"  Skipped {sqlcode}: {message}")
                    active_tables.remove((owner, table_name))
                    processes.remove(d)
            else:
                break
    if all_status_tend(tables_cnf, process_date, connection):
        print("All tables processed successfully.")
        exit(0)
    else:
        print("Some tables did not finish successfully.")
        exit(1)

def run_cli():
    try:
        connection = oracledb.connect(user=user, password=password, dsn=dsn)
        cursor = connection.cursor()
        cursor.execute("select to_char(sysdate,'YYYYMMDD') from dual")
        process_date, = cursor.fetchone()
        if not execute:
            process_date = "&process_date"
        cursor.execute("""select cnf_id, cnf_prod_owner, cnf_hist_owner, cnf_table_name, cnf_months_keep_prod, cnf_months_keep_hist, cnf_exec_day, cnf_frecuency, 
            cnf_is_active, cnf_purge_limit_date_expr, cnf_additional_expr, cnf_prod_orphan_purge, cnf_orphan_chk_column, cnf_has_lob, 
            cnf_ref_tables, cnf_join_expr, cnf_hint_expr, cnf_long_cols
            from tdb_conf
            where cnf_is_active = 'Y'""")
        columns = [col[0].lower() for col in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        tables_cnf = {}
        for row in rows:
            key = (row["cnf_prod_owner"], row["cnf_table_name"])
            if key not in tables_cnf: tables_cnf[key] = { "conds": [], "referencing_tables": [] }
            tables_cnf[key]["conds"].append(row)
            ref_tables = row["cnf_ref_tables"]
            if ref_tables:
                for ref_table in ref_tables.split(","):
                    ref_table = ref_table.strip()
                    if "." in ref_table: ref_owner, ref_table = ref_table.split(".")
                    else: ref_owner = row["cnf_prod_owner"]
                    ref_table, alias = ref_table.split(" ")
                    ref_key = (ref_owner.upper(), ref_table.upper())
                    if ref_key not in tables_cnf: tables_cnf[ref_key] = { "conds": [], "referencing_tables": [] }
                    tables_cnf[ref_key]["referencing_tables"].append(key)
        if not execute:
            print("""


                whenever oserror exit 1
                whenever sqlerror exit 1
                set echo on ver off trimspool on
                spool tdb_BHE.log
                COLUMN process_date NEW_VALUE process_date
                SELECT TO_CHAR(SYSDATE, 'YYYYMMDD') process_date FROM DUAL;
            """)
        for key, table_cnf in tables_cnf.items():
            cnd = table_cnf["conds"][0]
            ref_tables = cnd["cnf_ref_tables"]
            added_conds = [("A", cond) for cond in table_cnf["conds"]]
            referencing_tables = table_cnf["referencing_tables"]
            # print("referencing_tables", referencing_tables)
            if ref_tables:
                for ref_table in ref_tables.split(","):
                    ref_table = ref_table.strip()
                    if "." in ref_table: ref_owner, ref_table = ref_table.split(".")
                    else: ref_owner = row["cnf_prod_owner"]
                    ref_table, alias = ref_table.split(" ")
                    ref_conds = tables_cnf[(ref_owner, ref_table)]["conds"]
                    added_conds.extend([(alias, cond) for cond in ref_conds])
            # print("added_conds", added_conds)
            for al, cd in added_conds:
                cond_list = []
                pld_expr = nvl(cd["cnf_purge_limit_date_expr"], "")
                addtl_expr = nvl(cd["cnf_additional_expr"], "")
                mkp = nvl(cd["cnf_months_keep_prod"], 9999999999999)
                if addtl_expr:
                    cond_list.append(f"({" ".join(addtl_expr.replace("@", f"{al}.").splitlines())})")
                if pld_expr:
                    cond_list.append(f"({pld_expr.replace("@", f"{al}.")} < add_months(l_process_date,-{mkp}))")
                cd["cond_expr"] = " and ".join(cond_list)
            cnf_months_keep_hist_max = max(cd.get("cnf_months_keep_hist", 0) or 0 for al, cd in added_conds)
            where_expr = "\n        or ".join([f"({cd["cond_expr"]})" for al, cd in added_conds if cd["cond_expr"]])
            other_cols_exprs = [f"{cd["cnf_purge_limit_date_expr"].replace("@", f"{al}.")} tdb_date_{al}{cd["cnf_id"]}"
                for al, cd in added_conds if cd["cnf_purge_limit_date_expr"] and al != "A"] if use_added_cols else []
            other_cols_alias = [f"tdb_date_{al}{cd["cnf_id"]}"
                for al, cd in added_conds if cd["cnf_purge_limit_date_expr"] and al != "A"] if use_added_cols else []
            cnf_table_name, cnf_prod_owner, cnf_hist_owner = cnd["cnf_table_name"], cnd["cnf_prod_owner"], cnd["cnf_hist_owner"]
            cnf_join_expr, cnf_prod_orphan_purge, cnf_hint_expr = cnd["cnf_join_expr"], cnd["cnf_prod_orphan_purge"], cnd["cnf_hint_expr"]
            cursor.execute("""select lower(column_name) column_name
                from all_tab_columns
                where table_name = upper(:1) and owner = upper(:2) and column_id is not null
                order by column_id""", (cnf_table_name, cnf_prod_owner))
            table_columns = [row[0] for row in cursor.fetchall()]
            join_expr = cnf_join_expr.replace("@", "left outer" if cnf_prod_orphan_purge == "Y" else "inner") if cnf_join_expr else ""
            query_expr = f"""from {cnf_table_name.lower()} A{f"""
            {join_expr}""" if join_expr else ""}{f"""
            where {where_expr}""" if where_expr else ""}"""
            if cnd["cnf_long_cols"] and cnd["cnf_has_lob"] != "N":
                long_cols = [c.strip().lower() for c in cnd["cnf_long_cols"].split(",")]
                table_columns = list(set([col.lower for col in table_columns]) - set(long_cols))
            plsql = f"""declare
                    l_prod_owner varchar2(50):= '{cnf_prod_owner}';
                    l_hist_owner varchar2(50):= '{cnf_hist_owner}';
                    l_action varchar2(10):= '{action}';
                    l_mode varchar2(10):= '{mode}';
                    l_message varchar2(200) := case when l_mode = 'ALL' then null else 'QUERY ONLY' end;
                    l_table_name varchar2(50):= '{cnf_table_name}';
                    l_process_date date := to_date('{process_date}', 'YYYYMMDD');
                    l_referencing_tables t_referencing_tables := t_referencing_tables({", ".join([f"'{rt[0]+"."+rt[1]}'" for rt in referencing_tables])});
                    l_process_start date;
                    l_record_count pls_integer := 0;
                    l_plsql clob;
                    l_sqlcode number := null;
                    l_out_message varchar2(200) := null;{f"""
                    l_chunk_size pls_integer := {chunk_size};
                    l_chunk_start date;
                    cursor c_records is 
                        select /*+ {cnf_hint_expr} */ A.rowid,
                        {", ".join([lde for lde in ["A.*"]+other_cols_exprs])}
                        {query_expr};
                    type t_records is table of c_records%rowtype index by pls_integer;
                    r_rec t_records;""" if cnd["cnf_has_lob"] == "N" else ""}
                begin
                    l_process_start := sysdate;
                    check_save_status(l_prod_owner, l_table_name, l_process_date, l_action, 'TSTART', l_process_start, null, null, l_message, 0, null, l_sqlcode, l_out_message);
                    if l_sqlcode is not null then
                        raise_application_error(l_sqlcode, l_out_message);
                    end if;
                    check_referencing_tables(l_referencing_tables, l_process_date);
                    commit;{f"""
                    open c_records;
                    loop
                        l_chunk_start := sysdate;
                        check_save_status(l_prod_owner, l_table_name, l_process_date, l_action, 'CSTART', null, l_chunk_start, null, l_message, 0, null, l_sqlcode, l_out_message);
                        fetch c_records bulk collect into r_rec limit l_chunk_size;
                        if r_rec.count > 0 then
                            if (l_mode = 'ALL') then
                                {f"""for i in 1 .. r_rec.count loop
                                    insert into {cnf_table_name.lower()}@hist ({", ".join([col.lower() for col in table_columns+other_cols_alias])})
                                    values ({", ".join([f"r_rec(i).{col.lower()}" for col in table_columns+other_cols_alias])});
                                end loop;""" if action == "MANT_PROD" and cnf_months_keep_hist_max>0 else ""}
                                forall i in 1 .. r_rec.count
                                    delete from {cnf_table_name.lower()} where rowid = r_rec(i).rowid;
                            end if;
                            l_record_count := l_record_count + r_rec.count;
                            check_save_status(l_prod_owner, l_table_name, l_process_date, l_action, 'CEND', null, l_chunk_start, sysdate, l_message, r_rec.count, null, l_sqlcode, l_out_message);
                            commit;
                        else
                            exit;
                        end if;
                    end loop;
                    close c_records;""" if cnd["cnf_has_lob"] == "N" else f"""
                    if (l_mode = 'ALL') then
                        insert into {cnf_table_name.lower()}@hist ({", ".join([col.lower() for col in table_columns+other_cols_alias])})
                        select /*+ {cnf_hint_expr} */
                        {", ".join([f"a.{col.lower()}" for col in table_columns]+[col.lower() for col in other_cols_exprs])}
                        {query_expr};
                        delete from {cnf_table_name.lower()} where rowid in
                        (select /*+ {cnf_hint_expr} */ a.rowid
                        {query_expr});
                        l_record_count := sql%rowcount;
                    else
                        select /*+ {cnf_hint_expr} */ count(*) into l_record_count
                        {query_expr};
                    end if;"""}
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
            if not execute:
                print(f"rem table: {cnf_table_name}")
                print(plsql)
                print("/")
            tables_cnf[key]["plsql"] = plsql
        if not execute:
            print("""spool off
    exit 0
    """)
        cursor.close()
        execute(tables_cnf, process_date, connection)
        connection.close()
    except oracledb.DatabaseError as e:
        error, = e.args
        print(f"Database error: {error.code}: {error.message}")
    except Exception as e:
        print(f"Error: {str(e)}")
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'connection' in locals():
            connection.close()
    
