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


@pytest.mark.parametrize("case", ["read", "guard-drift", "missing-contract", "write", "context", "active", "invalid-flag", "future-flag-contract", "array-flag", "drive-relative-flag", "root-relative-flag", "array-contract", "non-utf8-environment", "old-client", "empty-policy", "array-response", "invalid-decision-response", "invalid-context-response", "policy-error"])
@pytest.mark.parametrize("native_shell", ["powershell.exe", "pwsh"])
def test_native_entry_keeps_uncertain_input_on_python(case, native_shell, tmp_path):
    shell = shutil.which(native_shell)
    if shell is None:
        pytest.skip(f"{native_shell} is unavailable")
    interpreter = Path(os.environ["COPILOT_TEST_HOOK_PRE_PYTHON"])
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in (
        "pre-tool-hook.ps1", "hook-read-policy.json", "versioned_runtime_native.ps1",
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
    elif case == "invalid-flag":
        flag.write_text('{"active":"true"}', encoding="utf-8")
    elif case == "future-flag-contract":
        flag.write_text('{"active":true,"contract":{"id":"copilot-extensions.pretool-killswitch","version":2}}', encoding="utf-8")
    if case == "array-flag":
        flag.write_text('[{"active":true}]', encoding="utf-8")
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
        "invalid-context-response", "policy-error",
    }:
        action = {
            "old-client": "if sys.argv[1] == 'preToolUse': print('{}')\n",
            "empty-policy": "",
            "array-response": "import json\nprint(json.dumps([{'permissionDecision':'allow'}]))\n",
            "invalid-decision-response": "import json\nprint(json.dumps({'permissionDecision':'invalid'}))\n",
            "invalid-context-response": "import json\nprint(json.dumps({'additionalContext':[]}))\n",
            "policy-error": "print('private native diagnostic',file=sys.stderr)\nraise SystemExit(3)\n",
        }[case]
        (scripts / "hook_client.py").write_text(
            "import sys\nfrom pathlib import Path\n"
            + "Path(" + repr(str(observed)) + ").write_text(sys.stdin.read())\n" + action,
            encoding="utf-8",
        )
    payload = {"toolName": "view" if case in {
        "read", "guard-drift", "missing-contract", "context", "active", "invalid-flag",
        "future-flag-contract", "drive-relative-flag", "root-relative-flag", "array-contract",
    } else "edit", "toolArgs": {"path": "non-ascii-\u00e9.txt"}}
    original = json.dumps(payload, ensure_ascii=False)
    command = "& '" + str(scripts / "pre-tool-hook.ps1").replace("'", "''") + "'"
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        input=original, capture_output=True, text=True, encoding="utf-8", env=env,
        cwd=home, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    if case == "policy-error":
        assert "private native diagnostic" not in result.stderr
    python_required = case not in {"read", "active"}
    assert observed.exists() is python_required, (result.stdout, result.stderr)
    if python_required:
        assert json.loads(observed.read_text(encoding="utf-8")) == payload
        assert json.loads(result.stdout)["permissionDecision"] == "deny"
    else:
        assert result.stdout == ""
