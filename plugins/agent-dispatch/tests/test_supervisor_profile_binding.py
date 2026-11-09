from __future__ import annotations

import hashlib
import os
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("changed", [False, True])
def test_master_binding_reaches_only_successor_environment(tmp_path, monkeypatch, changed):
    from agent_dispatch import procutil, supervisor_profile as profiles, self_update
    from agent_dispatch.supervisor_lane_status import PROFILE_ENV, GENERATION_ENV, profile_arguments
    from agent_dispatch.registrar_discovery import _parse_env_file

    profile = tmp_path / "supervisor.env"
    original = (
        "AGENT_DISPATCH_SUPERVISE_MODE=serve\nAGENT_DISPATCH_SUPERVISE_INTERVAL=5\n"
        "AGENT_DISPATCH_SUPERVISE_MACHINE=fixture\n"
    )
    profile.write_text(original)
    argv = profile_arguments(_parse_env_file(profile, strict=True))
    monkeypatch.setenv(PROFILE_ENV, str(profile))
    monkeypatch.setenv(GENERATION_ENV, hashlib.sha256(profile.read_bytes()).hexdigest())
    binding = profiles.take_master_profile(argv)
    assert PROFILE_ENV not in os.environ and GENERATION_ENV not in os.environ
    daemon = SimpleNamespace(
        _self_update_stale_target=lambda *_: tmp_path / "new-python",
        _self_update_argv=argv, machine="fixture", env="default",
    )
    binding.configure(daemon)
    if changed:
        profile.write_text(original.replace("=5", "=7") + "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--no-declared\n")
    target = daemon._self_update_stale_target(tmp_path, "old")
    assert daemon._self_update_argv == profile_arguments(_parse_env_file(profile, strict=True))
    captured = []
    monkeypatch.setattr(procutil, "spawn_dispatch_daemon", lambda command, **options: captured.append(options["env"]))
    daemon._self_update_spawn(target, daemon._self_update_argv)
    assert captured[0][PROFILE_ENV] == str(profile)
    assert captured[0][GENERATION_ENV] == hashlib.sha256(profile.read_bytes()).hexdigest()
    assert PROFILE_ENV not in os.environ and GENERATION_ENV not in os.environ
    if changed:
        monkeypatch.setattr(self_update, "slot_python", lambda *_: tmp_path / "same-python")
        profile.write_text(original)
        daemon._self_update_stale_target(tmp_path, "old")
        assert daemon._self_update_argv == argv
        assert binding.next_generation == binding.generation


@pytest.mark.parametrize("extra", ["--once", "--no-single-instance", "--interval nan"])
def test_master_profile_reconfiguration_refuses_nonresident_or_nonfinite_settings(tmp_path, extra):
    from agent_dispatch.supervisor_profile import MasterProfile

    profile = tmp_path / "supervisor.env"
    profile.write_text("AGENT_DISPATCH_SUPERVISE_MODE=serve\n")
    binding = MasterProfile(profile, None, ["supervise", "serve"])
    daemon = SimpleNamespace(_self_update_stale_target=lambda *_: None)
    binding.configure(daemon)
    profile.write_text("AGENT_DISPATCH_SUPERVISE_MODE=serve\nAGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=" + extra + "\n")
    with pytest.raises(ValueError, match="resident master"):
        daemon._self_update_stale_target(tmp_path, "current")


