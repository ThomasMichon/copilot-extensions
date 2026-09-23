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


def scan_module_for_core_attrs(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Return (direct_attrs, helper_attrs).

    `direct_attrs` -- names reached via `_core().attr`, `core = _core();
    ... core.attr`, or a bare `getattr(_core(), "name"[, default])` call NOT
    wrapped in the module's own `_core_helper()`. These need the cluster
    loaded if the name is "heavy" (see `classify_all_modules`).

    `helper_attrs` -- names reached via the module's own `_core_helper("name",
    local)` idiom. Since that helper does `vars(_core()).get(name)` (a plain
    dict lookup, not `getattr()`), it can NEVER trigger
    `__main__.__getattr__`'s eager `_load_full_command_surface()` -- it
    either finds a monkeypatched override already present on `__main__`, or
    silently falls back to `local`. These are always safe regardless of
    whether the name is heavy, so they never count against a module's
    cluster-free status. (An earlier version of `_core_helper` used
    `getattr(_core(), name, None)`, which -- despite the `None` default --
    still triggered a full eager load on every call before the cluster
    loaded, since `__getattr__` runs before `getattr()`'s default applies;
    fixed at the source in every `_core_helper` definition, not worked
    around here.)
    """
    direct: set[str] = set()
    helper: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and is_core_call(node.value):
            direct.add(node.attr)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and is_core_call(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            direct.add(node.args[1].value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_core_helper"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            helper.add(node.args[0].value)

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
                    direct.add(node.attr)

    def walk_scopes(node: ast.AST) -> None:
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)):
            scan_scope(list(node.body))
        for child in ast.iter_child_nodes(node):
            walk_scopes(child)

    walk_scopes(tree)
    return direct, helper


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
        attrs, helper_attrs = scan_module_for_core_attrs(tree)
        if not attrs and not helper_attrs:
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
        for a in sorted(helper_attrs):
            # Reached only through the module's own _core_helper() idiom --
            # always safe post-fix regardless of heaviness (see
            # scan_module_for_core_attrs's own docstring).
            classified.setdefault(a, {"class": "via_helper"})
        results[module_name] = classified
    return results


def compute_cluster_free_modules(candidate_modules: frozenset[str]) -> frozenset[str]:
    """Of `candidate_modules` (module names `_LAZY_DISPATCH_TABLE` maps to),
    return those that never need `_load_full_command_surface()`.

    A module qualifies when every attribute it reaches through `_core()`
    (directly, via an assigned var, or via its own `_core_helper()` idiom) is
    "cheap" or "via_helper" AND none of its cheap `_core()`-resolved
    __main__-native functions transitively touches a name bound only inside
    `_load_full_command_surface()` (see `find_transitively_unsafe_functions`'s
    own docstring -- this is the class of bug static single-level analysis
    alone cannot see, found for `session_inspection_cli`'s
    `_run_session_lifecycle`).
    """
    main_text = MAIN_PATH.read_text(encoding="utf-8")
    main_tree = ast.parse(main_text, filename=str(MAIN_PATH))
    func_start = func_end = -1
    for node in ast.walk(main_tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_load_full_command_surface":
            func_start, func_end = node.lineno, node.end_lineno
    _cheap, heavy_owned, heavy_native = classify_main_names(main_tree, func_start, func_end)
    deferred_only_names = frozenset(heavy_owned.keys()) | frozenset(heavy_native)
    transitively_unsafe = find_transitively_unsafe_functions(deferred_only_names)

    classified = classify_all_modules()
    free = set()
    for mod, attrs in classified.items():
        if mod not in candidate_modules:
            continue
        if not all(v["class"] in ("cheap", "via_helper") for v in attrs.values()):
            continue
        cheap_names = [a for a, v in attrs.items() if v["class"] == "cheap"]
        if any(n in transitively_unsafe for n in cheap_names):
            continue
        free.add(mod)

    # A candidate that never defines `_core()` at all isn't in `classified`
    # (nothing to classify) but is trivially cluster-free too (e.g. pane_lifecycle).
    for mod in candidate_modules:
        if mod in classified:
            continue
        path = SRC / f"{mod}.py"
        if path.is_file() and "def _core(" not in path.read_text(encoding="utf-8"):
            free.add(mod)
    return frozenset(free)


def find_transitively_unsafe_functions(deferred_only_names: frozenset[str]) -> dict[str, list[str]]:
    """Static call-graph check for the class of bug the scanner above cannot
    see: a __main__-native function that resolves cleanly via `_core().name`
    (because `name` itself is bound outside the deferred cluster-import
    block) can still reference, in its OWN body or transitively in a
    function it calls, a bare name that is bound ONLY inside
    `_load_full_command_surface()` -- a genuine `NameError` at runtime if
    that surface hasn't loaded (found this way for `_run_session_lifecycle`,
    which referenced `cmd_register_session` and `_aw_runtime_home`).

    Returns ``{function_name: [chain of names showing how a deferred name is
    reached]}`` for every __main__-native (non-deferred) top-level function
    that transitively touches a name in `deferred_only_names` as a bare
    ``Name`` load (not an attribute access, which resolves differently).
    Only meant to run over the SET of __main__-native functions that a
    cluster-free module's own `_core()` accesses resolve to -- pass the
    result of `classify_all_modules()`'s "cheap" names (filtered to ones that
    are actually function defs) as its own entry points via
    `deferred_only_names`'s caller.
    """
    main_text = MAIN_PATH.read_text(encoding="utf-8")
    main_tree = ast.parse(main_text, filename=str(MAIN_PATH))

    func_start = func_end = -1
    for node in ast.walk(main_tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_load_full_command_surface":
            func_start, func_end = node.lineno, node.end_lineno

    # Every __main__-native top-level function defined OUTSIDE the deferred
    # block, keyed by name, plus the set of bare Name loads and Call targets
    # (by name) each one's body contains.
    native_funcs: dict[str, ast.FunctionDef] = {}
    for node in main_tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not (func_start <= node.lineno <= func_end)
        ):
            native_funcs[node.name] = node

    def body_name_loads(fn: ast.AST) -> set[str]:
        loads = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                loads.add(node.id)
        return loads

    memo: dict[str, list[str] | None] = {}

    def check(fn_name: str, visiting: frozenset[str]) -> list[str] | None:
        if fn_name in memo:
            return memo[fn_name]
        if fn_name in visiting or fn_name not in native_funcs:
            return None
        fn = native_funcs[fn_name]
        loads = body_name_loads(fn)
        hit = loads & deferred_only_names
        if hit:
            memo[fn_name] = [fn_name, next(iter(hit))]
            return memo[fn_name]
        # Recurse into any other native top-level function this one's body
        # references by bare name (a plain call or reference).
        for callee in loads:
            if callee == fn_name or callee not in native_funcs:
                continue
            chain = check(callee, visiting | {fn_name})
            if chain:
                memo[fn_name] = [fn_name, *chain]
                return memo[fn_name]
        memo[fn_name] = None
        return None

    problems: dict[str, list[str]] = {}
    for name in native_funcs:
        chain = check(name, frozenset())
        if chain:
            problems[name] = chain
    return problems

