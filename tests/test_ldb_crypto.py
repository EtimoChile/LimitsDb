import base64
from pathlib import Path

import pytest

from limitsdb.core import ldb_crypto


def test_load_or_create_key_with_env(monkeypatch: pytest.MonkeyPatch):
    env_key = base64.b64encode(b"1" * 32).decode()
    monkeypatch.setenv("LDB_MASTER_KEY", env_key)
    key = ldb_crypto.load_or_create_key()
    assert key == b"1" * 32


def test_encrypt_decrypt_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    key_dir = tmp_path / ".limitsdb"
    key_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ldb_crypto, "LDB_DIR", key_dir)
    monkeypatch.setattr(ldb_crypto, "KEY_PATH", key_dir / "ldb.key")
    token = ldb_crypto.encrypt("secret")
    assert ldb_crypto.is_encrypted(token)
    assert ldb_crypto.decrypt(token) == "secret"
    stored_key = ldb_crypto.KEY_PATH
    assert stored_key.exists()

    stored_key.write_bytes(b"short")
    with pytest.raises(ValueError):
        ldb_crypto.load_or_create_key()


def test_invalid_env_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LDB_MASTER_KEY", "not-a-key")
    with pytest.raises(ValueError):
        ldb_crypto.load_or_create_key()
