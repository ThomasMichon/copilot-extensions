"""Bridge bootstrap submits fixed actions without owning an installer process."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs


PLUGIN = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("suffix", ["ps1", "sh"])
def test_bootstrap_has_one_installation_owner(suffix):
    text = (PLUGIN / "scripts" / f"bootstrap-check.{suffix}").read_text()
    assert ("first-use.$suffix" if suffix == "ps1" else "first-use.sh") in text
    for required in (
        "bridge-install-v1", "Launch is not durable request acceptance.",
        "UPDATE_DRIVER_WITNESS", "AGENT_BRIDGE_NO_SELFPROVISION", "AGENT_BRIDGE_PINNED_CONTEXT",
    ):
        assert required in text
    for removed in (
        "reconcile-status.json", "reconcile.lock", "reconcileMutex",
        "staleSeconds", "Stop-Process", "nohup", "init.ps1", "init.sh",
    ):
        assert removed not in text
    if suffix == "ps1":
        assert "$env:AGENT_BRIDGE_NONINTERACTIVE = '1'" in text


def test_deadlines_and_guidance_remain_independent():
    hooks = json.loads((PLUGIN / "hooks.json").read_text())["hooks"]["sessionStart"]
    assert [hook["timeoutSec"] for hook in hooks] == [15, 45]
    for shell, suffix in (("powershell", "ps1"), ("bash", "sh")):
        assert f"bootstrap-check.{suffix}" in hooks[0][shell]
        assert f"write-session-guidance.{suffix}" in hooks[1][shell]


@pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux", reason="native Windows/Linux bootstrap",
)
@pytest.mark.skipif(
    os.environ.get("UPDATE_DRIVER_BRIDGE_BOOTSTRAP_ACCEPTANCE") != "1",
    reason="explicit manual Bridge bootstrap portfolio",
)
@pytest.mark.parametrize("phase", ["cold", "drift", "healthy", "incomplete", "corrupt"])
@pytest.mark.parametrize("condition", [
    "ordinary", "empty-no-self",
    pytest.param("no-python", marks=pytest.mark.skipif(
        os.name == "nt", reason="runtime-owned interpreter fallback is POSIX",
    )),
    "no-ack", "foreign-ack", "driver-refused",
    "old-driver", "wrong-plugin", "array-plugin", "snapshot", "ordinary-copy",
])
def test_native_handoff_preserves_project_history_and_unattended_mode(tmp_path, phase, condition):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    payload = home / ".copilot/installed-plugins/fixture-market/agent-bridge"
    if condition == "snapshot":
        payload = home / ".agent-bridge/snapshots/retained"
    elif condition == "ordinary-copy":
        payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    suffix = "ps1" if os.name == "nt" else "sh"
    hook = scripts / f"bootstrap-check.{suffix}"
    shutil.copyfile(PLUGIN / "scripts" / hook.name, hook)
    identity = {"wrong-plugin": "agent-vault", "array-plugin": ["agent-bridge"]}.get(
        condition, "agent-bridge",
    )
    (payload / "plugin.json").write_text(json.dumps({"name": identity}), encoding="utf-8")
    (payload / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
    for source in (PLUGIN.parents[1] / "libs/versioned-runtime").iterdir():
        if source.suffix in (".py", ".sh"):
            shutil.copyfile(source, scripts / source.name)
    query = scripts / "installation-context/json-query.awk"
    query.parent.mkdir()
    shutil.copyfile(PLUGIN.parents[1] / "libs/installation-context/json-query.awk", query)
    driver = payload / "libs/update-driver/scripts" / f"first-use.{suffix}"
    driver.parent.mkdir(parents=True)
    if os.name == "nt":
        driver.write_text(
            "# Installation driver lifecycle contract: bridge-install-v1\n"
            "param($Root,$Payload,$RuntimeRoot,$Project,$InstallerAction)\n"
            "[IO.File]::WriteAllLines((Join-Path $Project 'handoff'), "
            "[string[]]@($Root,$Payload,$RuntimeRoot,$Project,$InstallerAction,"
            "$env:AGENT_BRIDGE_NONINTERACTIVE))\n"
            "if ($env:BRIDGE_TEST_CONDITION -ne 'no-ack') {\n"
            "$ackRoot = if ($env:BRIDGE_TEST_CONDITION -eq 'foreign-ack') { $Project } else { $Root }\n"
            "[Console]::Out.WriteLine((@{launched=$true;pid=123;created=1;"
            "log=(Join-Path $ackRoot 'first-use-00000000000000000000000000000000.log')} | ConvertTo-Json -Compress))\n"
            "}\nif ($env:BRIDGE_TEST_CONDITION -eq 'driver-refused') { exit 37 }\nexit 0\n",
            encoding="utf-8",
        )
    else:
        driver.write_text(
            "# Installation driver lifecycle contract: bridge-install-v1\n"
            'printf "%s\\n" "$1" "$2" "$3" "$4" "$6" >"$4/handoff"\n'
            'if [[ "$BRIDGE_TEST_CONDITION" != no-ack ]]; then\n'
            'ack_root="$1"; [[ "$BRIDGE_TEST_CONDITION" != foreign-ack ]] || ack_root="$4"\n'
            'printf "launched:123 log:%s/first-use-00000000-0000-0000-0000-000000000000.log\\n" "$ack_root"\n'
            'fi\n[[ "$BRIDGE_TEST_CONDITION" != driver-refused ]] || exit 37\n',
            encoding="utf-8",
        )
    if condition == "old-driver":
        driver.write_text("# Installation driver lifecycle contract: vault-stamp-v1\nexit 99\n",
                          encoding="utf-8")
    installation = home / ".agent-bridge"
    installation.mkdir(exist_ok=True)
    history = installation / "reconcile-status.json"
    history.write_text('{"launched_pid":1,"at":"2000-01-01T00:00:00Z"}', encoding="utf-8")
    if phase != "cold":
        (installation / "deploy-manifest.json").write_text(json.dumps({
            "source": {"version": "0.0.0" if phase == "drift" else "1.0.0"},
        }), encoding="utf-8")
        if phase != "drift":
            (installation / "current-version").write_text("1.0.0", encoding="utf-8")
            slot = installation / "versions/1.0.0"
            python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            python.parent.mkdir(parents=True)
            python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            python.chmod(0o755)
            if phase != "incomplete":
                (slot / ".install-complete.json").write_text(
                    "{" if phase == "corrupt" else json.dumps({
                        "version": "1.0.0", "completed_at": "now", "pid": 0,
                    }),
                    encoding="utf-8",
                )
            if condition == "no-python" and os.name != "nt" and phase == "healthy":
                python.unlink()
                python.symlink_to(sys.executable)
    before = {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_BRIDGE_", "AGENT_RT_", "COPILOT_EXTENSIONS_", "UPDATE_DRIVER_"))
    }
    env.update(
        HOME=str(home), USERPROFILE=str(home), BRIDGE_TEST_CONDITION=condition,
        BOOTSTRAP_KILLSWITCH_STATE_FILE=str(home / "killswitch.json"),
    )
    if condition == "empty-no-self":
        env["AGENT_BRIDGE_NO_SELFPROVISION"] = ""
    if condition == "no-python" and os.name != "nt":
        tools = tmp_path / "native-tools"
        tools.mkdir()
        for name in ("awk", "bash", "basename", "cut", "date", "dirname", "grep", "sed", "sort", "tr", "uname"):
            executable = shutil.which(name)
            assert executable is not None, name
            (tools / name).symlink_to(executable)
        env["PATH"] = str(tools)
        assert shutil.which("python3", path=env["PATH"]) is None
        assert shutil.which("python", path=env["PATH"]) is None
    if os.name == "nt":
        shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        command = [
            str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
            f"Set-Location -LiteralPath {quote(project)}; "
            f"[IO.Directory]::SetCurrentDirectory({quote(home)}); "
            f"& {quote(hook)}; exit $LASTEXITCODE",
        ]
    else:
        command = ["bash", str(hook)]
    result = subprocess.run(
        command, env=env, cwd=project, capture_output=True, text=True,
        encoding="utf-8", timeout=15, **no_window_kwargs(),
    )
    healthy = phase == "healthy" and (
        os.name != "nt" or condition not in {"wrong-plugin", "array-plugin"}
    )
    refused = not healthy and condition not in {"ordinary", "empty-no-self", "no-python"}
    assert result.returncode == (3 if refused else 0), result.stdout + result.stderr
    assert result.stdout == "{}"
    assert ("supervision launched" in result.stderr) == (not refused and not healthy)
    handoff = project / "handoff"
    if healthy or condition in {"old-driver", "wrong-plugin", "array-plugin", "snapshot", "ordinary-copy"}:
        assert not handoff.exists()
    else:
        expected = [
            str(home / ".copilot-extensions/update-driver"), str(payload),
            str(installation), str(project), "stamp" if phase == "cold" else "install",
        ]
        if os.name == "nt":
            expected.append("" if phase == "cold" else "1")
        assert handoff.read_text(encoding="utf-8-sig").splitlines() == expected
    assert {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before
