"""Actor-free source/recipient custody around the actual password transaction."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
import importlib
import os
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from .test_vault_mutation_selection import ordinary as ordinary
from .test_vault_read_selection import PACKAGE, peer as peer, production_functions

caller = importlib.import_module(f"{PACKAGE}.password_command")
transport = importlib.import_module(f"{PACKAGE}.command_transport")
selection = importlib.import_module(f"{PACKAGE}.cache_warm_selection")
invocation = importlib.import_module(f"{PACKAGE}.invocation")


class MemoryCache:
    def __init__(self) -> None:
        self.enabled = True
        self.held = False
        self.pending: dict[str, object] | None = None
        self.value = "fixture-old"
        self.events: list[tuple[object, ...]] = []
        self.complete_ok = True

    @contextmanager
    def replacement_lock(self) -> Iterator[None]:
        assert not self.held
        self.held = True
        try:
            yield
        finally:
            self.held = False

    def pending_replace(self, entry: str, field: str) -> dict[str, object] | None:
        assert self.held
        self.events.append(("pending", entry, field))
        return self.pending

    def migrate_entry(self, old: str, entry: str, field: str) -> bool:
        assert self.held
        self.events.append(("migrate", old, entry, field))
        return True

    def replace(self, entry: str, field: str, value: str, generation: int) -> bool:
        assert self.held
        self.events.append(("replace", entry, field, value, generation))
        self.value, self.pending = value, None
        return True

    def begin_replace(self, entry: str, field: str, value: str, operation: int) -> bool:
        assert self.held
        self.events.append(("begin", entry, field, value, operation))
        self.pending = {"operation": operation}
        return True

    def complete_replace(
        self, entry: str, field: str, value: str, operation: int, generation: int,
    ) -> bool:
        assert self.held
        self.events.append(("complete", entry, field, value, operation, generation))
        if self.complete_ok:
            self.value, self.pending = value, None
        return self.complete_ok

    def rollback_replace(self, entry: str, field: str, operation: int) -> bool:
        assert self.held
        self.events.append(("rollback", entry, field, operation))
        self.pending = None
        return True


@pytest.fixture
def password(ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = ordinary
    persistent = MemoryCache()
    state.cache = persistent
    state.reply_mode, state.read_mode = "success", "success"
    state.read_generation, state.mutation_generation = 7, 7
    state.server_value = "fixture-old"
    state.mutation_actions = sorted(selection.MUTATION_ACTIONS | selection.PASSWORD_ACTIONS)
    state.waits = []
    state.getpass_value, state.input_prompts = "fixture-candidate", []
    state.loaded_group, state.group_loads, state.gain_group, state.pings = "Example", 0, False, 0
    production_functions(
        "config", {"_nonempty", "_clean_group", "normalize_entry"}, state.config.__dict__,
    )
    server_config: dict[str, Any] = {
        "load_config": lambda: SimpleNamespace(group="Other"),
    }
    production_functions(
        "config", {"_nonempty", "_clean_group", "normalize_entry"}, server_config,
    )
    state.server_entries = None
    state.server_targets = []

    def load_config() -> Any:
        state.group_loads += 1
        return SimpleNamespace(group=state.loaded_group)

    state.config.load_config = load_config
    monkeypatch.setattr(sys.modules[f"{PACKAGE}.cache"], "get_cache", lambda: persistent)
    reads = ModuleType(f"{PACKAGE}.credential_reads")
    reads.__dict__.update(config=state.config, os=os)
    production_functions("credential_reads", {"legacy_cache_entry"}, reads.__dict__)
    monkeypatch.setitem(sys.modules, reads.__name__, reads)
    getpass = ModuleType("getpass")

    def prompt(message: str) -> str:
        state.input_prompts.append(message)
        return str(state.getpass_value)

    getpass.__dict__["getpass"] = prompt
    monkeypatch.setitem(sys.modules, "getpass", getpass)

    def reply(request: dict[str, Any], _host: str, port: int, wait: float | None) -> Any:
        action = request["action"]
        state.requests.append((port, dict(request), invocation.configuration_root()))
        if action == "ping":
            state.pings += 1
            if state.gain_group and state.pings == 2:
                state.loaded_group = "Other"
            return {
                "ok": True, "accepting_work": True,
                "cache_warm_selection_versions": state.versions,
                "cache_warm_read_selection_versions": state.read_versions,
                "cache_warm_selection_actions": state.mutation_actions,
                "unlocked_vaults": (
                    [state.context.kpdb] if state.unlocked else []
                ) + state.other_unlocked,
            }
        if action == "unlock":
            if state.unlock_mode == "lost":
                return None
            if state.unlock_mode == "password" and not request.get("password"):
                return {"ok": False, "needs_unlock": True}
            state.unlocked = True
            response: dict[str, Any] = {"ok": True}
        else:
            assert persistent.held
            state.waits.append((action, wait))
            entry = server_config["normalize_entry"](request["entry"], request.get("group"))
            state.server_targets.append((action, entry))
            if action == "get":
                if state.read_mode == "lost":
                    return None
                if state.read_mode == "invalid-ok":
                    return {"ok": "false", "value": "must-not-cache", "generation": 7}
                if state.read_mode == "invalid-value":
                    return {"ok": True, "value": 7, "generation": 7}
                response = {
                    "ok": True,
                    "value": (
                        state.server_entries[entry] if state.server_entries is not None
                        else state.server_value
                    ),
                    "generation": state.read_generation,
                }
            else:
                assert action == "set-password"
                if state.reply_mode not in ("refused", "pending"):
                    state.server_value = request["password"]
                    if state.server_entries is not None:
                        state.server_entries[entry] = request["password"]
                if state.reply_mode == "lost":
                    return None
                if state.reply_mode == "invalid":
                    return {"ok": "false", "message": "must-not-print"}
                if state.reply_mode == "invalid-committed":
                    response = {"ok": False, "committed": "false", "generation": 7}
                elif state.reply_mode == "invalid-pending":
                    response = {"ok": False, "cache_pending": "false"}
                elif state.reply_mode == "refused":
                    response = {"ok": False, "error": "fixture-refusal"}
                elif state.reply_mode == "pending":
                    response = {"ok": False, "cache_pending": True, "error": "fixture-pending"}
                elif state.reply_mode == "committed":
                    response = {
                        "ok": False, "committed": True, "generation": state.mutation_generation,
                        "error": "fixture-cache-failure",
                    }
                else:
                    response = {"ok": True, "message": "fixture-updated"}
                    if state.reply_mode != "generation-missing":
                        response["generation"] = state.mutation_generation
        if selection.FIELD in request and state.receipt:
            response["cache_warm"] = {
                "state": state.warm_state,
                "target_policy": invocation.CACHE_TARGET_CAPABILITY,
                "request_selection_version": 1,
            }
        return response

    def preferred(request: dict[str, Any], wait: float | None, _context: object, **_kw: Any) -> Any:
        return None if state.bound else reply(request, "127.0.0.1", 19070, wait)

    boundary = replace(
        state.boundary, tcp_send=reply, resolve_context=lambda: state.config.resolve_context(),
        registry=lambda: SimpleNamespace(try_transports=preferred),
    )
    monkeypatch.setattr(state.cli, "send_command", lambda request, timeout=5.0, **options: transport.send_command(
        boundary, request, timeout, **options,
    ))
    state.cli.PASSWORD_MUTATION_TIMEOUT = 30.0
    production_functions("cli", {"cmd_set_password"}, state.cli.__dict__)
    return state


def arguments() -> SimpleNamespace:
    return SimpleNamespace(entry="direct", password="fixture-candidate")


def test_password_recovery_mutation_and_confirmation_keep_one_scope_and_cache_owner(
    password: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    password.cache.pending = {"operation": 1}
    password.reply_mode = "generation-missing"
    assert password.cli.cmd_set_password(arguments()) == 0
    output = capsys.readouterr()
    assert output.out == "fixture-updated\n" and not output.err
    assert [request["action"] for _, request, _ in password.requests] == [
        "ping", "ping", "get", "set-password", "get",
    ]
    assert all(port == 19071 and root == password.root for port, _, root in password.requests)
    assert all(
        request[selection.FIELD]["root"] == password.root
        and request["kpdb"] == password.context.kpdb
        for _, request, _ in password.requests[2:]
    )
    assert all(wait == 30.0 for _, wait in password.waits)
    assert password.cache.pending is None and password.cache.value == "fixture-candidate"
    assert [event[0] for event in password.cache.events] == ["pending", "replace", "begin", "replace"]
    assert all(event[1] == "Example/direct" for event in password.cache.events)
    assert not password.cache.held and invocation.configuration_root() is None


def test_password_captures_vault_and_group_before_readiness_and_terminal_unlock(
    password: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original = password.context
    replacement = SimpleNamespace(
        kpdb="other-database", group="Other", vault_name="second",
        port=19071, sources={"port": "flag"},
    )
    resolutions: list[object] = []

    def resolve() -> Any:
        context = original if not resolutions else replacement
        resolutions.append(context)
        return context

    monkeypatch.setattr(password.config, "resolve_context", resolve)
    password.unlocked, password.unlock_mode, password.redirect_on_prompt = False, "password", True
    password.other_unlocked = [replacement.kpdb]
    assert password.cli.cmd_set_password(arguments()) == 0
    output = capsys.readouterr()
    assert output.out == "fixture-updated\n" and not output.err
    assert resolutions == [original] and len(password.password_reads) == 1
    assert [request["action"] for _, request, _ in password.requests] == [
        "ping", "ping", "unlock", "unlock", "set-password",
    ]
    assert all(port == 19071 and request["kpdb"] == original.kpdb for port, request, _ in password.requests)
    assert all(event[1] == "Example/direct" for event in password.cache.events)
    assert password.cache.value == "fixture-candidate" and invocation.configuration_root() is None


def test_password_uncertain_and_committed_outcomes_preserve_journal_and_never_replay(
    password: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    for mode, code, pending in (
        ("lost", 1, True), ("invalid", 1, True), ("pending", 1, True),
        ("invalid-committed", 1, True), ("invalid-pending", 1, True),
        ("refused", 1, False), ("committed", 2, False),
    ):
        password.reply_mode = mode
        password.requests.clear()
        password.cache.pending = None
        password.cache.events.clear()
        assert password.cli.cmd_set_password(arguments()) == code
        output = capsys.readouterr()
        assert not output.out and "Error:" in output.err and "must-not-print" not in output.err
        assert [request["action"] for _, request, _ in password.requests] == [
            "ping", "ping", "set-password",
        ]
        assert (password.cache.pending is not None) is pending
        assert any(event[0] == "rollback" for event in password.cache.events) is (mode == "refused")
        assert not password.cache.held and invocation.configuration_root() is None
    password.reply_mode = "generation-missing"
    password.read_mode = "lost"
    password.cache.pending = None
    password.requests.clear()
    assert password.cli.cmd_set_password(arguments()) == 2
    assert "offline cache" in capsys.readouterr().err
    assert [request["action"] for _, request, _ in password.requests] == [
        "ping", "ping", "set-password", "get",
    ]
    assert password.cache.pending is not None
    for mode in ("lost", "invalid-ok", "invalid-value"):
        password.read_mode = mode
        password.requests.clear()
        password.cache.events.clear()
        password.cache.value = "fixture-old"
        assert password.cli.cmd_set_password(arguments()) == 2
        assert "pending password rotation" in capsys.readouterr().err
        assert [request["action"] for _, request, _ in password.requests] == ["ping", "ping", "get"]
        assert [event[0] for event in password.cache.events] == ["pending"]
        assert password.cache.pending is not None and password.cache.value == "fixture-old"


def test_password_invalid_generations_never_settle_recovery_confirmation_or_mutation(
    password: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    malformed: tuple[object, ...] = (True, 1.5, None, "7", "invalid", -1)
    for stage in ("recovery", "confirmation"):
        for generation in malformed:
            password.read_generation = generation
            password.reply_mode = "success" if stage == "recovery" else "generation-missing"
            password.requests.clear()
            password.cache.events.clear()
            password.cache.pending = {"operation": 1} if stage == "recovery" else None
            password.cache.value = password.server_value = "fixture-old"
            assert password.cli.cmd_set_password(arguments()) == 2
            assert not capsys.readouterr().out
            assert password.cache.pending is not None and password.cache.value == "fixture-old"
            assert not any(event[0] in ("replace", "complete", "rollback") for event in password.cache.events)
            actions = [request["action"] for _, request, _ in password.requests]
            assert actions == (
                ["ping", "ping", "get"] if stage == "recovery" else
                ["ping", "ping", "set-password", "get"]
            )
    password.read_generation, password.reply_mode = 7, "success"
    for generation in (True, 1.5, "7", "invalid", -1):
        password.mutation_generation = generation
        password.cache.pending = None
        password.cache.value = "fixture-old"
        password.cache.events.clear()
        password.requests.clear()
        assert password.cli.cmd_set_password(arguments()) == 2
        output = capsys.readouterr()
        assert not output.out and "offline cache" in output.err
        assert password.cache.pending is not None and password.cache.value == "fixture-old"
        assert [event[0] for event in password.cache.events] == ["pending", "begin"]
        assert [request["action"] for _, request, _ in password.requests] == [
            "ping", "ping", "set-password",
        ]
    password.mutation_generation = None
    password.cache.pending = None
    password.requests.clear()
    assert password.cli.cmd_set_password(arguments()) == 0
    assert capsys.readouterr().out == "fixture-updated\n"
    assert password.cache.pending is None
    assert [request["action"] for _, request, _ in password.requests] == [
        "ping", "ping", "set-password", "get",
    ]


def test_password_captured_no_group_never_reloads_a_new_default(
    password: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    password.context.group = password.loaded_group = None
    password.gain_group = True
    password.cache.pending = {"operation": 1}
    password.reply_mode = "generation-missing"
    password.server_entries = {"direct": "fixture-old", "Other/direct": "fixture-other"}
    assert password.cli.cmd_set_password(arguments()) == 0
    output = capsys.readouterr()
    assert output.out == "fixture-updated\n" and not output.err
    assert password.loaded_group == "Other" and password.group_loads == 0
    assert all(event[1] == "direct" for event in password.cache.events)
    assert password.cache.pending is None and password.cache.value == "fixture-candidate"
    assert password.server_targets == [
        ("get", "direct"), ("set-password", "direct"), ("get", "direct"),
    ]
    assert password.server_entries == {
        "direct": password.cache.value, "Other/direct": "fixture-other",
    }


def test_password_mixed_capabilities_pinned_and_unbound_peers_keep_explicit_baseline(
    password: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for actions, reads in ((["add", "get"], [1]), (["set-password"], []),
                           (["set-password"], [True]), (["set-password"], ["1"])):
        password.mutation_actions, password.read_versions = actions, reads
        password.reply_mode = "generation-missing"
        password.cache.pending = {"operation": 1}
        password.requests.clear()
        assert password.cli.cmd_set_password(arguments()) == 0
        output = capsys.readouterr()
        assert output.out == "fixture-updated\n" and "unsupported" in output.err
        assert [request["action"] for _, request, _ in password.requests] == [
            "ping", "ping", "get", "set-password", "get",
        ]
        assert all(selection.FIELD not in request for _, request, _ in password.requests[2:])
    password.read_versions = [1]
    monkeypatch.setenv("VAULT_READ_TEST_PIN", "immutable-fixture-context")
    owner_reads = password.owner_reads
    assert password.cli.cmd_set_password(arguments()) == 0
    assert "unsupported" in capsys.readouterr().err and password.owner_reads == owner_reads
    assert selection.FIELD not in password.requests[-1][1]
    monkeypatch.delenv("VAULT_READ_TEST_PIN")
    password.bound, password.unlocked = False, False
    password.requests.clear()
    password.cache.events.clear()
    assert password.cli.cmd_set_password(arguments()) == 1
    assert "retained service connector" in capsys.readouterr().err
    assert [request["action"] for _, request, _ in password.requests] == ["ping"]
    assert not password.cache.events and not password.password_reads


def test_password_unbound_unlocked_peer_cannot_apply_and_replay_a_lost_mutation(
    password: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    password.bound, password.unlocked, password.reply_mode = False, True, "lost"
    password.cache.pending = {"operation": 1}
    assert password.cli.cmd_set_password(arguments()) == 1
    output = capsys.readouterr()
    assert not output.out and "retained service connector" in output.err
    assert [request["action"] for _, request, _ in password.requests] == ["ping"]
    assert password.server_value == "fixture-old" and not password.cache.events
    assert password.cache.pending == {"operation": 1}
    assert not password.password_reads and invocation.configuration_root() is None


def test_password_host_owned_route_refuses_guest_journal_before_input_or_unlock(
    password: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    password.context.group = "Guest"
    password.unlocked = False
    password.server_entries = {"Guest/direct": "fixture-guest", "Host/direct": "fixture-host"}
    endpoint = SimpleNamespace(
        transport="tcp", source="windows", tcp_host_port=("127.0.0.1", 19071),
    )
    endpoint.usable = lambda _predicate: endpoint
    boundary = replace(password.boundary, discover=lambda *_a, **_kw: endpoint, is_wsl=True)
    monkeypatch.setattr(password.cli, "send_command", lambda request, timeout=5.0, **options: transport.send_command(
        boundary, request, timeout, **options,
    ))
    args = arguments()
    args.password = None
    assert password.cli.cmd_set_password(args) == 1
    output = capsys.readouterr()
    assert not output.out and "run password replacement on the host" in output.err
    assert [request["action"] for _, request, _ in password.requests] == ["ping"]
    assert all(
        "group" not in request and "kpdb" not in request and "vault" not in request
        for _, request, _ in password.requests
    )
    assert password.server_entries == {
        "Guest/direct": "fixture-guest", "Host/direct": "fixture-host",
    }
    assert not password.cache.events and not password.password_reads and not password.input_prompts
    assert invocation.configuration_root() is None


def test_password_optional_receipt_and_cancelled_input_restore_transaction_scope(
    password: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    for receipt, state in ((False, "idle"), (True, {})):
        password.receipt, password.warm_state = receipt, state
        password.requests.clear()
        assert password.cli.cmd_set_password(arguments()) == 0
        output = capsys.readouterr()
        assert output.out == "fixture-updated\n" and "not confirmed" in output.err
        assert password.cache.pending is None
        assert [request["action"] for _, request, _ in password.requests] == [
            "ping", "ping", "set-password",
        ]
    password.requests.clear()
    password.cache.events.clear()
    password.getpass_value = ""
    args = arguments()
    args.password = None
    assert password.cli.cmd_set_password(args) == 1
    assert "no password provided" in capsys.readouterr().err
    assert not password.cache.events and password.input_prompts == ["New password: "]
    assert [request["action"] for _, request, _ in password.requests] == ["ping", "ping"]
    assert invocation.configuration_root() is None


def test_password_scope_excludes_explicit_bindings_and_other_action_owners() -> None:
    calls: list[str] = []

    class ExplicitBoundary(RuntimeError):
        pass

    def native(*_args: Any, **_kwargs: Any) -> Any:
        raise ExplicitBoundary

    def scoped(request: dict[str, Any], _timeout: float | None, **_flags: bool) -> dict[str, Any]:
        calls.append(request["action"])
        return {"ok": True}

    boundary = SimpleNamespace(
        resolve_context=native, host="127.0.0.1", discover=native,
        unavailable=ExplicitBoundary, invalid=ValueError,
    )
    with transport.use_password_sender(scoped):
        assert transport.send_command(boundary, {"action": "set-password"})["ok"]
        for action, options in (
            ("unlock", {}), ("get", {}), ("add", {}), ("import-key-create-only", {}),
            ("set-password", {"_route": transport.CommandRoute()}),
            ("set-password", {"_context": SimpleNamespace(port=19071)}),
        ):
            with pytest.raises(ExplicitBoundary):
                transport.send_command(boundary, {"action": action}, **options)
        binding = SimpleNamespace(require_unselected=lambda: calls.append("binding"))
        with pytest.raises(ValueError, match="read-only health"):
            transport.send_command(boundary, {"action": "set-password"}, binding=binding)
    with pytest.raises(ExplicitBoundary):
        transport.send_command(boundary, {"action": "set-password"})
    assert calls == ["set-password", "binding"]
