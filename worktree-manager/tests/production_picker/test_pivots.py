"""Tests for the cross-plugin pivot-registry manifest schema + scanner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from worktree_manager.production_picker.picker_tui import pivots

#: Repo root (…/copilot-extensions), derived from this test's location:
#: worktree-manager/tests/production_picker/test_pivots.py -> parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _write(directory, name, data):
    path = directory / f"{name}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_discover_missing_dir_is_empty(tmp_path):
    assert pivots.discover_pivots(tmp_path / "does-not-exist") == []


def test_discover_empty_dir_is_empty(tmp_path):
    assert pivots.discover_pivots(tmp_path) == []


def test_preview_mode_suppresses_ambient_pivot_materialization(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("WORKTREE_MANAGER_PICKER_NO_PIVOT_MATERIALIZE", "1")
    monkeypatch.setattr(
        pivots,
        "_materialize_active_pivots",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    report = pivots.scan_pivot_registry(tmp_path)

    assert report.contributions == []
    assert calls == []


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
                "group": "group",
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
    assert p.group_field == "group"
    assert p.badge_fields == ("labels",)
    assert p.empty_hint == "No proposed tasks."
    assert [a.key for a in p.actions] == ["open", "action1"]
    assert p.actions[0].label == "Open"
    assert p.actions[1].confirm is True
    # External actions carry no internal verb.
    assert p.actions[0].internal is None


def test_state_root_file_visibility_is_lazy_and_configuration_gated(
    tmp_path,
    monkeypatch,
):
    manifests = tmp_path / "pivots"
    manifests.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    _write(manifests, "always", {"label": "Always", "list": ["always"]})
    _write(
        manifests,
        "configured",
        {
            "label": "Configured",
            "list": ["configured"],
            "visible_when": {"state_root_file": "workflows/sources.json"},
        },
    )
    calls = []
    monkeypatch.setattr(
        pivots,
        "_resolve_state_root_path",
        lambda: calls.append(True) or state_root,
    )

    assert [pivot.label for pivot in pivots.discover_pivots(manifests)] == ["Always"]
    assert len(calls) == 1

    sources = state_root / "workflows" / "sources.json"
    sources.parent.mkdir()
    sources.write_text("{}", encoding="utf-8")
    assert [pivot.label for pivot in pivots.discover_pivots(manifests)] == [
        "Always",
        "Configured",
    ]
    assert len(calls) == 2


def test_ungated_pivots_do_not_resolve_state_root(tmp_path, monkeypatch):
    _write(tmp_path, "always", {"label": "Always", "list": ["always"]})
    monkeypatch.setattr(
        pivots,
        "_resolve_state_root_path",
        lambda: pytest.fail("ungated discovery must not resolve the state root"),
    )

    assert [pivot.label for pivot in pivots.discover_pivots(tmp_path)] == ["Always"]


@pytest.mark.parametrize(
    "state_root_file",
    ["/absolute.json", "../escape.json", "C:/escape.json", ""],
)
def test_state_root_file_visibility_rejects_unsafe_paths(
    tmp_path,
    state_root_file,
):
    _write(
        tmp_path,
        "unsafe",
        {
            "label": "Unsafe",
            "list": ["unsafe"],
            "visible_when": {"state_root_file": state_root_file},
        },
    )

    assert pivots.discover_pivots(tmp_path) == []


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

    assert len(restored) == 1
    assert restored[0].startswith("agent-dispatch.")
    assert json.loads(
        (dest / "agent-dispatch.json").read_text(encoding="utf-8")
    )["label"] == "Local"  # untouched
    assert {p.label for p in pivots.discover_pivots(dest)} == {
        "Local",
        "Source",
    }


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


# ---- config_sections (B slice 2) ----------------------------------------


def test_parse_config_sections_minimal_and_full():
    """A `config_sections` entry needs a label + a run argv; key/confirm/
    description default sensibly and `source` is the manifest stem."""
    out = pivots.parse_config_sections(
        {
            "config_sections": [
                {"label": "SSH", "run": ["agent-ssh", "config"]},
                {
                    "key": "mcp",
                    "label": "MCP",
                    "run": ["agent-mcp", "menu", "--machine", "{machine}"],
                    "confirm": True,
                    "description": "MCP servers",
                },
            ]
        },
        name="net",
    )
    assert [s.label for s in out] == ["SSH", "MCP"]
    assert out[0].key == "net0"          # defaulted from stem + index
    assert out[0].source == "net"
    assert out[0].run == ("agent-ssh", "config")
    assert out[0].confirm is False
    assert out[1].key == "mcp"           # explicit key honored
    assert out[1].confirm is True
    assert out[1].description == "MCP servers"


def test_parse_config_sections_absent_is_empty():
    assert pivots.parse_config_sections({}, name="x") == ()


@pytest.mark.parametrize("bad", [
    {"config_sections": "nope"},                       # not an array
    {"config_sections": [42]},                          # entry not an object
    {"config_sections": [{"run": ["x"]}]},              # missing label
    {"config_sections": [{"label": " ", "run": ["x"]}]},  # blank label
    {"config_sections": [{"label": "L"}]},              # missing run
    {"config_sections": [{"label": "L", "run": []}]},   # empty run argv
    {"config_sections": [{"label": "L", "run": "x"}]},  # run not an array
])
def test_parse_config_sections_malformed_raises(bad):
    with pytest.raises(pivots.ManifestError):
        pivots.parse_config_sections(bad, name="x")


def test_discover_config_sections_independent_of_list(tmp_path):
    """Config sections are discovered from a manifest that carries no `list`
    pivot, in stable (filename, declared) order; a malformed manifest is
    skipped, never fatal."""
    _write(tmp_path, "net", {"config_sections": [
        {"label": "SSH", "run": ["agent-ssh", "config"]},
    ]})
    _write(tmp_path, "mcp", {"config_sections": [
        {"label": "MCP", "run": ["agent-mcp", "menu"]},
    ]})
    _write(tmp_path, "bad", {"config_sections": [{"label": "X"}]})  # no run
    out = pivots.discover_config_sections(tmp_path)
    assert [(s.source, s.label) for s in out] == [("mcp", "MCP"), ("net", "SSH")]


def test_discover_config_sections_missing_dir_is_empty(tmp_path):
    assert pivots.discover_config_sections(tmp_path / "nope") == []


# ---- shipped-manifest contract guard ------------------------------------


def _shipped_manifests():
    """Every `plugins/*/pivots/*.json` manifest shipped in the repo."""
    root = _REPO_ROOT / "plugins"
    return sorted(root.glob("*/pivots/*.json")) if root.is_dir() else []


@pytest.mark.guard
def test_shipped_pivot_manifests_are_contract_valid():
    """Every manifest a plugin ships in its `pivots/` dir must parse cleanly
    against the current contract -- a `list` pivot (if declared), plus any
    `worktree_actions` / `config_sections`. This guards the zero-engine-change
    C-slice list pivots (Bridges, CodeSpaces, Tasks) from silently shipping a
    malformed manifest that the picker would then skip at runtime."""
    manifests = _shipped_manifests()
    assert manifests, "expected at least one shipped pivot manifest"
    for path in manifests:
        data = json.loads(path.read_text(encoding="utf-8"))
        # A manifest may carry a list pivot, worktree actions, config sections,
        # or any combination -- each parser must accept it without raising.
        if "list" in data:
            pivots.parse_manifest(data, name=path.stem, source_path=str(path))
        pivots.parse_worktree_actions(data, name=path.stem)
        pivots.parse_config_sections(data, name=path.stem)


@pytest.mark.guard
def test_shipped_list_pivots_have_runnable_argv():
    """Each shipped `list` pivot names a non-empty argv whose first token looks
    like a plugin binstub on PATH (not a placeholder), so the picker's runtime
    can resolve and spawn it."""
    for path in _shipped_manifests():
        data = json.loads(path.read_text(encoding="utf-8"))
        if "list" not in data:
            continue
        reg = pivots.parse_manifest(data, name=path.stem, source_path=str(path))
        assert reg.list_cmd, f"{path.name}: empty list argv"
        assert reg.list_cmd[0].startswith("agent-"), (
            f"{path.name}: list argv[0] {reg.list_cmd[0]!r} is not a plugin binstub"
        )


# ---- D1: declarative columns + summary + payload normalization ----------------


def test_columns_absent_defaults_empty(tmp_path):
    _write(tmp_path, "tasks", {"label": "Tasks", "list": ["agent-dispatch", "inbox"]})
    [p] = pivots.discover_pivots(tmp_path)
    assert p.columns == ()
    assert p.summary_template is None


def test_parse_columns_and_summary(tmp_path):
    _write(
        tmp_path,
        "codespaces",
        {
            "label": "CodeSpaces",
            "list": ["agent-codespaces", "pool", "--picker-json"],
            "columns": [
                {"key": "name", "header": "codespace", "width": 30},
                {"key": "disposition", "header": "state", "width": 8, "style": "yellow"},
                {"key": "cores", "align": "r"},
            ],
            "summary": "{spent_cores}/{total_cores} cores \u00b7 {headroom_cores} free",
        },
    )
    [p] = pivots.discover_pivots(tmp_path)
    assert [c.key for c in p.columns] == ["name", "disposition", "cores"]
    assert p.columns[0].header == "codespace"
    assert p.columns[0].width == 30
    assert p.columns[0].align == "l"          # default
    assert p.columns[1].style == "yellow"
    assert p.columns[2].header == "cores"     # header defaults to key
    assert p.columns[2].align == "r"
    assert p.columns[2].width is None         # unset => renderer-sized
    assert p.summary_template == "{spent_cores}/{total_cores} cores \u00b7 {headroom_cores} free"


def test_bad_column_sinks_only_its_manifest(tmp_path):
    _write(tmp_path, "ok", {"label": "Ok", "list": ["agent-x", "y"]})
    _write(
        tmp_path,
        "bad_width",
        {"label": "Bad", "list": ["agent-x", "y"],
         "columns": [{"key": "a", "width": 0}]},
    )
    _write(
        tmp_path,
        "bad_align",
        {"label": "Bad2", "list": ["agent-x", "y"],
         "columns": [{"key": "a", "align": "middle"}]},
    )
    _write(
        tmp_path,
        "bad_keyless",
        {"label": "Bad3", "list": ["agent-x", "y"], "columns": [{"header": "x"}]},
    )
    names = {p.name for p in pivots.discover_pivots(tmp_path)}
    assert names == {"ok"}          # the three malformed manifests are skipped


def test_non_string_summary_is_rejected(tmp_path):
    with pytest.raises(pivots.ManifestError):
        pivots.parse_manifest(
            {"label": "X", "list": ["a"], "summary": {"not": "a string"}},
            name="x", source_path="x",
        )


def test_parse_list_payload_bare_array_backcompat():
    rows, summary = pivots.parse_list_payload(
        [{"id": "1"}, {"id": "2"}, "junk", 7]
    )
    assert rows == [{"id": "1"}, {"id": "2"}]      # non-dict rows dropped
    assert summary == {}                            # no summary in array form


def test_parse_list_payload_object_form():
    rows, summary = pivots.parse_list_payload(
        {"entries": [{"id": "1"}], "summary": {"headroom_cores": 16}}
    )
    assert rows == [{"id": "1"}]
    assert summary == {"headroom_cores": 16}


def test_parse_list_payload_malformed_degrades_empty():
    # A non-list entries / non-dict summary must not raise.
    rows, summary = pivots.parse_list_payload({"entries": "nope", "summary": 5})
    assert rows == []
    assert summary == {}
    assert pivots.parse_list_payload(None) == ([], {})


def test_scope_defaults_machine_and_parses_account(tmp_path):
    _write(tmp_path, "m", {"label": "M", "list": ["agent-x", "y"]})
    [p] = pivots.discover_pivots(tmp_path)
    assert p.scope == "machine"
    assert p.account_scoped is False
    _write(tmp_path, "m", {"label": "M", "list": ["agent-x", "y"], "scope": "account"})
    [p] = pivots.discover_pivots(tmp_path)
    assert p.scope == "account"
    assert p.account_scoped is True


def test_bad_scope_sinks_only_its_manifest(tmp_path):
    _write(tmp_path, "ok", {"label": "Ok", "list": ["agent-x", "y"]})
    _write(tmp_path, "bad", {"label": "Bad", "list": ["agent-x", "y"], "scope": "planet"})
    assert {p.name for p in pivots.discover_pivots(tmp_path)} == {"ok"}


def test_group_field_parses_from_entry(tmp_path):
    _write(tmp_path, "cs", {"label": "CS", "list": ["agent-x", "y"],
                            "entry": {"group": "group"}})
    [p] = pivots.discover_pivots(tmp_path)
    assert p.group_field == "group"
    _write(tmp_path, "cs", {"label": "CS", "list": ["agent-x", "y"]})
    [p] = pivots.discover_pivots(tmp_path)
    assert p.group_field is None


def test_column_palette_parses(tmp_path):
    _write(tmp_path, "cs", {"label": "CS", "list": ["agent-x", "y"],
        "columns": [{"key": "status", "palette": "state"}, {"key": "cores"}]})
    [p] = pivots.discover_pivots(tmp_path)
    assert p.columns[0].palette == "state"
    assert p.columns[1].palette is None


def test_bad_column_palette_sinks_manifest(tmp_path):
    _write(tmp_path, "ok", {"label": "Ok", "list": ["agent-x", "y"]})
    _write(tmp_path, "bad", {"label": "Bad", "list": ["agent-x", "y"],
        "columns": [{"key": "s", "palette": 5}]})
    assert {p.name for p in pivots.discover_pivots(tmp_path)} == {"ok"}


def test_stream_defaults_off_and_parses_true(tmp_path):
    _write(tmp_path, "m", {"label": "M", "list": ["agent-x", "y"]})
    [p] = pivots.discover_pivots(tmp_path)
    assert p.stream is False
    assert p.subscribe is False
    _write(tmp_path, "m", {"label": "M", "list": ["agent-x", "y"],
                           "stream": True, "subscribe": True})
    [p] = pivots.discover_pivots(tmp_path)
    assert p.stream is True
    assert p.subscribe is True


@pytest.mark.parametrize("bad", [{"stream": "yes"}, {"subscribe": 1}])
def test_bad_stream_flags_sink_only_their_manifest(tmp_path, bad):
    _write(tmp_path, "ok", {"label": "Ok", "list": ["agent-x", "y"]})
    _write(tmp_path, "bad", {"label": "Bad", "list": ["agent-x", "y"], **bad})
    assert {p.name for p in pivots.discover_pivots(tmp_path)} == {"ok"}


def test_action_when_defaults_none_and_parses(tmp_path):
    _write(tmp_path, "m", {"label": "M", "list": ["agent-x", "y"], "actions": [
        {"label": "Always", "run": ["do"]},
        {"label": "Release", "run": ["rel", "{id}"], "when": {"disposition": "in-use"}},
    ]})
    [p] = pivots.discover_pivots(tmp_path)
    assert p.actions[0].when is None
    assert p.actions[1].when == {"disposition": "in-use"}


def test_bad_action_when_sinks_only_its_manifest(tmp_path):
    _write(tmp_path, "ok", {"label": "Ok", "list": ["agent-x", "y"]})
    _write(tmp_path, "bad", {"label": "Bad", "list": ["agent-x", "y"],
        "actions": [{"label": "X", "run": ["do"], "when": "in-use"}]})
    assert {p.name for p in pivots.discover_pivots(tmp_path)} == {"ok"}


def test_entry_matches_gate():
    assert pivots.entry_matches(None, {"disposition": "in-use"}) is True
    assert pivots.entry_matches({}, {"disposition": "in-use"}) is True
    # single value (case-insensitive) + record miss.
    assert pivots.entry_matches({"disposition": "in-use"}, {"disposition": "IN-USE"}) is True
    assert pivots.entry_matches({"disposition": "in-use"}, {"disposition": "stale"}) is False
    # list of allowed values; every field must match.
    assert pivots.entry_matches({"status": ["stale", "stopped"]}, {"status": "STALE"}) is True
    assert pivots.entry_matches(
        {"status": ["stale"], "use": "free"}, {"status": "stale", "use": "in-use"}) is False


def test_action_when_matches_via_worktree_action_wrapper():
    # The generalized matcher still backs the WorktreeAction gate.
    a = pivots.WorktreeAction(key="k", label="L", run=("x",), source="s",
                              when={"state": "FINAL"})
    assert pivots.worktree_action_matches(a, {"state": "FINAL"}) is True
    assert pivots.worktree_action_matches(a, {"state": "WIP"}) is False


def test_action_progress_defaults_off_and_parses(tmp_path):
    _write(tmp_path, "m", {"label": "M", "list": ["agent-x", "y"], "actions": [
        {"label": "Info", "run": ["do"]},
        {"label": "Recycle", "run": ["rec", "{id}"], "progress": True},
    ]})
    [p] = pivots.discover_pivots(tmp_path)
    assert p.actions[0].progress is False
    assert p.actions[1].progress is True


def test_bad_action_progress_sinks_only_its_manifest(tmp_path):
    _write(tmp_path, "ok", {"label": "Ok", "list": ["agent-x", "y"]})
    _write(tmp_path, "bad", {"label": "Bad", "list": ["agent-x", "y"],
        "actions": [{"label": "X", "run": ["do"], "progress": "yes"}]})
    assert {p.name for p in pivots.discover_pivots(tmp_path)} == {"ok"}


# ---- A5: steering-seam form/card action kinds -------------------------------


def test_form_action_parses(tmp_path):
    _write(tmp_path, "m", {"label": "M", "list": ["agent-x", "y"], "actions": [
        {"key": "steer", "label": "Steer", "kind": "form",
         "fields_from": "card.request_input",
         "title_from": "card.title", "body_from": "card.status",
         "run": ["agent-dispatch", "steer", "submit", "{task_id}",
                 "--field", "decision={field.decision}"],
         "when": {"awaiting_steer": True}},
    ]})
    [p] = pivots.discover_pivots(tmp_path)
    a = p.actions[0]
    assert a.form == {
        "fields_from": "card.request_input",
        "title_from": "card.title",
        "body_from": "card.status",
    }
    assert a.card is None and a.internal is None
    assert a.when == {"awaiting_steer": True}
    assert a.run[:4] == ("agent-dispatch", "steer", "submit", "{task_id}")


def test_form_action_requires_fields_from(tmp_path):
    _write(tmp_path, "ok", {"label": "Ok", "list": ["agent-x", "y"]})
    _write(tmp_path, "bad", {"label": "Bad", "list": ["agent-x", "y"], "actions": [
        {"label": "Steer", "kind": "form", "run": ["x"]},
    ]})
    # The missing fields_from sinks only the bad manifest.
    assert {p.name for p in pivots.discover_pivots(tmp_path)} == {"ok"}


def test_form_action_requires_run(tmp_path):
    with pytest.raises(pivots.ManifestError):
        pivots.parse_manifest(
            {"label": "M", "list": ["x"], "actions": [
                {"label": "Steer", "kind": "form", "fields_from": "card.request_input"}]},
            name="m", source_path="x")


def test_card_action_parses_with_defaults(tmp_path):
    _write(tmp_path, "m", {"label": "M", "list": ["agent-x", "y"], "actions": [
        {"key": "card", "label": "View card", "kind": "card",
         "when": {"awaiting_steer": True}},
    ]})
    [p] = pivots.discover_pivots(tmp_path)
    a = p.actions[0]
    assert a.card == {
        "title_from": "card.title",
        "status_from": "card.status",
        "link_from": "card.link",
        "body_from": "card.body",
    }
    assert a.form is None and a.internal is None
    assert a.run == ()


def test_card_action_custom_paths(tmp_path):
    [p] = [pivots.parse_manifest(
        {"label": "M", "list": ["x"], "actions": [
            {"label": "C", "kind": "card", "body_from": "detail.text",
             "title_from": "detail.head"}]},
        name="m", source_path="x")]
    a = p.actions[0]
    assert a.card["body_from"] == "detail.text"
    assert a.card["title_from"] == "detail.head"
    # Unspecified paths still default.
    assert a.card["status_from"] == "card.status"


def test_bad_from_path_type_is_manifest_error():
    with pytest.raises(pivots.ManifestError):
        pivots.parse_manifest(
            {"label": "M", "list": ["x"], "actions": [
                {"label": "C", "kind": "card", "body_from": 123}]},
            name="m", source_path="x")


def test_resolve_path():
    rec = {"task_id": "abc", "card": {"body": "B",
           "request_input": [{"name": "feedback", "type": "textarea"}]}}
    assert pivots.resolve_path(rec, "task_id") == "abc"
    assert pivots.resolve_path(rec, "card.body") == "B"
    assert pivots.resolve_path(rec, "card.request_input") == [
        {"name": "feedback", "type": "textarea"}]
    # Missing key or non-mapping mid-walk => None, never raises.
    assert pivots.resolve_path(rec, "card.nope") is None
    assert pivots.resolve_path(rec, "task_id.deeper") is None
    assert pivots.resolve_path(rec, None) is None
    assert pivots.resolve_path(None, "card.body") is None


def test_format_form_template_substitutes_fields_and_ctx():
    template = ["agent-dispatch", "steer", "submit", "{task_id}",
                "--field", "feedback={field.feedback}",
                "--field", "decision={field.decision}"]
    out = pivots.format_form_template(
        template,
        {"task_id": "T123"},
        {"feedback": "looks good", "decision": "post-approved"},
    )
    assert out == ["agent-dispatch", "steer", "submit", "T123",
                   "--field", "feedback=looks good",
                   "--field", "decision=post-approved"]


def test_format_form_template_missing_field_is_empty():
    out = pivots.format_form_template(
        ["--field", "decision={field.decision}"], {}, {})
    assert out == ["--field", "decision="]


def test_format_form_template_field_value_is_literal_no_injection():
    # A field value that itself contains a brace token is inserted literally --
    # it must NOT be re-scanned/substituted (no token injection).
    out = pivots.format_form_template(
        ["--field", "feedback={field.feedback}"],
        {"task_id": "SECRET"},
        {"feedback": "use {task_id} carefully"},
    )
    assert out == ["--field", "feedback=use {task_id} carefully"]


def test_format_form_template_fields_expansion():
    # `{fields}` as a standalone arg expands to one --field pair per collected
    # field (the general "submit all my answers" form).
    out = pivots.format_form_template(
        ["agent-dispatch", "steer", "submit", "{task_id}", "{fields}"],
        {"task_id": "T1"},
        {"feedback": "ok", "decision": "revise"},
    )
    assert out == [
        "agent-dispatch", "steer", "submit", "T1",
        "--field", "feedback=ok",
        "--field", "decision=revise",
    ]


def test_format_form_template_multichoice_list_json_encoded():
    # A list value (multichoice) is JSON-encoded so comma'd members survive.
    out = pivots.format_form_template(
        ["{fields}"], {}, {"tags": ["perf", "a, b"]})
    assert out == ["--field", 'tags=["perf", "a, b"]']
    # Same encoding via the per-name token.
    out2 = pivots.format_form_template(
        ["--field", "tags={field.tags}"], {}, {"tags": ["perf", "api"]})
    assert out2 == ["--field", 'tags=["perf", "api"]']
