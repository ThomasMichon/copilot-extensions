"""Admission precedes policy and lifecycle, independent of global installation."""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest
from agent_procutil import no_window_kwargs

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(f"admission_test_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("kind", ["preToolUse", "postToolUse", "sessionStart", "sessionEnd"])
def test_unmanaged_event_never_dispatches(kind, tmp_path, monkeypatch, capsys):
    client = load("hook_client")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("COPILOT_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "sessionId": "unmanaged", "cwd": str(tmp_path), "toolName": "edit",
        "toolArgs": {"path": "source.py"},
    })))

    def forbidden(*args, **kwargs):
        pytest.fail("unmanaged event reached policy or lifecycle activation")

    monkeypatch.setattr(client, "decide", forbidden)
    monkeypatch.setattr(client, "_enrich_session_payload", forbidden)
    monkeypatch.setattr(client, "_enrich_session_end_payload", forbidden)
    assert client.main([kind]) == 0
    output = capsys.readouterr()
    assert output.out.strip() in {"", "{}"}
    assert not output.err
    assert not (tmp_path / ".copilot").exists()


def fixture_project(tmp_path):
    anchor = tmp_path / "anchor-\u00e9"
    git_dir = anchor / ".git"
    for folder in ("objects", "refs"):
        (git_dir / folder).mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    registry = tmp_path / ".agent-worktrees"
    registry.mkdir()
    (registry / "projects.yaml").write_text('projects:\n  example:\n    config_dir: "~/.example"\n')
    (registry / "repos.yaml").write_text(
        "repos:\n  example:\n    class: worktree\n"
        + f"    {'windows' if os.name == 'nt' else 'linux'}: {json.dumps(str(anchor))}\n"
    )
    return anchor, registry


def test_positive_membership_is_cached_but_registration_alone_is_not(tmp_path, monkeypatch):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, registry = fixture_project(tmp_path)
    payload = {"sessionId": "owned", "cwd": str(anchor)}
    assert module.admitted(payload, home=tmp_path)
    original = module._entries
    monkeypatch.setattr(module, "_entries", lambda *args: pytest.fail("warm membership reparsed registries"))
    assert module.admitted(payload, home=tmp_path)
    monkeypatch.setattr(module, "_entries", original)
    (registry / "projects.yaml").write_text("projects: {}\n")
    assert not module.admitted(payload, home=tmp_path)
    (registry / "projects.yaml").write_text("projects:\n  example: {}\n")
    assert module.admitted(payload, home=tmp_path)
    assert not module.admitted({**payload, "cwd": str(tmp_path)}, home=tmp_path)
    assert not module.admitted({**payload, "sessionId": "../bad"}, home=tmp_path)


