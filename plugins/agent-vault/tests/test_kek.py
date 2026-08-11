"""Tests for the envelope KEK (seal/unseal + DPAPI/file wrap)."""

from __future__ import annotations

import base64
import importlib.util

import pytest

from agent_vault import kek

# AES-256-GCM seal/unseal needs the optional 'cryptography' dep; the DPAPI/KEK-store
# tests below do not. Skip only the crypto-dependent cases when it is absent (CI runs
# a minimal env), matching the persistent-cache tests.
_HAS_CRYPTO = importlib.util.find_spec("cryptography") is not None
_needs_crypto = pytest.mark.skipif(not _HAS_CRYPTO, reason="cryptography not installed")


@pytest.fixture(autouse=True)
def _isolated_kek_dir(tmp_path, monkeypatch):
    """Point the KEK store at a temp dir so tests never touch the real one."""
    monkeypatch.setenv("AGENT_VAULT_KEK_DIR", str(tmp_path / "keks"))
    yield


def test_wrap_unwrap_roundtrip():
    data = b"\x00\x01secret-key-material\xff"
    wrapped = kek.wrap_bytes(data)
    assert kek.is_wrapped(wrapped)
    assert wrapped != data
    assert kek.unwrap_bytes(wrapped) == data


def test_unwrap_rejects_non_wrapped():
    with pytest.raises(kek.KekError):
        kek.unwrap_bytes(b"not-a-wrapped-blob")


def test_kek_created_once_and_stable():
    k1 = kek.load_or_create_kek("spark")
    assert len(k1) == kek.KEK_BYTES
    assert kek.kek_exists("spark")
    k2 = kek.load_or_create_kek("spark")
    assert k1 == k2  # stable across calls
    # distinct names -> distinct keys
    assert kek.load_or_create_kek("other") != k1


@_needs_crypto
def test_seal_unseal_roundtrip():
    token = "aeyJ...a-token-like-value...zzz"
    sealed = kek.seal("spark", token.encode())
    # ciphertext is opaque base64, not the plaintext
    assert token not in sealed
    assert base64.b64decode(sealed)  # valid base64
    assert kek.unseal("spark", sealed) == token.encode()


@_needs_crypto
def test_seal_autocreates_kek():
    assert not kek.kek_exists("fresh")
    sealed = kek.seal("fresh", b"payload")
    assert kek.kek_exists("fresh")
    assert kek.unseal("fresh", sealed) == b"payload"


def test_unseal_missing_kek_fails_cleanly():
    with pytest.raises(kek.KekError):
        kek.unseal("never-created", base64.b64encode(b"AVK1whatever").decode())


@_needs_crypto
def test_unseal_tampered_blob_fails():
    sealed = kek.seal("spark", b"payload")
    raw = bytearray(base64.b64decode(sealed))
    raw[-1] ^= 0x01  # flip a ciphertext/tag bit
    tampered = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(kek.KekError):
        kek.unseal("spark", tampered)


@_needs_crypto
def test_unseal_wrong_kek_fails():
    sealed = kek.seal("spark", b"payload")
    # A different KEK name must not decrypt (also AAD-bound to the name).
    kek.load_or_create_kek("intruder")
    with pytest.raises(kek.KekError):
        kek.unseal("intruder", sealed)


def test_list_and_delete():
    kek.load_or_create_kek("a")
    kek.load_or_create_kek("b")
    assert set(kek.list_keks()) >= {"a", "b"}
    assert kek.delete_kek("a") is True
    assert kek.delete_kek("a") is False
    assert "a" not in kek.list_keks()


def test_invalid_name_rejected():
    for bad in ("", "../escape", "a/b", "."):
        with pytest.raises(kek.KekError):
            kek.load_or_create_kek(bad)
