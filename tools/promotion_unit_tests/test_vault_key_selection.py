"""Actor-free specialized-key commands through the actual scoped transport."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
import importlib
import json
import re
import sys
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest

from .test_vault_read_selection import PACKAGE, ROOT, peer as peer, production_functions

identity = ModuleType(f"{PACKAGE}.identity_key")
identity_tree = ast.parse((ROOT / "plugins/agent-vault/src/agent_vault/identity_key.py").read_bytes())
error_class = next(
    node for node in identity_tree.body if isinstance(node, ast.ClassDef) and node.name == "IdentityKeyError"
)
exec(compile(ast.Module(body=[error_class], type_ignores=[]), "<identity-error>", "exec"), identity.__dict__)
sys.modules[identity.__name__] = identity
caller = importlib.import_module(f"{PACKAGE}.key_client")
transport = importlib.import_module(f"{PACKAGE}.command_transport")
selection = importlib.import_module(f"{PACKAGE}.cache_warm_selection")
invocation = importlib.import_module(f"{PACKAGE}.invocation")


class MemoryCache:
    enabled = True
    _recovery = False

    def __init__(self) -> None:
        self.held = False
        self.value: str | None = None
        self.events: list[object] = []

    def prepare(self) -> None:
        self.events.append("prepare")

    @contextmanager
    def replacement_lock(self) -> Iterator[None]:
        self.held = True
        try:
            yield
        finally:
            self.held = False

    def reconcile_authoritative(self, entry: str, field: str, value: str, generation: int) -> bool:
        assert self.held
        self.events.append((entry, field, generation))
        self.value = value
        return True

    def get(self, _entry: str, _field: str, *, readonly: bool) -> str | None:
        assert self.held and readonly
        return self.value


class FixturePath:
    def __init__(self, value: object) -> None:
        self.value = str(value)

    def expanduser(self) -> FixturePath:
        return self

    def resolve(self, *, strict: bool) -> FixturePath:
        assert strict
        return self

    def is_file(self) -> bool:
        return True

    def is_absolute(self) -> bool:
        return True

    def __str__(self) -> str:
        return self.value


@pytest.fixture
def key(peer: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = peer
    state.actions = sorted(selection.KEY_ACTIONS)
    state.strict_reads, state.targets, state.effects, state.waits = [], [], [], []
    state.host, state.changed = False, False
    state.cache = MemoryCache()
    initial = state.context

    def resolve(**options: object) -> SimpleNamespace:
        state.strict_reads.append(options)
        return cast(SimpleNamespace, initial)

    monkeypatch.setattr(state.config, "resolve_context", resolve)
    production_functions("config", {"_nonempty", "_clean_group", "normalize_entry"}, state.config.__dict__)
    state.config.load_config = lambda: SimpleNamespace(group="Other")
    cache_module = sys.modules[f"{PACKAGE}.cache"]
    monkeypatch.setattr(cache_module, "get_cache", lambda *_a: state.cache)

    def source(context: SimpleNamespace | None = None) -> dict[str, Any]:
        return {"version": 1, "env": {
            "KPDB": initial.kpdb, "AGENT_VAULT_CACHE_DIR": "fixture-cache",
            "group": context.group if context is not None else (
                "Changed" if state.changed else initial.group
            ),
        }}

    def reply(request: dict[str, Any], _host: str, port: int, wait: float | None) -> Any:
        state.requests.append((port, dict(request), invocation.configuration_root()))
        action = request["action"]
        if action == "ping":
            state.endpoint = 19072
            return {
                "ok": True, "accepting_work": True,
                "cache_warm_selection_versions": state.versions,
                "cache_warm_read_selection_versions": state.read_versions,
                "cache_warm_selection_actions": state.actions,
            }
        state.targets.append(state.config.normalize_entry(request["entry"], request["group"]))
        state.waits.append(wait)
        if state.outcome == "lost":
            return None
        if state.outcome == "invalid":
            return {"ok": "false", "value": "fixture-key"}
        state.changed = state.outcome == "context-changed"
        result: dict[str, Any] = {
            "ok": True, "value": "fixture-key", "generation": 7,
            "status": "reused", "public_key": "fixture-public", "fingerprint": "fixture-fingerprint",
        }
        if selection.FIELD in request and state.receipt:
            result["cache_warm"] = {
                "state": "queued", "target_policy": invocation.CACHE_TARGET_CAPABILITY,
                "request_selection_version": 1,
            }
        return result

    def discover(context: object, *, strict: bool) -> SimpleNamespace:
        endpoint = state.boundary.discover(context, strict=strict)
        endpoint.source = "windows" if state.host else "fixture"
        return cast(SimpleNamespace, endpoint)

    def preferred(request: dict[str, Any], wait: float | None, *_a: Any, **_kw: Any) -> Any:
        return None if state.bound else reply(request, "fixture", 19070, wait)

    boundary = replace(
        state.boundary, discover=discover, tcp_send=reply,
        registry=lambda: SimpleNamespace(try_transports=preferred),
    )
    monkeypatch.setattr(state.cli, "send_command", lambda request, timeout=5.0, **options: transport.send_command(
        boundary, request, timeout, **options,
    ))
    state.cli.auto_unlock = lambda *_a: pytest.fail("key command entered ordinary mutation unlock")
    metadata = lambda _public: {"public_key": "fixture-public", "fingerprint": "fixture-fingerprint"}
    common = {
        "__package__": PACKAGE, "config": state.config, "json": json, "Path": FixturePath,
        "IdentityKeyError": identity.IdentityKeyError, "_crypto": lambda: None,
        "public_metadata": metadata,
        "FAILURE_HINTS": {"service_unavailable": "fixture-unavailable"},
    }
    identity_namespace = {
        **common, "__name__": f"{PACKAGE}.identity_key", "source_context": source,
        "validate_key": lambda value: value,
        "SERVICE_FAILURE_HINTS": {"service_unavailable": "fixture-unavailable"},
    }
    production_functions("identity_key", {"command", "_command"}, identity_namespace)
    ssh_namespace = {**common, "__name__": f"{PACKAGE}.ssh_key"}
    production_functions("ssh_key", {"command", "_command", "_validate_args", "validate_request"}, ssh_namespace)
    files = ModuleType(f"{PACKAGE}.ssh_key_files")

    def install(*_args: object) -> dict[str, str]:
        state.effects.append("files")
        return {"status": "installed"}

    files.__dict__["install"] = install
    monkeypatch.setitem(sys.modules, files.__name__, files)
    install_namespace = {
        **common, "__name__": f"{PACKAGE}.ssh_key_install", "re": re,
        "validate_request": ssh_namespace["validate_request"],
        "decode_record": lambda _value: {"private_key": "fixture-private", "public_key": "fixture-public"},
    }
    production_functions("ssh_key_install", {"command", "_command", "_validate_args"}, install_namespace)
    state.commands = {
        "identity-key-setup": identity_namespace["command"],
        "ssh-key-prepare": ssh_namespace["command"],
        "ssh-key-read": install_namespace["command"],
    }
    state.args = SimpleNamespace(
        identity_command="setup", entry="direct", initialize_owner=False, confirm_first_owner=False,
        initialize=False, confirm_initialization=False, require_running=False, prompt=False,
        expected_fingerprint=None, directory="fixture-directory",
    )
    return state


def test_key_commands_preserve_owner_path_and_local_transactions(
    key: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    for action, command in key.commands.items():
        key.requests.clear()
        key.targets.clear()
        key.cache.events.clear()
        key.endpoint = 19071
        assert command(key.args) == 0
        output = capsys.readouterr()
        assert not output.err
        assert "fixture-key" not in output.out and "fixture-private" not in output.out
        assert all(port == 19071 for port, _request, _root in key.requests)
        assert all(target == "Example/direct" for target in key.targets)
        requests = [request for _, request, _ in key.requests if request["action"] != "ping"]
        assert [request["action"] for request in requests] == (
            [action, "get"] if action == "identity-key-setup" else [action]
        )
        assert all(
            request["entry"] == "/Example/direct" and request["group"] == "Example"
            and request[selection.FIELD]["root"] == key.root for request in requests
        )
        if action == "identity-key-setup":
            assert key.cache.events == ["prepare", ("Example/direct", "password", 7)]
        else:
            assert not key.cache.events
    assert key.effects == ["files"]
    assert all(options == {"strict": True} for options in key.strict_reads)
    assert all(wait == 90 for wait in key.waits)


def test_key_skew_and_missing_receipts_never_claim_partial_source_support(
    key: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    for action, command in key.commands.items():
        for skew in ("old", "read-only", "missing-read", "missing-receipt", "bool"):
            key.requests.clear()
            key.endpoint = 19071
            key.actions = ["get"] if skew == "read-only" else sorted(selection.KEY_ACTIONS)
            key.versions = [] if skew == "old" else [True] if skew == "bool" else [1]
            key.read_versions = [] if skew == "missing-read" else [1]
            key.receipt = skew != "missing-receipt"
            assert command(key.args) == 0
            output = capsys.readouterr()
            qualified = skew == "missing-receipt" or skew == "missing-read" and action != "identity-key-setup"
            if skew == "missing-receipt":
                assert "not confirmed" in output.err
            elif not qualified:
                assert "unsupported" in output.err
            else:
                assert not output.err
            requests = [request for _, request, _ in key.requests if request["action"] != "ping"]
            assert all((selection.FIELD in request) == qualified for request in requests)
            assert all(
                request["entry"] == ("/Example/direct" if qualified else "Example/direct")
                and request["group"] == ("Example" if qualified else "") for request in requests
            )


def test_key_unsafe_routes_and_unknown_replies_preserve_no_replay_and_output_custody(
    key: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    for command in key.commands.values():
        for outcome in ("host", "unbound", "lost", "invalid"):
            key.requests.clear()
            key.effects.clear()
            key.cache.events.clear()
            key.host, key.bound = outcome == "host", outcome != "unbound"
            key.outcome = outcome
            assert command(key.args) == 1
            output = capsys.readouterr()
            assert json.loads(output.out)["error"] == "service_unavailable"
            assert "fixture-key" not in output.out + output.err
            assert not key.cache.events and not key.effects
            assert len(key.requests) == (1 if outcome in ("host", "unbound") else 2)


def test_key_post_effect_context_check_is_fresh_and_precedes_cache_mutation(
    key: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    key.outcome = "context-changed"
    assert key.commands["identity-key-setup"](key.args) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "context_invalid"
    assert not key.cache.events
    assert [request["action"] for _, request, _ in key.requests] == ["ping", "identity-key-setup"]


def test_key_captured_absent_group_never_reloads_a_different_default(
    key: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    key.context.group = None
    for action, command in key.commands.items():
        key.requests.clear()
        key.targets.clear()
        key.cache.events.clear()
        assert command(key.args) == 0
        assert not capsys.readouterr().err
        assert key.targets and all(target == "direct" for target in key.targets)
        if action == "identity-key-setup":
            assert key.cache.events == ["prepare", ("direct", "password", 7)]
