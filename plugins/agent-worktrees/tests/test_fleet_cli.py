"""Tests for `agent-worktrees fleet` -- the fleet-wide `list --json`
aggregator (agent-worktrees-fleet-flows Phase 1, the downstream tracker).

Validates the Phase 1 Validation Plan bullet: one `list --json` invocation
per SSH target (never per-worktree), and an unreachable host degrading to a
partial result rather than failing the whole call.
"""

from __future__ import annotations

import base64
import io
import json
import shlex
import subprocess
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees import config as cfg
from agent_worktrees import delegate_cli
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


def _fake_config(tmp_path, *, machine="atlas-core", platform="wsl"):
    return SimpleNamespace(
        machine=machine,
        platform=platform,
        default_repo=SimpleNamespace(anchor=str(tmp_path)),
    )


def _run_json(argv, entries, config, *, run_side_effect, project="agent-worktrees"):
    buf = io.StringIO()
    with patch.object(fleet.cfg, "load_config", return_value=config), \
         patch.object(fleet.cfg, "load_machines_yaml", return_value=entries), \
         patch.object(fleet.cfg, "project_name", return_value=project), \
         patch.object(fleet.subprocess, "run", side_effect=run_side_effect), \
         redirect_stdout(buf):
        rc = fleet.run_fleet(argv)
    assert rc == 0
    return json.loads(buf.getvalue())


def _record_calls(calls, stdout='{"worktrees": []}'):
    def _fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    return _fake_run


def _decode_pwsh(remote_cmd):
    """Return the script inside a ``pwsh ... -EncodedCommand <b64>`` command."""
    return base64.b64decode(remote_cmd.split()[-1]).decode("utf-16-le")


def _bash_argv(remote_cmd):
    """Return the argv a ``bash -lc '<inner>'`` command actually runs."""
    bash, flag, inner = shlex.split(remote_cmd)
    assert (bash, flag) == ("bash", "-lc")
    return shlex.split(inner)


def _three_targets():
    """Local WSL env plus a remote Windows (pwsh) and a remote Linux (bash) env."""
    return {
        "atlas-core": _entry("atlas-core", envs=[
            ("windows", "atlas-core"), ("wsl", "atlas-core-wsl"),
        ]),
        "ember": _entry("ember", envs=[("linux", "ember")]),
    }


def test_one_call_per_ssh_target_never_per_worktree(tmp_path):
    """Two machines, three environments total -- exactly 3 subprocess calls,
    each `list --json` (not one per worktree inside them)."""
    entries = {
        "atlas-core": _entry("atlas-core", envs=[
            ("windows", "atlas-core"), ("wsl", "atlas-core-wsl"),
        ]),
        "ember": _entry("ember", envs=[("linux", "ember")]),
    }
    config = _fake_config(tmp_path, machine="atlas-core", platform="wsl")
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
        "atlas-core": _entry("atlas-core", envs=[("wsl", "atlas-core-wsl")]),
        "borealis": _entry("borealis", envs=[("linux", "borealis")]),
    }
    config = _fake_config(tmp_path, machine="atlas-core", platform="wsl")

    def _fake_run(cmd, **kwargs):
        if "borealis" in cmd:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=15)
        payload = json.dumps({"worktrees": [{"id": "a"}]})
        return SimpleNamespace(returncode=0, stdout=payload, stderr="")

    result = _run_json(["--json"], entries, config, run_side_effect=_fake_run)

    by_machine = {row["machine"]: row for row in result["hosts"]}
    assert by_machine["atlas-core"]["reachable"] is True
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
    """The current machine's own environment is run directly by the local
    runtime (still a subprocess, not an ssh hop), matching the machine/
    platform this test pretends to be."""
    entries = {
        "atlas-core": _entry("atlas-core", envs=[
            ("windows", "atlas-core"), ("wsl", "atlas-core-wsl"),
        ]),
    }
    config = _fake_config(tmp_path, machine="atlas-core", platform="wsl")
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout='{"worktrees": []}', stderr="")

    _run_json(["--json"], entries, config, run_side_effect=_fake_run)

    # Exactly one of the two calls is a local runtime invocation (no "ssh"
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
        "ember": _entry("ember", envs=[], alias="ember"),
    }
    config = _fake_config(tmp_path, machine="atlas-core", platform="wsl")
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout='{"worktrees": []}', stderr="")

    result = _run_json(["--json"], entries, config, run_side_effect=_fake_run)

    assert len(result["hosts"]) == 1
    assert result["hosts"][0]["machine"] == "ember"
    assert result["hosts"][0]["reachable"] is True
    assert calls[0][0] == "ssh"
    assert calls[0][-2] == "ember"


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



