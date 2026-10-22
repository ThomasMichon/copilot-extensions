import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from agent_chat_service import ClientError
from agent_chat_service.cli import main
import agent_chat_service.workspace as workspace


@pytest.fixture
def owner(tmp_path, monkeypatch):
    anchor, worktree = tmp_path / "anchor", tmp_path / "worktree"
    anchor.mkdir()
    (worktree / "nested").mkdir(parents=True)
    values = {
        ("get", "repo-dir"): str(anchor),
        ("get", "worktree-dir"): str(worktree),
        ("get", "execution-identity"): {
            "schema": "agent-worktrees.execution-identity", "version": 1,
            "status": "ready", "configured": True, "source": "project-machine-config",
            "machine": "example-space", "project": "sample", "platform": "windows",
        },
        ("get", "owner-ref"): "example-space/sample/worktree-1",
        ("list", "--json", "--cache-only", "--worktree-id", "worktree-1"): {
            "version": 1, "worktrees": [{
                "id": "worktree-1", "machine": "example-space", "repo": "sample",
                "platform": "win", "path": str(worktree), "codename": "friendly-label",
            }],
        },
        ("head-session", "--worktree-id", "worktree-1", "--json"): {
            "version": 1, "worktree_id": "worktree-1", "tracked": True,
            "head_session": "startup-1", "active": True, "state": "active",
            "head_revision": 7, "pending_handoffs": [],
        },
    }
    calls = []
    selected = [sys.executable, "-I", "-m", "example_owner"]

    def read(argv, **options):
        assert argv[:5] == [sys.executable, "-I", "-c", workspace._CWD_RUNNER,
                           str((worktree / "nested").resolve())]
        assert argv[5:5 + len(selected)] == selected
        assert 0 < options.pop("timeout") <= 60
        assert options == {"max_capture": 65536}
        args = tuple(argv[5 + len(selected):])
        calls.append(args)
        assert args == ("record", "--cwd", str((worktree / "nested").resolve()), "--json")
        return owner_response(values)

    monkeypatch.setattr(workspace, "run_command", read)
    return {
        "values": values, "calls": calls, "selected": selected, "anchor": anchor,
        "root": worktree, "cwd": str(worktree / "nested"),
    }


def owner_response(values):
    rows = values[("list", "--json", "--cache-only", "--worktree-id", "worktree-1")]
    if isinstance(rows, bytes):
        return rows
    rows = copy.deepcopy(rows["worktrees"])
    row = rows[0] if len(rows) == 1 else rows
    if isinstance(row, dict):
        row["head"] = values[("head-session", "--worktree-id", "worktree-1", "--json")]
    anchor = values[("get", "repo-dir")]
    root = values[("get", "worktree-dir")] or anchor
    context = {
        "schema": "agent-worktrees.workspace-context", "version": 1, "project": "sample",
        "anchor": anchor, "root": root,
        "identity": values[("get", "execution-identity")] if root == anchor else None,
        "worktree": None if root == anchor else row,
    }
    if values[("get", "owner-ref")] != "example-space/sample/worktree-1":
        context["project"] = "other"
    return json.dumps({"ok": True, "version": 1, "context": context}).encode("utf-8")


def resolve(owner, session="startup-1"):
    return workspace.resolve_workspace(
        worktrees_command=owner["selected"], session_id=session, cwd=owner["cwd"],
    )


def test_worktree_identity_survives_sessions_and_label_changes_without_adoption(owner, monkeypatch):
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "unrelated-environment")
    before = Path.cwd()
    first = resolve(owner)
    row = owner["values"][("list", "--json", "--cache-only", "--worktree-id", "worktree-1")]["worktrees"][0]
    row["codename"] = "another-label"
    second = resolve(owner, "startup-2")
    assert first["participant"] == second["participant"] == {
        "kind": "worktree", "id": "worktree-1", "machine": "example-space", "repo": "sample",
    }
    assert first["display_name"] == "friendly-label" and second["display_name"] == "another-label"
    assert first["head"] == {
        "session_id": "startup-1", "revision": 7, "is_initializing_session": True,
        "pending_handoff": False,
    }
    assert second["head"]["is_initializing_session"] is False
    assert first["assignment"] == {
        "status": "unavailable", "reason": "chat-assignment-binding-required",
    }
    assert "actor" not in first and not first["native_listening_verified"] and not first["mutations_performed"]
    assert Path.cwd() == before
    assert len(owner["calls"]) == 2 and not list(owner["root"].glob("*.json"))
    assert ("get", "execution-identity") not in owner["calls"]


