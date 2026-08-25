"""Compatibility tests for the migrated knowledge-plugin assembler."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_MOD = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "binding-knowledge"
    / "scripts"
    / "assemble_plugins.py"
)
_spec = importlib.util.spec_from_file_location("assemble_plugins", _MOD)
ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ap)


def _result(
    summary: dict | str,
    *,
    returncode: int = 0,
    stderr: str = "",
) -> subprocess.CompletedProcess:
    stdout = summary if isinstance(summary, str) else json.dumps(summary)
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _summary(action: str) -> dict:
    if action == "composed":
        return {
            "action": "composed",
            "paired": True,
            "changed": True,
            "settings_local": "/harness/settings.local.json",
            "harness_path": "/harness",
            "knowledge_path": "/knowledge",
            "marketplaces": ["personal"],
            "enabled_plugins": ["skills@personal"],
            "count": 1,
            "conflicts": {"marketplaces": [], "enabled_plugins": []},
        }
    if action == "retired":
        return {
            "action": "retired",
            "paired": False,
            "retired": True,
            "changed": True,
            "settings_local": "/harness/settings.local.json",
            "harness_path": "/harness",
            "pair_error": "pair records disagree",
            "retired_entries": {
                "marketplaces": ["personal"],
                "enabled_plugins": ["skills@personal"],
            },
            "preserved_modified": {
                "marketplaces": [],
                "enabled_plugins": [],
            },
            "file_removed": False,
        }
    return {
        "action": "no-op",
        "paired": False,
        "retired": False,
        "changed": False,
        "pair_error": "ordinary repo is not paired",
    }


def test_assemble_delegates_explicit_paths_and_parses_json(
    monkeypatch, tmp_path: Path
):
    summary = _summary("composed")
    calls = []
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    monkeypatch.setattr(
        ap.shutil,
        "which",
        lambda name: "/tools/agent-worktrees" if name == "agent-worktrees" else None,
    )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return _result(summary)

    monkeypatch.setattr(ap.subprocess, "run", fake_run)

    assert ap.assemble(harness, knowledge) == summary
    assert calls == [
        (
            [
                "/tools/agent-worktrees",
                "knowledge",
                "compose-plugins",
                "--harness-path",
                str(harness),
                "--knowledge-path",
                str(knowledge),
                "--json",
            ],
            {"capture_output": True, "text": True, "check": False},
        )
    ]


def test_assemble_from_pair_uses_public_command(monkeypatch):
    summary = _summary("no-op")
    commands = []
    monkeypatch.setattr(ap.shutil, "which", lambda _name: "agent-worktrees.exe")
    monkeypatch.setattr(
        ap.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command) or _result(summary),
    )

    assert ap.assemble_from_pair() == summary
    assert commands == [
        [
            "agent-worktrees.exe",
            "knowledge",
            "compose-plugins",
            "--json",
        ]
    ]


def test_missing_command_is_explicit(monkeypatch):
    monkeypatch.setattr(ap.shutil, "which", lambda _name: None)

    with pytest.raises(ap.KnowledgePluginError, match="not found on PATH"):
        ap.assemble_from_pair()


def test_execution_failure_is_explicit(monkeypatch):
    monkeypatch.setattr(ap.shutil, "which", lambda _name: "agent-worktrees")

    def fail(*_args, **_kwargs):
        raise OSError("launch denied")

    monkeypatch.setattr(ap.subprocess, "run", fail)

    with pytest.raises(ap.KnowledgePluginError, match="launch denied"):
        ap.assemble_from_pair()


def test_nonzero_exit_uses_structured_error(monkeypatch):
    monkeypatch.setattr(ap.shutil, "which", lambda _name: "agent-worktrees")
    monkeypatch.setattr(
        ap.subprocess,
        "run",
        lambda *_args, **_kwargs: _result(
            {"action": "error", "error": "unsafe malformed overlay"},
            returncode=3,
            stderr="less useful stderr",
        ),
    )

    with pytest.raises(
        ap.KnowledgePluginError,
        match="status 3: unsafe malformed overlay",
    ):
        ap.assemble_from_pair()


@pytest.mark.parametrize("payload", ["not json", "[]"])
def test_invalid_json_response_is_explicit(monkeypatch, payload):
    monkeypatch.setattr(ap.shutil, "which", lambda _name: "agent-worktrees")
    monkeypatch.setattr(
        ap.subprocess,
        "run",
        lambda *_args, **_kwargs: _result(payload),
    )

    with pytest.raises(ap.KnowledgePluginError, match="returned .*JSON"):
        ap.assemble_from_pair()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"action": "unknown"},
        {"action": []},
        {"action": "composed", "paired": True},
        {
            **_summary("composed"),
            "conflicts": {"marketplaces": [], "enabled_plugins": [1]},
        },
        {"action": "retired", "paired": False, "changed": True},
        {"action": "no-op", "paired": False, "changed": False},
    ],
)
def test_invalid_success_summary_is_explicit(monkeypatch, payload):
    monkeypatch.setattr(ap.shutil, "which", lambda _name: "agent-worktrees")
    monkeypatch.setattr(
        ap.subprocess,
        "run",
        lambda *_args, **_kwargs: _result(payload),
    )

    with pytest.raises(ap.KnowledgePluginError, match="invalid"):
        ap.assemble_from_pair()


def test_non_json_composed_output(monkeypatch, capsys):
    monkeypatch.setattr(ap, "assemble", lambda *_args: _summary("composed"))

    assert ap.main(
        ["--harness-path", "/harness", "--knowledge-path", "/knowledge"]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Updated knowledge plugin overlay: /harness/settings.local.json",
        "  knowledge: /knowledge",
        "  marketplaces: personal",
        "  enabled: skills@personal",
        "Canonical command: agent-worktrees knowledge compose-plugins",
    ]


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        (
            _summary("retired"),
            [
                (
                    "Retired stale knowledge plugin overlay: "
                    "/harness/settings.local.json"
                ),
                "  pair error: pair records disagree",
            ],
        ),
        (
            _summary("no-op"),
            [
                (
                    "Knowledge plugin preflight: no-op "
                    "(ordinary repo is not paired)"
                ),
            ],
        ),
    ],
)
def test_from_pair_non_json_action_outputs(
    monkeypatch, capsys, summary, expected
):
    monkeypatch.setattr(ap, "assemble_from_pair", lambda: summary)

    assert ap.main(["--from-pair"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        *expected,
        "Canonical command: agent-worktrees knowledge compose-plugins",
    ]


def test_json_error_output(monkeypatch, capsys):
    def fail():
        raise ap.KnowledgePluginError("agent-worktrees unavailable")

    monkeypatch.setattr(ap, "assemble_from_pair", fail)

    assert ap.main(["--from-pair", "--json"]) == 3
    assert json.loads(capsys.readouterr().out) == {
        "paired": False,
        "error": "agent-worktrees unavailable",
    }


@pytest.mark.parametrize(
    "summary",
    [
        {},
        {"action": "unexpected"},
        {"action": []},
        {"action": "composed", "paired": True, "changed": True},
        {"action": "retired", "paired": False, "changed": True},
        {"action": "no-op", "paired": False, "changed": False},
    ],
)
def test_main_invalid_summary_returns_documented_exit(
    monkeypatch, capsys, summary
):
    monkeypatch.setattr(ap, "assemble_from_pair", lambda: summary)

    assert ap.main(["--from-pair", "--json"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["paired"] is False
    assert "invalid" in payload["error"]


def test_non_json_error_output(monkeypatch, capsys):
    def fail():
        raise ap.KnowledgePluginError("agent-worktrees unavailable")

    monkeypatch.setattr(ap, "assemble_from_pair", fail)

    assert ap.main(["--from-pair"]) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == (
        "Knowledge plugin overlay not composed: agent-worktrees unavailable"
    )
