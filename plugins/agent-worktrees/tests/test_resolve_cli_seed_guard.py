"""Local and structured SSH ``resolve --seed`` admission guards.

New and cold Resume stage prompt intent on the target. Remote text requires
non-preview JSON admission; interactive remote resolve and incompatible base,
bare-resume and restore modes refuse before mutation.
"""
from __future__ import annotations

import argparse
import contextlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, TypeVar, cast

import pytest
from machine_transport import MachineEntry

from agent_worktrees import config as cfg, local_cache_refresh, output, resolve_cli
from agent_worktrees.resolve_target import target_is_local

pytestmark = pytest.mark.guard
T = TypeVar("T")


def _capture(calls: list[T], value: T, result: int) -> int:
    calls.append(value)
    return result


@pytest.mark.parametrize("setting", [None, "0", "1"])
def test_resolve_projection_deferral_defaults_to_launcher_environment(
    monkeypatch: pytest.MonkeyPatch, setting: str | None,
) -> None:
    if setting is None:
        monkeypatch.delenv("AGENT_WORKTREES_DEFER_PROJECTIONS", raising=False)
    else:
        monkeypatch.setenv("AGENT_WORKTREES_DEFER_PROJECTIONS", setting)
    parser = argparse.ArgumentParser()
    resolve_cli.add_parsers(parser.add_subparsers())
    assert parser.parse_args(["resolve"]).defer_projections is (setting == "1")
    assert parser.parse_args(["resolve", "--defer-projections"]).defer_projections is True


def _args(**overrides: object) -> argparse.Namespace:
    base: dict[str, object] = dict(
        json=True, base=False, new_worktree=True, auto=False,
        machine="example-host", environment=None, worktree_id=None,
        codename=None, seed=None, bare_resume=False, restore=False,
        target_no_mux=False, no_mux=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _config(tmp_path: Path, machine: str) -> cfg.Config:
    return cfg.Config(
        machine=machine, platform="windows", repo_name="example", srcroot=str(tmp_path),
        repos={"example": cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path))},
    )


def test_resolve_new_with_machine_and_seed_preview_is_rejected_json(capfd: pytest.CaptureFixture[str]) -> None:
    rc = resolve_cli.cmd_resolve(_args(seed="do the thing", dry_run=True))

    assert rc != 0
    out = json.loads(capfd.readouterr().out)
    assert "seed" in out.get("error", "").lower()
    assert "machine" in out.get("error", "").lower()


def test_resolve_new_with_machine_and_seed_is_rejected_non_json(capfd: pytest.CaptureFixture[str]) -> None:
    """Regression for the non-JSON dispatcher, which checks ``use_new``
    before ``requested_machine`` and would otherwise never see --machine
    at all for this combination."""
    rc = resolve_cli.cmd_resolve(_args(json=False, seed="do the thing"))

    assert rc != 0
    out = capfd.readouterr().out
    assert "seed" in out.lower()
    assert "machine" in out.lower()


def test_resolve_new_with_machine_and_no_seed_is_unaffected(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """The rejection is seed-specific -- an ordinary remote --new (no
    --seed) must still reach the existing remote-dispatch path untouched."""
    state = resolve_cli.ResolveCommandState.from_args(_args(seed=None))
    state.config = _config(tmp_path, "")
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda _, **_kwargs: {})
    called: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *a, **k: _capture(called, (a, k), 0),
    )

    rc = resolve_cli._resolve_json_mode(state)

    assert rc == 0
    assert called  # reached the remote-dispatch call, unaffected by the guard


