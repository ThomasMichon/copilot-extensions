"""Tests for the cross-plugin pivot-registry manifest schema + scanner."""

from __future__ import annotations

import json

import pytest

from agent_worktrees.picker_tui import pivots


def _write(directory, name, data):
    path = directory / f"{name}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_discover_missing_dir_is_empty(tmp_path):
    assert pivots.discover_pivots(tmp_path / "does-not-exist") == []


def test_discover_empty_dir_is_empty(tmp_path):
    assert pivots.discover_pivots(tmp_path) == []


def test_parse_minimal_manifest_applies_defaults(tmp_path):
    _write(tmp_path, "tasks", {"label": "Tasks", "list": ["agent-dispatch", "inbox"]})
    [p] = pivots.discover_pivots(tmp_path)
    assert p.name == "tasks"
    assert p.label == "Tasks"
    assert p.after == "Worktrees"          # default position hint
    assert p.list_cmd == ("agent-dispatch", "inbox")
    assert p.id_field == "id"
    assert p.title_field == "title"
    assert p.worktree_field == "target_worktree"
    assert p.badge_fields == ()
    assert p.actions == ()
    assert p.kind == "registered"


def test_parse_full_manifest(tmp_path):
    _write(
        tmp_path,
        "tasks",
        {
            "label": "Tasks",
            "after": "Worktrees",
            "list": ["agent-dispatch", "inbox", "--machine", "{machine}"],
            "entry": {
                "id": "id",
                "title": "title",
                "worktree": "target_worktree",
                "subtitle": "repo_name",
                "badges": ["labels"],
            },
            "empty_hint": "No proposed tasks.",
            "actions": [
                {"key": "open", "label": "Open", "run": ["do", "{id}"]},
                {"label": "Abandon", "run": ["rm", "{id}"], "confirm": True},
            ],
        },
    )
    [p] = pivots.discover_pivots(tmp_path)
    assert p.list_cmd == ("agent-dispatch", "inbox", "--machine", "{machine}")
    assert p.subtitle_field == "repo_name"
    assert p.badge_fields == ("labels",)
    assert p.empty_hint == "No proposed tasks."
    assert [a.key for a in p.actions] == ["open", "action1"]
    assert p.actions[0].label == "Open"
    assert p.actions[1].confirm is True
    # External actions carry no internal verb.
    assert p.actions[0].internal is None


def test_parse_internal_action(tmp_path):
    # #1425: an internal (picker-navigation) action carries a verb, not a CLI.
    _write(
        tmp_path,
        "bridges",
        {
            "label": "Bridges",
            "list": ["agent-bridge", "list", "--json"],
            "entry": {"id": "id", "title": "title", "worktree": "worktree"},
            "actions": [
                {"key": "jump", "label": "Jump to host", "kind": "internal",
                 "verb": "jump-host", "args": ["{worktree}"]},
                {"key": "open", "label": "Open", "run": ["do", "{id}"]},
            ],
        },
    )
    [p] = pivots.discover_pivots(tmp_path)
    jump, ext = p.actions
    assert jump.internal == "jump-host"
    assert jump.run == ("{worktree}",)      # args become the template
    assert ext.internal is None             # external unchanged
    assert ext.run == ("do", "{id}")


def test_internal_action_without_verb_is_skipped(tmp_path):
    # A malformed internal action sinks only its manifest, never the picker.
    _write(tmp_path, "ok", {"label": "Ok", "list": ["x"]})
    _write(
        tmp_path,
        "bad",
        {
            "label": "Bad",
            "list": ["x"],
            "actions": [{"label": "Nav", "kind": "internal"}],  # no `verb`
        },
    )
    assert [p.name for p in pivots.discover_pivots(tmp_path)] == ["ok"]


def test_malformed_manifest_is_skipped_not_fatal(tmp_path):
    _write(tmp_path, "good", {"label": "Good", "list": ["x"]})
    (tmp_path / "broken.json").write_text("{ not json", encoding="utf-8")
    _write(tmp_path, "nolist", {"label": "NoList"})          # missing required `list`
    _write(tmp_path, "nolabel", {"list": ["x"]})             # missing required `label`
    found = pivots.discover_pivots(tmp_path)
    assert [p.name for p in found] == ["good"]


def test_discovery_is_sorted_by_filename(tmp_path):
    _write(tmp_path, "zzz", {"label": "Z", "list": ["z"]})
    _write(tmp_path, "aaa", {"label": "A", "list": ["a"]})
    assert [p.name for p in pivots.discover_pivots(tmp_path)] == ["aaa", "zzz"]


def test_env_override_selects_directory(tmp_path, monkeypatch):
    _write(tmp_path, "tasks", {"label": "Tasks", "list": ["x"]})
    monkeypatch.setenv(pivots.PIVOTS_DIR_ENV, str(tmp_path))
    [p] = pivots.discover_pivots()
    assert p.label == "Tasks"


def test_order_pivots_inserts_after_hint():
    builtins = ["Worktrees", "Maintenance", "Profiles"]
    reg = pivots.RegisteredPivot(
        name="tasks", label="Tasks", after="Worktrees",
        list_cmd=("x",), id_field="id", title_field="title",
        worktree_field=None, badge_fields=(), subtitle_field=None,
        empty_hint="", actions=(), source_path="x",
    )
    order = pivots.order_pivots(builtins, [reg])
    assert [d["label"] for d in order] == ["Worktrees", "Tasks", "Maintenance", "Profiles"]
    assert order[0]["kind"] == "worktrees"
    assert order[1]["kind"] == "registered"
    assert order[1]["pivot"] is reg


