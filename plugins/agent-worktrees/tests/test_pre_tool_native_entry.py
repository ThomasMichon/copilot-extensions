"""Opt-in native pre-entry contract; the legacy Python entry remains supported."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
pytestmark = pytest.mark.skipif(
    os.name != "nt" or not os.environ.get("COPILOT_TEST_HOOK_PRE_PYTHON"),
    reason="explicit contained Windows hook-entry fixture",
)


@pytest.mark.parametrize("case", ["read", "guard-drift", "missing-contract", "write", "context", "active", "invalid-flag", "future-flag-contract", "array-flag", "array-contract-id", "empty-contract-id", "drive-relative-flag", "root-relative-flag", "array-contract", "non-utf8-environment", "old-client", "empty-policy", "array-response", "invalid-decision-response", "invalid-context-response", "policy-error", "malformed-response", "case-flag", "case-payload", "keys-shadow-response"])
@pytest.mark.parametrize("native_shell", ["powershell.exe", "pwsh"])
def test_native_entry_keeps_uncertain_input_on_python(case, native_shell, tmp_path):
    shell = shutil.which(native_shell)
    if shell is None:
        pytest.skip(f"{native_shell} is unavailable")
    interpreter = Path(os.environ["COPILOT_TEST_HOOK_PRE_PYTHON"])
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in (
        "pre-tool-hook.ps1", "hook-read-policy.json", "versioned_runtime_native.ps1", "hook_policy_process.py",
        "statelessness_guard.py", "cross_repo_guard.py", "anchor_write_guard.py", "pr_supersede_guard.py",
    ):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    observed = tmp_path / "python-ran"
    (scripts / "hook_client.py").write_text(
        "import json,sys\nfrom pathlib import Path\n"
        + "Path(" + repr(str(observed)) + ").write_text(sys.stdin.read())\n"
        + "print(json.dumps({'permissionDecision':'deny','permissionDecisionReason':'fixture Python path'}))\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    flag = tmp_path / "pretool.json"
    env = os.environ.copy()
    for name in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_RT_ROOT", "PYTHONUTF8", "PYTHONIOENCODING"):
        env.pop(name, None)
    env.update(HOME=str(home), USERPROFILE=str(home),
               PRETOOL_KILLSWITCH_STATE_FILE=str(flag),
               PATH=str(interpreter.parent) + os.pathsep + env.get("PATH", ""))
    if case == "guard-drift":
        with (scripts / "anchor_write_guard.py").open("a", encoding="utf-8") as output:
            output.write("\n# changed guard implementation\n")
    elif case == "missing-contract":
        (scripts / "hook-read-policy.json").unlink()
    elif case == "context":
        env["COPILOT_EXTENSIONS_CONTEXT"] = str(tmp_path / "context.json")
    elif case == "active":
        flag.write_text('{"active":true}', encoding="utf-8")
    elif case == "case-flag":
        flag.write_text('{"Active":true}', encoding="utf-8")
    elif case == "invalid-flag":
        flag.write_text('{"active":"true"}', encoding="utf-8")
    elif case == "future-flag-contract":
        flag.write_text('{"active":true,"contract":{"id":"copilot-extensions.pretool-killswitch","version":2}}', encoding="utf-8")
    if case == "array-flag":
        flag.write_text('[{"active":true}]', encoding="utf-8")
    elif case in {"array-contract-id", "empty-contract-id"}:
        flag.write_text(json.dumps({"active": True, "contract": {
            "id": ["copilot-extensions.pretool-killswitch"] if case == "array-contract-id" else [],
            "version": 1,
        }}), encoding="utf-8")
    elif case in {"drive-relative-flag", "root-relative-flag"}:
        flag = home / "pretool.json"
        flag.write_text('{"active":true}', encoding="utf-8")
        env["PRETOOL_KILLSWITCH_STATE_FILE"] = (
            home.drive + flag.name if case == "drive-relative-flag"
            else str(flag)[len(home.drive):]
        )
    elif case == "array-contract":
        contract = scripts / "hook-read-policy.json"
        contract.write_text("[" + contract.read_text(encoding="utf-8") + "]", encoding="utf-8")
    elif case == "non-utf8-environment":
        env["PYTHONIOENCODING"] = "cp1252"
    if case in {
        "old-client", "empty-policy", "array-response", "invalid-decision-response",
        "invalid-context-response", "policy-error", "malformed-response", "keys-shadow-response",
    }:
        action = {
            "old-client": "if sys.argv[1] == 'preToolUse': print('{}')\n",
            "empty-policy": "",
            "array-response": "import json\nprint(json.dumps([{'permissionDecision':'allow'}]))\n",
            "invalid-decision-response": "import json\nprint(json.dumps({'permissionDecision':'invalid'}))\n",
            "invalid-context-response": "import json\nprint(json.dumps({'additionalContext':[]}))\n",
            "policy-error": "print('private native diagnostic',file=sys.stderr)\nraise SystemExit(3)\n",
            "malformed-response": "print('{private-native-response-text')\n",
            "keys-shadow-response": "import json\nprint(json.dumps({'Keys': [], 'unexpected': True}))\n",
        }[case]
        (scripts / "hook_client.py").write_text(
            "import sys\nfrom pathlib import Path\n"
            + "Path(" + repr(str(observed)) + ").write_text(sys.stdin.read())\n" + action,
            encoding="utf-8",
        )
    payload = {"toolName": "view" if case in {
        "read", "guard-drift", "missing-contract", "context", "active", "invalid-flag",
        "future-flag-contract", "array-contract-id", "empty-contract-id", "drive-relative-flag", "root-relative-flag", "array-contract",
    } else "edit", "toolArgs": {"path": "non-ascii-\u00e9.txt"}}
    if case == "case-payload":
        payload["ToolName"] = "view"
    original = json.dumps(payload, ensure_ascii=False, indent=2)
    command = "& '" + str(scripts / "pre-tool-hook.ps1").replace("'", "''") + "'"
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        input=original, capture_output=True, text=True, encoding="utf-8", env=env,
        cwd=home, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    if case == "policy-error":
        assert "private native diagnostic" not in result.stderr
    if case == "malformed-response":
        assert "private-native-response-text" not in result.stderr
        assert "private-native-response-text" not in result.stdout
    python_required = case not in {"read", "active", "context"}
    assert observed.exists() is python_required, (result.stdout, result.stderr)
    if python_required:
        assert json.loads(observed.read_text(encoding="utf-8")) == payload
        assert json.loads(result.stdout)["permissionDecision"] == "deny"
    elif case == "context":
        assert json.loads(result.stdout)["permissionDecision"] == "deny"
    else:
        assert result.stdout == ""


@pytest.mark.parametrize("native_shell", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("invalid_context", [False, True])
def test_native_entry_selects_validated_cell_runtime(native_shell, invalid_context, tmp_path):
    import sysconfig
    import agent_procutil

    shell = shutil.which(native_shell)
    if shell is None:
        pytest.skip(f"{native_shell} is unavailable")
    interpreter = Path(os.environ["COPILOT_TEST_HOOK_PRE_PYTHON"])
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for name in ("pre-tool-hook.ps1", "hook_policy_process.py", "resolve-runtime.ps1"):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    shutil.copytree(SCRIPTS / "installation-context", scripts / "installation-context")
    shutil.copyfile(SCRIPTS.parent / "plugin.json", payload / "plugin.json")
    observed = tmp_path / "policy-ran"
    (scripts / "hook_client.py").write_text(
        "import json,sys\nfrom pathlib import Path\n"
        + "Path(" + repr(str(observed)) + ").write_text(sys.executable)\n"
        + "print(json.dumps({'additionalContext':'validated cell policy'}))\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    durable = tmp_path / "durable"
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW}
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
         str(scripts / "installation-context" / "installation-context.ps1"),
         "-Action", "stamp", "-SourceJson",
         json.dumps({"source": "github", "repo": "example/marketplace"}),
         "-MarketplaceKey", "fixture", "-PluginId", "agent-worktrees",
         "-PayloadRoot", str(payload), "-PayloadVersion", "1.0.0",
         "-PayloadOrigin", "explicit", "-ExpectedNamespaceGeneration", "0",
         "-ExpectedInstallGeneration", "0", "-DurableHome", str(durable)],
        capture_output=True, text=True, encoding="utf-8", timeout=30, **flags,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    context = json.loads(result.stdout)
    root = Path(context["pluginRoot"])
    runtime = root / "versions" / "1.0.0"
    ambient = tmp_path / "ambient"
    for target in (runtime, ambient):
        created = subprocess.run(
            [str(interpreter), "-m", "venv", "--without-pip", str(target)],
            capture_output=True, text=True, timeout=30, **flags,
        )
        assert created.returncode == 0, (created.stdout, created.stderr)
    site = runtime / "Lib" / "site-packages"
    (site / "fixture-runtime.pth").write_text(
        sysconfig.get_path("purelib") + "\n" + str(Path(agent_procutil.__file__).parents[1]) + "\n",
        encoding="utf-8",
    )
    (runtime / ".install-complete.json").write_text(json.dumps({
        "version": "1.0.0", "completed_at": "2026-01-01T00:00:00Z", "pid": 1,
    }), encoding="utf-8")
    (root / "current-version").write_text("1.0.0\n", encoding="utf-8")
    pointer = Path(context["installReceipt"])
    if invalid_context:
        receipt = json.loads(pointer.read_text(encoding="utf-8"))
        receipt["pluginId"] = "foreign-plugin"
        pointer.write_text(json.dumps(receipt), encoding="utf-8")
    env = os.environ.copy()
    for name in ("AGENT_RT_ROOT", "AGENT_WORKTREES_PAYLOAD_ROOT", "PYTHONPATH"):
        env.pop(name, None)
    env.update(HOME=str(home), USERPROFILE=str(home), COPILOT_PLUGIN_ROOT=str(payload),
               COPILOT_EXTENSIONS_CONTEXT=str(pointer),
               PATH=str(ambient / "Scripts") + os.pathsep + env.get("PATH", ""))
    available = subprocess.run(
        [str(ambient / "Scripts" / "python.exe"), "-c", "import agent_procutil"],
        capture_output=True, text=True, env=env, timeout=10, **flags,
    )
    assert available.returncode != 0, "ambient Python must lack the owned worker dependency"
    available = subprocess.run(
        [str(runtime / "Scripts" / "python.exe"), "-c",
         "from agent_procutil.owned_process import require_caller_owned_support; require_caller_owned_support()"],
        capture_output=True, text=True, env=env, timeout=10, **flags,
    )
    assert available.returncode == 0, (available.stdout, available.stderr)
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(scripts / "pre-tool-hook.ps1")],
        input=json.dumps({"toolName": "view", "toolArgs": {"path": "fixture.txt"}}),
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=home, timeout=25, **flags,
    )
    assert result.returncode == 0, result.stderr
    reply = json.loads(result.stdout)
    if invalid_context:
        assert reply["permissionDecision"] == "deny"
        assert not observed.exists()
    else:
        assert reply == {"additionalContext": "validated cell policy"}, result.stderr
        assert Path(observed.read_text()).is_relative_to(runtime)