@pytest.mark.parametrize("invalid", [
    "AGENT_DISPATCH_SUPERVISE_INTERVAL=not-a-number\n",
    "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--unknown-fixture-option\n",
    "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--source-candidate fixture\n",
    "AGENT_DISPATCH_SUPERVISE_MACHINE=other-machine\n",
    "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--env other-environment\n",
])
def test_invalid_live_profile_keeps_incumbent_and_recovers_after_valid_edit(tmp_path, monkeypatch, invalid):
    from agent_dispatch import procutil
    from agent_dispatch.supervisor_profile import MasterProfile
    from agent_dispatch.supervisor_lane_status import profile_arguments
    from agent_dispatch.registrar_discovery import _parse_env_file
    from tests.test_supervisor_daemon import FakeClient, FakeLauncher, FakeLock, _daemon

    profile = tmp_path / "supervisor.env"
    original = "AGENT_DISPATCH_SUPERVISE_MODE=serve\nAGENT_DISPATCH_SUPERVISE_MACHINE=anomalous-potato\n"
    profile.write_text(original)
    argv = profile_arguments(_parse_env_file(profile, strict=True))
    binding = MasterProfile(profile, None, argv)
    slot = tmp_path / "versions" / "1.0" / "Scripts" / "python.exe"
    slot.parent.mkdir(parents=True)
    slot.touch()
    (tmp_path / "current-version").write_text("1.0")
    now = [100.0]
    target_checks = []
    lock = FakeLock()
    daemon = _daemon(
        FakeClient([]), FakeLauncher(), lock=lock, clock=lambda: now[0],
        self_update_enabled=True, self_update_argv=argv,
        self_update_install_dir=lambda: tmp_path, self_update_running_version="1.0",
        self_update_stale_target=lambda *_: target_checks.append(True), self_update_poll_interval=1,
    )
    assert daemon.acquire_singleton()
    monkeypatch.setattr(daemon, "_recheck_governance", lambda _: None)
    binding.configure(daemon)
    captured = []
    monkeypatch.setattr(procutil, "spawn_dispatch_daemon", lambda command, **options: captured.append(command))
    edited = (
        original.replace("AGENT_DISPATCH_SUPERVISE_MACHINE=anomalous-potato\n", invalid)
        if invalid.startswith("AGENT_DISPATCH_SUPERVISE_MACHINE=") else original + invalid
    )
    profile.write_text(edited)
    assert daemon._maybe_self_update() is False
    assert not lock.released
    assert daemon._self_update_argv == argv
    assert binding.next_generation == binding.generation
    assert captured == []
    assert target_checks == []
    profile.write_text(original + "AGENT_DISPATCH_SUPERVISE_INTERVAL=7\n")
    now[0] += 2
    assert daemon._maybe_self_update() is True
    assert lock.released
    assert target_checks == [True]
    assert captured == [[slot, "-m", "agent_dispatch", *profile_arguments(_parse_env_file(profile, strict=True))]]


@pytest.mark.parametrize("legacy", [False, True])
def test_source_primary_binding_reuses_startup_defaults_on_live_edit(tmp_path, monkeypatch, legacy):
    from agent_dispatch import source_start
    from agent_dispatch.supervisor_profile import MasterProfile, take_master_profile, OWNER_ENV
    from tests.test_source_supervisor_startup import _sources

    _sources(monkeypatch)
    primary = tmp_path / "supervisor.env"
    primary.write_text(
        "AGENT_DISPATCH_SUPERVISE_MODE=serve\nAGENT_DISPATCH_SUPERVISE_MACHINE=fixture\n"
        + ("AGENT_DISPATCH_SUPERVISE_LABELS=fixture\n" if legacy else ""),
    )
    initial = source_start.supervisor_plan(tmp_path)
    binding = MasterProfile(primary, initial.profile_generation, initial.argv, source_primary=True)
    daemon = SimpleNamespace(
        _self_update_stale_target=lambda *_: tmp_path / "selected-python", _self_update_argv=initial.argv,
        machine=initial.machine, env=initial.env,
    )
    binding.configure(daemon)
    primary.write_text(primary.read_text() + "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--no-declared\n")
    daemon._self_update_stale_target(tmp_path, "same-version")
    changed = source_start.supervisor_plan(tmp_path)
    assert changed.args.interval == initial.args.interval == 5
    assert changed.args.legacy_env is initial.args.legacy_env is legacy
    assert daemon._self_update_argv == list(changed.argv)
    for key, value in binding.environment().items():
        monkeypatch.setenv(key, value)
    incoming = take_master_profile(daemon._self_update_argv)
    assert incoming.source_primary and OWNER_ENV not in os.environ
    assert incoming.generation == changed.profile_generation


