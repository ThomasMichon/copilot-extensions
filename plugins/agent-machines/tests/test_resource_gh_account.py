"""Declared account prerequisites use the existing resource/restore boundary."""

from __future__ import annotations

import json
import subprocess

import pytest

from agent_machines import resources as R
from agent_machines.manifest import ManifestError, load_package
from agent_machines.reconcile import plan, restore, restore_result_to_dict

from ._helpers import base_package, write_package


def declaration(**overrides):
    return {
        "type": "gh-account",
        "host": "github.example.com",
        "login": "example-user",
        "credential_source": "stored",
        **overrides,
    }


def package(tmp_path, **overrides):
    data = base_package(
        name="example/auth",
        schema_version=4,
        manage={},
        resources=[declaration(**overrides)],
    )
    return load_package(write_package(tmp_path, "auth.yaml", data))


def entry(**overrides):
    return {
        "host": "github.example.com",
        "login": "example-user",
        "state": "success",
        "active": False,
        "tokenSource": "keyring",
        **overrides,
    }


def status(*entries):
    return R.RunOutcome(0, json.dumps({"hosts": {"github.example.com": list(entries)}}))


def apply(tmp_path, outcome, *, dry_run=False, maintenance_safe=False, **overrides):
    calls = []

    def runner(argv):
        calls.append(argv)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    pkg = package(tmp_path, **overrides)
    ctx = R.ResourceContext(tmp_path, {}, "windows", runner)
    results = R.apply_resources(
        [pkg],
        "box-1",
        "windows",
        ctx,
        dry_run=dry_run,
        maintenance_safe=maintenance_safe,
    )
    assert len(results) == 1
    return results[0], calls


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("maintenance_safe", [False, True])
def test_declared_inactive_account_is_checked_without_mutation(
    tmp_path,
    dry_run,
    maintenance_safe,
):
    result, calls = apply(
        tmp_path,
        status(entry()),
        dry_run=dry_run,
        maintenance_safe=maintenance_safe,
    )
    assert result.ok and result.status == "ok"
    assert result.action == "check" and not result.changed
    assert result.dry_run is dry_run
    assert calls == [
        [
            "gh",
            "auth",
            "status",
            "--hostname",
            "github.example.com",
            "--json",
            "hosts",
            "--no-refresh",
        ]
    ]
    assert result.backup_path is None
    assert result.commands == calls


@pytest.mark.parametrize("source", ["stored", "environment"])
def test_credential_source_is_explicit_not_inferred(tmp_path, source):
    result, _ = apply(
        tmp_path,
        status(entry(tokenSource="GH_TOKEN")),
        credential_source=source,
    )
    assert result.ok is (source == "environment")
    assert not result.changed


@pytest.mark.parametrize(
    "source",
    [
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
    ],
)
def test_declared_environment_source_does_not_require_an_active_switch(tmp_path, source):
    result, _ = apply(
        tmp_path,
        status(entry(tokenSource=source)),
        credential_source="environment",
    )
    assert result.ok and result.action == "check"


def test_unrelated_invalid_account_does_not_hide_the_declared_healthy_account(tmp_path):
    result, _ = apply(
        tmp_path,
        status(entry(login="another-user", state="error"), entry()),
    )
    assert result.ok


@pytest.mark.parametrize(
    "outcome",
    [
        R.RunOutcome(0, '{"hosts":{}}'),
        status(),
        status(entry(login="another-user")),
        status(entry(state="error")),
        status(entry(state="timeout")),
        status(entry(state="refresh_expired")),
        status(entry(state="unknown")),
        status(entry(), entry()),
        status(entry(host="another.example.com")),
        status(entry(tokenSource="unknown-source")),
        R.RunOutcome(1, status(entry()).stdout, "sensitive-stderr"),
        R.RunOutcome(0, "not-json sensitive-stdout"),
        R.RunOutcome(0, '{"hosts":[]}'),
        R.RunOutcome(0, '{"hosts":{"github.example.com":[null]}}'),
        R.RunOutcome(0, '{"hosts":{},"hosts":{"github.example.com":[]}}'),
        R.RunOutcome(0, " " * (64 * 1024 + 1)),
        FileNotFoundError("sensitive-exception"),
        subprocess.TimeoutExpired("gh", 5, output="sensitive-stdout"),
    ],
)
def test_missing_invalid_ambiguous_or_unavailable_evidence_never_converges(
    tmp_path,
    outcome,
):
    result, calls = apply(tmp_path, outcome)
    assert not result.ok and result.status == "blocked"
    assert not result.changed and result.action == "check"
    assert len(calls) == 1
    serialized = json.dumps(vars(result))
    assert "sensitive-" not in serialized
    assert "login" not in calls[0] and "switch" not in calls[0]
    assert "refresh" not in calls[0] and "--show-token" not in calls[0]


