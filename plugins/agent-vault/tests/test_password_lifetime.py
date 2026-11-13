"""Password expiry and status must not inherit daemon persistence."""

from __future__ import annotations

import asyncio
import base64
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_vault import cli, extensions, kek, service, ssh_key
from agent_vault.extensions import ExtensionRegistry
from agent_vault.password_lifetime import CONTRACT

Unlocked = tuple[service.VaultService, str, list[float]]


@pytest.fixture
def unlocked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Unlocked:
    registry = ExtensionRegistry()
    registry._loaded = True
    monkeypatch.setattr(extensions, "_REGISTRY", registry)
    clock = [1000.0]
    monkeypatch.setattr(service.time, "time", lambda: clock[0])
    monkeypatch.setattr(service, "PASSWORD_TTL", 600)
    vault = service.VaultService()
    kpdb = str(tmp_path / "fixture.kdbx")
    vault._default_kpdb = kpdb
    vault.cli._cli_path = "synthetic-keepass"
    vault.cli.set_password(kpdb, "fixture-master")
    vault._password_set_at[kpdb] = clock[0]
    vault.ttl_override = 0
    return vault, kpdb, clock


def test_ping_reports_absolute_expiry_despite_activity_and_persistence(
    unlocked: Unlocked,
) -> None:
    vault, kpdb, clock = unlocked
    vault.cache[(kpdb, "fixture", "password")] = "fixture-value"
    clock[0] += 300
    response = vault.handle_request({"action": "ping"})
    assert response["ttl"] == 999999
    assert response["daemon_persistent"] is True
    assert response["password_lifetime"] == {
        "contract": CONTRACT, "version": 1, "state": "bounded", "remaining_seconds": 300,
    }
    clock[0] += 300
    response = vault.handle_request({"action": "ping"})
    assert response["password_lifetime"]["state"] == "locked"
    assert response["password_lifetime"]["remaining_seconds"] is None
    assert vault.ttl_override == 0
    assert not vault.cli.has_password(kpdb)
    assert vault.cache == {}
    assert kpdb not in vault._password_set_at


