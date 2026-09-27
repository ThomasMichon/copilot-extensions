"""Regression tests for the "no sibling agent-* plugin writes tracking YAML
directly" structural guard (agent-worktrees-authoritative-daemon effort,
Phase 4).

``find_violations``/``_check_file`` accept an explicit repo root, so most
cases are plain unit tests against a throwaway directory tree (no
subprocess, no git). One end-to-end smoke test drives the real script as a
subprocess against the actual repo checkout, confirming it stays clean --
mirrors test_check_no_agent_machines_packages.py's own pattern.

Run:  python -m pytest tools/test_check_no_sibling_tracking_writes.py
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check-no-sibling-tracking-writes.py"
REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("check_no_sibling_tracking_writes", SCRIPT)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _write_plugin_file(root: Path, plugin: str, rel: str, content: str) -> Path:
    path = root / "plugins" / plugin / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_clean_tree_has_no_violations(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "def hello():\n    return 1\n",
    )
    assert guard.find_violations(tmp_path) == []


def test_read_only_accessor_is_not_flagged(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def get(wt_id):\n"
        "    return tracking.load_record_by_id(wt_id)\n",
    )
    assert guard.find_violations(tmp_path) == []


def test_flags_direct_call_via_module_import(tmp_path):
    path = _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def write(record, wt_path):\n"
        "    tracking.save_record(record, wt_path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert violations[0].path == path
    assert "save_record" in violations[0].detail


def test_flags_direct_from_import_of_write_function(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import save_record\n"
        "\n"
        "def write(record, wt_path):\n"
        "    save_record(record, wt_path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_flags_import_from_owning_submodule(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees.tracking_claims import add_follow_up\n"
        "\n"
        "def note(record, summary):\n"
        "    add_follow_up(record, summary)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "add_follow_up" in violations[0].detail


def test_owning_plugin_itself_is_never_scanned(tmp_path):
    # agent-worktrees is exempt: it's the module that OWNS these writes.
    _write_plugin_file(
        tmp_path, "agent-worktrees", "src/agent_worktrees/tracking.py",
        "def save_record(record, path):\n    pass\n",
    )
    assert guard.find_violations(tmp_path) == []


def test_aliased_module_import_is_still_caught(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking as wt_tracking\n"
        "\n"
        "def write(record, path):\n"
        "    wt_tracking.stamp_mux_live(\"wt-1\", True)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "stamp_mux_live" in violations[0].detail


def test_package_root_aliased_import_is_caught(tmp_path):
    # `import agent_worktrees as aw; aw.tracking.save_record(...)` --
    # a two-level attribute chain off a package-root alias, not a module
    # alias -- must be caught just like the single-level module-alias form.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import agent_worktrees as aw\n"
        "\n"
        "def write(record, path):\n"
        "    aw.tracking.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_unaliased_package_root_import_is_caught(tmp_path):
    # The unaliased equivalent: `import agent_worktrees;
    # agent_worktrees.tracking.save_record(...)`.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import agent_worktrees\n"
        "\n"
        "def write(record, path):\n"
        "    agent_worktrees.tracking.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_tracking_controller_relations_reexport_is_caught(tmp_path):
    # tracking_controller_relations.py re-exports save_record (a thin
    # `from .tracking import save_record as impl; impl(...)` wrapper) --
    # importing that module's own copy must be caught identically to
    # importing tracking.py's directly.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking_controller_relations\n"
        "\n"
        "def write(record, path):\n"
        "    tracking_controller_relations.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_dotted_import_without_alias_binds_the_package_not_the_submodule(tmp_path):
    # `import agent_worktrees.tracking` (NO `as`) is Python-bound to the
    # top-level name `agent_worktrees`, never a bare `tracking` -- the
    # submodule is only reachable via the two-level
    # `agent_worktrees.tracking...` chain. A prior version of this guard
    # mismodeled this shape as a `tracking` module-alias binding, which
    # both mis-caught an unrelated bare `tracking.save_record(...)` name
    # collision and MISSED this actual valid import shape.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import agent_worktrees.tracking\n"
        "\n"
        "def write(record, path):\n"
        "    agent_worktrees.tracking.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_controller_relation_functions_are_denylisted(tmp_path):
    # backfill_legacy_controller_relations / set_controller_relation /
    # end_controller_relation / remove_controller_relation persist records
    # in tracking_controller_relations.py and are ALSO re-exported by
    # tracking.py itself -- both import paths must be caught.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def write(record, **kw):\n"
        "    tracking.set_controller_relation(record, **kw)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "set_controller_relation" in violations[0].detail


def test_load_or_create_anchor_record_is_denylisted(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking_claims\n"
        "\n"
        "def write(*a, **kw):\n"
        "    tracking_claims.load_or_create_anchor_record(*a, **kw)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "load_or_create_anchor_record" in violations[0].detail


def test_real_repo_checkout_is_clean():
    """End-to-end smoke test: the real, live repo tree must never trip this
    guard -- confirms the script (not just find_violations()) exits 0
    against the actual checkout, matching every sibling plugin's true
    (all read-only) tracking usage this effort's own survey found."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
