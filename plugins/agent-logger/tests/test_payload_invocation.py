from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_logger.segmenter.ramp_up import _render_brief

PLUGIN = Path(__file__).resolve().parents[1]
EXPECTED_COMMANDS = {
    "agent-logger",
    "collate-session",
    "prepare-session-log",
    "ramp-up-session",
    "read-session-digest",
    "session-sync",
}


def _capability_text() -> str:
    paths = [
        *sorted((PLUGIN / "skills").rglob("*.md")),
        *sorted((PLUGIN / "agents").rglob("*.md")),
    ]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def test_payload_manifest_covers_every_runtime_command() -> None:
    manifest = json.loads(
        (PLUGIN / "payload-invocation.json").read_text(encoding="utf-8")
    )

    assert manifest["plugin"] == "agent-logger"
    assert {command["command"] for command in manifest["commands"]} == EXPECTED_COMMANDS


def test_session_start_emits_payload_catalog_after_bootstrap() -> None:
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    session_hooks = hooks["hooks"]["sessionStart"]

    assert len(session_hooks) == 2
    for shell in ("bash", "powershell"):
        assert "bootstrap-check" in session_hooks[0][shell]
        assert "emit-command-catalog" in session_hooks[1][shell]
        for hook in session_hooks:
            assert "COPILOT_PLUGIN_ROOT" in hook[shell]
            assert "'{}'" in hook[shell]


def test_agent_capabilities_use_command_specific_catalog_entries() -> None:
    text = _capability_text()

    for command in EXPECTED_COMMANDS:
        assert f'<agent-logger catalog "{command}" argv[0]>' in text
    assert "installed-plugins/*/agent-logger" not in text
    assert "~/.agent-logger/.venv" not in text
    assert "Ensure `agent-logger` is on PATH" not in text
    assert "If `agent-logger` is not on PATH" not in text
    assert "explicit service-management boundary" in text
    assert "explicit remote-management boundary" in text


@pytest.mark.parametrize(
    ("module", "command"),
    [
        ("agent_logger.segmenter.collate", "collate-session"),
        ("agent_logger.segmenter.prepare_log", "prepare-session-log"),
        ("agent_logger.segmenter.ramp_up", "ramp-up-session"),
        ("agent_logger.segmenter.read_digest", "read-session-digest"),
    ],
)
def test_auxiliary_help_uses_public_command_name(module: str, command: str) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        value
        for value in (str(PLUGIN / "src"), env.get("PYTHONPATH", ""))
        if value
    )
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.startswith(f"usage: {command} ")


def test_ramp_brief_uses_caller_supplied_command_paths(tmp_path: Path) -> None:
    brief = _render_brief(
        session={"id": "example-session"},
        workspace={},
        session_start={},
        checkpoints=[],
        turns=[],
        snapshots=[],
        tail_turns=10,
        digest_dir=tmp_path,
        other_count=1,
    )

    assert "caller-supplied `digest_argv0`" in brief
    assert "caller-supplied ramp command" in brief
    assert "read-session-digest example-session" not in brief
    assert "`ramp-up-session" not in brief
