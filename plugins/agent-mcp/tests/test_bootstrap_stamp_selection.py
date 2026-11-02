from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


_BOOTSTRAP = (
    Path(__file__).resolve().parents[1] / "scripts" / "bootstrap-check.sh"
)


@pytest.mark.parametrize("mode,condition", [
    ("ordinary", "healthy"), ("source", "healthy"),
    ("ordinary", "incomplete"), ("source", "incomplete"),
    ("ordinary", "wrong-generation"), ("source", "wrong-generation"),
    ("source", "foreign-source"),
])
@pytest.mark.parametrize("language,host_name", [
    ("ps1", "pwsh"), ("ps1", "powershell.exe"), ("sh", "bash"),
])
def test_native_post_repair_bootstrap_readiness(
    tmp_path: Path, mode: str, condition: str, language: str, host_name: str,
) -> None:
    if language == "sh" and (os.name == "nt" or not shutil.which("bash")):
        pytest.skip("Native POSIX shell required")
    host = shutil.which(host_name)
    if language == "ps1" and not host:
        pytest.skip("PowerShell host unavailable")
    root = _BOOTSTRAP.parents[3]
    shared = runpy.run_path(str(root / "libs/versioned-runtime/tests/test_bootstrap_selected_readiness.py"))
    payload, home, install = shared["_fixture"](tmp_path, "agent-mcp")
    scripts = payload / "scripts"
    for name in ("bootstrap-check.ps1", "resolve-runtime.ps1", "versioned_runtime_native.ps1"):
        shutil.copyfile(_BOOTSTRAP.with_name(name), scripts / name)
    (scripts / "init.ps1").write_text("throw 'A real installer must not run'\n", encoding="utf-8")
    selected = shared["VERSION"]
    fields = {}
    if mode == "source":
        identity = shared["_source_payload"](payload)
        selected = identity.runtime_version
        fields = shared["source"].manifest_fields(identity)
    selected += ("." if "+" in selected else "+") + "repair." + "d" * 32
    fields["runtime_version"] = selected
    if condition == "foreign-source":
        fields["runtime_source"] = {**fields["runtime_source"], "source_tree": "c" * 40}
    shared["_manifest"](install, **fields)
    slot = shared["_slot"](install, selected, complete=condition != "incomplete")
    (slot / "Scripts").mkdir()
    (slot / "Scripts/python.exe").write_bytes(b"fixture interpreter; must not execute")
    actual = selected
    if condition == "wrong-generation":
        actual = selected[:-32] + "e" * 32
        shared["_slot"](install, actual)
    (install / "current-version").write_text(actual, encoding="utf-8")
    if language == "sh":
        result, _ = shared["_hook"](payload, home)
        assert result.returncode == 0, result.stderr
        if condition != "healthy":
            shared["_wait_for_child"](home)
        assert bool(shared["_children"](home)) is (condition != "healthy")
        assert not (install / "logs").exists()
        return
    hook = str(scripts / "bootstrap-check.ps1").replace("'", "''")
    children = str(home / "children").replace("'", "''")
    command = (
        f"function Start-Process {{ [IO.File]::WriteAllText('{children}', 'reconcile'); "
        "[pscustomobject]@{Id=12345} }\n"
        f". '{hook}'"
    )
    env = shared["_environment"](home)
    env["USERPROFILE"] = str(home)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        [host, "-NoProfile", "-Command", command], cwd=home, env=env,
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0 and result.stdout == "{}", result
    assert (home / "children").exists() is (condition != "healthy"), result.stderr
    if condition == "healthy":
        assert not (install / "reconcile.lock").exists()
    assert not (install / "logs").exists()


def test_bootstrap_falls_through_init_shim_to_stamp_capable_installer(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX bootstrap execution is covered on POSIX")
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is unavailable")

    plugin = tmp_path / "plugin"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(_BOOTSTRAP, scripts / "bootstrap-check.sh")
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "bootstrap-probe"}),
        encoding="utf-8",
    )
    (scripts / "init.sh").write_text(
        "#!/usr/bin/env bash\n"
        "echo 'usage: install|stamp' >/dev/null\n"
        "exec bash \"$(dirname \"$0\")/install.sh\" install\n",
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        "#!/usr/bin/env bash\n"
        "case \"${1:-}\" in\n"
        "  stamp) touch \"$HOME/stamp-ran\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    env = {**os.environ, "HOME": str(tmp_path / "home")}
    Path(env["HOME"]).mkdir()

    subprocess.run(
        [bash, str(scripts / "bootstrap-check.sh")],
        env=env,
        check=True,
    )

    assert (Path(env["HOME"]) / "stamp-ran").is_file()
