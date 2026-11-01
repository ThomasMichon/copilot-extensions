"""Additional bounded process, metadata and import fault witnesses."""

from __future__ import annotations

from copy import deepcopy
import json
import re
from pathlib import Path
import sys
import subprocess
from typing import Any
from uuid import UUID

import pytest

from test_agenda_store import (
    AGENDA, CLI, PUBLISH, STORE, SKILL, documents as documents, home as home,
    outcome, proof, queue, request, update,
)


def test_publisher_timeout_is_pending_and_process_is_settled(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke = PUBLISH._invoke

    async def short(argv: list[str], payload: bytes) -> dict[str, Any]:
        return await invoke(argv, payload, timeout=0.05)

    monkeypatch.setattr(PUBLISH, "_invoke", short)
    status = PUBLISH.publish(home, [sys.executable, "-c", "import time; time.sleep(2)"])
    assert status["state"] == "pending" and "timed out" in status["error"]["message"]
    assert STORE.read(home)["revision"] == 1


def test_empty_structured_adapter_error_is_not_supported_delivery(home: Path) -> None:
    status = PUBLISH.publish(home, [sys.executable, "-c",
        "print('{\"ok\":false,\"error\":{\"code\":\"\",\"message\":\"\"}}')"])
    assert status["state"] == "pending" and status["error"]["code"] == "publisher-unavailable"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX legacy-adoption durability path")
def test_bounded_documents_not_artificially_limited_to_one_aggregate_document(
    tmp_path: Path, documents: dict[str, Any]
) -> None:
    documents["boards"]["agenda.json"]["legacy_extension"] = "x" * 90000
    documents["boards"]["other.json"] = deepcopy(documents["boards"]["agenda.json"])
    # Import is bounded per document; its two valid boards exceed 128 KiB together.
    documents["cursor"]["queue"] = ["other.json"]
    for name, document in {"cursor.json": documents["cursor"], **documents["boards"]}.items():
        (tmp_path / name).write_text(json.dumps(document))
    STORE.mutate(tmp_path, request({"operation": "init"}, 0, "import"), import_home=tmp_path)
    assert len(STORE.read(tmp_path)["documents"]["boards"]) == 2


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX legacy-adoption durability path")
def test_utf8_legacy_document_bound_is_not_ascii_escape_expansion(
    tmp_path: Path, documents: dict[str, Any]
) -> None:
    documents["boards"]["agenda.json"]["extension"] = "\u00e9" * 50000
    for name, document in {"cursor.json": documents["cursor"], **documents["boards"]}.items():
        (tmp_path / name).write_text(json.dumps(document, ensure_ascii=False))
    STORE.mutate(tmp_path, request({"operation": "init"}, 0, "import"), import_home=tmp_path)
    assert STORE.read(tmp_path)["documents"]["boards"]["agenda.json"]["extension"] == "\u00e9" * 50000


@pytest.mark.parametrize("raw", [b'{"number":' + b"1" * 5000 + b"}"])
def test_integer_decode_limits_are_diagnostics(raw: bytes) -> None:
    with pytest.raises(AGENDA.AgendaError, match="cannot decode"):
        AGENDA.decode(raw)


def test_exact_document_byte_limit() -> None:
    prefix, suffix = b'{"padding":"', b'"}'
    raw = prefix + b"x" * (131072 - len(prefix) - len(suffix)) + suffix
    assert len(raw) == 131072 and AGENDA.decode(raw)["padding"]
    with pytest.raises(AGENDA.AgendaError, match="128 KiB"):
        AGENDA.decode(raw + b" ")


def test_exact_observation_limit_and_shape(home: Path) -> None:
    _, initial = STORE.delivery_record(home)
    size = len((STORE.encode(initial) + "\n").encode())
    action_size = len(initial["snapshot"]["next_action"].encode())
    action = "x" * (32768 - size + action_size)
    update(home, {"operation": "wait", "reason": "fixture", "next_action": action,
                  "evidence": proof()})
    _, observation = STORE.delivery_record(home)
    assert len((STORE.encode(observation) + "\n").encode()) == 32768
    assert set(observation) == {"schema", "version", "source", "revision", "committed_at", "snapshot"}
    assert set(observation["source"]) == {"worktree", "session", "store_id"}
    assert PUBLISH.publish(home, [sys.executable, "-c", "print('{\"ok\":true}')"])["state"] == "delivered"
    update(home, {"operation": "wait", "reason": "fixture", "next_action": action + "x",
                  "evidence": proof()})
    assert PUBLISH.publish(home, [str(home / "must-not-execute")])["error"]["code"] == "observation-too-large"


@pytest.mark.parametrize("metadata", [{"actor": "x" * 1025}, {"session": "x" * 1025},
                                    {"actor": "\ud800"}])
def test_invalid_caller_metadata_has_no_commit(home: Path, metadata: dict[str, Any]) -> None:
    before = STORE.read(home)
    with pytest.raises(AGENDA.AgendaError):
        STORE.mutate(home, request({"operation": "park", "ref": "fixture", "reason": "nonblocking"}),
                     **metadata)
    assert STORE.read(home) == before


def test_cli_history_zero_is_invalid_not_default(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert CLI.main(["history", "--state-home", str(home), "--limit", "0"]) == 2
    assert "integer >= 1" in capsys.readouterr().err


def test_imported_check_labels_are_not_a_generated_recheck(tmp_path: Path) -> None:
    cursor = json.loads((SKILL / "assets" / "cursor.deferred.example.json").read_text())
    board = json.loads((SKILL / "assets" / "agenda.deferred.example.json").read_text())
    cursor["frames"] = []
    cursor["status"] = "steering"
    cursor["deferred"][0]["checks"] = [{"claimed": "imported check"}]
    STORE.mutate(tmp_path, request({"operation": "init", "documents": {
        "cursor": cursor, "boards": {"agenda.json": board}}}, 0, "init"))
    with pytest.raises(AGENDA.AgendaError, match="accepted by this store"):
        update(tmp_path, {"operation": "restore", "id": cursor["deferred"][0]["id"],
                          "acceptance_ref": "asserted permission", "evidence": proof()})


def test_import_cannot_clone_stale_json_from_a_managed_home(
    home: Path, documents: dict[str, Any]
) -> None:
    for name, document in {"cursor.json": documents["cursor"], **documents["boards"]}.items():
        (home / name).write_text(json.dumps(document))
    target = home / "other-store"
    target.mkdir()
    with pytest.raises(AGENDA.AgendaError, match="source is managed"):
        STORE.mutate(target, request({"operation": "init"}, 0, "init"), import_home=home)


def test_completed_agenda_can_accept_a_new_assigned_root(home: Path) -> None:
    outcome(home, "ready")
    outcome(home, "passed")
    update(home, {"operation": "complete-root", "acceptance_ref": "recorded canonical acceptance",
                  "evidence": proof(STORE.read(home)["snapshot"]["exit_criteria"][0])})
    assert STORE.read(home)["snapshot"]["status"] == "complete"
    queue(home)
    assert STORE.read(home)["snapshot"]["status"] == "working"
    update(home, {"operation": "select", "agenda": "child.json", "assignment_ref": "new assignment"})
    assert "child.json" in STORE.read(home)["snapshot"]["root"]


def test_generated_store_id_is_stable_lowercase_uuid_hex(home: Path) -> None:
    initial = STORE.read(home)
    identity = initial["store_id"]
    assert re.fullmatch(r"[0-9a-f]{32}", identity)
    assert UUID(hex=identity).version == 4
    assert STORE.history(home)[0]["source"]["store_id"] == identity
    update(home, {"operation": "park", "ref": "fixture", "reason": "nonblocking"})
    assert STORE.read(home)["store_id"] == identity
    assert STORE.delivery_record(home)[1]["source"]["store_id"] == identity


@pytest.mark.parametrize("size", [32769, 35840, 35841])
def test_valid_json_reply_exact_shared_frame_bound(home: Path, size: int) -> None:
    assert PUBLISH.BODY_LIMIT == 32768 and PUBLISH.REPLY_LIMIT == 35840
    # A valid reply, not oversized invalid JSON that would fail independently.
    prefix, suffix = b'{"ok":true,"fixture":"', b'"}\n'
    payload = prefix + b"x" * (size - len(prefix) - len(suffix)) + suffix
    assert len(payload) == size and json.loads(payload)["ok"] is True
    code = (
        "import sys; n=int(sys.argv[1]); "
        "prefix=b'{\"ok\":true,\"fixture\":\"'; suffix=b'\"}\\n'; "
        "sys.stdout.buffer.write(prefix+b'x'*(n-len(prefix)-len(suffix))+suffix)"
    )
    status = PUBLISH.publish(home, [sys.executable, "-B", "-c", code, str(size)])
    if size <= 35840:
        assert status["state"] == "delivered"
    else:
        assert status["state"] == "pending"
        assert "reply exceeds 35 KiB" in status["error"]["message"]
    assert STORE.read(home)["revision"] == 1


@pytest.mark.parametrize("revision", [(1 << 63) - 1, 1 << 63, 1 << 100])
@pytest.mark.parametrize("mode", ["documents", "import"])
def test_managed_revision_invalid_init_does_not_fence_legacy_lane(
    tmp_path: Path, documents: dict[str, Any], revision: int, mode: str
) -> None:
    documents["cursor"]["revision"] = revision
    for name, value in {"cursor.json": documents["cursor"], **documents["boards"]}.items():
        (tmp_path / name).write_text(json.dumps(value))
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert AGENDA.inspect(tmp_path).revision == revision
    change = {"operation": "init"} if mode == "import" else {
        "operation": "init", "documents": documents}
    with pytest.raises(AGENDA.AgendaError, match="revision bound|exhausted"):
        STORE.mutate(tmp_path, request(change, 0, "invalid-init"),
                     import_home=tmp_path if mode == "import" else None)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    assert AGENDA.inspect(tmp_path).revision == revision


@pytest.mark.parametrize("revision", [1 << 63, 1 << 100])
@pytest.mark.parametrize("operation", ["expected", "history", "read", "delivery"])
def test_managed_revision_huge_parameters_have_no_partial_state(
    home: Path, revision: int, operation: str
) -> None:
    before = STORE.read(home)
    with pytest.raises(AGENDA.AgendaError, match="revision bound"):
        if operation == "expected":
            STORE.mutate(home, request({"operation": "park", "ref": "fixture", "reason": "nonblocking"},
                                      revision, "huge-expected"))
        elif operation == "history":
            STORE.history(home, before_revision=revision)
        elif operation == "read":
            STORE.read(home, expected_revision=revision)
        else:
            STORE.record_delivery(home, revision, {"state": "delivered"})
    assert STORE.read(home) == before
    assert len(STORE.history(home)) == 1


def test_managed_revision_exhausted_current_refuses_increment(
    tmp_path: Path, documents: dict[str, Any]
) -> None:
    documents["cursor"]["revision"] = STORE.MAX_REVISION - 1
    init = request({"operation": "init", "documents": documents}, 0, "last-init")
    accepted, _ = STORE.mutate(tmp_path, init)
    assert accepted["revision"] == STORE.MAX_REVISION
    before = STORE.read(tmp_path)
    with pytest.raises(AGENDA.AgendaError, match="exhausted"):
        STORE.mutate(tmp_path, request({"operation": "park", "ref": "fixture", "reason": "nonblocking"},
                                     STORE.MAX_REVISION, "exhausted-update"))
    assert STORE.read(tmp_path) == before
    assert len(STORE.history(tmp_path)) == 1
    assert STORE.history(tmp_path, before_revision=STORE.MAX_REVISION) == []
    assert STORE.mutate(tmp_path, init) == (accepted, False)


def test_relative_home_actual_cli_init_read_update_check_history_publish(
    tmp_path: Path, documents: dict[str, Any]
) -> None:
    (tmp_path / "lane").mkdir()
    (tmp_path / "init.json").write_text(json.dumps(
        request({"operation": "init", "documents": documents}, 0, "relative-cli-init")))

    def invoke(command: str, *args: str) -> dict[str, Any]:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        result = subprocess.run(
            [sys.executable, "-B", str(SKILL / "scripts" / "agenda_cli.py"),
             command, "--state-home", "lane", *args],
            cwd=tmp_path, capture_output=True, text=True, timeout=10, creationflags=flags)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    assert invoke("init", "--request", "init.json")["result"]["revision"] == 1
    assert invoke("read")["snapshot"]["revision"] == 1
    (tmp_path / "update.json").write_text(json.dumps(request({
        "operation": "wait", "reason": "synthetic review",
        "next_action": "relative-cli-revision-2", "evidence": proof()}, 1, "relative-cli-update")))
    assert invoke("update", "--request", "update.json")["result"]["persisted"] is True
    assert invoke("check")["next_action"] == "relative-cli-revision-2"
    assert len(invoke("history")["history"]) == 2
    assert invoke("publish")["delivery"]["state"] == "not-configured"


def test_relative_home_direct_store_and_publisher_apis(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(home.parent)
    relative = Path(home.name)
    state = STORE.read(relative)
    assert STORE.validate_documents(relative, state["documents"]).revision == 1
    req = request({"operation": "park", "ref": "relative-api", "reason": "nonblocking"})
    result, accepted = STORE.mutate(relative, req)
    assert accepted and result["revision"] == 2
    assert STORE.read(relative)["snapshot"]["parked"] == 1
    assert len(STORE.history(relative)) == 2
    assert STORE.delivery_record(relative)[0] == 2
    assert PUBLISH.publish(relative, [sys.executable, "-B", "-c",
                                    "print('{\"ok\":true}')"])["state"] == "delivered"
    assert STORE.record_delivery(relative, 2, {"state": "pending"})["state"] == "pending"
    assert STORE.mutate(home, req) == (result, False)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX legacy-adoption durability path")
def test_relative_home_direct_same_home_import(
    tmp_path: Path, documents: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    for name, value in {"cursor.json": documents["cursor"], **documents["boards"]}.items():
        (legacy / name).write_text(json.dumps(value))
    monkeypatch.chdir(tmp_path)
    result, _ = STORE.mutate(Path("legacy"), request({"operation": "init"}, 0, "relative-import"),
                             import_home=Path("legacy"))
    assert result["persisted"] and STORE.read(Path("legacy"))["revision"] == 1


@pytest.mark.parametrize("operation", ["read", "mutate", "history", "publish"])
@pytest.mark.parametrize("path_kind", ["link", "ancestor", "traversal"])
def test_relative_home_retains_symlink_ancestor_and_traversal_refusal(
    home: Path, monkeypatch: pytest.MonkeyPatch, operation: str, path_kind: str
) -> None:
    before = STORE.read(home)
    link = home.parent / f"{home.name}-linked"
    link.symlink_to(home, target_is_directory=True)
    monkeypatch.chdir(home.parent)
    relative = (Path(link.name) if path_kind == "link" else
                Path(link.name) / "descendant" if path_kind == "ancestor" else
                Path(home.name) / ".." / home.name)
    with pytest.raises(AGENDA.AgendaError, match="unsafe"):
        if operation == "read":
            STORE.read(relative)
        elif operation == "mutate":
            STORE.mutate(relative, request({"operation": "park", "ref": "fixture", "reason": "nonblocking"}))
        elif operation == "history":
            STORE.history(relative)
        else:
            PUBLISH.publish(relative, [])
    assert STORE.read(home) == before


@pytest.mark.parametrize("entry", ["api", "cli"])
def test_distinct_asyncio_timeout_keeps_accepted_state_history_and_pending_delivery(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], entry: str
) -> None:
    class DistinctAsyncioTimeout(Exception):
        pass

    assert not issubclass(DistinctAsyncioTimeout, (TimeoutError, OSError))
    monkeypatch.setattr(PUBLISH.asyncio, "TimeoutError", DistinctAsyncioTimeout)

    async def timeout(argv: list[str], payload: bytes) -> dict[str, Any]:
        raise PUBLISH.asyncio.TimeoutError("synthetic Python 3.10 asyncio timeout")

    monkeypatch.setattr(PUBLISH, "_invoke", timeout)
    req = request({"operation": "park", "ref": "timeout-fixture", "reason": "nonblocking"})
    if entry == "api":
        result, accepted = STORE.mutate(home, req, publisher_configured=True)
        assert accepted
        delivery = PUBLISH.publish(home, [sys.executable])
    else:
        path = home / "request.json"
        path.write_text(json.dumps(req))
        assert CLI.main(["update", "--state-home", str(home), "--request", str(path),
                         "--publish-arg", sys.executable]) == 0
        output = json.loads(capsys.readouterr().out)
        result, delivery = output["result"], output["delivery"]
    assert result["persisted"] is True and delivery["state"] == "pending"
    assert "asyncio timeout" in delivery["error"]["message"]
    state = STORE.read(home)
    assert state["revision"] == 2 and state["snapshot"]["parked"] == 1
    assert state["delivery"] == delivery
    assert STORE.history(home)[0] == result and len(STORE.history(home)) == 2
    assert STORE.mutate(home, req, publisher_configured=True) == (result, False)


def test_legacy_evidence_field_collision_is_explicit_not_dropped(
    tmp_path: Path, documents: dict[str, Any]
) -> None:
    documents["cursor"]["frames"][0]["executions"] = {"original": "preserve unknown extension"}
    STORE.mutate(tmp_path, request({"operation": "init", "documents": documents}, 0, "init"))
    before = STORE.read(tmp_path)
    with pytest.raises(AGENDA.AgendaError, match="recorded executions is not a list"):
        update(tmp_path, {"operation": "link", "executions": [{"kind": "task", "ref": "fixture"}],
                          "evidence": proof()})
    assert STORE.read(tmp_path) == before


def test_no_native_identity_is_inferred_from_environment(
    tmp_path: Path, documents: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "not-an-authenticated-store-source")
    monkeypatch.setenv("USER", "also-not-authentication")
    result, _ = STORE.mutate(tmp_path,
        request({"operation": "init", "documents": documents}, 0, "init"))
    assert result["actor"]["qualification"] == "unknown"
    assert result["source"]["session"] is None


def test_request_file_cannot_be_symlink(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    link = home / "request-link.json"
    link.symlink_to(home / "missing-request")
    assert CLI.main(["update", "--state-home", str(home), "--request", str(link)]) == 2
    assert "unsafe symlink" in capsys.readouterr().err


@pytest.mark.parametrize("cursor_change", [
    {"frames": None}, {"deferred": [None], "version": 2}, {"queue": [None]},
])
def test_malformed_import_cursor_is_diagnostic(
    tmp_path: Path, documents: dict[str, Any], cursor_change: dict[str, Any]
) -> None:
    documents["cursor"].update(cursor_change)
    with pytest.raises(AGENDA.AgendaError):
        STORE.mutate(tmp_path, request({"operation": "init", "documents": documents}, 0, "init"))
    with pytest.raises(AGENDA.AgendaError, match="no accepted initialization|uninitialized"):
        STORE.read(tmp_path)


@pytest.fixture
def provider_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import agenda_provider

    package = tmp_path / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "agenda_contract.py").write_text("CONTRACT_VERSIONS = (1, 2)\n")
    (package / "__main__.py").write_text(
        "import argparse\n"
        "def build_parser():\n"
        " p=argparse.ArgumentParser(); s=p.add_subparsers(dest='command')\n"
        " a=s.add_parser('agenda-publish')\n"
        " a.add_argument('--contract-version',type=int,choices=(1,2),default=1)\n"
        " a.add_argument('--json',action='store_true'); return p\n")
    health = {"capabilities": ["worktree-agendas-v2"],
              "worktrees": {"status": "ready", "generation": "a" * 32}}
    health_file = tmp_path / "health.json"
    health_file.write_text(json.dumps(health))
    calls = tmp_path / "calls.json"
    mode = tmp_path / "reply-mode.txt"
    mode.write_text("ok")
    (package / "worktrees_client.py").write_text(
        "import json\nfrom pathlib import Path\n"
        f"HEALTH=Path({str(health_file)!r}); CALLS=Path({str(calls)!r}); MODE=Path({str(mode)!r})\n"
        "def read_health(): return json.loads(HEALTH.read_text())\n"
        "class Client:\n"
        " def _request(self, action, payload, **bounds):\n"
        "  CALLS.write_text(json.dumps({'action':action,'payload':payload,'bounds':bounds}))\n"
        "  if MODE.read_text()=='unsupported':\n"
        "   return {'ok':False,'contract_version':2,'error':{'code':'unsupported','message':'fixture unsupported'}}\n"
        "  b=payload['observation']; s=b['source']\n"
        "  return {'ok':True,'contract_version':2,'generation':payload['expected_generation'],\n"
        "   'sequence':19,'project':payload['project'],'worktree_id':s['worktree'],\n"
        "   'session_id':s['session'],'availability':'observed','observation':b}\n"
        "def _control_client(): return Client()\n")
    monkeypatch.setattr(agenda_provider, "PROVIDER",
        f"import sys; sys.path.insert(0, {str(tmp_path)!r})\n" + agenda_provider.PROVIDER)
    return {"package": package, "calls": calls, "mode": mode, "health": health_file,
            "adapter": agenda_provider}


def v2_observation(home: Path) -> bytes:
    observation = STORE.delivery_record(home)[1]
    observation["version"] = 2
    observation["snapshot"]["cursor_version"] = 4
    observation["source"]["session"] = "fixture-head"
    return (STORE.encode(observation) + "\n").encode()


def test_provider_v2_selected_interpreter_negotiates_then_sends_exact_payload(
    home: Path, provider_fixture: dict[str, Any]
) -> None:
    payload = v2_observation(home)
    reply = provider_fixture["adapter"].deliver(sys.executable, "fixture-project", payload)
    assert reply["ok"] and reply["contract_version"] == 2 and reply["sequence"] == 19
    calls = json.loads(provider_fixture["calls"].read_text())
    assert calls == {"action": "agenda-publish", "payload": {
        "contract_version": 2, "project": "fixture-project",
        "expected_generation": "a" * 32, "observation": json.loads(payload)},
        "bounds": {"max_response_bytes": 35 * 1024, "total_timeout": 1.0}}


@pytest.mark.parametrize("unsupported", ["declaration", "capability", "unready", "generation", "parser"])
def test_provider_v2_missing_support_never_enters_publication(
    home: Path, provider_fixture: dict[str, Any], unsupported: str
) -> None:
    if unsupported == "declaration":
        (provider_fixture["package"] / "agenda_contract.py").write_text("CONTRACT_VERSIONS=(1,)\n")
    elif unsupported == "parser":
        (provider_fixture["package"] / "__main__.py").write_text(
            "import argparse\ndef build_parser(): return argparse.ArgumentParser()\n")
    else:
        health = json.loads(provider_fixture["health"].read_text())
        if unsupported == "capability":
            health["capabilities"] = ["worktree-agendas-v1"]
        elif unsupported == "unready":
            health["worktrees"]["status"] = "draining"
        else:
            health["worktrees"]["generation"] = "invalid"
        provider_fixture["health"].write_text(json.dumps(health))
    result = provider_fixture["adapter"].deliver(sys.executable, "fixture-project", v2_observation(home))
    assert result["ok"] is False and result["error"]["message"]
    assert not provider_fixture["calls"].exists()


def test_provider_v2_structured_unsupported_is_visible(
    home: Path, provider_fixture: dict[str, Any]
) -> None:
    provider_fixture["mode"].write_text("unsupported")
    result = provider_fixture["adapter"].deliver(sys.executable, "fixture-project", v2_observation(home))
    assert result["error"]["code"] == "unsupported"
    assert provider_fixture["calls"].exists()


def test_provider_probe_uses_actual_build_parser_without_main_project_option() -> None:
    import ast
    import agenda_provider

    # This fixture requires the source provider test context. It never imports
    # a real host endpoint or enters the provider's operational main routine.
    main = pytest.importorskip("agent_worktrees.__main__", reason="actual provider test dependencies unavailable")
    tree = ast.parse(agenda_provider.PROVIDER)
    parse_call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "parse_args")
    argv = ast.literal_eval(parse_call.args[0])
    assert argv == ["agenda-publish", "--contract-version", "2", "--json"]
    parsed = main.build_parser().parse_args(argv)
    assert parsed.command == "agenda-publish" and parsed.contract_version == 2
    with pytest.raises(SystemExit):
        main.build_parser().parse_args([*argv, "--project", "fixture-project"])


def test_provider_isolated_child_ignores_cwd_and_pythonpath_shadow_modules(
    home: Path, provider_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    shadow = home / "untrusted-cwd"
    shadow.mkdir()
    touched = home / "shadow-executed"
    malicious = f"open({str(touched)!r},'w').write('executed')\nraise RuntimeError('shadow import')\n"
    (shadow / "json.py").write_text(malicious)
    (shadow / "re.py").write_text(malicious)
    package = shadow / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text(malicious)
    monkeypatch.chdir(shadow)
    monkeypatch.setenv("PYTHONPATH", str(shadow))
    result = provider_fixture["adapter"].deliver(sys.executable, "fixture-project", v2_observation(home))
    assert result["ok"] is True
    assert not touched.exists()
    assert provider_fixture["calls"].exists()
