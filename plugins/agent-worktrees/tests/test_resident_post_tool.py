"""Opt-in resident postToolUse: one launch-time mode selects SDK or command entry."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from agent_procutil import no_window_kwargs
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
EXTENSION = PLUGIN / "extensions" / "agent-worktrees"
MODES = [None, "", "command", "resident", "RESIDENT", "both"]


def _node() -> str:
    node = shutil.which("node")
    assert node, "resident post-tool requires Node"
    return node


def _run_node(script: str, *args: str, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_node(), "--input-type=module", "-", *args], input=script, capture_output=True,
        text=True, encoding="utf-8", timeout=30, env=env, cwd=cwd, **no_window_kwargs(),
    )


@pytest.mark.parametrize("mode", MODES)
def test_extension_registers_post_tool_only_when_selected(mode: str | None, tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    extension = plugin / "extensions" / "agent-worktrees"
    shutil.copytree(EXTENSION, extension)
    shutil.copyfile(
        PLUGIN.parents[1] / "libs" / "copilot-driver-core" / "src"
        / "agent_remote_driver" / "bridge_extension" / "sdk-compat.mjs",
        extension / "sdk-compat.mjs",
    )
    sdk = plugin / "node_modules" / "@github" / "copilot-sdk"
    sdk.mkdir(parents=True)
    (sdk / "package.json").write_text(
        '{"type":"module","exports":{".":"./mock.mjs","./extension":"./mock.mjs"}}', encoding="utf-8",
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
const resident = process.env.AGENT_WORKTREES_POST_TOOL_IMPLEMENTATION === "resident";
const hooks = calls[0].hooks?.handlers ?? calls[0].hooks;
assert.equal(Boolean(hooks?.onPostToolUse), resident);
assert.equal(Boolean(hooks?.onPreToolUse), false, "post-tool selection never selects pre-tool");
if (resident) assert.deepEqual(await hooks.onPostToolUse({
  sessionId: "fixture-session", workingDirectory: process.cwd(), toolName: "edit",
}), {}, "unadmitted sessions do no post-tool work");
"""
    env = os.environ.copy()
    env.pop("AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION", None)
    env.pop("AGENT_WORKTREES_POST_TOOL_IMPLEMENTATION", None)
    env["AGENT_WORKTREES_RESIDENT_PRE_TOOL_PYTHON"] = str(tmp_path / "absent-python")
    if mode is not None:
        env["AGENT_WORKTREES_POST_TOOL_IMPLEMENTATION"] = mode
    result = _run_node(script, str(extension / "extension.mjs"), str(sdk / "mock.mjs"), env=env, cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("shell_name", ["powershell.exe", "pwsh"])
def test_powershell_post_entry_runs_only_selected_command(
    mode: str | None, shell_name: str, tmp_path: Path,
) -> None:
    shell = shutil.which(shell_name)
    if shell is None:
        pytest.skip(f"{shell_name} is unavailable")
    scripts = tmp_path / "plugin" / "scripts"
    scripts.mkdir(parents=True)
    marker = tmp_path / "command-ran"
    (scripts / "post-tool-hook.ps1").write_text(
        "function Invoke-PostToolHook { param($Raw) [IO.File]::WriteAllText($env:SELECTION_MARKER, 'command') }\n",
        encoding="utf-8",
    )
    entry = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))["hooks"]["postToolUse"][0]
    env = os.environ.copy()
    env.update(COPILOT_PLUGIN_ROOT=str(tmp_path / "plugin"), SELECTION_MARKER=str(marker))
    env.pop("AGENT_WORKTREES_POST_TOOL_IMPLEMENTATION", None)
    if mode is not None:
        env["AGENT_WORKTREES_POST_TOOL_IMPLEMENTATION"] = mode
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", entry["powershell"]],
        input="{}", capture_output=True, text=True, timeout=30, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.exists() == (mode != "resident")
    if mode == "resident":
        assert result.stdout == "" and result.stderr == ""


def test_posix_post_entry_skips_before_client_resolution(tmp_path: Path, native_bash: str) -> None:
    entry = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))["hooks"]["postToolUse"][0]
    env = os.environ.copy()
    env.update(AGENT_WORKTREES_POST_TOOL_IMPLEMENTATION="resident", HOME=str(tmp_path / "absent-home"))
    result = subprocess.run([native_bash, "-c", entry["bash"]], input="not-json",
                            capture_output=True, text=True, env=env, timeout=15, **no_window_kwargs())
    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and result.stderr == ""


