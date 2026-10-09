from __future__ import annotations

import os
import json
import importlib.util
from pathlib import Path
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def _interpreter(extension: str) -> str:
    interpreter = shutil.which("pwsh" if extension == "ps1" else "bash")
    if extension == "sh" and os.name == "nt":
        git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
        interpreter = str(git_bash) if git_bash.is_file() else None
    if not interpreter:
        pytest.skip(f"{extension} interpreter unavailable")
    return interpreter


@pytest.mark.guard
@pytest.mark.parametrize("extension", ["ps1", "sh"])
def test_read_only_and_cell_actions_never_allocate_legacy_stage(
    tmp_path: Path, extension: str,
) -> None:
    interpreter = _interpreter(extension)
    home = tmp_path / "home"
    home.mkdir()
    payload = tmp_path / ".copilot" / "installed-plugins" / "example" / "agent-index"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "plugin.json").write_text('{"name":"agent-index"}\n', encoding="utf-8")
    text = (PLUGIN / "scripts" / f"install.{extension}").read_text(encoding="utf-8")
    start = text.index("# Status and dependency-light cell-slot actions")
    end = text.index("# === install-contract:v4 smoke seam", start)
    header = (
        "param([string]$Action)\n$ErrorActionPreference = 'Stop'\n"
        if extension == "ps1"
        else '#!/usr/bin/env bash\nset -euo pipefail\n__legacy_action="$1"\n'
    )
    entry = scripts / f"install.{extension}"
    entry.write_text(header + text[start:end], encoding="utf-8", newline="\n")
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_"))
    }
    environment.update({"HOME": str(home), "USERPROFILE": str(home)})
    command = (
        [interpreter, "-NoProfile", "-File", str(entry)]
        if extension == "ps1"
        else [interpreter, entry.as_posix()]
    )
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    for action in ("status", "slot-provision"):
        for stage_flag in ("", "1"):
            environment["COPILOT_PLUGIN_INSTALL_STAGED"] = stage_flag
            result = subprocess.run(
                [*command, action], cwd=home, env=environment,
                capture_output=True, text=True, timeout=15, **kwargs,
            )
            assert result.returncode == 0, (result.stdout, result.stderr)
            assert not (home / ".agent-index").exists()


@pytest.mark.guard
@pytest.mark.parametrize("extension", ["ps1", "sh"])
@pytest.mark.parametrize("action", ["cell-provision", "cell-recover", "slot-cutover"])
def test_cell_coordinator_does_not_reenter_payload(
    tmp_path: Path, extension: str, action: str,
) -> None:
    interpreter = _interpreter(extension)
    home = tmp_path / "home"
    home.mkdir()
    payload = tmp_path / ".copilot" / "installed-plugins" / "example" / "agent-index"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    entry = scripts / f"install.{extension}"
    shutil.copyfile(PLUGIN / "scripts" / entry.name, entry)
    (payload / "plugin.json").write_text('{"name":"agent-index"}\n', encoding="utf-8")
    (payload / "pyproject.toml").write_text('version = "0.1.0"\n', encoding="utf-8")
    receipt = home / "cwd.json"
    (scripts / "cell-runtime.py").write_text(
        "import json, os, time\nfrom pathlib import Path\n"
        "Path(os.environ['TEST_CWD_RECEIPT']).write_text(json.dumps(str(Path.cwd())))\n"
        "time.sleep(3)\n",
        encoding="utf-8",
    )
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_"))
    }
    environment.update({
        "HOME": str(home), "USERPROFILE": str(home),
        "TEST_CWD_RECEIPT": str(receipt),
        "PATH": os.pathsep.join(
            part for part in environment["PATH"].split(os.pathsep)
            if "WindowsApps" not in part
        ),
    })
    arguments = (
        ["-Context", str(home / "install.json"), "-ExpectedMarketplaceId", "example"]
        if extension == "ps1"
        else ["--context", (home / "install.json").as_posix(), "--expected-marketplace-id", "example"]
    )
    if action == "slot-cutover":
        options = {
            "ExpectedNamespaceGeneration": "1", "ExpectedInstallGeneration": "1",
            "TargetPayloadRoot": str(payload), "TargetPayloadVersion": "0.1.0",
            "TargetSnapshotId": "snapshot", "TargetRuntimeVersion": "0.1.0",
        }
        for name, value in options.items():
            flag = (
                f"-{name}" if extension == "ps1"
                else "--" + "".join("-" + char.lower() if char.isupper() else char for char in name).lstrip("-")
            )
            arguments.extend([flag, value])
        arguments.append("-ExpectCurrentAbsent" if extension == "ps1" else "--expect-current-absent")
    command = (
        [interpreter, "-NoProfile", "-File", str(entry)]
        if extension == "ps1"
        else [interpreter, entry.as_posix()]
    )
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    process = subprocess.Popen(
        [*command, action, *arguments], cwd=payload if extension == "ps1" else home,
        env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs,
    )
    try:
        deadline = time.monotonic() + 15
        while not receipt.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not receipt.exists():
            stdout, stderr = process.communicate(timeout=5)
            pytest.fail(f"no coordinator receipt: {process.returncode}\n{stdout}\n{stderr}")
        assert Path(json.loads(receipt.read_text())).resolve() == home.resolve()
        assert process.poll() is None
        payload.rename(payload.with_name("replaced-payload"))
        payload.mkdir()
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (stdout, stderr)
        assert not (home / ".agent-index").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


@pytest.mark.guard
def test_context_helper_child_uses_home_not_payload(tmp_path: Path, monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location(
        "cwd_cell_runtime", PLUGIN / "scripts" / "cell-runtime.py",
    )
    assert spec and spec.loader
    coordinator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(coordinator)
    home = tmp_path / "home"
    home.mkdir()
    payload = tmp_path / "payload"
    helpers = payload / "scripts" / "installation-context"
    helpers.mkdir(parents=True)
    receipt = home / "ready"
    (helpers / "installation_context.py").write_text(
        "import json, time\nfrom pathlib import Path\n"
        f"Path({str(receipt)!r}).write_text('ready')\n"
        "time.sleep(3)\nprint(json.dumps({'cwd': str(Path.cwd())}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(coordinator._run_context, payload, "status")
        deadline = time.monotonic() + 10
        while not receipt.exists() and not result.done() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert receipt.exists()
        assert not result.done()
        payload.rename(tmp_path / "replaced-payload")
        payload.mkdir()
        assert Path(result.result(timeout=10)["cwd"]).resolve() == home.resolve()
