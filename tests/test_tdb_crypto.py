import base64
import os
from pathlib import Path

import pytest

from terminusdb.core import tdb_crypto


def test_load_or_create_key_with_env(monkeypatch: pytest.MonkeyPatch):
    env_key = base64.b64encode(b"1" * 32).decode()
    monkeypatch.setenv("TDB_MASTER_KEY", env_key)
    key = tdb_crypto.load_or_create_key()
    assert key == b"1" * 32


def test_encrypt_decrypt_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    key_dir = tmp_path / ".terminusdb"
    key_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tdb_crypto, "TDB_DIR", key_dir)
    monkeypatch.setattr(tdb_crypto, "KEY_PATH", key_dir / "tdb.key")
    token = tdb_crypto.encrypt("secret")
    assert tdb_crypto.is_encrypted(token)
    assert tdb_crypto.decrypt(token) == "secret"
    stored_key = tdb_crypto.KEY_PATH
    assert stored_key.exists()

    stored_key.write_bytes(b"short")
    with pytest.raises(ValueError):
        tdb_crypto.load_or_create_key()


def test_invalid_env_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TDB_MASTER_KEY", "not-a-key")
    with pytest.raises(ValueError):
        tdb_crypto.load_or_create_key()
