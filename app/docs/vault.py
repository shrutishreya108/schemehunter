"""Encrypted document storage (LLD §12.4). AES-GCM; key from VAULT_KEY (32 bytes, base64)."""

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12


class VaultKeyError(ValueError):
    pass


def load_key(b64: str | None = None) -> bytes:
    if b64 is None:
        from app.config import get_settings

        b64 = get_settings().VAULT_KEY
    try:
        key = base64.b64decode(b64, validate=True)
    except ValueError as exc:
        raise VaultKeyError("VAULT_KEY is not valid base64") from exc
    if len(key) != 32:
        raise VaultKeyError("VAULT_KEY must decode to 32 bytes")
    return key


def encrypt(b: bytes, key: bytes | None = None) -> bytes:
    key = key or load_key()
    n = os.urandom(NONCE_BYTES)
    return n + AESGCM(key).encrypt(n, b, None)


def decrypt(blob: bytes, key: bytes | None = None) -> bytes:
    key = key or load_key()
    return AESGCM(key).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], None)


def path_for(case_id: str, doc_id: str, root: Path | None = None) -> Path:
    if root is None:
        from app.config import get_settings

        root = get_settings().VAULT_DIR
    return Path(root) / str(case_id) / f"{doc_id}.bin"
