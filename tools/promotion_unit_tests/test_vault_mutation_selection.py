"""Actor-free full-command custody for ordinary mutation warming."""

from __future__ import annotations

from dataclasses import replace
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from .test_vault_read_selection import PACKAGE, peer as peer, production_functions

caller = importlib.import_module(f"{PACKAGE}.mutation_client")
transport = importlib.import_module(f"{PACKAGE}.command_transport")
selection = importlib.import_module(f"{PACKAGE}.cache_warm_selection")
invocation = importlib.import_module(f"{PACKAGE}.invocation")


@pytest.fixture
def ordinary(peer: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = peer
    state.unlocked = True
    state.unlock_mode = "provider"
    state.mutation_mode = "success"
    state.mutation_actions = sorted(selection.MUTATION_ACTIONS)
    state.password_reads = []
    state.redirect_on_prompt = False
    state.other_unlocked = []

    def reply(request: dict[str, Any], _host: str, port: int, _timeout: float | None) -> Any:
        state.requests.append((port, dict(request), invocation.configuration_root()))
        action = request["action"]
        if action == "ping":
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
            if state.unlock_mode == "invalid":
                return {"ok": "false"}
            if state.unlock_mode == "refused":
                return {"ok": False, "error": "fixture-refusal", "retryable": True}
            if state.unlock_mode == "password" and not request.get("password"):
                return {"ok": False, "needs_unlock": True}
            state.unlocked = True
            response: dict[str, Any] = {"ok": True}
        else:
            assert action in selection.MUTATION_ACTIONS
            if state.mutation_mode == "lost":
                return None
            if state.mutation_mode == "invalid":
                return {"ok": "false", "message": "must-not-print"}
            if state.mutation_mode == "refused":
                return {"ok": False, "error": "fixture-refusal"}
            response = {"ok": True, "message": "fixture-created"}
        if selection.FIELD in request and state.receipt:
            response["cache_warm"] = {
                "state": state.warm_state,
                "target_policy": invocation.CACHE_TARGET_CAPABILITY,
                "request_selection_version": 1,
            }
        return response

    def preferred(request: dict[str, Any], timeout: float | None, _context: object, **_kw: Any) -> Any:
        return None if state.bound else reply(request, "127.0.0.1", 19070, timeout)

    boundary = replace(
        state.boundary, tcp_send=reply, resolve_context=lambda: state.config.resolve_context(),
        registry=lambda: SimpleNamespace(try_transports=preferred),
    )
    cli = state.cli
    monkeypatch.setattr(cli, "send_command", lambda request, timeout=5.0, **options: transport.send_command(
        boundary, request, timeout, **options,
    ))

    def password(prompt: str) -> str:
        state.password_reads.append(prompt)
        if state.redirect_on_prompt:
            state.endpoint = 19072
            monkeypatch.setattr(
                state.config, "configuration_selection",
                lambda **_kw: (str(Path(state.root) / "replacement"), False),
            )
        return "fixture-master"

    cli.__dict__.update(
        sys=sys, os=SimpleNamespace(environ={}), ordinary_mutation=caller.ordinary_mutation,
        _ensure_unlocked_service=caller._ensure_unlocked_service, PROVIDER_UNLOCK_TIMEOUT=5.0,
        _has_controlling_tty=lambda: True, _read_password_from_tty=password,
        _in_ssh_session=lambda: False,
        prompt_password=lambda: pytest.fail("unexpected GUI prompt"),
        prompt_password_with_confirm=lambda *_a: pytest.fail("unexpected credential prompt"),
    )
    production_functions(
        "cli", {
            "auto_unlock", "_provider_only_unlock", "_terminal_unlock_local",
            "_current_vault_unlocked", "cmd_add", "cmd_set_username", "cmd_remove", "cmd_move",
        }, cli.__dict__,
    )
    copy_namespace = {
        "__name__": f"{PACKAGE}.copy_command", "__package__": PACKAGE,
        "ordinary_mutation": caller.ordinary_mutation, "sys": sys,
    }
    production_functions("copy_command", {"cmd_copy"}, copy_namespace)
    cli.__dict__["cmd_copy"] = copy_namespace["cmd_copy"]
    return state


def arguments() -> SimpleNamespace:
    return SimpleNamespace(
        entry="direct", username="fixture-user", url="", generate=False,
        password="fixture-entry-password", dest="Other/entry", force=True,
        skip_notes=False, skip_url=True,
    )


def test_ordinary_commands_reuse_existing_pings_and_negotiate_only_named_effects(
    ordinary: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    for name, action in (
        ("cmd_add", "add"), ("cmd_set_username", "set-username"),
        ("cmd_remove", "remove"), ("cmd_move", "move"), ("cmd_copy", "copy"),
    ):
        ordinary.requests.clear()
        assert getattr(ordinary.cli, name)(arguments()) == 0
        output = capsys.readouterr()
        assert output.out == "fixture-created\n" and not output.err
        assert [request["action"] for _, request, _ in ordinary.requests] == ["ping", "ping", action]
        request = ordinary.requests[-1][1]
        assert request[selection.FIELD]["root"] == ordinary.root
        assert request[selection.FIELD]["native_default"] is True
        assert request["kpdb"] == ordinary.context.kpdb
        assert all(root == ordinary.root for _, _, root in ordinary.requests)
        if action == "add":
            assert request["password"] == "fixture-entry-password"
        elif action == "set-username":
            assert request["username"] == "fixture-user"
        elif action == "remove":
            assert request["force"] is True
        elif action == "move":
            assert request["dest"] == "Other/entry" and request["force"] is True
        else:
            assert request["dest"] == "Other/entry" and request["skip_url"] is True
            assert "skip_notes" not in request
    assert not ordinary.password_reads
    assert invocation.configuration_root() is None


def test_provider_unlock_prompt_and_effect_keep_original_connector_and_source(
    ordinary: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    ordinary.unlocked = False
    ordinary.unlock_mode = "password"
    ordinary.redirect_on_prompt = True
    assert ordinary.cli.cmd_add(arguments()) == 0
    output = capsys.readouterr()
    assert output.out == "fixture-created\n" and not output.err
    assert [request["action"] for _, request, _ in ordinary.requests] == [
        "ping", "ping", "unlock", "unlock", "add",
    ]
    assert len(ordinary.password_reads) == 1
    assert all(port == 19071 for port, _, _ in ordinary.requests)
    selected = [request[selection.FIELD] for _, request, _ in ordinary.requests[2:]]
    assert all(value["root"] == ordinary.root and value["native_default"] for value in selected)
    assert ordinary.requests[-2][1]["password"] == "fixture-master"
    assert ordinary.requests[-1][1]["password"] == "fixture-entry-password"
    assert invocation.configuration_root() is None
    assert ordinary.cli.send_command({"action": "ping"})["ok"]
    assert ordinary.requests[-1][0] == 19072 and ordinary.requests[-1][2] is None


def test_old_mutation_metadata_keeps_baseline_without_claiming_source_support(
    ordinary: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    advertisements: tuple[object, ...] = (None, [], ["get", "has"], "add")
    for actions in advertisements:
        ordinary.mutation_actions = actions
        assert ordinary.cli.cmd_add(arguments()) == 0
        output = capsys.readouterr()
        assert output.out == "fixture-created\n" and caller.UNSUPPORTED in output.err
        assert selection.FIELD not in ordinary.requests[-1][1]


def test_readiness_uses_captured_vault_while_unscoped_helper_keeps_current_selection(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    first: SimpleNamespace = ordinary.context
    second = SimpleNamespace(
        kpdb="other-database", group="Other", vault_name="second",
        port=19071, sources={"port": "flag"},
    )
    resolutions: list[str] = []

    def resolve() -> SimpleNamespace:
        context = first if not resolutions else second
        resolutions.append(context.kpdb)
        return context

    monkeypatch.setattr(ordinary.config, "resolve_context", resolve)
    ordinary.unlocked = False
    ordinary.other_unlocked = [second.kpdb]
    ordinary.unlock_mode = "password"
    assert ordinary.cli.cmd_add(arguments()) == 0
    output = capsys.readouterr()
    assert output.out == "fixture-created\n" and not output.err
    assert resolutions == [first.kpdb]
    assert [request["action"] for _, request, _ in ordinary.requests] == [
        "ping", "ping", "unlock", "unlock", "add",
    ]
    assert all(request["kpdb"] == first.kpdb for _, request, _ in ordinary.requests)
    assert len(ordinary.password_reads) == 1
    ordinary.unlocked = False
    ordinary.requests.clear()
    assert caller._ensure_unlocked_service()
    assert resolutions[-1] == second.kpdb
    assert [request["action"] for _, request, _ in ordinary.requests] == ["ping", "ping"]
    assert all(request["kpdb"] == second.kpdb for _, request, _ in ordinary.requests)


def test_missing_or_malformed_receipt_does_not_repeat_a_successful_mutation(
    ordinary: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    ordinary.receipt = False
    states: tuple[object, ...] = ("idle", {})
    for warm_state in states:
        ordinary.requests.clear()
        ordinary.warm_state = warm_state
        ordinary.receipt = warm_state != "idle"
        assert ordinary.cli.cmd_add(arguments()) == 0
        output = capsys.readouterr()
        assert output.out == "fixture-created\n" and "not confirmed" in output.err
        assert [request["action"] for _, request, _ in ordinary.requests] == ["ping", "ping", "add"]


def test_uncertain_unlock_stops_before_prompt_or_mutation_and_restores_scope(
    ordinary: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    for mode in ("lost", "invalid", "refused"):
        ordinary.unlocked = False
        ordinary.unlock_mode = mode
        ordinary.requests.clear()
        assert ordinary.cli.cmd_add(arguments()) == 1
        output = capsys.readouterr()
        assert not output.out and "Error:" in output.err
        assert [request["action"] for _, request, _ in ordinary.requests] == ["ping", "ping", "unlock"]
        assert not ordinary.password_reads and invocation.configuration_root() is None


def test_uncertain_or_refused_mutation_never_prints_success_or_replays(
    ordinary: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    for mode in ("lost", "invalid", "refused"):
        ordinary.requests.clear()
        ordinary.mutation_mode = mode
        assert ordinary.cli.cmd_add(arguments()) == 1
        output = capsys.readouterr()
        assert not output.out and "Error:" in output.err and "must-not-print" not in output.err
        assert [request["action"] for _, request, _ in ordinary.requests] == ["ping", "ping", "add"]


def test_unbound_legacy_mutation_can_run_but_cannot_receive_an_unlock_password(
    ordinary: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    ordinary.bound = False
    assert ordinary.cli.cmd_add(arguments()) == 0
    output = capsys.readouterr()
    assert output.out == "fixture-created\n" and caller.UNSUPPORTED in output.err
    ordinary.unlocked = False
    ordinary.requests.clear()
    assert ordinary.cli.cmd_add(arguments()) == 1
    output = capsys.readouterr()
    assert not output.out and "retained unlock" in output.err
    assert [request["action"] for _, request, _ in ordinary.requests] == ["ping", "ping"]
    assert not ordinary.password_reads


def test_pinned_mutation_keeps_retained_unlock_without_inventing_a_source(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("VAULT_READ_TEST_PIN", "immutable-fixture-context")
    ordinary.unlocked = False
    ordinary.unlock_mode = "password"
    assert ordinary.cli.cmd_add(arguments()) == 0
    output = capsys.readouterr()
    assert output.out == "fixture-created\n" and caller.UNSUPPORTED in output.err
    assert ordinary.owner_reads == 0 and len(ordinary.password_reads) == 1
    assert all(selection.FIELD not in request for _, request, _ in ordinary.requests)
    assert all(port == 19071 for port, _, _ in ordinary.requests)


def test_cancelled_entry_prompt_restores_scope_without_sending_a_mutation(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = arguments()
    args.password = None
    monkeypatch.setattr(ordinary.cli, "prompt_password_with_confirm", lambda *_a: None)
    assert ordinary.cli.cmd_add(args) == 1
    output = capsys.readouterr()
    assert not output.out and "no password provided" in output.err
    assert [request["action"] for _, request, _ in ordinary.requests] == ["ping", "ping"]
    assert invocation.configuration_root() is None
    ordinary.endpoint = 19072
    assert ordinary.cli.send_command({"action": "ping"})["ok"]
    assert ordinary.requests[-1][0] == 19072


def test_mutation_scope_does_not_capture_explicit_or_specialized_requests() -> None:
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
    with transport.use_mutation_sender(scoped):
        assert transport.send_command(boundary, {"action": "add"})["ok"]
        for action, options in (
            ("unlock", {}), ("get", {}), ("set-password", {}), ("import-key-create-only", {}),
            ("add", {"_route": transport.CommandRoute()}),
            ("add", {"_context": SimpleNamespace(port=19071)}),
        ):
            with pytest.raises(ExplicitBoundary):
                transport.send_command(boundary, {"action": action}, **options)
        binding = SimpleNamespace(require_unselected=lambda: calls.append("binding"))
        with pytest.raises(ValueError, match="read-only health"):
            transport.send_command(boundary, {"action": "add"}, binding=binding)
    with pytest.raises(ExplicitBoundary):
        transport.send_command(boundary, {"action": "add"})
    assert calls == ["add", "binding"]