def test_anchor_segments_startup_session_and_never_queries_a_synthetic_head(owner):
    values = owner["values"]
    values[("get", "worktree-dir")] = ""
    values[("get", "repo-dir")] = str(owner["root"])
    first = resolve(owner)
    second = resolve(owner, "startup-2")
    assert first["participant"]["kind"] == "anchor"
    assert first["participant"]["id"] == "startup-1"
    assert second["participant"]["id"] == "startup-2"
    assert first["head"] is None and first["owner_ref"] is None and first["display_name"] is None
    assert owner["calls"] == [("record", "--cwd", str(Path(owner["cwd"]).resolve()), "--json")] * 2
    values[("get", "worktree-dir")] = str(owner["root"])
    assert resolve(owner) == first


@pytest.mark.parametrize("state", ["other", "unassigned", "transition", "versioned"])
def test_existing_owner_head_and_execution_tuple_are_not_reassigned(owner, state):
    head = owner["values"][("head-session", "--worktree-id", "worktree-1", "--json")]
    if state == "other":
        head["head_session"] = "other-session"
    elif state == "unassigned":
        head.update(head_session=None, active=False, state=None)
    elif state == "transition":
        head["pending_handoffs"] = [{"token": "existing-handoff"}]
    else:
        row = owner["values"][("list", "--json", "--cache-only", "--worktree-id", "worktree-1")]["worktrees"][0]
        row.update(machine="physical-host", machine_identity_version=1,
                   execution_machine="example-space", environment="windows")
    retained = copy.deepcopy(owner["values"])
    result = resolve(owner)
    assert result["head"]["session_id"] == head["head_session"]
    assert result["head"]["pending_handoff"] == (state == "transition")
    assert result["head"]["is_initializing_session"] == (state in {"transition", "versioned"})
    assert result["participant"]["machine"] == "example-space"
    assert owner["values"] == retained


@pytest.mark.parametrize("change", [
    "session-missing", "session-path", "session-large", "cwd-relative", "cwd-missing", "cwd-file",
])
def test_invalid_initialization_refuses_before_executing_owner(owner, change):
    session, cwd = "startup-1", owner["cwd"]
    if change == "session-missing":
        session = None
    elif change == "session-path":
        session = "../foreign"
    elif change == "session-large":
        session = "s" * 129
    elif change == "cwd-relative":
        cwd = "relative"
    elif change == "cwd-missing":
        cwd = str(owner["root"] / "missing")
    else:
        path = owner["root"] / "file"
        path.write_bytes(b"fixture")
        cwd = str(path)
    with pytest.raises(ClientError, match="no adoption") as caught:
        workspace.resolve_workspace(worktrees_command=owner["selected"], session_id=session, cwd=cwd)
    assert caught.value.code == "invalid_initialization" and owner["calls"] == []


