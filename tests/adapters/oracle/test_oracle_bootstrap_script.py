import re

import pytest

from limitsdb.core.ldb_ddl import render_script
from limitsdb.db.ldb_engines import (
    ColumnDefinition,
    DatabaseLinkDefinition,
    IndexDefinition,
    RoleDefinition,
    SequenceDefinition,
    TableDefinition,
    UserDefinition,
)
from limitsdb.db.oracle.ldb_engine_impl import OracleEngine

_SECRET = "S3cretPwd#1"


def _bootstrap(
    *,
    title: str = "SOURCE database of schema s",
    roles: list[RoleDefinition],
    users: list[UserDefinition],
    tables: list[TableDefinition],
    sequences: list[SequenceDefinition],
    supporting_owner: str,
    links: list[DatabaseLinkDefinition],
    runtime_username: str,
    runtime_dsn: str,
) -> str:
    """The script ldb-impl writes when admin credentials are absent: the ensure_* fragments, rendered."""
    fragments: list[str] = []
    OracleEngine.script_password_prompts(users, links, ddl_sink=fragments.append)
    OracleEngine.ensure_roles(None, roles, ddl_sink=fragments.append)
    OracleEngine.ensure_users(None, users, ddl_sink=fragments.append)
    OracleEngine.ensure_tables(None, tables, ddl_sink=fragments.append)
    OracleEngine.ensure_sequences(None, sequences, ddl_sink=fragments.append)
    OracleEngine.ensure_supporting_objects(None, supporting_owner, ddl_sink=fragments.append)
    OracleEngine.ensure_database_links(
        None, links, ddl_sink=fragments.append, connect_as=(runtime_username, runtime_dsn)
    )
    return render_script(OracleEngine, title, fragments)


def _script() -> str:
    return _bootstrap(
        roles=[RoleDefinition(name="LDB_ROLE")],
        users=[
            UserDefinition(
                name="LDB_USER", password=_SECRET, roles=("LDB_ROLE",), system_privileges=("CREATE SESSION",)
            )
        ],
        tables=[
            TableDefinition(
                owner="LDB_USER",
                name="LDB_CTL",
                columns=(ColumnDefinition(name="ID", data_type="NUMBER", nullable=False),),
                primary_key=("ID",),
                indexes=(IndexDefinition(name="LDB_CTL_IX", columns=("ID",), unique=False),),
            )
        ],
        sequences=[SequenceDefinition(owner="LDB_USER", name="LDB_LOG_ID")],
        supporting_owner="LDB_USER",
        links=[DatabaseLinkDefinition(name="LDB_HIST", username="HIST_USER", password=_SECRET, dsn="host/svc")],
        runtime_username="LDB_USER",
        runtime_dsn="host/svc",
    )


def test_bootstrap_script_never_contains_passwords_and_prompts_for_them_hidden():
    # Spec: README > ldb-impl without admin credentials — the script embeds no password; it prompts for them
    script = _script()

    assert _SECRET not in script
    prompts = re.findall(r"^accept (\w+) char prompt .* hide$", script, flags=re.MULTILINE)
    assert len(prompts) == 2  # the user (asked once, reused to reconnect as it) and the database link
    assert "ldb_runtime_pwd" not in script


def test_bootstrap_script_asks_for_every_password_before_changing_anything():
    # Spec: README > ldb-impl without admin credentials — the script asks for all passwords at its start
    script = _script()

    last_prompt = max(match.start() for match in re.finditer(r"^accept ", script, flags=re.MULTILINE))
    first_change = min(script.index(marker) for marker in ("begin\n  execute immediate", "GRANT ", "connect "))
    assert last_prompt < first_change
    assert script.index("accept ldb_user_pwd_1") < script.index("accept ldb_link_pwd_1")


def test_bootstrap_script_follows_the_sqlplus_contract_of_the_pending_ddl_file():
    # Spec: same contract as the ldb-run pending-DDL file — stops at the first error, ends with an OK banner
    script = _script()

    assert "whenever sqlerror exit failure" in script
    assert "whenever oserror exit failure" in script
    assert "LimitsDb: OK" in script
    assert script.rstrip().endswith("exit success")


def test_bootstrap_script_creates_objects_only_inside_blocks_tolerating_already_exists():
    # Spec: the script is safe to rerun — each CREATE tolerates only its own "already exists" error
    script = _script()
    blocks = re.findall(
        r"begin\n  execute immediate '(.*?)';\nexception.*?not in \(([-\d, ]+)\) then raise; end if;\nend;\n/",
        script,
        flags=re.DOTALL,
    )
    tolerated = {statement.split()[1] + ":" + codes for statement, codes in blocks}

    assert "ROLE:-1921" in tolerated
    assert "USER:-1920" in tolerated
    assert "TABLE:-955" in tolerated
    assert "SEQUENCE:-955" in tolerated
    assert any("PRIMARY KEY" in statement and codes == "-2260, -2264" for statement, codes in blocks)
    # No bare CREATE outside a tolerant block
    outside = re.sub(r"begin\n  execute immediate.*?\n/", "", script, flags=re.DOTALL)
    assert not re.search(r"^CREATE (ROLE|USER|TABLE|SEQUENCE)", outside, flags=re.MULTILINE)


