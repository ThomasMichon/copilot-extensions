"""Resident SDK policy tests run in a contained native Node process."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("case", [
    "reads", "drift", "contract-drift", "missing", "context", "write",
    "active", "invalid-flag", "future-contract", "hot-toggle", "failure",
    "invalid-result", "sdk-input", "python-policy", "python-error", "python-missing-guard",
    "real-protected-write", "real-invalid-context", "replacement", "oversize",
    "real-malformed-guard", "real-throwing-guard",
    "python-empty", "report-failure", "bom", "python-legacy", "python-invalid-json",
    "real-allowed-shell", "real-allowed-tool",
    "real-missing-policy", "real-strict-active",
    "real-array-result", "real-invalid-decision", "real-invalid-context-result", "real-unknown-result",
    "python-encoding", "python-warning", "python-invalid-shape",
    "python-runtime-unavailable", "python-missing-worker", "real-invalid-decision-type",
    "python-unknown-response", "root-relative-stop", "slash-root-relative-stop",
])
def test_resident_pre_tool_contract(case, tmp_path):
    if case in {"root-relative-stop", "slash-root-relative-stop"} and os.name != "nt":
        pytest.skip("Windows root-relative override semantics")
    node = shutil.which("node")
    assert node, "resident hook contract requires Node"
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in (
        "hook-read-policy.json", "statelessness_guard.py", "cross_repo_guard.py",
        "anchor_write_guard.py", "pr_supersede_guard.py",
        "hook_runtime.py", "pretool_killswitch.py", "hook_pre_relevance.py", "hook_pre_policy.py", "hook_policy_process.py",
    ):
        shutil.copyfile(PLUGIN / "scripts" / name, scripts / name)
    home = tmp_path / "home"
    home.mkdir()
    flag = tmp_path / "pretool.json"
    client = scripts / "hook_client.py"
    client.write_text(
        "import json,sys\np=json.load(sys.stdin)\n"
        "assert p['cwd'] == p['workingDirectory']\n"
        "assert p['toolArgs']['path'] == 'non-ascii-\\u00e9.txt'\n"
        "print(json.dumps({'permissionDecision':'deny','permissionDecisionReason':'real Python policy'}))\n",
        encoding="utf-8",
    )
    if case.startswith("real-"):
        for path in (PLUGIN / "scripts").glob("*.py"):
            shutil.copyfile(path, scripts / path.name)
        (home / ".git").mkdir()
        (home / ".agent-worktrees").mkdir()
        (home / ".agent-worktrees" / "config.yaml").write_text(
            "default_branch: main\nstateless: true\n", encoding="utf-8"
        )
    module = PLUGIN / "extensions" / "agent-worktrees" / "resident-pre-tool.mjs"
    driver = tmp_path / "driver.mjs"
    driver.write_text(
        """
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { appendFileSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
const [modulePath, root, flag, cwd, python, name] = process.argv.slice(2);
const { createResidentPreToolHook, createPythonPolicy } = await import(pathToFileURL(modulePath));
const env = { ...process.env, PRETOOL_KILLSWITCH_STATE_FILE: flag };
delete env.COPILOT_EXTENSIONS_CONTEXT;
const reports = [];
let calls = 0;
const input = { toolName: "view", toolArgs: { path: "non-ascii-\\u00e9.txt" },
  workingDirectory: cwd, sessionId: "fixture-session", timestamp: new Date() };
const fallback = async payload => {
  calls++;
  assert.equal(payload.cwd, cwd);
  assert.equal(payload.sessionId, "fixture-session");
  if (name === "failure" || name === "report-failure") throw new Error("fixture");
  if (name === "invalid-result") return [];
  return { permissionDecision: "deny", permissionDecisionReason: "fixture policy" };
};
const make = invokePolicy => createResidentPreToolHook({
  scriptsRoot: root, environment: env, invokePolicy, report: reason => {
    if (name === "report-failure") throw new Error("fixture diagnostic failure");
    reports.push(reason);
  },
});
const hook = make(fallback);
const assertUnavailable = result => {
  assert.equal(result.permissionDecision, undefined);
  for (const field of ["agent-worktrees", "Boundary:", "Effect:", "Next:", "Retry:"])
    assert.ok(result.additionalContext.includes(field), JSON.stringify(result));
};
if (name === "reads" || name === "bom") {
  if (name === "bom") {
    const path = join(root, "anchor_write_guard.py");
    writeFileSync(path, "\\uFEFF" + readFileSync(path, "utf8"));
  }
  for (const toolName of ["view", "rg", "glob"]) assert.deepEqual(await hook({ ...input, toolName }), {});
  assert.equal(calls, 0);
} else if (["drift", "contract-drift", "missing", "replacement", "oversize"].includes(name)) {
  assert.deepEqual(await hook(input), {});
  if (name === "drift") appendFileSync(join(root, "anchor_write_guard.py"), "\\n# new policy\\n");
  if (name === "contract-drift") writeFileSync(join(root, "hook-read-policy.json"), "{}");
  if (name === "missing") unlinkSync(join(root, "anchor_write_guard.py"));
  if (name === "replacement") {
    writeFileSync(join(root, "replacement.py"), "# substituted guard\\n");
    renameSync(join(root, "replacement.py"), join(root, "anchor_write_guard.py"));
  }
  if (name === "oversize") writeFileSync(join(root, "hook-read-policy.json"), " ".repeat(4097));
  assert.equal((await hook(input)).permissionDecision, "deny");
  assert.equal(calls, 1);
} else if (name === "active") {
  writeFileSync(flag, JSON.stringify({ active: true }));
  assert.deepEqual(await hook({ ...input, toolName: "edit" }), {});
  assert.equal(calls, 0);
} else if (["root-relative-stop", "slash-root-relative-stop"].includes(name)) {
  writeFileSync(flag, JSON.stringify({ active: true }));
  env.PRETOOL_KILLSWITCH_STATE_FILE = flag.slice(2);
  if (name === "slash-root-relative-stop") {
    env.PRETOOL_KILLSWITCH_STATE_FILE = env.PRETOOL_KILLSWITCH_STATE_FILE.replaceAll("\\\\", "/");
  }
  assert.equal((await hook({ ...input, toolName: "edit" })).permissionDecision, "deny");
  assert.equal(calls, 1);
} else if (name === "hot-toggle") {
  assert.deepEqual(await hook(input), {});
  writeFileSync(flag, JSON.stringify({ active: true }));
  assert.deepEqual(await hook({ ...input, toolName: "edit" }), {});
  writeFileSync(flag, JSON.stringify({ active: false }));
  assert.equal((await hook({ ...input, toolName: "edit" })).permissionDecision, "deny");
  assert.equal(calls, 1);
} else if (name === "sdk-input") {
  assertUnavailable(await hook({ ...input, workingDirectory: undefined }));
  assert.equal(calls, 0);
  assert.deepEqual(reports, ["invalid-sdk-input"]);
} else if (["real-allowed-shell", "real-allowed-tool"].includes(name)) {
  const payload = {
    ...input,
    toolName: name === "real-allowed-shell" ? "powershell" : "session_artifacts",
    toolArgs: name === "real-allowed-shell" ?
      { command: "Get-Date -Format o" } : { operation: "read", path: "checkpoint.md" },
  };
  const raw = spawnSync(python, ["-X", "utf8", "-B", join(root, "hook_client.py"), "preToolUseStrict"], {
    windowsHide: true, cwd, env, encoding: "utf8", timeout: 28000,
    input: JSON.stringify({ ...payload, cwd }),
  });
  assert.equal(raw.error, undefined);
  assert.equal(raw.status, 0, raw.stderr);
  assert.equal(raw.stdout.trim(), "{}", JSON.stringify({
    status: raw.status, stdout: raw.stdout, stderr: raw.stderr,
  }));
  const legacy = spawnSync(python, ["-X", "utf8", "-B", join(root, "hook_client.py"), "preToolUse"], {
    windowsHide: true, cwd, env, encoding: "utf8", timeout: 28000,
    input: JSON.stringify({ ...payload, cwd }),
  });
  assert.equal(legacy.status, 0, legacy.stderr);
  assert.equal(legacy.stdout, "", "legacy command-hook no-op remains silent");
  const actual = make(createPythonPolicy({ scriptsRoot: root, python, environment: env }));
  assert.deepEqual(await actual(payload), {});
  assert.deepEqual(reports, []);
} else if (name === "real-strict-active") {
  writeFileSync(flag, JSON.stringify({ active: true }));
  const observed = join(cwd, "stopped-guard-ran");
  writeFileSync(join(root, "statelessness_guard.py"),
    "from pathlib import Path\\nPath(" + JSON.stringify(observed) + ").write_text('ran')\\n");
  for (const kind of ["preToolUseStrict", "preToolUse"]) {
    const raw = spawnSync(python, ["-X", "utf8", "-B", join(root, "hook_client.py"), kind], {
      windowsHide: true, cwd, env, encoding: "utf8", timeout: 28000,
      input: JSON.stringify({ ...input, toolName: "edit", cwd }),
    });
    assert.equal(raw.error, undefined);
    assert.equal(raw.status, 0, raw.stderr);
    assert.equal(raw.stdout.trim(), kind === "preToolUseStrict" ? "{}" : "");
  }
  assert.throws(() => readFileSync(observed), { code: "ENOENT" });
} else if (name === "real-missing-policy") {
  unlinkSync(join(root, "hook_pre_policy.py"));
  const payload = { ...input, toolName: "edit", cwd };
  for (const kind of ["preToolUse", "preToolUseStrict"]) {
    const raw = spawnSync(python, ["-X", "utf8", "-B", join(root, "hook_client.py"), kind], {
      windowsHide: true, cwd, env, encoding: "utf8", timeout: 28000,
      input: JSON.stringify(payload),
    });
    assert.equal(raw.error, undefined);
    assert.equal(raw.status, 0, raw.stderr);
    const decision = JSON.parse(raw.stdout);
    assertUnavailable(decision);
    assert.match(decision.additionalContext, /hook_pre_policy.py unavailable/);
  }
  const actual = make(createPythonPolicy({ scriptsRoot: root, python, environment: env }));
  assertUnavailable(await actual(payload));
  assert.deepEqual(reports, ["policy-unavailable"]);
} else if (name.startsWith("real-")) {
  if (name === "real-invalid-context") env.COPILOT_EXTENSIONS_CONTEXT = join(cwd, "missing-context.json");
  if (name === "real-malformed-guard") writeFileSync(join(root, "statelessness_guard.py"), "def invalid(:\\n");
  if (name === "real-throwing-guard") writeFileSync(join(root, "statelessness_guard.py"),
    "def decide(*args, **kwargs):\\n    raise RuntimeError('fixture policy failure')\\n");
  const invalidGuardResults = {
    "real-array-result": "[]", "real-invalid-decision": "{'permissionDecision':'invalid'}",
    "real-invalid-decision-type": "{'permissionDecision':[]}",
    "real-invalid-context-result": "{'additionalContext':[]}",
    "real-unknown-result": "{'error':'unavailable'}",
  };
  if (name in invalidGuardResults) writeFileSync(join(root, "statelessness_guard.py"),
    "def decide(*args, **kwargs):\\n    return " + invalidGuardResults[name] + "\\n");
  const actual = make(createPythonPolicy({ scriptsRoot: root, python, environment: env }));
  const result = await actual({
    ...input, toolName: "create", toolArgs: { path: join(cwd, "efforts", "active", "fixture.md") },
  });
  assert.equal(reports.length, 0, "must be an actual policy outcome, not adapter failure");
  if (name === "real-protected-write") {
    assert.equal(result.permissionDecision, "deny");
    assert.match(result.permissionDecisionReason, /stateless|personal/i);
  } else {
    assertUnavailable(result);
    assert.match(result.additionalContext,
      name === "real-invalid-context" ? /context/i : /statelessness_guard.py/i);
  }
} else if (["python-policy", "python-error", "python-missing-guard", "python-empty", "python-legacy", "python-invalid-json", "python-encoding", "python-warning", "python-invalid-shape", "python-runtime-unavailable", "python-missing-worker", "python-unknown-response"].includes(name)) {
  if (name === "python-encoding") env.PYTHONIOENCODING = "cp1252";
  if (name === "python-runtime-unavailable") writeFileSync(join(root, "agent_procutil.py"), "# no owned runtime API\\n");
  if (name === "python-missing-worker") unlinkSync(join(root, "hook_policy_process.py"));
  if (name === "python-unknown-response") writeFileSync(join(root, "hook_client.py"),
    "import json,sys\\nprint('private policy diagnostic', file=sys.stderr)\\nprint(json.dumps({'unexpected':True}))\\n");
  if (name === "python-warning") appendFileSync(join(root, "hook_client.py"),
    "print('expected policy warning', file=sys.stderr)\\n");
  if (name === "python-error") writeFileSync(join(root, "hook_client.py"),
    "import sys\\nprint('private policy diagnostic', file=sys.stderr)\\nraise SystemExit(3)\\n");
  if (name === "python-missing-guard") unlinkSync(join(root, "anchor_write_guard.py"));
  if (name === "python-empty") writeFileSync(join(root, "hook_client.py"), "# no decision\\n");
  if (name === "python-invalid-json" || name === "python-invalid-shape") {
    writeFileSync(join(root, "hook_client.py"),
      "import sys\\nprint('private policy diagnostic', file=sys.stderr)\\n" +
      (name === "python-invalid-shape" ? "print('[]')\\n" : "print('private invalid output')\\n"));
  }
  if (name === "python-legacy") writeFileSync(join(root, "hook_client.py"),
    "import sys\\nif sys.argv[1] == 'preToolUse': print('{}')\\n");
  const actual = make(createPythonPolicy({ scriptsRoot: root, python, environment: env }));
  const result = await actual({ ...input, toolName: "edit" });
  if (["python-policy", "python-encoding", "python-warning", "python-missing-guard"].includes(name)) {
    assert.equal(result.permissionDecision, "deny");
    assert.equal(result.permissionDecisionReason, "real Python policy");
  } else {
    assertUnavailable(result);
  }
  assert.equal(reports.length, ["python-policy", "python-encoding", "python-warning", "python-missing-guard"].includes(name) ? 0 : 1);
  const diagnostic = {
    "python-empty": "policy-response-empty", "python-legacy": "policy-response-empty",
    "python-error": "policy-process-failed", "python-invalid-json": "policy-response-invalid",
    "python-invalid-shape": "policy-response-invalid",
    "python-runtime-unavailable": "policy-lifetime-unavailable",
    "python-unknown-response": "policy-response-invalid",
  }[name];
  if (diagnostic) assert.deepEqual(reports, [diagnostic]);
} else {
  if (name === "context") env.COPILOT_EXTENSIONS_CONTEXT = join(cwd, "context.json");
  if (name === "invalid-flag") writeFileSync(flag, '{"active":"true"}');
  if (name === "future-contract") writeFileSync(flag, JSON.stringify({
    active: true, contract: { id: "copilot-extensions.pretool-killswitch", version: 2 },
  }));
  const payload = ["write", "failure", "invalid-result", "report-failure"].includes(name) ? { ...input, toolName: "edit" } : input;
  const result = await hook(payload);
  if (["failure", "invalid-result", "report-failure"].includes(name)) assertUnavailable(result);
  else assert.equal(result.permissionDecision, "deny");
  assert.equal(calls, 1);
  if (["failure", "invalid-result"].includes(name)) {
    await hook(payload);
    assert.deepEqual(reports, [name === "invalid-result" ? "policy-response-invalid" : "policy-unavailable"]);
  }
}
console.log(JSON.stringify({ case: name, calls, reports }));
""",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(HOME=str(home), USERPROFILE=str(home))
    result = subprocess.run(
        [node, str(driver), str(module), str(scripts), str(flag), str(home), sys.executable, case],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=home, timeout=40,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    if result.returncode:
        print(result.stderr)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["case"] == case
    if case == "python-warning":
        assert "expected policy warning" in result.stderr
    elif case in {"python-error", "python-invalid-json", "python-invalid-shape", "python-unknown-response"}:
        assert "private policy diagnostic" not in result.stderr


@pytest.mark.parametrize("fault", [
    "missing-entry", "missing-policy", "broken-policy", "missing-runtime", "malformed-event", "none",
    "failed-component-deny", "failed-component-ask", "activation-report-failure", "missing-sibling-protected",
    "unsafe-sibling-protected",
])
def test_sdk_pretool_unavailable_recovery_preserves_confirmed_violation(tmp_path, fault):
    from agent_procutil import no_window_kwargs
    from test_hook_pre_relevance import _assert_pretool_recovery_outcome, _pretool_recovery_fixture

    node = shutil.which("node")
    assert node, "SDK recovery consumer requires Node"
    home, cwd, scripts, env = _pretool_recovery_fixture(tmp_path, fault)
    target = cwd / "efforts" / "active" / "fixture.md"
    module = PLUGIN / "extensions" / "agent-worktrees" / "resident-pre-tool.mjs"
    driver = tmp_path / "recovery.mjs"
    driver.write_text(
        """
import { pathToFileURL } from 'node:url';
const [modulePath, scriptsRoot, python, cwd, fault] = process.argv.slice(2);
const { createPythonPolicy, createResidentPreToolHook, selectResidentPreToolHooks } =
  await import(pathToFileURL(modulePath));
const environment = { ...process.env, AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION: 'resident' };
const hooks = selectResidentPreToolHooks({
  environment, report: () => {
    if (fault === 'activation-report-failure') throw new Error('fixture reporter failed');
  },
  createHook: () => {
    if (fault === 'activation-report-failure') throw new Error('fixture activation failed');
    return createResidentPreToolHook({
    scriptsRoot, environment, report: () => {},
    invokePolicy: createPythonPolicy({
      scriptsRoot, python: fault === 'missing-runtime' ? python + '.missing' : python, environment,
    }),
    });
  },
});
const protectedEvent = fault === 'none' || fault.endsWith('sibling-protected') || fault.startsWith('failed-component-');
const event = {
  toolName: protectedEvent ? 'create' : 'powershell',
  toolArgs: protectedEvent ? { path: cwd + '/efforts/active/fixture.md' } :
    { command: 'agent-worktrees doctor --json' },
  workingDirectory: cwd, sessionId: 'recovery-fixture', timestamp: new Date(),
};
const result = await hooks.onPreToolUse(fault === 'malformed-event' ? [] : event);
console.log(JSON.stringify(result));
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(driver), str(module), str(scripts), sys.executable, str(cwd), fault],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=home, timeout=35,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not target.exists()
    decision = json.loads(result.stdout)
    if fault.startswith("failed-component-"):
        assert decision.get("permissionDecision") == fault.rsplit("-", 1)[1], decision
        assert "anchor" in decision.get("permissionDecisionReason", "").lower(), decision
        assert "statelessness_guard.py evaluation failed" in decision.get("additionalContext", ""), decision
    elif fault in ("none", "missing-sibling-protected"):
        assert decision.get("permissionDecision") == "deny", decision
        assert "personal" in decision.get("permissionDecisionReason", "").lower(), decision
    else:
        _assert_pretool_recovery_outcome(decision)
        if fault == "activation-report-failure":
            assert "policy activation unavailable" in decision["additionalContext"]
            assert "diagnostic-failed" in decision["additionalContext"]

