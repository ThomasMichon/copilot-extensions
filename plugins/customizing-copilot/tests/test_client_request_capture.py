from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

SCRIPT = (
    Path(__file__).parents[1] / "skills" / "capturing-client-requests"
    / "scripts" / "capture-client-request.py"
)
spec = importlib.util.spec_from_file_location("client_request_capture", SCRIPT)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)

pytestmark = pytest.mark.guard


def test_real_authorization_and_scoped_environment(monkeypatch):
    monkeypatch.setenv("COPILOT_PROVIDER_BASE_URL", "https://example.com/shared")
    monkeypatch.setenv("COPILOT_PROVIDER_UNKNOWN_OPTION", "must-not-survive")
    monkeypatch.setenv("GH_TOKEN", "synthetic-ambient-credential")
    for name in ("COPILOT_GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        monkeypatch.setenv(name, "synthetic-ambient-credential")
    monkeypatch.setenv("HTTPS_PROXY", "https://example.com/proxy")
    monkeypatch.setenv("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", "synthetic-instructions")
    environment = capture.child_environment("synthetic-key", 12345, "synthetic-model")
    assert capture.authorization("synthetic-key").split() == ["Bearer", "synthetic-key"]
    assert environment["COPILOT_PROVIDER_BASE_URL"] == "http://127.0.0.1:12345/v1"
    assert environment["COPILOT_PROVIDER_API_KEY"] == "synthetic-key"
    assert environment["COPILOT_CUSTOM_INSTRUCTIONS_DIRS"] == "synthetic-instructions"
    assert environment["COPILOT_OFFLINE"] == "true"
    assert environment["NO_PROXY"] == "127.0.0.1"
    assert not {
        "GH_TOKEN", "COPILOT_GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
        "HTTPS_PROXY", "COPILOT_PROVIDER_UNKNOWN_OPTION",
    } & environment.keys()
    assert os.environ["COPILOT_PROVIDER_BASE_URL"] == "https://example.com/shared"


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout_never_reserves_output(tmp_path, timeout):
    target = tmp_path / "request.json"
    with pytest.raises(ValueError, match="finite and positive"):
        capture.capture([sys.executable], tmp_path, target, prompt="synthetic", model="m", timeout=timeout)
    assert not target.exists()


def test_existing_output_is_never_overwritten(tmp_path):
    target = tmp_path / "request.json"
    target.write_bytes(b"existing-private-output")
    with pytest.raises(FileExistsError):
        capture.capture([sys.executable], tmp_path, target, prompt="synthetic", model="m", timeout=1)
    assert target.read_bytes() == b"existing-private-output"


def test_missing_client_reports_failure_without_prompt(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(capture.shutil, "which", lambda _: str(tmp_path / "absent.exe"))

    def missing_client(*_args, **_kwargs):
        raise FileNotFoundError("SYNTHETIC_PRIVATE_SENTINEL in potentially sensitive exception")

    monkeypatch.setattr(capture, "capture", missing_client)
    assert capture.main([
        "--working-directory", str(tmp_path), "--output", str(tmp_path / "request.json"),
        "--prompt", "SYNTHETIC_PRIVATE_SENTINEL",
        "--model", "synthetic-model",
    ]) == 1
    printed = capsys.readouterr().out
    assert "FileNotFoundError" in printed
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in printed
    assert not (tmp_path / "request.json").exists()


def test_relative_client_is_pinned_before_entering_harness(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(capture.shutil, "which", lambda _: "selected-client.exe")
    harness = tmp_path / "different-harness"
    seen = []

    def selected_client(command, working_directory, *_args, **_kwargs):
        seen.append((command, working_directory))
        return {"status": "captured"}

    monkeypatch.setattr(capture, "capture", selected_client)
    monkeypatch.setattr(capture, "analyze_file", lambda *_a, **_k: {"warnings": []})
    assert capture.main([
        "--working-directory", str(harness), "--output", str(tmp_path / "request.json"),
        "--model", "synthetic-model",
    ]) == 0
    assert seen == [([str(tmp_path / "selected-client.exe")], harness)]
    capsys.readouterr()


def guidance_payload():
    return {
        "messages": [],
        "tools": [
            {"type": "function", "function": {
                "name": name, "parameters": {
                    "type": "object", "properties": properties, "required": list(properties),
                },
            }}
            for name, properties in [
                ("glob", {"pattern": {"type": "string"}, "paths": {"anyOf": [
                    {"type": "string"}, {"type": "array", "items": {"type": "string"}},
                ]}}),
                ("view", {"path": {"type": "string"}, "forceReadLargeFiles": {"type": "boolean"}}),
            ]
        ],
    }


def tool_result(identifier, content):
    return {"role": "tool", "tool_call_id": identifier, "content": content}


@pytest.mark.parametrize("status", [
    "absent", "unsafe_root", "redirected_root", "entry_limit", "file_limit",
])
def test_refused_initial_inventory_emits_no_tool_calls(tmp_path, monkeypatch, status):
    import snapshot_rounds

    root = tmp_path / "instructions"
    if status == "unsafe_root":
        root.write_text("Not a directory", encoding="utf-8")
    elif status == "redirected_root":
        root.mkdir()
        resolve = Path.resolve
        monkeypatch.setattr(
            Path, "resolve",
            lambda path, *args, **kwargs: (
                tmp_path / "outside" if path == root else resolve(path, *args, **kwargs)
            ),
        )
        status = "unsafe_root"
    elif status != "absent":
        root.mkdir()
        for name in ("one.md", "two.md"):
            (root / name).write_text("Synthetic guidance", encoding="utf-8")
        limit = "MAX_INVENTORY_ENTRIES" if status == "entry_limit" else "MAX_FILES"
        monkeypatch.setattr(snapshot_rounds, limit, 1)

    snapshot = snapshot_rounds.GuidanceSnapshot(root)
    reply = snapshot.receive(guidance_payload(), 100, tmp_path / "request.json")

    assert "tool_calls" not in reply
    assert snapshot.state == "incomplete"
    assert snapshot.reason == "instructions_" + status
    assert snapshot.pending == {}
    assert snapshot.report()["rounds"][0]["generated_files"]["status"] == status


def test_snapshot_correlates_full_bodies_and_wire_deltas(tmp_path):
    from snapshot_rounds import GuidanceSnapshot

    root = tmp_path / "instructions"
    root.mkdir()
    files = [root / "one.md", root / "two.md"]
    for path in files:
        path.write_text(f"First line for {path.name}\nSecond line\n", encoding="utf-8")
    snapshot = GuidanceSnapshot(root)
    payload = guidance_payload()
    reply = snapshot.receive(payload, 100, tmp_path / "request.json")
    assert json.loads(reply["tool_calls"][0]["function"]["arguments"]) == {
        "pattern": "**/*.md", "paths": str(root),
    }
    payload["messages"] = [tool_result("capture-list", "\n".join(map(str, files)))]
    reply = snapshot.receive(payload, 200, tmp_path / "request.round-2.json")
    assert len(reply["tool_calls"]) == 2
    for index, path in enumerate(files):
        annotated = "\n".join(f"{line_number}. {line}" for line_number, line in enumerate(
            path.read_text().splitlines(), 1,
        ))
        payload["messages"].append(tool_result(f"capture-read-{index}", annotated))
    final = snapshot.receive(payload, 500, tmp_path / "request.round-3.json")
    assert "tool_calls" not in final and snapshot.state == "complete"
    report = snapshot.report()
    assert [row["wire_delta_bytes"] for row in report["rounds"]] == [None, 100, 300]
    assert all(item["source_body_observed"] for item in report["rounds"][-1]["tool_results"])
    assert "First line" not in json.dumps(report)
    assert report["session_start_completion"] == "not_observable"


@pytest.mark.parametrize("listing,reason", [
    ("", "empty_listing"),
    ("Permission denied", "unsupported_or_denied_listing"),
    ("../outside.md", "unsupported_or_denied_listing"),
])
def test_listing_does_not_authorize_unlisted_or_denied_reads(tmp_path, listing, reason):
    from snapshot_rounds import GuidanceSnapshot

    snapshot = GuidanceSnapshot(tmp_path)
    payload = guidance_payload()
    snapshot.receive(payload, 100, tmp_path / "request.json")
    payload["messages"] = [tool_result("capture-list", listing)]
    reply = snapshot.receive(payload, 200, tmp_path / "round2.json")
    assert "tool_calls" not in reply
    assert snapshot.reason == reason


@pytest.mark.parametrize("failure,reason", [
    ("missing", "missing_or_duplicate_tool_result"),
    ("duplicate", "missing_or_duplicate_tool_result"),
    ("denied", "read_denied_truncated_or_unmatched"),
    ("empty_denied", "read_denied_truncated_or_unmatched"),
    ("changed", "guidance_changed_during_read"),
    ("oversize", "guidance_file_limit"),
    ("schema", "unsupported_read_tool"),
])
def test_incomplete_guidance_never_reports_complete(tmp_path, failure, reason):
    from snapshot_rounds import GuidanceSnapshot, MAX_FILE_BYTES

    path = tmp_path / "cue.md"
    path.write_text("" if failure == "empty_denied" else "Synthetic full guidance body", encoding="utf-8")
    payload = guidance_payload()
    snapshot = GuidanceSnapshot(tmp_path)
    snapshot.receive(payload, 100, tmp_path / "request.json")
    payload["messages"] = [tool_result("capture-list", str(path))]
    if failure == "oversize":
        path.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    if failure == "schema":
        payload["tools"][1]["function"]["parameters"]["required"].append("unsupported")
    reply = snapshot.receive(payload, 200, tmp_path / "round2.json")
    if failure not in {"oversize", "schema"}:
        if failure != "missing":
            payload["messages"].append(tool_result("capture-read-0", "Permission denied"))
        if failure == "duplicate":
            payload["messages"].append(tool_result("capture-read-0", "duplicate"))
        if failure == "changed":
            path.write_text("Changed guidance", encoding="utf-8")
        reply = snapshot.receive(payload, 300, tmp_path / "round3.json")
    assert "tool_calls" not in reply
    assert snapshot.state == "incomplete" and snapshot.reason == reason


def test_multi_round_reserves_all_outputs_before_launch(tmp_path, monkeypatch):
    target = tmp_path / "request.json"
    existing = tmp_path / "request.round-2.json"
    existing.write_bytes(b"existing")
    monkeypatch.setattr(capture.subprocess, "Popen", lambda *_a, **_k: pytest.fail("launched client"))
    with pytest.raises(FileExistsError):
        capture.capture(
            [sys.executable], tmp_path, target, prompt="synthetic", model="m", timeout=1,
            load_session_guidance=True, session_id="11111111-2222-4333-8444-555555555555",
        )
    assert existing.read_bytes() == b"existing"
    assert not target.exists()
