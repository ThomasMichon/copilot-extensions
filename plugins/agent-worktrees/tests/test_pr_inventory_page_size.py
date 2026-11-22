"""Selected inventory pages fit existing capture bounds without more requests."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from agent_worktrees import pr_inventory_cli
from agent_worktrees.providers import get_provider, gitea, observation_inventory
from agent_worktrees.providers.observation_credentials import (
    CredentialSource, ObservationCredential, with_http_profile,
)


@dataclass(frozen=True)
class _Forge:
    provider: str
    api_base: str
    repository: str = "owner/repo"
    permission: str = "observe"
    authentication: str = "bearer"


@dataclass(frozen=True)
class _Material:
    token: str
    expires_at: float
    forge: _Forge


def source(provider: str, endpoint: str) -> CredentialSource:
    def acquire() -> ObservationCredential:
        return _Material("synthetic-page-size", time.time() + 60, _Forge(provider, endpoint))

    return acquire


@pytest.mark.parametrize("kind", ["github", "gitea"])
@pytest.mark.parametrize("profile", ["legacy", "direct-v1"])
def test_smaller_rich_pages_fit_the_same_capture_and_four_request_budget(
    monkeypatch: pytest.MonkeyPatch, kind: str, profile: str,
) -> None:
    rows = [{"number": number, "state": "open", "body": "x" * 40_000}
            for number in range(1, 46)]
    calls: list[int] = []
    provider = get_provider(kind)
    endpoint = "https://api.github.com" if kind == "github" else "https://forge.example"
    origin = "https://github.com" if kind == "github" else endpoint

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        address = next(part for part in argv if part.startswith("https://"))
        query = parse_qs(urlsplit(address).query)
        size = int(query["per_page" if kind == "github" else "limit"][0])
        page = int(query["page"][0])
        calls.append(size)
        assert kwargs["max_stdout_bytes"] <= 1_048_584
        assert kwargs["max_stderr_bytes"] == 8192
        body = json.dumps(rows[(page - 1) * size:page * size])
        suffix = "\n200" if kind == "gitea" or profile == "direct-v1" else ""
        return subprocess.CompletedProcess(argv, 0, body + suffix, "")

    monkeypatch.setattr(observation_inventory, "run_cli", run)
    monkeypatch.setattr(gitea, "run_cli", run)

    def read(**options: Any) -> dict[str, Any]:
        return observation_inventory.inventory(
            provider, "owner/repo", api_base=origin,
            credential_source=with_http_profile(source(kind, endpoint), profile),
            max_pages=4, deadline=time.monotonic() + 10, **options,
        )

    baseline = read()
    assert baseline["outcome"] == "unavailable" and baseline["pages"] == 0
    assert calls == [50]
    calls.clear()
    selected = read(page_size=20)
    assert selected["outcome"] == "complete" and selected["pages"] == 4
    assert selected["numbers"] == list(range(1, 46))
    assert calls == [20] * 4
    assert selected["max_pages"] == baseline["max_pages"] == 4
    assert selected["max_pulls"] == baseline["max_pulls"] == 200


@pytest.mark.parametrize("damage", ["pages", "pulls", "server"])
def test_selected_page_keeps_partial_limits_and_refuses_a_larger_server_page(
    monkeypatch: pytest.MonkeyPatch, damage: str,
) -> None:
    sends: list[int] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        sends.append(1)
        page = int(parse_qs(urlsplit(argv[-1]).query)["page"][0])
        numbers = range(1, 4) if damage == "server" else range(2 * page - 1, 2 * page + 1)
        body = json.dumps([{"number": number, "state": "open"} for number in numbers])
        return subprocess.CompletedProcess(argv, 0, body, "")

    monkeypatch.setattr(observation_inventory, "run_cli", run)
    result = observation_inventory.inventory(
        get_provider("github"), "owner/repo", api_base="https://github.com",
        credential_source=source("github", "https://api.github.com"),
        page_size=2, max_pages=2, max_pulls=3 if damage == "pulls" else 200,
    )
    if damage == "server":
        assert result["outcome"] == "unavailable" and not result["numbers"]
        assert len(sends) == 1
    else:
        assert result["outcome"] == "partial"
        assert result["error"] == ("pull_limit" if damage == "pulls" else "page_limit")
        assert result["numbers"] == ([1, 2, 3] if damage == "pulls" else [1, 2, 3, 4])
        assert len(sends) == 2


@pytest.mark.parametrize("size", [0, 51, True, "2"])
def test_invalid_python_page_size_refuses_before_credentials(
    monkeypatch: pytest.MonkeyPatch, size: Any,
) -> None:
    monkeypatch.setattr(
        observation_inventory, "run_cli", lambda *_a, **_kw: pytest.fail("unexpected send"),
    )
    with pytest.raises(ValueError, match="invalid bounded"):
        observation_inventory.inventory(
            get_provider("github"), "owner/repo", api_base="https://github.com",
            credential_source=lambda: pytest.fail("unexpected credential"), page_size=size,
        )


def test_declared_option_reaches_actual_parser_and_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_worktrees import __main__ as main, pr_credential_command

    assert pr_inventory_cli.dispatch(["--contract", "--json"]) == 0
    support = json.loads(capsys.readouterr().out)["inventory_page_size"]
    assert support["version"] == 1 and support["minimum"] <= 2 <= support["maximum"]
    config = tmp_path / "native.json"
    config.write_text(json.dumps({"repos": {"selected": {"pr": {
        "provider": "github", "api_base": "https://github.com", "repository": "owner/repo",
    }}}}))
    sends: list[str] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        sends.append(argv[-1])
        return subprocess.CompletedProcess(argv, 0, "[]", "")

    monkeypatch.setattr(observation_inventory, "run_cli", run)
    monkeypatch.setattr(
        main, "_resolve_active_project", lambda *_a: pytest.fail("unexpected project"),
    )
    monkeypatch.setattr(
        main, "_ensure_cluster_loaded", lambda: pytest.fail("unexpected lifecycle"),
    )
    monkeypatch.setattr(
        pr_credential_command, "command_source",
        lambda *_a, **_kw: source("github", "https://api.github.com"),
    )
    assert main.main([
        "pr-watch", "inventory", "owner/repo", "--config", str(config),
        "--credential-command", json.dumps([sys.executable]), "--json",
        support["option"], "2",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "complete"
    assert len(sends) == 1 and "per_page=2" in sends[0]
    assert pr_inventory_cli.dispatch(["--contract", support["option"], "2"]) == 2


@pytest.mark.parametrize("arguments", [
    ["--page-size", "0"], ["--page-size", "51"], ["--page-size", "invalid"],
    ["--page-siz", "2"],
])
def test_invalid_cli_selection_is_passive(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str],
) -> None:
    from agent_worktrees import pr_credential_command, pr_inventory_config

    monkeypatch.setattr(
        pr_credential_command, "command_source", lambda *_a, **_kw: pytest.fail("credential"),
    )
    monkeypatch.setattr(
        pr_inventory_config, "load_binding", lambda *_a: pytest.fail("config"),
    )
    assert pr_inventory_cli.dispatch(["owner/repo", "--json", *arguments]) == 2
