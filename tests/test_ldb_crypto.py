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


def test_load_or_create_key_with_hex_env(monkeypatch: pytest.MonkeyPatch):
    hex_key = "a" * 64  # 64 valid hex chars → bytes.fromhex path (line 44)
    monkeypatch.setenv("LDB_MASTER_KEY", hex_key)
    key = ldb_crypto.load_or_create_key()
    assert key == bytes.fromhex(hex_key)


def test_load_or_create_key_with_raw_32_char_string(monkeypatch: pytest.MonkeyPatch):
    # "z"*32 is 32 chars, not all hex, base64-decodes to 24 bytes (not 32) → raw path (line 52)
    raw = "z" * 32
    monkeypatch.setenv("LDB_MASTER_KEY", raw)
    key = ldb_crypto.load_or_create_key()
    assert key == raw.encode("utf-8")


def test_load_or_create_key_base64_not_32_bytes_raises(monkeypatch: pytest.MonkeyPatch):
    # base64 of 16 bytes → 24-char string, decodes to 16 bytes ≠ 32 → False branch (line 47→51)
    b64_16 = base64.b64encode(b"x" * 16).decode()  # 24-char string
    monkeypatch.setenv("LDB_MASTER_KEY", b64_16)
    with pytest.raises(ValueError):
        ldb_crypto.load_or_create_key()


def test_decrypt_raises_for_non_encrypted_token():
    with pytest.raises(ValueError, match="non-encrypted"):
        ldb_crypto.decrypt("plaintext_token")


def test_chmod600_raises_secret_error_on_oserror(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import os

    path = tmp_path / "test.key"
    path.write_bytes(b"key_data")
    monkeypatch.setattr(os, "chmod", lambda p, mode: (_ for _ in ()).throw(OSError("permission denied")))
    with pytest.raises(ValueError, match="Unable to restrict permissions"):
        ldb_crypto._chmod600(path)
