"""Opt-in real owner + native parser/runtime, without installation or live tasks."""

from copy import deepcopy
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlparse

import pytest

from worktree_manager.form_requests import prepared_record, recovery_records, retain_request
from worktree_manager.production_picker.picker_tui import engine_form_requests as requests
from worktree_manager.production_picker.picker_tui import pivots, tasks

pytestmark = pytest.mark.contract("worktree_manager.form_requests")
HELPER = Path(__file__).with_name("_native_form_request_owner.py")


@pytest.fixture
def native_owner(tmp_path):
    python = os.environ.get("COPILOT_TEST_FORM_REQUEST_OWNER_PYTHON")
    if not python:
        pytest.skip("opt-in isolated dispatch test interpreter is not selected")
    assert Path(python).is_file(), "selected isolated dispatch interpreter is unavailable"
    process = subprocess.Popen(
        [python, str(HELPER), "serve", str(tmp_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            assert process.poll() is None, process.communicate()[1].decode()
            if (tmp_path / "url").exists():
                url = (tmp_path / "url").read_text(encoding="utf-8")
                connection = http.client.HTTPConnection("127.0.0.1", urlparse(url).port, timeout=0.5)
                try:
                    connection.request("GET", "/health")
                    if connection.getresponse().status == 200:
                        break
                except (OSError, http.client.HTTPException):
                    pass
                finally:
                    connection.close()
            time.sleep(0.05)
        else:
            pytest.fail("disposable owner did not become healthy")
        yield python, tmp_path
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


@pytest.mark.parametrize("name", ["agent-dispatch.json", "agent-dispatch-maintenance.json"])
@pytest.mark.timeout(120)
def test_native_contributions_recover_only_original_acceptance_after_row_and_card_loss(
    native_owner, monkeypatch, name,
):
    python, root = native_owner
    repo = Path(__file__).resolve().parents[3]
    raw = json.loads((repo / "plugins" / "agent-dispatch" / "pivots" / name).read_text(encoding="utf-8"))
    prefix = [python, str(HELPER), "invoke", str(root)]
    action = next(action for action in raw["actions"] if action["key"] == "steer")
    for template in (action["run"], action["request"]["prepare"], action["request"]["receipt"]):
        template[:1] = prefix
    reg = pivots.parse_manifest(raw, name=name.removesuffix(".json"), source_path=str(root / name))
    action = next(action for action in reg.actions if action.key == "steer")
    runtime = tasks.RegisteredPivotRuntime(reg)
    task_id = (root / "task").read_text(encoding="utf-8")
    ok, output = runtime.run_json_resolved([*prefix, "show", task_id])
    assert ok, output
    ctx = json.loads(output)
    ctx.update(task_id=task_id, machine="host", request_id="native-original")
    values = {"answer": "original\nliteral {task_id} [red]plain"}
    ok, output = runtime.run_json_resolved(pivots.format_form_template(
        action.form["request"]["prepare"], ctx, values,
    ))
    assert ok, output
    record = prepared_record(reg, action, ctx, values, {"card": "original"}, json.loads(output))
    path = retain_request(root / "drafts", record)
    monkeypatch.setattr(requests, "current_request_registrations", lambda: [reg])
    ok, output = requests.run_retained_request(path, record)
    assert not ok and "response lost" in output
    advanced = subprocess.run(
        [python, str(HELPER), "advance", str(root)], capture_output=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert advanced.returncode == 0, advanced.stderr
    # A new client has no board rows/card; it has only the persisted request.
    restored = recovery_records(path.parent)[0][1]
    (root / name).write_text(json.dumps(raw), encoding="utf-8")
    fresh = subprocess.run(
        [sys.executable, "-c",
         "import json,sys;from pathlib import Path;"
         "from worktree_manager.agent_worktrees_runtime import ensure_engine_runtime;"
         "ensure_engine_runtime();"
         "from worktree_manager.production_picker.picker_tui import pivots;"
         "from worktree_manager.production_picker.picker_tui import engine_form_requests as r;"
         "source=Path(sys.argv[1]);path=Path(sys.argv[2]);"
         "reg=pivots.parse_manifest(json.loads(source.read_text()),name=source.stem,source_path=str(source));"
         "r.current_request_registrations=lambda:[reg];"
         "print(json.dumps(r.run_retained_request(path,json.loads(path.read_text()),receipt=True)))",
         str(root / name), str(path)],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert fresh.returncode == 0, fresh.stderr
    ok, output = json.loads(fresh.stdout)
    assert ok, output
    receipt = json.loads(output)
    assert receipt["consumed_at"] is not None
    assert receipt["acceptance"]["request_id"] == "native-original"
    assert receipt["wake"]["id"] == receipt["acceptance"]["wake_operation_id"]
    assert receipt["wake_status"] == "delivered"
    assert receipt["steering_scope"] == record["prepared_request"]["scope"]
    ok, output = requests.run_retained_request(path, restored)
    assert ok and json.loads(output)["receipt"]["acceptance"] == receipt["acceptance"]
    snapshot = subprocess.run(
        [python, str(HELPER), "snapshot", str(root)], capture_output=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert snapshot.returncode == 0, snapshot.stderr
    observed = json.loads(snapshot.stdout)
    assert len(observed["answers"]) == len(observed["wake_messages"]) == 1
    assert observed["answers"][0]["fields"] == values
    assert observed["wake_messages"] == [record["prepared_request"]["message"]]
    wrong_scope = deepcopy(restored)
    wrong_scope["context"]["draft_scope"]["queue"] = "another-owner"
    wrong_scope["prepared_request"]["scope"]["queue"] = "another-owner"
    ok, output = requests.run_retained_request(path, wrong_scope, receipt=True)
    assert not ok and "scope" in output.lower()
    assert json.loads(path.read_text(encoding="utf-8")) == restored