def test_resolve_machine_matching_the_local_machine_is_a_no_op_not_a_self_ssh_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: `--machine <this machine's own name>` previously skipped
    straight to `_emit_remote_plan_for_env` with no local-machine check at
    all (unlike the non-JSON/interactive path's own
    `requested_machine != config.machine` guard before its equivalent
    `_try_machine_handoff` call) -- so a caller already running on the named
    machine got handed back a real "ssh <this box>" plan instead of ordinary
    local resolution. Confirmed live via `resolve --machine <local-name>
    --base --json`."""
    state = resolve_cli.ResolveCommandState.from_args(_args(seed=None))
    state.config = cast(cfg.Config, SimpleNamespace(machine=state.requested_machine))  # deliberately incomplete
    called: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *a, **k: _capture(called, (a, k), 0),
    )

    with contextlib.suppress(Exception):  # fake config is too bare for the local path to finish
        resolve_cli._resolve_json_mode(state)

    assert not called  # must never dispatch to itself over SSH


def test_resolve_machine_matching_local_but_environment_cross_platform_still_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the Windows Worktree Picker launching a same-machine
    WSL worktree (e.g. `--machine some-host --environment WSL` run from
    that same host's own Windows side): the self-targeting no-op above must
    NOT swallow this case merely because `--machine` names the local
    machine -- that worktree lives in a different OS/filesystem with its
    own tracking directory, invisible to this process's local
    `cfg.tracking_dir()`. Before the fix this fell through to local
    resolution and failed closed with "Worktree not found" instead of
    dispatching the SSH handoff into the requested environment."""
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    state = resolve_cli.ResolveCommandState.from_args(
        _args(seed=None, environment="WSL")
    )
    state.config = cast(cfg.Config, SimpleNamespace(machine=state.requested_machine))  # deliberately incomplete
    called: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *a, **k: _capture(called, (a, k), 0),
    )

    rc = resolve_cli._resolve_json_mode(state)

    assert rc == 0
    assert called  # must still dispatch -- the environment differs from "windows"


def test_resolve_machine_matching_local_and_environment_matches_current_platform_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sibling of the above: when `--environment` is given but it names the
    SAME platform this process is already running on (e.g. `--environment
    Win` from Windows), the self-targeting no-op still applies -- this is
    genuinely local, not a cross-platform handoff."""
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    state = resolve_cli.ResolveCommandState.from_args(
        _args(seed=None, environment="Win")
    )
    state.config = cast(cfg.Config, SimpleNamespace(machine=state.requested_machine))  # deliberately incomplete
    called: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *a, **k: _capture(called, (a, k), 0),
    )

    with contextlib.suppress(Exception):  # fake config is too bare for the local path to finish
        resolve_cli._resolve_json_mode(state)

    assert not called  # same machine, same platform -- must stay local


def test_resolve_machine_differing_from_the_local_machine_still_dispatches_remotely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Sibling of the self-match regression above: a genuinely different
    `--machine` must still reach `_emit_remote_plan_for_env` -- the new guard
    is specific to the self-match case, not a blanket skip."""
    state = resolve_cli.ResolveCommandState.from_args(_args(seed=None))
    state.config = _config(tmp_path, "a-different-machine")
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda _, **_kwargs: {})
    called: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *a, **k: _capture(called, (a, k), 0),
    )

    rc = resolve_cli._resolve_json_mode(state)

    assert rc == 0
    assert called


@pytest.mark.parametrize("target", [None, "", "EXAMPLE-HOST", "raw-host", "local-alias", "Example Host"])
@pytest.mark.parametrize("environment,remote", [("Win", False), ("WSL", True)])
def test_json_launch_canonicalizes_self_target_before_selecting_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: str | None,
    environment: str, remote: bool,
) -> None:
    entry = MachineEntry(
        key="example-host", hostname="raw-host", alias="local-alias",
        display_name="Example Host",
    )
    config = _config(tmp_path, "example-host")
    state = resolve_cli.ResolveCommandState.from_args(
        _args(new_worktree=False, base=True, machine=target, environment=environment)
    )
    state.config = config
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda _, **_kwargs: {entry.key: entry})
    calls: list[str] = []
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *args, **kwargs: _capture(calls, "remote", 7),
    )
    monkeypatch.setattr(
        resolve_cli, "_preflight_launch",
        lambda *args: SimpleNamespace(error=None),
    )
    monkeypatch.setattr(local_cache_refresh, "prepare_for_launch", lambda *a, **k: None)
    monkeypatch.setattr(resolve_cli, "_build_launch_cmd", lambda *a, **k: ["local-launch"])
    monkeypatch.setattr(resolve_cli, "_repo_session_env", lambda *a: {})
    monkeypatch.setattr(resolve_cli, "_build_env", lambda *a, **k: {})
    monkeypatch.setattr(
        output, "_json_output",
        lambda plan: calls.append("local") if plan["action"] == "exec" else None,
    )

    dispatch_remote = remote and bool(target)
    assert resolve_cli._resolve_json_mode(state) == (7 if dispatch_remote else 0)
    assert calls == ["remote" if dispatch_remote else "local"]


