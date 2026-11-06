"""Fixed-alias custody survives primary endpoint changes."""

import asyncio
import json
import os
import stat
from types import SimpleNamespace

import pytest

from agent_vault import service_generation as generation
from agent_vault import service_unix_alias as alias


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(alias, "installation_id", lambda: "fixture")
    monkeypatch.setattr(alias, "socket_identity", lambda _: (1, 2))
    monkeypatch.setattr(alias, "birth_identity", lambda _: "native-birth")
    monkeypatch.setattr(alias.os, "getuid", lambda: 10, raising=False)
    monkeypatch.setattr(alias.os, "O_NOFOLLOW", getattr(os, "O_NOFOLLOW", 0), raising=False)
    monkeypatch.setattr(alias.os, "O_NONBLOCK", getattr(os, "O_NONBLOCK", 0), raising=False)
    observed = alias.os.fstat
    monkeypatch.setattr(alias.os, "fstat", lambda fd: SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600, st_uid=10, st_size=observed(fd).st_size,
    ))
    result = alias.UnixAlias(tmp_path, str(tmp_path / "fixed.sock"))
    result.publish()
    return result


@pytest.mark.parametrize("state", ("dead", "reused", "matching", "unknown"))
def test_alias_owner_is_independent_of_primary_generation(owner, monkeypatch, state):
    calls = []
    monkeypatch.setattr(alias, "identity_state", lambda pid, birth: (
        calls.append((pid, birth)) or state
    ))
    assert owner.dead_identity() == ((1, 2) if state in {"dead", "reused"} else None)
    assert calls == [(os.getpid(), "native-birth")]


def test_first_fixed_bind_creates_its_absent_run_directory(owner):
    root = owner.record.parent / "absent-run"
    first = alias.UnixAlias(root, owner.path)
    assert not root.exists()
    first.publish()
    assert first.read()["pid"] == os.getpid()


@pytest.mark.parametrize("change", ("installation", "path", "birth", "pid", "identity", "oversize", "json"))
def test_untrusted_or_replaced_alias_record_cannot_authorize_unlink(
    owner, monkeypatch, change,
):
    payload = json.loads(owner.record.read_text())
    if change == "identity":
        monkeypatch.setattr(alias, "socket_identity", lambda _: (3, 4))
    elif change == "oversize":
        owner.record.write_text(" " * 4097)
    elif change == "json":
        owner.record.write_text("{")
    else:
        payload[change] = {"installation": "foreign", "path": "foreign", "birth": "", "pid": True}[change]
        owner.record.write_text(json.dumps(payload))

    def unexpected(*_):
        pytest.fail("Invalid alias custody reached the native owner probe")

    monkeypatch.setattr(alias, "identity_state", unexpected)
    assert owner.dead_identity() is None


@pytest.mark.parametrize("mode,uid", (
    (stat.S_IFLNK | 0o600, 10), (stat.S_IFREG | 0o644, 10),
    (stat.S_IFREG | 0o600, 11),
))
def test_alias_record_requires_a_private_regular_file(owner, monkeypatch, mode, uid):
    monkeypatch.setattr(alias.os, "fstat", lambda _: SimpleNamespace(
        st_mode=mode, st_uid=uid, st_size=100,
    ))
    assert owner.read() is None


def test_legacy_fixed_primary_capture_survives_exit_but_not_socket_replacement(owner, monkeypatch):
    owner.record.unlink()
    owner.capture_previous(123)
    monkeypatch.setattr(alias, "identity_state", lambda pid, birth: (
        "dead" if (pid, birth) == (123, "native-birth") else "matching"
    ))
    assert owner.dead_identity() == (1, 2)
    owner.record.write_text("{")
    assert owner.dead_identity() is None
    owner.record.unlink()
    monkeypatch.setattr(alias, "socket_identity", lambda _: (3, 4))
    assert owner.dead_identity() is None


def test_old_cleanup_preserves_a_replaced_alias_and_current_discovery(tmp_path, monkeypatch):
    from agent_vault import service

    monkeypatch.setattr(service, "run_dir", lambda: tmp_path)
    monkeypatch.setattr(generation, "home_dir", lambda: tmp_path)
    events = []
    controller = generation.ServiceGeneration(SimpleNamespace(), SimpleNamespace(), [], [])
    controller.owned_sockets = {"old.sock": (1, 2), "fixed.sock": (3, 4)}
    monkeypatch.setattr(generation, "socket_identity", lambda path: (
        (1, 2) if path == "old.sock" else (5, 6)
    ))
    monkeypatch.setattr(service, "PID_FILE", str(tmp_path / "pid"))
    (tmp_path / "pid").write_text("123")
    monkeypatch.setattr(service, "clear_endpoint", lambda _: events.append("clear-own-endpoint"))
    monkeypatch.setattr(generation.os, "unlink", lambda path: events.append(path))
    controller.cleanup()
    assert events == ["clear-own-endpoint", "old.sock"]
    assert (tmp_path / "pid").read_text() == "123"


