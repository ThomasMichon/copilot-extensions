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
  assert.equal((await hook({ ...input, workingDirectory: undefined })).permissionDecision, "deny");
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
    assert.equal(decision.permissionDecision, "deny");
    assert.match(decision.permissionDecisionReason, /policy helper is unavailable/);
  }
  const actual = make(createPythonPolicy({ scriptsRoot: root, python, environment: env }));
  assert.equal((await actual(payload)).permissionDecision, "deny");
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
  assert.equal(result.permissionDecision, "deny");
  assert.equal(reports.length, 0, "must be an actual policy denial, not adapter failure");
  assert.match(result.permissionDecisionReason,
    name === "real-protected-write" ? /stateless|personal/i :
    name === "real-invalid-context" ? /context/i : /required.*policy/i);
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
  assert.equal(result.permissionDecision, "deny");
  assert.equal(result.permissionDecisionReason.includes("real Python policy"), ["python-policy", "python-encoding", "python-warning"].includes(name));
  assert.equal(reports.length, ["python-policy", "python-encoding", "python-warning"].includes(name) ? 0 : 1);
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
  assert.equal((await hook(payload)).permissionDecision, "deny");
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