def test_self_identity_comparison_reads_the_declared_target_before_local_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr("agent_worktrees.execution_spaces.require_owner_identity",
                        lambda *args, **kwargs: True)
    reads: list[str] = []
    def declared_targets(anchor: str, **kwargs: object) -> dict[str, MachineEntry]:
        reads.append(anchor)
        return {}

    monkeypatch.setattr(cfg, "load_machines_yaml", declared_targets)
    assert target_is_local(
        _config(tmp_path, "example-host"), "EXAMPLE-HOST"
    )
    assert reads == [str(tmp_path)]


def test_self_alias_rejects_unknown_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str],
) -> None:
    state = resolve_cli.ResolveCommandState.from_args(
        _args(new_worktree=False, base=True, machine="local-alias", environment="Bogus")
    )
    state.config = _config(tmp_path, "example-host")
    monkeypatch.setattr(
        cfg, "load_machines_yaml",
        lambda _, **_kwargs: {"example-host": MachineEntry(
            key="example-host", alias="local-alias", display_name="Example Host",
        )},
    )
    assert resolve_cli._resolve_json_mode(state) != 0
    assert "Unknown environment" in json.loads(capfd.readouterr().out)["error"]


@pytest.mark.parametrize("target,environment,expected", [
    ("local-alias", "WSL", 7),
    ("local-alias", "Bogus", 1),
    ("remote-host", "WSL", 7),
    ("remote-host", "Win", 7),
    ("remote-host", "Bogus", 1),
])
def test_interactive_self_alias_honors_explicit_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: str, environment: str, expected: int,
) -> None:
    config = _config(tmp_path, "example-host")
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cfg, "peek_base_repo", lambda: False)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        resolve_cli.machine_identity, "is_local_machine",
        lambda name, config: name == "local-alias",
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    calls: list[str] = []
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *a, **k: _capture(calls, "remote", 7),
    )
    rc = resolve_cli.cmd_resolve(
        _args(json=False, new_worktree=False, machine=target, environment=environment)
    )
    assert rc == expected
    assert calls == (["remote"] if environment != "Bogus" else [])


@pytest.mark.parametrize("reverse", [False, True])
def test_ambiguous_self_alias_never_launches_locally(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str], reverse: bool,
) -> None:
    entries = [
        MachineEntry(key="example-host", alias="shared", display_name="Local"),
        MachineEntry(key="remote-host", alias="shared", display_name="Remote"),
    ]
    if reverse:
        entries.reverse()
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda _, **_kwargs: {e.key: e for e in entries})
    state = resolve_cli.ResolveCommandState.from_args(
        _args(new_worktree=False, base=True, machine="shared", environment="Win")
    )
    state.config = _config(tmp_path, "example-host")
    assert resolve_cli._resolve_json_mode(state) != 0
    assert "ambig" in json.loads(capfd.readouterr().out)["error"].lower()


def test_interactive_environment_handoff_rejects_nested_ssh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: _config(tmp_path, "example-host"))
    monkeypatch.setattr(cfg, "peek_base_repo", lambda: False)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setenv("SSH_CONNECTION", "192.0.2.1 1234 192.0.2.2 22")
    assert resolve_cli.cmd_resolve(
        _args(json=False, new_worktree=False, machine="example-host", environment="WSL")
    ) != 0


def test_resolve_worktree_id_with_machine_and_seed_preview_is_rejected_json(capfd: pytest.CaptureFixture[str]) -> None:
    """The same remote-target rejection applies to a --worktree-id resume
    seed, not only --new."""
    rc = resolve_cli.cmd_resolve(
        _args(new_worktree=False, worktree_id="some-wt", seed="do the thing", dry_run=True)
    )

    assert rc != 0
    out = json.loads(capfd.readouterr().out)
    assert "seed" in out.get("error", "").lower()
    assert "machine" in out.get("error", "").lower()


