from __future__ import annotations

import httpx
import pytest

from tests import test_relevance_closeout_cli as existing
from tests.test_relevance_closeout_cli import Caller

managed_assignment = existing.managed_assignment
assignment = existing.assignment
caller = existing.caller
independent_readiness = existing.independent_readiness


@pytest.mark.parametrize("old_at_read", [1, 2, 3, 4])
def test_missing_disposition_stops_at_the_changed_boundary(caller: Caller, old_at_read: int) -> None:
    reads = 0

    def legacy(request: httpx.Request, response: httpx.Response) -> httpx.Response:
        nonlocal reads
        if request.method == "GET":
            reads += 1
            if reads >= old_at_read:
                body = response.json()
                body["result"].pop("finish_action", None)
                return httpx.Response(200, json=body)
        return response

    caller.rewrite = legacy
    assert caller.run() == 1
    assert caller.finalized == (old_at_read == 4)
    assert [r.url.path.rsplit("/", 1)[-1] for r in caller.posts] == (
        ["prepare"] if old_at_read >= 3 else []
    )
    task = caller.queue.get(caller.task.id)
    assert task is not None and task.status == "started"
