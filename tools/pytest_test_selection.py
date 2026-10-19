"""Union exact nodes, guards and whole changed files after collection.

A whole-file request covers every current item, including replacements for
retired baseline node IDs. Missing nodes outside those files still fail closed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from plugin_test_selection import load_selection, selection_diagnostic


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--exact-selection", type=Path)
    parser.addoption("--selection-plugin")


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    path = config.getoption("--exact-selection")
    if path is None:
        return
    plugin = config.getoption("--selection-plugin")
    if not isinstance(plugin, str) or not plugin.strip():
        raise pytest.UsageError("--exact-selection requires a nonempty --selection-plugin")
    try:
        selection = load_selection(path, plugin, Path.cwd())
    except (OSError, ValueError) as error:
        raise pytest.UsageError(f"invalid exact test selection: {error}") from error
    available = {item.nodeid for item in items}
    files = set(selection.files)
    missing = {
        node for node in set(selection.nodes) - available
        if node.split("::", 1)[0] not in files
    }
    if missing:
        raise pytest.UsageError(selection_diagnostic("selected test nodes were not collected", missing))
    empty_files = files - {item.nodeid.split("::", 1)[0] for item in items}
    if empty_files:
        raise pytest.UsageError(selection_diagnostic("selected test files collected no tests", empty_files))
    requested = set(selection.nodes)
    kept, deselected = [], []
    for item in items:
        if (
            item.nodeid in requested or item.nodeid.split("::", 1)[0] in files
            or (selection.include_guards and item.get_closest_marker("guard") is not None)
        ):
            kept.append(item)
        else:
            deselected.append(item)
    if not kept:
        raise pytest.UsageError("exact test selection collected no runnable items")
    config.hook.pytest_deselected(items=deselected)
    items[:] = kept
