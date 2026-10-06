from limitsdb.core import ldb_meta, ldb_status


def test_meta_markers():
    assert ldb_meta.Help("txt").text == "txt"
    assert ldb_meta.Secret().enabled is True
    assert ldb_meta.Cli("--flag").flag == "--flag"
    assert ldb_meta.Env("NAME").name == "NAME"
    assert ldb_meta.CliOnly().enabled is True


def test_status_constants():
    assert ldb_status.Status.TABLE_START == "TSTART"
    assert ldb_status.Status.ERROR == "ERROR"
