"""Provider inspection with synthetic credentials and redirected native state."""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent_worktrees import repos


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    path = repos._repos_yaml_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        "repos:\n"
        "  sample:\n"
        "    remote: https://github.com/example/sample.git\n"
        "    account: selected-user\n"
        "    class: reference\n",
        encoding="utf-8",
    )
    return path


def inspect():
    from agent_worktrees.repos_authentication import inspect_repository

    return inspect_repository("sample", "sample")


def api(value, code=200, exit_code=0):
    return SimpleNamespace(
        returncode=exit_code,
        stdout=f"HTTP/2.0 {code} Response\r\nX-Private: sentinel\r\n\r\n{json.dumps(value)}",
        stderr="secret-bearing-diagnostic",
    )


@pytest.fixture
def provider(monkeypatch):
    from agent_worktrees import github_account_observation

    calls = []
    responses = [
        SimpleNamespace(returncode=0, stdout="synthetic-private-token\n", stderr=""),
        api({"login": "selected-user", "private_metadata": "sentinel"}),
        api({"full_name": "example/sample", "permissions": {"push": True}}),
    ]

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        value = responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(github_account_observation, "run_caller_owned", run)
    monkeypatch.setattr("shutil.which", lambda name: "/qualified/gh" if name == "gh" else None)
    return calls, responses


def test_selected_account_host_stages_and_single_budget(registry, provider, monkeypatch):
    calls, _ = provider
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        monkeypatch.setenv(key, "foreign-private-token")
    monkeypatch.setenv("GH_HOST", "foreign.example")
    monkeypatch.setenv("GH_DEBUG", "api")
    before = registry.read_bytes()
    result = inspect()
    assert result["status"] == "observed"
    assert result["selection"] == {
        "provider": "github", "host": "github.com", "repository": "example/sample",
        "account": "selected-user", "account_source": "repository", "configured": True,
    }
    assert result["identity"]["status"] == "verified"
    assert result["repository_access"]["status"] == "verified"
    assert calls[0][0] == [
        "/qualified/gh", "auth", "token", "--hostname", "github.com", "--user", "selected-user",
    ]
    assert calls[1][0][-1] == "https://api.github.com/user"
    assert calls[2][0][-1] == "https://api.github.com/repos/example/sample"
    assert len({id(kwargs["budget"]) for _, kwargs in calls}) == 1
    assert "GH_TOKEN" not in calls[0][1]["env"]
    assert "GH_HOST" not in calls[0][1]["env"]
    assert "GH_DEBUG" not in calls[0][1]["env"]
    assert calls[1][1]["env"]["GH_TOKEN"] == "synthetic-private-token"
    assert registry.read_bytes() == before
    rendered = json.dumps(result)
    for private in ("synthetic-private-token", "foreign-private-token", "sentinel", "permissions", "push"):
        assert private not in rendered
    assert {"git_transport", "gcm", "write_permission", "codespaces", "mcp", "copilot"} <= set(
        result["unobserved"]
    )


@pytest.mark.parametrize("kind", ["missing-account", "unsupported-host", "missing-gh"])
def test_unobserved_selection_does_not_consume_credentials(registry, provider, monkeypatch, kind):
    calls, _ = provider
    content = registry.read_text(encoding="utf-8")
    if kind == "missing-account":
        content = content.replace("    account: selected-user\n", "")
    elif kind == "unsupported-host":
        content = content.replace("github.com", "other.example")
    else:
        monkeypatch.setattr("shutil.which", lambda name: None)
    registry.write_text(content, encoding="utf-8")
    result = inspect()
    assert result["status"] != "observed"
    assert result["repository_access"]["status"] == "not-observed"
    assert calls == []
    if kind == "unsupported-host":
        assert result["selection"]["host"] == "other.example"


def test_owner_account_mapping_uses_same_strict_snapshot(registry, provider, monkeypatch):
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("    account: selected-user\n", "")
        + "account_map:\n  EXAMPLE: selected-user\n", encoding="utf-8",
    )
    original = repos.read_registry
    reads = []

    def read(**kwargs):
        reads.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(repos, "read_registry", read)
    result = inspect()
    assert result["selection"]["account_source"] == "account-map"
    assert result["identity"]["status"] == "verified"
    assert reads == [{"strict": True}]


def test_preferred_account_is_observed_without_claiming_an_owner_mapping(registry, provider):
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("    account: selected-user\n", "")
        + "account_map:\n  'github.com:*': selected-user\n", encoding="utf-8",
    )
    result = inspect()
    assert result["selection"]["account"] == "selected-user"
    assert result["selection"]["account_source"] == "default-account"
    assert result["identity"]["status"] == "verified"


