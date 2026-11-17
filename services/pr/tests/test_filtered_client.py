"""Explicit population CLI selection refuses an old owner before mutation."""

import json
from pathlib import Path
from typing import Any

import pytest

from agent_pull_requests_service.__main__ import main
from agent_pull_requests_service.filtered_client import PopulationClient
from agent_pull_requests_service.local_client import LocalClientError


@pytest.mark.parametrize("storage", [None, "repository_cache_version:2"])
def test_new_client_refuses_legacy_capability_before_subscribe(
    monkeypatch: pytest.MonkeyPatch, storage: str | None,
) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    def http(self: PopulationClient, method: str, operation: str,
             value: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((method, operation, value))
        return {"continuing": True, "storage": storage, "forge_writes": False, "task_emissions": False}
    monkeypatch.setattr(PopulationClient, "_http", http)
    with pytest.raises(LocalClientError, match="unsupported"):
        PopulationClient(Path("unused"), "test", "repo").request("subscribe", {
            "request_id": "original", "ttl": 60, "definition": "team",
        })
    assert calls == [("GET", "capabilities", None)]


def test_actual_cli_named_and_inline_filter_forms_are_exact_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    def request(self: PopulationClient, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((self.route, operation, payload))
        return {"status": "active"}
    monkeypatch.setattr(PopulationClient, "request", request)
    common = ["--socket", str(tmp_path / "local.sock"), "--namespace", "test", "--target", "repo"]
    assert main(["population-subscribe", *common, "--request-id", "original", "--definition", "team"]) == 0
    assert calls[-1] == ("populations", "subscribe", {
        "request_id": "original", "ttl": 3600, "definition": "team",
    })
    selected = tmp_path / "filter.json"
    value = {"protocol": "agent-pull-requests.filters/v1", "where": {"repo": ["owner/repo"]}}
    selected.write_text(json.dumps(value))
    assert main(["population-subscribe", *common, "--request-id", "inline", "--filter-file", str(selected)]) == 0
    assert calls[-1][2]["filter"] == value
    before = len(calls)
    selected.write_bytes(b" " * 32769)
    assert main(["population-subscribe", *common, "--request-id", "large", "--filter-file", str(selected)]) == 2
    assert len(calls) == before
    assert main(["population-renew", *common, "--registration-id", "retained", "--expires-at", "200"]) == 0
    assert calls[-1] == ("populations", "renew", {"registration_id": "retained", "expires_at": 200})
    assert "refused" in capsys.readouterr().err
