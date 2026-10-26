"""No-actor qualification of the canonical account-prerequisite evaluator."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "plugins/agent-machines/src/agent_machines/gh_account_prerequisite.py"


@pytest.fixture
def probe(monkeypatch):
    parent = ModuleType("agent_procutil")
    parent.__path__ = []
    native = ModuleType("agent_procutil.owned_process")
    native.OutputLimitExceeded = type(
        "OutputLimitExceeded", (subprocess.SubprocessError,), {}
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("a unit attempted native authentication")

    native.run_caller_owned = forbidden
    monkeypatch.setitem(sys.modules, parent.__name__, parent)
    monkeypatch.setitem(sys.modules, native.__name__, native)
    spec = importlib.util.spec_from_file_location(
        "promotion_gh_account_probe", ROOT / SOURCE
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def row(**overrides):
    return {
        "host": "github.example.com",
        "login": "example-user",
        "state": "success",
        "active": False,
        "tokenSource": "keyring",
        **overrides,
    }


def output(*rows):
    return SimpleNamespace(
        returncode=0,
        stdout=json.dumps({"hosts": {"github.example.com": rows}}),
        stderr="",
    )


def check(probe, result, source="stored"):
    calls = []

    def runner(argv):
        calls.append(argv)
        if isinstance(result, Exception):
            raise result
        return result

    reason, argv = probe.check_gh_account(
        "github.example.com",
        "example-user",
        source,
        runner=runner,
    )
    assert calls == [argv]
    assert argv == [
        "gh",
        "auth",
        "status",
        "--hostname",
        "github.example.com",
        "--json",
        "hosts",
        "--no-refresh",
    ]
    return reason


def test_explicit_sources_accept_healthy_inactive_accounts_without_switching(probe):
    for source in ("keyring", "/isolated/gh/hosts.yml", r"C:\isolated\gh\hosts.yml"):
        assert check(probe, output(row(tokenSource=source))) is None
    for source in (
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
    ):
        result = output(row(tokenSource=source))
        assert check(probe, result, "environment") is None
        assert check(probe, result, "stored") is not None
    assert (
        check(
            probe,
            output(
                row(login="unrelated", state="error"),
                row(),
            ),
        )
        is None
    )


def test_missing_invalid_ambiguous_or_unhealthy_evidence_never_succeeds(probe):
    for result in (
        output(),
        output(row(login="other")),
        output(row(), row()),
        output(row(host="other.example.com")),
        output(row(tokenSource="unknown")),
        output(row(tokenSource="relative/hosts.yml")),
        output(row(state="error")),
        output(row(state="timeout")),
        output(row(state="refresh_expired")),
        output(row(state="unknown")),
        output(row(active=1)),
        output(None),
        SimpleNamespace(returncode=1, stdout=output(row()).stdout, stderr=""),
        SimpleNamespace(returncode=0, stdout='{"hosts":{}}', stderr=""),
        SimpleNamespace(returncode=0, stdout='{"hosts":[]}', stderr=""),
        SimpleNamespace(returncode=0, stdout='{"hosts":{},"hosts":{}}', stderr=""),
        SimpleNamespace(
            returncode=0, stdout='{"hosts":{"github.example.com":{}}}', stderr=""
        ),
        SimpleNamespace(
            returncode=0,
            stdout='{"hosts":{"github.example.com":[],"GITHUB.EXAMPLE.COM":[]}}',
            stderr="",
        ),
        SimpleNamespace(returncode=0, stdout="null", stderr=""),
        SimpleNamespace(returncode=0, stdout="not-json", stderr=""),
        SimpleNamespace(returncode=0, stdout=" " * (64 * 1024 + 1), stderr=""),
    ):
        assert check(probe, result) is not None


def test_raw_output_and_probe_errors_are_not_exposed(probe):
    marker = "private-observation"
    assert check(probe, output(row(token=marker, scopes=marker, error=marker))) is None
    for result, expected in (
        (FileNotFoundError(marker), "probe-unavailable"),
        (
            UnicodeDecodeError("utf-8", b"private-observation\xff", 19, 20, "invalid"),
            "invalid-status",
        ),
        (subprocess.TimeoutExpired(marker, 5, output=marker), "probe-timeout"),
        (probe.OutputLimitExceeded(marker), "probe-unavailable"),
        (
            SimpleNamespace(returncode=1, stdout=marker, stderr=marker),
            "probe-failed-or-unsupported",
        ),
    ):
        reason = check(probe, result)
        assert reason == expected and marker not in reason


def test_native_adapter_has_short_owned_limits_and_no_refresh_or_debug(
    probe, monkeypatch
):
    calls = []

    def native(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"hosts":{}}', stderr="")

    monkeypatch.setenv("GH_DEBUG", "api")
    monkeypatch.setattr(probe.shutil, "which", lambda name: "synthetic-gh")
    monkeypatch.setattr(probe, "run_caller_owned", native)
    reason, argv = probe.check_gh_account(
        "github.example.com",
        "example-user",
        "stored",
    )
    assert reason == "account-missing"
    command, options = calls[0]
    assert command == ["synthetic-gh", *argv[1:]] and "--no-refresh" in command
    assert options["timeout"] == 5
    assert options["max_stdout_bytes"] == 64 * 1024
    assert options["max_stderr_bytes"] == 8 * 1024
    assert options["env"]["GH_PROMPT_DISABLED"] == "1"
    assert "GH_DEBUG" not in options["env"]
    monkeypatch.setattr(probe.shutil, "which", lambda name: None)
    assert probe.check_gh_account("github.example.com", "example-user", "stored")[
        0
    ] == ("probe-unavailable")


def test_unconfirmed_process_custody_is_not_swallowed(probe):
    class CustodyError(subprocess.SubprocessError):
        pass

    failure = CustodyError("native child cleanup did not complete")
    with pytest.raises(CustodyError) as raised:
        check(probe, failure)
    assert raised.value is failure
