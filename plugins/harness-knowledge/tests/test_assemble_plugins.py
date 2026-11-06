"""Compatibility tests for the migrated knowledge-plugin assembler."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_MOD = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "binding-knowledge"
    / "scripts"
    / "assemble_plugins.py"
)
_spec = importlib.util.spec_from_file_location("assemble_plugins", _MOD)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load the plugin assembler from {_MOD}")
ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ap)


def _result(
    summary: dict[str, Any] | str,
    *,
    returncode: int = 0,
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    stdout = summary if isinstance(summary, str) else json.dumps(summary)
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _summary(action: str) -> dict[str, Any]:
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summary = _summary("composed")
    calls = []
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    monkeypatch.setattr(
        ap.shutil,
        "which",
        lambda name: "/tools/agent-worktrees" if name == "agent-worktrees" else None,
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
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
            {"capture_output": True, "text": True, "check": False,
             "creationflags": int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if sys.platform == "win32" else 0},
        )
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console creation contract")
def test_composer_delegate_stays_windowless_from_windowless_parent(tmp_path: Path) -> None:
    marker = tmp_path / "console.json"
    child = (
        "import ctypes,json,pathlib,sys;"
        "pathlib.Path(sys.argv[1]).write_text(str(ctypes.windll.kernel32.GetConsoleWindow()));"
        f"print({json.dumps(_summary('no-op'))!r})"
    )
    parent = (
        "import importlib.util,sys;"
        f"spec=importlib.util.spec_from_file_location('assembler',{str(_MOD)!r});"
        "module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);"
        f"module._resolve_command=lambda _: [sys.executable,'-c',{child!r},{str(marker)!r}];"
        "from pathlib import Path;"
        "result=[]\n"
        "for _ in range(2):\n"
        " module._delegate([], 'selected-owner')\n"
        f" result.append(Path({str(marker)!r}).read_text())\n"
        "assert result == ['0','0'], result\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", parent], capture_output=True, text=True, timeout=15,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        env=dict(os.environ),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_assemble_from_pair_uses_public_command(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = _summary("no-op")
    commands: list[list[str]] = []
    monkeypatch.setattr(ap.shutil, "which", lambda _name: "agent-worktrees.exe")
    monkeypatch.setattr(
        ap.subprocess,
        "run",
        lambda command, **_kwargs: (commands.__iadd__([command]), _result(summary))[1],
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


def test_assemble_prefers_explicit_command_over_ambient_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller-supplied resolved command must win over `PATH` -- the whole
    point is not selecting a different marketplace/cell's ambient install."""
    summary = _summary("no-op")
    commands: list[list[str]] = []
    monkeypatch.setattr(
        ap.shutil, "which", lambda _name: "/wrong-cell/bin/agent-worktrees"
    )
    monkeypatch.setattr(
        ap.subprocess,
        "run",
        lambda command, **_kwargs: (commands.__iadd__([command]), _result(summary))[1],
    )

    assert (
        ap.assemble(
            "/harness", "/knowledge",
            agent_worktrees_command="/right-cell/bin/agent-worktrees",
        )
        == summary
    )
    assert commands == [
        [
            "/right-cell/bin/agent-worktrees",
            "knowledge",
            "compose-plugins",
            "--harness-path",
            "/harness",
            "--knowledge-path",
            "/knowledge",
            "--json",
        ]
    ]


def test_missing_command_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ap.shutil, "which", lambda _name: None)

    with pytest.raises(ap.KnowledgePluginError, match="not found on PATH"):
        ap.assemble_from_pair()


def test_explicit_ps1_without_powershell_host_fails_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A resolved .ps1 command with no pwsh/powershell host must raise, not
    silently fall through to an unexecutable argv[0]."""
    if ap.os.name != "nt":
        return
    monkeypatch.setattr(ap.shutil, "which", lambda _name: None)

    with pytest.raises(ap.KnowledgePluginError, match="PowerShell"):
        ap.assemble_from_pair(
            agent_worktrees_command=r"C:\right-cell\agent-worktrees.ps1"
        )


def test_execution_failure_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ap.shutil, "which", lambda _name: "agent-worktrees")

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("launch denied")

    monkeypatch.setattr(ap.subprocess, "run", fail)

    with pytest.raises(ap.KnowledgePluginError, match="launch denied"):
        ap.assemble_from_pair()


def test_nonzero_exit_uses_structured_error(monkeypatch: pytest.MonkeyPatch) -> None:
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
def test_invalid_json_response_is_explicit(monkeypatch: pytest.MonkeyPatch, payload: str) -> None:
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
def test_invalid_success_summary_is_explicit(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> None:
    monkeypatch.setattr(ap.shutil, "which", lambda _name: "agent-worktrees")
    monkeypatch.setattr(
        ap.subprocess,
        "run",
        lambda *_args, **_kwargs: _result(payload),
    )

    with pytest.raises(ap.KnowledgePluginError, match="invalid"):
        ap.assemble_from_pair()


def test_non_json_composed_output(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(ap, "assemble", lambda *_args, **_kwargs: _summary("composed"))

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


def test_main_forwards_agent_worktrees_path_to_assemble(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
    monkeypatch.setattr(
        ap,
        "assemble",
        lambda *args, **kwargs: (calls.__iadd__([(args, kwargs)]), _summary("composed"))[1],
    )

    assert ap.main([
        "--harness-path", "/harness",
        "--knowledge-path", "/knowledge",
        "--agent-worktrees-path", "/right-cell/agent-worktrees",
        "--json",
    ]) == 0

    assert calls == [
        (
            ("/harness", "/knowledge"),
            {"agent_worktrees_command": "/right-cell/agent-worktrees"},
        )
    ]


def test_main_from_pair_forwards_agent_worktrees_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str]] = []
    monkeypatch.setattr(
        ap,
        "assemble_from_pair",
        lambda **kwargs: (calls.__iadd__([kwargs]), _summary("no-op"))[1],
    )

    assert ap.main([
        "--from-pair",
        "--agent-worktrees-path", "/right-cell/agent-worktrees",
        "--json",
    ]) == 0

    assert calls == [{"agent_worktrees_command": "/right-cell/agent-worktrees"}]


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
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    summary: dict[str, Any], expected: list[str],
) -> None:
    monkeypatch.setattr(ap, "assemble_from_pair", lambda **_kwargs: summary)

    assert ap.main(["--from-pair"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        *expected,
        "Canonical command: agent-worktrees knowledge compose-plugins",
    ]


def test_json_error_output(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fail(**_kwargs: object) -> None:
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
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], summary: dict[str, Any],
) -> None:
    monkeypatch.setattr(ap, "assemble_from_pair", lambda **_kwargs: summary)

    assert ap.main(["--from-pair", "--json"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["paired"] is False
    assert "invalid" in payload["error"]


def test_non_json_error_output(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fail(**_kwargs: object) -> None:
        raise ap.KnowledgePluginError("agent-worktrees unavailable")

    monkeypatch.setattr(ap, "assemble_from_pair", fail)

    assert ap.main(["--from-pair"]) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == (
        "Knowledge plugin overlay not composed: agent-worktrees unavailable"
    )
