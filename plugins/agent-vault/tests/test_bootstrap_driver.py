"""Native bootstrap routing, opt-outs and skew without a real installer."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from agent_procutil import no_window_kwargs

from .test_bootstrap_check_reconcile_always_on import (
    _BASH, _clean_env, _make_fake_install, _make_fake_plugin,
)


def native_bootstrap(plugin, project, native_directory):
    script = plugin / "scripts" / ("bootstrap-check.ps1" if os.name == "nt" else "bootstrap-check.sh")
    if os.name != "nt":
        if _BASH is None:
            pytest.skip("native Bash is unavailable")
        return [_BASH, str(script)]
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    return [
        str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
        f"Set-Location -LiteralPath {quote(project)}; "
        f"[IO.Directory]::SetCurrentDirectory({quote(native_directory)}); "
        f"& {quote(script)}; exit $LASTEXITCODE",
    ]


@pytest.mark.parametrize("phase", ["cold", "drift"])
@pytest.mark.parametrize("condition", [
    "ordinary", "no-self", "pinned", "killswitch", "cell", "missing-driver",
    "old-driver", "missing-environment", "driver-refused", "nested", "stale-opt-in", "unicode",
    "no-ack", "foreign-ack",
])
def test_bootstrap_routes_or_defers_without_inline_install(
    tmp_path, phase, condition,
):
    label = "-caf\u00e9-\u6d4b\u8bd5" if condition == "unicode" else ""
    home = tmp_path / ("home" + label)
    home.mkdir()
    project = tmp_path / ("project" + label)
    project.mkdir()
    plugin = _make_fake_plugin(tmp_path, "agent-vault")
    installation = home / ".agent-vault"
    if phase == "drift":
        _make_fake_install(home, "agent-vault")
        lock = installation / "reconcile.lock"
        lock.write_text("424242", encoding="utf-8")
        os.utime(lock, (1_000_000_000, 1_000_000_000))
        old_lock = (lock.read_bytes(), lock.stat().st_mtime_ns)
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
        "AGENT_VAULT_INSTALLATION_ID": "foreign-id",
        "AGENT_VAULT_PIPE": "foreign-pipe",
    })
    suffix = "ps1" if os.name == "nt" else "sh"
    driver = plugin / "libs" / "update-driver" / "scripts" / ("first-use." + suffix)
    if condition == "no-self":
        env["AGENT_VAULT_NO_SELFPROVISION"] = "1"
    elif condition == "pinned":
        env["AGENT_VAULT_PINNED_CONTEXT"] = "{}"
    elif condition == "killswitch":
        (home / "killswitch.json").write_text('{"active":true}', encoding="utf-8")
    elif condition == "cell":
        env["COPILOT_EXTENSIONS_CONTEXT"] = str(home / "cell.json")
    elif condition == "missing-driver":
        driver.unlink()
    elif condition == "old-driver":
        driver.write_text("exit 99\n", encoding="utf-8")
    elif condition == "missing-environment":
        (plugin / "scripts" / ("runtime-environment." + suffix)).unlink()
    elif condition == "driver-refused":
        env["BOOTSTRAP_TEST_DRIVER_EXIT_CODE"] = "37"
    elif condition == "no-ack":
        env["BOOTSTRAP_TEST_NO_ACK"] = "1"
    elif condition == "foreign-ack":
        env["BOOTSTRAP_TEST_FOREIGN_ACK"] = "1"
    elif condition == "nested":
        env["UPDATE_DRIVER_WITNESS"] = "{}"
    elif condition == "stale-opt-in":
        config = project / ".copilot-extensions" / "config.yaml"
        config.parent.mkdir()
        config.write_text("background_reconcile_agent-vault: false\n", encoding="utf-8")
    result = subprocess.run(
        native_bootstrap(plugin, project, home),
        env=env, cwd=project, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", timeout=15, **no_window_kwargs(),
    )
    assert json.loads(result.stdout) == {}, result.stdout + result.stderr
    handoff = project / "handoff"
    if condition in {"ordinary", "driver-refused", "stale-opt-in", "unicode", "no-ack", "foreign-ack"}:
        assert handoff.read_text(encoding="utf-8-sig").splitlines() == [
            str(home / ".copilot-extensions" / "update-driver"),
            str(plugin), str(installation), str(project),
            "stamp" if phase == "cold" else "provision", "<absent>", "<absent>",
        ]
        refused = condition in {"driver-refused", "no-ack", "foreign-ack"}
        assert result.returncode == (3 if refused else 0), result.stderr
        assert ("supervision launched" in result.stderr) == (not refused)
        if condition == "unicode":
            acknowledgement = next(
                line.partition("driver launch acknowledgement: ")[2]
                for line in result.stderr.splitlines() if "driver launch acknowledgement: " in line
            )
            if os.name == "nt":
                assert json.loads(acknowledgement)["project"] == str(project)
            else:
                assert str(home) in acknowledgement
    else:
        assert not handoff.exists()
        refused = condition in {"missing-driver", "old-driver", "missing-environment", "nested"}
        assert result.returncode == (3 if refused else 0), result.stderr
    assert not (plugin / "scripts" / "inline-entered").exists()
    assert not (home / ".copilot-extensions" / "update-driver").exists()
    if phase == "drift":
        assert (lock.read_bytes(), lock.stat().st_mtime_ns) == old_lock
    else:
        assert not installation.exists()


def test_healthy_bootstrap_never_requires_driver_or_environment_helper(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    plugin = _make_fake_plugin(tmp_path, "agent-vault")
    installation = _make_fake_install(home, "agent-vault")
    manifest = installation / "deploy-manifest.json"
    manifest.write_text('{"source":{"version":"1.0.1"}}', encoding="utf-8")
    (installation / "current-version").write_text("1.0.1", encoding="utf-8")
    python = installation / "versions" / "1.0.1" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    python.parent.mkdir(parents=True)
    python.write_text("healthy interpreter fixture\n", encoding="utf-8")
    python.chmod(0o755)
    shutil.rmtree(plugin / "libs")
    for suffix in ("ps1", "sh"):
        (plugin / "scripts" / ("runtime-environment." + suffix)).unlink()
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
    })
    before = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    result = subprocess.run(
        native_bootstrap(plugin, project, home), env=env, cwd=project,
        capture_output=True, text=True, encoding="utf-8", timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0 and json.loads(result.stdout) == {}, result.stdout + result.stderr
    assert not result.stderr and not (project / "handoff").exists()
    assert {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("failure", [
    "empty-owner", "wrong-owner", "malformed-deployment", "nonstring-version", "missing-version",
])
def test_invalid_bootstrap_metadata_refuses_visibly_without_driver_handoff(tmp_path, failure):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    plugin = _make_fake_plugin(tmp_path, "agent-vault")
    installation = _make_fake_install(home, "agent-vault")
    if failure in {"empty-owner", "wrong-owner"}:
        (plugin / "plugin.json").write_text(
            json.dumps({"name": "" if failure == "empty-owner" else "other-plugin"}),
            encoding="utf-8",
        )
    elif failure == "malformed-deployment":
        (installation / "deploy-manifest.json").write_text("{", encoding="utf-8")
    elif failure == "nonstring-version":
        (installation / "deploy-manifest.json").write_text('{"source":{"version":true}}', encoding="utf-8")
    else:
        (plugin / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    before = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
    })
    result = subprocess.run(
        native_bootstrap(plugin, project, home), env=env, cwd=project,
        capture_output=True, text=True, encoding="utf-8", timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 3 and json.loads(result.stdout) == {}, result.stdout + result.stderr
    assert "Runtime publication refused" in result.stderr and not (project / "handoff").exists()
    assert {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX metadata-reader boundary")
@pytest.mark.parametrize("killswitch", [False, True])
def test_bootstrap_without_metadata_python_remains_read_only(tmp_path, killswitch):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    plugin = _make_fake_plugin(tmp_path, "agent-vault")
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("bash", "dirname"):
        executable = shutil.which(name)
        if executable is None:
            pytest.skip(f"native tool is unavailable: {name}")
        (tools / name).symlink_to(executable)
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home), "PATH": str(tools),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
    })
    if killswitch:
        (home / "killswitch.json").write_text('{"active":true}', encoding="utf-8")
    before = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    result = subprocess.run(
        native_bootstrap(plugin, project, home), env=env, cwd=project,
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0 and json.loads(result.stdout) == {}, result.stdout + result.stderr
    assert not (project / "handoff").exists()
    assert {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before
