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
    original = "AGENT_DISPATCH_SUPERVISE_MODE=serve\nAGENT_DISPATCH_SUPERVISE_INTERVAL=5\n"
    profile.write_text(original)
    argv = profile_arguments(_parse_env_file(profile, strict=True))
    monkeypatch.setenv(PROFILE_ENV, str(profile))
    monkeypatch.setenv(GENERATION_ENV, hashlib.sha256(profile.read_bytes()).hexdigest())
    binding = profiles.take_master_profile(argv)
    assert PROFILE_ENV not in os.environ and GENERATION_ENV not in os.environ
    daemon = SimpleNamespace(_self_update_stale_target=lambda *_: tmp_path / "new-python", _self_update_argv=argv)
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
    lock = FakeLock()
    daemon = _daemon(
        FakeClient([]), FakeLauncher(), lock=lock, clock=lambda: now[0],
        self_update_enabled=True, self_update_argv=argv,
        self_update_install_dir=lambda: tmp_path, self_update_running_version="1.0",
        self_update_stale_target=lambda *_: None, self_update_poll_interval=1,
    )
    assert daemon.acquire_singleton()
    monkeypatch.setattr(daemon, "_recheck_governance", lambda _: None)
    binding.configure(daemon)
    captured = []
    monkeypatch.setattr(procutil, "spawn_dispatch_daemon", lambda command, **options: captured.append(command))
    profile.write_text(original + invalid)
    assert daemon._maybe_self_update() is False
    assert not lock.released
    assert daemon._self_update_argv == argv
    assert binding.next_generation == binding.generation
    assert captured == []
    profile.write_text(original + "AGENT_DISPATCH_SUPERVISE_INTERVAL=7\n")
    now[0] += 2
    assert daemon._maybe_self_update() is True
    assert lock.released
    assert captured == [[slot, "-m", "agent_dispatch", *profile_arguments(_parse_env_file(profile, strict=True))]]