@pytest.mark.parametrize("kind, expected", [
    ("User", "user"), ("Organization", "organization"), (None, "unknown"),
])
def test_repository_owner_kind_comes_from_metadata_not_the_selected_login(
    registry, provider, kind, expected,
):
    calls, responses = provider
    responses[2] = api({
        "full_name": "example/sample", "owner": {"type": kind, "private": "sentinel"},
    })
    result = inspect()
    assert result["repository_access"].get("owner_kind", "unknown") == expected
    assert result["selection"]["account"] == "selected-user"
    assert len(calls) == 3
    assert "sentinel" not in json.dumps(result)


@pytest.mark.parametrize("stage", ["credential", "identity", "repository"])
@pytest.mark.parametrize("failure", ["nonzero", "malformed", "timeout", "overflow", "runtime"])
def test_stage_failures_are_distinct_sanitized_and_not_retried(registry, provider, stage, failure):
    from agent_procutil.owned_process import OutputLimitExceeded

    calls, responses = provider
    index = ("credential", "identity", "repository").index(stage)
    if failure == "timeout":
        value = subprocess.TimeoutExpired(["secret-command"], 1, output="private-output")
    elif failure == "overflow":
        value = OutputLimitExceeded("stdout", 1024)
    elif failure == "runtime":
        value = RuntimeError("private-output")
    elif failure == "nonzero":
        value = api({"secret": "private-output"}, code=403, exit_code=1)
    else:
        value = SimpleNamespace(returncode=0, stdout="malformed private-output", stderr="private-output")
    responses[index] = value
    result = inspect()
    assert result["status"] == "unavailable"
    assert result["failure"]["stage"] == stage
    assert len(calls) == index + 1
    assert "private-output" not in json.dumps(result)
    assert "secret-command" not in json.dumps(result)
    if stage == "repository":
        assert result["identity"]["status"] == "verified"
    else:
        assert result["repository_access"]["status"] == "not-observed"


def test_identity_mismatch_never_observes_repository(registry, provider):
    calls, responses = provider
    responses[1] = api({"login": "different-user"})
    result = inspect()
    assert result["identity"]["status"] == "mismatch"
    assert result["repository_access"]["status"] == "not-observed"
    assert len(calls) == 2


@pytest.mark.parametrize("value", [None, "not a login", "github_pat_private_body"])
def test_invalid_identity_is_not_disclosed(registry, provider, value):
    calls, responses = provider
    responses[1] = api({"login": value})
    result = inspect()
    assert result["failure"] == {"stage": "identity", "code": "invalid-response"}
    assert len(calls) == 2
    assert "private_body" not in json.dumps(result)


@pytest.mark.parametrize("code", [401, 403, 404, 429])
def test_http_failure_preserves_status_without_diagnosing_expiration(registry, provider, code):
    _, responses = provider
    responses[2] = api({"private": "do-not-return"}, code=code, exit_code=1)
    result = inspect()
    assert result["credential"]["status"] == "available"
    assert result["identity"]["status"] == "verified"
    assert result["failure"] == {
        "stage": "repository", "code": "access-unconfirmed", "exit_code": 1, "http_status": code,
    }
    assert "do-not-return" not in json.dumps(result)


def test_cleanup_failure_retains_exact_owner_custody(registry, provider):
    from agent_procutil.owned_process import OwnedCleanupError

    _, responses = provider
    failure = OwnedCleanupError(None)
    responses[0] = failure
    with pytest.raises(OwnedCleanupError) as caught:
        inspect()
    assert caught.value is failure


@pytest.mark.parametrize("available", [True, False])
def test_configuration_projection_preserves_native_observation_and_read_safety(
    registry, provider, monkeypatch, available,
):
    calls, _ = provider
    if not available:
        monkeypatch.setattr("shutil.which", lambda name: None)
    before = registry.read_bytes()
    result = inspect()
    projection = result["configuration"]
    assert projection["summary"]["status"] == result["status"]
    assert projection["summary"]["observed_at"] == result["observed_at"]
    rows = {row["id"]: row for row in projection["entries"]}
    assert rows["repository"]["status"] == result["repository_access"]["status"]
    assert rows["identity"]["status"] == result["identity"]["status"]
    assert rows["credential"]["status"] == result["credential"]["status"]
    details = "\n".join(row["card"]["body"] for row in rows.values())
    for value in (result["target"]["resource"], result["registry_revision"], result["observed_at"]):
        assert value in details
    assert "Git transport" in details and "not observed" in details
    assert "no sign-in" in details
    assert registry.read_bytes() == before
    assert len(calls) == (3 if available else 0)
    assert "synthetic-private-token" not in json.dumps(projection)