@pytest.mark.skipif(os.name != "nt", reason="Windows caller-owned native/SDK deadline consumers")
@pytest.mark.parametrize("consumer", ["resident", "powershell.exe"])
@pytest.mark.parametrize("late_result", ["timeout", "deny", "cleanup-error"])
def test_pretool_preserves_accumulated_ask_across_later_lookup(tmp_path, consumer, late_result, aliased=False):
    import time
    import uuid

    from agent_procutil import spawn_sync_in_kill_on_close_job
    from test_hook_policy_lifetime import _pin_descendant
    from test_hook_pre_relevance import _pretool_recovery_fixture

    home, cwd, scripts, env = _pretool_recovery_fixture(tmp_path, "none")
    (scripts.parent / "repos.yaml").write_text(
        "repos:\n  fixture:\n    class: worktree\n    windows: '" + str(cwd) + "'\n", encoding="utf-8",
    )
    env["ANCHOR_WRITE_GUARD_MODE"] = "ask"
    probes = tmp_path / "local-gh"
    probes.mkdir()
    observations = tmp_path / "observations"
    observations.mkdir()
    nonce = uuid.uuid4().hex
    child = probes / "gh_child.py"
    child.write_text(
        """
import ctypes, json, os, sys, threading
from pathlib import Path
from agent_procutil._daemon_instance_identity import _windows_birth
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.GetCurrentProcess.restype = ctypes.c_void_p
birth = _windows_birth(kernel.GetCurrentProcess())
args = sys.argv[1:]
root = Path(os.environ['PRETOOL_TEST_OBSERVATIONS'])
if args[:2] == ['pr', 'view']:
    number = args[2]
    marker = root / ('gh-' + number + '.json')
    prepared = marker.with_suffix('.prepared')
    prepared.write_text(json.dumps({'pid': os.getpid(), 'birth': birth, 'nonce': os.environ['PRETOOL_TEST_NONCE']}), encoding='utf-8')
    prepared.replace(marker)
    marker.with_suffix('.published').write_text(os.environ['PRETOOL_TEST_NONCE'], encoding='utf-8')
    if os.environ['PRETOOL_TEST_LATE_RESULT'] != 'deny':
        threading.Event().wait(11 if number == '41' else 60)
        state = 'CLOSED'
    else:
        state = 'OPEN'
    print(json.dumps({'author': {'login': 'fixture-contributor'}, 'state': state}))
elif args[:2] == ['api', 'user']:
    print('fixture-operator')
else:
    raise AssertionError('unexpected synthetic gh request')
""",
        encoding="utf-8",
    )
    (probes / "sitecustomize.py").write_text(
        """
import json, os, subprocess, sys, time
from pathlib import Path
from agent_procutil import owned_process
_real_run = owned_process.run_caller_owned
_retained = []
_root = Path(os.environ['PRETOOL_TEST_OBSERVATIONS'])
def emit(value):
    with (_root / 'events.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value) + '\\n')
def local_gh(args, *positional, **kwargs):
    if isinstance(args, (list, tuple)) and args and str(args[0]).lower() == 'gh':
        budget = kwargs.get('budget')
        emit({'event': 'gh-call', 'args': list(args[1:]), 'timeout': kwargs.get('timeout'),
              'deadline': budget.deadline, 'cleanup_reserve': budget.cleanup_reserve,
              'time': time.monotonic()})
        if os.environ['PRETOOL_TEST_LATE_RESULT'] == 'cleanup-error' and args[1:3] == ['pr', 'view'] and args[3] == '42':
            from agent_procutil import spawn_sync_in_kill_on_close_job
            process, job = spawn_sync_in_kill_on_close_job(
                [sys.executable, os.environ['PRETOOL_TEST_GH_CHILD'], *args[1:]],
                require_job=True, allow_breakaway=False, env=kwargs['env'], cwd=kwargs['cwd'],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            _retained.append((process, job))
            raise owned_process.OwnedCleanupError(process, job)
        return _real_run([sys.executable, os.environ['PRETOOL_TEST_GH_CHILD'], *args[1:]], *positional, **kwargs)
    return _real_run(args, *positional, **kwargs)
owned_process.run_caller_owned = local_gh
def observe(frame, event, result):
    if event == 'return' and frame.f_code.co_name == '_merge_pre_decisions' and frame.f_code.co_filename.endswith('hook_client.py'):
        if isinstance(result, dict) and result.get('permissionDecision') in ('ask', 'deny'):
            emit({'event': 'accumulated-decision', 'decision': result['permissionDecision'], 'time': time.monotonic()})
    elif event == 'call' and frame.f_code.co_name == 'decide' and frame.f_code.co_filename.endswith('pr_supersede_guard.py'):
        emit({'event': 'pr-guard-entry', 'deadline': frame.f_locals.get('deadline'), 'time': time.monotonic()})
sys.setprofile(observe)
""",
        encoding="utf-8",
    )
    env.update(
        PRETOOL_TEST_OBSERVATIONS=str(observations), PRETOOL_TEST_NONCE=nonce,
        PRETOOL_TEST_GH_CHILD=str(child), PRETOOL_TEST_LATE_RESULT=late_result,
        PYTHONPATH=str(probes) + os.pathsep + env.get("PYTHONPATH", ""),
    )
    no_backend = tmp_path / "no-backend"
    no_backend.mkdir()
    node = shutil.which("node")
    shell = shutil.which("powershell.exe")
    assert node and shell
    env["PATH"] = str(no_backend)
    target = cwd / "protected.txt"
    event = {
        "toolName": "powershell", "cwd": str(cwd), "workingDirectory": str(cwd),
        "sessionId": "ask-deadline-fixture",
        "toolArgs": {"command": "Set-Content '" + str(target) + "' value; "
                     "gh pr close 41 --repo example/project; gh pr close 42 --repo example/project"},
    }
    if aliased:
        event = {"tool_name": "powershell", "cwd": str(cwd),
                 "tool_input": json.dumps({"script": event["toolArgs"]["command"]})}
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    if consumer == "resident":
        driver = tmp_path / "ask-deadline.mjs"
        driver.write_text(
            "import {readFileSync} from 'node:fs'; import {pathToFileURL} from 'node:url';\n"
            "const [modulePath,scriptsRoot,python,eventPath]=process.argv.slice(2);\n"
            "const {createPythonPolicy,createResidentPreToolHook}=await import(pathToFileURL(modulePath));\n"
            "const hook=createResidentPreToolHook({scriptsRoot,invokePolicy:createPythonPolicy({scriptsRoot,python})});\n"
            "console.log(JSON.stringify(await hook(JSON.parse(readFileSync(eventPath,'utf8')))));\n",
            encoding="utf-8",
        )
        command = [node, str(driver), str(PLUGIN / "extensions" / "agent-worktrees" / "resident-pre-tool.mjs"),
                   str(scripts), sys.executable, str(event_path)]
    elif consumer == "legacy":
        command = [sys.executable, str(scripts / "hook_client.py"), "preToolUse"]
    else:
        command = [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(scripts / "pre-tool-hook.ps1")]
    started = time.monotonic()
    process, job = spawn_sync_in_kill_on_close_job(
        command, require_job=True, allow_breakaway=False, env=env, cwd=home,
        stdin=subprocess.DEVNULL if consumer == "resident" else subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    close_observer = None
    try:
        if consumer != "resident":
            process.stdin.write(json.dumps(event).encode("utf-8"))
            process.stdin.close()
            process.stdin = None
        if late_result != "deny":
            marker = observations / "gh-42.json"
            deadline = time.monotonic() + 15
            while not marker.with_suffix(".published").is_file():
                assert process.poll() is None, process.communicate(timeout=5)
                assert time.monotonic() < deadline, "second local lookup never started"
                time.sleep(0.02)
            record = json.loads(marker.read_text(encoding="utf-8"))
            assert record["nonce"] == nonce
            retired, close_observer = _pin_descendant(record["pid"], record["birth"])
        stdout, stderr = process.communicate(timeout=40)
        assert process.returncode == 0, (stdout, stderr)
        assert time.monotonic() - started < 30, "consumer exceeded the original hook deadline"
        if late_result != "deny":
            assert retired(), "local lookup child survived its owned policy scope"
        observed = [json.loads(line) for line in (observations / "events.jsonl").read_text().splitlines()]
        decisions = [row["decision"] for row in observed if row["event"] == "accumulated-decision"]
        assert decisions and decisions[0] == "ask", observed
        calls = [row for row in observed if row["event"] == "gh-call"]
        assert len(calls) == 2, calls
        assert calls[0]["deadline"] == calls[1]["deadline"]
        assert all(row["cleanup_reserve"] == 3.0 for row in calls)
        if late_result != "deny":
            assert calls[1]["timeout"] < calls[0]["timeout"]
        reply = json.loads(stdout)
        print(json.dumps({"consumer": consumer, "late_result": late_result, "observed": observed, "reply": reply}))
        assert not target.exists()
        assert reply.get("permissionDecision") == ("deny" if late_result == "deny" else "ask"), reply
        if late_result == "deny":
            assert decisions[-1] == "deny"
            assert "contributor" in reply.get("permissionDecisionReason", "")
    finally:
        if close_observer is not None:
            close_observer()
        job.terminate_and_wait(5)
        process.wait(timeout=5)


def test_missing_sdk_does_not_suppress_intact_stateless_guard(monkeypatch, tmp_path):
    import builtins
    import importlib.util
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location("fixture_pre_policy", PLUGIN / "scripts" / "hook_pre_policy.py")
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    original = builtins.__import__

    def missing_sdk(name, *args, **kwargs):
        if name.startswith("agent_procutil"):
            raise ModuleNotFoundError("fixture missing SDK")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_sdk)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    result = policy.decide(
        {}, home=tmp_path, budget_seconds=25,
        load_sibling=lambda name: SimpleNamespace(decide=lambda *a, **k: {
            "permissionDecision": "deny", "permissionDecisionReason": "intact stateless guard",
        }),
        merge_decisions=lambda left, right: {**left, **right},
    )
    assert result["permissionDecision"] == "deny"


