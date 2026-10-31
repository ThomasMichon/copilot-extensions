"""CodeSpaces bootstrap submits fixed actions; it is not another installer."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

from .test_bootstrap_check_reconcile_always_on import _BASH, _clean_env


PLUGIN = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("suffix", ["ps1", "sh"])
def test_codespaces_bootstrap_has_no_outer_installer_owner(suffix):
    text = (PLUGIN / "scripts" / f"bootstrap-check.{suffix}").read_text()
    assert ("first-use.$suffix" if suffix == "ps1" else "first-use.sh") in text
    assert "vault-stamp-v1" in text
    assert "Launch is not durable request acceptance." in text
    assert "UPDATE_DRIVER_WITNESS" in text
    assert "AGENT_CODESPACES_NO_SELFPROVISION" in text
    assert "AGENT_CODESPACES_PINNED_CONTEXT" in text
    for removed in ("reconcile.lock", "staleSeconds", "Stop-Process", "nohup", "init.ps1", "init.sh"):
        assert removed not in text


def test_codespaces_preserves_deadlines_and_independent_hook_owners():
    hooks = json.loads((PLUGIN / "hooks.json").read_text())["hooks"]["sessionStart"]
    assert len(hooks) == 3
    assert [hook["timeoutSec"] for hook in hooks] == [15, 10, 45]
    for shell, suffix in (("powershell", "ps1"), ("bash", "sh")):
        assert f"bootstrap-check.{suffix}" in hooks[0][shell]
        assert f"register-bridge-provider.{suffix}" in hooks[1][shell]
        assert f"write-session-guidance.{suffix}" in hooks[2][shell]


def test_codespaces_packages_foundation_through_canonical_materializer():
    text = (PLUGIN / "pyproject.toml").read_text()
    assert '"agent-update-driver"' in text
    assert 'agent-update-driver = { path = "../../libs/update-driver", editable = true }' in text


@pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux", reason="native Windows/Linux bootstrap",
)
@pytest.mark.parametrize("phase", ["cold", "drift"])
@pytest.mark.parametrize("condition", [
    "ordinary", "empty-no-self", "no-ack", "foreign-ack", "driver-refused",
    "old-driver", "wrong-plugin", "array-plugin", "snapshot", "ordinary-copy",
])
def test_native_bootstrap_validates_handoff_without_installing(tmp_path, phase, condition):
    if os.name != "nt" and _BASH is None:
        pytest.skip("native Bash unavailable")
    home = tmp_path / "home-caf\u00e9"
    home.mkdir()
    project = tmp_path / "project-\u6d4b\u8bd5"
    project.mkdir()
    payload = home / ".copilot/installed-plugins/fixture-market/agent-codespaces"
    if condition == "snapshot":
        payload = home / ".agent-codespaces/snapshots/retained"
    elif condition == "ordinary-copy":
        payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    suffix = "ps1" if os.name == "nt" else "sh"
    hook = scripts / f"bootstrap-check.{suffix}"
    shutil.copyfile(PLUGIN / "scripts" / hook.name, hook)
    identity = "agent-codespaces"
    if condition == "wrong-plugin":
        identity = "agent-vault"
    elif condition == "array-plugin":
        identity = ["agent-codespaces"]
    (payload / "plugin.json").write_text(json.dumps({"name": identity}), encoding="utf-8")
    (payload / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
    driver = payload / "libs/update-driver/scripts" / f"first-use.{suffix}"
    driver.parent.mkdir(parents=True)
    query = driver.parent.parent / "src/update_driver/json-query.awk"
    query.parent.mkdir(parents=True)
    shutil.copyfile(PLUGIN.parents[1] / "libs/update-driver/src/update_driver/json-query.awk", query)
    if os.name == "nt":
        driver.write_text(
            "# Installation driver lifecycle contract: vault-stamp-v1\n"
            "param($Root,$Payload,$RuntimeRoot,$Project,$InstallerAction)\n"
            "[IO.File]::WriteAllLines((Join-Path $Project 'handoff'), "
            "[string[]]@($Root,$Payload,$RuntimeRoot,$Project,$InstallerAction))\n"
            "if ($env:CODESPACES_TEST_CONDITION -ne 'no-ack') {\n"
            "$ackRoot = if ($env:CODESPACES_TEST_CONDITION -eq 'foreign-ack') { $Project } else { $Root }\n"
            "[Console]::Out.WriteLine((@{launched=$true;pid=123;created=1;"
            "log=(Join-Path $ackRoot 'first-use-00000000000000000000000000000000.log')} | ConvertTo-Json -Compress))\n"
            "}\nif ($env:CODESPACES_TEST_CONDITION -eq 'driver-refused') { exit 37 }\nexit 0\n",
            encoding="utf-8",
        )
    else:
        driver.write_text(
            "# Installation driver lifecycle contract: vault-stamp-v1\n"
            'printf "%s\\n" "$1" "$2" "$3" "$4" "$6" >"$4/handoff"\n'
            'if [[ "$CODESPACES_TEST_CONDITION" != no-ack ]]; then\n'
            'ack_root="$1"; [[ "$CODESPACES_TEST_CONDITION" != foreign-ack ]] || ack_root="$4"\n'
            'printf "launched:123 log:%s/first-use-00000000-0000-0000-0000-000000000000.log\\n" "$ack_root"\n'
            'fi\n[[ "$CODESPACES_TEST_CONDITION" != driver-refused ]] || exit 37\n',
            encoding="utf-8",
        )
    if condition == "old-driver":
        driver.write_text("exit 99\n", encoding="utf-8")
    installation = home / ".agent-codespaces"
    if phase == "drift":
        installation.mkdir(exist_ok=True)
        (installation / "deploy-manifest.json").write_text(json.dumps({
            "source": {"version": "0.0.0", "path": str(tmp_path / "foreign-payload")},
        }), encoding="utf-8")
        (installation / "reconcile.lock").write_text("424242", encoding="utf-8")
    before = {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home), "CODESPACES_TEST_CONDITION": condition,
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
    })
    if condition == "empty-no-self":
        env["AGENT_CODESPACES_NO_SELFPROVISION"] = ""
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
    refused = condition not in {"ordinary", "empty-no-self"}
    assert result.returncode == (3 if refused else 0), result.stdout + result.stderr
    assert result.stdout == "{}"
    assert ("supervision launched" in result.stderr) == (not refused)
    handoff = project / "handoff"
    if condition in {"old-driver", "wrong-plugin", "array-plugin", "snapshot", "ordinary-copy"}:
        assert not handoff.exists()
    else:
        assert handoff.read_text(encoding="utf-8-sig").splitlines() == [
            str(home / ".copilot-extensions/update-driver"), str(payload),
            str(installation), str(project), "stamp" if phase == "cold" else "provision",
        ]
    assert {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before
