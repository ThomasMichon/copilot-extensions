"""Opt-in native SDK client over a contained, GET-only HTTP fixture."""

from __future__ import annotations

from copy import deepcopy
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import subprocess
from threading import Thread
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import pytest

from agent_procutil import no_window_kwargs

if TYPE_CHECKING:
    from services.pr.tests.test_reviewer_dispatch_example import Client, LANE
    from services.pr.tests.test_reviewer_feed_example import SERVICE, selection
else:
    from test_reviewer_dispatch_example import Client, LANE
    from test_reviewer_feed_example import SERVICE, selection

NATIVE = os.environ.get("PR_DISPATCH_NATIVE_PYTHON")
CHILD = """
import json,sys
from pathlib import Path
root=Path(sys.argv[1])
sys.path[:0]=[str(root/'services/pr/examples'),
              str(root/'plugins/agent-dispatch/src'),
              str(root/'plugins/agent-pull-requests/src')]
import httpx
from agent_dispatch.client import DispatchClient
from agent_dispatch.client_errors import DispatchUpgradeRequired
from agent_pull_requests.repository_contract import RepositoryIdentity
from reviewer_feed import Scope,propose
from reviewer_dispatch import prepare_notification,retained_task,reviewer_task_key
data=json.load(sys.stdin)
scope=Scope(RepositoryIdentity.parse(data['identity']),'consumer','reviewer',
            tuple(data['task_scope']))
key=reviewer_task_key('https://forge.example/owner/repo',land='author')
with DispatchClient(sys.argv[2],timeout=2,transport=httpx.HTTPTransport(retries=0)) as client:
    try:
        proposal=propose(data['page'],scope,notifications={},now=1000,
            lookup=lambda action:retained_task(action,scope,client=client,
                lane=data['lane'],task_key=key))
        request=None
        if proposal.kind=='steer':
            request=prepare_notification(proposal,scope,client=client,
                request_id='original-native-http-request',fields={'action':proposal.action_key},
                sender='consumer',message='Review activity',lane=data['lane'],task_key=key)
        print(json.dumps({'proposal':proposal.kind,'request':request}))
    except DispatchUpgradeRequired:
        print(json.dumps({'proposal':'unsupported','request':None}))
"""

COORDINATOR_CHILD = """
import json,os,sys
from dataclasses import asdict
from pathlib import Path
root,fixture=Path(sys.argv[1]),Path(sys.argv[2])
os.environ['AGENT_DISPATCH_INSTALL_DIR']=str(fixture/'install')
os.environ['AGENT_DISPATCH_RUN_DIR']=str(fixture/'run')
for name in (
    'AGENT_DISPATCH_ENDPOINT','AGENT_DISPATCH_WINDOWS_RUN_DIR',
    'AGENT_DISPATCH_WINDOWS_MOUNT','AGENT_DISPATCH_TASK_AUTHORITY',
    'AGENT_DISPATCH_CONTROL_TOKEN_COMMAND','AGENT_DISPATCH_TOKEN_COMMAND',
    'AGENT_DISPATCH_SHARED_TOKEN_COMMAND','AGENT_DISPATCH_SHARED_CONTROL_TOKEN_COMMAND',
    'COPILOT_AGENT_SESSION_ID',
):
    os.environ.pop(name,None)
os.environ['AGENT_DISPATCH_SELF_UPDATE']='0'
sys.path[:0]=[str(root/'services/pr/examples'),
              str(root/'plugins/agent-dispatch/src'),
              str(root/'plugins/agent-pull-requests/src')]
import httpx
from fastapi.testclient import TestClient
from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskQueue
from agent_pull_requests.repository_contract import RepositoryIdentity
from reviewer_feed import Scope,propose
from reviewer_dispatch import prepare_notification,retained_task,reviewer_task_key
data=json.load(sys.stdin)
key=reviewer_task_key('https://forge.example/owner/repo',land='author')
queue=TaskQueue(fixture/'tasks.db')
task=queue.create('Fixture review',repo=data['lane'],
                  dedup_key='unrelated' if data['mode']=='absent' else key(7))
queue.claim_one('fixture/worker',task_id=task.id)
queue.start(task.id,'fixture/worker',owner_session_id='fixture-session')
if data['mode']=='operator-form':
    queue.set_card(task.id,'fixture/worker',card={
        'title':'Question','request_input':[{'name':'answer','type':'textarea'}]})
else:
    queue.suspend(task.id,'fixture/worker',reason='Fixture wait',cooldown_seconds=None)
app=create_app(queue,enable_mcp=False,verification_interval=0)
api=TestClient(app)
methods=[]
def forward(request):
    methods.append(request.method)
    assert request.method=='GET'
    response=api.get(request.url.path,params=request.url.params)
    return httpx.Response(response.status_code,json=response.json(),request=request)
try:
    with DispatchClient('http://fixture',transport=httpx.MockTransport(forward)) as client:
        native=client.get(task.id)
        scope=Scope(RepositoryIdentity.parse(data['identity']),'consumer','reviewer',
                    tuple(native['draft_scope'][name] for name in ('queue','installation','machine')))
        def state():
            return (asdict(queue.get(task.id)),queue.events(task.id),queue.steer_log(task.id),
                    queue.list_wakes(task.id),len(queue.list()))
        before=state()
        proposal=propose(data['page'],scope,notifications={},now=1000,
            lookup=lambda action:retained_task(action,scope,client=client,lane=data['lane'],task_key=key))
        request=None
        if proposal.kind=='steer':
            request=prepare_notification(proposal,scope,client=client,
                request_id='original-native-owner-request',fields={'action':proposal.action_key},
                sender='consumer',message='Review activity',lane=data['lane'],task_key=key)
        assert state()==before
        assert queue.steer_log(task.id)==queue.list_wakes(task.id)==[]
        print(json.dumps({'proposal':proposal.kind,'prepared':request is not None,
            'scope_matches':request is None or request['scope']==native['draft_scope'],
            'unchanged':True,'methods':methods}))
finally:
    api.close()
"""


