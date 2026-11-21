"""Provider observations are complete, exact and read-only."""

import json
import subprocess
from typing import Any

import pytest

from agent_worktrees.providers import ProviderError, github_head_reconciliation as observation
from agent_worktrees.providers.github import GitHubProvider


@pytest.mark.parametrize("change", ["none", "case", "open", "number", "branch", "head", "fork", "base", "multiple", "malformed"])
def test_only_single_exact_merged_association_qualifies(monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    row: dict[str, Any] = {
        "number": 12, "state": "closed", "merged_at": "2026-01-01T00:00:00Z",
        "head": {"ref": "pr/example", "sha": "abc", "repo": {"full_name": "example/repo"}},
        "base": {"repo": {"full_name": "example/repo"}},
    }
    if change == "case":
        row["head"]["repo"]["full_name"] = "Example/REPO"
        row["base"]["repo"]["full_name"] = "EXAMPLE/Repo"
    elif change == "open":
        row.update(state="open", merged_at=None)
    elif change == "number":
        row["number"] = 13
    elif change == "branch":
        row["head"]["ref"] = "pr/other"
    elif change == "head":
        row["head"]["sha"] = "changed"
    elif change == "fork":
        row["head"]["repo"]["full_name"] = "fork/repo"
    elif change == "base":
        row["base"]["repo"]["full_name"] = "example/other"
    rows: list[object] = [row, row] if change == "multiple" else [row]
    if change == "malformed":
        rows = [None]
    calls: list[list[str]] = []
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(rows), "")
    monkeypatch.setattr(observation, "run_cli", run)
    result = GitHubProvider().confirm_reconciled_head_v1(
        "example/repo", "pr/example", number=12, head_sha="abc",
    )
    assert result is (change in ("none", "case"))
    assert len(calls) == 1
    assert "state=all" in calls[0] and "per_page=2" in calls[0]
    assert "head=example:pr/example" in calls[0]
    assert calls[0][calls[0].index("--method") + 1] == "GET"


@pytest.mark.parametrize("payload,code", [("[]", 0), ("[{}]", 0), ("{}", 0), ("null", 0), ("", 1), ("", 0)])
def test_absence_is_not_an_unavailable_or_nonempty_response(monkeypatch: pytest.MonkeyPatch, payload: str, code: int) -> None:
    monkeypatch.setattr(
        observation, "run_cli",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, code, payload, "unavailable"),
    )
    if payload not in ("[]", "[{}]"):
        with pytest.raises(ProviderError):
            GitHubProvider().confirm_reconciled_head_v1("example/repo", "pr/example")
    else:
        assert GitHubProvider().confirm_reconciled_head_v1(
            "example/repo", "pr/example",
        ) is (payload == "[]")


@pytest.mark.parametrize("host,endpoint", [
    ("github.com", "https://api.github.com"),
    ("forge.example.com", "https://forge.example.com/api/v3"),
])
def test_absolute_endpoint_and_selected_credentials_cannot_be_redirected(
    monkeypatch: pytest.MonkeyPatch, host: str, endpoint: str,
) -> None:
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert f"{endpoint}/repos/example/repo/pulls" in argv
        assert argv[argv.index("--hostname") + 1] == host
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
            assert kwargs["env"][key] == "selected-test-credential"
        return subprocess.CompletedProcess(argv, 0, "[]", "")
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        monkeypatch.setenv(key, "unrelated-test-credential")
    monkeypatch.setattr(observation, "run_cli", run)
    assert GitHubProvider().confirm_reconciled_head_v1(
        "example/repo", "pr/example", api_base=host, token="selected-test-credential",
    )
