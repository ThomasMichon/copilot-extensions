"""Tests for `agent-worktrees lease` disposition round-trip (Phase 1).

Exercises the CLI wiring (`--disposition` sugar over the context key) end-to-end
against a real scratch bare remote, reusing the store test's git helper.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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
    renewed = _run(
        capsys, "renew", "codespace", "cs-1",
        "--token", data["token"], "--origin", str(remote),
    )
    assert ob.from_context(renewed["context"]) == ob.AT_REST


def test_acquire_defaults_have_no_disposition(remote: Path, capsys):
    data = _run(
        capsys, "acquire", "codespace", "cs-2",
        "--holder", "m/p/w", "--origin", str(remote),
    )
    # No --disposition -> no disposition key; a reader still degrades to active.
    assert "disposition" not in data["context"]
    assert ob.from_context(data["context"]) == ob.ACTIVE
    renewed = _run(
        capsys, "renew", "codespace", "cs-2",
        "--token", data["token"], "--origin", str(remote), "--disposition", "at-rest",
    )
    assert renewed["context"]["disposition"] == "at-rest"

    # inspect confirms the settled disposition is durable on the ref.
    seen = _run(capsys, "inspect", "codespace", "cs-2", "--origin", str(remote))
    assert ob.from_context(seen["context"]) == ob.AT_REST


def test_released_only_cli_refuses_expiry_but_accepts_release(remote, capsys, monkeypatch):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(lease_cli.GitLeaseStore, "_utc_now", lambda self: now)
    acquired = _run(
        capsys, "acquire", "codespace", "sequential",
        "--holder", "first/project/work", "--origin", str(remote),
        "--ttl", "60", "--released-only",
    )
    now += timedelta(hours=1)
    assert run_lease([
        "borrow", "codespace", "sequential", "--holder", "next/project/work",
        "--origin", str(remote), "--released-only",
    ]) == 3
    capsys.readouterr()
    unchanged = _run(capsys, "inspect", "codespace", "sequential", "--origin", str(remote))
    assert unchanged["token"] == acquired["token"]
    assert unchanged["live"] is False
    assert unchanged["state"] == "leased"
    # The existing default retains its explicit stale-takeover semantics.
    taken = _run(
        capsys, "acquire", "codespace", "sequential",
        "--holder", "first/project/work", "--origin", str(remote),
    )
    assert taken["event"] == "takeover"
    _run(
        capsys, "release", "codespace", "sequential",
        "--token", taken["token"], "--origin", str(remote),
    )
    reused = _run(
        capsys, "borrow", "codespace", "sequential",
        "--holder", "next/project/work", "--origin", str(remote), "--released-only",
    )
    assert reused["event"] == "acquire"
    assert reused["holder"] == "next/project/work"


def test_return_proof_cli_uses_real_separate_receiver(remote, capsys):
    from agent_procutil import no_window_kwargs

    acquired = _run(
        capsys, "acquire", "codespace", "return-proof",
        "--holder", "first/project/work", "--origin", str(remote),
    )
    invocation = [
        sys.executable, "-c",
        "import sys; from agent_worktrees.lease_cli import run_lease; "
        "raise SystemExit(run_lease(sys.argv[1:]))",
    ]
    returned = subprocess.run(
        [*invocation, "release", "codespace", "return-proof", "--token", acquired["token"],
         "--retain-return-proof", "--origin", str(remote)],
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert returned.returncode == 0, returned.stderr
    result = json.loads(returned.stdout)
    assert result["schema_version"] == 2
    assert result["returned_generations"] == [{
        "lease_id": acquired["lease_id"], "holder": acquired["holder"],
        "released_at": result["renewed_at"],
    }]
    reused = _run(
        capsys, "borrow", "codespace", "return-proof",
        "--holder", "next/project/work", "--origin", str(remote), "--released-only",
    )
    assert reused["returned_generations"] == result["returned_generations"]
    assert reused["holder"] == "next/project/work"


def test_return_proof_legacy_codec_refuses_without_overwrite(remote, capsys, monkeypatch):
    from agent_worktrees import lease_store
    from agent_worktrees.lease_protocol import ProtocolError

    acquired = _run(
        capsys, "acquire", "codespace", "rollback",
        "--holder", "first/project/work", "--origin", str(remote),
    )
    assert acquired["schema_version"] == 1
    assert "returned_generations" not in acquired
    released = _run(
        capsys, "release", "codespace", "rollback", "--token", acquired["token"],
        "--retain-return-proof", "--origin", str(remote),
    )
    parse = lease_store.parse_record

    def legacy_codec(message, expected=None):
        if json.loads(message.split("\n", 1)[1])["schema_version"] != 1:
            raise ProtocolError("unsupported lease schema_version")
        return parse(message, expected)

    with monkeypatch.context() as legacy:
        legacy.setattr(lease_store, "parse_record", legacy_codec)
        assert run_lease([
            "acquire", "codespace", "rollback", "--holder", "next/project/work",
            "--origin", str(remote),
        ]) == 2
        assert "unsupported lease schema_version" in capsys.readouterr().err
    unchanged = _run(capsys, "inspect", "codespace", "rollback", "--origin", str(remote))
    assert unchanged == released
    baseline = _run(
        capsys, "acquire", "codespace", "legacy-baseline",
        "--holder", "first/project/work", "--origin", str(remote),
    )
    ordinary = _run(
        capsys, "release", "codespace", "legacy-baseline", "--token", baseline["token"],
        "--origin", str(remote),
    )
    assert ordinary["schema_version"] == 1
    assert "returned_generations" not in ordinary


def test_return_proof_cli_typo_is_rejected_before_store_access(monkeypatch, capsys):
    monkeypatch.setattr(
        lease_cli, "GitLeaseStore", lambda *args: pytest.fail("invalid syntax reached the store"),
    )
    with pytest.raises(SystemExit) as error:
        run_lease([
            "release", "codespace", "typo", "--token", "a" * 40, "--retain-return-proofs",
        ])
    assert error.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


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