def test_order_pivots_unknown_after_appends():
    builtins = ["Worktrees", "Maintenance", "Profiles"]
    reg = pivots.RegisteredPivot(
        name="tasks", label="Tasks", after="Nonexistent",
        list_cmd=("x",), id_field="id", title_field="title",
        worktree_field=None, badge_fields=(), subtitle_field=None,
        empty_hint="", actions=(), source_path="x",
    )
    order = pivots.order_pivots(builtins, [reg])
    assert [d["label"] for d in order] == ["Worktrees", "Maintenance", "Profiles", "Tasks"]


def test_format_template_substitutes_and_preserves():
    out = pivots.format_template(
        ["run", "--id", "{id}", "--machine", "{machine}", "--flag"],
        {"id": "t9", "machine": "host-a"},
    )
    assert out == ["run", "--id", "t9", "--machine", "host-a", "--flag"]


def test_format_template_unknown_token_is_empty():
    out = pivots.format_template(["x", "{missing}"], {"id": "t9"})
    assert out == ["x", ""]


def test_format_template_none_becomes_empty():
    out = pivots.format_template(["x", "{worktree}"], {"worktree": None})
    assert out == ["x", ""]


@pytest.mark.parametrize("bad_list", [None, "notalist", [], {}])
def test_list_must_be_nonempty_argv(tmp_path, bad_list):
    _write(tmp_path, "b", {"label": "B", "list": bad_list})
    assert pivots.discover_pivots(tmp_path) == []


# -- ensure_pivots: self-heal the runtime dir from the marketplace tree (#2180) --


def _plugin_manifest(root, marketplace, plugin, name, data):
    """Write ``<root>/<marketplace>/<plugin>/pivots/<name>.json`` and return it."""
    directory = root / marketplace / plugin / "pivots"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_ensure_pivots_restores_missing_manifest(tmp_path):
    src_root = tmp_path / "installed-plugins"
    dest = tmp_path / "pivots"
    manifest = {"label": "Tasks", "list": ["agent-dispatch", "inbox"]}
    _plugin_manifest(src_root, "copilot-extensions", "agent-dispatch", "agent-dispatch", manifest)

    restored = pivots.ensure_pivots(base=dest, plugins_root=src_root)

    assert restored == ["agent-dispatch.json"]
    assert (dest / "agent-dispatch.json").is_file()
    # And the restored manifest is now discoverable as a real pivot.
    [p] = pivots.discover_pivots(dest)
    assert p.label == "Tasks"


def test_ensure_pivots_does_not_clobber_existing(tmp_path):
    src_root = tmp_path / "installed-plugins"
    dest = tmp_path / "pivots"
    dest.mkdir()
    # A locally-present manifest (e.g. one a newer contributor install wrote).
    (dest / "agent-dispatch.json").write_text(
        json.dumps({"label": "Local", "list": ["x"]}), encoding="utf-8"
    )
    _plugin_manifest(
        src_root, "copilot-extensions", "agent-dispatch", "agent-dispatch",
        {"label": "Source", "list": ["y"]},
    )

    restored = pivots.ensure_pivots(base=dest, plugins_root=src_root)

    assert restored == []
    [p] = pivots.discover_pivots(dest)
    assert p.label == "Local"  # untouched


def test_ensure_pivots_missing_source_root_is_noop(tmp_path):
    dest = tmp_path / "pivots"
    assert pivots.ensure_pivots(base=dest, plugins_root=tmp_path / "nope") == []
    assert not dest.exists()  # nothing to restore -> dest not even created


def test_ensure_pivots_is_idempotent(tmp_path):
    src_root = tmp_path / "installed-plugins"
    dest = tmp_path / "pivots"
    _plugin_manifest(
        src_root, "copilot-extensions", "agent-dispatch", "agent-dispatch",
        {"label": "Tasks", "list": ["agent-dispatch", "inbox"]},
    )

    assert pivots.ensure_pivots(base=dest, plugins_root=src_root) == ["agent-dispatch.json"]
    # Second pass: already present, nothing restored.
    assert pivots.ensure_pivots(base=dest, plugins_root=src_root) == []


def test_ensure_pivots_restores_multiple_plugins(tmp_path):
    src_root = tmp_path / "installed-plugins"
    dest = tmp_path / "pivots"
    _plugin_manifest(
        src_root, "copilot-extensions", "agent-dispatch", "agent-dispatch",
        {"label": "Tasks", "list": ["agent-dispatch", "inbox"]},
    )
    _plugin_manifest(
        src_root, "copilot-extensions", "agent-bridge", "bridges",
        {"label": "Bridges", "list": ["agent-bridge", "list"]},
    )

    restored = pivots.ensure_pivots(base=dest, plugins_root=src_root)

    assert set(restored) == {"agent-dispatch.json", "bridges.json"}
    assert {p.label for p in pivots.discover_pivots(dest)} == {"Tasks", "Bridges"}


def test_installed_plugins_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv(pivots.PLUGINS_ROOT_ENV, str(tmp_path / "custom"))
    assert pivots.installed_plugins_dir() == tmp_path / "custom"
