"""Direct coverage for plugin-owned Picker pivot visibility."""

from __future__ import annotations

import json

from agent_worktrees.picker_tui import pivots


def _write(directory, name, data):
    path = directory / f"{name}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_configured_pivot_requires_state_root_file(tmp_path, monkeypatch):
    manifests = tmp_path / "pivots"
    manifests.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    _write(
        manifests,
        "configured",
        {
            "label": "Configured",
            "list": ["configured"],
            "visible_when": {"state_root_file": "feature/config.json"},
        },
    )
    calls = []
    monkeypatch.setattr(
        pivots,
        "_resolve_state_root_path",
        lambda: calls.append(True) or state_root,
    )

    assert pivots.discover_pivots(manifests) == []
    assert len(calls) == 1

    config = state_root / "feature" / "config.json"
    config.parent.mkdir()
    config.write_text("{}", encoding="utf-8")
    assert [pivot.label for pivot in pivots.discover_pivots(manifests)] == [
        "Configured"
    ]
    assert len(calls) == 2


def test_unbound_state_root_is_resolved_once(tmp_path, monkeypatch):
    for name in ("first", "second"):
        _write(
            tmp_path,
            name,
            {
                "label": name.title(),
                "list": [name],
                "visible_when": {"state_root_file": f"{name}.json"},
            },
        )
    calls = []
    monkeypatch.setattr(
        pivots,
        "_resolve_state_root_path",
        lambda: calls.append(True) or None,
    )

    assert pivots.discover_pivots(tmp_path) == []
    assert len(calls) == 1
