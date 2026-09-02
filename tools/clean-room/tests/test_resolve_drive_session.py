from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools" / "clean-room" / "resolve_drive_session.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "resolve_drive_session", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, rows: object) -> Path:
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def test_resolves_exact_create_owned_session(tmp_path: Path) -> None:
    session_id = tmp_path / "session-id"
    session_id.write_text("new-session\n", encoding="utf-8")
    sessions = _write(
        tmp_path / "sessions.json",
        [
            {
                "session_id": "old",
                "agent_name": "cleanroom:cr-base",
                "usage_model": "gpt-5.4",
            },
            {
                "session_id": "new-session",
                "agent_name": "cleanroom:cr-base",
                "usage_model": "gpt-5.6-sol-fast",
            },
            {
                "session_id": "other-new",
                "agent_name": "another-agent",
                "usage_model": "claude-opus-4.8",
            },
        ],
    )

    assert _module().resolve(
        session_id, sessions, "cleanroom:cr-base"
    ) == {
        "session_id": "new-session",
        "model": "gpt-5.6-sol-fast",
        "reason": "resolved",
        "candidate_count": 1,
    }


def test_fails_closed_when_owned_session_is_absent(tmp_path: Path) -> None:
    session_id = tmp_path / "session-id"
    session_id.write_text("owned-session\n", encoding="utf-8")
    sessions = _write(
        tmp_path / "sessions.json",
        [
            {
                "session_id": "other-session",
                "agent_name": "cleanroom:cr-base",
                "usage_model": "gpt-5.4",
            },
        ],
    )

    result = _module().resolve(
        session_id, sessions, "cleanroom:cr-base"
    )

    assert result["session_id"] == "owned-session"
    assert result["model"] == ""
    assert result["reason"] == "session-not-found"


def test_fails_closed_for_non_scalar_model(tmp_path: Path) -> None:
    session_id = tmp_path / "session-id"
    session_id.write_text("new-session\n", encoding="utf-8")
    sessions = _write(
        tmp_path / "sessions.json",
        {
            "sessions": [
                {
                    "session_id": "new-session",
                    "agent_name": "cleanroom:cr-base",
                    "usage_model": ["gpt-5.4", "gpt-5.6-sol-fast"],
                }
            ]
        },
    )

    result = _module().resolve(
        session_id, sessions, "cleanroom:cr-base"
    )

    assert result["session_id"] == "new-session"
    assert result["model"] == ""
    assert result["reason"] == "missing-or-invalid-model"


def test_fails_closed_for_missing_session_id_file(tmp_path: Path) -> None:
    sessions = _write(tmp_path / "sessions.json", [])

    result = _module().resolve(
        tmp_path / "missing", sessions, "cleanroom:cr-base"
    )

    assert result["session_id"] == ""
    assert result["reason"] == "missing-or-invalid-session-id-file"
