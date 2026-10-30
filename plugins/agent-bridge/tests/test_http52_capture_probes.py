"""Replacement client/authenticated HTTP/journal evidence, without native launch."""

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import bridge_http52_evidence as evidence
from bridge_http_retained_probes import capture_request, record_observations
from agent_bridge import hosting_policy
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
from test_live_inbox_runtime import installation as installation, serving as serving
from test_native_replacement import (
    OLD, NEW, replacement_rig as replacement_rig,
    creation_rig as creation_rig, rig as rig,
)

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.contract("agent_bridge.workspace_creation")


def test_http52_capture_actual_observations(serving, replacement_rig, request, monkeypatch):
    bound = capture_request(evidence, ROOT)
    assert HTTP_PROTOCOL_VERSION == evidence.GENERATION
    vector = json.loads(evidence.safe(ROOT, evidence.INPUT).read_bytes())
    app, http, db, _ = serving
    rig = replacement_rig
    assert db is rig.db
    app.state.session_manager = rig.manager
    # Ground owner/hosting attribution is outside this HTTP observation.
    monkeypatch.setattr(hosting_policy, "marketplace_id", lambda: "")

    async def resolve(name, **_kwargs):
        if name == "dispatch:task-a":
            return replace(rig.target, venue=rig.source)
        assert name == "test-project@local"
        return replace(rig.target)

    app.state.resolver = SimpleNamespace(resolve_async=resolve)
    observed = {"advertised": {
        key: http.get("/health").json()[key]
        for key in ("protocol_version", "min_protocol_version")
    }}
    client = BridgeClient(base_url="http://127.0.0.1:1", token="test")
    posts = []

    def transport(method, path, body=None, **_kwargs):
        if method == "GET":
            assert path == "/health"
            return http.get(path).json()
        assert path == "/api/v1/sessions/workspace-creations"
        posts.append(body)
        result = http.request(method, path, json=body)
        if result.status_code != 200:
            raise BridgeClientError(result.status_code, result.text)
        return result.json()

    monkeypatch.setattr(client, "_request", transport)
    arguments = dict(
        agent="test-project@local", worktree_id=rig.row["id"],
        creation_key=vector["requests"]["creation_key"],
        replacement_task=vector["requests"]["replacement_task"],
    )
    first = client.start_session(**arguments)
    replay = client.start_session(**arguments)
    created = first["creation"]
    observed.update(
        receipt={key: created[key] for key in vector["expected"]["receipt"]},
        same_native_retry=first["session_id"] == replay["session_id"],
        same_grant_retry=created["native_replacement"] == replay["creation"]["native_replacement"],
        replayed=replay["creation"]["replayed"],
        predecessor_preserved=(
            created["native_replacement"]["expected_head"] == OLD
            and created["native_replacement"]["replacement_session"] == NEW
        ),
        native_starts=len(rig.manager.list_sessions()), promotions=len(rig.promotions),
        turns=len(db.execute_read("SELECT * FROM turns")),
    )
    path = "/api/v1/sessions/workspace-creations"
    body = posts[0]
    observed["unexpected_prompt"] = http.post(path, json={
        **body, "creation_prompt": vector["requests"]["unexpected_prompt"],
    }).status_code
    old = BridgeClient(base_url="http://127.0.0.1:1", token="test")
    old_posts = []

    def old_transport(method, path, body=None, **_kwargs):
        if method == "GET":
            assert path == "/health"
            return {"protocol_version": 51, "min_protocol_version": 1}
        old_posts.append(body)
        pytest.fail("HTTP-51 must not receive replacement creation")

    monkeypatch.setattr(old, "_request", old_transport)
    with pytest.raises(BridgeClientError) as refused:
        old.start_session(**arguments)
    observed["old_receiver"] = {"status": refused.value.status, "posts": len(old_posts)}
    rig.source["task"]["hold_reason"] = "operator pause"
    observed["revoked_authority"] = http.post(path, json=body).status_code
    http.headers.pop("Authorization")
    observed["unauthenticated"] = http.post(path, json=body).status_code
    assert observed == vector["expected"]
    if bound is not None:
        record_observations(evidence, bound, request.node.callspec.params["installation"], observed)
def test_capture_report_option_preserves_windows_paths_and_spaces(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    import shlex

    import agent_procutil.owned_process as processes
    import capture_bridge_http48 as capture

    root = tmp_path / "capture root"
    root.mkdir()

    class CapturedOption(Exception):
        pass

    def inspect_options(*args, **kwargs):
        request_path = Path(os.environ[capture.binding.REQUEST])
        assert shlex.split(os.environ["PYTEST_ADDOPTS"]) == [
            "--junitxml=" + str(request_path.parent / "junit.xml"),
        ]
        raise CapturedOption

    monkeypatch.setattr(processes, "run_caller_owned", inspect_options)
    with pytest.raises(CapturedOption):
        capture.run_probes(
            root, {evidence.INPUT: {"sha256": "0" * 64}}, {},
            evidence=evidence,
        )