@pytest.mark.parametrize("relative", [False, True])
def test_linked_worktree_requires_the_adopted_common_directory(tmp_path, monkeypatch, relative):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, _ = fixture_project(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    admin = anchor / ".git" / "worktrees" / "example-worktree"
    admin.mkdir(parents=True)
    (admin / "HEAD").write_text("ref: refs/heads/work\n")
    (admin / "commondir").write_text("../..\n")
    (admin / "gitdir").write_text(
        (os.path.relpath(worktree / ".git", admin) if relative else str(worktree / ".git")) + "\n"
    )
    (worktree / ".git").write_text("gitdir: " + str(admin) + "\n")
    nested = worktree / "src"
    nested.mkdir()
    payload = {"sessionId": "owned", "cwd": str(nested)}
    assert module.admitted(payload, home=tmp_path)
    (admin / "commondir").write_text(str(tmp_path / "foreign") + "\n")
    assert not module.admitted(payload, home=tmp_path)


def test_generated_registry_admission_needs_no_yaml_install(tmp_path, monkeypatch):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setitem(sys.modules, "yaml", None)
    anchor, _ = fixture_project(tmp_path)
    assert module.admitted({"sessionId": "fresh", "cwd": str(anchor)}, home=tmp_path)


def test_cache_is_scoped_to_session_and_selected_registry(tmp_path, monkeypatch):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, registry = fixture_project(tmp_path)
    payload = {"sessionId": "first", "cwd": str(anchor)}
    assert module.admitted(payload, home=tmp_path)
    calls = []
    original = module._entries
    monkeypatch.setattr(module, "_entries", lambda *args: calls.append(args) or original(*args))
    assert module.admitted({**payload, "sessionId": "second"}, home=tmp_path)
    assert len(calls) == 2
    other = tmp_path / "other-installation"
    other.mkdir()
    (other / "projects.yaml").write_text("projects: {}\n")
    monkeypatch.setattr(module, "resolve_registry_root", lambda **kwargs: other)
    assert not module.admitted(payload, home=tmp_path)
    assert not (other / "cache").exists()
    assert (registry / "cache").is_dir()


def test_real_installation_cells_do_not_share_admission_or_fall_back(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import test_registry_paths as fixtures

    native_run = subprocess.run
    monkeypatch.setattr(fixtures, "subprocess", SimpleNamespace(
        run=lambda *args, **kwargs: native_run(*args, **(kwargs | no_window_kwargs())),
    ))
    anchor, legacy = fixture_project(tmp_path)
    cells = []
    for label in ("first", "second"):
        payload = fixtures._payload(tmp_path, label)
        for name in ("managed_session.py", "guarded_anchor_map.py"):
            shutil.copyfile(SCRIPTS / name, payload / "scripts" / name)
        context, registry = fixtures._stamp(
            tmp_path / "durable", payload,
            marketplace=label, repository=f"example/{label}",
        )
        (registry / "projects.yaml").write_text(
            (legacy / "projects.yaml").read_text() if label == "first" else "projects: {}\n"
        )
        (registry / "repos.yaml").write_text((legacy / "repos.yaml").read_text())
        cells.append((payload, context, registry))
    for index in (0, 1, 0):
        payload, context, registry = cells[index]
        env = environment(tmp_path, anchor)
        env.update(
            COPILOT_PLUGIN_ROOT=str(payload), AGENT_WORKTREES_PAYLOAD_ROOT=str(payload),
            COPILOT_EXTENSIONS_CONTEXT=str(context),
        )
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(payload / "scripts" / "managed_session.py")],
            input=json.dumps({"sessionId": "same-session", "cwd": str(anchor)}),
            text=True, capture_output=True, timeout=10, env=env, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["admitted"] is (index == 0), result.stdout
        assert (registry / "cache" / "managed-sessions").exists() is (index == 0)
    assert not (legacy / "cache").exists()


def environment(home, anchor):
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("COPILOT_", "AGENT_WORKTREES_", "WORKTREE_")):
            env.pop(key)
    env.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        COPILOT_PROJECT_DIR=str(anchor), COPILOT_PLUGIN_ROOT=str(SCRIPTS.parent),
        PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
    )
    return env


@pytest.mark.parametrize("kind", ["preToolUse", "postToolUse", "sessionStart", "sessionEnd"])
@pytest.mark.parametrize("repo_class", ["worktree", "reference", None])
def test_real_python_entry_is_inert_for_registry_only_repo(kind, repo_class, tmp_path):
    anchor, registry = fixture_project(tmp_path)
    (registry / "projects.yaml").write_text("projects: {}\n")
    repos = registry / "repos.yaml"
    if repo_class is None:
        repos.unlink()
        (registry / "projects.yaml").unlink()
    else:
        repos.write_text(repos.read_text().replace("class: worktree", "class: " + repo_class))
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "hook_client.py"), kind],
        input=json.dumps({"sessionId": "unmanaged", "cwd": str(anchor), "toolName": "edit",
                          "toolArgs": {"path": str(anchor / "source.py")}}),
        text=True, capture_output=True, timeout=10, cwd=anchor,
        env=environment(tmp_path, anchor), **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() in {"", "{}"}
    assert not result.stderr
    assert not (tmp_path / ".copilot").exists()
    assert not (registry / "cache").exists()


def test_real_managed_pretool_preserves_anchor_write_protection(tmp_path):
    anchor, _ = fixture_project(tmp_path)
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "hook_client.py"), "preToolUse"],
        input=json.dumps({"sessionId": "managed", "cwd": str(anchor), "toolName": "edit",
                          "toolArgs": {"path": str(anchor / "source.py")}}),
        text=True, capture_output=True, timeout=10, cwd=anchor,
        env=environment(tmp_path, anchor), **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["permissionDecision"] == "deny"
    assert "anchor-write-guard" in result.stdout


def test_invalid_query_does_not_reuse_ambient_admission(tmp_path):
    anchor, registry = fixture_project(tmp_path)
    env = environment(tmp_path, anchor)
    env["COPILOT_AGENT_SESSION_ID"] = "managed"
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "managed_session.py")],
        input="{", text=True, capture_output=True, timeout=5, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["admitted"] is False
    assert not (registry / "cache").exists()