@pytest.mark.skipif(os.name != "nt", reason="native Windows legacy command consumer")
def test_legacy_aliased_json_keeps_ask_after_cleanup_error(tmp_path):
    test_pretool_preserves_accumulated_ask_across_later_lookup(
        tmp_path, "legacy", "cleanup-error", aliased=True,
    )


@pytest.mark.parametrize("permission", ["deny", "ask"])
@pytest.mark.parametrize("admission", ["native", "initialization"])
def test_legacy_main_keeps_cheap_guard_when_sdk_native_admission_unavailable(monkeypatch, tmp_path, capsys, permission, admission):
    import importlib.util
    import io
    from types import SimpleNamespace
    from agent_procutil import owned_process

    modules = {}
    for filename in ("hook_client", "hook_pre_policy", "hook_policy_process", "pr_supersede_guard"):
        spec = importlib.util.spec_from_file_location(filename, PLUGIN / "scripts" / (filename + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules[filename + ".py"] = module
        monkeypatch.setitem(sys.modules, filename, module)
    client = modules["hook_client.py"]
    modules.update({
        "pretool_killswitch.py": SimpleNamespace(is_active=lambda: False),
        "statelessness_guard.py": SimpleNamespace(decide=lambda *a, **k: {
            "permissionDecision": permission, "permissionDecisionReason": "confirmed cheap guard",
        }),
        "cross_repo_guard.py": SimpleNamespace(decide=lambda *a, **k: None),
        "anchor_write_guard.py": SimpleNamespace(decide=lambda *a, **k: None),
    })
    monkeypatch.setattr(client, "_load_sibling", lambda filename: modules.get(filename))
    def unavailable():
        raise RuntimeError("fixture unavailable native observation")
    monkeypatch.setattr(owned_process, "require_caller_owned_support", unavailable)
    if admission == "initialization":
        import builtins
        original = builtins.__import__
        def fail_initialization(name, *args, **kwargs):
            if name == "agent_procutil.owned_budget":
                raise RuntimeError("fixture ordinary SDK initialization failure")
            return original(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", fail_initialization)
    monkeypatch.delenv("AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "toolName": "powershell", "cwd": str(tmp_path),
        "toolArgs": {"command": "gh pr close 41 --repo example/project"},
    })))
    assert client.main(["preToolUse"]) == 0
    assert json.loads(capsys.readouterr().out)["permissionDecision"] == permission


