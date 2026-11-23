"""Specialized commands keep the chosen peer, key identity and local custody."""

from __future__ import annotations

import base64
from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from agent_vault import cli, config, extensions, identity_key, ssh_key, ssh_key_files
from agent_vault.cache import PersistentCache
from agent_vault.cache_warm_selection import FIELD, capabilities
from agent_vault.invocation import CACHE_TARGET_CAPABILITY, configuration_root
from agent_vault.rendezvous import Endpoint

KEY = base64.b64encode(bytes(range(32))).decode("ascii")


@pytest.fixture
def peer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    for name in tuple(os.environ):
        if name.startswith(("AGENT_VAULT", "VAULT_")) or name == "KPDB":
            monkeypatch.delenv(name)
    database = tmp_path / "fixture.kdbx"
    database.write_bytes(b"disposable fixture")
    monkeypatch.setenv("AGENT_VAULT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("AGENT_VAULT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("AGENT_VAULT_CACHE", "1")
    monkeypatch.chdir(tmp_path)
    context = config.ResolvedVault(
        kpdb=str(database), group="Allowed", vault_name=None, port=19071, sources={"port": "flag"},
    )
    state = SimpleNamespace(
        context=context, requests=[], discoveries=[], resolutions=[], port=19071,
        capabilities=capabilities(), mode="success", changed=False, host=False,
        record=ssh_key._generate_record(), selection=str(tmp_path), bound=True,
    )

    def resolve(**options: object) -> config.ResolvedVault:
        state.resolutions.append((options, configuration_root()))
        selected = cast(config.ResolvedVault, state.context)
        return replace(selected, group="Changed") if state.changed else selected

    monkeypatch.setattr(config, "configuration_selection", lambda **_kw: (state.selection, False))
    monkeypatch.setattr(config, "resolve_context", resolve)
    registry = extensions.ExtensionRegistry()
    registry._loaded = True
    monkeypatch.setattr(extensions, "_REGISTRY", registry)
    monkeypatch.setattr(cli, "IS_WINDOWS", True)
    monkeypatch.setattr(cli, "IS_WSL", False)
    monkeypatch.setattr(cli, "start_service", lambda *_a: pytest.fail("unexpected activation"))
    monkeypatch.setattr(cli, "auto_unlock", lambda *_a: pytest.fail("client unlock changed key custody"))

    def discover(_context: config.ResolvedVault, **_kw: object) -> Endpoint:
        state.discoveries.append(state.port)
        return Endpoint(
            "tcp", f"127.0.0.1:{state.port}", source="windows" if state.host else "fixture",
        )

    def reply(request: dict[str, Any], _host: str, port: int, timeout: float | None) -> Any:
        state.requests.append((port, dict(request), timeout, configuration_root()))
        action = request["action"]
        if action == "ping":
            state.port = 19072
            return {"ok": True, "accepting_work": True, **state.capabilities}
        if state.mode == "lost":
            return None
        if state.mode == "invalid":
            return {"ok": "false", "value": KEY, "status": "reused"}
        if action == "ssh-key-prepare":
            result = {"ok": True, "status": "reused", **ssh_key._read_record(state.record)}
        else:
            result = {
                "ok": True, "status": "reused", "generation": 7,
                "value": state.record if action == "ssh-key-read" else KEY,
            }
        if state.mode == "context-changed":
            state.changed = True
        if FIELD in request and state.mode != "missing-receipt":
            result["cache_warm"] = {
                "state": "queued", "target_policy": CACHE_TARGET_CAPABILITY,
                "request_selection_version": 1,
            }
        return result

    monkeypatch.setattr(cli, "_discover_endpoint", discover)
    monkeypatch.setattr(cli, "_send_tcp", reply)
    state.reply, state.registry = reply, registry
    return state


def argv(operation: str, tmp_path: Path, entry: str = "direct") -> list[str]:
    if operation == "identity-key-setup":
        return ["identity-key", "setup", "--entry", entry]
    return [
        "ssh-key", "prepare" if operation == "ssh-key-prepare" else "install",
        "--entry", entry,
        *(["--directory", str(tmp_path / "keys")] if operation == "ssh-key-read" else []),
    ]


@pytest.mark.parametrize("operation", ["identity-key-setup", "ssh-key-prepare", "ssh-key-read"])
@pytest.mark.parametrize("entry", ["direct", "Folder/key"])
@pytest.mark.parametrize("flags", [[], ["--require-running"], ["--prompt"]])
def test_full_key_commands_keep_readiness_peer_and_exact_key_under_selected_warming_group(
    peer: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], operation: str, entry: str, flags: list[str],
) -> None:
    files: list[object] = []

    def install(*args: object) -> dict[str, str]:
        files.append(args)
        return {"status": "installed"}

    monkeypatch.setattr(ssh_key_files, "install", install)
    assert cli.main(argv(operation, tmp_path, entry) + flags) == 0
    output = capsys.readouterr()
    assert not output.err and KEY not in output.out and peer.record not in output.out
    assert peer.discoveries == [19071]
    assert all(port == 19071 for port, *_rest in peer.requests)
    canonical = config.normalize_entry(entry, "Allowed")
    requests = [request for _, request, *_rest in peer.requests if request["action"] != "ping"]
    assert [request["action"] for request in requests] == (
        [operation, "get"] if operation == "identity-key-setup" else [operation]
    )
    assert all(
        request["entry"] == "/" + canonical and request["group"] == "Allowed"
        and request[FIELD]["root"] == str(tmp_path)
        and request["allow_prompt"] == ("--prompt" in flags) for request in requests
    )
    assert all(
        wait == (120 if "--prompt" in flags else 90)
        for _, request, wait, _ in peer.requests if request["action"] != "ping"
    )
    assert all(options == {"strict": True} and root == str(tmp_path) for options, root in peer.resolutions)
    assert len(peer.resolutions) == (2 if operation == "identity-key-setup" else 1)
    if operation == "identity-key-setup":
        cache = PersistentCache(identity_key.source_context(peer.context)["env"]["AGENT_VAULT_CACHE_DIR"])
        assert cache.get(canonical, "password", readonly=True) == KEY
        assert not files
    else:
        assert len(files) == (1 if operation == "ssh-key-read" else 0)


