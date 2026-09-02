"""Tests for the bounded aggregate-mode agent-worktrees producer."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN / "scripts" / "emit_session_context.py"
SPEC = importlib.util.spec_from_file_location("emit_session_context", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_cold_start_budgets_fit_declared_engine_timeout() -> None:
    declaration = json.loads(
        (PLUGIN / "session-context.json").read_text(encoding="utf-8")
    )
    contributor = next(
        item
        for item in declaration["contributors"]
        if item["id"] == "aggregate-context"
    )
    engine_timeout = contributor["timeoutSeconds"]

    assert MODULE.DEADLINE_SECONDS <= engine_timeout
    assert MODULE.AWAIT_TIMEOUT_SECONDS < MODULE.DEADLINE_SECONDS
    assert MODULE.MACHINE_TIMEOUT_SECONDS >= 4
    assert MODULE.CONDUCT_TIMEOUT_SECONDS >= 6


def test_compose_prioritizes_binding_and_conduct_within_budget() -> None:
    fragments = {
        "marketplace": "marketplace-" + ("x" * 500),
        "binding": "[agent-worktrees] This Copilot session is bound.",
        "machine": "Machine: Example\nProject: example",
        "conduct": (
            "Agent-worktrees owns this session's worktree binding. "
            "`agent-worktrees status` is authoritative. "
            "Load `agent-worktrees:worktree` for details."
        ),
        "nudge": "nudge-" + ("x" * 500),
    }

    rendered = MODULE._compose("1.2.3", fragments)
    payload = json.loads(rendered)
    context = payload["additionalContext"]

    assert context.startswith("[owner: agent-worktrees@1.2.3]")
    assert fragments["binding"] in context
    assert fragments["conduct"] in context
    assert len(rendered.encode("utf-8")) <= MODULE.MAX_CONTEXT_BYTES


@pytest.mark.parametrize("segments", [80, 400])
def test_compose_bounds_final_json_with_deep_windows_paths(
    segments: int,
) -> None:
    windows_path = "C:\\" + "\\".join(
        f"segment-{index:03d}" for index in range(segments)
    )
    fragments = {
        "binding": (
            "[agent-worktrees] This Copilot session is bound to "
            f"{windows_path}."
        ),
        "conduct": "Use exact paths and preserve \"quoted\" context.",
    }

    rendered = MODULE._compose("1.2.3", fragments)
    payload = json.loads(rendered)

    assert len(rendered.encode("utf-8")) <= MODULE.MAX_CONTEXT_BYTES
    assert payload["additionalContext"].startswith(
        "[owner: agent-worktrees@1.2.3]"
    )


def test_compactors_preserve_binding_and_machine_identity() -> None:
    binding = (
        "## command catalog\n\n"
        "[agent-worktrees] This Copilot session reports mux pane %7 and is "
        "bound to worktree wt-example; run task commands from /repo."
    )
    assert MODULE._compact_binding(binding).startswith(
        "[agent-worktrees] This Copilot session"
    )

    machine = MODULE._compact_machine(
        "Machine: Example\nHostname: host\nDescription: long\n"
        "Capabilities: many\nProject: repo\nBinstub: repo"
    )
    assert machine.splitlines() == [
        "Machine: Example",
        "Hostname: host",
        "Project: repo",
        "Binstub: repo",
    ]


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None or shutil.which("pwsh") is None,
    reason="Bash/PowerShell parity requires both shells",
)
def test_payload_wrappers_emit_identical_bounded_context(tmp_path: Path) -> None:
    plugin = tmp_path / "agent-worktrees"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPT, scripts / SCRIPT.name)
    shutil.copy2(
        PLUGIN / "scripts" / "emit-session-context.sh",
        scripts / "emit-session-context.sh",
    )
    shutil.copy2(
        PLUGIN / "scripts" / "emit-session-context.ps1",
        scripts / "emit-session-context.ps1",
    )
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "agent-worktrees", "version": "1.2.3"}),
        encoding="utf-8",
    )
    for name in (
        "marketplace-overrides",
        "register-session",
        "session-machine",
        "session-conduct",
        "register-nudge",
    ):
        (scripts / f"{name}.sh").write_text(
            "#!/usr/bin/env bash\nprintf '{}'\n",
            encoding="utf-8",
        )
        (scripts / f"{name}.ps1").write_text(
            "[Console]::Out.Write('{}')\n",
            encoding="utf-8",
        )
    environment = {**os.environ, "COPILOT_PLUGIN_ROOT": str(plugin)}
    payload = '{"sessionId":"session-1","cwd":"/repo"}'
    bash = subprocess.run(
        ["bash", str(scripts / "emit-session-context.sh")],
        input=payload,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    powershell = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-File",
            str(scripts / "emit-session-context.ps1"),
        ],
        input=payload,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )

    assert powershell.stdout == bash.stdout
    context = json.loads(bash.stdout)["additionalContext"]
    assert context == "[owner: agent-worktrees@1.2.3]"
    assert len(bash.stdout.encode("utf-8")) <= MODULE.MAX_CONTEXT_BYTES

    py_only = tmp_path / "py-only"
    py_only.mkdir()
    (py_only / "py").symlink_to(sys.executable)
    powershell_command = shutil.which("pwsh")
    assert powershell_command is not None
    py_result = subprocess.run(
        [
            powershell_command,
            "-NoProfile",
            "-File",
            str(scripts / "emit-session-context.ps1"),
        ],
        input=payload,
        env={
            **environment,
            "PATH": str(py_only),
            "HOME": str(tmp_path / "home"),
            "USERPROFILE": str(tmp_path / "home"),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(py_result.stdout)["additionalContext"] == (
        "[owner: agent-worktrees@1.2.3]"
    )