@pytest.mark.parametrize("kind", ["preToolUse", "postToolUse", "sessionStart", "sessionEnd"])
def test_real_powershell_hook_does_not_activate_unmanaged_repo(kind, tmp_path):
    powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("PowerShell is unavailable")
    anchor, registry = fixture_project(tmp_path)
    (registry / "projects.yaml").write_text("projects: {}\n")
    hooks = json.loads((SCRIPTS.parent / "hooks.json").read_text())
    command = hooks["hooks"][kind][0]["powershell"]
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        input=json.dumps({"sessionId": "unmanaged", "cwd": str(anchor), "toolName": "edit",
                          "toolArgs": {"path": str(anchor / "source.py")}}),
        text=True, capture_output=True, timeout=15, cwd=anchor,
        env=environment(tmp_path, anchor), **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() in {"", "{}"}, result.stdout
    assert not result.stderr
    assert not (tmp_path / ".copilot").exists()


@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
def test_session_start_forwards_unicode_membership_query(shell, tmp_path):
    powershell = shutil.which(shell)
    if not powershell:
        pytest.skip("PowerShell is unavailable")
    anchor, _ = fixture_project(tmp_path)
    payload = tmp_path / "payload"
    shutil.copytree(SCRIPTS, payload / "scripts")
    (payload / "scripts" / "hook_client.py").write_text(
        "import json,sys\nfrom managed_session import admitted\n"
        "assert admitted(json.load(sys.stdin))\nprint('{}')\n"
    )
    env = environment(tmp_path, anchor)
    env["COPILOT_PLUGIN_ROOT"] = str(payload)
    hooks = json.loads((SCRIPTS.parent / "hooks.json").read_text())
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command",
         hooks["hooks"]["sessionStart"][0]["powershell"]],
        input=json.dumps({"sessionId": "managed", "cwd": str(anchor)}, ensure_ascii=False),
        text=True, capture_output=True, encoding="utf-8", timeout=10,
        cwd=anchor, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert json.loads(result.stdout) == {}