def test_runner_failure_returns_contract_json_without_traceback(registry, provider, capsys):
    from agent_worktrees.repos_cli import cmd_repos_dispatch

    _, responses = provider
    responses[0] = RuntimeError("private-native-diagnostic")
    assert cmd_repos_dispatch([
        "authentication", "inspect", "--context-project", "sample",
        "--repository", "sample", "--json",
    ]) != 0
    captured = capsys.readouterr()
    value = json.loads(captured.out)
    assert value["contract"] == "agent-worktrees.repository-authentication"
    assert value["status"] == "unavailable"
    assert value["failure"] == {"stage": "credential", "code": "capture-failed"}
    assert captured.err == ""
    assert "private-native-diagnostic" not in captured.out


def test_strict_cli_typo_refuses_before_registry_or_credentials(monkeypatch, capsys):
    from agent_worktrees.repos_cli import cmd_repos_dispatch

    monkeypatch.setattr(repos, "read_registry", lambda **kwargs: pytest.fail("unexpected read"))
    assert cmd_repos_dispatch([
        "authentication", "inspect", "--context-project", "sample",
        "--repository", "sample", "--jsno",
    ]) != 0
    assert json.loads(capsys.readouterr().out)["code"] == "invalid-request"


@pytest.mark.parametrize("failure", ["duplicate", "private-identity", "unknown-project"])
def test_native_request_failure_suppresses_private_registry_diagnostics(
    registry, provider, capsys, failure,
):
    from agent_worktrees.repos_cli import cmd_repos_dispatch

    calls, _ = provider
    content = registry.read_text(encoding="utf-8")
    if failure == "duplicate":
        content += "    remote: private-sentinel\n"
    elif failure == "private-identity":
        content = content.replace("selected-user", "github_pat_private-sentinel")
    registry.write_text(content, encoding="utf-8")
    before = registry.read_bytes()
    assert cmd_repos_dispatch([
        "authentication", "inspect", "--context-project",
        "unregistered" if failure == "unknown-project" else "sample",
        "--repository", "sample", "--json",
    ]) != 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "failed"
    assert "private-sentinel" not in captured.out + captured.err
    assert calls == []
    assert registry.read_bytes() == before


def test_native_parser_contract_and_unconfigured_read_preserve_state(registry, tmp_path):
    from agent_procutil import no_window_kwargs

    registry.write_text(
        registry.read_text(encoding="utf-8").replace("    account: selected-user\n", ""),
        encoding="utf-8",
    )
    credential_store = tmp_path / "credential-sentinel"
    credential_store.write_text("unchanged", encoding="utf-8")
    before = {path: path.read_bytes() for path in (registry, credential_store)}
    base = [sys.executable, "-B", "-m", "agent_worktrees", "repos", "authentication"]
    for args, status in (
        (["contract", "--json"], None),
        (["inspect", "--context-project", "sample", "--repository", "sample", "--json"], "selection-required"),
        (["inspect", "--context-project", "sample", "--repository", "sample", "--jsno"], "failed"),
    ):
        result = subprocess.run(
            [*base, *args], capture_output=True, text=True, timeout=15, **no_window_kwargs(),
        )
        value = json.loads(result.stdout)
        assert value["contract"] == "agent-worktrees.repository-authentication"
        assert value["version"] == 1
        if status is None:
            assert result.returncode == 0
            assert value["read_only"] is True
        else:
            assert result.returncode != 0
            assert value["status"] == status
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("expire", [False, True])
def test_real_capture_composes_synthetic_provider_and_aggregate_deadline(
    registry, tmp_path, monkeypatch, expire,
):
    from agent_procutil import owned_process
    from agent_procutil.owned_budget import OwnedExecutionBudget
    from agent_worktrees import github_account_observation

    script = tmp_path / "synthetic_provider.py"
    script.write_text(
        "import json, sys, time\n"
        + ("time.sleep(1.2)\n" if expire else "")
        + "if sys.argv[1] == 'auth':\n"
        "    print('synthetic-private-token')\n"
        "else:\n"
        "    value = {'login': 'selected-user'} if sys.argv[-1].endswith('/user') else {'full_name': 'example/sample'}\n"
        "    print('HTTP/2.0 200 OK\\r\\n\\r\\n' + json.dumps(value))\n",
        encoding="utf-8",
    )
    original_run = owned_process.run_caller_owned
    original_budget = OwnedExecutionBudget.from_timeout
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return original_run([sys.executable, "-B", str(script), *argv[1:]], **kwargs)

    monkeypatch.setattr(github_account_observation, "run_caller_owned", run)
    monkeypatch.setattr("shutil.which", lambda name: "/qualified/gh" if name == "gh" else None)
    if expire:
        monkeypatch.setattr(
            OwnedExecutionBudget, "from_timeout",
            lambda *args, **kwargs: original_budget(2.5, cleanup_reserve=.5),
        )
    result = inspect()
    if expire:
        assert result["failure"] == {"stage": "identity", "code": "timeout"}
        assert len(calls) == 2
    else:
        assert result["repository_access"]["status"] == "verified"
        assert len(calls) == 3
