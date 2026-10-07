"""Tests for the encrypted identity marker (effort
``pr-attribution-codenames``, encrypted-identity-marker slice): key
resolution/generation, the AES-256-GCM encrypt/decrypt round trip, and the
standalone CLI.
"""

from __future__ import annotations

import base64
import importlib.util
import json

import pytest

from agent_worktrees import identity_marker

# AES-256-GCM needs the optional 'cryptography' dep -- the key-resolution /
# path-only tests below don't, so only the encrypt/decrypt round trip is
# skipped without it (same pattern as agent-vault's test_kek.py).
_HAS_CRYPTO = importlib.util.find_spec("cryptography") is not None
_needs_crypto = pytest.mark.skipif(not _HAS_CRYPTO, reason="cryptography not installed")


class TestKeyResolution:
    def test_env_var_override_takes_precedence(self, tmp_path, monkeypatch):
        onedrive = tmp_path / "onedrive"
        onedrive.mkdir()
        monkeypatch.setenv("OneDrive", str(onedrive))
        explicit = tmp_path / "explicit.key"
        monkeypatch.setenv(identity_marker.KEY_ENV_VAR, str(explicit))
        assert identity_marker.identity_key_path() == explicit

    def test_default_resolves_under_onedrive_root(self, tmp_path, monkeypatch):
        monkeypatch.delenv(identity_marker.KEY_ENV_VAR, raising=False)
        onedrive = tmp_path / "onedrive"
        onedrive.mkdir()
        monkeypatch.setenv("OneDrive", str(onedrive))
        path = identity_marker.identity_key_path()
        assert path == onedrive / identity_marker.DEFAULT_KEY_SUBDIR / identity_marker.KEY_FILENAME

    def test_no_key_path_when_no_onedrive_root(self, tmp_path, monkeypatch):
        monkeypatch.delenv(identity_marker.KEY_ENV_VAR, raising=False)
        for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(identity_marker.Path, "home", classmethod(lambda cls: tmp_path))
        assert identity_marker.default_identity_key_path() is None
        assert identity_marker.load_identity_key() is None

    def test_load_identity_key_missing_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv(identity_marker.KEY_ENV_VAR, str(tmp_path / "missing.key"))
        assert identity_marker.load_identity_key() is None

    def test_load_identity_key_wrong_length_returns_none(self, tmp_path, monkeypatch):
        bad = tmp_path / "bad.key"
        bad.write_text(base64.b64encode(b"too-short").decode(), encoding="utf-8")
        monkeypatch.setenv(identity_marker.KEY_ENV_VAR, str(bad))
        assert identity_marker.load_identity_key() is None

    def test_load_identity_key_bad_base64_returns_none(self, tmp_path, monkeypatch):
        bad = tmp_path / "bad.key"
        bad.write_text("not-valid-base64!!!", encoding="utf-8")
        monkeypatch.setenv(identity_marker.KEY_ENV_VAR, str(bad))
        assert identity_marker.load_identity_key() is None


class TestGenerateIdentityKey:
    def test_generates_a_valid_key(self, tmp_path, monkeypatch):
        target = tmp_path / "nested" / "identity.key"
        created = identity_marker.generate_identity_key(target)
        assert created == target
        monkeypatch.setenv(identity_marker.KEY_ENV_VAR, str(target))
        key = identity_marker.load_identity_key()
        assert key is not None
        assert len(key) == identity_marker.KEY_BYTES

    def test_refuses_to_overwrite_without_force(self, tmp_path):
        target = tmp_path / "identity.key"
        identity_marker.generate_identity_key(target)
        with pytest.raises(identity_marker.IdentityMarkerError):
            identity_marker.generate_identity_key(target)

    def test_force_overwrites(self, tmp_path):
        target = tmp_path / "identity.key"
        identity_marker.generate_identity_key(target)
        original = target.read_text(encoding="utf-8")
        identity_marker.generate_identity_key(target, force=True)
        assert target.read_text(encoding="utf-8") != original

    def test_raises_when_no_path_resolvable(self, monkeypatch):
        monkeypatch.delenv(identity_marker.KEY_ENV_VAR, raising=False)
        monkeypatch.setattr(identity_marker, "default_identity_key_path", lambda: None)
        with pytest.raises(identity_marker.IdentityMarkerError):
            identity_marker.generate_identity_key()


class TestBuildIdentityPayload:
    def test_only_required_fields_present_by_default(self):
        payload = identity_marker.build_identity_payload(worktree_id="wt-1")
        assert payload["worktree_id"] == "wt-1"
        assert payload["v"] == identity_marker.PAYLOAD_VERSION
        assert "ts" in payload
        assert "machine" not in payload
        assert "session" not in payload
        assert "head" not in payload
        assert "project" not in payload

    def test_optional_fields_included_when_given(self):
        payload = identity_marker.build_identity_payload(
            worktree_id="wt-1", machine="m1", session="s1", head="abc123", project="repo",
        )
        assert payload["machine"] == "m1"
        assert payload["session"] == "s1"
        assert payload["head"] == "abc123"
        assert payload["project"] == "repo"


