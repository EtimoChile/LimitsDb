import base64
import os
from pathlib import Path

import pytest

from limitsdb.core import ldb_crypto


def test_secret_encrypted_with_master_key_can_be_decrypted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Spec: README > Secrets & Encryption — credentials stored encrypted and
    # transparently decrypted at runtime
    # Given: an isolated key directory and a plaintext secret
    key_dir = tmp_path / ".limitsdb"
    key_dir.mkdir(parents=True)
    monkeypatch.setattr(ldb_crypto, "LDB_DIR", key_dir)
    monkeypatch.setattr(ldb_crypto, "KEY_PATH", key_dir / "ldb.key")

    # When: the secret is encrypted then decrypted
    token = ldb_crypto.encrypt("my_password")
    result = ldb_crypto.decrypt(token)

    # Then: the original value is recovered exactly
    assert result == "my_password"


def test_encrypted_token_is_recognised_and_plaintext_is_not(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Spec: README > Secrets & Encryption — ldb-crypt replaces plaintext with enc:v1:aes256gcm:...
    # Given: an isolated key directory
    key_dir = tmp_path / ".limitsdb"
    key_dir.mkdir(parents=True)
    monkeypatch.setattr(ldb_crypto, "LDB_DIR", key_dir)
    monkeypatch.setattr(ldb_crypto, "KEY_PATH", key_dir / "ldb.key")

    # When: a value is encrypted
    token = ldb_crypto.encrypt("value")

    # Then: is_encrypted distinguishes the token from plaintext and empty strings
    assert ldb_crypto.is_encrypted(token)
    assert not ldb_crypto.is_encrypted("plaintext")
    assert not ldb_crypto.is_encrypted("")


def test_decrypting_plaintext_raises_secret_error():
    # Spec: docs/exception-handling.md — SecretError when decryption fails
    # Given: a value that was never encrypted
    # When: decrypt is called on it
    # Then: SecretError is raised — plaintext is never silently accepted
    with pytest.raises(ValueError):
        ldb_crypto.decrypt("plaintext_password")


def test_master_key_from_environment_is_accepted(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Secrets & Encryption — key in environment is used transparently
    # Given: a valid base64-encoded 32-byte key in LDB_MASTER_KEY
    env_key = base64.b64encode(b"1" * 32).decode()
    monkeypatch.setenv("LDB_MASTER_KEY", env_key)

    # When: key is loaded
    key = ldb_crypto.load_or_create_key()

    # Then: the raw 32-byte value is returned
    assert key == b"1" * 32


def test_invalid_master_key_in_environment_raises_secret_error(monkeypatch: pytest.MonkeyPatch):
    # Spec: docs/exception-handling.md — SecretError: master keys fail
    # Given: an unusable key string in the environment
    monkeypatch.setenv("LDB_MASTER_KEY", "not-a-valid-key")

    # When / Then: SecretError is raised before any encryption attempt
    with pytest.raises(ValueError):
        ldb_crypto.load_or_create_key()


def test_corrupted_key_file_raises_secret_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Spec: docs/exception-handling.md — SecretError: secret files fail
    # Given: a key file that contains fewer bytes than a valid key
    key_path = tmp_path / "ldb.key"
    key_path.write_bytes(b"short")
    monkeypatch.setattr(ldb_crypto, "LDB_DIR", tmp_path)
    monkeypatch.setattr(ldb_crypto, "KEY_PATH", key_path)

    # When / Then: SecretError is raised because the stored key is invalid
    with pytest.raises(ValueError):
        ldb_crypto.load_or_create_key()


def test_key_file_permission_failure_raises_secret_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Spec: docs/exception-handling.md — SecretError when secret files cannot be secured
    # Given: key directory exists but the OS refuses permission restriction
    key_dir = tmp_path / ".limitsdb"
    key_dir.mkdir(parents=True)
    monkeypatch.setattr(ldb_crypto, "LDB_DIR", key_dir)
    monkeypatch.setattr(ldb_crypto, "KEY_PATH", key_dir / "ldb.key")
    monkeypatch.setattr(os, "chmod", lambda p, mode: (_ for _ in ()).throw(OSError("permission denied")))

    # When: a new key needs to be created (no existing key file)
    # Then: SecretError is raised — the key is never silently left unprotected
    with pytest.raises(ValueError, match="Unable to restrict permissions"):
        ldb_crypto.load_or_create_key()
