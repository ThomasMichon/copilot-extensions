"""Phase 1 contract test: the one-way, dependency-free boundary (DQ2 / #354).

The load-bearing invariant of the whole effort: the Configurator *knows about*
the plugins, but **neither depends on the other**. Concretely, a plugin
installed and run with the Configurator absent behaves identically. These tests
assert that boundary as real code/dependency edges (not the mere English word
"configurator", which appears generically in unrelated plugin prose):

  * the Configurator imports NO plugin code, and
  * NO plugin imports or declares a dependency on the Configurator.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import configurator
from configurator.catalog import find_repo_root, load_catalog

# Runtime module prefixes that would betray the Configurator reaching into the
# plugins' own Python packages.
_PLUGIN_RUNTIME_PREFIXES = ("agent_worktrees", "agent_bridge", "agent_codespaces",
                            "agent_containers", "agent_mcp", "agent_dispatch",
                            "agent_vault", "agent_index", "agent_logger",
                            "agent_ssh", "agent_machines", "plugins")

_IMPORT_PLUGIN = re.compile(
    r"^\s*(?:from|import)\s+(?:plugins\b|agent_(?:worktrees|bridge|codespaces|"
    r"containers|mcp|dispatch|vault|index|logger|ssh|machines)\b)",
    re.MULTILINE,
)
_IMPORT_CONFIGURATOR = re.compile(r"^\s*(?:from|import)\s+configurator\b", re.MULTILINE)


def _configurator_pkg_dir() -> Path:
    return Path(configurator.__file__).resolve().parent


def test_configurator_imports_no_plugin_code_statically():
    pkg = _configurator_pkg_dir()
    offenders = []
    for py in pkg.rglob("*.py"):
        if _IMPORT_PLUGIN.search(py.read_text("utf-8")):
            offenders.append(py.name)
    assert not offenders, f"configurator imports plugin code in: {offenders}"


def test_loading_the_catalog_pulls_in_no_plugin_runtime():
    # Fresh: nothing plugin-side should be imported by exercising the model.
    before = set(sys.modules)
    load_catalog()
    # Touch every code path that reads plugin-published metadata (discovery +
    # overlay + coverage), staying local (no network) when a checkout is present.
    root = find_repo_root()
    if root is not None:
        from configurator.model import build_model, coverage
        build_model(repo_root=root, allow_remote=False)
        coverage(repo_root=root, allow_remote=False)
    leaked = [
        m for m in (set(sys.modules) - before)
        if m.split(".")[0] in _PLUGIN_RUNTIME_PREFIXES
    ]
    assert not leaked, f"loading the catalog imported plugin runtime modules: {leaked}"


def test_no_plugin_imports_the_configurator():
    """No plugin's Python code imports the Configurator (only meaningful inside a
    checkout that ships the plugins)."""
    root = find_repo_root()
    if root is None:
        return
    plugins_dir = root / "plugins"
    offenders = []
    for py in plugins_dir.rglob("*.py"):
        if _IMPORT_CONFIGURATOR.search(py.read_text("utf-8")):
            offenders.append(str(py.relative_to(root)))
    assert not offenders, f"plugins import the configurator (dependency edge!): {offenders}"


def test_no_plugin_declares_the_configurator_as_a_dependency():
    root = find_repo_root()
    if root is None:
        return
    plugins_dir = root / "plugins"
    pkg_name = "copilot-extensions-configurator"
    offenders = []
    for meta in (*plugins_dir.rglob("pyproject.toml"),
                 *plugins_dir.rglob("plugin.json")):
        if pkg_name in meta.read_text("utf-8"):
            offenders.append(str(meta.relative_to(root)))
    assert not offenders, f"plugins declare a dependency on the configurator: {offenders}"


def test_configurator_stays_out_of_the_plugin_pipe():
    """The Configurator is delivered outside the plugin pipe: it is not a plugin
    (no plugin.json) and is not listed in the marketplace."""
    root = find_repo_root()
    if root is None:
        return
    import json

    pkg = _configurator_pkg_dir()
    # No plugin.json anywhere in the configurator payload.
    assert not list(pkg.parent.parent.rglob(".claude-plugin/plugin.json")), (
        "the configurator must not ship a plugin.json"
    )
    market = json.loads((root / ".github" / "plugin" / "marketplace.json").read_text("utf-8"))
    names = {p["name"] for p in market.get("plugins", [])}
    assert "configurator" not in names
    assert "copilot-extensions-configurator" not in names