def test_local_row_launches_without_a_project_binstub(tmp_path):
    """The local row must not depend on a ``<project>`` launcher file. On
    Windows the launchers are ``.ps1``/``.cmd`` shims that an argv-list
    ``subprocess.run`` cannot start, which used to fail the local row with
    "file not found". The local argv starts from a real interpreter and
    passes the project explicitly before the verb."""
    entries = {"atlas-core": _entry("atlas-core", envs=[("wsl", "atlas-core-wsl")])}
    config = _fake_config(tmp_path, machine="atlas-core", platform="wsl")
    calls = []

    result = _run_json(["--json"], entries, config, run_side_effect=_record_calls(calls))

    assert result["hosts"][0]["reachable"] is True
    (cmd, _kwargs), = calls
    assert Path(cmd[0]).is_file()
    assert cmd[1:] == [
        "-I", "-X", "utf8", "-m", "agent_worktrees",
        "--project", "agent-worktrees", "list", "--json", "--passive",
    ]


def test_local_project_argv_runs_the_real_cli():
    """End to end: the local argv prefix starts the actual CLI (no binstub
    needed), and its top-level help surfaces the ``fleet`` verb."""
    proc = subprocess.run(
        [*fleet._local_project_argv("example"), "--help"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, stdin=subprocess.DEVNULL, **no_window_kwargs(),
    )
    assert proc.returncode == 0, proc.stderr
    assert "fleet [--json]" in proc.stdout
    assert "selected project only" in proc.stdout


def test_local_project_argv_ignores_a_shadowing_package_in_cwd(tmp_path):
    """A passive fleet read must not execute workspace code: an
    ``agent_worktrees`` package in the caller's working directory must not
    shadow the installed runtime."""
    shadow = tmp_path / "agent_worktrees"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("", encoding="utf-8")
    (shadow / "__main__.py").write_text("print('SHADOW-EXECUTED')\n", encoding="utf-8")
    proc = subprocess.run(
        [*fleet._local_project_argv("example"), "--help"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, stdin=subprocess.DEVNULL, cwd=tmp_path, **no_window_kwargs(),
    )
    assert "SHADOW-EXECUTED" not in proc.stdout + proc.stderr
    assert proc.returncode == 0, proc.stderr
    assert "fleet [--json]" in proc.stdout


def test_selected_project_reaches_local_and_remote_commands(tmp_path):
    """``--project <p> fleet`` lists ``<p>`` on every host: the local argv and
    both remote shell forms name it explicitly through the owner command,
    so a host needs no ``<p>`` binstub."""
    config = _fake_config(tmp_path, machine="atlas-core", platform="wsl")
    calls = []

    _run_json(
        ["--json"], _three_targets(), config,
        run_side_effect=_record_calls(calls), project="proj-x",
    )

    expected = ["agent-worktrees", "--project", "proj-x", "list", "--json", "--passive"]
    local = [cmd for cmd, _ in calls if cmd[0] != "ssh"]
    remote = {cmd[-2]: cmd[-1] for cmd, _ in calls if cmd[0] == "ssh"}
    assert len(local) == 1
    assert local[0][-5:] == ["--project", "proj-x", "list", "--json", "--passive"]
    assert _bash_argv(remote["ember"]) == [
        "AGENT_WORKTREES_NO_SELFPROVISION=1", *expected,
    ]
    script = _decode_pwsh(remote["atlas-core"])
    assert "& 'agent-worktrees' '--project' 'proj-x' 'list' '--json' '--passive';" in script


def test_children_close_stdin_stay_headless_and_never_self_provision(tmp_path):
    """Every child gets a closed stdin (ssh must never read the caller's
    stdin) and no console window; the local child and both remote shells
    disable runtime self-provisioning so ``fleet`` stays a passive read."""
    config = _fake_config(tmp_path, machine="atlas-core", platform="wsl")
    calls = []

    _run_json(["--json"], _three_targets(), config, run_side_effect=_record_calls(calls))

    assert len(calls) == 3
    for _cmd, kwargs in calls:
        assert kwargs["stdin"] is subprocess.DEVNULL
        for key, value in no_window_kwargs().items():
            assert kwargs[key] == value
    (local_kwargs,) = [kwargs for cmd, kwargs in calls if cmd[0] != "ssh"]
    assert local_kwargs["env"]["AGENT_WORKTREES_NO_SELFPROVISION"] == "1"
    remote = {cmd[-2]: cmd[-1] for cmd, _ in calls if cmd[0] == "ssh"}
    assert "$env:AGENT_WORKTREES_NO_SELFPROVISION='1';" in _decode_pwsh(remote["atlas-core"])
    assert _bash_argv(remote["ember"])[0] == "AGENT_WORKTREES_NO_SELFPROVISION=1"


def test_remote_command_quotes_hostile_values():
    """A project or pass-through value containing quotes or separators stays
    one literal argument in both shells -- never executable syntax."""
    hostile = "a'b;c $(x) `y`"
    assert _bash_argv(fleet._remote_list_cmd("bash", hostile, ["--tracking-status", hostile])) == [
        "AGENT_WORKTREES_NO_SELFPROVISION=1", "agent-worktrees",
        "--project", hostile, "list", "--json", "--tracking-status", hostile,
    ]
    script = _decode_pwsh(fleet._remote_list_cmd("pwsh", hostile, []))
    assert "'--project' 'a''b;c $(x) `y`' 'list'" in script


def test_missing_command_or_bad_output_is_a_gap_not_zero(tmp_path):
    """A host whose owner command is missing (exit 127), whose launch fails,
    or whose output is unparseable is unknown coverage -- ``reachable:
    false`` with an ``error`` -- never a reachable host with zero worktrees.
    A failing host never drops another host's rows."""
    entries = {
        name: _entry(name, envs=[("linux", name)])
        for name in ("missing", "nolaunch", "garbled", "empty", "busy")
    }
    config = _fake_config(tmp_path, machine="observer", platform="wsl")
    outcomes = {
        "missing": SimpleNamespace(
            returncode=127, stdout="", stderr="agent-worktrees: command not found",
        ),
        "garbled": SimpleNamespace(returncode=0, stdout="not json", stderr=""),
        "empty": SimpleNamespace(returncode=0, stdout='{"worktrees": []}', stderr=""),
        "busy": SimpleNamespace(
            returncode=0, stdout='{"worktrees": [{"id": "w1", "state": "active"}]}', stderr="",
        ),
    }

    def _fake_run(cmd, **kwargs):
        if cmd[-2] == "nolaunch":
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return outcomes[cmd[-2]]

    result = _run_json(["--json"], entries, config, run_side_effect=_fake_run)

    rows = {row["machine"]: row for row in result["hosts"]}
    for name in ("missing", "nolaunch", "garbled"):
        assert rows[name]["reachable"] is False
        assert rows[name]["error"]
        assert rows[name]["worktrees"] == []
    assert "command not found" in rows["missing"]["error"]
    # An unlabeled reply (no current-roster provenance) is reachable but unproven.
    assert rows["empty"] == {
        "machine": "empty", "env": "linux", "reachable": True, "worktrees": [],
        "availability": {"status": "stale", "reason": delegate_cli._UNPROVEN_ROSTER_REASON},
    }
    assert rows["busy"]["reachable"] is True
    assert [w["id"] for w in rows["busy"]["worktrees"]] == ["w1"]


def test_every_host_read_is_passive_and_saved_rows_keep_provenance(tmp_path):
    """Each host is read with ``list --json --passive`` so ``fleet`` never
    starts or waits on a host's status-monitor. A host serving its saved
    roster keeps ``availability``/``projection`` on its row, so advisory
    (possibly stale) rows stay distinguishable from current ones."""
    config = _fake_config(tmp_path, machine="atlas-core", platform="wsl")
    calls = []
    saved = json.dumps({
        "worktrees": [{"id": "w1", "state": "active"}],
        "projection": {"quality": "advisory", "observed_at": 1700000000.0},
        "availability": {"status": "advisory", "reason": "resident not running"},
    })

    result = _run_json(
        ["--json", "--tracking-status", "active"], _three_targets(), config,
        run_side_effect=_record_calls(calls, stdout=saved),
    )

    tail = ["list", "--json", "--passive", "--tracking-status", "active"]
    local = [cmd for cmd, _ in calls if cmd[0] != "ssh"]
    remote = {cmd[-2]: cmd[-1] for cmd, _ in calls if cmd[0] == "ssh"}
    assert local[0][-5:] == tail
    assert _bash_argv(remote["ember"])[-5:] == tail
    assert "'list' '--json' '--passive' '--tracking-status' 'active';" in _decode_pwsh(
        remote["atlas-core"]
    )
    for row in result["hosts"]:
        assert row["reachable"] is True
        assert row["availability"]["status"] == "advisory"
        assert row["projection"]["observed_at"] == 1700000000.0


def test_resident_freshness_and_stale_label_reach_fleet_rows(tmp_path, capsys):
    """A resident roster read passively carries ``projection.freshness``; one
    not republished within the resident's bound is labeled stale and named
    apart from a saved roster in the human view."""
    config = _fake_config(tmp_path, machine="atlas-core", platform="wsl")
    fresh = {"quality": "current", "observed_at": 1.0,
             "freshness": {"refreshed_age_s": 5.0, "bound_s": 90.0}}
    replies = {
        "local": {"worktrees": [], "projection": fresh},
        "ember": {
            "worktrees": [{"id": "w1"}],
            "projection": {**fresh, "freshness": {"refreshed_age_s": 95.0, "bound_s": 90.0}},
            "availability": {"status": "stale", "reason": "current roster refresh overdue"},
        },
        "atlas-core": {
            "worktrees": [],
            "projection": {"quality": "advisory", "observed_at": 1.0},
            "availability": {"status": "advisory", "reason": "resident not running"},
        },
    }

    def _fake_run(cmd, **_kwargs):
        reply = replies["local" if cmd[0] != "ssh" else cmd[-2]]
        return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr="")

    result = _run_json(["--json"], _three_targets(), config, run_side_effect=_fake_run)
    rows = {(row["machine"], row["env"]): row for row in result["hosts"]}
    assert rows[("atlas-core", "wsl")]["projection"]["freshness"]["refreshed_age_s"] == 5.0
    assert "availability" not in rows[("atlas-core", "wsl")]
    assert rows[("ember", "linux")]["availability"]["status"] == "stale"
    assert rows[("ember", "linux")]["projection"]["freshness"]["bound_s"] == 90.0

    with patch.object(fleet.cfg, "load_config", return_value=config), \
         patch.object(fleet.cfg, "load_machines_yaml", return_value=_three_targets()), \
         patch.object(fleet.cfg, "project_name", return_value="agent-worktrees"), \
         patch.object(fleet.subprocess, "run", side_effect=_fake_run):
        assert fleet.run_fleet([]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert any("ember" in line and "(stale roster)" in line for line in lines)
    assert any("windows" in line and "(saved roster)" in line for line in lines)
    assert not any("wsl" in line and "roster)" in line for line in lines)


def test_unproven_current_reply_is_not_shown_as_a_plain_count(tmp_path, capsys):
    """An older resident answers ``quality: "current"`` without the freshness
    proof or an availability label; the human view still qualifies it, and the
    JSON row is labeled stale so a consumer cannot read it as zero work."""
    config = _fake_config(tmp_path, machine="atlas-core", platform="wsl")
    reply = {"worktrees": [], "projection": {"quality": "current", "observed_at": 1.0}}
    proc = SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr="")
    result = _run_json(["--json"], _three_targets(), config, run_side_effect=lambda *a, **k: proc)
    assert [row["availability"]["status"] for row in result["hosts"]] == ["stale"] * 3
    with patch.object(fleet.cfg, "load_config", return_value=config), \
         patch.object(fleet.cfg, "load_machines_yaml", return_value=_three_targets()), \
         patch.object(fleet.cfg, "project_name", return_value="agent-worktrees"), \
         patch.object(fleet.subprocess, "run", return_value=proc):
        assert fleet.run_fleet([]) == 0
    hosts = [line for line in capsys.readouterr().out.splitlines() if line.startswith("  ")]
    assert len(hosts) == 3
    assert all("0 worktree(s) (stale roster)" in line for line in hosts)


def test_runtime_without_passive_or_saved_roster_is_a_gap(tmp_path):
    """An older host runtime that rejects ``--passive`` (argparse exit 2), or
    a host with no resident and no saved roster (exit 1 with a JSON error),
    is unknown coverage -- never a reachable host with zero worktrees."""
    entries = {
        name: _entry(name, envs=[("linux", name)]) for name in ("older", "unsaved")
    }
    config = _fake_config(tmp_path, machine="observer", platform="wsl")
    outcomes = {
        "older": SimpleNamespace(
            returncode=2, stdout="",
            stderr="agent-worktrees: error: unrecognized arguments: --passive",
        ),
        "unsaved": SimpleNamespace(
            returncode=1,
            stdout='{"error": "connection refused; no usable advisory roster"}',
            stderr="",
        ),
    }

    result = _run_json(
        ["--json"], entries, config, run_side_effect=lambda cmd, **_: outcomes[cmd[-2]],
    )

    rows = {row["machine"]: row for row in result["hosts"]}
    assert "unrecognized arguments: --passive" in rows["older"]["error"]
    assert "no usable advisory roster" in rows["unsaved"]["error"]
    for row in rows.values():
        assert row["reachable"] is False
        assert row["worktrees"] == []