@pytest.mark.parametrize("damage", [
    "outside", "unconfigured", "detected", "identity-version", "owner-mismatch",
    "rows-duplicate", "row-not-object", "row-project", "row-path", "row-platform",
    "tuple-partial", "tuple-version", "label", "head-version", "untracked",
    "head-worktree", "head-active", "head-state", "head-revision", "head-pending",
    "head-missing-session", "head-missing-state",
])
def test_unresolved_or_inconsistent_owner_evidence_refuses_without_fallback(owner, damage):
    values = owner["values"]
    identity = values[("get", "execution-identity")]
    rows = values[("list", "--json", "--cache-only", "--worktree-id", "worktree-1")]["worktrees"]
    head = values[("head-session", "--worktree-id", "worktree-1", "--json")]
    if damage == "outside":
        values[("get", "worktree-dir")] = str(owner["anchor"])
    elif damage == "unconfigured":
        values[("get", "repo-dir")] = values[("get", "worktree-dir")]
        identity["configured"] = False
    elif damage == "detected":
        values[("get", "repo-dir")] = values[("get", "worktree-dir")]
        identity["source"] = "detected"
    elif damage == "identity-version":
        values[("get", "repo-dir")] = values[("get", "worktree-dir")]
        identity["version"] = True
    elif damage == "owner-mismatch":
        values[("get", "owner-ref")] = "other/sample/worktree-1"
    elif damage == "rows-duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif damage == "row-not-object":
        rows[0] = []
    elif damage == "row-project":
        rows[0]["repo"] = "other"
    elif damage == "row-path":
        rows[0]["path"] = str(owner["anchor"])
    elif damage == "row-platform":
        rows[0]["platform"] = "unrecognized-platform"
    elif damage == "tuple-partial":
        rows[0]["environment"] = "windows"
    elif damage == "tuple-version":
        rows[0].update(machine_identity_version=True, execution_machine="example-space", environment="windows")
    elif damage == "label":
        rows[0]["codename"] = "../invalid"
    elif damage == "head-version":
        head["version"] = True
    elif damage == "untracked":
        head["tracked"] = False
    elif damage == "head-worktree":
        head["worktree_id"] = "other"
    elif damage == "head-active":
        head["active"] = False
    elif damage == "head-state":
        head["state"] = "concluded"
    elif damage == "head-revision":
        head["head_revision"] = True
    elif damage == "head-missing-session":
        head.update(active=False, state=None)
        del head["head_session"]
    elif damage == "head-missing-state":
        head.update(head_session=None, active=False)
        del head["state"]
    else:
        head["pending_handoffs"] = None
    with pytest.raises(ClientError) as caught:
        resolve(owner)
    assert caught.value.code in {"workspace_unresolved", "workspace_response_invalid"}
    assert len(owner["calls"]) <= 6


def test_workspace_reads_share_one_finite_deadline(owner, monkeypatch):
    instants = iter([10, 11, 71])
    monkeypatch.setattr(workspace.time, "monotonic", lambda: next(instants))
    with pytest.raises(ClientError) as caught:
        resolve(owner)
    assert caught.value.code == "workspace_timeout"
    assert owner["calls"] == [("record", "--cwd", str(Path(owner["cwd"]).resolve()), "--json")]


def test_late_owner_response_cannot_turn_deadline_expiry_into_success(owner, monkeypatch):
    instants = iter([10, 11, 71])
    monkeypatch.setattr(workspace.time, "monotonic", lambda: next(instants))
    with pytest.raises(ClientError) as caught:
        resolve(owner)
    assert caught.value.code == "workspace_timeout"
    assert owner["calls"] == [("record", "--cwd", str(Path(owner["cwd"]).resolve()), "--json")]


@pytest.mark.parametrize("selected", [None, [], ["agent-worktrees"], ["selected.ps1"]])
def test_workspace_requires_explicit_complete_owner_argv_before_execution(owner, selected):
    with pytest.raises(ClientError) as caught:
        workspace.resolve_workspace(worktrees_command=selected, session_id="startup-1", cwd=owner["cwd"])
    assert caught.value.code == "invalid_configuration" and not owner["calls"]


def test_owner_failure_is_not_retried_or_downgraded_to_anchor(owner, monkeypatch):
    calls = []

    def failed(*args, **kwargs):
        calls.append(args)
        raise ClientError("provider_failed", "Selected owner refused.")

    monkeypatch.setattr(workspace, "run_command", failed)
    with pytest.raises(ClientError) as caught:
        resolve(owner)
    assert caught.value.code == "provider_failed" and len(calls) == 1


