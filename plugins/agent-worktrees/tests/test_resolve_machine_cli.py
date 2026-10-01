"""Tests for `resolve_machine_cli`'s machine-label matching.

Covers the `hostname:`-only identification case: a machine declared with no
top-level `alias`, addressed only by its raw COMPUTERNAME via `hostname:`,
must still resolve -- `resolve --machine <raw-hostname>` previously fell
through to "unknown or unreachable remote machine" because the lookup only
matched `key`/`display_name`/`alias`, never `hostname`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import resolve_machine_cli as rmc

pytestmark = pytest.mark.guard


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
        "atlas-core": _entry(
            "atlas-core",
            hostname="CPC-FAKE-HOST1",
            envs=[("windows", "atlas-core")],
        ),
    }
    config = _fake_config(tmp_path)
    with patch.object(cfg, "load_machines_yaml", return_value=entries):
        assert rmc._machine_key_for_display(config, "CPC-FAKE-HOST1") == "atlas-core"
        # Case-insensitive, matching the existing key/alias/display_name checks.
        assert rmc._machine_key_for_display(config, "cpc-fake-host1") == "atlas-core"


def test_emit_remote_plan_for_env_resolves_by_hostname(tmp_path):
    """`_emit_remote_plan_for_env` loads `entries` once itself, then calls
    `_machine_key_for_display` which loads its *own* copy via a second
    `load_machines_yaml` call. A naive single `return_value` mock lets that
    inner call also resolve the hostname, so `entries.get(key)` would already
    succeed before this function's own fallback loop ever runs. Make the
    inner (second) `load_machines_yaml` call fail -- so `_machine_key_for_display`
    returns the name unchanged -- forcing this function's own hostname match
    to actually execute."""
    entries = {
        "atlas-core": _entry(
            "atlas-core",
            hostname="CPC-FAKE-HOST1",
            envs=[("windows", "atlas-core")],
        ),
    }
    config = _fake_config(tmp_path)
    with patch.object(
        cfg, "load_machines_yaml", side_effect=[entries, FileNotFoundError()]
    ), \
         patch.object(cfg, "project_name", return_value="example-project"), \
         patch.object(rmc, "_emit_plan") as emit_plan:
        rc = rmc._emit_remote_plan_for_env(config, "CPC-FAKE-HOST1", "Win", [])

    assert rc == 0
    emit_plan.assert_called_once()
    (plan,) = emit_plan.call_args.args
    assert plan["action"] == "remote"
    assert plan["ssh_alias"] == "atlas-core"
    assert plan["machine"] == "atlas-core"


def test_emit_remote_plan_for_env_still_unknown_for_unmatched_name(tmp_path):
    entries = {
        "atlas-core": _entry(
            "atlas-core",
            hostname="CPC-FAKE-HOST1",
            envs=[("windows", "atlas-core")],
        ),
    }
    config = _fake_config(tmp_path)
    with patch.object(cfg, "load_machines_yaml", return_value=entries):
        rc = rmc._emit_remote_plan_for_env(config, "some-other-box", "Win", [])

    assert rc is None
