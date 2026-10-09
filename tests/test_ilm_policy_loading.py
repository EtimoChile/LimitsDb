from pathlib import Path

import pytest
import yaml

from limitsdb.core import ldb_ilm_config


def test_duplicate_table_in_ilm_file_is_rejected(tmp_path: Path):
    # Spec: limitsdb/resources/ilm.example.yml — each table appears once;
    # duplicate entries indicate a configuration mistake and must be caught early.
    # Given: ILM file with the same table listed twice
    path = tmp_path / "ilm.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "tables": [
                    {"source_owner": "SRC", "table_name": "T1"},
                    {"source_owner": "SRC", "table_name": "T1"},
                ]
            }
        ),
        encoding="utf-8",
    )

    # When / Then: loading raises — the error surfaces before any ILM run
    with pytest.raises(ValueError):
        ldb_ilm_config.load_rows_from_yaml(str(path))


def test_table_with_active_condition_is_included_in_loaded_rows(tmp_path: Path):
    # Spec: limitsdb/resources/ilm.example.yml — is_active controls whether a
    # condition participates in ILM processing
    # Given: a table with one active condition
    path = tmp_path / "ilm.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "tables": [
                    {
                        "source_owner": "SRC",
                        "table_name": "T1",
                        "retain_months_source": 3,
                        "conds": [{"is_active": True}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    # When: rows are loaded
    rows = ldb_ilm_config.load_rows_from_yaml(str(path))

    # Then: the table appears in the result
    assert len(rows) == 1
    assert rows[0]["table_name"] == "T1"


def test_table_with_all_inactive_conditions_is_excluded(tmp_path: Path):
    # Spec: limitsdb/resources/ilm.example.yml — is_active: false excludes the
    # condition; a table with no active conditions contributes nothing to the run
    # Given: a table whose only condition is inactive
    path = tmp_path / "ilm.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "tables": [
                    {
                        "source_owner": "SRC",
                        "table_name": "T1",
                        "conds": [{"is_active": False}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    # When: rows are loaded
    rows = ldb_ilm_config.load_rows_from_yaml(str(path))

    # Then: the table is absent — no rows to process
    assert len(rows) == 0


def test_retention_months_defaults_propagate_to_loaded_row(tmp_path: Path):
    # Spec: limitsdb/resources/ilm.example.yml — retain_months_history controls
    # how long rows are kept in the history schema
    # Given: a table with explicit retention values
    path = tmp_path / "ilm.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "tables": [
                    {
                        "source_owner": "SRC",
                        "table_name": "T2",
                        "retain_months_source": 1,
                        "retain_months_history": 12,
                        "conds": [{"is_active": True}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    # When: rows are loaded
    rows = ldb_ilm_config.load_rows_from_yaml(str(path))

    # Then: retention values are preserved exactly
    assert rows[0]["retain_months_history"] == 12
    assert rows[0]["retain_months_source"] == 1


def test_non_mapping_tables_value_raises_type_error(tmp_path: Path):
    # Spec: limitsdb/resources/ilm.example.yml — tables must be a list of mappings
    # Given: ILM file where tables is a scalar
    path = tmp_path / "ilm.yml"
    path.write_text("tables: 123", encoding="utf-8")

    # When / Then: loading raises TypeError — malformed structure is rejected
    with pytest.raises(TypeError):
        ldb_ilm_config.load_rows_from_yaml(str(path))


def test_missing_tables_key_raises_key_error(tmp_path: Path):
    # Spec: limitsdb/resources/ilm.example.yml — root must contain a "tables" key
    # Given: ILM file without a tables key
    path = tmp_path / "ilm.yml"
    path.write_text(yaml.safe_dump({"wrong": []}), encoding="utf-8")

    # When / Then: loading raises — the required key is absent
    with pytest.raises(KeyError):
        ldb_ilm_config.load_rows_from_yaml(str(path))


def test_empty_ilm_file_returns_no_rows(tmp_path: Path):
    # Spec: limitsdb/resources/ilm.example.yml — an empty file contributes no ILM rules;
    # the loader treats absent YAML content as an empty configuration, not an error
    # Given: an ILM file with no content
    path = tmp_path / "ilm.yml"
    path.write_text("", encoding="utf-8")

    # When: rows are loaded
    rows = ldb_ilm_config.load_rows_from_yaml(str(path))

    # Then: no rows — empty file is valid and contributes nothing
    assert rows == []


def test_ilm_file_with_list_root_raises_type_error(tmp_path: Path):
    # Spec: limitsdb/resources/ilm.example.yml — root must be a mapping; a list root
    # indicates a malformed file and must be rejected early
    # Given: ILM file whose root is a YAML list
    path = tmp_path / "ilm.yml"
    path.write_text("- item1\n- item2\n", encoding="utf-8")

    # When / Then: TypeError is raised — the loader does not silently accept non-dict roots
    with pytest.raises(TypeError):
        ldb_ilm_config.load_rows_from_yaml(str(path))


def test_ilm_file_with_non_mapping_table_entry_raises_type_error(tmp_path: Path):
    # Spec: limitsdb/resources/ilm.example.yml — each element of the tables list must
    # be a mapping; a scalar or list element must be rejected
    # Given: ILM file where tables contains a scalar element
    path = tmp_path / "ilm.yml"
    path.write_text(yaml.safe_dump({"tables": [123]}), encoding="utf-8")

    # When / Then: TypeError is raised — malformed table entries are caught at load time
    with pytest.raises(TypeError):
        ldb_ilm_config.load_rows_from_yaml(str(path))


def test_duplicate_table_in_resolved_ilm_config_is_rejected(monkeypatch: pytest.MonkeyPatch):
    # Spec: limitsdb/resources/ilm.example.yml — each table appears once across merged
    # config layers; duplicates indicate a configuration mistake and must be caught early
    # Given: merged ILM config with the same table listed twice (both with active conds)
    config = {
        "tables": [
            {"source_owner": "SRC", "table_name": "T1", "conds": [{"is_active": True}]},
            {"source_owner": "SRC", "table_name": "T1", "conds": [{"is_active": True}]},
        ]
    }
    monkeypatch.setattr(ldb_ilm_config, "load_ilm_config", lambda **_: config)

    # When / Then: resolve_and_load_ilm_rows raises — the duplicate surfaces before any ILM run
    with pytest.raises(ValueError, match="Duplicate"):
        ldb_ilm_config.resolve_and_load_ilm_rows(schema="s", profile=None, config_dir=None)


def test_resolved_ilm_rows_with_explicit_conds_and_multi_table(monkeypatch: pytest.MonkeyPatch):
    # Spec: limitsdb/resources/ilm.example.yml — tables with explicit conds skip the
    # default-active injection; inactive conds exclude the table from the result
    # Given: two tables — one with explicit active cond, one with inactive cond
    config = {
        "tables": [
            {"source_owner": "SRC", "table_name": "T1", "conds": [{"is_active": True}]},
            {"source_owner": "SRC", "table_name": "T2", "conds": [{"is_active": False}]},
        ]
    }
    monkeypatch.setattr(ldb_ilm_config, "load_ilm_config", lambda **_: config)

    # When: rows are resolved
    rows = ldb_ilm_config.resolve_and_load_ilm_rows(schema="s", profile=None, config_dir=None)

    # Then: only the active table contributes rows
    assert len(rows) == 1
    assert rows[0]["table_name"] == "T1"


def test_resolve_and_load_ilm_rows_returns_rows_from_resolved_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Spec: README > Run ILM > --ilm-config-file — a specific ILM YAML file
    # can bypass DB discovery
    # Given: a valid ILM file and a resolver that points to it
    config = {
        "tables": [
            {
                "source_owner": "SRC",
                "table_name": "T1",
                "retain_months_source": 1,
                "retain_months_history": 2,
            }
        ]
    }
    monkeypatch.setattr(ldb_ilm_config, "load_ilm_config", lambda **_: config)

    # When: rows are resolved and loaded
    rows = ldb_ilm_config.resolve_and_load_ilm_rows(schema="s", profile=None, config_dir=None)

    # Then: the row contains the configured retention values
    assert rows[0]["retain_months_history"] == 2
    assert rows[0]["source_owner"] == "SRC"