@pytest.mark.parametrize("raw", [b"not json", b'{"duplicate":1,"duplicate":2}', b"\xff", b"null"])
def test_malformed_owner_json_has_the_client_response_error_boundary(owner, raw):
    owner["values"][("list", "--json", "--cache-only", "--worktree-id", "worktree-1")] = raw
    with pytest.raises(ClientError) as caught:
        resolve(owner)
    assert caught.value.code == "workspace_response_invalid"
    assert "not json" not in caught.value.message and "duplicate" not in caught.value.message


@pytest.mark.parametrize("source", [[], {}, None, 1, True, "detected"])
def test_anchor_source_type_is_checked_before_configured_provenance(owner, source):
    owner["values"][("get", "repo-dir")] = str(owner["root"])
    owner["values"][("get", "worktree-dir")] = ""
    owner["values"][("get", "execution-identity")]["source"] = source
    with pytest.raises(ClientError) as caught:
        resolve(owner)
    assert caught.value.code == "workspace_unresolved"


@pytest.mark.parametrize("damage", ["schema", "version", "extra", "missing"])
def test_workspace_cli_requires_exact_explicit_initialization_before_owner(owner, damage):
    fields = {
        "schema": "agent-chat.workspace-request", "version": 1, "session_id": "startup-1",
        "cwd": owner["cwd"], "worktrees_command": owner["selected"],
    }
    if damage == "schema":
        fields["schema"] = "other"
    elif damage == "version":
        fields["version"] = True
    elif damage == "extra":
        fields["actor"] = {"codename": "not-an-assignment"}
    else:
        del fields["cwd"]
    output, error = io.StringIO(), io.StringIO()
    with io.TextIOWrapper(io.BytesIO(json.dumps(fields).encode()), encoding="utf-8") as stream:
        with redirect_stdout(output), redirect_stderr(error), patch.object(sys, "stdin", stream):
            assert main(["workspace", "--request-file", "-"]) == 1
    assert not output.getvalue() and json.loads(error.getvalue())["error"] == "invalid_initialization"
    assert owner["calls"] == []


def test_installed_workspace_uses_trusted_cwd_and_selected_owner_without_source_or_environment(
    installed_chat, owner, monkeypatch,
):
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "not-the-host-initialization")
    script = installed_chat.root / "selected_owner.py"
    fixture = installed_chat.root / "owner.json"
    fixture.write_bytes(owner_response(owner["values"]))
    script.write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "assert Path.cwd()==Path(sys.argv[2])\n"
        "assert 'PYTHONPATH' not in os.environ\n"
        "assert sys.argv[3:]==['record','--cwd',str(Path.cwd()),'--json']\n"
        "print(Path(sys.argv[1]).read_text('utf-8'))\n",
        encoding="utf-8",
    )
    request = {
        "schema": "agent-chat.workspace-request", "version": 1,
        "session_id": "startup-1", "cwd": owner["cwd"],
        "worktrees_command": [sys.executable, "-I", str(script), str(fixture), owner["cwd"]],
    }
    catalog = json.loads(installed_chat.run([str(installed_chat.console), "catalog"], cwd=installed_chat.root))
    assert "workspace.py" in catalog["resource_sha256s"]
    result = json.loads(installed_chat.run([
        *catalog["commands"][0]["argv"], "--config", str(installed_chat.root / "absent.json"),
        "workspace", "--request-file", "-",
    ], input_text=json.dumps(request), cwd=installed_chat.root))
    assert result["schema"] == "agent-chat.workspace-context" and result["version"] == 1
    assert result["participant"]["id"] == "worktree-1" and result["display_name"] == "friendly-label"
    assert result["cwd"] == str(Path(owner["cwd"]).resolve()) and result["head"]["is_initializing_session"]
    assert result["assignment"]["status"] == "unavailable"
    assert not result["native_listening_verified"] and not result["mutations_performed"]
    assert not (installed_chat.root / "source").exists() and not owner["calls"]