def test_unbounded_policy_still_reports_and_obeys_inherited_deadline(
    unlocked: Unlocked, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, kpdb, clock = unlocked
    monkeypatch.setattr(service, "PASSWORD_TTL", 0)
    assert vault.handle_request({"action": "ping"})["password_lifetime"]["state"] == "unbounded"
    vault._password_handoff_deadlines[kpdb] = clock[0] + 30
    status = vault.handle_request({"action": "ping"})["password_lifetime"]
    assert status["state"] == "bounded" and status["remaining_seconds"] == 30
    clock[0] += 30
    assert vault.handle_request({"action": "ping"})["password_lifetime"]["state"] == "locked"
    assert kpdb not in vault._password_handoff_deadlines


def test_missing_timestamp_or_selected_database_does_not_invent_lifetime(
    unlocked: Unlocked,
) -> None:
    vault, kpdb, _clock = unlocked
    vault._password_set_at.clear()
    assert vault.handle_request({"action": "ping"})["password_lifetime"]["state"] == "unknown"
    vault._password_set_at[kpdb] = 1000.0
    vault._default_kpdb = None
    assert vault.handle_request({"action": "ping"})["password_lifetime"]["state"] == "unknown"


def test_idle_server_clears_expired_password_without_a_request(
    unlocked: Unlocked, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, kpdb, clock = unlocked
    observations = []

    class Generation:
        def __init__(self, *_args: object) -> None:
            pass

        async def start(self, _port: int) -> None:
            pass

        async def tick(self) -> None:
            observations.append(vault.cli.has_password(kpdb))
            if len(observations) == 1:
                clock[0] += 600
            else:
                vault._shutdown = True

    async def no_wait(_seconds: float) -> None:
        pass

    monkeypatch.setattr(service, "ServiceGeneration", Generation)
    monkeypatch.setattr(service.asyncio, "sleep", no_wait)
    asyncio.run(service._serve(vault, 0, [], [], None))
    assert observations == [True, False]


@pytest.mark.parametrize("expired", [False, True])
def test_ping_does_not_wait_for_an_inflight_credential_read(
    unlocked: Unlocked, monkeypatch: pytest.MonkeyPatch, expired: bool,
) -> None:
    vault, kpdb, clock = unlocked
    entered, release = threading.Event(), threading.Event()

    def read(_kpdb: str, _entry: str, _field: str) -> str:
        entered.set()
        assert release.wait(3)
        return "fixture-value"

    monkeypatch.setattr(vault.cli, "get_entry", read)
    with ThreadPoolExecutor(max_workers=2) as workers:
        pending = workers.submit(
            vault._handle_admitted_request,
            {"action": "get", "kpdb": kpdb, "entry": "fixture"},
        )
        try:
            assert entered.wait(2)
            if expired:
                clock[0] += 600
            ping = workers.submit(vault._handle_admitted_request, {"action": "ping"})
            response = ping.result(timeout=1)
            assert response["ok"] is True
            assert response["password_lifetime"]["state"] == "unknown"
            assert vault.cli.has_password(kpdb)
        finally:
            release.set()
        assert pending.result(timeout=2)["value"] == "fixture-value"
    if expired:
        vault._check_password_ttl()
        assert not vault.cli.has_password(kpdb)


def test_expiry_waits_for_the_complete_add_including_parent_creation(
    unlocked: Unlocked, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, kpdb, clock = unlocked
    entered, release = threading.Event(), threading.Event()
    calls: list[str] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv[1])
        assert str(kwargs["input"]).startswith("fixture-master\n")
        if argv[1] == "mkdir":
            entered.set()
            assert release.wait(3)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    with ThreadPoolExecutor(max_workers=2) as workers:
        pending = workers.submit(
            vault._handle_admitted_request,
            {
                "action": "add", "kpdb": kpdb, "entry": "Parent/fixture",
                "password": "fixture-entry",
            },
        )
        try:
            assert entered.wait(2)
            clock[0] += 600
            workers.submit(vault._check_password_ttl).result(timeout=1)
            assert vault.cli.has_password(kpdb)
            assert calls == ["mkdir"]
        finally:
            release.set()
        assert pending.result(timeout=2) == {"ok": True, "message": "Entry created"}
    assert calls == ["mkdir", "add"]
    vault._check_password_ttl()
    assert not vault.cli.has_password(kpdb)


@pytest.mark.parametrize(
    "action, timeout",
    [
        ("set-password", 30.0),
        ("identity-key-setup", 5.0),
        ("ssh-key-prepare", 5.0),
        ("ssh-key-read", 5.0),
    ],
)
def test_dispatch_preserves_handler_owned_queue_limits(
    unlocked: Unlocked, monkeypatch: pytest.MonkeyPatch, action: str, timeout: float,
) -> None:
    vault, kpdb, _clock = unlocked
    waits: list[float] = []

    class BusyLock:
        def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
            if blocking:
                assert timeout >= 0
                waits.append(timeout)
            return False

        def release(self) -> None:
            pytest.fail("unacquired lock was released")

        def __enter__(self) -> None:
            pytest.fail("dispatcher bypassed the handler-owned queue deadline")

        def __exit__(self, *_args: object) -> None:
            pytest.fail("unacquired context was exited")

    def no_backend(*_args: object, **_kwargs: object) -> None:
        pytest.fail("contended request reached the credential backend")

    monkeypatch.setattr(vault, "_credential_lock", BusyLock())
    monkeypatch.setattr(vault.cli, "edit_password", no_backend)
    monkeypatch.setattr(vault.cli, "read_identity_key", no_backend)
    monkeypatch.setattr(vault.cli, "create_identity_key", no_backend)
    monkeypatch.setattr(ssh_key, "_crypto", lambda: None)
    result = vault._handle_admitted_request({
        "action": action, "kpdb": kpdb, "entry": "Parent/fixture", "password": "fixture-value",
    })
    assert result["ok"] is False
    if action == "set-password":
        assert result["error"] == "Password mutation expired before the backend write"
    else:
        assert result["code"] == "service_busy"
    assert waits == [timeout]


@pytest.mark.parametrize(
    "action", ["set-password", "identity-key-setup", "ssh-key-prepare", "ssh-key-read"],
)
def test_timed_handler_rechecks_expiry_after_successful_contended_acquisition(
    unlocked: Unlocked, monkeypatch: pytest.MonkeyPatch, action: str,
) -> None:
    vault, kpdb, clock = unlocked
    Path(kpdb).write_bytes(b"fixture database")
    clock[0] += 599.5
    cache_key = (kpdb, "Parent/fixture", "password")
    vault.cache[cache_key] = "fixture-value"
    vault._credential_generations[cache_key] = 123
    inner = threading.RLock()
    held, queued, release = (threading.Event() for _ in range(3))

    class ObservedLock:
        def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
            if blocking and timeout >= 0:
                queued.set()
                return inner.acquire(timeout=timeout)
            return inner.acquire(blocking=blocking)

        def release(self) -> None:
            inner.release()

        def __enter__(self) -> ObservedLock:
            inner.acquire()
            return self

        def __exit__(self, *_args: object) -> None:
            inner.release()

    def hold() -> None:
        with inner:
            held.set()
            assert release.wait(5)

    def no_backend(*_args: object, **_kwargs: object) -> None:
        pytest.fail("expired credential reached backend or persistent-cache work")

    monkeypatch.setattr(vault, "_credential_lock", ObservedLock())
    monkeypatch.setattr(vault.cli, "edit_password", no_backend)
    monkeypatch.setattr(vault.cli, "read_identity_key", no_backend)
    monkeypatch.setattr(vault.cli, "create_identity_key", no_backend)
    monkeypatch.setattr(service, "get_cache", no_backend)
    monkeypatch.setattr(ssh_key, "_crypto", lambda: None)
    with ThreadPoolExecutor(max_workers=2) as workers:
        holder = workers.submit(hold)
        try:
            assert held.wait(2)
            pending = workers.submit(vault._handle_admitted_request, {
                "action": action, "kpdb": kpdb, "entry": "Parent/fixture",
                "password": "fixture-value",
            })
            assert queued.wait(2)
            clock[0] += 1
        finally:
            release.set()
        holder.result(timeout=2)
        response = pending.result(timeout=2)
    assert response["ok"] is False
    if action == "set-password":
        assert response["needs_unlock"] is True
    else:
        assert response["code"] == "needs_unlock"
    assert not vault.cli.has_password(kpdb)
    assert kpdb not in vault._password_set_at
    assert vault.cache == {} and vault._credential_generations == {}


@pytest.mark.parametrize(
    "action", ["git-credential", "seal", "unseal", "kek-list"],
)
def test_independent_actions_do_not_wait_for_keepass_custody(
    unlocked: Unlocked, monkeypatch: pytest.MonkeyPatch, action: str,
) -> None:
    vault, kpdb, _clock = unlocked
    held, release = threading.Event(), threading.Event()
    monkeypatch.setattr(service, "git_credential_action", lambda _request: {"ok": True})
    monkeypatch.setattr(kek, "seal", lambda _name, _data: "fixture-envelope")
    monkeypatch.setattr(kek, "unseal", lambda _name, _data: b"fixture-data")
    monkeypatch.setattr(kek, "list_keks", lambda: ["fixture-key"])

    def hold() -> None:
        with vault._credential_lock:
            held.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=2) as workers:
        holder = workers.submit(hold)
        try:
            assert held.wait(2)
            response = workers.submit(vault._handle_admitted_request, {
                "action": action, "kpdb": kpdb, "name": "fixture-key",
                "data": base64.b64encode(b"fixture-data").decode(),
                "sealed": "fixture-envelope",
            }).result(timeout=1)
            assert response["ok"] is True
        finally:
            release.set()
        holder.result(timeout=2)


def test_gcm_wait_does_not_hold_keepass_custody(
    unlocked: Unlocked, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, kpdb, _clock = unlocked
    entered, release = threading.Event(), threading.Event()
    vault.cache[(kpdb, "fixture", "password")] = "fixture-value"

    def gcm(_request: dict) -> dict:
        entered.set()
        assert release.wait(5)
        return {"ok": True}

    monkeypatch.setattr(service, "git_credential_action", gcm)
    with ThreadPoolExecutor(max_workers=2) as workers:
        pending = workers.submit(
            vault._handle_admitted_request, {"action": "git-credential", "kpdb": kpdb},
        )
        try:
            assert entered.wait(2)
            response = workers.submit(vault._handle_admitted_request, {
                "action": "get", "kpdb": kpdb, "entry": "fixture",
            }).result(timeout=1)
            assert response["value"] == "fixture-value"
        finally:
            release.set()
        assert pending.result(timeout=2)["ok"] is True


def test_extension_credential_action_retains_complete_custody(
    unlocked: Unlocked,
) -> None:
    vault, kpdb, clock = unlocked
    entered, release = threading.Event(), threading.Event()

    def operation(*_args: object) -> dict:
        entered.set()
        assert release.wait(5)
        return {"ok": vault.cli.has_password(kpdb)}

    extensions.get_registry().register_action("fixture-extension", operation)
    with ThreadPoolExecutor(max_workers=2) as workers:
        pending = workers.submit(vault._handle_admitted_request, {
            "action": "fixture-extension", "kpdb": kpdb,
        })
        try:
            assert entered.wait(2)
            clock[0] += 600
            workers.submit(vault._check_password_ttl).result(timeout=1)
            assert vault.cli.has_password(kpdb)
        finally:
            release.set()
        assert pending.result(timeout=2)["ok"] is True
    vault._check_password_ttl()
    assert not vault.cli.has_password(kpdb)


@pytest.mark.parametrize(
    "lifetime, expected",
    [
        (None, "unknown (service did not report it)"),
        ({"contract": CONTRACT, "version": 2}, "unknown (unsupported status contract)"),
        ({"contract": CONTRACT, "version": 1, "state": "locked"}, "locked"),
        ({"contract": CONTRACT, "version": 1, "state": "unbounded"}, "unbounded"),
        ({"contract": CONTRACT, "version": 1, "state": "bounded", "remaining_seconds": 42}, "42s"),
        ({"contract": CONTRACT, "version": 1, "state": "unknown"}, "unknown"),
    ],
)
def test_cli_separates_password_lifetime_and_legacy_daemon_ttl(
    lifetime: dict | None,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = {
        "ok": True, "pid": 123, "ttl": 999999, "cached": 0, "cli": "unlocked",
    }
    if lifetime is not None:
        response.update(password_lifetime=lifetime, daemon_persistent=True)
    monkeypatch.setattr(cli, "send_command", lambda _request: response)
    assert cli.cmd_ping(None) == 0
    output = capsys.readouterr().out
    assert f"password lifetime={expected}" in output
    assert ("daemon idle TTL 999999s" if lifetime is None else "daemon=persistent") in output