def test_bootstrap_script_creates_database_link_after_connecting_as_runtime_user():
    # Spec: the database link is created connected as the runtime user, never as the admin
    script = _script()

    connect_at = script.index('connect ldb_user/"&ldb_user_pwd_1"@"host/svc"')
    link_at = script.index("CREATE DATABASE LINK")
    assert connect_at < link_at
    assert script.count("CREATE DATABASE LINK") == 1
    assert "user_db_links" in script  # skipped when the link already exists


def _tables_script(table: TableDefinition) -> str:
    fragments: list[str] = []
    OracleEngine.ensure_tables(None, [table], ddl_sink=fragments.append)
    return "\n".join(fragments)


def test_bootstrap_script_fails_when_the_existing_primary_key_is_not_the_required_one():
    # Spec: README > ldb-impl without admin credentials — an "already exists" error for the primary key is
    # tolerated only when the existing key has the required name and columns; otherwise the script fails
    # Given: a control table whose primary key is the named constraint on two columns
    table = TableDefinition(
        owner="LDB_USER",
        name="LDB_LOG",
        columns=(ColumnDefinition(name="A", data_type="NUMBER"), ColumnDefinition(name="B", data_type="NUMBER")),
        primary_key=("A", "B"),
    )

    # When: its script is generated
    script = _tables_script(table)

    # Then: after the tolerant block, the dictionary is checked for that name, type and column order, and a
    # difference raises an error (which stops the script: whenever sqlerror exit failure)
    tolerant = script.index("-2260, -2264")
    check = script.index("from dba_constraints", tolerant)
    assert "c.owner = 'LDB_USER' and c.table_name = 'LDB_LOG'" in script[check:]
    assert "c.constraint_type = 'P' and c.constraint_name = 'LDB_LOG_PK'" in script[check:]
    assert "within group (order by cc.position)" in script[check:]
    assert ") = 'A,B';" in script[check:]
    assert "raise_application_error(-20001" in script[check:]
    assert "LimitsDb does not change it" in script[check:]


def test_bootstrap_script_checks_the_primary_key_of_a_lowercase_table_as_it_was_created():
    # Spec: limitsdb/resources/ilm.example.yml > Identifier rules — identifiers are exact dictionary names, so a
    # lowercase name is a quoted identifier and the dictionary check must look for it as written
    # Given: a table whose owner, name and key column are lowercase
    table = TableDefinition(
        owner="ldb", name="log", columns=(ColumnDefinition(name="id", data_type="NUMBER"),), primary_key=("id",)
    )

    # When: the bootstrap script is generated
    script = _tables_script(table)

    # Then: the primary key check names the table, constraint and column as they were created
    assert "c.owner = 'ldb' and c.table_name = 'log'" in script
    assert "c.constraint_name = 'log_PK'" in script
    assert ") = 'id';" in script
    assert "'LDB'" not in script


def _link_script(name: str) -> str:
    fragments: list[str] = []
    link = DatabaseLinkDefinition(name=name, username="U", password=_SECRET, dsn="host/svc")
    OracleEngine.ensure_database_links(None, [link], ddl_sink=fragments.append, connect_as=("ldb_user", "host/svc"))
    return "\n".join(fragments)


def test_bootstrap_script_detects_an_existing_database_link_by_exact_name_or_domain_suffix_only():
    # Spec: README > ldb-impl without admin credentials — an existing database link is skipped; a link
    # whose name merely starts with the configured one (HISTORY next to HIST) is another link
    # Given: a database link named HIST
    # When: the bootstrap script is generated
    script = _link_script("HIST")

    # Then: the link is looked up by exact name or by name plus domain, never by prefix
    assert "where db_link = 'HIST'" in script
    assert "or db_link like 'HIST.%' escape '\\'" in script
    assert "like 'HIST%'" not in script
    assert "upper(db_link)" not in script


def test_bootstrap_script_matches_underscore_and_percent_of_a_link_name_literally():
    # Spec: README > ldb-impl without admin credentials — an existing link is found by its own name, so `_` and
    # `%` in a link name must not act as LIKE wildcards and match another link
    # Given: a database link whose name contains `_` and `%`
    # When: the bootstrap script is generated
    script = _link_script("LDB_HI%T")

    # Then: both characters are escaped in the lookup
    assert "where db_link = 'LDB_HI%T'" in script
    assert "like 'LDB\\_HI\\%T.%' escape '\\'" in script


