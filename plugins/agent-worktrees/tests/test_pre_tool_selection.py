"""One launch-time mode selects SDK or command policy, never both."""
from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
MODES = [None, "", "command", "resident", "RESIDENT", "both"]


@pytest.mark.parametrize("mode", MODES)
def test_cached_command_entry_selects_only_command_policy(mode, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("selection_client", PLUGIN / "scripts" / "hook_client.py")
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    if mode is None:
        monkeypatch.delenv("AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION", raising=False)
    else:
        monkeypatch.setenv("AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION", mode)
    calls = []
    monkeypatch.setattr(client, "_load_sibling", lambda name: SimpleNamespace(is_active=lambda: False))
    monkeypatch.setattr(client, "decide", lambda *a, **k: calls.append((a, k)) or {})
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert client.main(["preToolUse"]) == 0
    assert len(calls) == (0 if mode == "resident" else 1)
    if mode == "resident":
        monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
        assert client.main(["preToolUseStrict"]) == 0
        assert len(calls) == 1
        assert calls[0][1] == {"require_policy": True}
        assert json.loads(capsys.readouterr().out) == {}


@pytest.mark.parametrize("mode", MODES)
def test_extension_registers_only_selected_policy(mode, tmp_path):
    node = shutil.which("node")
    assert node, "resident selection requires Node"
    plugin = tmp_path / "plugin"
    extension = plugin / "extensions" / "agent-worktrees"
    extension.mkdir(parents=True)
    for name in ("extension.mjs", "resident-pre-tool.mjs"):
        shutil.copyfile(PLUGIN / "extensions" / "agent-worktrees" / name, extension / name)
    sdk = plugin / "node_modules" / "@github" / "copilot-sdk"
    sdk.mkdir(parents=True)
    (sdk / "package.json").write_text(
        '{"type":"module","exports":{".":"./mock.mjs","./extension":"./mock.mjs"}}',
        encoding="utf-8",
    )
    (sdk / "mock.mjs").write_text(r"""
export const calls = [];
export const approveAll = () => {};
export async function joinSession(options) {
  calls.push(options);
  return { sessionId: "fixture-session", on: () => {} };
}
""", encoding="utf-8")
    script = r"""
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
const [entry, sdk] = process.argv.slice(2);
const { calls } = await import(pathToFileURL(sdk));
await import(pathToFileURL(entry));
assert.equal(calls.length, 1);
assert.equal(typeof calls[0].onPermissionRequest, "function");
const resident = process.env.AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION === "resident";
assert.equal(Boolean(calls[0].hooks?.onPreToolUse), resident);
if (resident) {
  // Deliberately missing interpreter must deny through the selected callback.
  const result = await calls[0].hooks.onPreToolUse({ toolName: "edit" });
  assert.equal(result.permissionDecision, "deny");
}
"""
    env = os.environ.copy()
    env.pop("AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION", None)
    env["AGENT_WORKTREES_RESIDENT_PRE_TOOL_PYTHON"] = str(tmp_path / "absent-python")
    if mode is not None:
        env["AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION"] = mode
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run(
        [node, "--input-type=module", "-", str(extension / "extension.mjs"), str(sdk / "mock.mjs")],
        input=script, capture_output=True, text=True, encoding="utf-8", timeout=15,
        creationflags=flags, env=env, cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("shell_name", ["powershell.exe", "pwsh"])
def test_powershell_wrapper_runs_only_selected_command(mode, shell_name, tmp_path, native_powershell):
    shell = shutil.which(shell_name)
    if shell is None:
        pytest.skip(f"{shell_name} is unavailable")
    root = tmp_path / "plugin"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    marker = tmp_path / "command-ran"
    (scripts / "pre-tool-hook.ps1").write_text(
        "[IO.File]::WriteAllText($env:SELECTION_MARKER, 'command')\n", encoding="utf-8"
    )
    entry = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))["hooks"]["preToolUse"][0]
    env = os.environ.copy()
    env.update(COPILOT_EXTENSIONS_CONTEXT=str(tmp_path / "context.json"),
               COPILOT_PLUGIN_ROOT=str(root), SELECTION_MARKER=str(marker))
    env.pop("AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION", None)
    if mode is not None:
        env["AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION"] = mode
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", entry["powershell"]],
        input="{}", capture_output=True, text=True, timeout=15, env=env,
        creationflags=flags,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.exists() == (mode != "resident")


def test_direct_native_and_cached_python_resident_entries_do_no_work(tmp_path, native_powershell):
    env = os.environ.copy()
    env["AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION"] = "resident"
    env["USERPROFILE"] = str(tmp_path / "absent-home")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    for command in (
        [native_powershell, "-NoProfile", "-NonInteractive", "-File", str(PLUGIN / "scripts" / "pre-tool-hook.ps1")],
        [sys.executable, "-B", str(PLUGIN / "scripts" / "hook_client.py"), "preToolUse"],
    ):
        result = subprocess.run(command, input="not-json", capture_output=True, text=True,
                                env=env, timeout=15, creationflags=flags)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "" and result.stderr == ""
    for name in (".agent-worktrees", ".copilot-extensions"):
        assert not (tmp_path / "absent-home" / name).exists()


def test_posix_wrapper_skips_before_command_resolution(tmp_path, native_bash):
    entry = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))["hooks"]["preToolUse"][0]
    env = os.environ.copy()
    env.update(AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION="resident", HOME=str(tmp_path / "absent-home"))
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run([native_bash, "-c", entry["bash"]], input="not-json",
                            capture_output=True, text=True, env=env, timeout=15,
                            creationflags=flags)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and result.stderr == ""
