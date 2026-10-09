"""Decision/state contracts without claiming or signaling any real process."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from zdd.singleton_manager import SingletonManager, UnmanagedDaemonError
from zdd.singleton_state import LEASE_ENV, ManagerState, ProcessIdentity, StateStore


@dataclass
class FakeReference:
    identity: ProcessIdentity
    live: Callable[[], bool]
    closed: bool = False

    def alive(self) -> bool:
        return self.live()

    def close(self) -> None:
        assert not self.closed
        self.closed = True


class FakeBackend:
    boot_id = "test-boot"
    owner = ProcessIdentity(10, "100", boot_id)

    def __init__(self) -> None:
        self.identities = {
            20: ProcessIdentity(20, "200", self.boot_id),
            30: ProcessIdentity(30, "300", self.boot_id),
        }
        self.live = {20: True, 30: False}
        self.descendants = {20, 30}
        self.references: list[FakeReference] = []
        self.claimed = False
        self.cleaned = False

    def claim_tree(self) -> None:
        self.claimed = True

    def identify(self, pid: int) -> ProcessIdentity | None:
        return self.identities.get(pid)

    def pid_is_alive(self, pid: int) -> bool:
        return self.live.get(pid, False)

    def open_process(self, identity: ProcessIdentity) -> FakeReference | None:
        if self.identify(identity.pid) != identity or not self.live.get(identity.pid, False):
            return None
        reference = FakeReference(
            identity, lambda: (
                self.live.get(identity.pid, False) and self.identify(identity.pid) == identity
            ),
        )
        self.references.append(reference)
        return reference

    def owns(self, reference: FakeReference) -> bool:
        return reference.identity.pid in self.descendants

    def reap_zombies(self, watched_pid: int | None = None) -> None:
        pass

    def cleanup(self) -> None:
        self.cleaned = True


@dataclass
class FakeChild:
    pid: int = 20
    code: int | None = None

    def poll(self) -> int | None:
        return self.code


@dataclass
class FakeLease:
    fd: int = 50
    closed: bool = False
    prepared: bool = False

    def close(self) -> None:
        self.closed = True

    def prepare_exec(self) -> None:
        self.prepared = True


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.routing = tmp_path / "routing"
        self.routing.mkdir()
        self.state = tmp_path / "state"
        self.backend = FakeBackend()
        self.lease = FakeLease()
        self.ticks = 0.0
        self.spawns = 0
        self.on_sleep: Callable[[], None] = lambda: self.backend.live.update({20: False})

    def route(self, pid: int, token: str | None) -> None:
        self.routing.joinpath("active.json").write_text(json.dumps({
            "active": {"bind": "127.0.0.1", "port": 1234, "pid": pid,
                       "process_start_time": token},
        }))

    def spawn(self) -> FakeChild:
        self.spawns += 1
        return FakeChild()

    def sleep(self, seconds: float) -> None:
        self.ticks += seconds
        self.on_sleep()

    def manager(self, **kwargs) -> SingletonManager:
        return SingletonManager(
            self.routing, self.spawn, manager_state_dir=self.state,
            backend=self.backend, lease_factory=lambda path: self.lease,
            sleep=self.sleep, clock=lambda: self.ticks,
            successor_wait_s=0.2, poll_interval=0.1, **kwargs,
        )


def test_crash_is_bounded_and_closes_owned_resources(tmp_path: Path) -> None:
    world = World(tmp_path)
    result = world.manager().run()
    assert result.exit_code != 0
    assert result.last_watched_pid == 20
    assert world.spawns == 1
    assert world.ticks <= 0.5
    assert world.backend.cleaned and world.lease.closed
    assert all(reference.closed for reference in world.backend.references)


def test_cutover_adopts_published_descendant_without_respawning(tmp_path: Path) -> None:
    world = World(tmp_path)

    def advance() -> None:
        if world.ticks < 0.2:
            world.backend.live.update({20: False, 30: True})
            world.route(30, "300")
        else:
            world.backend.live[30] = False

    world.on_sleep = advance
    result = world.manager().run()
    assert world.spawns == 1
    assert result.last_watched_pid == 30
    saved = StateStore(world.state).read()
    assert saved is not None
    assert saved.watched.start_time == "300"
    assert world.backend.cleaned


@pytest.mark.parametrize("token,owned", [(None, True), ("999", True), ("300", False)])
def test_unverified_successors_are_never_adopted(
    tmp_path: Path, token: str | None, owned: bool,
) -> None:
    world = World(tmp_path)

    def advance() -> None:
        world.backend.live.update({20: False, 30: True})
        world.route(30, token)
        if not owned:
            world.backend.descendants.discard(30)

    world.on_sleep = advance
    result = world.manager().run()
    assert result.last_watched_pid == 20
    assert world.spawns == 1
    assert all(reference.closed for reference in world.backend.references)


def test_post_exec_recovers_watched_identity_without_spawn(tmp_path: Path) -> None:
    world = World(tmp_path)
    StateStore(world.state).write(ManagerState(
        world.backend.owner, world.backend.identities[20],
    ))
    result = world.manager().run()
    assert world.spawns == 0
    assert result.last_watched_pid == 20


def test_post_exec_discovers_successor_instead_of_spawning(tmp_path: Path) -> None:
    world = World(tmp_path)
    StateStore(world.state).write(ManagerState(
        world.backend.owner, world.backend.identities[20], "discovering", 0.15,
    ))
    world.backend.live.update({20: False, 30: True})
    world.route(30, "300")
    world.on_sleep = lambda: world.backend.live.update({30: False})
    result = world.manager().run()
    assert world.spawns == 0
    assert result.last_watched_pid == 30


def test_exec_discovery_retains_original_deadline(tmp_path: Path) -> None:
    world = World(tmp_path)
    world.ticks = 100.0
    StateStore(world.state).write(ManagerState(
        world.backend.owner, world.backend.identities[20], "discovering", 1.0,
    ))
    result = world.manager().run()
    assert result.reason == "no verified successor"
    assert world.ticks == 100.0
    assert world.spawns == 0


@pytest.mark.parametrize("token", [None, "300"])
def test_live_foreign_route_blocks_bootstrap(tmp_path: Path, token: str | None) -> None:
    world = World(tmp_path)
    world.backend.live[30] = True
    world.backend.descendants.remove(30)
    world.route(30, token)
    with pytest.raises(UnmanagedDaemonError, match="live route"):
        world.manager().run()
    assert world.spawns == 0
    assert world.backend.cleaned and world.lease.closed


def test_state_from_old_manager_does_not_adopt_surviving_incumbent(tmp_path: Path) -> None:
    world = World(tmp_path)
    StateStore(world.state).write(ManagerState(
        ProcessIdentity(9, "90", world.backend.boot_id), world.backend.identities[20],
    ))
    with pytest.raises(UnmanagedDaemonError, match="survived"):
        world.manager().run()
    assert world.spawns == 0


def test_fresh_bootstrap_does_not_adopt_a_route_without_manager_provenance(tmp_path: Path) -> None:
    world = World(tmp_path)
    world.route(20, "200")
    with pytest.raises(UnmanagedDaemonError, match="live route"):
        world.manager().run()
    assert world.spawns == 0
    assert all(reference.closed for reference in world.backend.references)


def test_reused_persisted_pid_discovers_without_resetting_baseline(tmp_path: Path) -> None:
    world = World(tmp_path)
    StateStore(world.state).write(ManagerState(
        world.backend.owner, ProcessIdentity(20, "199", world.backend.boot_id),
    ))
    result = world.manager().run()
    assert world.spawns == 0
    assert result.last_watched_pid == 20
    saved = StateStore(world.state).read()
    assert saved is not None and saved.watched.start_time == "199"


def test_update_is_polled_before_child_exit_and_preserves_lease(tmp_path: Path) -> None:
    world = World(tmp_path)
    seen: list[tuple[str, list[str], dict[str, str]]] = []

    def exec_image(executable: str, argv: list[str], env: dict[str, str]) -> None:
        seen.append((executable, argv, env))
        raise OSError("injected exec failure")

    with pytest.raises(OSError, match="exec failure"):
        world.manager(
            resolve_update=lambda: ["/new/python", "-m", "daemon_manager"], execve=exec_image,
        ).run()
    assert seen[0][2][LEASE_ENV] == "50"
    assert world.lease.prepared
    assert world.ticks == 0
    assert StateStore(world.state).read() is not None
    assert world.backend.cleaned and world.lease.closed


def test_invalid_state_fails_explicitly_without_spawn(tmp_path: Path) -> None:
    world = World(tmp_path)
    world.state.mkdir()
    world.state.joinpath("manager.json").write_text('{"schema_version": 99}')
    with pytest.raises(ValueError, match="schema"):
        world.manager().run()
    assert world.spawns == 0
    assert world.backend.cleaned and world.lease.closed


def test_state_write_failure_closes_pending_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(tmp_path)

    def fail_write(store: StateStore, state: ManagerState) -> None:
        raise OSError("injected state failure")

    monkeypatch.setattr(StateStore, "write", fail_write)
    with pytest.raises(OSError, match="state failure"):
        world.manager().run()
    assert all(reference.closed for reference in world.backend.references)
    assert world.backend.cleaned and world.lease.closed


@pytest.mark.parametrize("code", [0, 7])
def test_child_exit_without_successor_is_a_service_failure(
    tmp_path: Path, code: int, caplog: pytest.LogCaptureFixture,
) -> None:
    world = World(tmp_path)
    world.backend.live[20] = False
    world.spawn = lambda: FakeChild(code=code)
    with caplog.at_level("INFO", logger="zdd"):
        result = world.manager().run()
    assert result.exit_code == (1 if code == 0 else code)
    assert f"child code {code}" in caplog.text
    assert result.reason == "no verified successor"


def test_missing_spawn_identity_fails_closed(tmp_path: Path) -> None:
    world = World(tmp_path)
    del world.backend.identities[20]
    with pytest.raises(RuntimeError, match="identity could not be established"):
        world.manager().run()
    assert world.backend.cleaned and world.lease.closed
    assert StateStore(world.state).read() is None


def test_unowned_spawn_never_publishes_manager_ownership(tmp_path: Path) -> None:
    world = World(tmp_path)
    world.backend.descendants.remove(20)
    with pytest.raises(UnmanagedDaemonError, match="spawn callback"):
        world.manager().run()
    assert all(reference.closed for reference in world.backend.references)
    assert StateStore(world.state).read() is None


def test_spawn_ownership_probe_error_closes_pending_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(tmp_path)

    def fail_probe(reference: FakeReference) -> bool:
        raise OSError("injected ownership failure")

    monkeypatch.setattr(world.backend, "owns", fail_probe)
    with pytest.raises(OSError, match="ownership failure"):
        world.manager().run()
    assert all(reference.closed for reference in world.backend.references)
    assert world.backend.cleaned and world.lease.closed


@pytest.mark.parametrize("contents", ["{broken", "null", '{"active": {"pid": 20}}'])
def test_corrupt_routing_cannot_authorize_fresh_spawn(tmp_path: Path, contents: str) -> None:
    world = World(tmp_path)
    world.routing.joinpath("active.json").write_text(contents)
    with pytest.raises(ValueError):
        world.manager().run()
    assert world.spawns == 0
    assert world.backend.cleaned and world.lease.closed


def test_live_previous_also_blocks_fresh_spawn(tmp_path: Path) -> None:
    world = World(tmp_path)
    world.routing.joinpath("active.json").write_text(json.dumps({
        "previous": {"bind": "127.0.0.1", "port": 1234, "pid": 20,
                     "process_start_time": "200"},
    }))
    with pytest.raises(UnmanagedDaemonError, match="live route"):
        world.manager().run()
    assert world.spawns == 0


def test_persisted_recovery_probe_error_closes_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(tmp_path)
    StateStore(world.state).write(ManagerState(
        world.backend.owner, world.backend.identities[20],
    ))

    def fail_probe(reference: FakeReference) -> bool:
        raise OSError("injected persisted recovery probe failure")

    monkeypatch.setattr(world.backend, "owns", fail_probe)
    with pytest.raises(OSError, match="persisted recovery probe"):
        world.manager().run()
    assert all(reference.closed for reference in world.backend.references)
    assert world.backend.cleaned and world.lease.closed
    assert world.spawns == 0


def test_backend_initialization_failure_releases_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zdd import singleton_manager

    world = World(tmp_path)

    def fail_backend() -> FakeBackend:
        raise OSError("injected backend initialization failure")

    monkeypatch.setattr(singleton_manager, "LinuxBackend", fail_backend)
    manager = SingletonManager(
        world.routing, world.spawn, manager_state_dir=world.state,
        lease_factory=lambda directory: world.lease,
    )
    with pytest.raises(OSError, match="backend initialization"):
        manager.run()
    assert world.lease.closed
    assert not world.backend.claimed
    assert world.spawns == 0


@pytest.mark.parametrize("during_transition", [False, True])
def test_pidfd_close_error_cannot_skip_cleanup_or_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, during_transition: bool,
) -> None:
    world = World(tmp_path)
    events: list[str] = []

    def failed_close(reference: FakeReference) -> None:
        events.append("reference-close")
        reference.closed = True
        raise OSError("injected pidfd close failure")

    def cleanup() -> None:
        events.append("descendant-cleanup")
        world.backend.cleaned = True

    def release() -> None:
        events.append("lease-release")
        world.lease.closed = True

    monkeypatch.setattr(FakeReference, "close", failed_close)
    monkeypatch.setattr(world.backend, "cleanup", cleanup)
    monkeypatch.setattr(world.lease, "close", release)
    kwargs = {} if during_transition else {"resolve_update": lambda: ["relative-command"]}
    with pytest.raises(OSError, match="pidfd close failure"):
        world.manager(**kwargs).run()
    assert events == ["reference-close", "descendant-cleanup", "lease-release"]
    assert world.backend.cleaned and world.lease.closed


@pytest.mark.parametrize("token", [None, "200"])
def test_live_unidentifiable_route_never_permits_bootstrap(
    tmp_path: Path, token: str | None,
) -> None:
    world = World(tmp_path)
    world.route(20, token)
    del world.backend.identities[20]
    with pytest.raises(UnmanagedDaemonError, match="live route"):
        world.manager().run()
    assert world.spawns == 0


def test_old_manager_live_unidentifiable_incumbent_blocks_spawn(tmp_path: Path) -> None:
    world = World(tmp_path)
    StateStore(world.state).write(ManagerState(
        ProcessIdentity(9, "90", world.backend.boot_id), world.backend.identities[20],
    ))
    del world.backend.identities[20]
    with pytest.raises(UnmanagedDaemonError, match="survived"):
        world.manager().run()
    assert world.spawns == 0


def test_proven_dead_route_permits_fresh_spawn(tmp_path: Path) -> None:
    world = World(tmp_path)
    world.route(20, None)
    del world.backend.identities[20]
    world.backend.live[20] = False

    def spawn_replacement() -> FakeChild:
        world.spawns += 1
        world.backend.live[30] = True
        return FakeChild(pid=30)

    world.spawn = spawn_replacement
    world.on_sleep = lambda: world.backend.live.update({30: False})
    result = world.manager().run()
    assert world.spawns == 1
    assert result.last_watched_pid == 30