@pytest.mark.parametrize("fault", ["prepare", "timeout-garbage", "timeout-ask", "timeout-deny", "nonzero", "empty", "context"])
def test_owned_legacy_retains_initial_after_preparation_or_settled_failure(monkeypatch, capsys, fault):
    import importlib.util
    from agent_procutil import owned_process
    import agent_procutil

    loaded = {}
    for name in ("hook_client", "hook_pre_policy", "hook_policy_process"):
        spec = importlib.util.spec_from_file_location(name, PLUGIN / "scripts" / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        monkeypatch.setitem(sys.modules, name, module)
        loaded[name] = module
    monkeypatch.setattr(owned_process, "require_caller_owned_support", lambda: None)
    if fault == "prepare":
        def refuse(python):
            raise RuntimeError("fixture interpreter refused before launch")
        monkeypatch.setattr(agent_procutil, "direct_python", refuse)

    def invoke(argv, **kwargs):
        assert fault != "prepare", "preparation refusal must not launch"
        kwargs["on_settled"]()
        if fault == "nonzero":
            return subprocess.CompletedProcess(argv, 7, "unusable output", "")
        if fault in ("empty", "context"):
            return subprocess.CompletedProcess(argv, 0, "" if fault == "empty" else '{"additionalContext":"unavailable"}', "")
        output = b"unusable output" if fault == "timeout-garbage" else json.dumps({
            "permissionDecision": "deny" if fault == "timeout-deny" else "ask",
            "permissionDecisionReason": "confirmed worker",
        }).encode()
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output=output)

    monkeypatch.setattr(owned_process, "run_caller_owned", invoke)
    result = loaded["hook_policy_process"].execute(
        b"{}", legacy=True, initial_decision={
            "permissionDecision": "ask", "permissionDecisionReason": "confirmed parent",
        },
    )
    assert result == 0
    decision = json.loads(capsys.readouterr().out)
    assert decision["permissionDecision"] == ("deny" if fault == "timeout-deny" else "ask")


