#!/usr/bin/env python3
"""Guard Windows background-process launch invariants.

Windows-launch-hardening (ThomasMichon/copilot-extensions#786). Once a plugin
depends on the shared ``agent-procutil`` lib, every Windows console-suppression /
detach flag it needs is available through ``no_window_kwargs`` /
``detached_kwargs`` / ``no_window_flags``. Re-introducing a raw
``subprocess.CREATE_NO_WINDOW`` / ``DETACHED_PROCESS`` / ``CREATE_NEW_PROCESS_GROUP``
/ ``CREATE_BREAKAWAY_FROM_JOB`` literal in that plugin's ``src`` re-opens the
exact drift the lib was created to close, so this guard freezes it.

Independently, ``CREATE_NEW_CONSOLE`` is forbidden in every production plugin
and canonical shared-library source. Windows Default Terminal can surface that
console even when the caller supplies ``SW_HIDE``; background work must use the
appropriate shared no-window primitive instead. A genuinely interactive launch
may use the inline escape hatch below.

**Scope.** The raw-flag rule applies only to plugins whose ``pyproject.toml``
declares ``agent-procutil`` -- adoption is incremental. The unsafe-new-console
rule applies to every ``plugins/*/src``, canonical ``libs/*/src``, shipped
``plugins/*/libs/*/src``, and ``plugins/*/scripts`` tree.

**AST-based**, so a docstring or comment that merely *names* a flag is never
flagged -- only real code (an attribute access like ``subprocess.CREATE_NO_WINDOW``,
a bare ``Name`` reference, or a ``getattr(subprocess, "CREATE_NO_WINDOW", ...)``
string) counts.

A genuinely-intentional low-level exception (e.g. the ``winjob`` job-object
primitive, or a deliberately-different detach) carries an inline
``# headless-guard: allow <why>`` comment on the offending line and is skipped.

**A third, complementary rule (#789): a console-subsystem spawn with NO
suppression signal at all.** The two rules above both assume *some* flag is
already present and police *how* it got there; neither one catches the
simpler, more common production incident -- a plain
``subprocess.run(["agent-worktrees", "pr-watch", ...], check=False)`` with no
window handling whatsoever, which allocates a brand-new visible console the
instant its parent process (a detached waiter, a background daemon) has none
of its own. This rule scans every production/script root (same scope as the
``CREATE_NEW_CONSOLE`` rule, regardless of ``agent-procutil`` adoption) for a
``subprocess.Popen/run/call/check_call/check_output`` (or the bare imported
form) or ``os.system`` call whose resolvable argv[0] names a known
console-subsystem program (``cmd``, ``powershell``, ``pwsh``, ``conhost``,
``python`` -- never ``pythonw``, a GUI-subsystem interpreter -- ``node``,
``git``, ``ssh``; case-insensitive, ``.exe`` optional) and flags it unless the
call (or its enclosing function, to tolerate the common
``kwargs = {...}; kwargs.update(no_window_kwargs()); Popen(cmd, **kwargs)``
shape without real data-flow analysis) already references a raw flag
(``creationflags=``/``startupinfo=``) or one of the shared helpers
(``no_window_kwargs``/``detached_kwargs``/``windowless_daemon_kwargs``/
``_process_tree_kwargs``/``no_window_flags``). ``agent_procutil.spawn_daemon``
is the approved headless primitive for a resident daemon: it is built on the
detach helpers and is not itself a ``subprocess`` call, so a launch through it
is never reported. It is deliberately *not* one of the helpers above: a
``spawn_daemon(...)`` call says nothing about a raw ``subprocess`` spawn beside
it in the same function, which is still checked on its own. Only a **literal, resolvable**
argv[0] is checked -- a dynamically-built command (a variable, a function
call) is silently skipped rather than risk a false positive from a value this
guard cannot actually resolve. The same inline ``# headless-guard: allow
<why>`` escape hatch applies.

**A fourth rule: the same program list, declared in JSON/YAML instead of
Python.** A task/hook/manifest spec can embed a literal argv array (e.g.
``"cmd": ["powershell.exe", "-File", ...]``) that some Python loader will
eventually spawn -- this guard cannot trace that data flow, so it only
*reports* the declaration (never silently verified safe) for the same
production/script roots, unless the source line carries a comment containing
``headless-guard: allow`` (YAML) or the exact ``path:line`` is listed in
``tools/headless-guard.allow`` (one ``relative/path.json:42  reason`` or
``relative/path.yaml:7  reason`` entry per line; JSON has no comment syntax,
so this is its only escape hatch -- YAML may use either).

**A fifth rule: a resident daemon launches through ``spawn_daemon``.** In an
agent-procutil-adopting plugin's ``src``, a ``subprocess.Popen`` (or the bare
imported ``Popen``) that detaches with ``detached_kwargs`` /
``windowless_daemon_kwargs`` -- in the call itself or anywhere in its
enclosing function -- is reported: a hand-rolled detached launch skips
``agent_procutil.spawn_daemon``'s explicit cwd outside the payload, its log,
and its Job breakaway handling. A detached child that is not a resident
daemon (a waiter, a one-shot helper, a deliberately contained child) carries
an inline ``# daemon-spawn: allow <why>`` comment on the ``Popen`` line, or a
``relative/path.py::qualified.function  reason`` entry in
``tools/daemon-spawn.allow``. That file is a shrink-only baseline: each entry
covers exactly one launch in its function, an entry with no matching launch
is itself reported, and every entry needs a reason.

**A sixth rule: an installer never starts a daemon with a contained
PowerShell launch.** In ``plugins/*/scripts/install*.ps1`` and
``plugins/*/transports/*/scripts/install*.ps1``, a ``Start-Process`` of
``conhost`` (the windowless host every PowerShell start-now uses) is
reported: the child stays inside the installer's Job and dies with the SSH
session or updater that ran it. Start a daemon now through the service's
user-mode ensure path (``agent_procutil.spawn_daemon``), or WMI
``Win32_Process.Create`` for a PowerShell-only daemon. A child the caller
deliberately owns (a foreground supervisor that waits on it, or a warned
last resort) carries ``# daemon-spawn: allow <why>`` on the statement or the
comment line directly above it. Scheduled-task and Run-key registrations
hand the lifetime to Windows and are not launches.

Usage::

    python tools/check-headless-launch.py          # verify (CI / pre-push)
    python tools/check-headless-launch.py --list    # show the adopting plugins it checks
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import re
import sys
import tokenize
from collections import Counter
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
LIBS_DIR = REPO / "libs"
HEADLESS_GUARD_ALLOWLIST = REPO / "tools" / "headless-guard.allow"
DAEMON_SPAWN_ALLOWLIST = "tools/daemon-spawn.allow"  # relative to REPO

_ALLOW = "headless-guard: allow"
_DAEMON_ALLOW = "daemon-spawn: allow"
_DETACH_HELPERS = frozenset({"detached_kwargs", "windowless_daemon_kwargs"})
_UNSAFE_FLAG = "CREATE_NEW_CONSOLE"
_CREATE_NEW_CONSOLE_VALUE = 0x00000010
_FLAG_NAMES = frozenset({
    "CREATE_NO_WINDOW",
    "CREATE_NEW_CONSOLE",
    "DETACHED_PROCESS",
    "CREATE_NEW_PROCESS_GROUP",
    "CREATE_BREAKAWAY_FROM_JOB",
})
_SKIP_DIR_PARTS = {"libs", "tests", "build", "__pycache__", ".venv", ".venv-test", "dist"}

# Console-subsystem programs that allocate a visible Windows console when
# spawned from a window-less parent unless explicitly suppressed. Matched
# against the resolved argv[0] basename, case-insensitive, with an optional
# trailing ".exe" stripped first -- never ``pythonw``, a GUI-subsystem
# interpreter that never allocates one.
_CONSOLE_PROGRAMS = frozenset({
    "cmd", "powershell", "pwsh", "conhost", "python", "node", "git", "ssh",
})
_SPAWN_FUNCS = frozenset({"Popen", "run", "call", "check_call", "check_output"})
_SAFE_KWARGS = frozenset({"creationflags", "startupinfo"})
_SAFE_HELPERS = (
    "no_window_kwargs", "detached_kwargs", "windowless_daemon_kwargs",
    "_process_tree_kwargs", "no_window_flags",
)



def _adopting_plugins() -> list[Path]:
    """Plugins whose pyproject.toml depends on agent-procutil."""
    out: list[Path] = []
    if not PLUGINS_DIR.is_dir():
        return out
    for plugin in sorted(PLUGINS_DIR.iterdir()):
        pp = plugin / "pyproject.toml"
        if pp.is_file() and "agent-procutil" in pp.read_text(encoding="utf-8"):
            out.append(plugin)
    return out


def _iter_py(src: Path):
    for f in src.rglob("*.py"):
        if _SKIP_DIR_PARTS & set(f.relative_to(src).parts):
            continue
        yield f


def _iter_declarative(src: Path):
    for ext in ("*.json", "*.yaml", "*.yml"):
        for f in src.rglob(ext):
            if _SKIP_DIR_PARTS & set(f.relative_to(src).parts):
                continue
            yield f


def _program_name(value: str) -> str | None:
    """The console-program basename in *value*, or ``None`` if it doesn't
    name one of :data:`_CONSOLE_PROGRAMS` (case-insensitive, ``.exe``
    optional). *value* may be a bare argv[0] or a whole ``shell=True``-style
    command string, in which case only its first whitespace-separated token
    is considered."""
    token = value.split()[0] if value.split() else value
    name = Path(token.strip("\"'")).name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name if name in _CONSOLE_PROGRAMS else None


class _LaunchVisitor(ast.NodeVisitor):
    """Reuse bound visitor dispatch methods within one file's analysis."""

    def __init__(self) -> None:
        self._dispatch: dict[type[ast.AST], Callable[[ast.AST], None]] = {}

    def visit(self, node: ast.AST) -> None:
        kind = type(node)
        visitor = self._dispatch.get(kind)
        if visitor is None:
            visitor = getattr(self, "visit_" + kind.__name__, self.generic_visit)
            self._dispatch[kind] = visitor
        return visitor(node)


