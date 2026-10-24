"""Key vault encryption (SECURITY.md §4.2).

AES-256-GCM with a random 96-bit nonce per value. Ciphertexts are stored as
`<key_id>:<base64(nonce + ciphertext)>` so the master key can be rotated: new
values use the current key, old values stay readable while their key is
still configured.

Every value is bound to its purpose with associated data (for example
`provider_key:anthropic`), so a ciphertext copied into another row or column
fails to decrypt.
"""

from __future__ import annotations

import base64
import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12


class VaultError(Exception):
    """Raised when a value cannot be decrypted. Never includes the value."""


class Vault:
    def __init__(self, keys: dict[str, bytes], current_key_id: str) -> None:
        if current_key_id not in keys:
            raise ValueError("Current key ID is not among the configured keys")
        for key in keys.values():
            if len(key) != 32:
                raise ValueError("Vault keys must be 32 bytes")
        self._keys = dict(keys)
        self._current = current_key_id

    def encrypt(self, plaintext: str, purpose: str) -> str:
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(self._keys[self._current]).encrypt(
            nonce, plaintext.encode(), purpose.encode()
        )
        return f"{self._current}:{base64.b64encode(nonce + ciphertext).decode()}"

    def decrypt(self, token: str, purpose: str) -> str:
        key_id, sep, payload = token.partition(":")
        if not sep or key_id not in self._keys:
            raise VaultError("Unknown key ID or malformed value")
        try:
            raw = base64.b64decode(payload, validate=True)
            plaintext = AESGCM(self._keys[key_id]).decrypt(
                raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], purpose.encode()
            )
        except (InvalidTag, ValueError) as exc:
            raise VaultError("Value could not be decrypted") from exc
        return plaintext.decode()


def new_app_secret() -> str:
    """A new app signing secret: 256 bits, URL-safe."""
    return secrets.token_urlsafe(32)


def last4(value: str) -> str:
    return value[-4:] if len(value) >= 8 else "****"
