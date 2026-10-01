"""Tests for `resolve_machine_cli`'s machine-label matching.

Covers the `hostname:`-only identification case (machines.yaml, gim-home/
odsp-web-harness support thread 2026-09-30): a machine declared with no
top-level `alias`, addressed only by its raw COMPUTERNAME via `hostname:`,
must still resolve -- `resolve --machine <raw-hostname>` previously fell
through to "unknown or unreachable remote machine" because the lookup only
matched `key`/`display_name`/`alias`, never `hostname`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from agent_worktrees import config as cfg
from agent_worktrees import resolve_machine_cli as rmc


def _entry(key, *, hostname="", alias="", envs=()):
    return cfg.MachineEntry(
        key=key,
        display_name=key,
        environment=envs[0][0] if envs else "",
        hostname=hostname,
        alias=alias,
        ssh_environments=[
            cfg.SSHEnvironment(name=name, alias=alias_) for name, alias_ in envs
        ],
    )


def _fake_config(tmp_path):
    return SimpleNamespace(default_repo=SimpleNamespace(anchor=str(tmp_path)))


def test_machine_key_for_display_matches_hostname(tmp_path):
    entries = {
        "tmichon-cloud2": _entry(
            "tmichon-cloud2",
            hostname="CPC-tmich-Y97MC",
            envs=[("windows", "tmichon-cloud2")],
        ),
    }
    config = _fake_config(tmp_path)
    with patch.object(cfg, "load_machines_yaml", return_value=entries):
        assert rmc._machine_key_for_display(config, "CPC-tmich-Y97MC") == "tmichon-cloud2"
        # Case-insensitive, matching the existing key/alias/display_name checks.
        assert rmc._machine_key_for_display(config, "cpc-tmich-y97mc") == "tmichon-cloud2"


def test_emit_remote_plan_for_env_resolves_by_hostname(tmp_path):
    entries = {
        "tmichon-cloud2": _entry(
            "tmichon-cloud2",
            hostname="CPC-tmich-Y97MC",
            envs=[("windows", "tmichon-cloud2")],
        ),
    }
    config = _fake_config(tmp_path)
    with patch.object(cfg, "load_machines_yaml", return_value=entries), \
         patch.object(cfg, "project_name", return_value="odsp-web-harness"), \
         patch.object(rmc, "_emit_plan") as emit_plan:
        rc = rmc._emit_remote_plan_for_env(config, "CPC-tmich-Y97MC", "Win", [])

    assert rc == 0
    emit_plan.assert_called_once()
    (plan,) = emit_plan.call_args.args
    assert plan["action"] == "remote"
    assert plan["ssh_alias"] == "tmichon-cloud2"
    assert plan["machine"] == "tmichon-cloud2"


def test_emit_remote_plan_for_env_still_unknown_for_unmatched_name(tmp_path):
    entries = {
        "tmichon-cloud2": _entry(
            "tmichon-cloud2",
            hostname="CPC-tmich-Y97MC",
            envs=[("windows", "tmichon-cloud2")],
        ),
    }
    config = _fake_config(tmp_path)
    with patch.object(cfg, "load_machines_yaml", return_value=entries):
        rc = rmc._emit_remote_plan_for_env(config, "some-other-box", "Win", [])

    assert rc is None
