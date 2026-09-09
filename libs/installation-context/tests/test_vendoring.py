"""Tests for installation-context payload vendoring."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "tools" / "sync-installation-context.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("sync_installation_context", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_repo_vendored_copies_are_in_sync() -> None:
    module = _load_tool()
    assert module.verify() == []


def test_sync_repairs_missing_and_drifted_copies(tmp_path: Path) -> None:
    module = _load_tool()
    canonical = tmp_path / "libs" / "installation-context"
    plugins = tmp_path / "plugins"
    canonical.mkdir(parents=True)
    for name in (*module.FILES, *module.LEGACY_ENTRYPOINT_FILES):
        (canonical / name).write_text(f"{name}\n", encoding="utf-8")
    drifted = plugins / "plugin-a" / "scripts" / "installation-context"
    drifted.mkdir(parents=True)
    (drifted / module.FILES[0]).write_text("drifted\n", encoding="utf-8")

    module.REPO = tmp_path
    module.CANONICAL_DIR = canonical
    module.ADOPTERS = ("plugin-a", "plugin-b")
    module.LEGACY_ENTRYPOINT_ADOPTERS = ("plugin-a",)
    assert module.verify()
    written = module.sync()
    assert len(written) == len(module.FILES) * 2 + len(module.LEGACY_ENTRYPOINT_FILES)
    assert module.verify() == []
    if os.name != "nt":
        destination = plugins / "plugin-a" / "scripts" / "installation-context" / module.FILES[0]
        destination.chmod(0o600)
        assert any("mode differs" in problem for problem in module.verify())
        assert destination.relative_to(tmp_path).as_posix() in module.sync()
        assert module.verify() == []


def test_verify_flags_a_plugin_that_vendors_without_being_registered(
    tmp_path: Path,
) -> None:
    """A vendored copy outside ADOPTERS never receives foundation updates.

    The plugin still executes that copy, so the drift is invisible until the
    stale bytes fail. Verification must name it rather than report all-in-sync.
    """
    module = _load_tool()
    canonical = tmp_path / "libs" / "installation-context"
    plugins = tmp_path / "plugins"
    canonical.mkdir(parents=True)
    for name in (*module.FILES, *module.LEGACY_ENTRYPOINT_FILES):
        (canonical / name).write_text(f"{name}\n", encoding="utf-8")

    module.REPO = tmp_path
    module.CANONICAL_DIR = canonical
    module.ADOPTERS = ("plugin-a",)
    module.LEGACY_ENTRYPOINT_ADOPTERS = ()
    module.sync()
    assert module.verify() == []

    # A plugin that vendors the foundation but was never registered.
    (plugins / "plugin-unregistered" / "scripts" / "installation-context").mkdir(
        parents=True
    )
    assert module.unregistered_adopters() == ["plugin-unregistered"]
    problems = module.verify()
    assert any(
        "plugin-unregistered" in problem and "not" in problem for problem in problems
    ), problems
