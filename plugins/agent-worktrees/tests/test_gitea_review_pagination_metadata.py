"""Actual native transport metadata qualifies filtered review-page exhaustion."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from agent_worktrees.pr_snapshot import PRSnapshot
from agent_worktrees.providers import gitea
from agent_worktrees.providers.base import ProviderError
from agent_worktrees.providers.observation_credentials import (
    MAX_SNAPSHOT_REQUESTS, ObservationCredentialError, with_http_profile,
)

ENDPOINT = "https://forge.example/gitea"


@dataclass(frozen=True)
class Binding:
    provider: str = "gitea"
    api_base: str = ENDPOINT
    repository: str = "owner/repo"
    permission: str = "observe"
    authentication: str = "bearer"


@dataclass(frozen=True)
class Material:
    token: str
    expires_at: float
    forge: Binding


@dataclass(frozen=True)
class Page:
    body: object
    count: int | str | None


def review(number: int) -> dict[str, Any]:
    return {
        "id": number, "state": "APPROVED", "user": {"login": "reviewer"},
        "commit_id": "a" * 40, "submitted_at": "2026-01-01T00:00:00Z",
    }


def fetch(
    monkeypatch: pytest.MonkeyPatch, pages: Sequence[Page], *,
    profile: str = "direct-v1", calls: list[str] | None = None,
) -> PRSnapshot:
    sent = [] if calls is None else calls

    def invoke(argv: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        url = next(part for part in argv if part.startswith("https://"))
        sent.append(url)
        count = ""
        if "/reviews?" in url:
            assert options["max_stdout_bytes"] <= 1_048_584
            page = int(parse_qs(urlsplit(url).query)["page"][0])
            assert 1 <= page <= len(pages), "unexpected extra review request"
            selected = pages[page - 1]
            body = json.dumps(selected.body)
            count = "" if selected.count is None else str(selected.count)
        elif url.endswith("/status"):
            body = '{"state":"success","statuses":[]}'
        elif url.endswith("/pulls/7"):
            body = json.dumps({
                "state": "open", "head": {"sha": "a" * 40},
                "base": {"sha": "b" * 40, "ref": "dev"}, "user": {"login": "author"},
            })
        else:
            raise AssertionError("unexpected provider request")
        if profile != "token":
            assert "synthetic" not in repr(argv) and "synthetic" in options["input_text"]
        footer = argv[argv.index("-w") + 1].replace(
            "%header{x-total-count}", count,
        ).replace("%{http_code}", "200")
        return subprocess.CompletedProcess(argv, 0, body + footer, "")

    monkeypatch.setattr(gitea, "run_cli", invoke)
    provider = gitea.GiteaProvider()
    if profile == "token":
        return provider.get_snapshot("owner/repo", 7, api_base=ENDPOINT, token="synthetic")
    source = with_http_profile(
        lambda: Material("synthetic", time.time() + 60, Binding()), profile,
    )
    return provider.get_snapshot("owner/repo", 7, api_base=ENDPOINT, credential_source=source)


@pytest.mark.parametrize("profile", ["token", "legacy", "direct-v1"])
@pytest.mark.parametrize("pages,expected", [
    ([Page([], 0)], []),
    ([Page([review(1)], 1)], [1]),
    ([Page([review(1)], 2), Page([review(2)], 2)], [1, 2]),
    ([Page([], 3), Page([], 3), Page([review(9)], 3)], [9]),
])
def test_count_proves_exhaustion_without_short_or_empty_body_assumptions(
    monkeypatch: pytest.MonkeyPatch, profile: str, pages: list[Page], expected: list[int],
) -> None:
    calls: list[str] = []
    snapshot = fetch(monkeypatch, pages, profile=profile, calls=calls)
    assert snapshot.reviews_complete is True
    assert [row.id for row in snapshot.reviews] == expected
    assert len([url for url in calls if "/reviews?" in url]) == len(pages)
    assert type(snapshot.reviews) is tuple


@pytest.mark.parametrize("profile", ["token", "legacy", "direct-v1"])
def test_missing_count_preserves_unknown_instead_of_certifying_an_empty_page(
    monkeypatch: pytest.MonkeyPatch, profile: str,
) -> None:
    snapshot = fetch(monkeypatch, [Page([], None)], profile=profile)
    assert snapshot.reviews == () and snapshot.reviews_complete is None


@pytest.mark.parametrize("pages", [
    [Page([review(1)], 2), Page([review(2)], 3)],
    [Page([review(1)], 2), Page([], None)],
    [Page([review(1), review(2)], 1)],
    [Page([], "malformed")],
])
def test_changed_missing_or_contradictory_count_never_produces_complete_history(
    monkeypatch: pytest.MonkeyPatch, pages: list[Page],
) -> None:
    with pytest.raises(ProviderError):
        fetch(monkeypatch, pages)


def test_filtered_empty_pages_still_obey_the_actual_snapshot_request_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    with pytest.raises(ObservationCredentialError, match="request budget"):
        fetch(
            monkeypatch, [Page([], 1000)] * MAX_SNAPSHOT_REQUESTS, calls=calls,
        )
    assert len(calls) == MAX_SNAPSHOT_REQUESTS


def test_legacy_filtered_pages_cannot_create_an_unbounded_traversal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    with pytest.raises(ProviderError, match="page bound"):
        fetch(
            monkeypatch, [Page([], 1000)] * MAX_SNAPSHOT_REQUESTS,
            profile="token", calls=calls,
        )
    assert len([url for url in calls if "/reviews?" in url]) == MAX_SNAPSHOT_REQUESTS
