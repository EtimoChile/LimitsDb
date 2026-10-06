from __future__ import annotations
import base64, os, stat
from pathlib import Path
from typing import Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

LDB_DIR = Path(os.path.expanduser("~/.limitsdb"))
KEY_PATH = LDB_DIR / "ldb.key"

def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii")

def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s.encode("ascii"))

def _ensure_dir() -> None:
    LDB_DIR.mkdir(parents=True, exist_ok=True)

def _chmod600(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass

def _generate_aes256_key() -> bytes:
    return AESGCM.generate_key(256)

def load_or_create_key() -> bytes:
    env = os.getenv("LDB_MASTER_KEY")
    if env:
        if all(c in "0123456789abcdefABCDEF" for c in env) and len(env) == 64:
            return bytes.fromhex(env)
        try:
            k = base64.b64decode(env)
            if len(k) == 32:
                return k
        except Exception:
            pass
        if len(env) == 32:
            return env.encode("utf-8")
        raise ValueError("Invalid LDB_MASTER_KEY: use hex(64) / base64(32B) / raw(32B).")

    _ensure_dir()
    if KEY_PATH.exists():
        data = KEY_PATH.read_bytes()
        try:
            key = base64.b64decode(data)
        except Exception:
            key = data
        if len(key) != 32:
            raise ValueError("Invalid key at ~/.limitsdb/ldb.key (expected 32 bytes).")
        return key

    key = _generate_aes256_key()
    KEY_PATH.write_bytes(base64.b64encode(key))
    _chmod600(KEY_PATH)
    return key

def encrypt(plaintext: str, key: Optional[bytes] = None) -> str:
    key = key or load_or_create_key()
    aes = AESGCM(key)
    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    return f"enc:v1:aes256gcm:{_b64e(nonce)}:{_b64e(ct)}"

def is_encrypted(val: str) -> bool:
    return val.startswith("enc:v1:aes256gcm:")

def decrypt(token: str, key: Optional[bytes] = None) -> str:
    if not is_encrypted(token):
        raise ValueError("Attempted to decrypt a non-encrypted token.")
    key = key or load_or_create_key()
    _, _, _, nonce_b64, ct_b64 = token.split(":", 4)
    aes = AESGCM(key)
    pt = aes.decrypt(_b64d(nonce_b64), _b64d(ct_b64), associated_data=None)
    return pt.decode("utf-8")
