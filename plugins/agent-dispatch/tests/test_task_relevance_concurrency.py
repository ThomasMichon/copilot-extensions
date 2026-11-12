"""Independent queue processes race relevance against claim and preparation."""

from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys
from threading import Barrier

import pytest
from agent_procutil import no_window_kwargs

from agent_dispatch.queue import Status
from tests._helpers import RepoDefaultingQueue as TaskQueue

from tests.test_task_relevance import request, task


CHILD = """
import json, sys
from agent_dispatch.queue import TaskQueue, TaskError
from agent_dispatch.queue_relevance import RelevanceScope
q = TaskQueue(sys.argv[1])
task_id, operation = sys.argv[2:4]
values = json.loads(sys.argv[4])
try:
    if operation == 'report':
        values['scope'] = RelevanceScope(**values['scope'])
        value = q.reconcile_task_relevance(task_id, **values)['decision']
    elif operation == 'claim':
        value = 'claimed' if q.claim_one('host/worker', task_id=task_id) else 'not_claimed'
    else:
        _, reserved = q.reserve_spawn(task_id)
        value = 'reserved' if reserved else 'existing'
    print(json.dumps({'result': value}))
except TaskError as error:
    print(json.dumps({'error': str(error)}))
"""


@pytest.mark.parametrize("operation", ["claim", "reserve"])
def test_source_retirement_linearizes_with_an_independent_execution_process(tmp_path, operation):
    q = TaskQueue(tmp_path / "tasks.db")
    t = task(q)
    values = request(q, t.id)
    values["scope"] = vars(values["scope"])
    ready = Barrier(2)

    def run(action):
        ready.wait(timeout=10)
        result = subprocess.run(
            [sys.executable, "-c", CHILD, str(q.db_path), t.id, action, json.dumps(values)],
            text=True, capture_output=True, timeout=30, check=True, **no_window_kwargs(),
        )
        return json.loads(result.stdout)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(run, "report")
        second = executor.submit(run, operation)
        relevance, execution = first.result(timeout=40), second.result(timeout=40)
    current = q.get(t.id)
    if relevance.get("result") == "abandoned":
        assert current.status == Status.ABANDONED
        assert q.list_reservations(task_id=t.id) == []
        assert execution.get("result") != "claimed"
        assert execution.get("result") != "reserved"
    elif operation == "claim":
        assert "revision changed" in relevance["error"]
        assert execution == {"result": "claimed"}
        assert current.status == Status.CLAIMED
    else:
        assert relevance == {"result": "closeout_pending"}
        assert execution == {"result": "reserved"}
        assert len(q.list_reservations(task_id=t.id)) == 1
        assert current.status == Status.QUEUED
    assert len(q.steer_log(t.id)) == (relevance.get("result") == "closeout_pending")
