"""Contained CLI witnesses; no operator accounts, login or live configuration."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import time

import pytest

from agent_machines import gh_account_prerequisite as account
from agent_machines import resources as R


def isolated_environment(tmp_path, monkeypatch):
    for name in (
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
        "GH_DEBUG",
    ):
        monkeypatch.delenv(name, raising=False)
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("GH_CONFIG_DIR", str(config))
    return config


def fake_gh(tmp_path, monkeypatch, body):
    helper = tmp_path / "fake_gh.py"
    helper.write_text(body, encoding="utf-8")
    command = tmp_path / ("gh.cmd" if os.name == "nt" else "gh")
    if os.name == "nt":
        content = f'@echo off\n"{sys.executable}" "{helper}" %*\nexit /b %errorlevel%\n'
    else:
        content = (
            f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(helper))} "$@"\n'
        )
    command.write_text(content, encoding="utf-8")
    command.chmod(command.stat().st_mode | 0o111)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    return helper


def test_real_gh_first_touch_empty_config_is_an_honest_read_only_failure(
    tmp_path,
    monkeypatch,
):
    if shutil.which("gh") is None:
        pytest.skip("native GitHub CLI is unavailable")
    config = isolated_environment(tmp_path, monkeypatch)
    before = sorted(config.rglob("*"))
    reason, argv = account.check_gh_account(
        "github.example.com",
        "example-user",
        "stored",
    )
    assert reason in {"account-missing", "probe-failed-or-unsupported"}
    assert "--no-refresh" in argv and "--show-token" not in argv
    assert sorted(config.rglob("*")) == before


def test_native_resource_executes_only_no_refresh_status_and_projects_safe_result(
    tmp_path,
    monkeypatch,
):
    config = isolated_environment(tmp_path, monkeypatch)
    fake_gh(
        tmp_path,
        monkeypatch,
        """
import json
import sys
assert sys.argv[1:] == [
    "auth", "status", "--hostname", "github.example.com",
    "--json", "hosts", "--no-refresh",
]
print(json.dumps({"hosts": {"github.example.com": [{
    "host": "github.example.com", "login": "example-user", "state": "success",
    "active": False, "tokenSource": "keyring", "token": "private-child-output",
}]}}))
""",
    )
    handler = R.HANDLERS["gh-account"]
    resource = R.ResolvedResource(
        "gh-account",
        "github.example.com/example-user",
        ("gh-account", "github.example.com", "example-user"),
        {"host": "github.example.com", "login": "example-user", "credential_source": "stored"},
    )
    result = handler.apply(resource, R.ResourceContext(tmp_path, {}, "windows"), False)
    assert result.ok and not result.changed
    assert "private-child-output" not in json.dumps(vars(result))
    assert not list(config.rglob("*"))


def test_first_touch_witness_accepts_unsupported_cli_without_retry(tmp_path, monkeypatch):
    helper = fake_gh(
        tmp_path,
        monkeypatch,
        """
from pathlib import Path
import sys
assert "--no-refresh" in sys.argv
counter = Path(__file__).with_suffix(".calls")
counter.write_text(counter.read_text() + "." if counter.exists() else ".")
print("unknown flag: --no-refresh", file=sys.stderr)
raise SystemExit(2)
""",
    )
    test_real_gh_first_touch_empty_config_is_an_honest_read_only_failure(
        tmp_path,
        monkeypatch,
    )
    assert helper.with_suffix(".calls").read_text() == "."


def test_native_probe_deadline_is_five_seconds_not_package_manager_timeout(
    tmp_path,
    monkeypatch,
):
    isolated_environment(tmp_path, monkeypatch)
    fake_gh(tmp_path, monkeypatch, "import time\ntime.sleep(60)\n")
    started = time.monotonic()
    reason, _ = account.check_gh_account(
        "github.example.com",
        "example-user",
        "stored",
    )
    assert reason == "probe-timeout"
    assert time.monotonic() - started < 12


def test_native_probe_caps_output_without_disclosing_it(tmp_path, monkeypatch):
    isolated_environment(tmp_path, monkeypatch)
    fake_gh(tmp_path, monkeypatch, 'print("private-child-output" * 8192)\n')
    reason, _ = account.check_gh_account(
        "github.example.com",
        "example-user",
        "stored",
    )
    assert reason == "probe-unavailable"


def test_native_probe_does_not_disclose_malformed_encoding(tmp_path, monkeypatch):
    isolated_environment(tmp_path, monkeypatch)
    fake_gh(
        tmp_path,
        monkeypatch,
        'import sys\nsys.stdout.buffer.write(b"private-observation\\xff")\n',
    )
    reason, _ = account.check_gh_account(
        "github.example.com",
        "example-user",
        "stored",
    )
    assert reason == "invalid-status"