@pytest.mark.parametrize("new", [False, True])
def test_remote_prompt_cli_routes_same_seed_selectors_and_no_mux(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, new: bool,
) -> None:
    config = _config(tmp_path, "local")
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    called: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *a, **k: _capture(called, (a, k), 0),
    )
    args = _args(new_worktree=new, worktree_id=None if new else "wt-a",
                 seed="line one\nquotes ' $()", environment="WSL", target_no_mux=True)
    assert resolve_cli.cmd_resolve(args) == 0
    route, keywords = called[0]
    assert route[:3] == (config, "example-host", "WSL")
    assert route[3] == (
        ["--new", "--no-mux"] if new
        else ["--worktree-id", "wt-a", "--no-mux"]
    )
    assert keywords == {"seed": args.seed}


def test_resolve_worktree_id_with_seed_and_no_machine_is_accepted(capfd: pytest.CaptureFixture[str]) -> None:
    """resume-prompt-durable-seed-and-mux-fix: --seed is now valid alongside
    --worktree-id (not only --new) -- the guard must not reject this
    combination merely because --new is absent."""
    rc = resolve_cli.cmd_resolve(
        _args(
            new_worktree=False, worktree_id="some-wt", seed="do the thing",
            machine=None,
        )
    )

    # Past the seed guard entirely -- the call proceeds to resolve the
    # worktree (and fails for an unrelated reason: no such tracked worktree
    # in this test's environment), never the "--seed is only valid with"
    # rejection this guard owns.
    assert rc != 0
    out = json.loads(capfd.readouterr().out)
    assert "seed" not in out.get("error", "").lower()


def test_resolve_base_with_seed_is_still_rejected_json(capfd: pytest.CaptureFixture[str]) -> None:
    """--base has no worktree record and no resume-context launch argv for
    a seed to reach -- the guard must keep rejecting this combination."""
    rc = resolve_cli.cmd_resolve(
        _args(new_worktree=False, base=True, seed="do the thing", machine=None)
    )

    assert rc != 0
    out = json.loads(capfd.readouterr().out)
    assert "seed" in out.get("error", "").lower()


def test_resolve_worktree_id_with_bare_resume_and_seed_is_rejected_json(capfd: pytest.CaptureFixture[str]) -> None:
    """--bare-resume launches Copilot in HOME with no --resume at all
    (dodging a cwd-start bug) -- there is no resumed conversation, and
    arguably no well-defined worktree session, for a seed to join. Without
    this guard the combination used to exit 0 while silently discarding
    the prompt; it must now be rejected explicitly, the same as --base and
    a remote --machine target."""
    rc = resolve_cli.cmd_resolve(
        _args(
            new_worktree=False, worktree_id="some-wt", seed="do the thing",
            machine=None, bare_resume=True,
        )
    )

    assert rc != 0
    out = json.loads(capfd.readouterr().out)
    assert "seed" in out.get("error", "").lower()
    assert "bare" in out.get("error", "").lower()


def test_resolve_worktree_id_with_bare_resume_and_no_seed_is_unaffected(capfd: pytest.CaptureFixture[str]) -> None:
    """The new bare-resume guard must only fire when --seed is actually
    requested -- an ordinary --bare-resume call (no --seed) must proceed
    past the guard untouched."""
    rc = resolve_cli.cmd_resolve(
        _args(
            new_worktree=False, worktree_id="some-wt", seed=None,
            machine=None, bare_resume=True,
        )
    )

    # Past the seed guard entirely -- fails for an unrelated reason (no
    # such tracked worktree in this test's environment), never the
    # "--bare-resume" rejection this guard owns.
    assert rc != 0
    out = json.loads(capfd.readouterr().out)
    assert "bare" not in out.get("error", "").lower()


def test_restore_with_prompt_is_rejected_before_remux(capfd: pytest.CaptureFixture[str]) -> None:
    rc = resolve_cli.cmd_resolve(
        _args(
            new_worktree=False, worktree_id="some-wt", seed="must not run",
            machine=None, restore=True,
        )
    )
    assert rc != 0
    payload = json.loads(capfd.readouterr().out)
    assert "--restore" in payload["error"]
