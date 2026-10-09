"""Tests for the headless-launch guard (windows-launch-hardening #786).

The guard flags a raw process-creation flag literal in an ``agent-procutil``
adopter and forbids ``CREATE_NEW_CONSOLE`` across all production plugin and
canonical shared-library sources. It is AST-based (docstrings/comments never
count) and supports an inline ``# headless-guard: allow <reason>`` escape hatch.

Run:  python -m pytest tools/test_check_headless_launch.py
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent / "check-headless-launch.py"
_spec = importlib.util.spec_from_file_location("check_headless_launch", _SCRIPT)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _mk_plugin(root: Path, name: str, *, adopts: bool, body: str) -> None:
    p = root / "plugins" / name
    (p / "src" / name.replace("-", "_")).mkdir(parents=True)
    dep = '"agent-procutil",' if adopts else ""
    (p / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\ndependencies = [{dep}]\n', encoding="utf-8")
    (p / "src" / name.replace("-", "_") / "mod.py").write_text(body, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "REPO", tmp_path)
    monkeypatch.setattr(guard, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(guard, "LIBS_DIR", tmp_path / "libs")
    return tmp_path


def test_identical_source_is_analyzed_once_but_every_path_is_reported(repo, monkeypatch):
    body = (
        "import subprocess\n"
        "subprocess.Popen(['git', 'status'], creationflags=subprocess.CREATE_NEW_CONSOLE)\n"
    )
    _mk_plugin(repo, "alpha", adopts=True, body=body)
    _mk_plugin(repo, "beta", adopts=True, body=body)
    calls = []
    original_parse = guard.ast.parse

    def parse(text, *args, **kwargs):
        calls.append(text)
        return original_parse(text, *args, **kwargs)

    monkeypatch.setattr(guard.ast, "parse", parse)
    problems = guard.verify()

    assert calls == [body]
    assert any("plugins/alpha/src/alpha/mod.py:2:" in problem for problem in problems)
    assert any("plugins/beta/src/beta/mod.py:2:" in problem for problem in problems)


def test_declarative_single_walk_preserves_exact_candidates(tmp_path, monkeypatch):
    src = tmp_path / "src"
    names = [
        "task.json", "task.yaml", "task.yml", "TASK.JSON", "TASK.YAML",
        "TASK.YML", ".hidden.json", "ignore.js", "ignore.py", "ignore.yam",
        "nested/task.json", "nested/task.yaml", "nested/task.yml",
    ]
    names.extend(f"{skip}/nested/task.json" for skip in guard._SKIP_DIR_PARTS)
    names.extend(f"{skip}/nested/task.yaml" for skip in guard._SKIP_DIR_PARTS)
    names.extend(f"{skip}/nested/task.yml" for skip in guard._SKIP_DIR_PARTS)
    for name in names:
        path = src / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    (src / "directory.json").mkdir()
    # Exclusions are relative to the scan root, not its ancestors.
    excluded_root = src / "libs"
    roots = (src, excluded_root)
    expected = {
        root: [
            f
            for pattern in ("*.json", "*.yaml", "*.yml")
            for f in root.rglob(pattern)
            if not guard._SKIP_DIR_PARTS.intersection(f.relative_to(root).parts)
        ]
        for root in roots
    }
    original = Path.rglob
    walks = []

    def rglob(path, pattern):
        walks.append((path, pattern))
        yield from original(path, pattern)

    monkeypatch.setattr(Path, "rglob", rglob)
    for root in roots:
        assert list(guard._iter_declarative(root)) == expected[root]
    assert walks == [(root, "*.[jy]*") for root in roots]


def test_verify_reuses_candidates_and_ast_without_skipping_roots(repo, monkeypatch):
    body = (
        "import subprocess\n"
        "x = subprocess.CREATE_NO_WINDOW\n"
        "x = subprocess.CREATE_NEW_CONSOLE\n"
        'subprocess.run(["cmd"])\n'
    )
    _mk_plugin(repo, "agent-cache", adopts=True, body=body)
    other_roots = [
        repo / "plugins" / "agent-cache" / "scripts",
        repo / "plugins" / "agent-cache" / "libs" / "sample" / "src",
        repo / "libs" / "sample" / "src",
    ]
    for root in other_roots:
        root.mkdir(parents=True)
        (root / "safe.py").write_text("x = 1\n", encoding="utf-8")
    roots = guard._production_src_roots()
    expected_files = [f for root in roots for f in guard._iter_py(root)]
    expected_parses = []
    contents = set()
    for path in expected_files:
        text = path.read_text(encoding="utf-8")
        if text not in contents:
            contents.add(text)
            expected_parses.append(path)
    for root in roots:
        for skip in guard._SKIP_DIR_PARTS:
            excluded = root / skip / "invalid.py"
            excluded.parent.mkdir(parents=True)
            excluded.write_text("if (\n", encoding="utf-8")

    original_iter = guard._iter_py
    original_parse = ast.parse
    enumerated = []
    parsed = []

    def iter_py(src):
        enumerated.append(src)
        yield from original_iter(src)

    def parse(text, filename="<unknown>", *args, **kwargs):
        parsed.append(Path(filename))
        return original_parse(text, filename, *args, **kwargs)

    monkeypatch.setattr(guard, "_iter_py", iter_py)
    monkeypatch.setattr(guard.ast, "parse", parse)
    rel = "plugins/agent-cache/src/agent_cache/mod.py"
    assert guard.verify() == [
        f"{rel}:3: unsafe 'CREATE_NEW_CONSOLE' -- Windows Default Terminal "
        "may surface it even with SW_HIDE; use a shared no-window "
        "primitive, or add '# headless-guard: allow <interactive reason>'  ::  "
        "x = subprocess.CREATE_NEW_CONSOLE",
        f"{rel}:2: raw 'CREATE_NO_WINDOW' -- use agent_procutil "
        "(no_window_kwargs / detached_kwargs / no_window_flags), or add "
        "'# headless-guard: allow <why>'  ::  x = subprocess.CREATE_NO_WINDOW",
        f"{rel}:4: unsuppressed console spawn of 'cmd' -- "
        "a window-less parent (a detached waiter, a background "
        "daemon) would surface a brand-new visible console for "
        "this; pass creationflags/startupinfo or splat "
        "agent_procutil's no_window_kwargs()/detached_kwargs(), "
        "or add '# headless-guard: allow <why>'  ::  subprocess.run([\"cmd\"])",
    ]
    assert enumerated == roots
    assert parsed == expected_parses


def test_cached_scope_safety_matches_uncached_algorithm():
    text = (
        "import subprocess, os\n"
        "from subprocess import run as launch\n"
        "from os import system as shell\n"
        "subprocess.run(['cmd'])\n"
        "shell('git status')\n"
        "def safe():\n"
        "    subprocess.run(['cmd'])\n"
        "    no_window_kwargs()\n"
        "    os.system('git status')\n"
        "    def nested_unsafe():\n"
        "        launch(['ssh'])\n"
        "        shell('cmd /c echo')\n"
        "async def unsafe():\n"
        "    launch(['pwsh'])\n"
        "    subprocess.Popen(['python'])\n"
        "    subprocess.call(['cmd'], creationflags=0)\n"
        "    def nested_safe():\n"
        "        detached_kwargs()\n"
        "        subprocess.run(['node'])\n"
    )

    class UncachedFinder(guard._SpawnFinder):
        def _enclosing_has_safe_helper(self, node):
            if self._func_stack:
                func = self._func_stack[-1]
                lines = self._text.splitlines()
                end = getattr(func, "end_lineno", None) or len(lines)
                source = "\n".join(lines[func.lineno - 1:end])
            else:
                source = self._text
            return any(helper in source for helper in guard._SAFE_HELPERS)

    class CountingFinder(guard._SpawnFinder):
        def __init__(self, text):
            super().__init__(text)
            self.source_scopes = []

        def _enclosing_source(self, node):
            self.source_scopes.append(self._func_stack[-1] if self._func_stack else None)
            return super()._enclosing_source(node)

    tree = ast.parse(text)
    legacy = UncachedFinder(text)
    optimized = CountingFinder(text)
    legacy.visit(tree)
    optimized.visit(tree)
    assert optimized.hits == legacy.hits == [(11, "ssh"), (12, "cmd")]
    assert len(optimized.source_scopes) == len(set(optimized.source_scopes)) == 5


def test_flags_raw_flag_in_adopting_plugin(repo):
    _mk_plugin(repo, "agent-foo", adopts=True,
               body="import subprocess\nx = subprocess.CREATE_NO_WINDOW\n")
    problems = guard.verify()
    assert any("agent-foo" in p and "CREATE_NO_WINDOW" in p for p in problems)


def test_ignores_non_adopting_plugin(repo):
    _mk_plugin(repo, "agent-bar", adopts=False,
               body="import subprocess\nx = subprocess.CREATE_NO_WINDOW\n")
    assert guard.verify() == []


def test_forbids_new_console_in_non_adopting_plugin(repo):
    _mk_plugin(repo, "agent-window", adopts=False,
               body="import subprocess\nx = subprocess.CREATE_NEW_CONSOLE\n")
    problems = guard.verify()
    assert any("unsafe 'CREATE_NEW_CONSOLE'" in p for p in problems)


def test_forbids_new_console_in_canonical_shared_library(repo):
    src = repo / "libs" / "ssh-manager" / "src" / "ssh_manager"
    src.mkdir(parents=True)
    (src / "process.py").write_text(
        "import subprocess\nx = subprocess.CREATE_NEW_CONSOLE\n",
        encoding="utf-8",
    )
    problems = guard.verify()
    assert any("libs/ssh-manager" in p and "CREATE_NEW_CONSOLE" in p
               for p in problems)


def test_forbids_aliased_new_console_import(repo):
    _mk_plugin(
        repo,
        "agent-alias",
        adopts=False,
        body=(
            "from subprocess import CREATE_NEW_CONSOLE as NEW_CONSOLE\n"
            "x = NEW_CONSOLE\n"
        ),
    )
    assert any("CREATE_NEW_CONSOLE" in p for p in guard.verify())


def test_forbids_new_console_imported_from_low_level_module(repo):
    _mk_plugin(
        repo,
        "agent-winapi",
        adopts=False,
        body="from _winapi import CREATE_NEW_CONSOLE as FLAG\nx = FLAG\n",
    )
    assert any("CREATE_NEW_CONSOLE" in p for p in guard.verify())


def test_forbids_named_new_console_numeric_constant(repo):
    _mk_plugin(
        repo,
        "agent-numeric",
        adopts=False,
        body="_CREATE_NEW_CONSOLE = 0x00000010\nx = _CREATE_NEW_CONSOLE\n",
    )
    assert any("CREATE_NEW_CONSOLE" in p for p in guard.verify())


def test_forbids_new_console_in_vendored_library(repo):
    src = (
        repo / "plugins" / "agent-vendor" / "libs" / "ssh-manager"
        / "src" / "ssh_manager"
    )
    src.mkdir(parents=True)
    (src / "process.py").write_text(
        "import subprocess\nx = subprocess.CREATE_NEW_CONSOLE\n",
        encoding="utf-8",
    )
    assert any(
        "plugins/agent-vendor/libs/ssh-manager" in p for p in guard.verify()
    )


def test_forbids_new_console_in_shipped_plugin_script(repo):
    scripts = repo / "plugins" / "agent-script" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "launch.py").write_text(
        "import subprocess\nx = subprocess.CREATE_NEW_CONSOLE\n",
        encoding="utf-8",
    )
    assert any("plugins/agent-script/scripts" in p for p in guard.verify())


def test_production_syntax_error_fails_closed(repo):
    _mk_plugin(repo, "agent-invalid", adopts=False, body="if (\n")
    problems = guard.verify()
    assert any("cannot parse production Python" in p for p in problems)


def test_forbids_annotated_new_console_numeric_constant(repo):
    _mk_plugin(
        repo,
        "agent-annotated",
        adopts=False,
        body="_CREATE_NEW_CONSOLE: int = 0x00000010\nx = _CREATE_NEW_CONSOLE\n",
    )
    assert any("CREATE_NEW_CONSOLE" in p for p in guard.verify())


def test_docstring_mention_not_flagged(repo):
    _mk_plugin(repo, "agent-doc", adopts=True,
               body='"""Uses CREATE_NO_WINDOW and DETACHED_PROCESS in prose."""\nx = 1\n')
    assert guard.verify() == []


def test_allow_comment_suppresses(repo):
    _mk_plugin(repo, "agent-ok", adopts=True,
               body="import subprocess\n"
                    "x = subprocess.DETACHED_PROCESS  # headless-guard: allow: daemon\n")
    assert guard.verify() == []


def test_allow_comment_suppresses_interactive_new_console(repo):
    _mk_plugin(
        repo,
        "agent-interactive",
        adopts=False,
        body=(
            "import subprocess\n"
            "x = subprocess.CREATE_NEW_CONSOLE  "
            "# headless-guard: allow interactive terminal\n"
        ),
    )
    assert guard.verify() == []


def test_bare_allow_comment_does_not_suppress(repo):
    _mk_plugin(
        repo,
        "agent-empty-allow",
        adopts=False,
        body=(
            "import subprocess\n"
            "x = subprocess.CREATE_NEW_CONSOLE  # headless-guard: allow\n"
        ),
    )
    assert any("CREATE_NEW_CONSOLE" in p for p in guard.verify())


@pytest.mark.parametrize("directive", ["allowed", "allowance"])
def test_allow_prefix_word_does_not_suppress(repo, directive):
    _mk_plugin(
        repo,
        f"agent-{directive}",
        adopts=False,
        body=(
            "import subprocess\n"
            f"x = subprocess.CREATE_NEW_CONSOLE  # headless-guard: {directive}\n"
        ),
    )
    assert any("CREATE_NEW_CONSOLE" in p for p in guard.verify())


def test_allow_text_in_string_does_not_suppress(repo):
    _mk_plugin(
        repo,
        "agent-string-allow",
        adopts=False,
        body=(
            'note = "headless-guard: allow interactive"\n'
            "import subprocess\n"
            "x = subprocess.CREATE_NEW_CONSOLE\n"
        ),
    )
    assert any("CREATE_NEW_CONSOLE" in p for p in guard.verify())


def test_getattr_string_flag_flagged(repo):
    _mk_plugin(repo, "agent-ga", adopts=True,
               body='import subprocess\n'
                    'x = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)\n')
    assert any("CREATE_NEW_PROCESS_GROUP" in p for p in guard.verify())


# -- Rule 3: unsuppressed console-program spawn (#789) -----------------------


def test_flags_unsuppressed_spawn_list_literal(repo):
    _mk_plugin(repo, "agent-spawn", adopts=False,
               body='import subprocess\n'
                    'subprocess.run(["cmd.exe", "/c", "dir"], check=False)\n')
    problems = guard.verify()
    assert any("unsuppressed console spawn of 'cmd'" in p for p in problems)


def test_flags_unsuppressed_spawn_regardless_of_adoption(repo):
    _mk_plugin(repo, "agent-spawn2", adopts=True,
               body='import subprocess\n'
                    'subprocess.Popen(["powershell.exe", "-Command", "x"])\n')
    assert any("powershell" in p for p in guard.verify())


def test_allows_spawn_with_creationflags(repo):
    _mk_plugin(repo, "agent-ok-flags", adopts=False,
               body='import subprocess\n'
                    'subprocess.run(["cmd.exe", "/c", "x"], creationflags=0x08000000)\n')
    assert guard.verify() == []


def test_allows_spawn_splatting_no_window_kwargs(repo):
    _mk_plugin(repo, "agent-ok-splat", adopts=True,
               body='import subprocess\n'
                    'from agent_procutil import no_window_kwargs\n'
                    'subprocess.run(["pwsh", "-c", "x"], **no_window_kwargs())\n')
    assert guard.verify() == []


def test_allows_spawn_via_kwargs_dict_update_in_same_function(repo):
    _mk_plugin(
        repo, "agent-ok-update", adopts=True,
        body=(
            "import subprocess\n"
            "from agent_procutil import detached_kwargs\n"
            "def spawn(cmd):\n"
            "    kwargs = {}\n"
            "    kwargs.update(detached_kwargs())\n"
            "    subprocess.Popen(cmd, **kwargs)\n"
        ),
    )
    assert guard.verify() == []


def test_flags_literal_argv_wrapped_in_a_safety_dead_function(repo):
    """The real production incident this rule generalizes from
    (ThomasMichon/copilot-extensions#789): a bare list-literal argv reaching
    ``subprocess.run`` with no window handling anywhere in its function."""
    _mk_plugin(
        repo, "agent-bad-fn", adopts=True,
        body=(
            "import subprocess\n"
            "def runner():\n"
            "    proc = subprocess.run(['cmd.exe', '/c', 'dir'], check=False)\n"
            "    return proc.returncode\n"
        ),
    )
    problems = guard.verify()
    assert any("unsuppressed console spawn of 'cmd'" in p for p in problems)


def test_known_limitation_does_not_resolve_argv_rebuilt_via_list_call(repo):
    """Documented scope boundary, not a bug: ``list(cmd)``/``tuple(cmd)``
    wraps a *call* expression, not a literal, so its elements are opaque to
    this guard -- exactly the shape of the real incident's own
    ``subprocess.run(list(cmd), check=False)``, where ``cmd`` was a
    caller-supplied ``tuple[str, ...]`` parameter. Resolving this would need
    real interprocedural data-flow analysis this guard deliberately doesn't
    attempt, to keep its false-positive rate low across two large codebases;
    the companion literal-argv rule above is what actually generalizes from
    that incident."""
    _mk_plugin(
        repo, "agent-dynamic-wrap", adopts=False,
        body=(
            "import subprocess\n"
            "def runner(cmd):\n"
            "    proc = subprocess.run(list(cmd), check=False)\n"
            "    return proc.returncode\n"
        ),
    )
    assert guard.verify() == []


def test_ignores_dynamic_unresolvable_argv(repo):
    _mk_plugin(repo, "agent-dynamic", adopts=False,
               body='import subprocess\n'
                    'def spawn(cmd):\n'
                    '    subprocess.run(cmd, check=False)\n')
    assert guard.verify() == []


def test_ignores_non_console_program(repo):
    _mk_plugin(repo, "agent-benign", adopts=False,
               body='import subprocess\n'
                    'subprocess.run(["gh", "pr", "view"], check=False)\n')
    assert guard.verify() == []


def test_ignores_pythonw_gui_subsystem(repo):
    _mk_plugin(repo, "agent-pythonw", adopts=False,
               body='import subprocess\n'
                    'subprocess.Popen(["pythonw.exe", "-m", "x"])\n')
    assert guard.verify() == []


def test_flags_os_system_console_program(repo):
    _mk_plugin(repo, "agent-ossystem", adopts=False,
               body='import os\nos.system("cmd.exe /c dir")\n')
    assert any("unsuppressed console spawn of 'cmd'" in p for p in guard.verify())


def test_allow_comment_suppresses_unsuppressed_spawn(repo):
    _mk_plugin(
        repo, "agent-spawn-allow", adopts=False,
        body=(
            "import subprocess\n"
            'subprocess.run(["ssh", "host", "cmd"], '
            f"check=False)  # {guard._ALLOW}: interactive\n"
        ),
    )
    assert guard.verify() == []


def test_flags_imported_run_alias(repo):
    _mk_plugin(repo, "agent-bare-run", adopts=False,
               body='from subprocess import run\n'
                    'run(["git", "status"])\n')
    # "git" IS a watched program (ssh/scp wrappers often shell out via it);
    # confirm the bare-imported-name form resolves identically to the
    # subprocess.run(...) form.
    assert any("unsuppressed console spawn of 'git'" in p for p in guard.verify())


# -- Rule 4: declarative (JSON/YAML) console-program spawn (#789) ------------


def test_flags_declarative_json_spawn(repo):
    plugin = repo / "plugins" / "agent-json"
    (plugin / "scripts").mkdir(parents=True)
    (plugin / "pyproject.toml").write_text('[project]\nname = "agent-json"\n', encoding="utf-8")
    (plugin / "scripts" / "task.json").write_text(
        '{"cmd": ["powershell.exe", "-File", "x.ps1"]}', encoding="utf-8",
    )
    assert any("declarative spawn of 'powershell'" in p for p in guard.verify())


def test_json_repeated_identical_literal_maps_to_distinct_occurrences(repo):
    """Regression: multiple arrays sharing an identical literal argv[0]
    (e.g. several "python ..." prerequisite entries) must not all collapse
    onto the first occurrence's position."""
    plugin = repo / "plugins" / "agent-json-repeat"
    (plugin / "scripts").mkdir(parents=True)
    (plugin / "pyproject.toml").write_text(
        '[project]\nname = "agent-json-repeat"\n', encoding="utf-8")
    (plugin / "scripts" / "task.json").write_text(
        '{\n  "a": ["python", "x"],\n  "b": ["python", "y"]\n}\n', encoding="utf-8",
    )
    problems = [p for p in guard.verify() if "agent-json-repeat" in p]
    assert len(problems) == 2
    linenos = sorted(int(p.split(":")[1]) for p in problems)
    assert linenos == [2, 3]


def test_flags_declarative_yaml_spawn(repo):
    plugin = repo / "plugins" / "agent-yaml"
    (plugin / "scripts").mkdir(parents=True)
    (plugin / "pyproject.toml").write_text('[project]\nname = "agent-yaml"\n', encoding="utf-8")
    (plugin / "scripts" / "task.yaml").write_text(
        "cmd:\n  - cmd.exe\n  - /c\n  - dir\n", encoding="utf-8",
    )
    assert any("declarative spawn of 'cmd'" in p for p in guard.verify())


def test_declarative_yaml_allow_comment_suppresses(repo):
    plugin = repo / "plugins" / "agent-yaml-ok"
    (plugin / "scripts").mkdir(parents=True)
    (plugin / "pyproject.toml").write_text('[project]\nname = "agent-yaml-ok"\n', encoding="utf-8")
    (plugin / "scripts" / "task.yaml").write_text(
        f"cmd:\n  - cmd.exe  # {guard._ALLOW}: reviewed, consumer applies no_window_kwargs\n",
        encoding="utf-8",
    )
    assert guard.verify() == []


def test_declarative_json_allowlist_file_suppresses(repo, monkeypatch):
    plugin = repo / "plugins" / "agent-json-ok"
    (plugin / "scripts").mkdir(parents=True)
    (plugin / "pyproject.toml").write_text('[project]\nname = "agent-json-ok"\n', encoding="utf-8")
    (plugin / "scripts" / "task.json").write_text(
        '{"cmd": ["pwsh", "-c", "x"]}', encoding="utf-8",
    )
    rel = "plugins/agent-json-ok/scripts/task.json:1"
    allow = repo / "tools" / "headless-guard.allow"
    allow.parent.mkdir(parents=True, exist_ok=True)
    allow.write_text(f"{rel}  reviewed, consumer applies no_window_kwargs\n", encoding="utf-8")
    monkeypatch.setattr(guard, "HEADLESS_GUARD_ALLOWLIST", allow)
    assert guard.verify() == []