@pytest.mark.parametrize(("configured", "stored"), [("hist", "HIST"), ("Hist.Domain", "HIST.DOMAIN"), ("hi%t", "HI%T")])
def test_bootstrap_script_looks_a_database_link_up_the_way_the_dictionary_stores_it(configured: str, stored: str):
    # Spec: README > ldb-impl without admin credentials — an existing database link is skipped, so a rerun must
    # find the link it created; the dictionary keeps database link names in upper case
    # Given: a database link configured with lowercase or mixed-case letters
    # When: the bootstrap script is generated
    script = _link_script(configured)

    # Then: the lookup uses the upper-case name, so the link is found instead of created again (ORA-02011)
    assert f"where db_link = '{stored}'" in script
    assert f"where db_link = '{configured}'" not in script


def test_bootstrap_script_terminates_plsql_objects_with_slash():
    # Spec: supporting PL/SQL objects are created in the runtime user's schema and terminated for SQL*Plus
    script = _script()

    assert "alter session set current_schema = ldb_user;" in script
    for name in ("t_referencing_tables", "check_save_status", "check_referencing_tables"):
        assert name in script.lower()


@pytest.mark.parametrize("quote", ["'"])
def test_bootstrap_script_escapes_quotes_in_statements(quote: str):
    # Spec: statements are embedded in string literals, so quotes must be doubled
    script = _bootstrap(
        title="t",
        roles=[],
        users=[],
        tables=[
            TableDefinition(
                owner="O",
                name="T",
                columns=(ColumnDefinition(name="C", data_type="VARCHAR2", length=5, default=f"{quote}x{quote}"),),
            )
        ],
        sequences=[],
        supporting_owner="O",
        links=[],
        runtime_username="O",
        runtime_dsn="d",
    )

    assert "DEFAULT ''x''" in script


def test_bootstrap_script_warns_before_creating_a_user_that_already_exists_and_keeps_its_password():
    # Spec: README > ldb-impl without admin credentials — a rerun keeps the password of an existing user,
    # so the script tells the DBA instead of finishing silently with a password that may not match
    script = _script()

    notice = script.index("already exists: its password was not changed")
    assert script.index("accept ldb_user_pwd_1") < notice < script.index("CREATE USER")
    assert "from dba_users where username = 'LDB_USER'" in script
    assert "serveroutput on" in script
    assert _SECRET not in script


def test_script_fragments_need_no_database_connection():
    # Spec: README > ldb-impl without admin credentials — nothing is read from or created in the database
    fragments: list[str] = []

    with pytest.raises(ValueError):
        OracleEngine.ensure_roles(object(), [RoleDefinition(name="R")], ddl_sink=fragments.append)  # type: ignore[arg-type]

    assert fragments == []


def test_bootstrap_script_warns_before_reconnecting_that_a_rejected_password_means_the_user_already_existed():
    # Spec: README > ldb-impl without admin credentials — the script says, before it reconnects as the
    # runtime user, that an ORA-01017 there means the user already existed with another password
    script = _script()

    hint = script.index("If this fails with ORA-01017")
    assert hint < script.index('connect ldb_user/"&ldb_user_pwd_1"')


def test_bootstrap_script_looks_up_a_lowercase_user_in_the_dictionary_as_it_was_created():
    # Spec: identifiers are handled in data-dictionary form — a lowercase name is a quoted identifier
    fragments: list[str] = []
    user = UserDefinition(name="ldb", password=_SECRET)

    OracleEngine.ensure_users(None, [user], ddl_sink=fragments.append)
    script = "\n".join(fragments)

    assert 'CREATE USER "ldb"' in script
    assert "from dba_users where username = 'ldb'" in script
    assert "User ldb already exists" in script
    assert "'LDB'" not in script


def test_oracle_script_envelope_stops_at_the_first_error_and_ends_with_exit_success():
    # Spec: DatabaseEngine.script_preamble/script_prompt/script_epilogue — the engine owns the client commands
    # of the scripts the core composes
    # When: the engine is asked for the envelope of a non-interactive and an interactive script
    plain = OracleEngine.script_preamble(interactive=False)
    interactive = OracleEngine.script_preamble(interactive=True)

    # Then: both stop on the first error; only the interactive one accepts substitution values and notices
    for lines in (plain, interactive):
        assert "whenever sqlerror exit failure" in lines
        assert "whenever oserror exit failure" in lines
    assert "set echo on define off sqlblanklines on" in plain
    assert "set echo on define on verify off sqlblanklines on serveroutput on" in interactive
    assert OracleEngine.script_prompt("LimitsDb: OK") == "prompt LimitsDb: OK"
    assert OracleEngine.script_epilogue() == ["exit success"]
