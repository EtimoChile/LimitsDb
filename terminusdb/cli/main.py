import re
import oracledb

def run_cli():
    # Ajusta la cadena de conexión según tus datos de host, puerto, servicio, usuario, etc.
    dsn = "leon.etimo.cl:1521/alpha"
    user = "tdb"
    password = "etm1tdb"
    connection = oracledb.connect(user=user, password=password, dsn=dsn)
    cursor = connection.cursor()
    query = """select cnf_id, cnf_prod_owner, cnf_hist_owner, cnf_table, cnf_months_keep_prod, cnf_months_keep_hist, cnf_exec_day, cnf_frecuency, 
        cnf_is_active, cnf_purge_limit_date_expr, cnf_additional_expr, cnf_prod_orphan_purge, cnf_orphan_chk_column, cnf_has_lob, 
        cnf_ref_tables, cnf_join_expr, cnf_hint_expr, cnf_long_cols
        from tdb_conf
        where cnf_is_active = 'Y' or cnf_ref_tables is not null"""
    cursor.execute(query)
    columns = [col[0].lower() for col in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    grouped_conds = {}
    for row in rows:
        key = (row["cnf_prod_owner"], row["cnf_table"])
        if key not in grouped_conds: grouped_conds[key] = []
        grouped_conds[key].append(row)
    for key, conds in grouped_conds.items():
        cnd = conds[0]
        ref_tables = cnd["cnf_ref_tables"]
        added_conds = [("A", cond) for cond in conds]
        if ref_tables:
            for ref_table in ref_tables.split(","):
                ref_table = ref_table.strip()
                if "." in ref_table: ref_owner, ref_table = ref_table.split(".")
                else: ref_owner = row["cnf_prod_owner"]
                ref_table, alias = ref_table.split(" ")
                ref_conds = grouped_conds[(ref_owner, ref_table)]
                added_conds.extend([(alias, cond) for cond in ref_conds])
        cnf_months_keep_hist_max = max(cd.get("cnf_months_keep_hist", 0) or 0 for al, cd in added_conds)
        where_expr = """
        OR """.join([
            f"(({cd["cnf_additional_expr"].replace("@", f"{al}.")}) AND ({cd["cnf_purge_limit_date_expr"].replace("@", f"{al}.")} < add_months(fec_proceso,-{cd["cnf_months_keep_prod"]})))"
            for al, cd in added_conds if cd["cnf_additional_expr"] or cd["cnf_purge_limit_date_expr"]
        ])
        other_cols_exprs = [f"{cd["cnf_purge_limit_date_expr"].replace("@", f"{al}.")} tdb_date_{al}{cd["cnf_id"]}"
            for al, cd in added_conds if cd["cnf_purge_limit_date_expr"] and al != "A"]
        other_cols_exprs += [f"{cd["cnf_purge_limit_date_expr"].replace("@", f"{al}.")} tdb_date_{al}{cd["cnf_id"]}"
            for al, cd in added_conds if cd["cnf_purge_limit_date_expr"] and al != "A"]
        added_exprs_cols = [
            (al, col) for al, cd in added_conds if al != "A" and cd["cnf_additional_expr"]
            for col in re.findall(r"@([a-zA-Z_][a-zA-Z0-9_]*)", cd["cnf_additional_expr"])
        ]
        print(key)
        print(added_conds)
        print(added_exprs_cols)
        other_cols_alias = [f"tdb_date_{al}{cd["cnf_id"]}" for al, cd in added_conds if cd["cnf_purge_limit_date_expr"] and al != "A"]
        cnf_table, cnf_prod_owner = cnd["cnf_table"], cnd["cnf_prod_owner"]
        cnf_join_expr, cnf_prod_orphan_purge, cnf_hint_expr = cnd["cnf_join_expr"], cnd["cnf_prod_orphan_purge"], cnd["cnf_hint_expr"]
        cursor.execute("""select column_name
            from all_tab_columns
            where table_name = :1 and owner = :2 and column_id is not null
            order by column_id""", (cnf_table, cnf_prod_owner))
        table_columns = [row[0] for row in cursor.fetchall()]
        join_expr = cnf_join_expr.replace("@", "OUTER" if cnf_prod_orphan_purge == "Y" else "INNER LEFT") if cnf_join_expr else ""
        query_expr = f"""FROM {cnf_table} A{f"""
        {join_expr}""" if join_expr else ""}{f"""
        WHERE {where_expr}""" if where_expr else ""}"""
        action = "MANT_PROD"
        mode = "ALL"
        log_record = True
        process_date = "2025-01-13"
        query_only = "Y"
        state = "OK"
        if cnd["cnf_has_lob"] == "N":
            plsql = f"""DECLARE
    CURSOR cur_registros(p_accion VARCHAR2, p_fecha_min DATE, p_fecha_max DATE) IS 
        SELECT /*+ {cnf_hint_expr} */ A.rowid,
        {", ".join([lde for lde in ["A.*"]+other_cols_exprs])}
        {query_expr};
    accion			VARCHAR2(10):= '{action}';
    modo			VARCHAR2(10):= '{mode}';
    tabla			VARCHAR2(20):= '{cnf_table}';
    logreg			BOOLEAN     := {log_record};  
    fecha_proceso	DATE		:= to_date('{process_date}', 'YYYY-MM-DD');
    soloquery		VARCHAR2(5)	:= '{query_only}';
    estado			VARCHAR2(20):= '{state}';
    c_limit			PLS_INTEGER	:= 100000;	       
    inicio_lote		DATE;
    TYPE registro_t IS TABLE OF cur_registros%ROWTYPE INDEX BY PLS_INTEGER;
    reg registro_t;
BEGIN
    inicio_lote	 := sysdate;
    OPEN cur_registros (accion, null, fecha_max);
    LOOP
        FETCH cur_registros	BULK COLLECT INTO reg LIMIT c_limit;
        IF reg.COUNT > 0 THEN
            IF (modo = 'ALL') THEN
                {f"""FOR i IN 1 .. reg.COUNT LOOP
                    INSERT INTO {cnf_table}@hist ({", ".join(table_columns+other_cols_alias)})
                    VALUES {", ".join([f"reg(i).{col}" for col in table_columns+other_cols_alias])};
                END LOOP;""" if action == "MANT_PROD" and cnf_months_keep_hist_max>0 else ""}
                FORALL i IN 1 .. reg.COUNT
                    DELETE FROM {cnf_table}
                    WHERE rowid = reg(i).rowid;
                COMMIT;
            END IF;
            :num_reg := :num_reg + reg.COUNT;
            IF (logreg) THEN
                insert INTO ILM_LOG (ID, GRUPO, TABLA, ACCION, INICIO, FIN, ESTADO, REGISTROS, OBSERVACION) 
                VALUES (id_ilm_log.NEXTVAL, grupo, tabla, accion||soloquery, inicio_lote, sysdate, estado, :num_reg, null);
                COMMIT;	
            END IF;
            inicio_lote := sysdate;
        ELSE
            EXIT;
        END IF;
    END LOOP;
    CLOSE cur_registros;
EXCEPTION
    WHEN OTHERS THEN
        ROLLBACK;
        RAISE;
END;
"""
        else:
            if cnd["cnf_long_cols"]:
                long_cols = [c.strip() for c in cnd["cnf_long_cols"].split(",")]
                table_columns = list(set(table_columns) - set(long_cols))
            plsql = f"""DECLARE
	p_accion		VARCHAR2(10):= '{action}';
	tabla			VARCHAR2(20):= 'table';
	fecha_proceso	DATE		:= to_date('{process_date}', 'YYYY-MM-DD');
BEGIN
    INSERT INTO {cnf_table}@hist ({", ".join(table_columns+other_cols_alias)})
    SELECT /*+ {cnf_hint_expr} */
    {", ".join([f"A.{col}" for col in table_columns]+other_cols_exprs)}
    {query_expr};
    DELETE /*+ {cnf_hint_expr} */ FROM {cnf_table} WHERE ROWID IN
    (SELECT A.rowid
    {query_expr});
    :num_reg := SQL%ROWCOUNT;
    COMMIT;
EXCEPTION
	WHEN OTHERS THEN
		ROLLBACK;
		RAISE;
END;
"""
        print(cnd)
        print("plsql", plsql)
    cursor.close()
    connection.close()
