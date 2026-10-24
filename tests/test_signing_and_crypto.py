import base64
import os

import pytest

from app.core import signing
from app.core.crypto import Vault, VaultError

SECRET = b"s" * 32


def test_signature_roundtrip_and_tamper_detection() -> None:
    sig = signing.sign(SECRET, "POST", "/v1/tasks", "1700000000", "rq_12345678", b'{"a":1}')
    assert signing.verify(SECRET, sig, "POST", "/v1/tasks", "1700000000", "rq_12345678", b'{"a":1}')
    assert not signing.verify(
        SECRET, sig, "POST", "/v1/tasks", "1700000000", "rq_12345678", b'{"a":2}'
    )
    assert not signing.verify(
        SECRET, sig, "POST", "/v1/tenants", "1700000000", "rq_12345678", b'{"a":1}'
    )
    assert not signing.verify(
        b"x" * 32, sig, "POST", "/v1/tasks", "1700000000", "rq_12345678", b'{"a":1}'
    )


@pytest.mark.parametrize(
    ("timestamp", "fresh"),
    [
        ("1000", True),
        ("1300", True),
        ("1301", False),
        ("699", False),
        ("abc", False),
        ("-5", False),
    ],
)
def test_timestamp_window(timestamp: str, fresh: bool) -> None:
    assert signing.timestamp_is_fresh(timestamp, now=1000, max_skew_seconds=300) is fresh


def _vault() -> Vault:
    return Vault({"k1": os.urandom(32)}, "k1")


def test_vault_roundtrip_uses_fresh_nonce() -> None:
    vault = _vault()
    a = vault.encrypt("sk-test-value", "provider_key:anthropic")
    b = vault.encrypt("sk-test-value", "provider_key:anthropic")
    assert a != b
    assert a.startswith("k1:")
    assert vault.decrypt(a, "provider_key:anthropic") == "sk-test-value"


def test_vault_rejects_wrong_purpose_and_tampering() -> None:
    vault = _vault()
    token = vault.encrypt("secret", "app_secret:sermonpost")
    with pytest.raises(VaultError):
        vault.decrypt(token, "app_secret:mediateam")  # copied to another row
    key_id, payload = token.split(":")
    raw = bytearray(base64.b64decode(payload))
    raw[-1] ^= 1
    with pytest.raises(VaultError):
        vault.decrypt(f"{key_id}:{base64.b64encode(bytes(raw)).decode()}", "app_secret:sermonpost")
    with pytest.raises(VaultError):
        vault.decrypt("k9:" + payload, "app_secret:sermonpost")


def test_vault_supports_key_rotation() -> None:
    old, new = os.urandom(32), os.urandom(32)
    token = Vault({"k1": old}, "k1").encrypt("value", "p")
    rotated = Vault({"k1": old, "k2": new}, "k2")
    assert rotated.decrypt(token, "p") == "value"
    assert rotated.encrypt("value", "p").startswith("k2:")
