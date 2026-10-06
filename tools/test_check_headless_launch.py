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

