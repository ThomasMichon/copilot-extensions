from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bind_nudge.py"
_SPEC = importlib.util.spec_from_file_location("bind_nudge_under_test", _SCRIPT)
assert _SPEC and _SPEC.loader
bind_nudge = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = bind_nudge
_SPEC.loader.exec_module(bind_nudge)


def _record(home: Path, *, head: str = "", sessions: str = "") -> Path:
    root = home / "wt-a"
    root.mkdir()
    tracking = home / ".project" / "worktrees"
    tracking.mkdir(parents=True)
    (tracking / "wt-a.yaml").write_text(
        f"worktree_id: wt-a\nworktree_path: '{root}'\n"
        f"head_session: {head}\nsessions:\n{sessions}",
        encoding="utf-8",
    )
    return root


def test_unbound_record_emits_once_then_cools_down(tmp_path):
    root = _record(tmp_path)
    payload = {"cwd": str(root)}
    text = bind_nudge.decide(payload, home=tmp_path)
    assert f"bind-session --worktree-dir={root}" in text
    assert bind_nudge.decide(payload, home=tmp_path) is None


def test_head_session_is_quiet(tmp_path):
    root = _record(tmp_path, head="session-a")
    assert bind_nudge.decide({"cwd": str(root)}, home=tmp_path) is None


def test_active_legacy_session_is_quiet(tmp_path):
    root = _record(
        tmp_path,
        sessions="  - session_id: session-a\n    state: active\n",
    )
    assert bind_nudge.decide({"cwd": str(root)}, home=tmp_path) is None


def test_handed_off_legacy_session_nudges(tmp_path):
    root = _record(
        tmp_path,
        sessions="  - session_id: session-a\n    state: handed-off\n",
    )
    assert bind_nudge.decide({"cwd": str(root)}, home=tmp_path) is not None