@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
def test_real_powershell_managed_pretool_preserves_protection(tmp_path, record_testsuite_property, shell):
    powershell = shutil.which(shell)
    if not powershell:
        pytest.skip("PowerShell is unavailable")
    anchor, _ = fixture_project(tmp_path)
    hooks = json.loads((SCRIPTS.parent / "hooks.json").read_text())
    env = environment(tmp_path, anchor)
    env["AGENT_WORKTREES_RESIDENT_PRE_TOOL_PYTHON"] = sys.executable
    payload = tmp_path / "payload"
    shutil.copytree(SCRIPTS, payload / "scripts")
    (payload / "scripts" / "resolve-runtime.ps1").write_text(
        "param([string]$TraceMode)\n$AwPy = '" + sys.executable.replace("'", "''") + "'\n",
        encoding="utf-8-sig",
    )
    env["COPILOT_PLUGIN_ROOT"] = str(payload)
    event = json.dumps({"sessionId": "managed", "cwd": str(anchor), "toolName": "edit",
                        "toolArgs": {"path": str(anchor / "source.py")}})
    visible, foreground, observed, timings = set(), set(), set(), []
    for _ in range(2):
        started = time.monotonic()
        process = subprocess.Popen(
            [powershell, "-NoProfile", "-NonInteractive", "-Command",
             hooks["hooks"]["preToolUse"][0]["powershell"]],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=anchor, env=env, **no_window_kwargs(),
        )
        try:
            process.stdin.write(event)
            process.stdin.close()
            process.stdin = None
            while process.poll() is None:
                if os.name == "nt":
                    from test_status_monitor_windows import (
                        _descendants, _foreground_state, _is_terminal_window,
                        _process_snapshot, _window_snapshot,
                    )

                    processes = _process_snapshot()
                    descendants = _descendants(process.pid, processes)
                    observed.update(pid for pid in descendants if pid != process.pid and
                                    "python" in processes.get(pid, ("", 0))[0].lower())
                    visible.update(hwnd for hwnd, window in _window_snapshot(processes).items()
                                   if window[0] in descendants)
                    active = _foreground_state(processes)
                    if active[1] in descendants and _is_terminal_window(active):
                        foreground.add(active[0])
                assert time.monotonic() - started < 15, "native hook exceeded its fixture deadline"
                time.sleep(0.01)
            stdout, stderr = process.communicate(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
        timings.append(round((time.monotonic() - started) * 1000, 2))
        assert process.returncode == 0, stderr
        assert json.loads(stdout).get("permissionDecision") == "deny", stdout + stderr
    if os.name == "nt":
        assert len(observed) >= 2, "both windowless cycles must observe actual Python descendants"
        assert not visible and not foreground
    record_testsuite_property(f"native_hook_cold_warm_ms:{shell}", json.dumps(timings))
    record_testsuite_property(f"visible_windows:{shell}", len(visible))
    record_testsuite_property(f"foreground_transitions:{shell}", len(foreground))


def test_extension_is_inert_until_adoption_and_stops_after_revocation(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    anchor, registry = fixture_project(tmp_path)
    (registry / "projects.yaml").write_text("projects: {}\n")
    payload = tmp_path / "payload"
    extensions = payload / "extensions" / "agent-worktrees"
    shutil.copytree(SCRIPTS.parent / "extensions" / "agent-worktrees", extensions)
    shutil.copytree(SCRIPTS, payload / "scripts")
    (payload / "plugin.json").write_text('{"name":"agent-worktrees"}')
    sdk = payload / "node_modules" / "@github" / "copilot-sdk"
    sdk.mkdir(parents=True)
    (sdk / "package.json").write_text(json.dumps({
        "type": "module", "exports": {".": "./index.mjs", "./extension": "./extension.mjs"},
    }))
    (sdk / "index.mjs").write_text("export const approveAll = () => {};\n")
    (sdk / "extension.mjs").write_text(
        "export const joinSession = async options => { globalThis.residentHooks = options.hooks; "
        "return { sessionId: 'fixture-session', "
        "on: (name, callback) => { globalThis.handlers[name] = callback; }, "
        "send: () => { throw new Error('unexpected nudge'); } }; };\n"
    )
    state = tmp_path / ".copilot" / "session-state" / "fixture-session"
    state.mkdir(parents=True)
    driver = tmp_path / "driver.mjs"
    driver.write_text("""
import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
globalThis.handlers = {};
const [extension, projects, sidecar] = process.argv.slice(2);
await import(pathToFileURL(extension));
const {createResidentPreToolHook} = await import(new URL("./resident-pre-tool.mjs", pathToFileURL(extension)));
let policies = 0;
const hook = createResidentPreToolHook({
  scriptsRoot: fileURLToPath(new URL("../../scripts/", pathToFileURL(extension))),
  invokePolicy: async () => { policies++; return {permissionDecision: "deny"}; },
});
const nested = process.env.COPILOT_PROJECT_DIR + "/src";
mkdirSync(nested);
const event = {sessionId: "fixture-session", workingDirectory: nested,
  toolName: "edit", toolArgs: {path: process.env.COPILOT_PROJECT_DIR + "/source.py"}};
async function pulse(text) {
  await handlers["assistant.intent"]({data: {intent: text}});
  await handlers["session.idle"]({});
}
await pulse("unmanaged");
assert.equal(existsSync(sidecar), false);
assert.deepEqual(await hook(event), {});
assert.deepEqual(await residentHooks.onPreToolUse(event), {});
assert.equal(policies, 0);
writeFileSync(projects, "projects:\\n  example: {}\\n");
const head = process.env.COPILOT_PROJECT_DIR + "/.git/HEAD";
const headText = readFileSync(head);
unlinkSync(head);
await pulse("incomplete");
assert.equal(existsSync(sidecar), false);
writeFileSync(head, headText);
await pulse("managed");
assert.equal(JSON.parse(readFileSync(sidecar)).intent, "managed");
assert.equal((await hook(event)).permissionDecision, "deny");
assert.equal((await residentHooks.onPreToolUse(event)).permissionDecision, "deny");
assert.equal(policies, 1);
for (const part of ["objects", "refs"]) mkdirSync(nested + "/.git/" + part, {recursive: true});
writeFileSync(nested + "/.git/HEAD", "ref: refs/heads/main\\n");
assert.deepEqual(await hook(event), {}, "a new nested repository invalidates membership");
assert.equal(policies, 1);
writeFileSync(projects, "projects: {}\\n");
await pulse("revoked");
assert.equal(JSON.parse(readFileSync(sidecar)).intent, "managed");
assert.deepEqual(await hook(event), {});
assert.deepEqual(await residentHooks.onPreToolUse(event), {});
assert.equal(policies, 1);
""")
    env = environment(tmp_path, anchor)
    env["COPILOT_PLUGIN_ROOT"] = str(payload)
    env["AGENT_WORKTREES_RESIDENT_PRE_TOOL_PYTHON"] = sys.executable
    env["AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION"] = "resident"
    result = subprocess.run(
        [node, str(driver), str(extensions / "extension.mjs"),
         str(registry / "projects.yaml"), str(state / "substatus.json")],
        text=True, capture_output=True, timeout=15, cwd=anchor,
        env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows venv redirector lifetime")
def test_admission_timeout_retires_the_actual_python_worker(tmp_path):
    from test_hook_policy_lifetime import _pin_descendant

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    scripts = tmp_path / "scripts"
    shutil.copytree(SCRIPTS, scripts)
    marker = tmp_path / "worker.json"
    helper = scripts / "managed_session.py"
    source = helper.read_text()
    pause = (
        "    from hook_process_identity import process_start_time\n"
        "    import time\n"
        f"    marker = Path({str(marker)!r})\n"
        "    prepared = marker.with_suffix('.prepared')\n"
        "    prepared.write_text(json.dumps({'pid': os.getpid(), "
        "'birth': int(process_start_time(os.getpid()))}))\n"
        "    prepared.replace(marker)\n"
        "    time.sleep(60)\n"
    )
    assert source.count("        result = inspect(payload)\n") == 1
    helper.write_text(source.replace(
        "        result = inspect(payload)\n",
        "".join("    " + line + "\n" for line in pause.splitlines()) + "        result = inspect(payload)\n",
    ))
    driver = tmp_path / "timeout.mjs"
    driver.write_text("""
import {pathToFileURL} from "node:url";
const [module, scriptsRoot, python, workingDirectory] = process.argv.slice(2);
const {createManagedSessionAdmission} = await import(pathToFileURL(module));
const check = createManagedSessionAdmission({scriptsRoot, python});
console.log(JSON.stringify(await check({sessionId: "timeout-fixture", workingDirectory})));
""")
    process = subprocess.Popen(
        [node, str(driver), str(SCRIPTS.parent / "extensions" / "agent-worktrees" / "managed-session.mjs"),
         str(scripts), sys.executable, str(tmp_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=environment(tmp_path, tmp_path), **no_window_kwargs(),
    )
    close = None
    try:
        deadline = time.monotonic() + 10
        while not marker.is_file():
            assert process.poll() is None, process.communicate(timeout=5)
            assert time.monotonic() < deadline, "admission worker did not publish its native identity"
            time.sleep(0.01)
        worker = json.loads(marker.read_text())
        retired, close = _pin_descendant(worker["pid"], f"windows:{worker['birth']}")
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert json.loads(stdout) is False
        assert retired(), "actual admission interpreter survived its caller-owned timeout"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        if close:
            close()


@pytest.mark.skipif(os.name != "nt", reason="Windows application execution aliases")
def test_extension_admission_never_launches_windowsapps_aliases(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    aliases = tmp_path / "WindowsApps"
    aliases.mkdir()
    (aliases / "python.exe").write_text("fixture alias must never execute")
    driver = tmp_path / "aliases.mjs"
    driver.write_text("""
import assert from "node:assert/strict";
import childProcess from "node:child_process";
import {syncBuiltinESMExports} from "node:module";
import {dirname, delimiter, join} from "node:path";
import {pathToFileURL} from "node:url";
const [module, scriptsRoot, aliases, python, workingDirectory] = process.argv.slice(2);
const original = childProcess.execFile;
const calls = [];
childProcess.execFile = (...args) => { calls.push(args[0]); return original(...args); };
syncBuiltinESMExports();
const {createManagedSessionAdmission} = await import(pathToFileURL(module));
const environment = {...process.env, PATH: aliases};
delete environment.Path;
const input = {sessionId: "alias-fixture", workingDirectory};
assert.equal(await createManagedSessionAdmission({scriptsRoot, environment})(input), false);
assert.deepEqual(calls, []);
environment.PATH += delimiter + dirname(python);
assert.equal(await createManagedSessionAdmission({
  scriptsRoot, environment, python: join(aliases, "python.exe"),
})(input), false);
assert.deepEqual(calls, []);
assert.equal(await createManagedSessionAdmission({scriptsRoot, environment})(input), false);
assert.deepEqual(calls, [python]);
""")
    result = subprocess.run(
        [node, str(driver), str(SCRIPTS.parent / "extensions" / "agent-worktrees" / "managed-session.mjs"),
         str(SCRIPTS), str(aliases), sys.executable, str(tmp_path)],
        capture_output=True, text=True, timeout=10,
        env=environment(tmp_path, tmp_path), **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