class _SpawnFinder(_LaunchVisitor):
    """Collect (lineno, program) for an unsuppressed console-program spawn.

    Only a **literal, resolvable** argv[0] -- a string constant, or a
    list/tuple literal's first element -- is ever checked; anything
    dynamically built (a variable, a call, an f-string) is silently skipped
    rather than risk a false positive this guard cannot actually verify.
    """

    def __init__(self, text: str) -> None:
        super().__init__()
        self.hits: list[tuple[int, str]] = []
        self._text = text
        self._spawn_aliases: dict[str, str] = {}
        self._system_aliases: set[str] = set()
        self._func_stack: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "subprocess":
            for alias in node.names:
                if alias.name in _SPAWN_FUNCS:
                    self._spawn_aliases[alias.asname or alias.name] = alias.name
        elif node.module == "os":
            for alias in node.names:
                if alias.name == "system":
                    self._system_aliases.add(alias.asname or alias.name)
        self.generic_visit(node)

    def _enclosing_source(self, node: ast.AST) -> str:
        """Source text of the innermost enclosing function, or the whole
        module if *node* is top-level -- tolerates the common
        ``kwargs.update(no_window_kwargs())`` shape, where the helper call
        never appears directly in the spawn call's own argument list."""
        if not self._func_stack:
            return self._text
        func = self._func_stack[-1]
        lines = self._text.splitlines()
        end = getattr(func, "end_lineno", None) or len(lines)
        return "\n".join(lines[func.lineno - 1:end])

    def _is_safe(self, call: ast.Call) -> bool:
        for kw in call.keywords:
            if kw.arg in _SAFE_KWARGS:
                return True
        source = self._enclosing_source(call)
        return any(helper in source for helper in _SAFE_HELPERS)

    def _first_argv_program(self, call: ast.Call) -> str | None:
        if not call.args:
            return None
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return _program_name(first.value)
        if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
            head = first.elts[0]
            if isinstance(head, ast.Constant) and isinstance(head.value, str):
                return _program_name(head.value)
        return None

    def _check_call(self, node: ast.Call) -> None:
        func = node.func
        is_spawn = (
            (isinstance(func, ast.Attribute) and func.attr in _SPAWN_FUNCS
             and isinstance(func.value, ast.Name) and func.value.id == "subprocess")
            or (isinstance(func, ast.Name) and func.id in self._spawn_aliases)
        )
        is_system = (
            (isinstance(func, ast.Attribute) and func.attr == "system"
             and isinstance(func.value, ast.Name) and func.value.id == "os")
            or (isinstance(func, ast.Name) and func.id in self._system_aliases)
        )
        if not (is_spawn or is_system):
            return
        program = self._first_argv_program(node)
        if program is None:
            return
        if is_system:
            # os.system() has no kwargs at all -- it can never carry a safe
            # flag directly, only via the enclosing function having already
            # routed the *actual* spawn elsewhere (unlikely for this call
            # itself, but tolerate the same enclosing-source heuristic).
            source = self._enclosing_source(node)
            if any(helper in source for helper in _SAFE_HELPERS):
                return
            self.hits.append((node.lineno, program))
            return
        if not self._is_safe(node):
            self.hits.append((node.lineno, program))

    def visit_Call(self, node: ast.Call) -> None:
        self._check_call(node)
        self.generic_visit(node)

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._func_stack.append(node)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node)


