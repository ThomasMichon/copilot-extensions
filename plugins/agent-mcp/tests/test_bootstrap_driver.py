"""MCP bootstrap hands fixed actions to the packaged receiver, never an installer."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs


PLUGIN = Path(__file__).resolve().parents[1]
REPOSITORY = PLUGIN.parents[1]
NATIVE = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux", reason="native Windows/Linux bootstrap",
)


def bootstrap_fixture(tmp_path, *, phase="cold", condition="ordinary"):
    home, project = tmp_path / "home-caf\u00e9", tmp_path / "project-\u6d4b\u8bd5"
    home.mkdir()
    project.mkdir()
    payload = home / ".copilot/installed-plugins/fixture-market/agent-mcp"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    suffix = "ps1" if os.name == "nt" else "sh"
    hook = scripts / f"bootstrap-check.{suffix}"
    shutil.copyfile(PLUGIN / "scripts" / hook.name, hook)
    (payload / "plugin.json").write_text(
        json.dumps({"name": "agent-mcp", "version": "1.0.0"}), encoding="utf-8",
    )
    (payload / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
    query = scripts / "installation-context/json-query.awk"
    query.parent.mkdir()
    shutil.copyfile(REPOSITORY / "libs/installation-context/json-query.awk", query)
    driver = payload / "libs/update-driver/scripts" / f"first-use.{suffix}"
    driver.parent.mkdir(parents=True)
    if os.name == "nt":
        driver.write_text(
            "# Installation driver lifecycle contract: mcp-recipes-v1\n"
            "param($Root,$Payload,$RuntimeRoot,$Project,$InstallerAction)\n"
            "[IO.File]::WriteAllLines((Join-Path $Project 'handoff'), "
            "[string[]]@($Root,$Payload,$RuntimeRoot,$Project,$InstallerAction))\n"
            "if ($env:MCP_TEST_CONDITION -ne 'no-ack') {\n"
            "$ackRoot = if ($env:MCP_TEST_CONDITION -eq 'foreign-ack') { $Project } else { $Root }\n"
            "[Console]::Out.WriteLine((@{launched=$true;pid=123;created=1;"
            "log=(Join-Path $ackRoot 'first-use-00000000000000000000000000000000.log')} "
            "| ConvertTo-Json -Compress))\n"
            "}\nif ($env:MCP_TEST_CONDITION -eq 'driver-refused') { exit 37 }\nexit 0\n",
            encoding="utf-8",
        )
    else:
        driver.write_text(
            "# Installation driver lifecycle contract: mcp-recipes-v1\n"
            'printf "%s\\n" "$1" "$2" "$3" "$4" "$6" >"$4/handoff"\n'
            'if [[ "$MCP_TEST_CONDITION" != no-ack ]]; then\n'
            'ack_root="$1"; [[ "$MCP_TEST_CONDITION" != foreign-ack ]] || ack_root="$4"\n'
            'printf "launched:123 log:%s/first-use-00000000-0000-0000-0000-000000000000.log\\n" "$ack_root"\n'
            'fi\n[[ "$MCP_TEST_CONDITION" != driver-refused ]] || exit 37\n',
            encoding="utf-8",
        )
    installation = home / ".agent-mcp"
    if phase == "drift":
        installation.mkdir()
        (installation / "deploy-manifest.json").write_text(json.dumps({
            "source": {"version": "0.0.0", "path": str(tmp_path / "foreign-payload")},
        }), encoding="utf-8")
        for name in ("reconcile.lock", "reconcile-status.json"):
            (installation / name).write_text("424242", encoding="utf-8")
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_MCP_", "AGENT_RT_", "COPILOT_EXTENSIONS_",
                               "COPILOT_PLUGIN_", "UPDATE_DRIVER_"))
    }
    env.update(HOME=str(home), USERPROFILE=str(home), MCP_TEST_CONDITION=condition,
               BOOTSTRAP_KILLSWITCH_STATE_FILE=str(home / "killswitch.json"))
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.pathsep.join(
        part for part in env.get("PATH", "").split(os.pathsep) if "WindowsApps" not in part
    )
    return payload, installation, project, driver, env


def run_hook(payload, project, env):
    if os.name == "nt":
        shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        command = [
            str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
            f"Set-Location -LiteralPath {quote(project)}; "
            f"[IO.Directory]::SetCurrentDirectory({quote(Path(env['HOME']))}); "
            f"& {quote(payload / 'scripts/bootstrap-check.ps1')}; exit $LASTEXITCODE",
        ]
    else:
        command = ["bash", str(payload / "scripts/bootstrap-check.sh")]
    return subprocess.run(
        command, env=env, cwd=project, capture_output=True, text=True,
        encoding="utf-8", timeout=15, **no_window_kwargs(),
    )


@NATIVE
@pytest.mark.parametrize("phase", ["cold", "drift"])
@pytest.mark.parametrize("condition", [
    "ordinary", "empty-no-self", "no-self", "pinned", "nested", "old-driver",
    "missing-driver", "no-ack", "foreign-ack", "driver-refused", "malformed-manifest",
])
def test_native_bootstrap_handoff_and_refusals(tmp_path, phase, condition):
    payload, installation, project, driver, env = bootstrap_fixture(
        tmp_path, phase=phase, condition=condition,
    )
    if condition in {"empty-no-self", "no-self", "pinned", "nested"}:
        key = {
            "empty-no-self": "AGENT_MCP_NO_SELFPROVISION",
            "no-self": "AGENT_MCP_NO_SELFPROVISION",
            "pinned": "AGENT_MCP_PINNED_CONTEXT", "nested": "UPDATE_DRIVER_WITNESS",
        }[condition]
        env[key] = "" if condition == "empty-no-self" else "1"
    elif condition == "old-driver":
        driver.write_text("# Installation driver lifecycle contract: vault-stamp-v1\nexit 99\n",
                          encoding="utf-8")
    elif condition == "missing-driver":
        driver.unlink()
    elif condition == "malformed-manifest":
        installation.mkdir(exist_ok=True)
        (installation / "deploy-manifest.json").write_text("{", encoding="utf-8")
    home = Path(env["HOME"])
    before = {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    result = run_hook(payload, project, env)
    deferred = condition in {"no-self", "pinned"}
    accepted = condition in {"ordinary", "empty-no-self"}
    assert result.returncode == (0 if accepted or deferred else 3), result.stderr
    assert result.stdout == "{}"
    assert ("supervision launched" in result.stderr) is accepted
    handoff = project / "handoff"
    if accepted or condition in {"no-ack", "foreign-ack", "driver-refused"}:
        assert handoff.read_text(encoding="utf-8-sig").splitlines() == [
            str(home / ".copilot-extensions/update-driver"), str(payload),
            str(installation), str(project), "stamp" if phase == "cold" else "provision",
        ]
    else:
        assert not handoff.exists()
    assert {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before


def test_hook_keeps_deadline_guidance_and_single_execution_owner():
    hooks = json.loads((PLUGIN / "hooks.json").read_text())["hooks"]["sessionStart"]
    assert [hook["timeoutSec"] for hook in hooks] == [15, 45]
    for suffix in ("ps1", "sh"):
        text = (PLUGIN / "scripts" / f"bootstrap-check.{suffix}").read_text()
        assert "mcp-recipes-v1" in text
        assert "Launch is not durable request acceptance." in text
        assert f"write-session-guidance.{suffix}" in hooks[1][
            "powershell" if suffix == "ps1" else "bash"
        ]
        for removed in ("reconcile.lock", "staleSeconds", "Stop-Process", "nohup", "conhost.exe"):
            assert removed not in text
