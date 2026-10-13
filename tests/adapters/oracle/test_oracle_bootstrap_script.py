import re

import pytest

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


def _script() -> str:
    return OracleEngine.generate_bootstrap_script(
        title="SOURCE database of schema s",
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
    assert len(prompts) == 3  # the user, the runtime user reconnection and the database link


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

    connect_at = script.index('connect ldb_user/"&ldb_runtime_pwd"@"host/svc"')
    link_at = script.index("CREATE DATABASE LINK")
    assert connect_at < link_at
    assert script.count("CREATE DATABASE LINK") == 1
    assert "user_db_links" in script  # skipped when the link already exists


def test_bootstrap_script_terminates_plsql_objects_with_slash():
    # Spec: supporting PL/SQL objects are created in the runtime user's schema and terminated for SQL*Plus
    script = _script()

    assert "alter session set current_schema = ldb_user;" in script
    for name in ("t_referencing_tables", "check_save_status", "check_referencing_tables"):
        assert name in script.lower()


@pytest.mark.parametrize("quote", ["'"])
def test_bootstrap_script_escapes_quotes_in_statements(quote: str):
    # Spec: statements are embedded in string literals, so quotes must be doubled
    script = OracleEngine.generate_bootstrap_script(
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