def _find_unsuppressed_spawns(f: Path) -> tuple[str, list[tuple[int, str]]]:
    text = f.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(f))
    finder = _SpawnFinder(text)
    finder.visit(tree)
    return text, sorted(set(finder.hits))


def _own_scope_nodes(node: ast.AST):
    """``node``'s descendants, not descending into nested scopes (function,
    lambda and class bodies)."""
    pending = list(ast.iter_child_nodes(node))
    while pending:
        sub = pending.pop()
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        yield sub
        pending.extend(ast.iter_child_nodes(sub))


def _subprocess_aliases(scope: ast.AST) -> tuple[set[str], set[str], set[str]]:
    """(``subprocess`` aliases, ``Popen`` aliases, detach-helper aliases)
    imported in ``scope``'s own body."""
    modules: set[str] = set()
    popens: set[str] = set()
    helpers: set[str] = set()
    for node in _own_scope_nodes(scope):
        if isinstance(node, ast.Import):
            modules.update(a.asname for a in node.names if a.name == "subprocess" and a.asname)
        elif isinstance(node, ast.ImportFrom):
            if node.module == "subprocess":
                popens.update(a.asname or a.name for a in node.names if a.name == "Popen")
            helpers.update(a.asname for a in node.names if a.name in _DETACH_HELPERS and a.asname)
    return modules, popens, helpers