@_needs_crypto
class TestEncryptDecryptRoundTrip:
    @pytest.fixture
    def key(self):
        return b"\x42" * identity_marker.KEY_BYTES

    def test_encrypt_without_a_configured_key_returns_none(self, key, monkeypatch):
        # encrypt_identity_payload resolves its own key via
        # load_identity_key; with no key configured in this process, it
        # must degrade to None rather than raise.
        monkeypatch.setattr(identity_marker, "load_identity_key", lambda: None)
        payload = identity_marker.build_identity_payload(worktree_id="wt-1")
        assert identity_marker.encrypt_identity_payload(payload) is None

    def test_encrypt_then_decrypt_with_explicit_key(self, key, monkeypatch):
        monkeypatch.setattr(identity_marker, "load_identity_key", lambda: key)
        payload = identity_marker.build_identity_payload(worktree_id="wt-1", machine="m1")
        token = identity_marker.encrypt_identity_payload(payload)
        assert token is not None
        decoded = identity_marker.decrypt_identity_payload(token, key=key)
        assert decoded["worktree_id"] == "wt-1"
        assert decoded["machine"] == "m1"

    def test_decrypt_wrong_key_raises(self, key):
        # Build the token directly against `key`, then decrypt with a
        # different key -- must raise, not silently return garbage.
        payload = identity_marker.build_identity_payload(worktree_id="wt-1")
        plaintext = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        import secrets

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = secrets.token_bytes(identity_marker.NONCE_BYTES)
        ct = AESGCM(key).encrypt(nonce, plaintext, None)
        token = base64.b64encode(identity_marker.MARKER_MAGIC + nonce + ct).decode()
        wrong_key = b"\x99" * identity_marker.KEY_BYTES
        with pytest.raises(identity_marker.IdentityMarkerError):
            identity_marker.decrypt_identity_payload(token, key=wrong_key)

    def test_decrypt_no_key_configured_raises(self, monkeypatch):
        monkeypatch.setattr(identity_marker, "load_identity_key", lambda: None)
        with pytest.raises(identity_marker.IdentityMarkerError):
            identity_marker.decrypt_identity_payload("anything")

    def test_decrypt_malformed_base64_raises(self, key):
        with pytest.raises(identity_marker.IdentityMarkerError):
            identity_marker.decrypt_identity_payload("not-base64!!!", key=key)

    def test_decrypt_bad_magic_raises(self, key):
        token = base64.b64encode(b"XXXX" + b"\x00" * 20).decode()
        with pytest.raises(identity_marker.IdentityMarkerError):
            identity_marker.decrypt_identity_payload(token, key=key)

    def test_identity_marker_field_round_trip(self, key, monkeypatch):
        monkeypatch.setattr(identity_marker, "load_identity_key", lambda: key)
        token = identity_marker.identity_marker_field(
            worktree_id="wt-1", machine="m1", project="repo",
        )
        assert token is not None
        decoded = identity_marker.decrypt_identity_payload(token, key=key)
        assert decoded["worktree_id"] == "wt-1"
        assert decoded["project"] == "repo"


class TestIdentityMarkerFieldForRecord:
    def test_omits_when_no_key(self, monkeypatch):
        import types

        monkeypatch.setattr(identity_marker, "load_identity_key", lambda: None)
        record = types.SimpleNamespace(worktree_id="wt-1", machine="m1", sessions=None)
        assert identity_marker.identity_marker_field_for_record(record) is None

    def test_never_raises_on_bad_record(self):
        assert identity_marker.identity_marker_field_for_record(object()) is None  # type: ignore[arg-type]

    @_needs_crypto
    def test_uses_live_session_over_ended_one(self, monkeypatch):
        import types

        key = b"\x07" * identity_marker.KEY_BYTES
        monkeypatch.setattr(identity_marker, "load_identity_key", lambda: key)
        record = types.SimpleNamespace(
            worktree_id="wt-1",
            machine="m1",
            sessions=[
                types.SimpleNamespace(session_id="ended", ended_at="2020-01-01"),
                types.SimpleNamespace(session_id="live", ended_at=None),
            ],
        )
        token = identity_marker.identity_marker_field_for_record(record, project="repo")
        decoded = identity_marker.decrypt_identity_payload(token, key=key)
        assert decoded["session"] == "live"


class TestCli:
    def test_generate_writes_a_valid_key_file(self, tmp_path):
        key_path = tmp_path / "identity.key"
        rc = identity_marker._cli(["generate", "--path", str(key_path)])
        assert rc == 0
        assert key_path.is_file()

    def test_generate_refuses_overwrite_without_force(self, tmp_path, capsys):
        key_path = tmp_path / "identity.key"
        identity_marker.generate_identity_key(key_path)
        rc = identity_marker._cli(["generate", "--path", str(key_path)])
        assert rc == 1
        assert "error" in capsys.readouterr().out

    @_needs_crypto
    def test_decode_round_trips_a_real_token(self, tmp_path, monkeypatch, capsys):
        key_path = tmp_path / "identity.key"
        identity_marker._cli(["generate", "--path", str(key_path)])
        capsys.readouterr()  # discard the "identity key written to ..." line
        monkeypatch.setenv(identity_marker.KEY_ENV_VAR, str(key_path))
        token = identity_marker.identity_marker_field(worktree_id="wt-1", machine="m1")
        assert token is not None

        rc = identity_marker._cli(["decode", token])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["worktree_id"] == "wt-1"
        assert out["machine"] == "m1"

    def test_decode_invalid_token_reports_error(self, tmp_path, monkeypatch, capsys):
        key_path = tmp_path / "identity.key"
        identity_marker.generate_identity_key(key_path)
        monkeypatch.setenv(identity_marker.KEY_ENV_VAR, str(key_path))
        rc = identity_marker._cli(["decode", "not-a-real-token"])
        assert rc == 1
        assert "error" in capsys.readouterr().out