@pytest.mark.skipif(NATIVE is None, reason="select PR_DISPATCH_NATIVE_PYTHON for native SDK proof")
@pytest.mark.parametrize("mode", ["retained", "unsupported", "foreign"])
def test_native_http_client_keeps_read_and_preparation_inert(
    tmp_path: Path, mode: str,
) -> None:
    assert NATIVE is not None
    selected, scope, page = selection(tmp_path)
    before = selected[1].path.read_bytes()
    task = deepcopy(Client(scope).task)
    task["dedup_key"] = "recipe:reviewer:target=forge.example/owner/repo#7"
    if mode == "foreign":
        task["draft_scope"] = dict(task["draft_scope"], installation="another-installation")
    calls: list[tuple[str, str, dict[str, list[str]]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            address = urlsplit(self.path)
            query = parse_qs(address.query)
            calls.append((self.command, address.path, query))
            if address.path == "/health":
                value: Any = {
                    "exact_dedup_lookup": 0 if mode == "unsupported" else 1,
                    "steering_request_context": 1,
                }
            elif address.path == "/tasks":
                assert query == {"repo": [LANE], "dedup_key": [task["dedup_key"]]}
                value = [task]
            elif address.path == "/tasks/task-a":
                value = task
            else:
                self.send_error(404)
                return
            raw = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:
            calls.append((self.command, self.path, {}))
            self.send_error(405)

        def log_message(self, _format: str, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    owner = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    owner.start()
    try:
        result = subprocess.run(
            [NATIVE, "-B", "-c", CHILD, str(SERVICE.parents[1]),
             f"http://127.0.0.1:{server.server_port}"],
            input=json.dumps({
                "identity": scope.identity.document(), "task_scope": scope.task_scope,
                "page": page, "lane": LANE,
            }),
            text=True, capture_output=True, timeout=15, check=False,
            **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        response = json.loads(result.stdout)
    finally:
        server.shutdown()
        owner.join(2)
        server.server_close()
        assert not owner.is_alive()
    assert all(method == "GET" for method, _path, _query in calls)
    assert selected[1].path.read_bytes() == before
    if mode == "retained":
        assert response["proposal"] == "steer"
        request = response["request"]
        assert request["task_id"] == task["id"]
        assert request["request_id"] == "original-native-http-request"
        assert request["scope"] == task["draft_scope"]
        assert request["expected_revision"] == task["steering_revision"]
        assert [path for _method, path, _query in calls].count("/tasks") == 2
        assert [path for _method, path, _query in calls].count("/tasks/task-a") == 3
    else:
        assert response == {
            "proposal": "unsupported" if mode == "unsupported" else "reconcile",
            "request": None,
        }
        assert not any(path == "/tasks/task-a" for _method, path, _query in calls)
        if mode == "unsupported":
            assert calls == [("GET", "/health", {})]


@pytest.mark.skipif(NATIVE is None, reason="select PR_DISPATCH_NATIVE_PYTHON for native SDK proof")
@pytest.mark.parametrize("mode,expected", [
    ("retained", "steer"), ("operator-form", "noop"), ("absent", "reconcile"),
])
def test_native_coordinator_queue_routes_preserve_retained_owner(
    tmp_path: Path, mode: str, expected: str,
) -> None:
    assert NATIVE is not None
    selected, scope, page = selection(tmp_path / "cache")
    before = selected[1].path.read_bytes()
    fixture = tmp_path / "coordinator"
    fixture.mkdir()
    result = subprocess.run(
        [NATIVE, "-B", "-c", COORDINATOR_CHILD, str(SERVICE.parents[1]), str(fixture)],
        input=json.dumps({
            "identity": scope.identity.document(), "page": page, "lane": LANE, "mode": mode,
        }),
        text=True, capture_output=True, timeout=15, check=False,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["proposal"] == expected
    assert response["prepared"] is (mode == "retained")
    assert response["scope_matches"] and response["unchanged"]
    assert response["methods"] and set(response["methods"]) == {"GET"}
    assert selected[1].path.read_bytes() == before