@pytest.mark.parametrize(
    "overrides",
    [
        {"host": None},
        {"host": "-github.example.com"},
        {"host": "https://github.example.com"},
        {"host": "github.example.com/path"},
        {"host": "github.example.com\ninjected"},
        {"login": None},
        {"login": "-example"},
        {"login": "example user"},
        {"credential_source": None},
        {"credential_source": "any"},
        {"state": "absent"},
        {"token": "sensitive-value"},
    ],
)
def test_invalid_or_credential_bearing_declarations_are_rejected(tmp_path, overrides):
    with pytest.raises(ManifestError):
        package(tmp_path, **overrides)


def test_host_and_login_identity_is_case_insensitive_and_collides(tmp_path):
    first = package(tmp_path / "first")
    second = package(
        tmp_path / "second",
        host="GITHUB.EXAMPLE.COM",
        login="EXAMPLE-USER",
        credential_source="environment",
    )
    second.name = "example/other"
    resources, findings = R.resolve_resources([first, second], "box-1", "windows")
    assert len(resources) == 1
    assert resources[0].identity == (
        "gh-account",
        "github.example.com",
        "example-user",
    )
    assert any(f.level == "error" and f.code == "resource-conflict" for f in findings)


def test_plan_is_declarative_and_restore_json_preserves_prerequisite_failure(
    tmp_path,
    monkeypatch,
):
    from agent_machines import gh_account_prerequisite as account

    pkg = package(tmp_path)
    monkeypatch.setattr(
        account,
        "run_status",
        lambda argv: pytest.fail("planning must not probe live accounts"),
    )
    planned = plan([pkg], "box-1", "windows")
    assert planned.resources[0]["type"] == "gh-account"
    monkeypatch.setattr(account, "run_status", lambda argv: status())
    result = restore(
        [pkg],
        "box-1",
        dry_run=True,
        plat="windows",
        home=tmp_path,
        only=["gh-account"],
    )
    assert not result.ok
    payload = restore_result_to_dict(result)
    assert payload["resources"][0]["status"] == "blocked"
    assert payload["resources"][0]["changed"] is False


def test_healthy_result_never_serializes_raw_status_fields(tmp_path):
    marker = "private-status-detail"
    result, _ = apply(
        tmp_path,
        status(entry(token=marker, scopes=marker, error=marker)),
    )
    assert result.ok
    assert marker not in json.dumps(vars(result))


def test_native_probe_uses_short_owned_budget_without_refresh_or_debug(
    monkeypatch,
):
    from agent_machines import gh_account_prerequisite as account

    captured = {}

    def owned(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return subprocess.CompletedProcess(argv, 0, '{"hosts":{}}', "")

    monkeypatch.setenv("GH_DEBUG", "api")
    monkeypatch.setattr(account.shutil, "which", lambda name: "resolved-gh")
    monkeypatch.setattr(account, "run_caller_owned", owned)
    outcome = account.run_status(["gh", "auth", "status", "--no-refresh"])
    assert outcome.returncode == 0
    assert captured["argv"] == ["resolved-gh", "auth", "status", "--no-refresh"]
    assert captured["timeout"] == 5
    assert captured["max_stdout_bytes"] == 64 * 1024
    assert captured["max_stderr_bytes"] == 8 * 1024
    assert captured["env"]["GH_PROMPT_DISABLED"] == "1"
    assert "GH_DEBUG" not in captured["env"]
