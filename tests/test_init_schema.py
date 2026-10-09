from pathlib import Path

import pytest

from limitsdb.core import ldb_utils


def test_init_schema_creates_config_ilm_and_secrets_and_encrypts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Spec: README > ldb-init — creates config.yml, ilm.yml, secrets.json and
    # ilm.example.yml; encrypts any plaintext secrets
    # Given: a clean config root with encryption mocked
    calls = {}
    monkeypatch.setattr(ldb_utils, "load_or_create_key", lambda: calls.setdefault("key", True))
    monkeypatch.setattr(ldb_utils, "encrypt_secrets_in_place", lambda *a, **k: calls.setdefault("enc", True))

    # When: init_schema is called
    cfg, ilm, sec, ex = ldb_utils.init_schema(schema="s", profile=None, config_root=str(tmp_path), overwrite=True)

    # Then: all four files exist and encryption was performed
    assert cfg.exists() and ilm.exists() and sec.exists() and ex.exists()
    assert calls == {"key": True, "enc": True}


def test_init_schema_without_examples_returns_none_for_example_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Spec: README > ldb-init > --no-examples — skips the ilm.example.yml file
    # Given: with_examples=False
    monkeypatch.setattr(ldb_utils, "load_or_create_key", lambda: None)
    monkeypatch.setattr(ldb_utils, "encrypt_secrets_in_place", lambda *a, **k: None)

    # When: init_schema is called without examples
    cfg, ilm, sec, ex = ldb_utils.init_schema(
        schema="s", profile=None, config_root=str(tmp_path), overwrite=True, with_examples=False
    )

    # Then: main files exist but example path is None
    assert cfg.exists() and ilm.exists() and sec.exists()
    assert ex is None


def test_init_schema_without_auto_encrypt_skips_encryption(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Spec: README > ldb-init — auto_encrypt=False skips the encryption step so
    # secrets can be filled in manually before encrypting separately
    # Given: auto_encrypt disabled
    calls: dict = {}
    monkeypatch.setattr(ldb_utils, "load_or_create_key", lambda: None)
    monkeypatch.setattr(ldb_utils, "encrypt_secrets_in_place", lambda *a, **k: calls.setdefault("enc", True))

    # When: init_schema is called with auto_encrypt=False
    ldb_utils.init_schema(schema="s", profile=None, config_root=str(tmp_path), overwrite=True, auto_encrypt=False)

    # Then: encryption was not called
    assert "enc" not in calls


def test_render_config_template_returns_non_empty_string():
    # Spec: README > ldb-init — generated config.yml includes inline help for
    # all supported options
    # Given: no specific prerequisites
    # When: template is rendered
    output = ldb_utils.render_config_template_with_help()

    # Then: output is a non-empty string
    assert isinstance(output, str)
    assert len(output) > 0


def test_write_config_yaml_no_overwrite_when_file_exists(tmp_path: Path):
    # Spec: README > ldb-init > --overwrite — without overwrite flag an existing file
    # is preserved unchanged
    # Given: an existing config file with custom content
    first = ldb_utils.write_config_yaml("s", None, str(tmp_path), overwrite=True)
    first.write_text("original: true\n", encoding="utf-8")

    # When: write_config_yaml is called again without overwrite
    second = ldb_utils.write_config_yaml("s", None, str(tmp_path), overwrite=False)

    # Then: the file is unchanged
    assert second == first
    assert second.read_text(encoding="utf-8") == "original: true\n"


def test_write_ilm_yaml_no_overwrite_when_file_exists(tmp_path: Path):
    # Spec: README > ldb-init > --overwrite — without overwrite the ILM file is
    # preserved unchanged
    # Given: an existing ilm.yml with custom content
    first = ldb_utils.write_ilm_yaml("s", None, str(tmp_path), overwrite=True)
    first.write_text("original: []\n", encoding="utf-8")

    # When: write_ilm_yaml is called again without overwrite
    second = ldb_utils.write_ilm_yaml("s", None, str(tmp_path), overwrite=False)

    # Then: the file is unchanged
    assert second == first
    assert second.read_text(encoding="utf-8") == "original: []\n"


def test_write_ilm_example_no_overwrite_when_file_exists(tmp_path: Path):
    # Spec: README > ldb-init > --overwrite — without overwrite the example file is
    # preserved unchanged
    # Given: an existing example file with custom content
    first = ldb_utils.write_ilm_example("s", None, str(tmp_path), overwrite=True)
    first.write_text("original example\n", encoding="utf-8")

    # When: write_ilm_example is called again without overwrite
    second = ldb_utils.write_ilm_example("s", None, str(tmp_path), overwrite=False)

    # Then: the file is unchanged
    assert second == first
    assert second.read_text(encoding="utf-8") == "original example\n"
