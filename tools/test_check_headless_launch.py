"""Tests for the headless-launch guard (windows-launch-hardening #786).

The guard flags a raw process-creation flag literal in an ``agent-procutil``
adopter and forbids ``CREATE_NEW_CONSOLE`` across all production plugin and
canonical shared-library sources. It is AST-based (docstrings/comments never
count) and supports an inline ``# headless-guard: allow <reason>`` escape hatch.

Run:  python -m pytest tools/test_check_headless_launch.py
"""
from __future__ import annotations

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


def test_flags_raw_flag_in_adopting_plugin(repo):
    _mk_plugin(repo, "agent-foo", adopts=True,
               body="import subprocess\nx = subprocess.CREATE_NO_WINDOW\n")
    problems = guard.verify()
    assert any("agent-foo" in p and "CREATE_NO_WINDOW" in p for p in problems)


def test_one_parse_preserves_all_rules_and_refreshes_between_runs(repo, monkeypatch):
    _mk_plugin(
        repo, "agent-all", adopts=True,
        body=("import subprocess\nx = subprocess.CREATE_NEW_CONSOLE\n"
              "y = subprocess.CREATE_NO_WINDOW\nsubprocess.run(['git', 'status'])\n"),
    )
    source = repo / "plugins" / "agent-all" / "src" / "agent_all" / "mod.py"
    original_parse = guard.ast.parse
    calls = []

    def parse(text, **kwargs):
        calls.append(kwargs["filename"])
        return original_parse(text, **kwargs)

    monkeypatch.setattr(guard.ast, "parse", parse)
    problems = guard.verify()
    assert calls == [str(source)]
    assert any("unsafe 'CREATE_NEW_CONSOLE'" in p for p in problems)
    assert any("raw 'CREATE_NO_WINDOW'" in p for p in problems)
    assert any("unsuppressed console spawn of 'git'" in p for p in problems)
    source.write_text("x = 1\n", encoding="utf-8")
    assert guard.verify() == []
    assert calls == [str(source)] * 2


def test_ignores_non_adopting_plugin(repo):
    _mk_plugin(repo, "agent-bar", adopts=False,
               body="import subprocess\nx = subprocess.CREATE_NO_WINDOW\n")
    assert guard.verify() == []


@pytest.mark.parametrize("body", [
    *(f"import subprocess\nsubprocess.{name}(['git'])\n" for name in (
        "Popen", "run", "call", "check_call", "check_output",
    )),
    "from subprocess import run as launch\nlaunch(['git'])\n",
    "import os\nos.system('git status')\n",
    "from os import system as launch\nlaunch('git status')\n",
    "import subprocess\nsubprocess.\uff52un(['git'])\n",
    "from subprocess import \uff30open as launch\nlaunch(['git'])\n",
    "import subprocess as sp\nfrom agent_procutil import detached_kwargs\n"
    "sp.Popen(['worker'], **detached_kwargs())\n",
    "from subprocess import Popen as launch\n"
    "from agent_procutil import detached_kwargs as detach\n"
    "launch(['worker'], **detach())\n",
    "import subprocess\nsubprocess.Popen(['worker'], **helper.windowless_daemon_kwargs())\n",
    "import subprocess\nsubprocess.Popen(['worker'], **\uff44etached_kwargs())\n",
    "import subprocess\n"
    "def worker():\n"
    "    settings = detached_kwargs()\n"
    "    subprocess.Popen(['worker'], **settings)\n",
])
def test_ast_admission_preserves_unfiltered_spawn_diagnostics(repo, monkeypatch, body):
    _mk_plugin(repo, "agent-foo", adopts=True, body=body)
    actual = guard.verify()
    assert actual
    original = guard._FlagFinder

    class Unfiltered(original):
        def __init__(self):
            super().__init__()
            self.has_spawn_reference = True
            self.has_popen_reference = True
            self.has_detach_reference = True

    monkeypatch.setattr(guard, "_FlagFinder", Unfiltered)
    assert guard.verify() == actual