@pytest.mark.parametrize("current", (False, True))
def test_alias_publication_rechecks_selection_under_the_publication_lease(
    tmp_path, monkeypatch, current,
):
    from agent_vault import service

    monkeypatch.setattr(service, "run_dir", lambda: tmp_path)
    monkeypatch.setattr(generation, "home_dir", lambda: tmp_path)
    controller = generation.ServiceGeneration(SimpleNamespace(), SimpleNamespace(), [], [])
    monkeypatch.setattr(generation, "selected", lambda: current)
    monkeypatch.setattr(generation, "read_endpoint", lambda _: SimpleNamespace(
        pid=os.getpid(), installation_id=generation.installation_id(),
    ))
    events = []

    async def bind():
        events.append("bind")

    monkeypatch.setattr(controller, "_bind_legacy_aliases", bind)
    assert asyncio.run(controller._legacy_aliases()) is current
    assert events == (["bind"] if current else [])


@pytest.mark.parametrize("unavailable", ("pipe", "tcp"))
def test_partial_restoration_retries_only_unbound_transports(tmp_path, monkeypatch, unavailable):
    from agent_vault import service

    monkeypatch.setattr(service, "run_dir", lambda: tmp_path)
    monkeypatch.setattr(generation, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(service, "PIPE_PATH", "fixed.pipe")
    monkeypatch.setattr(service, "pipe_absent", lambda _: True)
    monkeypatch.setattr(generation, "selected", lambda: True)
    monkeypatch.setattr(generation, "read_endpoint", lambda _: SimpleNamespace(
        pid=os.getpid(), installation_id=generation.installation_id(),
    ))
    controller = generation.ServiceGeneration(SimpleNamespace(), SimpleNamespace(
        callback=lambda _: object(),
    ), [], [])
    controller.pipe = "suffixed.pipe"
    controller.port, controller.requested_port = 123, 456
    calls = {"pipe": 0, "tcp": 0}

    async def pipe(*_):
        calls["pipe"] += 1
        return [] if unavailable == "pipe" and calls["pipe"] == 1 else [object()]

    async def tcp(port):
        assert port == 456
        calls["tcp"] += 1
        if unavailable == "tcp" and calls["tcp"] == 1:
            raise OSError("fixture port occupied")

    monkeypatch.setattr(service, "start_pipe_server", pipe)
    monkeypatch.setattr(controller, "_tcp", tcp)
    assert not asyncio.run(controller._legacy_aliases())
    assert asyncio.run(controller._legacy_aliases())
    assert calls == {"pipe": 2 if unavailable == "pipe" else 1, "tcp": 2 if unavailable == "tcp" else 1}


@pytest.mark.parametrize("available,error,expected", (
    (True, 0, False), (False, 2, True), (False, 5, False),
    (False, 121, False), (False, 231, False),
))
def test_named_pipe_absence_requires_native_not_found(monkeypatch, available, error, expected):
    from agent_vault import winpipe

    monkeypatch.setattr(winpipe, "IS_WINDOWS", True)
    monkeypatch.setattr(winpipe, "_kernel32", lambda: SimpleNamespace(
        WaitNamedPipeW=lambda path, timeout: available,
        GetLastError=lambda: error,
    ))
    assert winpipe.pipe_absent("fixture.pipe") is expected


def test_writer_admission_waits_for_serialized_pid_publication(tmp_path, monkeypatch):
    from agent_vault import service
    from single_instance_lease import AlreadyRunningError

    monkeypatch.setattr(service, "run_dir", lambda: tmp_path)
    monkeypatch.setattr(generation, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(generation, "selected", lambda: True)
    monkeypatch.setattr(generation, "read_endpoint", lambda _: SimpleNamespace(
        pid=os.getpid(), installation_id=generation.installation_id(),
    ))
    state = {"blocked": True, "held": False}
    events = []

    class Publication:
        def acquire(self):
            if state["blocked"]:
                raise AlreadyRunningError(tmp_path / "publication.lock", 123)
            state["held"] = True

        def release(self):
            state["held"] = False

    vault = SimpleNamespace(_runtime_can_work=False)
    controller = generation.ServiceGeneration(vault, SimpleNamespace(), [], [])
    controller.publication = Publication()
    controller.has_writer = True

    def publish():
        assert state["held"] and not vault._runtime_can_work
        events.append("pid")

    monkeypatch.setattr(controller, "_write_pid", publish)
    monkeypatch.setattr(service, "resolve_context", lambda: SimpleNamespace(
        kpdb=None, group=None, vault_name=None,
    ))
    monkeypatch.setattr(service, "get_registry", lambda: SimpleNamespace(
        run_startup=lambda *_: events.append("startup"),
    ))
    asyncio.run(controller.tick())
    assert not vault._runtime_can_work and not controller.hooks_started and not events
    state["blocked"] = False
    asyncio.run(controller.tick())
    assert vault._runtime_can_work and controller.hooks_started
    assert events == ["pid", "startup"]