@pytest.mark.parametrize("fault", ["missing", "invalid"])
def test_deferred_context_refusal_preserves_initial_decision(monkeypatch, tmp_path, fault):
    import importlib.util
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location("fixture_policy", PLUGIN / "scripts" / "hook_pre_policy.py")
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "fixture-context")
    def refuse(**kwargs):
        raise RuntimeError("fixture invalid context")
    result = policy.decide(
        {}, home=tmp_path, budget_seconds=25, only_observations=True,
        initial_decision={"permissionDecision": "ask", "permissionDecisionReason": "confirmed parent"},
        load_sibling=lambda name: None if fault == "missing" else SimpleNamespace(resolve_registry_root=refuse),
        merge_decisions=lambda left, right: {**left, **right},
    )
    assert result["permissionDecision"] == "ask"
    assert "unavailable" in result["additionalContext"] or "failed" in result["additionalContext"]


@pytest.mark.parametrize("initial", [{}, {"permissionDecision": "ask", "permissionDecisionReason": "confirmed parent"}])
def test_successful_deferred_legacy_preserves_silent_noop_and_diagnostics(monkeypatch, capsys, initial):
    import importlib.util
    from agent_procutil import owned_process

    loaded = {}
    for name in ("hook_client", "hook_pre_policy", "hook_policy_process"):
        spec = importlib.util.spec_from_file_location(name, PLUGIN / "scripts" / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        monkeypatch.setitem(sys.modules, name, module)
        loaded[name] = module
    monkeypatch.setattr(owned_process, "require_caller_owned_support", lambda: None)

    def invoke(argv, **kwargs):
        kwargs["on_settled"]()
        return subprocess.CompletedProcess(argv, 0, "{}", "fixture observation warning\n")

    monkeypatch.setattr(owned_process, "run_caller_owned", invoke)
    assert loaded["hook_policy_process"].execute(
        b"{}", legacy=True, initial_decision=initial,
    ) == 0
    captured = capsys.readouterr()
    assert captured.err == "fixture observation warning\n"
    if initial:
        assert json.loads(captured.out) == initial
    else:
        assert captured.out == ""
