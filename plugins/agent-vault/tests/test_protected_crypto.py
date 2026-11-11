"""Independent format vectors and preservation across native crypto backends."""

from __future__ import annotations

import base64
import builtins
import json
from datetime import datetime

import pytest

from agent_vault import cache, kek, protected_crypto
from agent_vault.protected_crypto import AesGcm, Fernet, InvalidToken


def test_fernet_specification_vector(monkeypatch):
    # Public interoperability vector: https://github.com/fernet/spec/blob/master/generate.json
    key = b"cw_0x689RpI-jtRR7oE8h_eQsKImvJapLeSbXpwF4e4="
    token = (
        b"gAAAAAAdwJ6wAAECAwQFBgcICQoLDA0ODy021cpGVWKZ_eEwCGM4"
        b"BLLF_5CV9dOPmrhuVUPgJobwOz7JcbmrR64jVmpU4IwqDA=="
    )
    monkeypatch.setattr(
        protected_crypto.time, "time",
        lambda: datetime.fromisoformat("1985-10-26T01:20:00-07:00").timestamp(),
    )
    monkeypatch.setattr(protected_crypto.secrets, "token_bytes", lambda _size: bytes(range(16)))
    assert Fernet(key).decrypt(token) == b"hello"
    assert Fernet(key).encrypt(b"hello") == token


def test_nist_aes256_gcm_vector():
    # NIST AES-GCM zero-key/IV, 128-bit plaintext, no associated data.
    expected = bytes.fromhex(
        "cea7403d4d606b6e074ec5d3baf39d18d0d1c8a799996bf0265b98b5d48ab919",
    )
    cipher = AesGcm(bytes(32))
    assert cipher.encrypt(bytes(12), bytes(16), b"") == expected
    assert cipher.decrypt(bytes(12), expected, b"") == bytes(16)
    with pytest.raises(ValueError):
        cipher.decrypt(bytes(12), expected, b"different-name")


@pytest.mark.parametrize("size", [0, 1, 16, 17, 4096])
def test_fernet_padding_and_wrong_key_refusal(size):
    value = b"x" * size
    key = Fernet.generate_key()
    cipher = Fernet(key)
    token = cipher.encrypt(value)
    assert cipher.decrypt(token) == value
    with pytest.raises(InvalidToken):
        Fernet(Fernet.generate_key()).decrypt(token)


def test_fernet_previous_backend_interoperability():
    PreviousFernet = pytest.importorskip("cryptography.fernet").Fernet
    value = b"previous-backend fixture"
    key = Fernet.generate_key()
    cipher = Fernet(key)
    token = cipher.encrypt(value)
    assert PreviousFernet(key).decrypt(token) == value
    assert cipher.decrypt(PreviousFernet(key).encrypt(value)) == value


@pytest.mark.parametrize("offset", [0, 1, 9, 25, -1])
def test_fernet_authenticates_every_wire_field(offset):
    cipher = Fernet(Fernet.generate_key())
    raw = bytearray(base64.urlsafe_b64decode(cipher.encrypt(b"fixture-value")))
    raw[offset] ^= 1
    with pytest.raises(InvalidToken):
        cipher.decrypt(base64.urlsafe_b64encode(raw))


@pytest.mark.parametrize("token", [b"", b"?", b"gA==", b"\xff", None, 1])
def test_malformed_fernet_token_refused(token):
    with pytest.raises(InvalidToken):
        Fernet(Fernet.generate_key()).decrypt(token)


def test_gcm_cross_backend_and_old_keys_preserved(monkeypatch, tmp_path):
    previous = pytest.importorskip("cryptography.hazmat.primitives.ciphers.aead")
    monkeypatch.setenv("AGENT_VAULT_KEK_DIR", str(tmp_path))
    key = kek.load_or_create_kek("fixture")
    path = tmp_path / "fixture.kek"
    before = path.read_bytes(), path.stat().st_mtime_ns
    nonce = bytes(range(12))
    old = kek.SEAL_MAGIC + nonce + previous.AESGCM(key).encrypt(nonce, b"old-value", b"fixture")
    assert kek.unseal("fixture", base64.b64encode(old).decode("ascii")) == b"old-value"
    new = base64.b64decode(kek.seal("fixture", b"new-value"))
    assert previous.AESGCM(key).decrypt(new[4:16], new[16:], b"fixture") == b"new-value"
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_legacy_cache_and_kek_reopen_without_cryptography(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_VAULT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("AGENT_VAULT_KEK_DIR", str(tmp_path / "keks"))
    persistent = cache.PersistentCache()
    persistent._base_dir.mkdir()
    key = Fernet.generate_key()
    persistent._key_file.write_bytes(key)  # Existing unwrapped, pre-migration key.
    persistent._cache_file.write_bytes(Fernet(key).encrypt(json.dumps({
        "v": 1, "entries": {"SSH/node-a": {"password": {"value": "fixture-value"}}},
    }).encode()))
    before = persistent._key_file.read_bytes(), persistent._cache_file.read_bytes()
    sealed = kek.seal("fixture", b"sealed-value")
    kek_path = tmp_path / "keks" / "fixture.kek"
    before_kek = kek_path.read_bytes(), kek_path.stat().st_mtime_ns
    original = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("cryptography"):
            raise ImportError("previous backend unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    for _ in range(2):
        assert cache.PersistentCache().get("SSH/node-a", readonly=True) == "fixture-value"
        assert kek.unseal("fixture", sealed) == b"sealed-value"
        assert (persistent._key_file.read_bytes(), persistent._cache_file.read_bytes()) == before
        assert (kek_path.read_bytes(), kek_path.stat().st_mtime_ns) == before_kek
