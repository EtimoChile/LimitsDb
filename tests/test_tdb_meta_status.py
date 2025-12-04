from terminusdb.core import tdb_meta, tdb_status


def test_meta_markers():
    assert tdb_meta.Help("txt").text == "txt"
    assert tdb_meta.Secret().enabled is True
    assert tdb_meta.Cli("--flag").flag == "--flag"
    assert tdb_meta.Env("NAME").name == "NAME"
    assert tdb_meta.CliOnly().enabled is True


def test_status_constants():
    assert tdb_status.Status.TABLE_START == "TSTART"
    assert tdb_status.Status.ERROR == "ERROR"
