"""The bootstrap is one producer, not an installer or a companion-hook owner."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

from test_bootstrap_check_reconcile_always_on import _BASH, _clean_env


PLUGIN = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("suffix", ["ps1", "sh"])
def test_ssh_bootstrap_routes_only_fixed_actions_without_an_outer_owner(suffix):
    script = (PLUGIN / "scripts" / f"bootstrap-check.{suffix}").read_text()
    assert f"first-use.{suffix}" in script
    assert "vault-stamp-v1" in script
    assert "Launch is not durable request acceptance." in script
    assert "UPDATE_DRIVER_WITNESS" in script
    assert "AGENT_SSH_NO_SELFPROVISION" in script and "AGENT_SSH_PINNED_CONTEXT" in script
    assert "reconcile.lock" not in script and "staleSeconds" not in script
    assert "init.ps1" not in script and "init.sh" not in script
    assert "Stop-Process" not in script and "nohup" not in script


def test_ssh_bootstrap_preserves_declared_deadline_and_independent_hooks():
    hooks = json.loads((PLUGIN / "hooks.json").read_text())["hooks"]["sessionStart"]
    assert len(hooks) == 3
    assert [hook["timeoutSec"] for hook in hooks] == [15, 10, 45]
    for shell, suffix in (("powershell", "ps1"), ("bash", "sh")):
        assert f"bootstrap-check.{suffix}" in hooks[0][shell]
        assert "register-dispatch-companion" in hooks[1][shell]
        assert "write-session-guidance" in hooks[2][shell]


def test_ssh_foundation_uses_existing_source_materialization_owner():
    text = (PLUGIN / "pyproject.toml").read_text()
    assert '"agent-update-driver"' in text
    assert 'agent-update-driver = { path = "../../libs/update-driver", editable = true }' in text


@pytest.mark.parametrize("phase", ["cold", "drift"])
@pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux", reason="native Windows/Linux bootstrap",
)
@pytest.mark.parametrize("condition", [
    "ordinary", "empty-no-self", "no-ack", "foreign-ack", "driver-refused", "old-driver",
    "wrong-plugin", "array-plugin",
])
def test_native_bootstrap_validates_launch_handoff_without_installing(tmp_path, phase, condition):
    if os.name != "nt" and _BASH is None:
        pytest.skip("native Bash is unavailable")
    home = tmp_path / "home-caf\u00e9"
    home.mkdir()
    project = tmp_path / "project-\u6d4b\u8bd5"
    project.mkdir()
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    suffix = "ps1" if os.name == "nt" else "sh"
    hook = scripts / f"bootstrap-check.{suffix}"
    shutil.copyfile(PLUGIN / "scripts" / hook.name, hook)
    (payload / "plugin.json").write_text('{"name":"agent-ssh"}', encoding="utf-8")
    (payload / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
    driver = payload / "libs" / "update-driver" / "scripts" / f"first-use.{suffix}"
    driver.parent.mkdir(parents=True)
    query = driver.parent.parent / "src" / "update_driver" / "json-query.awk"
    query.parent.mkdir(parents=True)
    shutil.copyfile(
        PLUGIN.parents[1] / "libs/update-driver/src/update_driver/json-query.awk", query,
    )
    if condition in {"wrong-plugin", "array-plugin"}:
        (payload / "plugin.json").write_text(
            json.dumps({"name": "agent-vault" if condition == "wrong-plugin" else ["agent-ssh"]}),
            encoding="utf-8",
        )
    if os.name == "nt":
        driver.write_text(
            "# Installation driver lifecycle contract: vault-stamp-v1\n"
            "param($Root,$Payload,$RuntimeRoot,$Project,$InstallerAction)\n"
            "[IO.File]::WriteAllLines((Join-Path $Project 'handoff'), "
            "[string[]]@($Root,$Payload,$RuntimeRoot,$Project,$InstallerAction))\n"
            "if ($env:SSH_TEST_CONDITION -ne 'no-ack') {\n"
            "$ackRoot = if ($env:SSH_TEST_CONDITION -eq 'foreign-ack') { $Project } else { $Root }\n"
            "[Console]::Out.WriteLine((@{launched=$true;pid=123;created=1;"
            "log=(Join-Path $ackRoot 'first-use-00000000000000000000000000000000.log')} | ConvertTo-Json -Compress))\n"
            "}\nif ($env:SSH_TEST_CONDITION -eq 'driver-refused') { exit 37 }\nexit 0\n",
            encoding="utf-8",
        )
    else:
        driver.write_text(
            "# Installation driver lifecycle contract: vault-stamp-v1\n"
            'printf "%s\\n" "$1" "$2" "$3" "$4" "$6" >"$4/handoff"\n'
            'if [[ "$SSH_TEST_CONDITION" != no-ack ]]; then\n'
            'ack_root="$1"; [[ "$SSH_TEST_CONDITION" != foreign-ack ]] || ack_root="$4"\n'
            'printf "launched:123 log:%s/first-use-00000000-0000-0000-0000-000000000000.log\\n" "$ack_root"\n'
            'fi\n[[ "$SSH_TEST_CONDITION" != driver-refused ]] || exit 37\n',
            encoding="utf-8",
        )
    if condition == "old-driver":
        driver.write_text("exit 99\n", encoding="utf-8")
    installation = home / ".agent-ssh"
    if phase == "drift":
        installation.mkdir()
        (installation / "deploy-manifest.json").write_text(json.dumps({
            "source": {"version": "0.0.0", "path": str(payload)},
        }), encoding="utf-8")
        (installation / "reconcile.lock").write_text("424242", encoding="utf-8")
    before = {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home), "SSH_TEST_CONDITION": condition,
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
    })
    if condition == "empty-no-self":
        env["AGENT_SSH_NO_SELFPROVISION"] = ""
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
        command = [_BASH, str(hook)]
    result = subprocess.run(
        command, env=env, cwd=project, capture_output=True, text=True,
        encoding="utf-8", timeout=15, **no_window_kwargs(),
    )
    refused = condition in {
        "no-ack", "foreign-ack", "driver-refused", "old-driver", "wrong-plugin", "array-plugin",
    }
    assert result.returncode == (3 if refused else 0), result.stdout + result.stderr
    assert result.stdout == "{}"
    assert ("supervision launched" in result.stderr) == (not refused)
    handoff = project / "handoff"
    if condition in {"old-driver", "wrong-plugin", "array-plugin"}:
        assert not handoff.exists()
    else:
        assert handoff.read_text(encoding="utf-8-sig").splitlines() == [
            str(home / ".copilot-extensions" / "update-driver"), str(payload),
            str(installation), str(project), "stamp" if phase == "cold" else "provision",
        ]
    assert {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before
