"""Practical causal diagnostics and documented manual CLI compatibility."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from agent_dispatch import launch_debug
from agent_dispatch.procutil import no_window_kwargs

pytestmark = pytest.mark.contract("agent_dispatch.ordinary_bridge")


def enable(tmp_path, monkeypatch):
    root = tmp_path / "install"
    root.mkdir()
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    monkeypatch.delenv("AGENT_DISPATCH_BRIDGE_CLI_TRACE", raising=False)
    (root / "launch-debug.json").write_text('{"bridge_cli_trace":true}', encoding="utf-8")
    return root


def finished(root):
    return [json.loads(line) for path in (root / "logs" / "bridge-cli").glob("*.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()]


def test_windows_bom_config_is_supported(tmp_path, monkeypatch):
    root = enable(tmp_path, monkeypatch)
    (root / "launch-debug.json").write_text('{"bridge_cli_trace":true}', encoding="utf-8-sig")
    assert launch_debug.begin(["bridge"], ["status"]) is not None


@pytest.mark.parametrize("stdout,stderr,expected", [
    (
        {"error": "tracking_write refused in send/receive phase: RPC outcome unknown: timed out",
         "stage": "caller_tracking", "code": "owner_rpc_unknown",
         "env": {"OTHER": "opaque environment"}},
        "Windows transport warning: falling back to configured endpoint",
        "tracking_write refused in send/receive phase: RPC outcome unknown: timed out",
    ),
    (
        {"reason": "creation_conflict", "message": "HTTP 409: creation key belongs to retained session",
         "code": "creation_conflict", "prompt": "opaque assignment"},
        "HTTP 409 creation_conflict: retained creation key cannot be reassigned",
        "HTTP 409: creation key belongs to retained session",
    ),
])
def test_causal_error_message_and_separate_stderr_survive(tmp_path, monkeypatch, stdout, stderr, expected):
    root = enable(tmp_path, monkeypatch)
    trace = launch_debug.begin(["bridge"], ["--json", "create", "example@local"])
    assert trace is not None, "Windows UTF-8 BOM config must enable tracing"
    trace.finish(result=subprocess.CompletedProcess([], 1, json.dumps(stdout), stderr))
    row = finished(root)[1]
    assert expected in json.dumps(row["stdout"]["fields"])
    assert row["stderr"]["text"] == stderr
    assert "opaque environment" not in json.dumps(row)
    assert "opaque assignment" not in json.dumps(row)


def test_redacted_causal_text_and_context_without_opaque_stdin(tmp_path, monkeypatch):
    root = enable(tmp_path, monkeypatch)
    monkeypatch.setenv("EXAMPLE_TOKEN", "synthetic-secret-value")
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "native-example")
    monkeypatch.setenv("TMUX_PANE", "%7")
    monkeypatch.setenv("UNRELATED_SETTING", "never-copy-me")
    payload = json.dumps({"prompt": "opaque worker assignment"})
    trace = launch_debug.begin(["bridge"], ["--json", "status", "actor"], prompt_input=payload)
    assert trace
    trace.finish(result=subprocess.CompletedProcess(
        [], 1, json.dumps({"error": "tracking_write synthetic-secret-value \x1b[31mRPC failed"}),
        "HTTP 409 creation_conflict\nTO\x1b[31mKEN=stderr-secret\nopaque worker assignment",
    ))
    rows = finished(root)
    text = json.dumps(rows)
    for secret in ("synthetic-secret-value", "stderr-secret", "opaque worker assignment",
                   "never-copy-me", "\\u001b"):
        assert secret not in text
    assert rows[0]["context"]["session_id"] == "native-example"
    assert rows[0]["context"]["pane"] == "%7"
    assert rows[0]["context"]["install_root"] == str(root)
    assert "tracking_write" in rows[1]["stdout"]["fields"]["error"]
    assert "HTTP 409 creation_conflict" in rows[1]["stderr"]["text"]


def test_json_argv_and_paths_are_not_shell_strings(tmp_path, monkeypatch):
    root = enable(tmp_path, monkeypatch)
    cwd = tmp_path / "O'Brien's (work)  [copy]"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    prefix = [str(cwd / "bridge.exe")]
    argv = ["--json", "create", "repo+test@local", "--charter", "reviewer's (local) profile"]
    trace = launch_debug.begin(prefix, argv)
    assert trace
    row = finished(root)[0]
    assert row["prefix"] == prefix and row["argv"] == argv
    assert row["cwd"] == os.getcwd()
    assert row["argv_exact"] is True and row["cwd_exact"] is True


def test_unknown_flag_and_non_auth_identity_values_remain_exact(tmp_path, monkeypatch):
    root = enable(tmp_path, monkeypatch)
    argv = [
        "--json", "create", "example@local", "--new-unrecognized-option",
        """O'Brien's "quoted" diagnostic value""",
        "--creation-key", "retained-creation-key",
        "--expected-native-session-id", "native-example",
        "--worktree-id", "worktree-example",
    ]
    trace = launch_debug.begin(["bridge"], argv)
    assert trace
    trace.finish(result=subprocess.CompletedProcess(
        argv, 2, "", "unrecognized arguments: --new-unrecognized-option",
    ))
    rows = finished(root)
    assert rows[0]["argv"] == argv
    assert rows[0]["argv_exact"] is True
    assert rows[1]["stderr"]["text"] == "unrecognized arguments: --new-unrecognized-option"


@pytest.mark.parametrize("arguments", [
    ["--token", "opaque-credential"],
    ["--password", "opaque-credential"],
    ["--password=opaque-credential"],
    ["--new-unrecognized-option", "known-credential"],
])
def test_credential_flags_values_and_known_strings_are_still_redacted(tmp_path, monkeypatch, arguments):
    root = enable(tmp_path, monkeypatch)
    monkeypatch.setenv("EXAMPLE_TOKEN", "known-credential")
    argv = ["--json", "create", "example@local", *arguments]
    before = argv[:]
    trace = launch_debug.begin(["bridge"], argv)
    assert trace
    row = finished(root)[0]
    assert argv == before
    assert row["argv_exact"] is False
    assert "opaque-credential" not in json.dumps(row)
    assert "known-credential" not in json.dumps(row)
    assert "[REDACTED]" in json.dumps(row)


def test_unavailable_empty_and_partial_captures_are_distinct(tmp_path, monkeypatch):
    enable(tmp_path, monkeypatch)
    cases = [
        (subprocess.TimeoutExpired(["bridge"], 1), "unavailable", None),
        (subprocess.TimeoutExpired(["bridge"], 1, output=b"partial"), "partial", 7),
        (subprocess.CompletedProcess(["bridge"], 0, "", ""), "complete", 0),
    ]
    for outcome, capture, size in cases:
        trace = launch_debug.begin(["bridge"], ["--json", "status", "actor"])
        assert trace
        if isinstance(outcome, subprocess.TimeoutExpired):
            trace.finish(error=outcome)
        else:
            trace.finish(result=outcome)
        row = json.loads(trace.path.read_text().splitlines()[1])
        assert row["stdout"]["capture"] == capture
        assert row["stdout"]["bytes"] == size
        if capture == "unavailable":
            assert row["stdout"]["sha256"] is None
        else:
            assert len(row["stdout"]["sha256"]) == 64
        if capture == "partial":
            assert row["outcome"] == "unknown_timeout"
            assert outcome.output == b"partial"
            assert row["stderr"]["capture"] == "unavailable"
            assert row["stderr"]["bytes"] is None and row["stderr"]["sha256"] is None
        elif capture == "complete":
            assert row["stderr"]["capture"] == "complete"
            assert row["stdout"]["sha256"] == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


@pytest.mark.parametrize("stderr", [
    'transport warning\n{\n  "env": {\n    "OTHER": "private-value"\n  }\n}',
    'transport warning\n{\n  "env": {\n    "OTHER": "private-value"',
    'transport warning\n{\n  "prompt": {\n    "text": "private-value"\n  }\n}',
    'transport warning\n{\n  "en\x1b[31mv": {\n    "OTHER": "private-value"\n  }\n}',
], ids=["warning-envelope", "malformed-envelope", "nested-prompt", "control-obscured-context"])
def test_warning_prefixed_opaque_blocks_are_omitted_completely(tmp_path, monkeypatch, stderr):
    root = enable(tmp_path, monkeypatch)
    trace = launch_debug.begin(["bridge"], ["--json", "status", "actor"])
    assert trace
    trace.finish(result=subprocess.CompletedProcess(
        [], 1, '{"error":"tracking_write refused: RPC outcome unknown"}', stderr,
    ))
    row = finished(root)[1]
    assert "private-value" not in json.dumps(row)
    assert row["stderr"]["text"] == "<omitted:opaque-context>"
    assert row["stdout"]["fields"]["error"] == "tracking_write refused: RPC outcome unknown"


def test_documented_dispatch_recipes_match_actual_parser_without_network(monkeypatch):
    from agent_dispatch.__main__ import build_parser
    parser = build_parser()
    doc = Path(__file__).resolve().parents[1] / "docs" / "launch-debugging.md"
    commands = [line.removeprefix("<dispatch-command> ") for line in doc.read_text().splitlines()
                if line.startswith("<dispatch-command> ")]
    assert commands
    for command in commands:
        args = parser.parse_args(shlex.split(command))
        assert args.command in {"show", "reservations", "worktree-status", "claim", "start"}
        if args.command == "claim":
            assert args.task == "<task-id>" and args.evaluation is True


@pytest.mark.parametrize("body,warning", [
    ({"error": "tracking_write failed in send/receive phase: RPC outcome unknown: timed out",
      "stage": "caller_tracking", "code": "owner_rpc_unknown"},
     "Windows transport warning: synthetic-secret-value"),
    ({"reason": "creation_conflict", "message": "HTTP 409 creation_conflict: retained creation key"},
     "HTTP 409 creation_conflict: existing retained session"),
])
def test_real_causal_trace_read_after_caller_exit(tmp_path, monkeypatch, body, warning):
    root = enable(tmp_path, monkeypatch)
    monkeypatch.setenv("EXAMPLE_TOKEN", "synthetic-secret-value")
    source = Path(launch_debug.__file__).resolve().parents[1]
    child = tmp_path / "causal_child.py"
    child.write_text(
        f"import sys\nprint({json.dumps(body)!r})\nprint({warning!r},file=sys.stderr)\nsys.exit(17)\n",
        encoding="utf-8",
    )
    script = (
        "import sys,subprocess\n"
        f"sys.path.insert(0,{str(source)!r})\n"
        "from agent_dispatch import launch_debug\n"
        "from agent_dispatch.procutil import no_window_kwargs\n"
        f"prefix=[sys.executable];argv=[{str(child)!r}]\n"
        "trace=launch_debug.begin(prefix,argv,timeout=5)\n"
        "result=subprocess.run([*prefix,*argv],capture_output=True,text=True,encoding='utf-8',"
        "check=False,timeout=5,**no_window_kwargs())\n"
        "trace.finish(result=result)\n"
        "assert result.returncode==17\n"
    )
    caller = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True,
        timeout=15, check=False, **no_window_kwargs(),
    )
    assert caller.returncode == 0, caller.stderr
    rows = finished(root)
    assert len(rows) == 2
    assert rows[1]["returncode"] == 17
    assert rows[1]["stdout"]["fields"] == body
    assert warning.replace("synthetic-secret-value", "[REDACTED]") == rows[1]["stderr"]["text"]
    assert "synthetic-secret-value" not in json.dumps(rows)