_ROUNDTRIP = r"""
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
const [module, cwd] = process.argv.slice(2);
const { createResidentPostToolHook, UNAVAILABLE } = await import(pathToFileURL(module));
const reports = [];
const hook = createResidentPostToolHook({
  scriptsRoot: cwd, platform: "win32", admit: async () => true, report: reason => reports.push(reason),
});
const input = { sessionId: "post-fixture", workingDirectory: cwd, toolName: "view",
  toolArgs: { path: cwd }, toolResult: { textResultForLlm: "omitted" } };
const outputs = [await hook(input), await hook({ ...input, toolName: "empty" })];
console.log(JSON.stringify({ outputs, reports, unavailable: UNAVAILABLE }));
"""


def _roundtrip(tmp_path: Path, env_overrides: dict[str, str]) -> dict[str, Any]:
    env = os.environ.copy()
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_WORKTREES_NUDGE", "CROSS_REPO_GUARD"):
        env.pop(key, None)
    env.update(USERPROFILE=str(tmp_path), AGENT_HOME=str(tmp_path), **env_overrides)
    result = _run_node(_ROUNDTRIP, str(EXTENSION / "resident-post-tool.mjs"), str(tmp_path),
                       env=env, cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows resident post-tool transport")
def test_resident_post_tool_matches_native_command_request(tmp_path: Path) -> None:
    from agent_worktrees.hook_ipc import HookIpcServer

    seen: list[tuple[str, dict[str, Any]]] = []

    def decide(kind: str, payload: dict[str, Any], deadline: float | None) -> dict[str, Any]:
        seen.append((kind, payload))
        return {} if payload.get("toolName") == "empty" else {"additionalContext": "resident"}

    server = HookIpcServer(decide)
    server.start()
    try:
        root = tmp_path / ".agent-worktrees"
        root.mkdir()
        (root / "status-monitor.lock").write_text(json.dumps(server.rendezvous()), encoding="utf-8")
        value = _roundtrip(tmp_path, {"AGENT_WORKTREES_NUDGE": "off"})
    finally:
        server.close()
    assert value["outputs"] == [{"additionalContext": "resident"}, {}]
    assert value["reports"] == []
    assert [kind for kind, _ in seen] == ["postToolUse", "postToolUse"]
    payload = seen[0][1]
    metadata = payload.pop("_agentWorktrees")
    assert metadata["nativePolicy"] == 1
    assert metadata["environment"]["AGENT_WORKTREES_NUDGE"] == "off"
    assert metadata["environment"]["CROSS_REPO_GUARD"] is None
    assert payload == {
        "sessionId": "post-fixture", "workingDirectory": str(tmp_path), "cwd": str(tmp_path),
        "toolName": "view", "toolArgs": {"path": str(tmp_path)},
    }


@pytest.mark.skipif(os.name != "nt", reason="Native Windows resident post-tool transport")
@pytest.mark.parametrize("case", ["absent", "foreign-installation", "no-capability"])
def test_resident_post_tool_unavailable_reports_once_without_fallback(tmp_path: Path, case: str) -> None:
    root = tmp_path / ".agent-worktrees"
    root.mkdir()
    if case != "absent":
        lock: dict[str, Any] = {
            "hook_transport": "tcp", "hook_endpoint": "127.0.0.1:9", "hook_token": "t",
            "hook_generation": "g", "hook_capabilities": ["native-tool-policy-v1"],
        }
        if case == "foreign-installation":
            lock["installReceipt"] = "C:\\other\\install.json"
        else:
            lock["hook_capabilities"] = []
        (root / "status-monitor.lock").write_text(json.dumps(lock), encoding="utf-8")
    value = _roundtrip(tmp_path, {})
    assert value["outputs"] == [{}, {}]
    assert value["reports"] == [value["unavailable"]]


def test_resident_post_tool_posix_runs_same_client(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "hook_client.py").write_text(
        "import json,sys\n"
        "assert sys.argv[1:] == ['postToolUse']\n"
        "payload=json.load(sys.stdin)\n"
        "print(json.dumps({'additionalContext': payload['cwd'] + '|' + payload['toolName']}))\n",
        encoding="utf-8",
    )
    script = r"""
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
const [module, scripts, cwd] = process.argv.slice(2);
const { createResidentPostToolHook } = await import(pathToFileURL(module));
const hook = createResidentPostToolHook({ scriptsRoot: scripts, platform: "linux", admit: async () => true });
assert.deepEqual(await hook({ sessionId: "s", workingDirectory: cwd, toolName: "edit" }),
  { additionalContext: `${cwd}|edit` });
assert.deepEqual(await hook({ sessionId: "s", workingDirectory: "relative", toolName: "edit" }), {});
"""
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", "")])
    if os.name != "nt" and not (Path(sys.executable).parent / "python3").exists():
        pytest.skip("python3 is not beside the test interpreter")
    result = _run_node(script, str(EXTENSION / "resident-post-tool.mjs"), str(scripts), str(tmp_path),
                       env=env, cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
