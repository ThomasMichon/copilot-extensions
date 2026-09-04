"""Contract tests for the agent-index session command catalog."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def _repo(path: Path, *, active: bool = True) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if active:
        config = path / ".agent-index" / "config.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(
            "indexers:\n  - machine: host-a\n",
            encoding="utf-8",
        )
    return path


def _env() -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "AGENT_INDEX_CONFIG_DATA_B64",
        "AGENT_INDEX_EFFECTIVE_CONFIG",
        "AGENT_INDEX_REPO",
    ):
        env.pop(name, None)
    env["COPILOT_PLUGIN_ROOT"] = str(PLUGIN)
    return env


@pytest.mark.skipif(os.name == "nt", reason="POSIX catalog test")
def test_posix_catalog_uses_exact_payload_command(tmp_path: Path) -> None:
    env = _env()
    result = subprocess.run(
        ["bash", str(PLUGIN / "scripts" / "emit-command-catalog.sh")],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=_repo(tmp_path),
    )
    envelope = json.loads(result.stdout)
    assert "\r" not in envelope["additionalContext"]
    match = re.search(r"```json\n(.*)\n```", envelope["additionalContext"])
    assert match
    catalog = json.loads(match.group(1))
    command = catalog["commands"][0]
    assert catalog["schema"] == "copilot-extensions.session-command-catalog"
    assert command["id"] == "agent-index"
    assert command["argv"] == [str(PLUGIN / "bin" / "agent-index")]
    assert command["availability"] == "ready"


@pytest.mark.skipif(os.name == "nt", reason="POSIX catalog test")
def test_catalog_rejects_conflicting_payload_context(tmp_path: Path) -> None:
    env = _env()
    env["COPILOT_PLUGIN_ROOT"] = str(PLUGIN.parent)
    result = subprocess.run(
        ["bash", str(PLUGIN / "scripts" / "emit-command-catalog.sh")],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=_repo(tmp_path),
    )
    assert json.loads(result.stdout) == {}


def test_powershell_catalog_declares_same_schema_and_command() -> None:
    source = (PLUGIN / "scripts" / "emit-command-catalog.ps1").read_text(
        encoding="utf-8"
    )
    assert "copilot-extensions.session-command-catalog" in source
    assert "bin\\agent-index.ps1" in source
    assert "COPILOT_PLUGIN_ROOT" in source


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not installed")
def test_powershell_catalog_uses_exact_payload_command(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    env = _env()
    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(PLUGIN / "scripts" / "emit-command-catalog.ps1"),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=_repo(tmp_path),
    )
    envelope = json.loads(result.stdout)
    match = re.search(r"```json\n(.*)\n```", envelope["additionalContext"])
    assert match
    catalog = json.loads(match.group(1))
    command = catalog["commands"][0]
    assert command["argv"] == [str(PLUGIN / "bin" / "agent-index.ps1")]
    assert command["availability"] == "ready"


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
@pytest.mark.parametrize("malformed", [False, True], ids=["inactive", "malformed"])
def test_catalog_is_empty_without_valid_opt_in(
    tmp_path: Path, shell: str, malformed: bool
) -> None:
    if shell == "bash":
        if os.name == "nt" or shutil.which("bash") is None:
            pytest.skip("POSIX catalog test")
        command = ["bash", str(PLUGIN / "scripts" / "emit-command-catalog.sh")]
    else:
        pwsh = shutil.which("pwsh")
        if not pwsh:
            pytest.skip("pwsh is not installed")
        command = [
            pwsh,
            "-NoProfile",
            "-File",
            str(PLUGIN / "scripts" / "emit-command-catalog.ps1"),
        ]
    repo = _repo(tmp_path, active=False)
    if malformed:
        config = repo / ".agent-index" / "config.yaml"
        config.parent.mkdir()
        config.write_text("indexers: [\n", encoding="utf-8")
    env = _env()
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=repo,
    )
    assert json.loads(result.stdout) == {}


def test_hooks_use_runtime_payload_context_and_emit_catalog() -> None:
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    session_start = hooks["hooks"]["sessionStart"]
    assert any("emit-command-catalog.sh" in hook["bash"] for hook in session_start)
    assert any(
        "emit-command-catalog.ps1" in hook["powershell"] for hook in session_start
    )
    for hook in session_start:
        assert "COPILOT_PLUGIN_ROOT" in hook["bash"]
        assert "COPILOT_PLUGIN_ROOT" in hook["powershell"]
    for platform in ("bash", "powershell"):
        assert not any(
            "bootstrap-check" in hook[platform] for hook in session_start
        )
        assert not any("ensure-service" in hook[platform] for hook in session_start)