@pytest.mark.parametrize("operation", ["identity-key-setup", "ssh-key-prepare", "ssh-key-read"])
@pytest.mark.parametrize("skew", ["old", "read-only", "bool", "missing-read", "missing-receipt", "pinned"])
def test_key_capability_skew_keeps_legacy_payload_or_reports_unconfirmed_without_replay(
    peer: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], operation: str, skew: str,
) -> None:
    monkeypatch.setattr(ssh_key_files, "install", lambda *_a: {"status": "installed"})
    if skew == "old":
        peer.capabilities = {}
    elif skew == "read-only":
        peer.capabilities["cache_warm_selection_actions"] = ["get"]
    elif skew == "bool":
        peer.capabilities["cache_warm_selection_versions"] = [True]
    elif skew == "missing-read":
        peer.capabilities["cache_warm_read_selection_versions"] = []
    elif skew == "missing-receipt":
        peer.mode = "missing-receipt"
    else:
        monkeypatch.setenv(config.PINNED_CONTEXT_ENV, "fixture-pin-no-root")
        monkeypatch.setattr(config, "configuration_selection", lambda **_kw: pytest.fail("invented owner"))
    assert cli.main(argv(operation, tmp_path)) == 0
    output = capsys.readouterr()
    qualified = skew == "missing-receipt" or skew == "missing-read" and operation != "identity-key-setup"
    if skew == "missing-receipt":
        assert "not confirmed" in output.err
    elif not qualified:
        assert "unsupported" in output.err
    else:
        assert not output.err
    requests = [request for _, request, *_rest in peer.requests if request["action"] != "ping"]
    assert len(requests) == (2 if operation == "identity-key-setup" else 1)
    assert all((FIELD in request) == qualified for request in requests)
    assert all(
        request["entry"] == ("/Allowed/direct" if qualified else "Allowed/direct")
        and request["group"] == ("Allowed" if qualified else "") for request in requests
    )


@pytest.mark.parametrize("operation", ["identity-key-setup", "ssh-key-prepare", "ssh-key-read"])
@pytest.mark.parametrize("failure", ["lost", "invalid", "host", "unbound"])
def test_key_unknown_outcome_and_unsafe_recipient_never_retry_or_publish_local_outputs(
    peer: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], operation: str, failure: str,
) -> None:
    peer.mode, peer.host = failure, failure == "host"
    if failure == "unbound":
        monkeypatch.setattr(
            peer.registry, "try_transports",
            lambda request, timeout, *_a, **_kw: peer.reply(request, "fixture", 19070, timeout),
        )
    monkeypatch.setattr(PersistentCache, "prepare", lambda *_a: pytest.fail("cache prepared"))
    monkeypatch.setattr(ssh_key_files, "install", lambda *_a: pytest.fail("key files published"))
    assert cli.main(argv(operation, tmp_path)) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["error"] == "service_unavailable"
    assert KEY not in output.out + output.err and peer.record not in output.out + output.err
    actions = [request["action"] for _, request, *_rest in peer.requests]
    assert actions == (["ping"] if failure in ("host", "unbound") else ["ping", operation])


def test_identity_fresh_post_effect_context_check_precedes_cache_mutation(
    peer: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    peer.mode = "context-changed"
    monkeypatch.setattr(PersistentCache, "prepare", lambda *_a: pytest.fail("cache prepared after change"))
    assert cli.main(argv("identity-key-setup", tmp_path)) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "context_invalid"
    assert len(peer.resolutions) == 2
    assert [request["action"] for _, request, *_rest in peer.requests] == ["ping", "identity-key-setup"]


@pytest.mark.parametrize("operation", ["ssh-key-prepare", "ssh-key-read"])
def test_ssh_input_refusals_precede_context_and_never_expand_public_root_path_syntax(
    peer: SimpleNamespace, tmp_path: Path, capsys: pytest.CaptureFixture[str], operation: str,
) -> None:
    assert cli.main(argv(operation, tmp_path, "/direct")) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_entry"
    assert not peer.resolutions and not peer.requests
    if operation == "ssh-key-read":
        assert cli.main(argv(operation, tmp_path) + ["--expected-fingerprint", "invalid"]) == 1
        assert json.loads(capsys.readouterr().out)["error"] == "invalid_fingerprint"
        assert not peer.resolutions and not peer.requests


@pytest.mark.parametrize("operation", ["identity-key-setup", "ssh-key-prepare", "ssh-key-read"])
def test_key_captured_absent_group_never_reloads_a_new_default(
    peer: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], operation: str,
) -> None:
    peer.context = replace(peer.context, group=None)
    monkeypatch.setattr(ssh_key_files, "install", lambda *_a: {"status": "installed"})
    monkeypatch.setattr(config, "load_config", lambda: pytest.fail("key entry reloaded a default group"))
    assert cli.main(argv(operation, tmp_path)) == 0
    assert not capsys.readouterr().err
    requests = [request for _, request, *_rest in peer.requests if request["action"] != "ping"]
    assert all(request["entry"] == "/direct" and request["group"] == "" for request in requests)
