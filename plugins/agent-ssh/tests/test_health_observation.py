"""Provider-only contracts; no production profile or credential is a fixture."""

from __future__ import annotations

from argparse import Namespace
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from types import SimpleNamespace
from typing import Any, NoReturn

import pytest
from agent_procutil.owned_process import OutputLimitExceeded, OwnedCleanupError

from agent_ssh import health_observation as health

RouteFiles = tuple[Path, Path, Path]


@pytest.fixture
def route_files(tmp_path: Path) -> RouteFiles:
    identity = tmp_path / "synthetic key"
    identity.write_text("not a private key", encoding="utf-8")
    known_hosts = tmp_path / "known hosts"
    known_hosts.write_text("# synthetic fixture\n", encoding="utf-8")
    profile = tmp_path / "direct.conf"
    profile.write_text(
        'Host target-a\n  HostName 127.0.0.1\n  User fixture\n'
        f'  IdentityFile "{identity}"\n  IdentitiesOnly yes\n'
        '  StrictHostKeyChecking yes\n',
        encoding="utf-8",
    )
    return profile, identity, known_hosts


def test_observation_keeps_direction_time_and_configuration_private(
    route_files: RouteFiles, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, _, known_hosts = route_files
    original = profile.read_bytes(), known_hosts.read_bytes()
    captured: dict[str, Any] = {}
    monkeypatch.setattr(shutil, "which", lambda _name: "fixture-ssh")

    def run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        captured.update(argv=argv, **kwargs)
        return SimpleNamespace(returncode=0, stdout="private data", stderr="private error")

    monkeypatch.setattr(health, "run_caller_owned", run)
    result = health.observe(profile, "target-a", known_hosts)
    assert result["state"] == "ok" and result["direction"] == "outbound"
    assert type(result["observed_at_ms"]) is int
    assert not any(value in json.dumps(result) for value in ("private", "target-a", str(profile)))
    assert (profile.read_bytes(), known_hosts.read_bytes()) == original
    argv = captured["argv"]
    assert argv[:5] == ["fixture-ssh", "-F", "none", "-n", "-T"]
    assert argv[-3:] == ["--", "127.0.0.1", "exit 0"]
    for option in (
        "StrictHostKeyChecking=yes", "UpdateHostKeys=no", "IdentityAgent=none",
        "ProxyCommand=none", "ProxyJump=none", "ControlPath=none",
        "ClearAllForwardings=yes", "BatchMode=yes", "GlobalKnownHostsFile=none",
    ):
        assert option in argv
    assert captured["timeout"] is None
    assert captured["max_stdout_bytes"] == captured["max_stderr_bytes"] == 16 * 1024
    budget = captured["budget"]
    assert budget.execution_deadline < budget.deadline


def test_native_child_does_not_inherit_foreign_descriptor_state(
    route_files: RouteFiles, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, _, known_hosts = route_files
    descriptor = "C28FC6F98A2C44ABBBD89D6A3037D0D9_POSIX_FD_STATE"
    monkeypatch.setenv(descriptor, "synthetic-ancestor-state")
    monkeypatch.setenv("SSH_FIXTURE_SENTINEL", "preserved")
    monkeypatch.setattr(shutil, "which", lambda _name: "fixture-ssh")
    captured: dict[str, Any] = {}

    def run(*args: object, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(health, "run_caller_owned", run)
    assert health.observe(profile, "target-a", known_hosts)["state"] == "ok"
    assert (descriptor in captured["env"]) == (os.name != "nt")
    assert captured["env"]["SSH_FIXTURE_SENTINEL"] == "preserved"
    assert os.environ[descriptor] == "synthetic-ancestor-state"


@pytest.mark.parametrize("line", [
    "ProxyCommand helper --login", "ProxyJump jump", "LocalCommand mutate",
    "RemoteForward 1000 localhost:1000", "IdentityAgent agent", "HostName other",
    "StrictHostKeyChecking accept-new",
])
def test_effectful_or_ambiguous_route_refuses_before_launch(
    route_files: RouteFiles, monkeypatch: pytest.MonkeyPatch, line: str,
) -> None:
    profile, _, known_hosts = route_files
    profile.write_text(profile.read_text(encoding="utf-8") + f"  {line}\n", encoding="utf-8")
    monkeypatch.setattr(
        health, "run_caller_owned", lambda *a, **k: pytest.fail("must refuse before launch"),
    )
    assert health.observe(profile, "target-a", known_hosts)["state"] == "unsupported"


@pytest.mark.parametrize("prefix", [
    "Include config.d/*\n", "Match exec helper\n", "User global\n", "Host *\n",
])
def test_global_or_evaluated_config_is_not_loaded(route_files: RouteFiles, prefix: str) -> None:
    profile, _, known_hosts = route_files
    profile.write_text(prefix + profile.read_text(encoding="utf-8"), encoding="utf-8")
    assert health.observe(profile, "target-a", known_hosts)["state"] == "unsupported"


@pytest.mark.parametrize("error,reason", [
    (subprocess.TimeoutExpired("private command", 1), "timeout"),
    (OutputLimitExceeded("stderr", 16 * 1024), "output-limit"),
    (OSError("private path"), "unavailable"),
])
def test_failed_attempt_does_not_invent_authenticated_proof(
    route_files: RouteFiles, monkeypatch: pytest.MonkeyPatch, error: BaseException, reason: str,
) -> None:
    profile, _, known_hosts = route_files
    monkeypatch.setattr(shutil, "which", lambda _name: "fixture-ssh")

    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise error

    monkeypatch.setattr(health, "run_caller_owned", fail)
    result = health.observe(profile, "target-a", known_hosts)
    assert result["state"] == "unknown" and result["reason"] == reason
    assert result["observed_at_ms"] is None


def test_exit_status_is_not_a_credential_diagnosis(
    route_files: RouteFiles, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, _, known_hosts = route_files
    monkeypatch.setattr(shutil, "which", lambda _name: "fixture-ssh")
    monkeypatch.setattr(
        health, "run_caller_owned",
        lambda *a, **k: SimpleNamespace(returncode=255, stdout="", stderr="Permission denied"),
    )
    result = health.observe(profile, "target-a", known_hosts)
    assert result["reason"] == "route-failed" and result["observed_at_ms"] is None


def test_unconfirmed_cleanup_retains_the_original_owner(
    route_files: RouteFiles, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    profile, _, known_hosts = route_files
    monkeypatch.setattr(shutil, "which", lambda _name: "fixture-ssh")
    error = OwnedCleanupError(None)

    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise error

    monkeypatch.setattr(health, "run_caller_owned", fail)
    with pytest.raises(OwnedCleanupError) as raised:
        health.observe(profile, "target-a", known_hosts)
    assert raised.value is error
    args = Namespace(profile=profile, alias="target-a", known_hosts=known_hosts, timeout_ms=1000)
    assert health._run(args) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["reason"] == "cleanup-unconfirmed" and result["observed_at_ms"] is None


def test_budget_includes_selection_and_never_renews(
    route_files: RouteFiles, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, _, known_hosts = route_files
    monkeypatch.setattr(shutil, "which", lambda _name: "fixture-ssh")
    ticks = iter((1.0, 7.0))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(
        health, "run_caller_owned", lambda *a, **k: pytest.fail("selection exhausted budget"),
    )
    result = health.observe(profile, "target-a", known_hosts)
    assert result["reason"] == "timeout"


def test_regressed_source_clock_cannot_publish_a_fresh_proof(
    route_files: RouteFiles, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, _, known_hosts = route_files
    monkeypatch.setattr(shutil, "which", lambda _name: "fixture-ssh")
    monkeypatch.setattr(
        health, "run_caller_owned", lambda *a, **k: SimpleNamespace(returncode=0),
    )
    stamps = iter((2_000_000_000, 1_000_000_000))
    monkeypatch.setattr(time, "time_ns", lambda: next(stamps))
    result = health.observe(profile, "target-a", known_hosts)
    assert result["reason"] == "clock-skew" and result["observed_at_ms"] is None


@pytest.mark.parametrize("timeout", [0, 5001, True])
def test_invalid_budget_refuses_before_input_reads(
    monkeypatch: pytest.MonkeyPatch, timeout: int,
) -> None:
    monkeypatch.setattr(health, "read_route", lambda *a: pytest.fail("must not read inputs"))
    with pytest.raises(health.ObservationError, match="invalid-timeout"):
        health.observe(Path("unused"), "target-a", Path("unused"), timeout_ms=timeout)


def test_observe_parser_is_strict_without_executing_a_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_ssh.__main__ import build_parser

    monkeypatch.setattr(
        health, "run_caller_owned", lambda *a, **k: pytest.fail("parsing never probes"),
    )
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["observe", "--alias", "target-a", "--typo"])


@pytest.mark.parametrize("target", ["identity", "profile", "known-hosts"])
def test_nul_paths_return_closed_cli_refusal_before_filesystem_or_launch(
    route_files: RouteFiles, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], target: str,
) -> None:
    from agent_ssh.__main__ import main

    profile, identity, known_hosts = route_files
    if target == "identity":
        profile.write_text(
            profile.read_text(encoding="utf-8").replace(str(identity), str(identity) + "\0invalid"),
            encoding="utf-8",
        )
    elif target == "profile":
        profile = Path(str(profile) + "\0invalid")
    else:
        known_hosts = Path(str(known_hosts) + "\0invalid")
    monkeypatch.setattr(shutil, "which", lambda _name: "fixture-ssh")
    monkeypatch.setattr(
        health, "run_caller_owned", lambda *a, **k: pytest.fail("invalid paths cannot launch"),
    )
    assert main([
        "observe", "--alias", "target-a", "--profile", str(profile),
        "--known-hosts", str(known_hosts), "--json",
    ]) == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["state"] == "unsupported" and result["reason"] == "invalid-input"
    assert result["observed_at_ms"] is None and captured.err == ""


