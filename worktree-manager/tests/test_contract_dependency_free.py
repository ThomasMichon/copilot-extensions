"""Phase 1 contract test: the one-way, dependency-free boundary (DQ2 / #354).

The load-bearing invariant of the whole effort: the Worktree Manager *knows about*
the plugins, but **neither depends on the other**. Concretely, a plugin
installed and run with the Worktree Manager absent behaves identically. These tests
assert that boundary as real code/dependency edges (not the mere English word
"worktree-manager", which appears generically in unrelated plugin prose):

  * the Worktree Manager imports NO plugin code, and
  * NO plugin runtime imports or declares a dependency on the installed Worktree
    Manager (the self-contained admission toolkit carries its own source copy).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import worktree_manager
from worktree_manager.catalog import find_repo_root, load_catalog

# Runtime module prefixes that would betray the Worktree Manager reaching into the
# plugins' own Python packages.
_PLUGIN_RUNTIME_PREFIXES = ("agent_worktrees", "agent_bridge", "agent_codespaces",
                            "agent_containers", "agent_mcp", "agent_dispatch",
                            "agent_vault", "agent_index", "agent_logger",
                            "agent_ssh", "agent_machines", "plugins")

def _imported_roots(source: str) -> set[str]:
    roots = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _worktree_manager_pkg_dir() -> Path:
    return Path(worktree_manager.__file__).resolve().parent


def _plugin_runtime_sources(plugins_dir: Path):
    for plugin in plugins_dir.iterdir():
        if not plugin.is_dir():
            continue
        for child in plugin.iterdir():
            if child.name == "tests":
                continue
            if child.is_file() and child.suffix == ".py":
                yield child
            elif child.is_dir():
                for py in child.rglob("*.py"):
                    # The generated toolkit owns a private Manager source copy,
                    # not a dependency on the standalone installed application.
                    if not py.is_relative_to(plugin / "scripts" / "_runtime_admission"):
                        yield py


def test_worktree_manager_imports_no_plugin_code_statically():
    pkg = _worktree_manager_pkg_dir()
    offenders = []
    for py in pkg.rglob("*.py"):
        if _imported_roots(py.read_text("utf-8")).intersection(_PLUGIN_RUNTIME_PREFIXES):
            offenders.append(py.name)
    assert not offenders, f"worktree-manager imports plugin code in: {offenders}"


def test_loading_the_catalog_pulls_in_no_plugin_runtime():
    # Fresh: nothing plugin-side should be imported by exercising the model.
    before = set(sys.modules)
    load_catalog()
    # Touch every code path that reads plugin-published metadata (discovery +
    # overlay + coverage), staying local (no network) in the test checkout.
    root = find_repo_root(__file__)
    assert root is not None, "test checkout not found"
    from worktree_manager.model import build_model, coverage
    build_model(repo_root=root, allow_remote=False)
    coverage(repo_root=root, allow_remote=False)
    leaked = [
        m for m in (set(sys.modules) - before)
        if m.split(".")[0] in _PLUGIN_RUNTIME_PREFIXES
    ]
    assert not leaked, f"loading the catalog imported plugin runtime modules: {leaked}"


def test_no_plugin_imports_the_worktree_manager():
    """No plugin's Python code imports the Worktree Manager (only meaningful inside a
    checkout that ships the plugins)."""
    root = find_repo_root(__file__)
    assert root is not None, "test checkout not found"
    plugins_dir = root / "plugins"
    offenders = []
    for py in _plugin_runtime_sources(plugins_dir):
        if "worktree_manager" in _imported_roots(py.read_text("utf-8")):
            offenders.append(str(py.relative_to(root)))
    assert not offenders, f"plugins import the worktree-manager (dependency edge!): {offenders}"


def test_runtime_scan_retains_production_edges_not_test_harness_imports(tmp_path):
    plugin = tmp_path / "plugins" / "example"
    (plugin / "src").mkdir(parents=True)
    scripts = plugin / "scripts"
    toolkit = scripts / "_runtime_admission" / "worktree-manager" / "src"
    toolkit.mkdir(parents=True)
    (plugin / "tests" / "fixtures").mkdir(parents=True)
    runtime = plugin / "src" / "runtime.py"
    runtime.write_text("from worktree_manager import forbidden\n", encoding="utf-8")
    (plugin / "entry.py").write_text("import worktree_manager\n", encoding="utf-8")
    (scripts / "entry.py").write_text("import worktree_manager\n", encoding="utf-8")
    (toolkit / "private.py").write_text("import worktree_manager\n", encoding="utf-8")
    (plugin / "tests" / "fixtures" / "driver.py").write_text(
        "from worktree_manager import synthetic_driver\n", encoding="utf-8",
    )
    sources = list(_plugin_runtime_sources(tmp_path / "plugins"))
    assert set(sources) == {runtime, plugin / "entry.py", scripts / "entry.py"}
    assert all("worktree_manager" in _imported_roots(path.read_text("utf-8")) for path in sources)


def test_static_import_scan_distinguishes_process_programs_from_dependency_edges():
    source = (
        'program = """\nfrom agent_worktrees import cli\nimport worktree_manager\n"""\n'
        "# from agent_bridge import cli\n"
        "import os, agent_dispatch.cli as dispatch\n"
        "def lazy():\n"
        "    from agent_worktrees.cli import main\n"
        "    import worktree_manager.model\n"
    )
    assert _imported_roots(source) == {"os", "agent_dispatch", "agent_worktrees", "worktree_manager"}


def test_no_plugin_declares_the_worktree_manager_as_a_dependency():
    root = find_repo_root(__file__)
    assert root is not None, "test checkout not found"
    plugins_dir = root / "plugins"
    pkg_name = "copilot-extensions-worktree-manager"
    offenders = []
    for meta in (*plugins_dir.rglob("pyproject.toml"),
                 *plugins_dir.rglob("plugin.json")):
        if pkg_name in meta.read_text("utf-8"):
            offenders.append(str(meta.relative_to(root)))
    assert not offenders, f"plugins declare a dependency on the worktree-manager: {offenders}"


def test_worktree_manager_stays_out_of_the_plugin_pipe():
    """The Worktree Manager is delivered outside the plugin pipe: it is not a plugin
    (no plugin.json) and is not listed in the marketplace."""
    root = find_repo_root(__file__)
    assert root is not None, "test checkout not found"
    import json

    pkg = _worktree_manager_pkg_dir()
    # No plugin.json anywhere in the worktree-manager payload.
    assert not list(pkg.parent.parent.rglob(".claude-plugin/plugin.json")), (
        "the worktree-manager must not ship a plugin.json"
    )
    market = json.loads((root / ".github" / "plugin" / "marketplace.json").read_text("utf-8"))
    names = {p["name"] for p in market.get("plugins", [])}
    assert "worktree-manager" not in names
    assert "copilot-extensions-worktree-manager" not in names
