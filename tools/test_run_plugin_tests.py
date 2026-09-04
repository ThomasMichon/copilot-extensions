"""Focused contract tests for repository test-runner admission."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "run-plugin-tests.py"
_previous_path = sys.path.copy()
sys.path.insert(0, str(SCRIPT.parent))
try:
    _spec = importlib.util.spec_from_file_location("run_plugin_tests", SCRIPT)
    assert _spec and _spec.loader
    runner = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = runner
    _spec.loader.exec_module(runner)

    import plugin_test_containment as containment
    import pytest_portfolio_guard as portfolio_guard
finally:
    sys.path[:] = _previous_path


def test_default_environment_redirects_all_mutable_roots(tmp_path: Path) -> None:
    env = containment.isolated_environment(
        {
            "PATH": os.environ.get("PATH", ""),
            "HOME": "/real/home",
            "AGENT_WORKTREES_HOME": "/real/agent-worktrees",
            "AGENT_WORKTREES_PROJECTS_YAML": "/real/projects.yaml",
            "GH_TOKEN": "not-a-real-token",
            "COPILOT_AGENT_SESSION_ID": "live-session",
            "AGENT_WORKTREES_OWNER_REF": "live-owner",
        },
        tmp_path,
    )

    assert env["PATH"] == os.environ.get("PATH", "")
    assert "GH_TOKEN" not in env
    assert "COPILOT_AGENT_SESSION_ID" not in env
    assert "AGENT_WORKTREES_OWNER_REF" not in env
    for name in containment.ROOT_ENV_NAMES:
        Path(env[name]).resolve().relative_to(tmp_path.resolve())
    assert "AGENT_CONTAINERS_CONFIG" not in env
    Path(env["AGENT_WORKTREES_PROJECTS_YAML"]).resolve().relative_to(
        tmp_path.resolve()
    )


def test_optional_file_override_is_validated_only_when_present(
    monkeypatch,
    tmp_path: Path,
) -> None:
    env = containment.isolated_environment(os.environ, tmp_path)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    for name in containment.OPTIONAL_FILE_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    portfolio_guard.validate_contained_environment()

    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(tmp_path.parent / "config.yaml"))
    with pytest.raises(pytest.UsageError, match="AGENT_CONTAINERS_CONFIG"):
        portfolio_guard.validate_contained_environment()


def test_host_state_opt_in_preserves_credentials_not_session_affinity(
    tmp_path: Path,
) -> None:
    env = containment.isolated_environment(
        {
            "HOME": "/real/home",
            "GH_TOKEN": "not-a-real-token",
            "COPILOT_AGENT_SESSION_ID": "live-session",
            "AGENT_WORKTREES_OWNER_REF": "live-owner",
        },
        tmp_path,
        allow_explicit_tiers=True,
        allow_host_state=True,
    )

    assert env["HOME"] == "/real/home"
    assert env["GH_TOKEN"] == "not-a-real-token"
    assert env[containment.ALLOW_HOST_STATE_ENV] == "1"
    assert "COPILOT_AGENT_SESSION_ID" not in env
    assert "AGENT_WORKTREES_OWNER_REF" not in env
    for name in containment.ALWAYS_SANDBOX_ENV_NAMES:
        Path(env[name]).resolve().relative_to(tmp_path.resolve())


def test_host_state_requires_explicit_tiers_at_environment_boundary(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError,
        match="allow_host_state requires allow_explicit_tiers",
    ):
        containment.isolated_environment(
            os.environ,
            tmp_path,
            allow_host_state=True,
        )


def test_host_state_still_fails_closed_on_escaped_temp(
    monkeypatch,
    tmp_path: Path,
) -> None:
    env = containment.isolated_environment(
        os.environ,
        tmp_path,
        allow_explicit_tiers=True,
        allow_host_state=True,
    )
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TMPDIR", str(tmp_path.parent))

    with pytest.raises(pytest.UsageError, match="TMPDIR"):
        portfolio_guard.validate_contained_environment()


def test_admission_fails_fast_with_live_holder(monkeypatch, tmp_path: Path) -> None:
    class BusyLease:
        def acquire(self) -> None:
            raise runner.AlreadyRunningError(tmp_path / "runner.lock", 123)

    monkeypatch.setattr(runner, "SingleInstance", lambda *_args, **_kwargs: BusyLease())

    with pytest.raises(runner.AlreadyRunningError) as exc:
        runner._acquire_admission(0)
    assert exc.value.holder_pid == 123


def test_admission_wait_is_bounded_and_retries(monkeypatch) -> None:
    class EventuallyAvailableLease:
        calls = 0

        def acquire(self) -> None:
            self.calls += 1
            if self.calls == 1:
                raise runner.AlreadyRunningError(Path("runner.lock"), 123)

    lease = EventuallyAvailableLease()
    clock = iter((10.0, 10.0))
    monkeypatch.setattr(runner, "SingleInstance", lambda *_args, **_kwargs: lease)
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)

    assert runner._acquire_admission(1.0) is lease
    assert lease.calls == 2


def test_heavy_run_holds_admission_for_all_targets(monkeypatch) -> None:
    events: list[str] = []

    class Lease:
        def release(self) -> None:
            events.append("release")

    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    monkeypatch.setattr(
        runner,
        "_acquire_admission",
        lambda _wait: events.append("acquire") or Lease(),
    )
    monkeypatch.setattr(
        runner,
        "run_plugin",
        lambda name, *_args, **_kwargs: events.append(f"run:{name}") or 0,
    )

    assert runner.main(["alpha", "beta"]) == 0
    assert events == ["acquire", "run:alpha", "run:beta", "release"]


def test_guards_remain_available_without_heavy_admission(monkeypatch) -> None:
    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    monkeypatch.setattr(
        runner,
        "_acquire_admission",
        lambda _wait: pytest.fail("guard runs must not take the heavy-test slot"),
    )
    monkeypatch.setattr(runner, "run_plugin", lambda *_args, **_kwargs: 0)

    assert runner.main(["alpha", "--guards"]) == 0


def test_host_state_requires_explicit_tier_opt_in(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        runner.main(["--allow-host-state"])
    assert exc.value.code == 2
    assert "--allow-host-state requires --allow-explicit-tiers" in capsys.readouterr().err
