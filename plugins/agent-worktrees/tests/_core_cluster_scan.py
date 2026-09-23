"""AST-based scanner: classify every `_core()`-accessed attribute name by
where/how it's actually defined in `__main__.py`.

Backs `test_lazy_dispatch.py`'s drift check for `__main__._CLUSTER_FREE_MODULES`
(Phase 1b of the agent-cli-lazy-dispatch effort): a module is "cluster-free"
when every attribute its own CLI-submodule handlers reach through `_core()`
is available WITHOUT running `_load_full_command_surface()`. See that
constant's own comment in `__main__.py` for the full rationale, and do NOT
hand-edit `_CLUSTER_FREE_MODULES` without re-running this scan -- an earlier,
narrower regex-only scan of the same `_core()` cluster shipped a live
`create-pr` regression (see the effort's Journal).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "agent_worktrees"
MAIN_PATH = SRC / "__main__.py"


def is_core_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_core"
        and not node.args
        and not node.keywords
    )


def scan_module_for_core_attrs(tree: ast.Module) -> set[str]:
    attrs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and is_core_call(node.value):
            attrs.add(node.attr)

    def scan_scope(stmts: list[ast.stmt]) -> None:
        core_vars: set[str] = set()
        block = ast.Module(body=stmts, type_ignores=[])
        for node in ast.walk(block):
            if isinstance(node, ast.Assign) and is_core_call(node.value):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        core_vars.add(tgt.id)
            elif isinstance(node, ast.AnnAssign) and node.value is not None and is_core_call(node.value):
                if isinstance(node.target, ast.Name):
                    core_vars.add(node.target.id)
        if core_vars:
            for node in ast.walk(block):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in core_vars:
                    attrs.add(node.attr)

    def walk_scopes(node: ast.AST) -> None:
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)):
            scan_scope(list(node.body))
        for child in ast.iter_child_nodes(node):
            walk_scopes(child)

    walk_scopes(tree)
    return attrs


def classify_main_names(main_tree: ast.Module, func_start: int, func_end: int) -> tuple[dict[str, int], dict[str, str], set[str]]:
    """Return (cheap_name->lineno, heavy_owned_name->owning_module, heavy_native_names)."""
    cheap: dict[str, int] = {}
    heavy_owned: dict[str, str] = {}
    heavy_native: set[str] = set()

    for node in main_tree.body:
        lineno = node.lineno
        in_func = func_start <= lineno <= func_end
        names: list[str] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.append(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.append(alias.asname or alias.name)

        if lineno == func_start and isinstance(node, ast.FunctionDef) and node.name == "_load_full_command_surface":
            # Walk its body for `name = module.attr` assignments (global reexports).
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                    tgt = stmt.targets[0]
                    val = stmt.value
                    if isinstance(tgt, ast.Name) and isinstance(val, ast.Attribute) and isinstance(val.value, ast.Name):
                        heavy_owned[tgt.id] = val.value.id
                    elif isinstance(tgt, ast.Name) and tgt.id not in heavy_owned:
                        heavy_native.add(tgt.id)
                elif isinstance(stmt, (ast.ImportFrom, ast.Import)):
                    for alias in stmt.names:
                        heavy_native.add(alias.asname or alias.name)
            continue

        for n in names:
            if in_func:
                continue  # handled above for the specific function; ignore other in-range nodes
            cheap[n] = lineno

    return cheap, heavy_owned, heavy_native


def classify_all_modules() -> dict[str, dict[str, dict]]:
    """Classify every `_core()`-accessed attribute in every CLI submodule.

    Returns ``{module_name: {attr_name: {"class": "cheap"|"heavy_owned"|
    "heavy_native", ...}}}`` for every module that defines `_core()` and
    reaches for at least one attribute through it.
    """
    main_text = MAIN_PATH.read_text(encoding="utf-8")
    main_tree = ast.parse(main_text, filename=str(MAIN_PATH))

    func_start = func_end = -1
    for node in ast.walk(main_tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_load_full_command_surface":
            func_start, func_end = node.lineno, node.end_lineno

    cheap, heavy_owned, heavy_native = classify_main_names(main_tree, func_start, func_end)

    results: dict[str, dict] = {}
    for path in sorted(SRC.glob("*.py")):
        if path.name == "__main__.py":
            continue
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        if not any(isinstance(n, ast.FunctionDef) and n.name == "_core" for n in ast.walk(tree)):
            continue
        attrs = scan_module_for_core_attrs(tree)
        if not attrs:
            continue
        module_name = path.stem
        classified = {}
        for a in sorted(attrs):
            if a in cheap:
                classified[a] = {"class": "cheap", "line": cheap[a]}
            elif a in heavy_owned:
                classified[a] = {"class": "heavy_owned", "owner": heavy_owned[a]}
            elif a in heavy_native:
                classified[a] = {"class": "heavy_native"}
            else:
                classified[a] = {"class": "UNKNOWN"}
        results[module_name] = classified
    return results


def compute_cluster_free_modules(candidate_modules: frozenset[str]) -> frozenset[str]:
    """Of `candidate_modules` (module names `_LAZY_DISPATCH_TABLE` maps to),
    return those that never need `_load_full_command_surface()`.

    A module qualifies when it either never defines `_core()`, or every
    attribute it reaches through `_core()` is "cheap" (bound at __main__
    module level before the deferred cluster-import block) -- see
    `__main__.py`'s own `_CLUSTER_FREE_MODULES` comment for the full
    rationale.
    """
    classified = classify_all_modules()
    free = {
        mod
        for mod, attrs in classified.items()
        if mod in candidate_modules and all(v["class"] == "cheap" for v in attrs.values())
    }

    # A candidate that never defines `_core()` at all isn't in `classified`
    # (nothing to classify) but is trivially cluster-free too (e.g. pane_lifecycle).
    for mod in candidate_modules:
        if mod in classified:
            continue
        path = SRC / f"{mod}.py"
        if path.is_file() and "def _core(" not in path.read_text(encoding="utf-8"):
            free.add(mod)
    return frozenset(free)

