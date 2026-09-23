"""Regression tests for the picker preview flow (``--demo``/``--preview``):
render the REAL production Picker against deterministic mock data, not a
separate stand-in app -- see ``preview.py``'s module docstring for the two
injections (fake-engine worktree data + a manifest-injected mock pivot) this
composes.
"""

from __future__ import annotations

import json
import sys

import pytest

from worktree_manager import __main__ as cli
from worktree_manager import preview as preview_mod


@pytest.fixture(autouse=True)
def _reset_preview_state(monkeypatch):
    """Isolate each test's engine-command/env overrides and temp pivot dir.

    ``enable_preview_mode()`` deliberately mutates process-global state (an
    env var override + ``engine_client``'s module-level command) for the
    remainder of a real, one-shot ``worktree-manager --demo`` invocation --
    correct there, but it must never leak from one test into the next in the
    same pytest process. It writes directly to ``os.environ`` (not through
    ``monkeypatch``), so this fixture must explicitly clear both env vars
    on BOTH sides of the test, not rely on monkeypatch's own auto-restore.
    """
    import os

    def _clear():
        os.environ.pop(preview_mod.PIVOTS_DIR_ENV, None)
        os.environ.pop(preview_mod._NO_MATERIALIZE_ENV, None)
        preview_mod.set_engine_command(None)

    _clear()
    monkeypatch.setattr(preview_mod, "_active_tmp_dir", None)
    yield
    preview_mod._active_tmp_dir = None
    _clear()


class TestPickerPreviewDispatch:
    """``_cmd_picker``'s ``--demo``/``--preview`` branch must engage preview
    mode and reuse the SAME real production-Picker dispatch every other mode
    uses -- never a separate app, and never gated by the ordinary
    registered-project/engine-availability checks (preview mode is meant to
    work with zero live setup beyond the named project's own identity)."""

    def test_demo_flag_enables_preview_and_defaults_project(self, monkeypatch):
        calls = []
        monkeypatch.setattr(preview_mod, "enable_preview_mode", lambda: calls.append(True))
        seen = {}
        monkeypatch.setattr(
            cli, "_run_production_picker",
            lambda project: (seen.update(project=project), 0)[1])
        # Must not consult the ordinary project registry/engine-availability
        # gates at all in preview mode.
        monkeypatch.setattr(
            cli, "build_projects",
            lambda: (_ for _ in ()).throw(AssertionError("must not be called")))
        monkeypatch.setattr(
            cli, "engine_available",
            lambda: (_ for _ in ()).throw(AssertionError("must not be called")))

        rc = cli._cmd_picker(["--demo"])

        assert rc == 0
        assert calls == [True]
        assert seen["project"] == preview_mod.DEMO_PROJECT

    def test_preview_flag_is_an_alias_for_demo(self, monkeypatch):
        calls = []
        monkeypatch.setattr(preview_mod, "enable_preview_mode", lambda: calls.append(True))
        monkeypatch.setattr(cli, "_run_production_picker", lambda project: 0)

        rc = cli._cmd_picker(["--preview"])

        assert rc == 0
        assert calls == [True]

    def test_demo_mode_still_honors_an_explicit_project_positional(self, monkeypatch):
        monkeypatch.setattr(preview_mod, "enable_preview_mode", lambda: None)
        seen = {}
        monkeypatch.setattr(
            cli, "_run_production_picker",
            lambda project: (seen.update(project=project), 0)[1])

        rc = cli._cmd_picker(["--demo", "my-real-project"])

        assert rc == 0
        assert seen["project"] == "my-real-project"

    def test_demo_screenshot_reuses_the_real_capture_path(self, monkeypatch):
        """Screenshot-under-``--demo`` must go through ``runner.capture`` --
        the actual production Picker's capture path -- not a separate
        ``picker_app``-based SVG renderer."""
        monkeypatch.setattr(preview_mod, "enable_preview_mode", lambda: None)
        from worktree_manager.production_picker import runner
        seen = {}

        def fake_capture(project, **kwargs):
            seen.update(project=project, kwargs=kwargs)
            return {"svg": "<svg/>", "text": "text", "ansi": "ansi"}

        monkeypatch.setattr(runner, "capture", fake_capture)

        rc = cli._cmd_picker(["screenshot", "--demo", "--format", "text"])

        assert rc == 0
        assert seen["project"] == preview_mod.DEMO_PROJECT


class TestEnablePreviewMode:
    """Unit coverage of the two injections themselves, independent of the
    Textual render/capture path."""

    def test_sets_engine_command_to_the_fake_engine(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            preview_mod, "set_engine_command", lambda cmd: captured.update(cmd=cmd))

        preview_mod.enable_preview_mode()

        assert captured["cmd"] == [sys.executable, "-m", "worktree_manager.demo_engine"]

    def test_materializes_a_schema_less_operator_manifest(self, monkeypatch):
        preview_mod.enable_preview_mode()

        import os
        pivots_dir = os.environ[preview_mod.PIVOTS_DIR_ENV]
        manifest_path = None
        for name in os.listdir(pivots_dir):
            if name.endswith(".json"):
                manifest_path = os.path.join(pivots_dir, name)
        assert manifest_path is not None
        data = json.loads(open(manifest_path, encoding="utf-8").read())

        # No schema_version -> "operator" class: always active, no plugin/
        # root attribution required (see pivot_registry_scan.classify()).
        assert "schema_version" not in data
        assert data["label"] == "Demo Queue"
        assert data["list"][0] == sys.executable
        assert data["list"][1:] == ["-m", "worktree_manager.demo_pivot", "list"]

    def test_disables_real_plugin_pivot_materialization(self, monkeypatch):
        import os

        preview_mod.enable_preview_mode()

        assert os.environ[preview_mod._NO_MATERIALIZE_ENV] == "1"

    def test_idempotent_reuses_the_same_temp_directory(self):
        import os

        preview_mod.enable_preview_mode()
        first = os.environ[preview_mod.PIVOTS_DIR_ENV]
        preview_mod.enable_preview_mode()
        second = os.environ[preview_mod.PIVOTS_DIR_ENV]

        assert first == second


class TestDemoPivotFixture:
    def test_list_verb_emits_a_json_array_matching_the_manifest_columns(self):
        from worktree_manager import demo_pivot

        rows = demo_pivot.entries()

        assert rows  # non-empty, deterministic fixture
        for row in rows:
            assert {"id", "title", "state", "owner", "claims_summary"} <= row.keys()

    def test_main_list_prints_valid_json_to_stdout(self, capsys):
        from worktree_manager import demo_pivot

        rc = demo_pivot.main(["list"])

        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out == demo_pivot.entries()

    def test_main_unknown_verb_errors_without_raising(self, capsys):
        from worktree_manager import demo_pivot

        rc = demo_pivot.main(["bogus"])

        assert rc == 2
        out = json.loads(capsys.readouterr().out)
        assert "error" in out
