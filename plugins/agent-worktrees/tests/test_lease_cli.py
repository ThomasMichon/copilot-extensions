"""Tests for `agent-worktrees lease` disposition round-trip (Phase 1).

Exercises the CLI wiring (`--disposition` sugar over the context key) end-to-end
against a real scratch bare remote, reusing the store test's git helper.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agent_worktrees import lease_cli
from agent_worktrees import obligations as ob
from agent_worktrees.lease_config import ConfigError
from agent_worktrees.lease_cli import run_lease


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    path = tmp_path / "coordination.git"
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    subprocess.run(["git", "init", "--bare", str(path)], check=True, env=env,
                   capture_output=True, text=True)
    return path


def _run(capsys, *argv: str) -> dict:
    rc = run_lease(list(argv))
    out = capsys.readouterr().out
    assert rc == 0, out
    return json.loads(out)


def test_missing_private_store_fails_with_remediation(monkeypatch, capsys):
    def fail_settings(*, origin=None):
        raise ConfigError(
            "lease store is not configured: bind a private knowledge repo or "
            "set AGENT_WORKTREES_LEASE_ORIGIN/--origin explicitly"
        )

    monkeypatch.setattr(lease_cli, "load_lease_settings", fail_settings)

    assert run_lease(["list"]) == 2
    assert "bind a private knowledge repo" in capsys.readouterr().err


def test_acquire_with_disposition_rides_context(remote: Path, capsys):
    data = _run(
        capsys, "acquire", "codespace", "cs-1",
        "--holder", "m/p/w", "--origin", str(remote), "--disposition", "at-rest",
    )
    assert data["context"]["disposition"] == "at-rest"
    assert ob.from_context(data["context"]) == ob.AT_REST


def test_acquire_defaults_have_no_disposition(remote: Path, capsys):
    data = _run(
        capsys, "acquire", "codespace", "cs-2",
        "--holder", "m/p/w", "--origin", str(remote),
    )
    # No --disposition -> no disposition key; a reader still degrades to active.
    assert "disposition" not in data["context"]
    assert ob.from_context(data["context"]) == ob.ACTIVE


def test_renew_advances_disposition_to_at_rest(remote: Path, capsys):
    acq = _run(
        capsys, "acquire", "codespace", "cs-3",
        "--holder", "m/p/w", "--origin", str(remote),
    )
    token = acq["token"]
    renewed = _run(
        capsys, "renew", "codespace", "cs-3",
        "--token", token, "--origin", str(remote), "--disposition", "at-rest",
    )
    assert renewed["context"]["disposition"] == "at-rest"

    # inspect confirms the settled disposition is durable on the ref.
    seen = _run(capsys, "inspect", "codespace", "cs-3", "--origin", str(remote))
    assert ob.from_context(seen["context"]) == ob.AT_REST


def test_renew_without_flags_preserves_existing_disposition(remote: Path, capsys):
    _run(
        capsys, "acquire", "codespace", "cs-4",
        "--holder", "m/p/w", "--origin", str(remote), "--disposition", "at-rest",
    )
    acq = _run(capsys, "inspect", "codespace", "cs-4", "--origin", str(remote))
    token = acq["token"]
    # A plain renew (no --context, no --disposition) must keep the prior context.
    renewed = _run(
        capsys, "renew", "codespace", "cs-4",
        "--token", token, "--origin", str(remote),
    )
    assert ob.from_context(renewed["context"]) == ob.AT_REST


def test_disposition_rejects_unknown_value(remote: Path, capsys):
    with pytest.raises(SystemExit):
        run_lease([
            "acquire", "codespace", "cs-5",
            "--holder", "m/p/w", "--origin", str(remote), "--disposition", "bogus",
        ])
