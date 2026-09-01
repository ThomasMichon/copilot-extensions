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
from agent_worktrees import lease_config
from agent_worktrees import obligations as ob
from agent_worktrees import config as cfg
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


@pytest.fixture(autouse=True)
def standalone_acquisition(monkeypatch, tmp_path):
    repo = cfg.RepoConfig(
        anchor=str(tmp_path),
        worktree_root=str(tmp_path / "worktrees"),
        remote="origin",
    )
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: cfg.Config(
            srcroot=str(tmp_path),
            machine="test",
            platform="windows",
            repo_name="self",
            repos={"self": repo},
        ),
    )


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


def test_unbound_acquire_emits_structured_readiness_without_store_access(
    monkeypatch,
    capsys,
):
    repo = cfg.RepoConfig(
        anchor="/shared/harness",
        worktree_root="/shared/harness.worktrees",
        remote="origin",
        stateless=True,
        requires_external_state_root=True,
    )
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: cfg.Config(
            srcroot="/shared",
            machine="test",
            platform="windows",
            repo_name="harness",
            repos={"harness": repo},
            knowledge_repo="",
        ),
    )
    monkeypatch.setattr(
        lease_cli,
        "GitLeaseStore",
        lambda settings: pytest.fail("unbound acquisition constructed a store"),
    )

    rc = run_lease([
        "acquire",
        "codespace",
        "blocked",
        "--holder",
        "m/p/w",
        "--origin",
        "https://example.test/state.git",
    ])

    assert rc == 5
    payload = json.loads(capsys.readouterr().err)
    assert payload["code"] == "knowledge_binding_required"
    assert payload["coordination_readiness"]["version"] == 1


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


@pytest.mark.parametrize("origin_source", ["argument", "environment"])
def test_renew_and_release_do_not_run_acquisition_preflight(
    remote: Path,
    capsys,
    monkeypatch,
    origin_source,
):
    settings = lease_config.load_lease_settings(origin=str(remote))
    acquired = lease_cli.GitLeaseStore(settings).acquire(
        "codespace", "existing", "m/p/w"
    )
    monkeypatch.setattr(
        lease_cli,
        "load_acquisition_lease_settings",
        lambda **kwargs: pytest.fail("existing ownership ran acquisition preflight"),
    )
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: pytest.fail("existing ownership resolved current binding"),
    )
    origin_args = ["--origin", str(remote)]
    if origin_source == "environment":
        monkeypatch.setenv(lease_config.ORIGIN_ENV, str(remote))
        origin_args = []

    renewed = _run(
        capsys,
        "renew",
        "codespace",
        "existing",
        "--token",
        acquired.oid,
        *origin_args,
    )
    released = _run(
        capsys,
        "release",
        "codespace",
        "existing",
        "--token",
        renewed["token"],
        *origin_args,
    )
    assert released["state"] == "released"


def test_disposition_rejects_unknown_value(remote: Path, capsys):
    with pytest.raises(SystemExit):
        run_lease([
            "acquire", "codespace", "cs-5",
            "--holder", "m/p/w", "--origin", str(remote), "--disposition", "bogus",
        ])
