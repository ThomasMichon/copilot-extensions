"""Native Windows GCM known-answer and cross-backend identity-token tests."""

from __future__ import annotations

import os

import pytest

from agent_worktrees.identity_crypto_windows import IdentityCryptoError, WindowsAESGCM

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows CNG")


def test_aes256_gcm_known_answer_and_authentication():
    cipher = WindowsAESGCM(bytes(32))
    nonce = bytes(12)
    # NIST AES-256-GCM, empty AAD, one zero block.
    expected = bytes.fromhex(
        "cea7403d4d606b6e074ec5d3baf39d18d0d1c8a799996bf0265b98b5d48ab919"
    )
    assert cipher.encrypt(nonce, bytes(16), None) == expected
    assert cipher.decrypt(nonce, expected, None) == bytes(16)
    with pytest.raises(IdentityCryptoError):
        cipher.decrypt(nonce, expected[:-1] + bytes([expected[-1] ^ 1]), None)


@pytest.mark.parametrize("data", [b"", b"private-origin", bytes(range(255))])
def test_nonce_aad_and_roundtrip(data):
    cipher = WindowsAESGCM(b"k" * 32)
    token = cipher.encrypt(b"n" * 12, data, b"context")
    assert cipher.decrypt(b"n" * 12, token, b"context") == data
    with pytest.raises(IdentityCryptoError):
        cipher.decrypt(b"n" * 12, token, b"different")


def test_portable_backend_compatibility():
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key, nonce, data = b"k" * 32, b"n" * 12, b"origin-and-producer"
    native = WindowsAESGCM(key)
    portable = AESGCM(key)
    assert native.encrypt(nonce, data, None) == portable.encrypt(nonce, data, None)
    assert native.decrypt(nonce, portable.encrypt(nonce, data, None), None) == data