def test_source_primary_invalid_live_mode_change_retains_incumbent_and_recovers(tmp_path, monkeypatch):
    from agent_dispatch import pool_source_snapshot, procutil, source_start, supervisor_source
    from agent_dispatch.supervisor_profile import MasterProfile
    from tests.test_source_supervisor_startup import _sources
    from tests.test_supervisor_daemon import FakeClient, FakeLauncher, FakeLock, _daemon

    _sources(monkeypatch)
    monkeypatch.setattr(
        supervisor_source, "read_legacy_declarations",
        lambda **_: pool_source_snapshot.DeclarationRead((), True),
    )
    primary = tmp_path / "supervisor.env"
    original = (
        "AGENT_DISPATCH_SUPERVISE_MODE=serve\n"
        "AGENT_DISPATCH_SUPERVISE_MACHINE=anomalous-potato\n"
        "AGENT_DISPATCH_SUPERVISE_LABELS=fixture\n"
        "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--no-declared\n"
    )
    primary.write_text(original)
    initial = source_start.supervisor_plan(tmp_path)
    binding = MasterProfile(primary, initial.profile_generation, initial.argv, source_primary=True)
    slot = tmp_path / "versions" / "1.0" / "Scripts" / "python.exe"
    slot.parent.mkdir(parents=True)
    slot.touch()
    (tmp_path / "current-version").write_text("1.0")
    now, target_checks, captured = [100.0], [], []
    lock = FakeLock()
    daemon = _daemon(
        FakeClient([]), FakeLauncher(), lock=lock, clock=lambda: now[0],
        self_update_enabled=True, self_update_argv=list(initial.argv),
        self_update_install_dir=lambda: tmp_path, self_update_running_version="1.0",
        self_update_stale_target=lambda *_: target_checks.append(True), self_update_poll_interval=1,
    )
    assert daemon.acquire_singleton()
    monkeypatch.setattr(daemon, "_recheck_governance", lambda _: None)
    monkeypatch.setattr(procutil, "spawn_dispatch_daemon", lambda command, **_: captured.append(command))
    binding.configure(daemon)
    primary.write_text(original.replace("MODE=serve", "MODE=legacy"))
    assert daemon._maybe_self_update() is False
    assert not lock.released
    assert daemon._self_update_argv == list(initial.argv)
    assert binding.next_generation == binding.generation
    assert target_checks == captured == []
    primary.write_text(original + "AGENT_DISPATCH_SUPERVISE_INTERVAL=7\n")
    now[0] += 2
    assert daemon._maybe_self_update() is True
    assert lock.released and target_checks == [True]
    assert captured == [[slot, "-m", "agent_dispatch", *source_start.supervisor_plan(tmp_path).argv]]


@pytest.mark.parametrize("name", ["supervisor.env", "supervisors/named.env"])
def test_compatibility_bindings_keep_their_own_interval_and_legacy_defaults(tmp_path, name):
    from agent_dispatch import __main__ as cli
    from agent_dispatch.supervisor_profile import MasterProfile, OWNER_ENV
    from agent_dispatch.supervisor_lane_status import profile_arguments
    from agent_dispatch.registrar_discovery import _parse_env_file

    profile = tmp_path / name
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text("AGENT_DISPATCH_SUPERVISE_MODE=serve\nAGENT_DISPATCH_SUPERVISE_MACHINE=fixture\n")
    binding = MasterProfile(profile, None, profile_arguments(_parse_env_file(profile, strict=True)))
    daemon = SimpleNamespace(
        _self_update_stale_target=lambda *_: tmp_path / "selected-python",
        machine="fixture", env="default",
    )
    binding.configure(daemon)
    profile.write_text(profile.read_text() + "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--no-declared\n")
    daemon._self_update_stale_target(tmp_path, "same")
    parsed = cli.build_parser().parse_args(daemon._self_update_argv)
    assert parsed.interval == 30 and parsed.legacy_env is True
    assert OWNER_ENV not in binding.environment()


def test_partial_binding_metadata_is_not_a_marker_free_old_producer(monkeypatch):
    from agent_dispatch.supervisor_lane_status import PROFILE_ENV, GENERATION_ENV
    from agent_dispatch.supervisor_profile import OWNER_ENV, take_master_profile

    monkeypatch.delenv(PROFILE_ENV, raising=False)
    monkeypatch.delenv(OWNER_ENV, raising=False)
    monkeypatch.setenv(GENERATION_ENV, "unconfirmed-generation")
    with pytest.raises(ValueError, match="argument owner"):
        take_master_profile()
