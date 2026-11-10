"""Actual binding payload -> native parser -> owned synthetic provider process."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from agent_procutil import no_window_kwargs
import pytest

from agent_worktrees import repos


@pytest.mark.parametrize("mode", ["direct", "pull-request", "legacy", "timeout", "forbidden"])
def test_real_shared_binding_read_has_no_registration_or_credential_effects(
    tmp_path, monkeypatch, mode,
):
    if os.name == "nt" and not (shutil.which("pwsh.exe") or shutil.which("powershell.exe")):
        pytest.skip("The native binding payload needs its supported PowerShell host.")
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("PREFLIGHT_FIXTURE_MODE", mode)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    checkout = tmp_path / "knowledge"
    checkout.mkdir()
    registry = repos._repos_yaml_path()
    registry.parent.mkdir(parents=True)
    registry.write_text(
        "repos:\n  knowledge:\n    class: knowledge\n"
        "    remote: https://github.com/example/knowledge.git\n"
        "    account: selected-user\n"
        f"    {repos._current_platform()}: {json.dumps(str(checkout))}\n",
        encoding="utf-8",
    )
    sentinel = tmp_path / "credential-sentinel"
    sentinel.write_text("unchanged", encoding="utf-8")
    provider = tmp_path / "synthetic_provider.py"
    provider.write_text(
        "import json, os, sys, time\n"
        "mode = os.environ['PREFLIGHT_FIXTURE_MODE']\n"
        "if mode == 'legacy': raise RuntimeError('unexpected credential read')\n"
        "if mode == 'timeout': time.sleep(.4)\n"
        "if sys.argv[1] == 'auth':\n"
        "    print('synthetic-private-token')\n"
        "    raise SystemExit(0)\n"
        "if 'graphql' in sys.argv:\n"
        "    value = {'data': {'repository': {\n"
        "        'id': 'repository-id', 'nameWithOwner': 'example/knowledge',\n"
        "        'isArchived': False, 'isDisabled': False, 'viewerPermission': 'WRITE',\n"
        "        'defaultBranchRef': {'name': 'main', 'target': {'oid': 'a' * 40},\n"
        "            'branchProtectionRule': None}}}}\n"
        "    if mode == 'forbidden':\n"
        "        value = {'errors': [{'type': 'FORBIDDEN', 'message': 'private-policy-detail'}]}\n"
        "elif sys.argv[-1].endswith('/user'):\n"
        "    value = {'login': 'selected-user'}\n"
        "else:\n"
        "    value = [{'type': 'pull_request'}] if mode == 'pull-request' else []\n"
        "print('HTTP/2.0 200 OK\\r\\n\\r\\n' + json.dumps(value))\n",
        encoding="utf-8",
    )
    with provider.open("a", encoding="utf-8") as stream:
        stream.write(
            "if mode == 'forbidden' and 'graphql' in sys.argv:\n"
            "    print('private-provider-error', file=sys.stderr)\n"
            "    raise SystemExit(1)\n"
        )
    driver = tmp_path / "native_owner.py"
    driver.write_text(
        "import os, runpy, shutil, sys\n"
        "from agent_worktrees import github_account_observation as provider\n"
        "from agent_procutil.owned_process import run_caller_owned\n"
        "from agent_procutil.owned_budget import OwnedExecutionBudget\n"
        "which = shutil.which\n"
        "shutil.which = lambda name: sys.executable if name == 'gh' else which(name)\n"
        "def run(argv, **kwargs):\n"
        f"    return run_caller_owned([sys.executable, '-B', {str(provider)!r}, *argv[1:]], **kwargs)\n"
        "provider.run_caller_owned = run\n"
        "if os.environ['PREFLIGHT_FIXTURE_MODE'] == 'timeout':\n"
        "    budget = OwnedExecutionBudget.from_timeout\n"
        "    OwnedExecutionBudget.from_timeout = lambda *a, **k: budget(1.6, cleanup_reserve=.4)\n"
        "if os.environ['PREFLIGHT_FIXTURE_MODE'] == 'legacy':\n"
        "    from agent_worktrees import repos_list_cli\n"
        "    output = repos_list_cli.output._json_output\n"
        "    def legacy(value):\n"
        "        value.pop('capabilities', None)\n"
        "        return output(value)\n"
        "    repos_list_cli.output._json_output = legacy\n"
        "runpy.run_module('agent_worktrees', run_name='__main__')\n",
        encoding="utf-8",
    )
    owner = tmp_path / ("native_owner.ps1" if os.name == "nt" else "native-owner")
    if os.name == "nt":
        owner.write_text(
            "& '" + sys.executable.replace("'", "''") + "' -B '"
            + str(driver).replace("'", "''") + "' @args\nexit $LASTEXITCODE\n",
            encoding="utf-8",
        )
    else:
        import shlex

        owner.write_text(
            "#!/bin/sh\nexec " + shlex.join([sys.executable, "-B", str(driver)]) + ' "$@"\n',
            encoding="utf-8",
        )
        owner.chmod(0o700)
    payload = Path(__file__).resolve().parents[4] / (
        "plugins/harness-knowledge/skills/binding-knowledge/scripts/bind_knowledge.py"
    )
    before = {str(path.relative_to(tmp_path)): path.read_bytes()
              for path in tmp_path.rglob("*") if path.is_file()}
    completed = subprocess.run(
        [sys.executable, "-B", str(payload), "--preflight", "--knowledge", "knowledge",
         "--agent-worktrees-path", str(owner), "--json"],
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout)
    if mode == "legacy":
        assert value["status"] == "unsupported"
    elif mode == "timeout":
        assert value["status"] == "partial"
        assert value["failure"]["code"] == "timeout"
        assert value["landing"]["mode"] == "unknown"
    elif mode == "forbidden":
        assert value["status"] == "partial"
        assert value["account"]["status"] == "verified"
        assert value["landing"]["status"] == "insufficient-permission"
        assert value["landing"]["mode"] == "unknown"
    else:
        assert value["status"] == "observed"
        assert value["landing"]["mode"] == mode
        assert value["account"]["status"] == "verified"
    assert "synthetic-private-token" not in completed.stdout + completed.stderr
    assert "private-policy-detail" not in completed.stdout + completed.stderr
    assert "private-provider-error" not in completed.stdout + completed.stderr
    after = {str(path.relative_to(tmp_path)): path.read_bytes()
             for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before
