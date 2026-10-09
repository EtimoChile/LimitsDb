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
