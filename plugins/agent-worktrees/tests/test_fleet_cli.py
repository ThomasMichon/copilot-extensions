"""Tests for `agent-worktrees fleet` -- the fleet-wide `list --json`
aggregator (agent-worktrees-fleet-flows Phase 1, aperture-labs #2740).

Validates the Phase 1 Validation Plan bullet: one `list --json` invocation
per SSH target (never per-worktree), and an unreachable host degrading to a
partial result rather than failing the whole call.
"""

from __future__ import annotations

import io
import json
import subprocess
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import list_views_cli as fleet


def _entry(key, *, envs, ssh_ready=True, copilot=True, alias=""):
    return cfg.MachineEntry(
        key=key,
        display_name=key,
        environment=envs[0][0] if envs else "",
        ssh_ready=ssh_ready,
        copilot=copilot,
        alias=alias,
        ssh_environments=[
            cfg.SSHEnvironment(name=name, alias=alias_) for name, alias_ in envs
        ],
    )


def _fake_config(tmp_path, *, machine="lambda-core", platform="wsl"):
    return SimpleNamespace(
        machine=machine,
        platform=platform,
        default_repo=SimpleNamespace(anchor=str(tmp_path)),
    )


def _run_json(argv, entries, config, *, run_side_effect):
    buf = io.StringIO()
    with patch.object(fleet.cfg, "load_config", return_value=config), \
         patch.object(fleet.cfg, "load_machines_yaml", return_value=entries), \
         patch.object(fleet.cfg, "project_name", return_value="agent-worktrees"), \
         patch.object(fleet.subprocess, "run", side_effect=run_side_effect), \
         redirect_stdout(buf):
        rc = fleet.run_fleet(argv)
    assert rc == 0
    return json.loads(buf.getvalue())


def test_one_call_per_ssh_target_never_per_worktree(tmp_path):
    """Two machines, three environments total -- exactly 3 subprocess calls,
    each `list --json` (not one per worktree inside them)."""
    entries = {
        "lambda-core": _entry("lambda-core", envs=[
            ("windows", "lambda-core"), ("wsl", "lambda-core-wsl"),
        ]),
        "wheatley": _entry("wheatley", envs=[("linux", "wheatley")]),
    }
    config = _fake_config(tmp_path, machine="lambda-core", platform="wsl")
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        payload = json.dumps({"worktrees": [{"id": "a"}, {"id": "b"}]})
        return SimpleNamespace(returncode=0, stdout=payload, stderr="")

    result = _run_json(["--json"], entries, config, run_side_effect=_fake_run)

    assert len(calls) == 3  # one per ssh target, regardless of worktree count
    assert len(result["hosts"]) == 3
    for row in result["hosts"]:
        assert row["reachable"] is True
        assert len(row["worktrees"]) == 2


def test_offline_host_degrades_to_partial_result(tmp_path):
    """An unreachable host must not fail the whole fleet call -- it shows up
    as `reachable: false` alongside the others' real data."""
    entries = {
        "lambda-core": _entry("lambda-core", envs=[("wsl", "lambda-core-wsl")]),
        "borealis": _entry("borealis", envs=[("linux", "borealis")]),
    }
    config = _fake_config(tmp_path, machine="lambda-core", platform="wsl")

    def _fake_run(cmd, **kwargs):
        if "borealis" in cmd:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=15)
        payload = json.dumps({"worktrees": [{"id": "a"}]})
        return SimpleNamespace(returncode=0, stdout=payload, stderr="")

    result = _run_json(["--json"], entries, config, run_side_effect=_fake_run)

    by_machine = {row["machine"]: row for row in result["hosts"]}
    assert by_machine["lambda-core"]["reachable"] is True
    assert by_machine["borealis"]["reachable"] is False
    assert "error" in by_machine["borealis"]
    # Schema stability (review #3134): `worktrees` is always present, even
    # for an unreachable row, so a consumer never special-cases a missing key.
    assert by_machine["borealis"]["worktrees"] == []


