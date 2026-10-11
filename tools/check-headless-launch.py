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
``_process_tree_kwargs``/``no_window_flags``). Only a **literal, resolvable**
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
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
LIBS_DIR = REPO / "libs"
HEADLESS_GUARD_ALLOWLIST = REPO / "tools" / "headless-guard.allow"

_ALLOW = "headless-guard: allow"
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
    patterns = ("*.json", "*.yaml", "*.yml")
    candidates: list[list[Path]] = [[] for _ in patterns]
    for f in src.rglob("*.[jy]*"):
        if _SKIP_DIR_PARTS & set(f.relative_to(src).parts):
            continue
        for pattern, matches in zip(patterns, candidates):
            if f.match(pattern):
                matches.append(f)
                break
    # Keep the original extension-grouped reporting order.
    for matches in candidates:
        yield from matches


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


class _SpawnFinder(ast.NodeVisitor):
    """Collect (lineno, program) for an unsuppressed console-program spawn.

    Only a **literal, resolvable** argv[0] -- a string constant, or a
    list/tuple literal's first element -- is ever checked; anything
    dynamically built (a variable, a call, an f-string) is silently skipped
    rather than risk a false positive this guard cannot actually verify.
    """

    def __init__(self, text: str) -> None:
        self.hits: list[tuple[int, str]] = []
        self._text = text
        self._spawn_aliases: dict[str, str] = {}
        self._system_aliases: set[str] = set()
        self._func_stack: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        self._lines: list[str] | None = None
        self._safe_scopes: dict[ast.FunctionDef | ast.AsyncFunctionDef | None, bool] = {}

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
        if self._lines is None:
            self._lines = self._text.splitlines()
        lines = self._lines
        end = getattr(func, "end_lineno", None) or len(lines)
        return "\n".join(lines[func.lineno - 1:end])

    def _enclosing_has_safe_helper(self, node: ast.AST) -> bool:
        scope = self._func_stack[-1] if self._func_stack else None
        if scope not in self._safe_scopes:
            source = self._enclosing_source(node)
            self._safe_scopes[scope] = any(helper in source for helper in _SAFE_HELPERS)
        return self._safe_scopes[scope]

    def _is_safe(self, call: ast.Call) -> bool:
        for kw in call.keywords:
            if kw.arg in _SAFE_KWARGS:
                return True
        return self._enclosing_has_safe_helper(call)

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
            if self._enclosing_has_safe_helper(node):
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


class _FlagFinder(ast.NodeVisitor):
    """Collect line numbers where a raw process-creation flag is referenced in code."""

    def __init__(self) -> None:
        self.hits: list[tuple[int, str]] = []
        self.aliases: dict[str, str] = {}

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _FLAG_NAMES:
            self.hits.append((node.lineno, node.attr))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        flag = node.id if node.id in _FLAG_NAMES else self.aliases.get(node.id)
        if flag:
            self.hits.append((node.lineno, flag))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
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


def _allowed(lines: list[str], lineno: int) -> bool:
    line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
    try:
        tokens = tokenize.generate_tokens(io.StringIO(line).readline)
        comments = [tok.string for tok in tokens if tok.type == tokenize.COMMENT]
    except (IndentationError, tokenize.TokenError):
        return False
    for comment in comments:
        text = comment.removeprefix("#").strip()
        if not text.startswith(_ALLOW):
            continue
        suffix = text[len(_ALLOW):]
        if not suffix or suffix[0] not in " :":
            continue
        reason = suffix.lstrip(" :").strip()
        if reason:
            return True
    return False


def verify() -> list[str]:
    problems: list[str] = []
    roots = _production_src_roots()
    candidates = {src: tuple(_iter_py(src)) for src in roots}
    cache: dict[Path, tuple[str, list[tuple[int, str]], list[tuple[int, str]]]] = {}
    content_cache: dict[str, tuple[list[tuple[int, str]], list[tuple[int, str]]]] = {}
    parse_failures: set[Path] = set()

    def scan(f: Path) -> tuple[str, list[tuple[int, str]], list[tuple[int, str]]]:
        if f in cache:
            return cache[f]
        try:
            text = f.read_text(encoding="utf-8")
            if text not in content_cache:
                tree = ast.parse(text, filename=str(f))
                flags = _FlagFinder()
                flags.visit(tree)
                spawn_finder = _SpawnFinder(text)
                spawn_finder.visit(tree)
                content_cache[text] = (sorted(set(flags.hits)), sorted(set(spawn_finder.hits)))
            flag_hits, spawn_hits = content_cache[text]
            result = (text, flag_hits, spawn_hits)
        except SyntaxError as exc:
            text = f.read_text(encoding="utf-8")
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
    for src in roots:
        for f in candidates[src]:
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

    for plugin in _adopting_plugins():
        src = plugin / "src"
        if not src.is_dir():
            continue
        for f in candidates[src]:
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
    for src in roots:
        for f in candidates[src]:
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
                    f"agent_procutil's no_window_kwargs()/detached_kwargs(), "
                    f"or add '# {_ALLOW} <why>'  ::  {line.strip()}"
                )

    # Rule 4: the same program list declared in JSON/YAML, which this guard
    # cannot trace to its eventual Python spawn site -- reported, not
    # silently verified, unless explicitly allowlisted.
    for src in roots:
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
