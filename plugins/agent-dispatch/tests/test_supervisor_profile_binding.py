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