def test_ssh_login_banner_noise_does_not_break_parsing():
    """Login-banner/MOTD noise surrounding the JSON payload (common over a
    real SSH session) must not make a reachable host look unreachable --
    mirrors claimant.py's/codename_reverse_lookup.py's own boundary-scan
    tolerance (review #3134)."""
    noisy = (
        "Welcome to Ubuntu 24.04\nLast login: Mon Jan  1\n"
        '{"worktrees": [{"id": "a"}]}\n'
    )
    result = fleet._parse_list_payload(noisy)
    assert result == [{"id": "a"}]


def test_banner_noise_containing_brackets_does_not_break_parsing():
    """A banner line containing a stray `[`/`]` (e.g. a bracketed timestamp)
    must not widen the scanned range into the noise -- the dict-only
    ({..}) boundary scan is deliberately immune to this (review #3134)."""
    noisy = (
        "Last login: Mon Jan  1 12:00:00 [UTC] 2026 from 10.0.0.5\n"
        '{"worktrees": [{"id": "a"}]}\n'
    )
    result = fleet._parse_list_payload(noisy)
    assert result == [{"id": "a"}]


def test_local_environment_runs_locally_not_over_ssh(tmp_path):
    """The current machine's own environment is run directly via the local
    binstub (still a subprocess, not an ssh hop), matching the machine/
    platform this test pretends to be."""
    entries = {
        "lambda-core": _entry("lambda-core", envs=[
            ("windows", "lambda-core"), ("wsl", "lambda-core-wsl"),
        ]),
    }
    config = _fake_config(tmp_path, machine="lambda-core", platform="wsl")
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout='{"worktrees": []}', stderr="")

    _run_json(["--json"], entries, config, run_side_effect=_fake_run)

    # Exactly one of the two calls is a local binstub invocation (no "ssh"
    # in argv[0]); the other targets the Windows env over ssh.
    ssh_calls = [c for c in calls if c[0] == "ssh"]
    local_calls = [c for c in calls if c[0] != "ssh"]
    assert len(ssh_calls) == 1
    assert len(local_calls) == 1


def test_machine_level_alias_fallback_is_not_omitted(tmp_path):
    """A machine with no per-environment alias (only a top-level `alias`)
    must still get a fleet row -- mirrors `claimant.resolve_machine_ssh`'s
    own fallback (review #3134)."""
    entries = {
        "wheatley": _entry("wheatley", envs=[], alias="wheatley"),
    }
    config = _fake_config(tmp_path, machine="lambda-core", platform="wsl")
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout='{"worktrees": []}', stderr="")

    result = _run_json(["--json"], entries, config, run_side_effect=_fake_run)

    assert len(result["hosts"]) == 1
    assert result["hosts"][0]["machine"] == "wheatley"
    assert result["hosts"][0]["reachable"] is True
    assert calls[0][0] == "ssh"
    assert calls[0][-2] == "wheatley"


def test_invalid_timeout_fails_fast_with_usage_error(tmp_path, capsys):
    """A non-positive/NaN/inf --timeout must be rejected immediately with a
    clear usage error, not surface later as a confusing int()/subprocess
    failure mid-fan-out (review #3134)."""
    config = _fake_config(tmp_path)
    with patch.object(fleet.cfg, "load_config", return_value=config), \
         patch.object(fleet.cfg, "load_machines_yaml", return_value={}):
        for bad in ("0", "-5", "nan", "inf"):
            with pytest.raises(SystemExit) as ei:
                fleet.run_fleet(["--timeout", bad])
            assert ei.value.code == 2
    assert "--timeout must be" in capsys.readouterr().err


def test_config_load_failure_is_reported_not_swallowed(capsys):
    """A broken config must surface as a reported failure (exit 1), never a
    silently-empty `{"hosts": []}` result (review #3134)."""
    with patch.object(fleet.cfg, "load_config", side_effect=RuntimeError("boom")):
        rc = fleet.run_fleet([])
    assert rc == 1
    assert "could not load config" in capsys.readouterr().err

