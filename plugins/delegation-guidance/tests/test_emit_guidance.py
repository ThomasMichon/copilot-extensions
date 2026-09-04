"""Behavior and payload tests for coordinator-first delegation guidance."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
MANIFEST = PLUGIN / "plugin.json"
HOOKS = PLUGIN / "hooks.json"
POWERSHELL_PRODUCER = PLUGIN / "scripts" / "emit-guidance.ps1"
BASH_PRODUCER = PLUGIN / "scripts" / "emit-guidance.sh"
SKILL = PLUGIN / "skills" / "delegating-work" / "SKILL.md"
README = PLUGIN / "README.md"
SESSION_CONTEXT = PLUGIN / "session-context.json"
MODEL_ROUTING_RESOLVER = PLUGIN / "scripts" / "resolve-model-routing.py"
MODEL_ROUTING_SCHEMA = PLUGIN / "schemas" / "model-routing.schema.json"
MODEL_ROUTING_EXAMPLE = PLUGIN / "examples" / "model-routing.json"


def _hook_input() -> str:
    return json.dumps({
        "sessionId": "delegation-guidance-hook-test",
        "cwd": str(PLUGIN),
        "source": "test",
        "timestamp": 1,
    })


def _powershell() -> str | None:
    if os.name == "nt":
        return shutil.which("pwsh") or shutil.which("powershell.exe")
    return shutil.which("pwsh")


def _run_powershell(
    *args: str,
    plugin_root: Path | None = PLUGIN,
) -> subprocess.CompletedProcess[str]:
    powershell = _powershell()
    assert powershell
    environment = os.environ.copy()
    environment.pop("COPILOT_PLUGIN_ROOT", None)
    if plugin_root is not None:
        environment["COPILOT_PLUGIN_ROOT"] = str(plugin_root)
    return subprocess.run(
        [powershell, "-NoProfile", "-File", str(POWERSHELL_PRODUCER), *args],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )


def _run_bash(
    *args: str,
    plugin_root: Path | None = PLUGIN,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("COPILOT_PLUGIN_ROOT", None)
    if plugin_root is not None:
        environment["COPILOT_PLUGIN_ROOT"] = str(plugin_root)
    return subprocess.run(
        ["bash", str(BASH_PRODUCER), *args],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )


def _context(result: subprocess.CompletedProcess[str]) -> str:
    payload = json.loads(result.stdout)
    assert set(payload) == {"additionalContext"}
    return payload["additionalContext"]


def _hook_entry() -> dict[str, object]:
    hooks = json.loads(HOOKS.read_text(encoding="utf-8"))
    return hooks["hooks"]["sessionStart"][0]


def test_manifest_registers_cross_platform_session_start_hook() -> None:
    entry = _hook_entry()
    assert entry["type"] == "command"
    assert entry["timeoutSec"] == 30
    assert "COPILOT_PLUGIN_ROOT" in entry["powershell"]
    assert "PLUGIN_ROOT" in entry["powershell"]
    assert "CLAUDE_PLUGIN_ROOT" in entry["powershell"]
    assert "invoke-context-contributor.ps1" in entry["powershell"]
    assert "emit-guidance.ps1" in entry["powershell"]
    assert "COPILOT_PLUGIN_ROOT" in entry["bash"]
    assert "PLUGIN_ROOT" in entry["bash"]
    assert "CLAUDE_PLUGIN_ROOT" in entry["bash"]
    assert "invoke-context-contributor.sh" in entry["bash"]
    assert "emit-guidance.sh" in entry["bash"]


def test_delegation_guidance_is_first_turn_critical() -> None:
    declaration = json.loads(SESSION_CONTEXT.read_text(encoding="utf-8"))
    contributor = declaration["contributors"][0]

    assert contributor["id"] == "delegation-guidance"
    assert contributor["order"] == 90


@pytest.mark.skipif(_powershell() is None, reason="PowerShell is not installed")
def test_powershell_emits_owned_bounded_guidance() -> None:
    version = json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]
    context = _context(_run_powershell())
    assert context.startswith(f"[owner: delegation-guidance@{version}]\n")
    assert "Before broad code/file research" in context
    assert "three or more independent implementation/subsystem tracks" in context
    assert "reviewer is not an evidence-track substitute" in context
    assert "keep small bounded lookups" in context
    assert "Keep decomposition, synthesis, integration" in context
    assert "domain MCP/service calls" in context
    assert "If you were invoked as a sub-agent" in context
    assert "do not create child agents" in context
    assert "once per unchanged artifact" in context
    assert "use the `delegating-work` skill to load model routing" in context
    assert "candidates require explicit trials" in context
    assert len(context.encode("utf-8")) < 2048


@pytest.mark.skipif(_powershell() is None, reason="PowerShell is not installed")
def test_powershell_falls_back_to_script_location() -> None:
    assert _context(_run_powershell(plugin_root=None)).startswith(
        "[owner: delegation-guidance@"
    )


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="POSIX bash payload test",
)
def test_bash_matches_powershell_guidance() -> None:
    assert _context(_run_bash()) == _context(_run_powershell())


def test_aggregate_guidance_is_owned_compact_and_cross_platform() -> None:
    if os.name == "nt":
        assert _powershell()
        context = _context(_run_powershell("--aggregate"))
    else:
        context = _context(_run_bash("--aggregate"))
    assert context.startswith("[owner: delegation-guidance@")
    assert "3+ independent tracks" in context
    assert "reviewer is not a track substitute" in context
    assert "coordinator retains synthesis" in context
    assert "do not spawn children unless explicitly authorized" in context
    assert "use the `delegating-work` skill to load model routing" in context
    assert "candidates require explicit trials" in context
    assert len(context.encode("utf-8")) <= 768

    if os.name != "nt" and _powershell():
        assert _context(_run_powershell("--aggregate")) == context


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="POSIX bash payload test",
)
def test_bash_falls_back_to_script_location() -> None:
    assert _context(_run_bash(plugin_root=None)).startswith(
        "[owner: delegation-guidance@"
    )


def test_hook_commands_use_plugin_root() -> None:
    entry = _hook_entry()
    environment = os.environ.copy()
    environment["COPILOT_PLUGIN_ROOT"] = str(PLUGIN)

    powershell = _powershell()
    if powershell:
        result = subprocess.run(
            [powershell, "-NoProfile", "-Command", str(entry["powershell"])],
            input=_hook_input(),
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        assert _context(result) == _context(_run_powershell("--aggregate"))

    if os.name != "nt" and shutil.which("bash"):
        result = subprocess.run(
            ["bash", "-c", str(entry["bash"])],
            input=_hook_input(),
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        assert _context(result) == _context(_run_bash("--aggregate"))


def test_hook_commands_accept_compatibility_root_aliases() -> None:
    entry = _hook_entry()
    powershell = _powershell()
    for alias in ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"):
        environment = os.environ.copy()
        for name in ("COPILOT_PLUGIN_ROOT", "PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"):
            environment.pop(name, None)
        environment[alias] = str(PLUGIN)

        if powershell:
            result = subprocess.run(
                [powershell, "-NoProfile", "-Command", str(entry["powershell"])],
                input=_hook_input(),
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            assert _context(result) == _context(_run_powershell("--aggregate"))

        if os.name != "nt" and shutil.which("bash"):
            result = subprocess.run(
                ["bash", "-c", str(entry["bash"])],
                input=_hook_input(),
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            assert _context(result).startswith("[owner: delegation-guidance@")


def test_hook_commands_fail_open_without_plugin_root(tmp_path: Path) -> None:
    entry = _hook_entry()
    environment = os.environ.copy()
    for name in ("COPILOT_PLUGIN_ROOT", "PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"):
        environment.pop(name, None)

    powershell = _powershell()
    if powershell:
        result = subprocess.run(
            [powershell, "-NoProfile", "-Command", str(entry["powershell"])],
            check=True,
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=environment,
        )
        assert result.stdout.strip() == "{}"

    if os.name != "nt" and shutil.which("bash"):
        result = subprocess.run(
            ["bash", "-c", str(entry["bash"])],
            check=True,
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=environment,
        )
        assert result.stdout.strip() == "{}"


@pytest.mark.parametrize("failure", ["missing-skill", "invalid-version"])
def test_producers_fail_open_for_incomplete_payload(
    tmp_path: Path,
    failure: str,
) -> None:
    payload = tmp_path / "delegation-guidance"
    shutil.copytree(PLUGIN, payload)
    if failure == "missing-skill":
        (payload / "skills" / "delegating-work" / "SKILL.md").unlink()
    else:
        manifest = json.loads((payload / "plugin.json").read_text(encoding="utf-8"))
        manifest["version"] = "0.1"
        (payload / "plugin.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )

    powershell = _powershell()
    if powershell:
        result = _run_powershell(plugin_root=payload)
        assert result.stdout == "{}"
        assert result.returncode == 0

    if os.name != "nt" and shutil.which("bash"):
        result = _run_bash(plugin_root=payload)
        assert result.stdout == "{}"
        assert result.returncode == 0


def test_skill_trigger_boundary_and_inventory() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())
    assert "delegate research" in normalized
    assert "use agents to compare or evaluate" in normalized
    assert "split disjoint bulk code" in normalized
    assert "selecting an appropriate worker model" in normalized
    assert "model-routing configuration" in normalized
    assert "state is `demonstrated`" in normalized
    assert "`recheckAfter` date has not elapsed" in normalized
    assert "candidate" in normalized
    assert "also use proactively before opening broad multi-subsystem research" in normalized
    assert "three or more independent implementations or subsystems" in normalized
    assert "reviewers judge a completed artifact" in normalized
    assert "Not for choosing a named domain agent" in normalized
    assert "authoring an agent definition" in normalized
    assert len(skill.splitlines()) < 500

    readme = README.read_text(encoding="utf-8")
    assert "skills/delegating-work/SKILL.md" in readme
    assert "What this plugin provides - and what it doesn't" in readme
    assert "Dependencies & assumptions" in readme
    assert "Troubleshooting, contributing & issues" in readme
    assert "Model-routing configuration" in readme
    assert "schemas/model-routing.schema.json" in readme
    assert "examples/model-routing.json" in readme
    assert MODEL_ROUTING_RESOLVER.is_file()
    assert MODEL_ROUTING_SCHEMA.is_file()
    assert MODEL_ROUTING_EXAMPLE.is_file()
