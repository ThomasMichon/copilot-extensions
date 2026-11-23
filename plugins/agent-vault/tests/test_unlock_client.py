"""Explicit unlock keeps its selected source and transport through prompting."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_vault import cli, config, extensions
from agent_vault.cache_warm_selection import FIELD, RECEIPT_FIELD
from agent_vault.cache_warm_status import WarmState
from agent_vault.invocation import configuration_root, uses_native_default


@dataclass
class Peer:
    root: Path
    context: config.ResolvedVault
    unlocked: bool = True
    versions: object = field(default_factory=lambda: [1])
    mode: str = ""
    warming: object = field(default_factory=lambda: WarmState("queued").describe())
    requests: list[dict[str, Any]] = field(default_factory=list)
    password_reads: list[str] = field(default_factory=list)
    resolutions: int = 0
    provenance: list[bool] = field(default_factory=list)
    accepting_work: bool = True
    unlock_reply: dict[str, Any] | None = None

    def _warming_reply(self, request: dict[str, Any]) -> object:
        if not isinstance(self.warming, dict):
            return self.warming
        result = dict(self.warming)
        if FIELD in request and self.mode not in {"status-dropped", "receipt-old"}:
            result.setdefault(RECEIPT_FIELD, 1)
        return result

    def send(
        self, request: dict[str, Any], _host: str, _port: int, _timeout: float | None,
    ) -> dict[str, Any] | None:
        self.requests.append(dict(request))
        if request["action"] == "ping":
            if self.mode == "unavailable" or self.mode == "status-lost" and FIELD in request:
                return None
            return {
                "ok": True,
                "accepting_work": self.accepting_work
                and not (self.mode == "status-transition" and FIELD in request),
                "pid": 123, "cached": 0, "ttl": 30, "cache_warm": self._warming_reply(request),
                "unlocked_vaults": [self.context.kpdb] if self.unlocked else [],
                "cli": "unlocked" if self.unlocked else "locked",
                **({"cache_warm_selection_versions": self.versions}
                   if self.versions is not None
                   and not (self.mode == "status-rollback" and FIELD in request) else {}),
            }
        assert request["action"] == "unlock"
        if self.unlock_reply is not None:
            return dict(self.unlock_reply)
        if self.mode == "lost":
            return None
        if self.mode == "raised":
            raise OSError("private transport diagnostic")
        if self.mode == "refused":
            return {"ok": False, "error": "private rejection", "retryable": True}
        if not self.unlocked and not request.get("password") and not request.get("prompt"):
            return {"ok": False, "needs_unlock": True}
        self.unlocked = True
        return {
            "ok": True,
            **({"cache_warm": self._warming_reply(request)}
               if self.versions == [1] and FIELD in request else {}),
        }


@pytest.fixture
def peer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Peer:
    selected, caller = tmp_path / "selected", tmp_path / "foreign"
    selected.mkdir()
    caller.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.delenv("VAULT_UNLOCK_TERMINAL", raising=False)
    context = config.ResolvedVault("first", "synthetic-database", "Example", 19371, {"port": "flag"})
    result = Peer(selected, context)

    def resolve(**_options: object) -> config.ResolvedVault:
        result.resolutions += 1
        result.provenance.append(uses_native_default())
        assert configuration_root() == str(selected)
        return context

    def read_password(prompt: str) -> str:
        result.password_reads.append(prompt)
        return "fixture-master"

    registry = extensions.ExtensionRegistry()
    registry._loaded = True
    monkeypatch.setattr(extensions, "_REGISTRY", registry)
    monkeypatch.setattr(config, "configuration_selection", lambda **_kw: (str(selected), False))
    monkeypatch.setattr(config, "resolve_context", resolve)
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(cli, "_discover_endpoint", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli, "IS_WINDOWS", True)
    monkeypatch.setattr(cli, "IS_WSL", False)
    monkeypatch.setattr(cli, "_send_tcp", result.send)
    monkeypatch.setattr(cli, "_has_controlling_tty", lambda: True)
    monkeypatch.setattr(cli, "_read_password_from_tty", read_password)
    monkeypatch.setattr(
        cli, "prompt_password", lambda *_a: pytest.fail("unexpected GUI prompt"),
    )
    return result


def test_already_unlocked_qualified_service_receives_one_scoped_unlock_without_prompt(
    peer: Peer, capsys: pytest.CaptureFixture[str],
) -> None:
    caller = Path.cwd()
    assert cli.cmd_unlock(SimpleNamespace()) == 0
    assert [request["action"] for request in peer.requests] == ["ping", "unlock"]
    assert peer.requests[-1][FIELD]["root"] == str(peer.root)
    assert peer.requests[-1][FIELD]["native_default"] is False
    assert peer.requests[-1]["kpdb"] == peer.context.kpdb
    assert "password" not in peer.requests[-1]
    assert peer.resolutions == 1 and not peer.password_reads
    output = capsys.readouterr()
    assert output.out == "Vault available\n" and output.err == ""
    assert Path.cwd() == caller


def test_native_default_provenance_is_used_for_resolution_and_sent_without_demotion(
    peer: Peer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config, "configuration_selection", lambda **_kw: (str(peer.root), True),
    )
    assert cli.cmd_unlock(SimpleNamespace()) == 0
    assert peer.provenance == [True]
    assert peer.requests[-1][FIELD]["native_default"] is True
    assert not uses_native_default()


@pytest.mark.parametrize("versions", [None, [], [True], ["1"], [2]])
def test_old_or_unsupported_already_unlocked_service_keeps_legacy_effects_and_warns(
    peer: Peer, capsys: pytest.CaptureFixture[str], versions: object,
) -> None:
    peer.versions = versions
    assert cli.cmd_unlock(SimpleNamespace()) == 0
    assert [request["action"] for request in peer.requests] == ["ping"]
    assert not peer.password_reads
    output = capsys.readouterr()
    assert output.out == "Vault available\n"
    assert "legacy warming policy" in output.err


@pytest.mark.parametrize("versions", [[1], None])
def test_known_provider_miss_and_terminal_prompt_share_the_same_route_and_context(
    peer: Peer, versions: object,
) -> None:
    peer.unlocked = False
    peer.versions = versions
    assert cli.cmd_unlock(SimpleNamespace()) == 0
    attempts = [request for request in peer.requests if request["action"] == "unlock"]
    assert len(attempts) == 2 and len(peer.password_reads) == 1
    assert "password" not in attempts[0] and attempts[1]["password"] == "fixture-master"
    assert all(request["kpdb"] == peer.context.kpdb for request in attempts)
    assert all((FIELD in request) is (versions == [1]) for request in attempts)
    if versions == [1]:
        assert attempts[0][FIELD] == attempts[1][FIELD]
    assert peer.resolutions == 1


@pytest.mark.parametrize("mode", ["lost", "raised", "refused"])
def test_uncertain_or_refused_unlock_never_falls_through_to_another_prompt(
    peer: Peer, capsys: pytest.CaptureFixture[str], mode: str,
) -> None:
    peer.unlocked = False
    peer.mode = mode
    assert cli.cmd_unlock(SimpleNamespace()) == 1
    assert [request["action"] for request in peer.requests] == ["ping", "unlock"]
    assert not peer.password_reads
    output = capsys.readouterr()
    assert output.out == "" and "private" not in output.err
    assert ("unknown" if mode != "refused" else "refused") in output.err


@pytest.mark.parametrize("reply", [{}, {"needs_unlock": True}, {"ok": 0}, {"ok": "false"}])
@pytest.mark.parametrize("terminal", [False, True])
def test_non_boolean_unlock_outcome_never_succeeds_or_starts_another_prompt(
    peer: Peer, capsys: pytest.CaptureFixture[str], reply: dict[str, Any], terminal: bool,
) -> None:
    peer.unlock_reply = reply
    assert cli.cmd_unlock(SimpleNamespace(terminal=terminal)) == 1
    output = capsys.readouterr()
    assert output.out == "" and "unlock outcome is unknown" in output.err
    assert len(peer.requests) == 2
    assert len(peer.password_reads) == (1 if terminal else 0)


def test_reachable_nonaccepting_service_remains_visible_but_cannot_unlock(
    peer: Peer, capsys: pytest.CaptureFixture[str],
) -> None:
    peer.accepting_work = False
    assert cli.cmd_ping(None) == 0
    output = capsys.readouterr()
    assert "cache warm=unknown" in output.out and "not running" not in output.out
    assert cli.cmd_unlock(SimpleNamespace()) == 1
    assert "not accepting unlock work" in capsys.readouterr().err
    assert all(request["action"] == "ping" for request in peer.requests)
    assert not peer.password_reads


@pytest.mark.parametrize("unlocked", [False, True])
def test_unbound_extension_keeps_readonly_status_without_sending_credentials(
    peer: Peer, capsys: pytest.CaptureFixture[str], unlocked: bool,
) -> None:
    peer.unlocked = unlocked
    peer.warming = {
        **WarmState("complete", selected=1, cached=1).describe(), RECEIPT_FIELD: 1,
    }

    def unbound(
        request: dict[str, Any], timeout: float | None, _context: object,
    ) -> dict[str, Any] | None:
        return peer.send(request, "fixture", 19371, timeout)

    extensions.get_registry().register_transport(unbound, before_builtin=True)
    assert cli.cmd_ping(None) == 0
    assert "cache warm=unknown" in capsys.readouterr().out
    assert cli.cmd_unlock(SimpleNamespace()) == (0 if unlocked else 1)
    assert "cannot retain" in capsys.readouterr().err
    assert all(request["action"] == "ping" for request in peer.requests)
    assert not peer.password_reads


def test_explicit_terminal_with_unavailable_service_never_reads_a_password_or_starts_it(
    peer: Peer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    peer.mode = "unavailable"
    monkeypatch.setattr(cli, "ensure_service", lambda: pytest.fail("terminal started service"))
    assert cli.cmd_unlock(SimpleNamespace(terminal=True)) == 1
    assert [request["action"] for request in peer.requests] == ["ping"]
    assert not peer.password_reads


@pytest.mark.parametrize("warming", [
    None, {"state": "complete"},
    WarmState("not-queued", "configuration-unavailable").describe(),
    WarmState("disabled").describe(),
    {**WarmState("complete", selected=1, cached=1).describe(), RECEIPT_FIELD: True},
])
def test_unlock_success_does_not_claim_failed_or_missing_warming_acceptance(
    peer: Peer, capsys: pytest.CaptureFixture[str], warming: object,
) -> None:
    peer.warming = warming
    assert cli.cmd_unlock(SimpleNamespace()) == 0
    output = capsys.readouterr()
    assert output.out == "Vault available\n"
    assert "Automatic cache warming not confirmed" in output.err
    assert str(peer.root) not in output.err
    assert len(peer.requests) == 2


def test_context_aware_provider_without_receipt_is_not_claimed_as_confirmed_warming(
    peer: Peer, capsys: pytest.CaptureFixture[str],
) -> None:
    peer.mode = "receipt-old"
    assert cli.cmd_unlock(SimpleNamespace()) == 0
    assert "Automatic cache warming not confirmed: unknown" in capsys.readouterr().err
    assert len(peer.requests) == 2


@pytest.mark.parametrize("mode", ["", "lost"])
def test_unlock_scope_is_released_before_later_independent_requests(
    peer: Peer, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    peer.mode = mode
    assert cli.cmd_unlock(SimpleNamespace()) == (1 if mode else 0)
    peer.mode = ""
    monkeypatch.setattr(config, "resolve_context", lambda **_kw: peer.context)
    assert cli.send_command({"action": "unlock"}) is not None
    assert FIELD not in peer.requests[-1]


@pytest.mark.parametrize("versions", [[1], None])
def test_ping_projects_current_source_or_reports_unknown_instead_of_resident_progress(
    peer: Peer, capsys: pytest.CaptureFixture[str], versions: object,
) -> None:
    peer.versions = versions
    peer.warming = WarmState("complete", selected=1, cached=1).describe()
    assert cli.cmd_ping(None) == 0
    output = capsys.readouterr()
    assert f"cache warm={'complete' if versions == [1] else 'unknown'}" in output.out
    assert len(peer.requests) == (2 if versions == [1] else 1)
    assert all(request["action"] == "ping" for request in peer.requests)
    if versions == [1]:
        assert peer.requests[-1][FIELD]["root"] == str(peer.root)
    assert peer.resolutions == 1 and not peer.password_reads


@pytest.mark.parametrize("mode", [
    "status-lost", "status-rollback", "status-dropped", "status-transition",
])
def test_ping_never_retries_or_reuses_resident_progress_after_peer_loss_or_rollback(
    peer: Peer, capsys: pytest.CaptureFixture[str], mode: str,
) -> None:
    peer.mode = mode
    peer.warming = WarmState("complete", selected=1, cached=1).describe()
    assert cli.cmd_ping(None) == (1 if mode == "status-lost" else 0)
    output = capsys.readouterr()
    assert "cache warm=complete" not in output.out
    if mode != "status-lost":
        assert "cache warm=unknown" in output.out
    assert len(peer.requests) == 2 and not peer.password_reads