def test_ast_admission_skips_only_impossible_visitors_and_still_parses(repo, monkeypatch):
    _mk_plugin(
        repo, "agent-foo", adopts=True,
        body=("description = 'subprocess.Popen detached_kwargs'\n"
              "value = subprocess.CREATE_NEW_CONSOLE\n"),
    )
    def unexpected(*_args):
        raise AssertionError("an impossible spawn visitor was constructed")

    monkeypatch.setattr(guard, "_SpawnFinder", unexpected)
    monkeypatch.setattr(guard, "_DetachedSpawnFinder", unexpected)
    assert "unsafe 'CREATE_NEW_CONSOLE'" in guard.verify()[0]
    source = repo / "plugins/agent-foo/src/agent_foo/mod.py"
    source.write_text("def broken(:\n", encoding="utf-8")
    assert "cannot parse production Python" in guard.verify()[0]
    source.write_text("import subprocess\nsubprocess.run(['git'])\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="impossible spawn visitor"):
        guard.verify()


@pytest.mark.parametrize("visitor_name", ["_FlagFinder", "_SpawnFinder", "_DetachedSpawnFinder"])
def test_visitor_dispatch_resolves_each_node_type_once_per_file(visitor_name, monkeypatch):
    text = "from subprocess import Popen\n" + "value = alias\n" * 50
    tree = guard.ast.parse(text)
    visitor_type = getattr(guard, visitor_name)
    lookups = []

    def getattribute(self, name):
        if name == "visit_Name":
            lookups.append(name)
        return object.__getattribute__(self, name)

    monkeypatch.setattr(visitor_type, "__getattribute__", getattribute)

    def visitor():
        if visitor_name == "_SpawnFinder":
            return visitor_type(text)
        if visitor_name == "_DetachedSpawnFinder":
            return visitor_type(tree)
        return visitor_type()

    visitor().visit(tree)
    assert lookups == ["visit_Name"]
    visitor().visit(tree)
    assert lookups == ["visit_Name"] * 2


def test_daemon_analysis_only_visits_adopting_source_without_skipping_other_rules(
    repo, monkeypatch,
):
    body = (
        "import subprocess\n"
        "from agent_procutil import detached_kwargs\n"
        "x = subprocess.CREATE_NEW_CONSOLE\n"
        "def unsuppressed():\n"
        "    subprocess.run(['git', 'status'])\n"
        "subprocess.Popen(['worker'], **detached_kwargs())\n"
    )
    _mk_plugin(repo, "agent-adopter", adopts=True, body=body)
    _mk_plugin(repo, "agent-other", adopts=False, body=body)
    extra_roots = [
        repo / "plugins" / "agent-adopter" / "scripts",
        repo / "plugins" / "agent-adopter" / "libs" / "vendored" / "src",
        repo / "libs" / "shared" / "src",
    ]
    for root in extra_roots:
        root.mkdir(parents=True)
        (root / "mod.py").write_text(body, encoding="utf-8")
    visited = []
    original_init = guard._DetachedSpawnFinder.__init__

    def record_init(self, tree):
        visited.append(tree)
        original_init(self, tree)

    monkeypatch.setattr(guard._DetachedSpawnFinder, "__init__", record_init)
    problems = guard.verify()
    assert len(visited) == 1
    assert sum("unsafe 'CREATE_NEW_CONSOLE'" in p for p in problems) == 5
    assert sum("unsuppressed console spawn of 'git'" in p for p in problems) == 5
    assert sum("hand-rolled detached Popen" in p for p in problems) == 1
    assert "plugins/agent-adopter/src/" in next(
        p for p in problems if "hand-rolled detached Popen" in p
    )
    manifest = repo / "plugins" / "agent-other" / "pyproject.toml"
    manifest.write_text('dependencies = ["agent-procutil"]\n', encoding="utf-8")
    visited.clear()
    problems = guard.verify()
    assert len(visited) == 2
    assert sum("hand-rolled detached Popen" in p for p in problems) == 2


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
    # Rule 3 accepts the suppression; rule 5 separately reports the
    # hand-rolled detached launch (see the rule-5 tests below).
    assert [p for p in guard.verify() if "hand-rolled detached Popen" not in p] == []


def test_spawn_daemon_call_alone_is_clean(repo):
    """A launch through ``agent_procutil.spawn_daemon`` (the shared
    resident-daemon primitive) is windowless by construction and is never
    reported, even with a literal console-program argv[0]."""
    _mk_plugin(
        repo, "agent-ok-daemon", adopts=True,
        body=(
            "from agent_procutil import spawn_daemon\n"
            "def start(home):\n"
            "    return spawn_daemon(['python', '-m', 'svc'], cwd=home,\n"
            "                        log_path=home / 'svc.log')\n"
        ),
    )
    assert guard.verify() == []


def test_spawn_daemon_does_not_approve_a_raw_spawn_in_the_same_function(repo):
    """``spawn_daemon`` is its own approved call, not a kwargs helper: a bare
    fallback ``Popen`` next to it is still an unsuppressed console spawn."""
    _mk_plugin(
        repo, "agent-bad-daemon-fallback", adopts=True,
        body=(
            "import subprocess\n"
            "from agent_procutil import spawn_daemon\n"
            "def start(home):\n"
            "    try:\n"
            "        return spawn_daemon(['python', '-m', 'svc'], cwd=home,\n"
            "                            log_path=home / 'svc.log')\n"
            "    except OSError:\n"
            "        return subprocess.Popen(['python', '-m', 'svc'])\n"
        ),
    )
    problems = guard.verify()
    assert len(problems) == 1
    assert "unsuppressed console spawn of 'python'" in problems[0]


def test_spawn_daemon_mention_elsewhere_does_not_approve_a_raw_spawn(repo):
    _mk_plugin(
        repo, "agent-bad-daemon", adopts=True,
        body=(
            "import subprocess\n"
            "from agent_procutil import spawn_daemon\n"
            "def start(home):\n"
            "    return spawn_daemon(['svc'], cwd=home, log_path=home / 'svc.log')\n"
            "def other():\n"
            "    return subprocess.Popen(['python', '-m', 'svc'])\n"
        ),
    )
    problems = guard.verify()
    assert any("unsuppressed console spawn of 'python'" in p for p in problems)


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


# Rule 5: a resident daemon launches through agent_procutil.spawn_daemon.

_UPDATE_SHAPE = (
    "import subprocess\nfrom agent_procutil import detached_kwargs\n"
    "def start():\n    kwargs = {{'stdin': subprocess.DEVNULL}}\n"
    "    kwargs.update(detached_kwargs())\n"
    "    return subprocess.Popen(['svc'], **kwargs){comment}\n"
)


def _daemon_problems(problems):
    return [p for p in problems if "hand-rolled detached Popen" in p or "daemon-spawn" in p]


@pytest.mark.parametrize("body", [
    _UPDATE_SHAPE.format(comment=""),
    ("import subprocess\nfrom . import core\nclass Svc:\n    def start(self):\n"
     "        subprocess.Popen(['svc'], **core.windowless_daemon_kwargs(breakaway=True))\n"),
    ("from subprocess import Popen as P\nfrom agent_procutil import detached_kwargs\n"
     "def start():\n    P(['svc'], **detached_kwargs())\n"),
    ("from agent_procutil import windowless_daemon_kwargs\ndef start():\n"
     "    import subprocess as _subprocess\n"
     "    _subprocess.Popen(['svc'], **windowless_daemon_kwargs(breakaway=True))\n"),
    ("from agent_procutil import detached_kwargs\ndef start():\n"
     "    sp.Popen(['svc'], **detached_kwargs())\nimport subprocess as sp\n"),
    ("from agent_procutil import detached_kwargs\ndef start():\n"
     "    P(['svc'], **detached_kwargs())\nfrom subprocess import Popen as P\n"),
    ("import subprocess\nfrom agent_procutil import detached_kwargs as daemon_flags\n"
     "def start():\n    subprocess.Popen(['svc'], **daemon_flags())\n"),
    ("from agent_procutil import detached_kwargs\nclass Service:\n"
     "    import subprocess as sp\n    proc = sp.Popen(['svc'], **detached_kwargs())\n"),
], ids=[
    "kwargs-update", "attribute-helper-in-method", "imported-popen-alias", "module-alias",
    "module-alias-imported-after-use", "popen-alias-imported-after-use", "aliased-detach-helper",
    "class-body-launch",
])
def test_hand_rolled_detached_popen_is_flagged(repo, body):
    _mk_plugin(repo, "agent-daemon", adopts=True, body=body)
    problems = _daemon_problems(guard.verify())
    assert len(problems) == 1
    assert "plugins/agent-daemon/src/agent_daemon/mod.py:" in problems[0]
    assert "spawn_daemon" in problems[0]


@pytest.mark.parametrize("body", [
    ("from agent_procutil import spawn_daemon\ndef start(root):\n"
     "    return spawn_daemon(['svc'], cwd=root, log_path=root / 'svc.log')\n"),
    "import subprocess\ndef run():\n    return subprocess.Popen(['svc'], creationflags=0)\n",
    _UPDATE_SHAPE.format(comment="  # daemon-spawn: allow one-shot waiter, exits with its task"),
    ("import subprocess\nfrom agent_procutil import detached_kwargs\ndef outer():\n"
     "    def helper():\n        return detached_kwargs()\n"
     "    class Box:\n        kwargs = detached_kwargs()\n"
     "    return subprocess.Popen(['tool'], creationflags=0)\n"),
    ("import subprocess\nfrom agent_procutil import detached_kwargs\ndef outer():\n"
     "    helper = lambda: detached_kwargs()\n"
     "    return subprocess.Popen(['tool'], creationflags=0), helper\n"),
    ("from agent_procutil import detached_kwargs\nclass Service:\n"
     "    import subprocess as sp\n    def start(self, sp):\n"
     "        return sp.Popen(['x'], **detached_kwargs())\n"),
    ("import subprocess\nfrom agent_procutil import detached_kwargs\ndef start():\n"
     "    return subprocess.Popen(  # daemon-spawn: allow bounded helper\n"
     "        ['svc'], **detached_kwargs(),\n    )\n"),
], ids=[
    "spawn-daemon", "no-detach-helper", "inline-allow-with-reason", "helper-only-in-nested-scope",
    "helper-only-in-lambda", "class-alias-not-visible-in-method", "multiline-inline-allow",
])
def test_daemon_launches_that_pass(repo, body):
    _mk_plugin(repo, "agent-daemon", adopts=True, body=body)
    assert _daemon_problems(guard.verify()) == []


def test_inline_daemon_allow_needs_a_reason(repo):
    _mk_plugin(repo, "agent-daemon", adopts=True,
               body=_UPDATE_SHAPE.format(comment="  # daemon-spawn: allow"))
    assert len(_daemon_problems(guard.verify())) == 1


def test_non_adopting_plugin_is_out_of_daemon_scope(repo):
    _mk_plugin(repo, "agent-legacy", adopts=False, body=_UPDATE_SHAPE.format(comment=""))
    assert _daemon_problems(guard.verify()) == []


def _write_daemon_allow(repo, text):
    allow = repo / guard.DAEMON_SPAWN_ALLOWLIST
    allow.parent.mkdir(parents=True, exist_ok=True)
    allow.write_text(text, encoding="utf-8")


def test_daemon_baseline_entry_suppresses_and_stale_entries_are_reported(repo):
    _mk_plugin(repo, "agent-daemon", adopts=True, body=_UPDATE_SHAPE.format(comment=""))
    key = "plugins/agent-daemon/src/agent_daemon/mod.py::start"
    _write_daemon_allow(repo, f"# header\n{key}  resident daemon; migration pending\n")
    assert _daemon_problems(guard.verify()) == []
    _write_daemon_allow(
        repo,
        f"{key}  resident daemon; migration pending\n"
        "plugins/agent-daemon/src/agent_daemon/mod.py::gone  migrated already\n",
    )
    problems = _daemon_problems(guard.verify())
    assert len(problems) == 1 and "stale entry" in problems[0] and "::gone" in problems[0]


@pytest.mark.parametrize("entry", [
    "plugins/agent-daemon/src/agent_daemon/mod.py::start",
    "plugins/agent-daemon/src/agent_daemon/mod.py:6  path:line is not a function key",
])
def test_daemon_baseline_entries_need_a_function_key_and_reason(repo, entry):
    _mk_plugin(repo, "agent-daemon", adopts=True, body=_UPDATE_SHAPE.format(comment=""))
    _write_daemon_allow(repo, entry + "\n")
    assert any("daemon-spawn.allow:1: expected" in p for p in guard.verify())

def test_a_daemon_baseline_entry_covers_exactly_one_launch(repo):
    body = (
        "import subprocess\nfrom agent_procutil import detached_kwargs\n"
        "def start():\n    subprocess.Popen(['a'], **detached_kwargs())\n"
        "    subprocess.Popen(['b'], **detached_kwargs())\n"
    )
    _mk_plugin(repo, "agent-daemon", adopts=True, body=body)
    key = "plugins/agent-daemon/src/agent_daemon/mod.py::start"
    _write_daemon_allow(repo, f"{key}  first launch, migration pending\n")
    problems = _daemon_problems(guard.verify())
    assert len(problems) == 1 and "hand-rolled detached Popen in start" in problems[0]
    _write_daemon_allow(repo, f"{key}  first launch\n{key}  second launch\n")
    assert _daemon_problems(guard.verify()) == []
    _write_daemon_allow(repo, f"{key}  first\n{key}  second\n{key}  third\n")
    problems = _daemon_problems(guard.verify())
    assert len(problems) == 1 and "stale entry" in problems[0]


@pytest.mark.parametrize("body", [
    ("import subprocess\nfrom agent_procutil import detached_kwargs\n"
     "def helper():\n    import subprocess as sp\n    return sp\n"
     "def run(sp):\n    return sp.Popen(['x'], **detached_kwargs())\n"),
    ("from agent_procutil import detached_kwargs\n"
     "class Box:\n    from subprocess import Popen as P\n"
     "def run(P):\n    return P(['x'], **detached_kwargs())\n"),
], ids=["sibling-function-import", "class-body-import"])
def test_local_subprocess_aliases_do_not_leak_to_other_scopes(repo, body):
    _mk_plugin(repo, "agent-daemon", adopts=True, body=body)
    assert _daemon_problems(guard.verify()) == []


def test_enclosing_function_alias_reaches_a_nested_function(repo):
    _mk_plugin(
        repo, "agent-daemon", adopts=True,
        body=("from agent_procutil import detached_kwargs\ndef outer():\n"
              "    import subprocess as sp\n    def inner():\n"
              "        return sp.Popen(['x'], **detached_kwargs())\n    return inner\n"),
    )
    problems = _daemon_problems(guard.verify())
    assert len(problems) == 1 and "outer.inner" in problems[0]

def test_inline_allowance_never_consumes_a_baseline_entry(repo):
    one_shot = "    subprocess.Popen(['waiter'], **detached_kwargs())  # daemon-spawn: allow waiter\n"
    resident = "    subprocess.Popen(['svc'], **detached_kwargs())\n"
    head = "import subprocess\nfrom agent_procutil import detached_kwargs\ndef start():\n"
    key = "plugins/agent-daemon/src/agent_daemon/mod.py::start"
    _mk_plugin(repo, "agent-daemon", adopts=True, body=head + one_shot + resident)
    _write_daemon_allow(repo, f"{key}  resident daemon, migration pending\n")
    assert _daemon_problems(guard.verify()) == []
    # Once the resident launch is gone, the inline-allowed one can't keep the
    # entry alive: it is reported stale.
    module = repo / "plugins" / "agent-daemon" / "src" / "agent_daemon" / "mod.py"
    module.write_text(head + one_shot, encoding="utf-8")
    problems = _daemon_problems(guard.verify())
    assert len(problems) == 1 and "stale entry" in problems[0]


def test_two_launches_on_one_line_need_two_baseline_entries(repo):
    _mk_plugin(
        repo, "agent-daemon", adopts=True,
        body=("import subprocess\nfrom agent_procutil import detached_kwargs\ndef start():\n"
              "    a, b = subprocess.Popen(['a'], **detached_kwargs()), "
              "subprocess.Popen(['b'], **detached_kwargs())\n"),
    )
    key = "plugins/agent-daemon/src/agent_daemon/mod.py::start"
    _write_daemon_allow(repo, f"{key}  one launch, migration pending\n")
    assert len(_daemon_problems(guard.verify())) == 1
    _write_daemon_allow(repo, f"{key}  first\n{key}  second\n")
    assert _daemon_problems(guard.verify()) == []


# -- rule 6: installer start-now PowerShell launches -------------------------


def _write_installer(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _installer_problems(problems: list[str]) -> list[str]:
    return [p for p in problems if "Start-Process conhost" in p]


@pytest.mark.parametrize("rel", [
    "plugins/agent-x/scripts/install.ps1",
    "plugins/agent-x/transports/t/scripts/install-host.ps1",
])
@pytest.mark.parametrize("statement", [
    "Start-Process -FilePath 'conhost.exe' -ArgumentList $a -WindowStyle Hidden | Out-Null",
    "Start-Process conhost.exe -ArgumentList $a",
    'Start-Process -FilePath "C:\\Windows\\System32\\conhost.exe" `\n    -ArgumentList $a `\n    -PassThru',
    # named parameters in any order, positional after a named one, a variable
    "Start-Process -WindowStyle Hidden -FilePath 'conhost.exe' -ArgumentList $a",
    "Start-Process -WindowStyle Hidden conhost -ArgumentList $a",
    "$p = Start-Process -ArgumentList $a -FilePath $conhost -PassThru",
])
def test_installer_conhost_start_process_is_reported(repo, rel, statement):
    _write_installer(repo, rel, "function Start-It {\n    " + statement + "\n}\n")
    problems = _installer_problems(guard.verify())
    assert len(problems) == 1
    assert problems[0].startswith(f"{rel}:2:")


@pytest.mark.parametrize("text", [
    # marker on the statement's own line
    "Start-Process -FilePath 'conhost.exe' -PassThru  # daemon-spawn: allow foreground supervisor\n",
    # marker on a continuation line
    "Start-Process -FilePath 'conhost.exe' `\n    -PassThru  # daemon-spawn: allow waits on it\n",
    # marker on the comment line directly above
    "# daemon-spawn: allow warned last resort\nStart-Process -FilePath 'conhost.exe' -PassThru\n",
    # not a launch: a scheduled-task action or Run-key value hands lifetime to Windows
    "$a = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument $x\n",
    "New-ItemProperty -Path $k -Name n -Value \"conhost.exe $x\"\n",
    # a comment or generated here-string text is not a statement
    "# Start-Process -FilePath 'conhost.exe'\n",
    "$inner = @\"\nStart-Process -FilePath 'conhost.exe'\n\"@\n",
    "<#\n  a Start-Process child of conhost stays in the Job\n#>\nfunction F { }\n",
    "<# Start-Process conhost.exe #>\n",
    # another program
    "Start-Process -FilePath pwsh -ArgumentList $a\n",
    "Start-Process -FilePath $conhostLike -ArgumentList $a\n",
    "Start-ScheduledTask -TaskName conhost\n",
])
def test_installer_rule_ignores_allowed_and_non_launch_lines(repo, text):
    _write_installer(repo, "plugins/agent-x/scripts/install.ps1", text)
    assert _installer_problems(guard.verify()) == []


def test_installer_rule_needs_a_reason_and_an_adjacent_marker(repo):
    _write_installer(
        repo, "plugins/agent-x/scripts/install.ps1",
        "# daemon-spawn: allow\nStart-Process conhost.exe\n"
        "# daemon-spawn: allow too far away\n\nStart-Process conhost.exe\n",
    )
    problems = _installer_problems(guard.verify())
    assert [p.split(":", 2)[1] for p in problems] == ["2", "5"]


def test_installer_rule_scope_excludes_non_installer_scripts(repo):
    _write_installer(
        repo, "plugins/agent-x/scripts/bootstrap-check.ps1",
        "Start-Process -FilePath 'conhost.exe' -ArgumentList $a\n",
    )
    assert _installer_problems(guard.verify()) == []
