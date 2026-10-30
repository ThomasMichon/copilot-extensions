"""Exercise the shipped offline entrypoint with local synthetic artifacts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

SCRIPT = (
    Path(__file__).parents[2] / "skills" / "capturing-client-requests"
    / "scripts" / "analyze-client-request.py"
)


def invoke(tmp_path, raw, *, existing=False):
    root = tmp_path / "synthetic-harness"
    root.mkdir()
    (root / ".git").write_text("synthetic", encoding="utf-8")
    (root / "AGENTS.md").write_text("Synthetic instructions \u03a9.\n", encoding="utf-8")
    source = tmp_path / "request.json"
    source.write_bytes(raw)
    target = tmp_path / "request.analysis.json"
    if existing:
        target.write_bytes(b"existing-report")
    environment = dict(os.environ)
    environment["COPILOT_HOME"] = str(tmp_path / "synthetic-config")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--request", str(source),
         "--working-directory", str(root), "--output", str(target)],
        env=environment, capture_output=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return result, source, target


def test_offline_entrypoint_writes_report_without_changing_request(tmp_path):
    raw = json.dumps({
        "model": "fixture-model",
        "messages": [{"role": "system", "content": "Synthetic instructions \u03a9.\n"}],
        "tools": [{"type": "function", "function": {
            "name": "skill", "description": "SYNTHETIC_PRIVATE_SENTINEL",
            "parameters": {"type": "object", "properties": {"skill": {"type": "string"}}},
        }}],
    }, ensure_ascii=False).encode("utf-8")
    result, source, target = invoke(tmp_path, raw)
    assert result.returncode == 0, result.stdout.decode("utf-8")
    assert source.read_bytes() == raw
    metadata = json.loads(result.stdout)
    assert metadata["status"] == "analyzed"
    assert b"SYNTHETIC_PRIVATE_SENTINEL" not in result.stdout + result.stderr
    report_bytes = target.read_bytes()
    assert b"SYNTHETIC_PRIVATE_SENTINEL" not in report_bytes
    report = json.loads(report_bytes)
    assert report["request"]["original_wire_utf8_bytes"] == len(raw)
    assert any(row["full_content_present"] for row in report["instruction_files"]["files"])
    assert report["builtin_prompt"]["exact_utf8_bytes"] is None


def test_offline_entrypoint_refuses_overwrite_and_hides_invalid_input(tmp_path):
    result, source, target = invoke(tmp_path, b"SYNTHETIC_PRIVATE_SENTINEL", existing=True)
    assert result.returncode == 1
    assert target.read_bytes() == b"existing-report"
    assert source.read_bytes() == b"SYNTHETIC_PRIVATE_SENTINEL"
    assert b"SYNTHETIC_PRIVATE_SENTINEL" not in result.stdout + result.stderr


def test_offline_invalid_json_does_not_echo_content_or_leave_a_report(tmp_path):
    result, source, target = invoke(tmp_path, b"SYNTHETIC_PRIVATE_SENTINEL")
    assert result.returncode == 1
    assert json.loads(result.stdout)["error"] == "JSONDecodeError"
    assert source.read_bytes() == b"SYNTHETIC_PRIVATE_SENTINEL"
    assert not target.exists()
    assert b"SYNTHETIC_PRIVATE_SENTINEL" not in result.stdout + result.stderr


def test_offline_other_protocol_is_not_reported_as_zero_chat_messages(tmp_path):
    raw = json.dumps({"input": "SYNTHETIC_PRIVATE_SENTINEL"}).encode()
    result, source, target = invoke(tmp_path, raw)
    assert result.returncode == 1
    assert json.loads(result.stdout)["error"] == "ValueError"
    assert source.read_bytes() == raw
    assert not target.exists()