def _calls_detach_helper(node: ast.AST, helpers: frozenset[str] = _DETACH_HELPERS) -> bool:
    """Whether ``node``'s own scope calls a detach helper.

    Nested function and class bodies are separate scopes and are not searched,
    so a helper inside an inner function never marks an outer launch.
    """
    for sub in _own_scope_nodes(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in helpers:
                return True
    return False


class _DetachedSpawnFinder(_LaunchVisitor):
    """Collect (lineno, qualname) for a raw ``Popen`` that detaches itself."""

    def __init__(self, tree: ast.AST) -> None:
        super().__init__()
        self.hits: list[tuple[int, str]] = []
        self._names: list[str] = []
        self._funcs: list[ast.AST] = []
        # Aliases per lexical scope, each indexed before its body is visited (a
        # function may precede the import it uses). As in Python, a scope sees
        # its own imports and its enclosing functions' and module's, never a
        # sibling's or an enclosing class body's.
        modules, popens, helpers = _subprocess_aliases(tree)
        self._alias_stack: list[tuple[tuple[set[str], set[str], set[str]], bool]] = [
            (({"subprocess", *modules}, popens, {*_DETACH_HELPERS, *helpers}), False)
        ]

    def _scoped(self, node: ast.AST, *, func: bool) -> None:
        self._names.append(node.name)  # type: ignore[attr-defined]
        if func:
            self._funcs.append(node)
        self._alias_stack.append((_subprocess_aliases(node), not func))
        self.generic_visit(node)
        self._alias_stack.pop()
        if func:
            self._funcs.pop()
        self._names.pop()

    def _visible_aliases(self) -> list[tuple[set[str], set[str], set[str]]]:
        *outer, innermost = self._alias_stack
        return [aliases for aliases, is_class in outer if not is_class] + [innermost[0]]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scoped(node, func=False)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scoped(node, func=True)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._scoped(node, func=True)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        visible = self._visible_aliases()
        modules = set().union(*(scope[0] for scope in visible))
        popens = set().union(*(scope[1] for scope in visible))
        helpers = frozenset().union(*(scope[2] for scope in visible))
        is_popen = (
            (isinstance(func, ast.Attribute) and func.attr == "Popen"
             and isinstance(func.value, ast.Name) and func.value.id in modules)
            or (isinstance(func, ast.Name) and func.id in popens)
        )
        scope = self._funcs[-1] if self._funcs else None
        if is_popen and (
            _calls_detach_helper(node, helpers)
            or (scope is not None and _calls_detach_helper(scope, helpers))
        ):
            self.hits.append((node.lineno, ".".join(self._names) or "<module>"))
        self.generic_visit(node)


def _load_daemon_spawn_allowlist(problems: list[str]) -> Counter[str]:
    """Baseline entries; each one covers exactly one launch in its function."""
    allowlist = REPO / DAEMON_SPAWN_ALLOWLIST
    entries: Counter[str] = Counter()
    if not allowlist.is_file():
        return entries
    for lineno, line in enumerate(allowlist.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, _, reason = stripped.partition(" ")
        if "::" not in key or not reason.strip():
            problems.append(
                f"{DAEMON_SPAWN_ALLOWLIST}:{lineno}: expected "
                "'relative/path.py::qualified.function  <reason>'"
            )
            continue
        entries[key] += 1
    return entries


def _load_path_allowlist() -> set[str]:
    if not HEADLESS_GUARD_ALLOWLIST.is_file():
        return set()
    entries: set[str] = set()
    for line in HEADLESS_GUARD_ALLOWLIST.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.add(stripped.split(None, 1)[0])
    return entries


# Rule 6: an installer's start-now PowerShell process launch of a daemon.
_PS_INSTALLER_GLOBS = (
    "plugins/*/scripts/install*.ps1",
    "plugins/*/transports/*/scripts/install*.ps1",
)
_PS_START_PROCESS_RE = re.compile(r"(?<![\w-])Start-Process(?![\w-])", re.IGNORECASE)
# conhost as any token of the statement (quoted, path-qualified or a variable
# such as $conhost), wherever Start-Process's named or positional parameters
# put it.
_PS_CONHOST_TOKEN_RE = re.compile(
    r"(?<![\w.-])['\"]?(?:[^\s'\"(),]*[\\/$])?conhost(?:\.exe)?['\"]?(?![\w.-])",
    re.IGNORECASE,
)
_PS_DAEMON_ALLOW_RE = re.compile(r"#\s*" + re.escape(_DAEMON_ALLOW) + r"[ :]+\S")


def _find_ps_conhost_launches(text: str) -> list[int]:
    """1-based lines of unmarked ``Start-Process ... conhost`` statements.

    A statement continues across trailing-backtick lines. It is allowed by a
    ``# daemon-spawn: allow <why>`` comment on one of its lines or on the
    comment line directly above it. Comments (including ``<# ... #>`` blocks)
    and here-string bodies are not statements and are skipped.
    """
    lines = text.splitlines()
    hits: list[int] = []
    in_here_string = False
    in_block_comment = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if in_block_comment:
            in_block_comment = "#>" not in line
            i += 1
            continue
        if stripped.startswith("<#"):
            in_block_comment = "#>" not in stripped[2:]
            i += 1
            continue
        if in_here_string:
            in_here_string = not stripped.startswith(('"@', "'@"))
            i += 1
            continue
        if stripped.endswith(('@"', "@'")):
            in_here_string = True
            i += 1
            continue
        end = i
        while lines[end].rstrip().endswith("`") and end + 1 < len(lines):
            end += 1
        statement = " ".join(part.rstrip().rstrip("`") for part in lines[i:end + 1])
        code = statement.split("#", 1)[0] if not stripped.startswith("#") else ""
        if code and _PS_START_PROCESS_RE.search(code) and _PS_CONHOST_TOKEN_RE.search(code):
            marked = any(_PS_DAEMON_ALLOW_RE.search(part) for part in lines[i:end + 1])
            above = lines[i - 1].strip() if i else ""
            if not marked and not (above.startswith("#") and _PS_DAEMON_ALLOW_RE.search(above)):
                hits.append(i + 1)
        i = end + 1
    return hits


# YAML-only heuristic: an unquoted or quoted scalar in VALUE position (after
# "- " for a list item, or ": " for a mapping value) -- never a bare key,
# since a key is always followed by ":", not preceded by one. JSON is
# handled structurally instead (see ``_find_json_spawns``): a plain-text
# regex cannot tell a JSON object's KEY from its VALUE when both happen to
# be quoted strings (e.g. ``{"cmd": [...]}``'s own key literally reads
# "cmd", one of the watched program names).
_YAML_VALUE_RE = re.compile(
    r"""(?:^\s*-\s*|:\s*)["']?((?:""" + "|".join(_CONSOLE_PROGRAMS) + r""")(?:\.exe)?)["']?\s*(?:#.*)?$""",
    re.IGNORECASE,
)


def _find_yaml_spawns(text: str) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _ALLOW in line:
            continue
        match = _YAML_VALUE_RE.search(line)
        if not match:
            continue
        name = match.group(1).lower().removesuffix(".exe")
        if name in _CONSOLE_PROGRAMS:
            hits.append((lineno, name))
    return hits


def _find_json_spawns(text: str) -> list[tuple[int, str]]:
    """Walk parsed JSON structurally: only a JSON array's first element is
    ever argv[0]-shaped, so this never mistakes an object KEY (which a
    plain-text regex cannot distinguish from a quoted VALUE) for a spawned
    program -- e.g. ``{"cmd": ["powershell.exe", ...]}``'s own key literally
    reads "cmd", one of the watched program names, but is never argv[0]."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, list):
            if node and isinstance(node[0], str):
                name = _program_name(node[0])
                if name:
                    found.append(node[0])
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)

    walk(data)
    hits: list[tuple[int, str]] = []
    lines = text.splitlines()
    consumed: dict[int, int] = {}  # lineno -> next search offset
    for literal in found:
        name = _program_name(literal) or literal.lower().removesuffix(".exe")
        needle = f'"{literal}"'
        for lineno, line in enumerate(lines, start=1):
            if _ALLOW in line:
                continue
            start = consumed.get(lineno, 0)
            pos = line.find(needle, start)
            if pos == -1:
                continue
            hits.append((lineno, name))
            consumed[lineno] = pos + len(needle)
            break
    return hits


def _find_declarative_spawns(f: Path) -> list[tuple[int, str]]:
    try:
        text = f.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    if f.suffix == ".json":
        return _find_json_spawns(text)
    return _find_yaml_spawns(text)



def _production_src_roots() -> list[Path]:
    roots: list[Path] = []
    if PLUGINS_DIR.is_dir():
        for plugin in sorted(PLUGINS_DIR.iterdir()):
            for name in ("src", "scripts"):
                root = plugin / name
                if root.is_dir():
                    roots.append(root)
            libs = plugin / "libs"
            if libs.is_dir():
                for library in sorted(libs.iterdir()):
                    vendored_src = library / "src"
                    if vendored_src.is_dir():
                        roots.append(vendored_src)
    if LIBS_DIR.is_dir():
        for library in sorted(LIBS_DIR.iterdir()):
            src = library / "src"
            if src.is_dir():
                roots.append(src)
    return roots


class _FlagFinder(_LaunchVisitor):
    """Collect line numbers where a raw process-creation flag is referenced in code."""

    def __init__(self) -> None:
        super().__init__()
        self.hits: list[tuple[int, str]] = []
        self.aliases: dict[str, str] = {}
        self.has_spawn_reference = False
        self.has_popen_reference = False
        self.has_detach_reference = False

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name) and (
            (node.value.id == "subprocess" and node.attr in _SPAWN_FUNCS)
            or (node.value.id == "os" and node.attr == "system")
        ):
            self.has_spawn_reference = True
        if node.attr == "Popen":
            self.has_popen_reference = True
        if node.attr in _DETACH_HELPERS:
            self.has_detach_reference = True
        if node.attr in _FLAG_NAMES:
            self.hits.append((node.lineno, node.attr))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _DETACH_HELPERS:
            self.has_detach_reference = True
        flag = node.id if node.id in _FLAG_NAMES else self.aliases.get(node.id)
        if flag:
            self.hits.append((node.lineno, flag))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if (
                (node.module == "subprocess" and alias.name in _SPAWN_FUNCS)
                or (node.module == "os" and alias.name == "system")
            ):
                self.has_spawn_reference = True
            if node.module == "subprocess" and alias.name == "Popen":
                self.has_popen_reference = True
            if alias.name in _DETACH_HELPERS:
                self.has_detach_reference = True
            if alias.name in _FLAG_NAMES:
                self.aliases[alias.asname or alias.name] = alias.name
                self.hits.append((node.lineno, alias.name))

    def _record_assignment(
        self,
        target: ast.expr,
        value: ast.expr | None,
        lineno: int,
    ) -> None:
        if not isinstance(target, ast.Name) or value is None:
            return
        flag: str | None = None
        if isinstance(value, ast.Attribute) and value.attr in _FLAG_NAMES:
            flag = value.attr
        elif isinstance(value, ast.Name):
            flag = (
                value.id
                if value.id in _FLAG_NAMES
                else self.aliases.get(value.id)
            )
        elif (
            isinstance(value, ast.Constant)
            and value.value == _CREATE_NEW_CONSOLE_VALUE
            and target.id.upper().endswith(_UNSAFE_FLAG)
        ):
            flag = _UNSAFE_FLAG
        if flag:
            self.aliases[target.id] = flag
            self.hits.append((lineno, flag))

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._record_assignment(target, node.value, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record_assignment(node.target, node.value, node.lineno)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # getattr(subprocess, "CREATE_NO_WINDOW", ...) -- the flag name as a string
        # literal. A docstring is one big Constant whose value is the prose, so it
        # never equals a bare flag name.
        if isinstance(node.value, str) and node.value in _FLAG_NAMES:
            self.hits.append((node.lineno, node.value))


def _find_flags(f: Path) -> tuple[str, list[tuple[int, str]]]:
    text = f.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(f))
    finder = _FlagFinder()
    finder.visit(tree)
    return text, sorted(set(finder.hits))


def _allowed(lines: list[str], lineno: int, marker: str = _ALLOW) -> bool:
    line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
    comments: list[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(line).readline):
            if tok.type == tokenize.COMMENT:
                comments.append(tok.string)
    except (IndentationError, tokenize.TokenError):
        # A multiline call's opening line ends inside an open bracket: keep the
        # comments tokenized before EOF.
        pass
    for comment in comments:
        text = comment.removeprefix("#").strip()
        if not text.startswith(marker):
            continue
        suffix = text[len(marker):]
        if not suffix or suffix[0] not in " :":
            continue
        reason = suffix.lstrip(" :").strip()
        if reason:
            return True
    return False


def verify() -> list[str]:
    problems: list[str] = []
    cache: dict[Path, tuple[str, list[tuple[int, str]], list[tuple[int, str]]]] = {}
    detached: dict[Path, list[tuple[int, str]]] = {}
    parse_failures: set[Path] = set()
    adopting_plugins = _adopting_plugins()
    daemon_sources = {
        f
        for plugin in adopting_plugins
        for f in _iter_py(plugin / "src")
    }

    def scan(f: Path) -> tuple[str, list[tuple[int, str]], list[tuple[int, str]]]:
        if f in cache:
            return cache[f]
        text = f.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text, filename=str(f))
            flags = _FlagFinder()
            flags.visit(tree)
            # Necessary AST references admit later scans, including normalized identifiers.
            spawn_hits = []
            if flags.has_spawn_reference:
                spawns = _SpawnFinder(text)
                spawns.visit(tree)
                spawn_hits = spawns.hits
            if (
                f in daemon_sources
                and flags.has_popen_reference and flags.has_detach_reference
            ):
                daemons = _DetachedSpawnFinder(tree)
                daemons.visit(tree)
                detached[f] = sorted(daemons.hits)  # one hit per launch, even on one line
            result = (text, sorted(set(flags.hits)), sorted(set(spawn_hits)))
        except SyntaxError as exc:
            result = (text, [], [])
            if f not in parse_failures:
                parse_failures.add(f)
                rel = f.relative_to(REPO).as_posix()
                problems.append(
                    f"{rel}:{exc.lineno or 1}: cannot parse production Python; "
                    f"headless launch policy cannot be verified: {exc.msg}"
                )
        cache[f] = result
        return result

    # CREATE_NEW_CONSOLE is unsafe for background work regardless of whether a
    # package has adopted agent-procutil. Scan canonical shared libs as well as
    # plugin source so a vendored primitive cannot bypass the adoption gate.
    for src in _production_src_roots():
        for f in _iter_py(src):
            text, hits, _ = scan(f)
            lines = text.splitlines()
            rel = f.relative_to(REPO).as_posix()
            for lineno, tok in hits:
                if tok != _UNSAFE_FLAG or _allowed(lines, lineno):
                    continue
                line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
                problems.append(
                    f"{rel}:{lineno}: unsafe '{tok}' -- Windows Default Terminal "
                    "may surface it even with SW_HIDE; use a shared no-window "
                    f"primitive, or add '# {_ALLOW} <interactive reason>'  ::  "
                    f"{line.strip()}"
                )

    for plugin in adopting_plugins:
        src = plugin / "src"
        if not src.is_dir():
            continue
        for f in _iter_py(src):
            text, hits, _ = scan(f)
            if not hits:
                continue
            lines = text.splitlines()
            rel = f.relative_to(REPO).as_posix()
            for lineno, tok in hits:
                if tok == _UNSAFE_FLAG:
                    continue  # The stronger repository-wide rule reports it.
                line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
                if _allowed(lines, lineno):
                    continue
                problems.append(
                    f"{rel}:{lineno}: raw '{tok}' -- use agent_procutil "
                    f"(no_window_kwargs / detached_kwargs / no_window_flags), or add "
                    f"'# {_ALLOW} <why>'  ::  {line.strip()}"
                )

    # Rule 3: an unsuppressed console-program spawn, regardless of
    # agent-procutil adoption -- the simpler, more common production
    # incident neither rule above catches (see module docstring). Also
    # consults the shared baseline allowlist (tools/headless-guard.allow),
    # since this rule's rollout found a real pre-existing backlog too large
    # to triage line-by-line with inline comments in one pass -- see that
    # file's own header for the tracking issue.
    path_allow = _load_path_allowlist()
    for src in _production_src_roots():
        for f in _iter_py(src):
            text, _, hits = scan(f)
            if not hits:
                continue
            lines = text.splitlines()
            rel = f.relative_to(REPO).as_posix()
            for lineno, program in hits:
                if _allowed(lines, lineno) or f"{rel}:{lineno}" in path_allow:
                    continue
                line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
                problems.append(
                    f"{rel}:{lineno}: unsuppressed console spawn of '{program}' -- "
                    "a window-less parent (a detached waiter, a background "
                    "daemon) would surface a brand-new visible console for "
                    "this; pass creationflags/startupinfo or splat "
                    f"agent_procutil's no_window_kwargs()/detached_kwargs() "
                    "(launch a resident daemon with agent_procutil.spawn_daemon()), "
                    f"or add '# {_ALLOW} <why>'  ::  {line.strip()}"
                )

    # Rule 4: the same program list declared in JSON/YAML, which this guard
    # cannot trace to its eventual Python spawn site -- reported, not
    # silently verified, unless explicitly allowlisted.
    for src in _production_src_roots():
        for f in _iter_declarative(src):
            rel = f.relative_to(REPO).as_posix()
            for lineno, program in _find_declarative_spawns(f):
                if f"{rel}:{lineno}" in path_allow:
                    continue
                problems.append(
                    f"{rel}:{lineno}: declarative spawn of '{program}' -- "
                    "cannot verify its eventual consumer suppresses a "
                    "console; confirm it does, then add a comment containing "
                    f"'{_ALLOW} <why>' (YAML) or list "
                    f"'{rel}:{lineno}  <why>' in "
                    "tools/headless-guard.allow (JSON)"
                )

    # Rule 5: a resident daemon launches through spawn_daemon (see module
    # docstring); tools/daemon-spawn.allow is a shrink-only baseline.
    daemon_allow = _load_daemon_spawn_allowlist(problems)
    unmatched = Counter(daemon_allow)
    for plugin in adopting_plugins:
        src = plugin / "src"
        if not src.is_dir():
            continue
        for f in _iter_py(src):
            text = scan(f)[0]
            lines = text.splitlines()
            rel = f.relative_to(REPO).as_posix()
            for lineno, qualname in detached.get(f, []):
                key = f"{rel}::{qualname}"
                # An inline allowance never consumes a baseline entry.
                if _allowed(lines, lineno, _DAEMON_ALLOW):
                    continue
                if unmatched[key] > 0:
                    unmatched[key] -= 1
                    continue
                problems.append(
                    f"{rel}:{lineno}: hand-rolled detached Popen in {qualname} -- launch a "
                    "resident daemon with agent_procutil.spawn_daemon() (explicit cwd, "
                    f"log, Job breakaway); mark a non-daemon child '# {_DAEMON_ALLOW} <why>'"
                )
    for key, count in sorted(unmatched.items()):
        problems.extend(
            f"tools/daemon-spawn.allow: stale entry {key} -- it no longer hand-rolls that "
            "many detached Popen calls; remove one entry per migrated launch"
            for _ in range(count)
        )

    # Rule 6: an installer starts a daemon now through the service's user-mode
    # ensure path (or an escape such as WMI), never a contained Start-Process.
    for pattern in _PS_INSTALLER_GLOBS:
        for f in sorted(REPO.glob(pattern)):
            rel = f.relative_to(REPO).as_posix()
            text = f.read_text(encoding="utf-8-sig")
            for lineno in _find_ps_conhost_launches(text):
                problems.append(
                    f"{rel}:{lineno}: installer starts a process through Start-Process conhost "
                    "-- the child stays in the installer's Job and dies with an SSH session or "
                    "updater; start a daemon through its user-mode ensure path "
                    "(agent_procutil.spawn_daemon) or WMI Win32_Process.Create, or mark a "
                    f"child the caller owns '# {_DAEMON_ALLOW} <why>'"
                )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true",
                    help="print the agent-procutil-adopting plugins this guard checks")
    args = ap.parse_args()
    if args.list:
        for p in _adopting_plugins():
            print(p.relative_to(REPO).as_posix())
        return 0
    problems = verify()
    if problems:
        print("check-headless-launch: FAILED", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nBackground launches must not allocate a new Windows console, and an "
            "agent-procutil-adopting plugin must not hand-roll process-creation "
            "flags. Route launches through the shared helpers, or mark a genuine "
            f"interactive/low-level exception with '# {_ALLOW} <why>'.",
            file=sys.stderr,
        )
        return 1
    checked = len(_adopting_plugins())
    print(f"check-headless-launch: OK ({checked} agent-procutil adopters).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
