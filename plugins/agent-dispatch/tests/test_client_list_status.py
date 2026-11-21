"""Task-list status selection on the actual DispatchClient/coordinator wire."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client_errors import DispatchError
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue_records import Status
from tests._helpers import TEST_REPO, RepoDefaultingQueue
from tests.test_pool_placement_client import connected


def test_status_sequence_reaches_coordinator_as_one_filter(tmp_path: Path) -> None:
    queue = RepoDefaultingQueue(tmp_path / "queue.db")
    queued = queue.create("queued")
    proposed = queue.create("proposed", status=Status.PROPOSED)
    held = queue.create("not selected")
    queue.claim_one("fixture", task_id=held.id)
    requests: list[httpx.Request] = []
    api = TestClient(create_app(queue, token="data", control_token="control", enable_mcp=False))
    with connected(api, requests, control=False) as client:
        rows = client.list(repo=TEST_REPO, status=(Status.QUEUED, Status.PROPOSED))
        assert {row["id"] for row in rows} == {queued.id, proposed.id}
        assert requests[-1].url.params.get_list("status") == ["queued,proposed"]

        assert client.list(repo=TEST_REPO, status="queued,proposed") == rows
        assert [row["id"] for row in client.list(status=Status.CLAIMED)] == [held.id]
        assert requests[-1].url.params.get_list("status") == ["claimed"]
        before = len(requests)
        for invalid in ([], "", " , ", ["queued", ""], ["queued,proposed"], [None], b"queued"):
            with pytest.raises(DispatchError, match="status"):
                client.list(status=invalid)
        assert len(requests) == before
