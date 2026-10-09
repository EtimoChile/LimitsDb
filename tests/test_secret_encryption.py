import base64
import json
import os
from pathlib import Path

import pytest

from limitsdb.cli import ldb_run
from limitsdb.core import ldb_crypto, ldb_utils
from limitsdb.core.ldb_errors import SecretError

# ---------------------------------------------------------------------------
# Master key and encrypt/decrypt round-trip
# ---------------------------------------------------------------------------


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


def test_master_key_loaded_from_hex_string_in_environment(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Secrets & Encryption — LDB_MASTER_KEY accepts a 64-char hex string
    # Given: a valid 64-char hex key in LDB_MASTER_KEY
    raw = b"A" * 32
    monkeypatch.setenv("LDB_MASTER_KEY", raw.hex())  # 64 hex chars

    # When: key is loaded
    key = ldb_crypto.load_or_create_key()

    # Then: the 32-byte value decoded from hex is returned
    assert key == raw


def test_master_key_loaded_from_raw_32_byte_string_in_environment(monkeypatch: pytest.MonkeyPatch):
    # Spec: README > Secrets & Encryption — LDB_MASTER_KEY accepts a raw 32-char ASCII string
    # Given: a 32-char ASCII string in LDB_MASTER_KEY
    raw_str = "A" * 32
    monkeypatch.setenv("LDB_MASTER_KEY", raw_str)

    # When: key is loaded
    key = ldb_crypto.load_or_create_key()

    # Then: the UTF-8 encoded bytes are returned
    assert key == raw_str.encode("utf-8")


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


# ---------------------------------------------------------------------------
# Secrets file operations
# ---------------------------------------------------------------------------


def test_write_or_update_secrets_creates_initial_file(tmp_path: Path):
    # Spec: README > ldb-init — secrets file is created with an empty dict when absent
    # Given: no existing secrets file
    # When: write_or_update_secrets is called
    secret_file = ldb_utils.write_or_update_secrets("demo", None, str(tmp_path))

    # Then: file exists and contains a dict
    data = json.loads(secret_file.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


def test_write_or_update_secrets_does_not_overwrite_valid_json_array(tmp_path: Path):
    # Spec: docs/exception-handling.md — SecretError when the secrets file root
    # is not a dict; the original file must be preserved unmodified
    # Given: an existing secrets file with a JSON array at root
    secrets_path = tmp_path / "schemas" / "s" / "secrets.json"
    secrets_path.parent.mkdir(parents=True)
    secrets_path.write_text("[1, 2, 3]", encoding="utf-8")

    # When / Then: SecretError is raised AND the original file is untouched
    with pytest.raises(SecretError):
        ldb_utils.write_or_update_secrets("s", None, str(tmp_path))
    assert secrets_path.read_text(encoding="utf-8") == "[1, 2, 3]"


def test_encrypt_secrets_in_place_does_not_overwrite_valid_json_array(tmp_path: Path):
    # Spec: docs/exception-handling.md — SecretError when secrets file has non-dict
    # root; must not modify the file
    # Given: a secrets file with a JSON array
    secrets_path = tmp_path / "schemas" / "s" / "secrets.json"
    secrets_path.parent.mkdir(parents=True)
    secrets_path.write_text("[1, 2, 3]", encoding="utf-8")

    # When / Then: SecretError is raised and file is unchanged
    with pytest.raises(SecretError):
        ldb_utils.encrypt_secrets_in_place("s", None, str(tmp_path))
    assert secrets_path.read_text(encoding="utf-8") == "[1, 2, 3]"


def test_invalid_existing_secrets_are_not_overwritten(tmp_path: Path):
    # Spec: docs/exception-handling.md — SecretError: existing secrets files with
    # invalid JSON are rejected; neither operation may modify the file
    # Given: a broken secrets file
    secrets_path = tmp_path / "schemas" / "s" / "secrets.json"
    secrets_path.parent.mkdir(parents=True)
    secrets_path.write_text("{broken", encoding="utf-8")

    # When / Then: both operations raise SecretError and the file is unchanged
    with pytest.raises(SecretError):
        ldb_utils.write_or_update_secrets("s", None, str(tmp_path))
    with pytest.raises(SecretError):
        ldb_utils.encrypt_secrets_in_place("s", None, str(tmp_path))
    assert secrets_path.read_text(encoding="utf-8") == "{broken"


# ---------------------------------------------------------------------------
# Secret masking in logs
# ---------------------------------------------------------------------------


def test_secrets_are_masked_in_logged_config():
    # Spec: README > Secrets & Encryption — secret values are never printed to logs;
    # secret fields are replaced with "****" before any logging call
    # Given: a config dict containing a password and a non-secret field
    cfg = {"source_password": "secret", "other": 1}

    # When: the config is masked
    masked = ldb_run._mask_secrets(cfg)

    # Then: password is redacted; non-secret field is unchanged
    assert masked["source_password"] == "****"
    assert masked["other"] == 1
