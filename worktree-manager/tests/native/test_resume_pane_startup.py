"""Real Windows Resume-plan consumer; engine resolution and Copilot are fixtures."""

import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from _manager_process_fixture import boundary_runtime
from test_launch_admission_ordering import _fixture
from test_pane_args_file import PWSH, WRAPPER, _test_env
from test_pane_startup import _copied_setup

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Native Windows PSMux consumer"),
    pytest.mark.timeout(180),
]
ROOT = WRAPPER.parents[2]

OWNER = r'''
import json, os, subprocess, sys
from pathlib import Path

args = sys.argv[1:]
if args[:1] == ["-c"]:
    result = subprocess.run(
        [sys.executable, *args], input=sys.stdin.buffer.read(), timeout=25,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    raise SystemExit(result.returncode)
if args[:3] == ["-I", "-m", "agent_worktrees.session_shell"]:
    source = Path(os.environ["TEST_SOURCE_ROOT"])
    runtime_lib = Path(os.environ["AGENT_RT_ROOT"]) / "lib"
    sys.path[:0] = [str(source / "plugins/agent-worktrees/src"),
                    str(source / "libs/repository-policy/src"), str(runtime_lib)]
    import work_coalescing_singleton
    if not Path(work_coalescing_singleton.__file__).resolve().is_relative_to(runtime_lib.resolve()):
        raise SystemExit("Resume fixture dependency resolved outside its runtime")
    from agent_worktrees.session_shell import main
    sys.argv = ["session_shell", *args[3:]]
    raise SystemExit(main())
if args[:2] != ["-m", "agent_worktrees"]:
    raise SystemExit(91)
argv = args[2:]
if argv[:1] == ["--project"]:
    argv = argv[2:]
with Path(os.environ["TEST_CALLS"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(argv) + "\n")
if argv[:1] == ["resolve"]:
    print(Path(os.environ["TEST_PLAN"]).read_text(encoding="utf-8"))
elif argv[:2] == ["agent-policy", "check"]:
    from repository_policy import require_agent_hosting
    policy = require_agent_hosting(argv[argv.index("--path") + 1])
    print(json.dumps({**policy.to_dict(), "allowed": True}))
elif argv[:2] == ["copilot-identity", "ensure"]:
    print('{"status":"disabled"}')
elif argv[:2] == ["execution-leg", "get"]:
    print('{"execution_leg":null}')
elif argv[:1] in (["register-launch"], ["activity-log"], ["embody"]):
    print('{}')
else:
    raise SystemExit("Unexpected owner invocation: " + repr(argv))
'''


def _wait_for(check, label, consoles):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.05)
    output = "\n".join(console.output.decode("utf-8", "replace") for console in consoles)
    raise AssertionError(f"{label}\n{output}")


