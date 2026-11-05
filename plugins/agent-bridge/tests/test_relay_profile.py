"""Tests for the #892 Increment 2 credential-relay process boundary.

The safety-critical piece is ``FileTokenValidator``: agent-bridge applies a
provider's ``get-azure-token`` gate over a process boundary by reading the same
host-side token file. If it diverged from the providers' in-process validators
the gate could be *silently* weakened (a failure the fallback-to-import pattern
would NOT catch), so ``test_file_token_validator_equivalent_to_reference`` pins
it against the providers' EXACT logic across a token matrix.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from credential_relay.server import ScopeDenied

from agent_bridge.agent_registry import (
    FileTokenAuthorizer,
    FileTokenValidator,
    _apply_relay_profile,
    _register_provider_relay,
    _relay_profile_via_cli,
)


def _reference_validator(path):
    """The providers' EXACT in-process validator logic (copied verbatim from
    agent_codespaces.relay_token.validate / agent_containers.relay_provider._validate):
    empty -> False; else any(compare_digest(token, t) for stored values)."""
    def _validate(token: str) -> bool:
        if not token:
            return False
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            return False
        return any(secrets.compare_digest(token, t) for t in data.values())
    return _validate


def _write_store(path: Path, tokens: dict) -> None:
    path.write_text(json.dumps(tokens), encoding="utf-8")


# --- GOLDEN equivalence: FileTokenValidator == providers' validator ----------

def test_file_token_validator_equivalent_to_reference(tmp_path):
    tok_a = secrets.token_hex(32)
    tok_b = secrets.token_hex(32)
    store = tmp_path / "relay-tokens.json"
    _write_store(store, {"cs-a": tok_a, "cs-b": tok_b})

    fv = FileTokenValidator(store)
    ref = _reference_validator(store)

    # A well-formed store + a comprehensive ASCII token matrix (the real token
    # space -- tokens are hex): the two must agree on EVERY input (the golden
    # gate-equivalence guarantee).
    candidates = [
        tok_a, tok_b,                       # valid
        "", " ", "\n",                      # empty / whitespace
        "wrong-token",                      # unknown
        tok_a[:-1], tok_a + "x", tok_a[:8], # substring / superstring / prefix
        tok_a.upper(), tok_b.lower(),       # case variants
    ]
    for tok in candidates:
        assert fv(tok) is ref(tok), f"divergence on {tok!r}"
    # And the positives really are accepted (not vacuously equal on all-False).
    assert fv(tok_a) is True and fv(tok_b) is True
    assert fv("wrong-token") is False


def test_file_token_validator_rejects_non_ascii_without_raising(tmp_path):
    # A non-ASCII token can't match an (ASCII hex) secret. compare_digest raises
    # on non-ASCII, so the providers' validators would raise (rejecting); this
    # rejects too, without raising -- a hardening, never a weakening.
    store = tmp_path / "relay-tokens.json"
    _write_store(store, {"cs": secrets.token_hex(16)})
    assert FileTokenValidator(store)("café-π-🔑") is False


def test_file_token_validator_empty_store(tmp_path):
    store = tmp_path / "relay-tokens.json"
    _write_store(store, {})
    fv = FileTokenValidator(store)
    assert fv("anything") is False


def test_file_token_validator_missing_and_malformed_store(tmp_path):
    fv_missing = FileTokenValidator(tmp_path / "nope.json")
    assert fv_missing("x") is False
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert FileTokenValidator(bad)("x") is False


@pytest.mark.parametrize("transient", [False, True])
def test_file_token_read_contention_preserves_scope_gate(tmp_path, monkeypatch, transient):
    store = tmp_path / "relay-tokens.json"
    _write_store(store, {"cs": {"token": "synthetic", "allowed_resources": ["https://example.com"]}})
    read_text = Path.read_text
    failures = []

    def contended(path, *args, **kwargs):
        if path == store and (not transient or not failures):
            error = PermissionError("synthetic sharing violation")
            error.winerror = 32
            failures.append(True)
            raise error
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", contended)
    authorizer = FileTokenAuthorizer(store)
    assert FileTokenValidator(store)("synthetic") is transient
    if transient:
        failures.clear()
        assert authorizer("synthetic", "get-azure-token", {"resource": "https://example.com"})
        with pytest.raises(ScopeDenied):
            authorizer("synthetic", "get-azure-token", {"resource": "https://denied.example"})
    else:
        assert not authorizer("synthetic", "get-azure-token", {"resource": "https://example.com"})
    assert store.read_bytes()


def test_codespaces_issuer_store_is_consumed_and_revoked_by_bridge(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2] / "agent-codespaces" / "src"
    monkeypatch.syspath_prepend(str(source))
    from agent_codespaces import relay_token

    store = tmp_path / "relay-tokens.json"
    monkeypatch.setattr(relay_token, "_TOKENS_FILE", store)
    token = relay_token.token_for("cs", allowed_resources=["https://example.com"])
    validator = FileTokenValidator(store)
    authorizer = FileTokenAuthorizer(store)
    assert validator(token)
    assert authorizer(token, "get-azure-token", {"resource": "https://example.com"})
    with pytest.raises(ScopeDenied):
        authorizer(token, "get-azure-token", {"resource": "https://denied.example"})
    relay_token.revoke("cs")
    assert not validator(token)
    assert not authorizer(token, "get-azure-token", {"resource": "https://example.com"})


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["validator", "authorizer"])
async def test_file_contention_does_not_block_relay_ping(tmp_path, monkeypatch, gate):
    from credential_relay import token_store
    from credential_relay.server import CredentialRelayServer

    store = tmp_path / "relay-tokens.json"
    _write_store(store, {"cs": {"token": "synthetic", "allowed_resources": ["https://example.com"]}})
    read_text = Path.read_text
    entered, release = threading.Event(), threading.Event()
    failures = []

    def contended(path, *args, **kwargs):
        if path == store and not failures:
            failures.append(True)
            error = PermissionError("synthetic sharing violation")
            error.winerror = 32
            raise error
        return read_text(path, *args, **kwargs)

    def wait_for_release(_delay):
        entered.set()
        release.wait(timeout=2)

    def request(data):
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        writer = MagicMock()
        writer.get_extra_info.return_value = ("synthetic", 0)
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock()
        return reader, writer

    monkeypatch.setattr(Path, "read_text", contended)
    monkeypatch.setattr(token_store, "time", SimpleNamespace(sleep=wait_for_release))
    server = CredentialRelayServer(
        sources=[], token_required_actions=frozenset({"get-azure-token"}),
        token_validator=FileTokenValidator(store) if gate == "validator" else None,
        token_authorizer=FileTokenAuthorizer(store) if gate == "authorizer" else None,
    )
    pending = asyncio.create_task(server._handle_client(*request(
        b"get-azure-token\nauth=synthetic\nresource=https://example.com\n\n",
    )))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        ping_reader, ping_writer = request(b"ping\n\n")
        await asyncio.wait_for(server._handle_client(ping_reader, ping_writer), timeout=0.5)
        ping_writer.write.assert_called_once_with(b"pong\n\n")
        assert not pending.done(), "authorization must still be held while the ping completes"
    finally:
        release.set()
        await asyncio.wait_for(pending, timeout=3)


def test_file_token_validator_defensive_on_non_str_values(tmp_path):
    # A malformed store with non-string values must not raise -- return False
    # (a hardening over the providers, never a weakening: no real token matches).
    store = tmp_path / "relay-tokens.json"
    store.write_text(json.dumps({"a": 123, "b": None, "c": ["x"]}), encoding="utf-8")
    assert FileTokenValidator(store)("x") is False


# --- _apply_relay_profile ----------------------------------------------------

class _FakeBuilder:
    def __init__(self):
        self.sources = []
        self.port = None
        self.ado_host = None
        self.azure = None
        self.gated = None
        self.validator = None
        self.authorizer = None

    def add_source(self, s):
        self.sources.append(getattr(s, "name", type(s).__name__))

    def set_port(self, p):
        if p is not None:
            self.port = p

    def set_ado_host(self, h):
        if h:
            self.ado_host = h

    def allow_azure_resources(self, r):
        self.azure = list(r)

    def require_token(self, actions, validator):
        self.gated = list(actions)
        self.validator = validator

    def authorize_token(self, actions, authorizer):
        self.gated = list(actions)
        self.authorizer = authorizer


def test_apply_relay_profile_codespace_shape(tmp_path):
    store = tmp_path / "relay-tokens.json"
    _write_store(store, {"cs": "TOK"})
    b = _FakeBuilder()
    _apply_relay_profile(b, {
        "sources": ["git-credential"],
        "port": 50123,
        "ado_host": "https://ado.example",
        "azure_resources": ["499b84ac", "https://storage.azure.com/"],
        "gated_actions": ["get-azure-token"],
        "token_store": str(store),
    })
    assert "git-credential" in b.sources
    assert b.port == 50123 and b.ado_host == "https://ado.example"
    assert b.azure == ["499b84ac", "https://storage.azure.com/"]
    assert b.gated == ["get-azure-token"]
    # The applied validator is file-backed + accepts the stored token.
    assert isinstance(b.validator, FileTokenValidator)
    assert b.validator("TOK") is True and b.validator("nope") is False


def test_apply_relay_profile_container_two_sources(tmp_path):
    store = tmp_path / "ctokens.json"
    _write_store(store, {"ctr": "T"})
    b = _FakeBuilder()
    _apply_relay_profile(b, {
        "sources": ["git-credential", "gh-auth"],
        "port": None, "ado_host": None,
        "azure_resources": ["*"],
        "gated_actions": ["get-azure-token"],
        "token_store": str(store),
    })
    assert set(b.sources) >= {"git-credential"}  # gh-auth name may differ
    assert len(b.sources) == 2
    assert b.port is None and b.azure == ["*"]


def test_apply_relay_profile_passes_github_profile_to_gh_auth():
    b = _FakeBuilder()
    with patch("credential_relay.sources.gh_auth.GhAuthSource") as gh_source:
        gh_source.return_value.name = "gh-auth"
        _apply_relay_profile(b, {
            "sources": ["gh-auth"],
            "github_hosts": ["github.com"],
            "azure_resources": [],
        })

    assert b.sources == ["gh-auth"]
    gh_source.assert_called_once_with(github_hosts=["github.com"])


def test_apply_relay_profile_passes_github_profile_to_git_credential():
    b = _FakeBuilder()
    with patch("credential_relay.sources.git_credential.GitCredentialSource") as git_source:
        git_source.return_value.name = "git-credential"
        _apply_relay_profile(b, {
            "sources": ["git-credential"],
            "github_hosts": ["github.com"],
            "azure_resources": [],
        })

    assert b.sources == ["git-credential"]
    git_source.assert_called_once_with(github_hosts=["github.com"])


def test_apply_relay_profile_skips_unknown_source():
    b = _FakeBuilder()
    _apply_relay_profile(b, {"sources": ["bogus"], "azure_resources": []})
    assert b.sources == []


def test_file_token_validator_tolerates_structured_entries(tmp_path):
    # A store written by the scoped token_for carries structured entries; the
    # validator must read the ``token`` field (not skip the dict, which would
    # silently deny every request -- the version-skew hazard).
    store = tmp_path / "relay-tokens.json"
    _write_store(store, {
        "cs-a": {"token": "TOKA", "repository": "o/r", "allowed_resources": []},
        "cs-legacy": "TOKB",
    })
    fv = FileTokenValidator(store)
    assert fv("TOKA") is True   # structured entry's secret
    assert fv("TOKB") is True   # legacy string entry
    assert fv("nope") is False


def test_apply_relay_profile_scoped_azure_uses_authorizer(tmp_path):
    ado = "499b84ac-1321-427f-aa17-267ca6975798"
    store = tmp_path / "relay-tokens.json"
    _write_store(store, {
        "cs": {"token": "TOK", "repository": "o/r", "allowed_resources": [ado]},
    })
    b = _FakeBuilder()
    _apply_relay_profile(b, {
        "sources": ["git-credential"],
        "port": 50123,
        "ado_host": None,
        "azure_resources": [ado, "https://storage.azure.com/"],
        "gated_actions": ["get-azure-token"],
        "token_store": str(store),
        "scoped_azure": True,
    })
    # The scoped profile applies the request-scoped authorizer, not a validator.
    assert b.validator is None
    assert isinstance(b.authorizer, FileTokenAuthorizer)
    assert b.authorizer("TOK", "get-azure-token", {"scope": ado}) is True
    # A resource outside the token's allowlist is denied even for a valid
    # token -- raising ScopeDenied (#4367), not a plain False.
    with pytest.raises(ScopeDenied):
        b.authorizer("TOK", "get-azure-token", {"scope": "https://graph.microsoft.com/"})
    assert b.authorizer("nope", "get-azure-token", {"scope": ado}) is False


def test_file_token_authorizer_enforces_per_token_scope(tmp_path):
    ado = "499b84ac-1321-427f-aa17-267ca6975798"
    store = tmp_path / "relay-tokens.json"
    _write_store(store, {
        "cs": {"token": "TOK", "repository": "o/r",
               "allowed_resources": [ado, "https://storage.azure.com/"]},
    })
    fa = FileTokenAuthorizer(store)
    # In-allowlist resources authorize, with /.default normalization; others don't.
    assert fa("TOK", "get-azure-token", {"scope": ado + "/.default"}) is True
    assert fa("TOK", "get-azure-token",
              {"resource": "https://storage.azure.com/"}) is True
    # #4367: a recognized token whose specific scope is denied raises
    # ScopeDenied (a wire-visible denial), not a plain False.
    with pytest.raises(ScopeDenied):
        fa("TOK", "get-azure-token", {"scope": "https://graph.microsoft.com/.default"})
    # Non-Azure actions and unknown tokens are never authorized here (and
    # never raise: an unrecognized token stays fully silent over the wire).
    assert fa("TOK", "get-github-token", {}) is False
    assert fa("wrong", "get-azure-token", {"scope": ado}) is False


def test_file_token_authorizer_legacy_entry_falls_back_to_static(tmp_path):
    ado = "499b84ac-1321-427f-aa17-267ca6975798"
    store = tmp_path / "relay-tokens.json"
    # A legacy string entry carries no per-token allowlist; the authorizer falls
    # back to the profile's static allowlist so a pre-scoping token still works.
    _write_store(store, {"cs-legacy": "LTOK"})
    fa = FileTokenAuthorizer(store, [ado])
    assert fa("LTOK", "get-azure-token", {"scope": ado}) is True
    with pytest.raises(ScopeDenied):
        fa("LTOK", "get-azure-token", {"scope": "https://graph.microsoft.com/"})


@pytest.mark.asyncio
async def test_cli_profile_scope_denial_is_wire_visible_end_to_end(tmp_path):
    """#4367: the real CLI-profile path (``_apply_relay_profile`` +
    ``FileTokenAuthorizer``, exactly what a running bridge installs for a
    CodeSpace discovered via ``agent-codespaces relay-profile``) must reach a
    live server round trip that states a known-token/denied-scope response
    explicitly, while an unknown token stays fully silent."""
    from credential_relay import RelayBuilder
    from credential_relay.server import ACCESS_DENIED_RESPONSE

    ado = "499b84ac-1321-427f-aa17-267ca6975798"
    store = tmp_path / "relay-tokens.json"
    _write_store(store, {
        "cs": {"token": "TOK", "repository": "o/r", "allowed_resources": [ado]},
    })
    b = RelayBuilder()
    _apply_relay_profile(b, {
        "sources": ["git-credential"],
        "port": 0,
        "ado_host": None,
        "azure_resources": [ado],
        "gated_actions": ["get-azure-token"],
        "token_store": str(store),
        "scoped_azure": True,
    })
    srv = b.build()
    await srv.start()
    srv.port = srv._server.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", srv.port,
        )
        writer.write(
            b"get-azure-token\nauth=TOK\nscope=https://graph.microsoft.com/\n\n",
        )
        await writer.drain()
        data = await reader.read(4096)
        writer.close()
        assert data.decode() == ACCESS_DENIED_RESPONSE

        reader, writer = await asyncio.open_connection(
            "127.0.0.1", srv.port,
        )
        writer.write(f"get-azure-token\nauth=nope\nscope={ado}\n\n".encode())
        await writer.drain()
        data = await reader.read(4096)
        writer.close()
        assert data == b""  # unknown token: fully silent, no wire response
    finally:
        await srv.stop()


# --- _relay_profile_via_cli + _register_provider_relay -----------------------

def _cp(rc, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


def test_relay_profile_via_cli_parses():
    prof = {"sources": ["git-credential"], "gated_actions": [], "azure_resources": []}
    with patch("shutil.which", return_value="/bin/agent-codespaces"), \
         patch("agent_bridge.agent_registry_relay.run_captured", return_value=_cp(0, json.dumps(prof))):
        assert _relay_profile_via_cli("agent-codespaces") == prof


def test_relay_profile_via_cli_none_on_failure():
    with patch("shutil.which", return_value=None):
        assert _relay_profile_via_cli("agent-codespaces") is None
    with patch("shutil.which", return_value="/bin/x"), \
         patch("agent_bridge.agent_registry_relay.run_captured", return_value=_cp(1, "", "boom")):
        assert _relay_profile_via_cli("agent-codespaces") is None
    with patch("shutil.which", return_value="/bin/x"), \
         patch("agent_bridge.agent_registry_relay.run_captured", return_value=_cp(0, "not json")):
        assert _relay_profile_via_cli("agent-codespaces") is None


def test_register_provider_relay_prefers_cli(tmp_path):
    store = tmp_path / "s.json"
    _write_store(store, {"cs": "TOK"})
    prof = {
        "sources": ["git-credential"], "port": 1, "ado_host": None,
        "azure_resources": ["r"], "gated_actions": ["get-azure-token"],
        "token_store": str(store),
    }
    b = _FakeBuilder()
    with patch("shutil.which", return_value="/bin/agent-codespaces"), \
         patch("agent_bridge.agent_registry_relay.run_captured", return_value=_cp(0, json.dumps(prof))), \
         patch("importlib.import_module") as imp:
        _register_provider_relay(b, "agent-codespaces")
    imp.assert_not_called()  # CLI path -> no in-process import
    assert b.port == 1 and isinstance(b.validator, FileTokenValidator)


def test_register_provider_relay_no_import_when_cli_unavailable():
    # #1643: no in-process register_relay import fallback -- when the binstub is
    # absent the provider contributes NOTHING and no import is ever attempted.
    b = _FakeBuilder()
    with patch("shutil.which", return_value=None), \
         patch("importlib.import_module") as imp:
        _register_provider_relay(b, "agent-codespaces")
    imp.assert_not_called()
    assert b.sources == [] and b.port is None and b.validator is None


# --- #5405: registered provider whose seam is momentarily unavailable -------

def _register_manifest(tmp_path, monkeypatch, name="agent-codespaces"):
    d = tmp_path / "providers.d"
    d.mkdir(exist_ok=True)
    (d / f"{name}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AGENT_BRIDGE_PROVIDERS_DIR", str(d))


def test_registered_provider_retries_until_profile_available(tmp_path, monkeypatch):
    _register_manifest(tmp_path, monkeypatch)
    store = tmp_path / "s.json"
    _write_store(store, {"cs": "TOK"})
    prof = {
        "sources": ["git-credential"], "port": 1,
        "azure_resources": ["r"], "gated_actions": ["get-azure-token"],
        "token_store": str(store),
    }
    # Binstub missing for the first two probes (mid-update), then healthy.
    which = iter([None, None, "/bin/agent-codespaces"])
    sleeps: list[float] = []
    b = _FakeBuilder()
    with patch("shutil.which", side_effect=lambda _n: next(which)), \
         patch("agent_bridge.agent_registry_relay.run_captured", return_value=_cp(0, json.dumps(prof))):
        _register_provider_relay(b, "agent-codespaces", sleep=sleeps.append)
    assert len(sleeps) == 2
    assert b.port == 1 and isinstance(b.validator, FileTokenValidator)


def test_registered_provider_warns_after_bounded_retries(tmp_path, monkeypatch, caplog):
    _register_manifest(tmp_path, monkeypatch)
    sleeps: list[float] = []
    b = _FakeBuilder()
    with patch("shutil.which", return_value="/bin/agent-codespaces"), \
         patch("agent_bridge.agent_registry_relay.run_captured", return_value=_cp(1, "", "mid-update")), \
         caplog.at_level("WARNING", logger="agent-bridge"):
        _register_provider_relay(b, "agent-codespaces", sleep=sleeps.append)
    assert sleeps and sum(sleeps) <= 30
    assert b.sources == [] and b.validator is None
    assert any(
        r.levelname == "WARNING" and "agent-codespaces" in r.getMessage()
        and "service restart" in r.getMessage()
        for r in caplog.records
    )


def test_unregistered_provider_does_not_retry():
    sleeps: list[float] = []
    b = _FakeBuilder()
    with patch("shutil.which", return_value=None):
        _register_provider_relay(b, "agent-codespaces", sleep=sleeps.append)
    assert sleeps == []
    assert b.sources == []


def test_registered_provider_retries_stay_within_total_budget(tmp_path, monkeypatch):
    from agent_bridge import agent_registry_relay as relay

    _register_manifest(tmp_path, monkeypatch)
    now = [0.0]
    probe_timeouts: list[float] = []

    def sleep(seconds: float) -> None:
        now[0] += seconds

    def hung_probe(*_args, timeout, **_kwargs):
        # Every probe hangs for its full timeout (a wedged mid-update binstub).
        probe_timeouts.append(timeout)
        now[0] += timeout
        raise subprocess.TimeoutExpired("agent-codespaces", timeout)

    b = _FakeBuilder()
    with patch("shutil.which", return_value="/bin/agent-codespaces"), \
         patch("agent_bridge.agent_registry_relay.run_captured", side_effect=hung_probe):
        relay._register_provider_relay(
            b, "agent-codespaces", sleep=sleep, clock=lambda: now[0],
        )
    retry_elapsed = now[0] - probe_timeouts[0]  # exclude the initial probe
    assert retry_elapsed <= relay._PROFILE_RETRY_BUDGET
    assert all(t <= relay._PROFILE_RETRY_PROBE_TIMEOUT for t in probe_timeouts[1:])
    assert b.sources == [] and b.validator is None