def _resume_fixture(root, shared_env, manager_bin, index):
    root.mkdir()
    env, target, _ = _fixture(root)
    shutil.copytree(
        ROOT / "libs/work-coalescing-singleton/src/work_coalescing_singleton",
        root / "runtime/lib/work_coalescing_singleton",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    # Keep only declared OS variables and this fixture's explicit routing inputs.
    env = {
        **shared_env,
        **{key: value for key, value in env.items()
           if key.startswith("TEST_") or key in {
               "AGENT_RT_ROOT", "PYTHONPATH", "AGENT_WORKTREES_LAUNCH_TRACE",
           }},
    }
    env.update(TEST_SOURCE_ROOT=str(ROOT), WORKTREE_PANE_WAIT_TIMEOUT="0")
    (root / "owner.py").write_text(OWNER, encoding="utf-8")
    (target / ".copilot-extensions/agent-worktrees/config.yaml").write_text(
        "expose_agent: true\n", encoding="utf-8",
    )
    state = root / "state" / "worktrees"
    state.mkdir(parents=True)
    ident = "resume-proof-" + uuid4().hex
    record = state / (ident + ".yaml")
    record.write_text(
        f"worktree_id: {ident}\nrepo: example\nworktree_path: {json.dumps(str(target))}\n"
        "branch: worktree/fixture\nmachine: test-host\nplatform: windows\n"
        "status: active\nstarted_at: '2026-01-01T00:00:00+00:00'\n"
        "last_resumed_at: '2026-01-01T00:00:00+00:00'\nresume_count: 1\n",
        encoding="utf-8",
    )
    shell = _copied_setup(state)
    capture = root / "child.json"
    shell.joinpath("default-setup.ps1").write_text(
        "# agent-worktrees:native-setup-owner-v1\n"
        "[IO.File]::WriteAllText($env:TEST_CAPTURE, "
        "(ConvertTo-Json -InputObject @{pid=$PID;argv=@($args)} -Compress))\n"
        "[Console]::WriteLine('RESUME-FIXTURE-READY')\n"
        "while(-not(Test-Path -LiteralPath $env:TEST_RELEASE)){Start-Sleep -Milliseconds 50}\n"
        "exit 0\n", encoding="utf-8",
    )
    expected = [
        "-Project", "example", "-WorktreeId", ident, "-RuntimePython", str(root / "owner.ps1"),
        "--resume=" + str(uuid4()), "--experimental", "--allow-all", "", "two words", "a'quote", 'a"quote',
        "trailing\\", "$literal; & text", "\u03bb", "2026-10-08T12:00:00Z",
        *[f"fixture-{index}-{value}" for value in range(11)],
    ]
    command = [PWSH, "-NoProfile", "-NoLogo", "-File", str(shell / "default-setup.ps1"),
               *expected]
    assert len(command) == 33
    plan = root / "plan.json"
    plan.write_text(json.dumps({
        "action": "exec", "project": "example", "worktree_id": ident,
        "work_dir": str(target), "status_path": str(target), "record_path": str(record),
        "pane_shell_version": 1, "post_exit": False, "cmd": command,
        "env": {
            "TEST_CAPTURE": str(capture), "TEST_RELEASE": str(root / "release"),
            "WORKTREE_PANE_MIN_RUNTIME": "0", "WORKTREE_PANE_WAIT_TIMEOUT": "0",
            "AGENT_CONFIGURATION_CONTEXT": json.dumps({
                "schema": "agent-worktrees.configuration", "version": 1,
                "context": {
                    "repository_id": "repo-" + "a" * 24, "execution_machine": "test-host",
                    "installation_cell": str(root / "runtime"), "binding": "bound",
                    "base_root": str(target), "knowledge_root": str(root / "knowledge"),
                },
                "repository_machine_root": str(root / "project"),
            }, separators=(",", ":")),
        },
    }), encoding="utf-8")
    env["TEST_PLAN"] = str(plan)
    return {
        "argv": [PWSH, "-NoProfile", "-NoLogo", "-File",
                 str(manager_bin / "launch-session.ps1"), "--worktree-id", ident],
        "env": env, "target": target, "capture": capture,
        "expected": expected, "name": "wt-" + ident, "root": root, "state": state,
    }


@pytest.mark.parametrize("use_fixture_dependency", [False, True])
def test_resume_fixture_supplies_the_session_shell_dependency(tmp_path, use_fixture_dependency):
    env = _test_env(tmp_path)
    launch = _resume_fixture(tmp_path / "launch", env, tmp_path / "manager-bin", 0)
    runtime_lib = Path(launch["env"]["AGENT_RT_ROOT"]) / "lib"
    source_dependencies = sorted(
        str(path) for path in (ROOT / "libs").glob("*/src")
        if path.parent.name != "work-coalescing-singleton"
    )
    if not use_fixture_dependency:
        (runtime_lib / "work_coalescing_singleton").rename(tmp_path / "detached-dependency")
        source_dependencies.append(str(ROOT / "libs/work-coalescing-singleton/src"))
    bootstrap = """
import importlib.util, runpy, sys
from pathlib import Path
package = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location(
    "yaml", package / "__init__.py", submodule_search_locations=[str(package)])
module = importlib.util.module_from_spec(spec)
sys.modules["yaml"] = module
spec.loader.exec_module(module)
owner, runtime = sys.argv[2:4]
sys.path[:0] = sys.argv[3:]
sys.argv = [owner, "-I", "-m", "agent_worktrees.session_shell", "--help"]
try:
    runpy.run_path(owner, run_name="__main__")
except SystemExit as error:
    if error.code not in (0, None):
        raise
import work_coalescing_singleton
assert Path(work_coalescing_singleton.__file__).resolve().is_relative_to(Path(runtime).resolve())
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", bootstrap,
         str(Path(yaml.__file__).parent), str(launch["root"] / "owner.py"), str(runtime_lib),
         *source_dependencies],
        cwd=launch["target"], env=launch["env"], capture_output=True, text=True, timeout=20, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if use_fixture_dependency:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0
        assert "Resume fixture dependency resolved outside its runtime" in result.stderr


@pytest.mark.parametrize("prewarm", [False, True])
def test_four_simultaneous_resume_plans_acknowledge_and_reach_copied_setup(tmp_path, prewarm):
    from _terminal_console_fixture import Console

    psmux = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux")
    assert PWSH and psmux, "PowerShell 7 and PSMux are required for this native packet"
    env = _test_env(tmp_path)
    env.update(UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never")
    if prewarm:
        env.pop("PSMUX_NO_WARM")
    manager = tmp_path / "manager"
    slot = boundary_runtime(manager, "fixture")
    (manager / "current-version").write_text("fixture\n", encoding="utf-8")
    binary = slot / "payload" / "bin"
    shutil.copytree(WRAPPER.parent, binary)
    launches = [_resume_fixture(tmp_path / f"launch-{index}", env, binary, index)
                for index in range(4)]
    consoles = []

    def mux(*args):
        return subprocess.run(
            [psmux, *args], env=env, cwd=env["USERPROFILE"],
            capture_output=True, text=True, timeout=10, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    with ExitStack() as stack:
        try:
            for launch in launches:
                consoles.append(stack.enter_context(
                    Console(launch["argv"], env=launch["env"], cwd=launch["target"]),
                ))
            _wait_for(
                lambda: all(b"RESUME-FIXTURE-READY" in console.output for console in consoles),
                "Launcher did not complete its pane acknowledgment", consoles,
            )
            owners = []
            for console, launch in zip(consoles, launches, strict=True):
                captured = json.loads(launch["capture"].read_text("utf-8-sig"))
                assert captured["argv"] == launch["expected"]
                assert not list(launch["state"].glob("launch-attempts/*/*.shell/pane-args/*.json"))
                pane = mux("display-message", "-t", launch["name"], "-p", "#{pane_pid}")
                assert pane.returncode == 0, pane.stderr
                owners.append((console, console.pin(captured["pid"]),
                               console.pin(int(pane.stdout.strip()))))
            for console in consoles:
                console.send(b"\x02d")
            for console in consoles:
                assert console.wait() == 0, console.output.decode("utf-8", "replace")
            for launch in launches:
                assert mux("has-session", "-t", launch["name"]).returncode == 0
            assert all(not console.exited(handle) for console, *handles in owners for handle in handles)
            for launch in launches:
                (launch["root"] / "release").touch()
            _wait_for(
                lambda: all(mux("has-session", "-t", launch["name"]).returncode == 1
                            for launch in launches)
                and all(console.exited(handle) for console, *handles in owners for handle in handles),
                "Owned pane/child processes did not retire after release", consoles,
            )
        finally:
            for launch in launches:
                (launch["root"] / "release").touch()
                mux("kill-session", "-t", launch["name"])
