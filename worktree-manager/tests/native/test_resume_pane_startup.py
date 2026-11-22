"""Real Windows Resume-plan consumer; engine resolution and Copilot are fixtures."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, TypedDict, cast
from uuid import uuid4

import pytest
from agent_remote_driver.windows_mux_version import file_version
from _manager_process_fixture import boundary_runtime
from _native_python_owner import native_python_owner
from test_launch_admission_ordering import _fixture
from test_pane_args_file import PWSH, WRAPPER, _test_env
from test_pane_startup import _copied_setup as _legacy_copied_setup

_copied_setup = cast(Callable[[Path], Path], _legacy_copied_setup)
_NO_WINDOW = 0x08000000


class _Console(Protocol):
    output: bytearray

    def wait(self, timeout: int = 15000) -> int | None: ...
    def pin(self, pid: int) -> int: ...
    def exited(self, handle: int, timeout: int = 0) -> bool: ...
    def send(self, value: bytes) -> None: ...
    def __enter__(self) -> _Console: ...
    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class _ConsoleFactory(Protocol):
    def __call__(
        self, argv: list[str], *, env: dict[str, str], cwd: Path,
    ) -> _Console: ...


class _DiagnosticLaunch(TypedDict):
    env: dict[str, str]
    name: str
    root: Path


class _Launch(_DiagnosticLaunch):
    argv: list[str]
    target: Path
    capture: Path
    expected: list[str]
    state: Path

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Native Windows PSMux consumer"),
    pytest.mark.timeout(180),
]
ROOT = WRAPPER.parents[2]

OWNER = r'''
import json, os, subprocess, sys
from pathlib import Path

args = sys.argv[1:]
if (
    len(args) == 6 and args[:4] == ["-I", "-X", "utf8", "-m"]
    and (args[4], args[5]) in {
        ("agent_remote_driver.mux_creation", "--describe-contract"),
        ("agent_remote_driver.mux_observation", "--describe-contract"),
        ("agent_remote_driver.mux_observation", "--observe"),
    }
):
    result = subprocess.run(
        [sys.executable, *args], input=sys.stdin.buffer.read(), timeout=25,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    raise SystemExit(result.returncode)
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


def _wait_for(
    check: Callable[[], bool], label: str, consoles: Sequence[_Console],
    launches: Sequence[_DiagnosticLaunch] = (),
) -> None:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.05)
    output = "\n".join(console.output.decode("utf-8", "replace") for console in consoles)
    for launch in launches:
        for name in ("trace.jsonl", "calls.jsonl"):
            path = launch["root"] / name
            if path.is_file():
                output += f"\n{launch['name']}/{name}:\n{path.read_text('utf-8')[-4096:]}"
    if launches:
        root = Path(launches[0]["env"]["TEMP"])
        for path in sorted((root / "worktree-setup-logs").glob("*.log")):
            output += f"\n{path.name}:\n{path.read_text('utf-8-sig')[-4096:]}"
        output += _native_observation_diagnostics(launches)
    raise AssertionError(f"{label}\n{output}")


def _native_observation_diagnostics(launches: Sequence[_DiagnosticLaunch]) -> str:
    """Retain bounded fixed-query replies, never authentication or pane argv."""
    from agent_remote_driver import mux_inventory, mux_observation
    from agent_remote_driver.mux_observation_types import ObservationUnavailable

    output = ""
    for launch in launches:
        env = launch["env"]
        if not env.get("COPILOT_DRIVER_PSMUX_NAMESPACE"):
            continue
        root = Path(env["PSMUX_DATA_DIR"])
        base = env["COPILOT_DRIVER_PSMUX_NAMESPACE"] + "__" + launch["name"]
        try:
            port = int(mux_observation._read(root / (base + ".port")))
            key = mux_observation._read(root / (base + ".key")).decode("utf-8").strip()
            body = mux_observation._exchange(port, key, time.monotonic() + .25)
            output += f"\n{launch['name']}/fixed-reply: {body[:4096]!r}"
            observed = mux_observation._parse(body, launch["name"])
            output += f"\n{launch['name']}/parsed: {observed!r}"
        except (OSError, ValueError) as error:
            output += f"\n{launch['name']}/query: {type(error).__name__}: {error}"
    if launches and launches[0]["env"].get("COPILOT_DRIVER_PSMUX_NAMESPACE"):
        try:
            result = mux_inventory.observe(timeout=.5, data_dir=Path(
                launches[0]["env"]["PSMUX_DATA_DIR"],
            ))
            output += f"\ncomplete-native-inventory: {result!r}"
        except ObservationUnavailable as error:
            output += f"\ncomplete-native-inventory: {error}"
    return output


def _private_psmux(
    root: Path, binary: str, namespace: str, env: dict[str, str],
) -> None:
    """Keep private mux routing and the selected provider's passive version."""
    compiler = next((
        Path(os.environ["SystemRoot"]) / "Microsoft.NET" / framework / "v4.0.30319/csc.exe"
        for framework in ("Framework64", "Framework")
        if (Path(os.environ["SystemRoot"]) / "Microsoft.NET" / framework
            / "v4.0.30319/csc.exe").is_file()
    ), None)
    assert compiler, "The Windows .NET Framework compiler is required for the native mux fixture"
    version = file_version(binary)
    metadata = "" if version is None else (
        '[assembly: System.Reflection.AssemblyFileVersion("'
        + ".".join(map(str, version)) + '")]\n'
    )
    root.mkdir()
    source = root / "mux.cs"
    source.write_text(r'''
using System;
using System.Diagnostics;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;

VERSION_METADATA
class PrivateMux {
    [DllImport("kernel32.dll")]
    static extern uint GetConsoleCP();

    static string Quote(string value) {
        return "\"" + Regex.Replace(
            Regex.Replace(value, "(\\\\*)\"", "$1$1\\\""), "(\\\\+)$", "$1$1") + "\"";
    }

    static int Main(string[] args) {
        var start = new ProcessStartInfo {
            FileName = BINARY,
            Arguments = String.Join(" ", new[] { "-L", NAMESPACE }.Concat(args).Select(Quote)),
            UseShellExecute = false,
            // ConPTY clients must inherit their existing console; other calls stay windowless.
            CreateNoWindow = GetConsoleCP() == 0
        };
        // Only the outer launcher resolves the adapter; backend descendants use real tools.
        start.EnvironmentVariables["PATH"] = ORIGINAL_PATH;
        using (var child = Process.Start(start)) {
            child.WaitForExit();
            return child.ExitCode;
        }
    }
}
'''.replace("VERSION_METADATA", metadata)
   .replace("BINARY", '@"' + str(binary).replace('"', '""') + '"')
   .replace("ORIGINAL_PATH", '@"' + env["PATH"].replace('"', '""') + '"')
   .replace("NAMESPACE", '@"' + namespace.replace('"', '""') + '"'), encoding="utf-8")
    shim = root / "psmux.exe"
    result = subprocess.run(
        [str(compiler), "/nologo", "/target:exe", "/out:" + str(shim), str(source)],
        env=env, capture_output=True, text=True, timeout=20, check=False,
        creationflags=_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if version is not None:
        assert file_version(str(shim)) == version, "Private mux adapter lost provider file metadata"
    env["PATH"] = str(root) + os.pathsep + env["PATH"]
    env["COPILOT_DRIVER_PSMUX_NAMESPACE"] = namespace


@pytest.mark.parametrize("adapter", [False, True])
def test_private_mux_transport_reaches_its_child(tmp_path: Path, adapter: bool) -> None:
    from _terminal_console_fixture import Console as LegacyConsole
    from plugin_test_command_safety import private_mux_args

    Console = cast(_ConsoleFactory, LegacyConsole)
    psmux = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux")
    assert PWSH and psmux, "PowerShell 7 and PSMux are required for this native packet"
    env = _test_env(tmp_path)
    namespace = private_mux_args(tmp_path)
    binary = psmux
    prefix = namespace
    if adapter:
        _private_psmux(tmp_path / "mux-bin", psmux, namespace[1], env)
        binary = str(tmp_path / "mux-bin/psmux.exe")
        prefix = []
    child = tmp_path / "child.ps1"
    child.write_text(
        "[IO.File]::WriteAllText($env:TEST_CAPTURE, 'ready')\n"
        "while(-not(Test-Path -LiteralPath $env:TEST_RELEASE)){Start-Sleep -Milliseconds 50}\n",
        encoding="utf-8",
    )
    env.update(TEST_CAPTURE=str(tmp_path / "ready"), TEST_RELEASE=str(tmp_path / "release"))
    def quote(value: str | Path) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    start = tmp_path / "start.ps1"
    start.write_text(
        "& " + " ".join(map(quote, [
            binary, *prefix, "new-session", "-d", "-s", "fixture",
            "pwsh.exe", "-NoProfile", "-NoLogo", "-File", quote(child),
        ])) + "\nexit $LASTEXITCODE\n", encoding="utf-8",
    )
    with Console([PWSH, "-NoProfile", "-File", str(start)], env=env, cwd=tmp_path) as console:
        try:
            _wait_for(
                lambda: (tmp_path / "ready").is_file(), "Private mux child did not start",
                [console], [{"root": tmp_path, "name": "fixture", "env": env}],
            )
            assert console.wait() == 0, console.output.decode("utf-8", "replace")
        finally:
            (tmp_path / "release").touch()
            subprocess.run(
                [psmux, *namespace, "kill-session", "-t", "fixture"], env=env, cwd=tmp_path,
                capture_output=True, text=True, timeout=10, check=False,
                creationflags=_NO_WINDOW,
            )


def _resume_fixture(
    root: Path, shared_env: dict[str, str], manager_bin: Path, index: int,
) -> _Launch:
    assert PWSH, "PowerShell 7 is required for this native fixture"
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
    runtime_python = native_python_owner(root, env)
    (root / "runtime/bin/resolve-runtime.ps1").write_text(
        "$AwPy = '" + str(runtime_python).replace("'", "''") + "'\n", encoding="utf-8",
    )
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
        "-Project", "example", "-WorktreeId", ident, "-RuntimePython", str(runtime_python),
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


def test_resume_fixture_native_owner_preserves_python_argv_and_stdin(tmp_path: Path) -> None:
    from _terminal_console_fixture import Console as LegacyConsole
    from plugin_test_command_safety import private_mux_args

    Console = cast(_ConsoleFactory, LegacyConsole)
    env = _test_env(tmp_path)
    psmux = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux")
    assert PWSH and psmux, "PowerShell 7 and PSMux are required for this native packet"
    namespace = private_mux_args(tmp_path)
    _private_psmux(tmp_path / "mux-bin", psmux, namespace[1], env)
    launch = _resume_fixture(tmp_path / "launch", env, tmp_path / "manager-bin", 0)
    argv = ["", "two words", "a'quote", 'a"quote', "trailing\\", "\u03bb"]
    result = subprocess.run(
        [str(launch["root"] / "owner.exe"), "-c",
         "import json,sys; print(json.dumps([sys.argv[1:], sys.stdin.read()]))", *argv],
        input="native stdin\n", cwd=launch["target"], env=launch["env"],
        capture_output=True, text=True, timeout=20, check=False,
        creationflags=_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == [argv, "native stdin\n"]
    rejected = subprocess.run(
        [str(launch["root"] / "owner.exe"), "-c", "raise SystemExit(17)"],
        input="", cwd=launch["target"], env=launch["env"],
        capture_output=True, text=True, timeout=20, check=False,
        creationflags=_NO_WINDOW,
    )
    assert rejected.returncode == 17, rejected.stdout + rejected.stderr
    probe = subprocess.run(
        [str(launch["root"] / "owner.exe"), "-I", "-X", "utf8", "-m",
         "agent_remote_driver.mux_creation", "--describe-contract"],
        stdin=subprocess.DEVNULL, cwd=launch["target"], env=launch["env"],
        capture_output=True, text=True, timeout=20, check=False,
        creationflags=_NO_WINDOW,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    contract = json.loads(probe.stdout)
    assert contract["contract"] == "copilot-driver.mux-creation"
    assert "windows-psmux-create-or-join" in contract["operations"]
    assert Path(contract["python"]).resolve() == Path(sys.executable).resolve()
    assert Path(contract["entrypoint"]).is_file()
    observed = subprocess.run(
        [str(launch["root"] / "owner.exe"), "-I", "-X", "utf8", "-m",
         "agent_remote_driver.mux_observation", "--observe"],
        input=json.dumps({"version": 1, "session": "wt-" + uuid4().hex}),
        cwd=launch["target"], env=launch["env"],
        capture_output=True, text=True, timeout=20, check=False,
        creationflags=_NO_WINDOW,
    )
    assert observed.returncode == 0, observed.stdout + observed.stderr
    observation = json.loads(observed.stdout)
    assert observation["contract"] == "copilot-driver.mux-observation"
    assert observation["version"] == 1
    assert observation["status"] == "absent"
    script = launch["root"] / "pipeline.ps1"
    script.write_text(
        "$VenvPython = '" + str(launch["root"] / "owner.exe").replace("'", "''") + "'\n"
        "$target = '" + str(launch["target"]).replace("'", "''") + "'\n"
        "$tokens = $null; $errors = $null\n"
        "$ast = [Management.Automation.Language.Parser]::ParseFile('"
        + str(WRAPPER.parent / "launch-session.ps1").replace("'", "''")
        + "', [ref]$tokens, [ref]$errors)\n"
        "if ($errors.Count) { throw 'Launcher parse failed' }\n"
        "$admission = $ast.Find({ param($node) "
        "$node -is [Management.Automation.Language.FunctionDefinitionAst] -and "
        "$node.Name -eq 'Assert-LaunchTarget' }, $true)\n"
        "if (-not $admission) { throw 'Admission function missing' }\n"
        "Invoke-Expression $admission.Extent.Text\n"
        "Assert-LaunchTarget -Path $target\n"
        "& $VenvPython -m agent_worktrees register-launch *> $null\n"
        "& '" + str(psmux).replace("'", "''") + "' "
        + " ".join("'" + value.replace("'", "''") + "'" for value in namespace)
        + " has-session -t fixture *> $null\n"
        "if ($LASTEXITCODE -notin @(0,1)) { exit $LASTEXITCODE }\n"
        "Assert-LaunchTarget -Path $target\n"
        "[Console]::WriteLine('pipeline stdin')\nexit 0\n",
        encoding="utf-8",
    )
    with Console([PWSH, "-NoProfile", "-File", str(script)],
                 env=launch["env"], cwd=launch["target"]) as console:
        exit_code = console.wait()
    output = console.output.decode("utf-8", "replace")
    assert exit_code == 0, output + (launch["root"] / "calls.jsonl").read_text("utf-8")
    assert output.count("pipeline stdin") == 1


@pytest.mark.parametrize("use_fixture_dependency", [False, True])
def test_resume_fixture_supplies_the_session_shell_dependency(
    tmp_path: Path, use_fixture_dependency: bool,
) -> None:
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
    yaml_spec = importlib.util.find_spec("yaml")
    assert yaml_spec is not None and yaml_spec.origin
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", bootstrap,
         str(Path(yaml_spec.origin).parent), str(launch["root"] / "owner.py"), str(runtime_lib),
         *source_dependencies],
        cwd=launch["target"], env=launch["env"], capture_output=True, text=True, timeout=20, check=False,
        creationflags=_NO_WINDOW,
    )
    if use_fixture_dependency:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0
        assert "Resume fixture dependency resolved outside its runtime" in result.stderr


@pytest.mark.parametrize("prewarm", [False, True])
def test_four_simultaneous_resume_plans_acknowledge_and_reach_copied_setup(
    tmp_path: Path, prewarm: bool,
) -> None:
    from _terminal_console_fixture import Console as LegacyConsole
    from plugin_test_command_safety import private_mux_args

    Console = cast(_ConsoleFactory, LegacyConsole)
    psmux = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux")
    assert PWSH and psmux, "PowerShell 7 and PSMux are required for this native packet"
    env = _test_env(tmp_path)
    namespace = private_mux_args(tmp_path)
    _private_psmux(tmp_path / "mux-bin", psmux, namespace[1], env)
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
    consoles: list[_Console] = []

    def mux(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [psmux, *namespace, *args], env=env, cwd=env["USERPROFILE"],
            capture_output=True, text=True, timeout=10, check=False,
            creationflags=_NO_WINDOW,
        )

    with ExitStack() as stack:
        try:
            for launch in launches:
                consoles.append(stack.enter_context(
                    Console(launch["argv"], env=launch["env"], cwd=launch["target"]),
                ))
            _wait_for(
                lambda: all(b"RESUME-FIXTURE-READY" in console.output for console in consoles),
                "Launcher did not complete its pane acknowledgment", consoles, launches,
            )
            owners: list[tuple[_Console, int, int]] = []
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
            assert all(not console.exited(handle) for console, child, pane in owners
                       for handle in (child, pane))
            for launch in launches:
                (launch["root"] / "release").touch()
            _wait_for(
                lambda: all(mux("has-session", "-t", launch["name"]).returncode == 1
                            for launch in launches)
                and all(console.exited(handle) for console, child, pane in owners
                        for handle in (child, pane)),
                "Owned pane/child processes did not retire after release", consoles, launches,
            )
        finally:
            for launch in launches:
                (launch["root"] / "release").touch()
                mux("kill-session", "-t", launch["name"])


def test_cold_open_reattach_and_fresh_listings_preserve_other_attachment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None],
) -> None:
    from _terminal_console_fixture import Console as LegacyConsole
    from agent_remote_driver.mux_observation import observe_psmux
    from agent_remote_driver.mux_observation_types import Observation
    from plugin_test_command_safety import private_mux_args
    from worktree_manager import mux_attached_clients, mux_discovery

    Console = cast(_ConsoleFactory, LegacyConsole)
    psmux = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux")
    assert PWSH and psmux, "PowerShell 7 and PSMux are required for native continuity"
    env = _test_env(tmp_path)
    namespace = private_mux_args(tmp_path)
    _private_psmux(tmp_path / "mux-bin", psmux, namespace[1], env)
    manager = tmp_path / "manager"
    slot = boundary_runtime(manager, "fixture")
    (manager / "current-version").write_text("fixture\n", encoding="utf-8")
    binary = slot / "payload" / "bin"
    shutil.copytree(WRAPPER.parent, binary)
    launch = _resume_fixture(tmp_path / "launch", env, binary, 0)
    for key in ("PSMUX_DATA_DIR", "PSMUX_CONFIG_FILE", "COPILOT_DRIVER_PSMUX_NAMESPACE"):
        monkeypatch.setenv(key, env[key])
    monkeypatch.syspath_prepend(str(ROOT / "plugins/agent-worktrees/src"))
    from agent_worktrees import sessions

    run = subprocess.run
    backend = Path(psmux).resolve()

    def private_mux_run(
        argv: list[str], **kwargs: Any,
    ) -> subprocess.CompletedProcess[Any]:
        if argv and Path(argv[0]).resolve() == backend and argv[1:2] == ["list-sessions"]:
            argv = [argv[0], *namespace, *argv[1:]]
        return run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", private_mux_run)
    monkeypatch.setattr(
        mux_discovery, "run_mux_child", lambda *a, **k: pytest.fail("Passive PSMux CLI"),
    )
    name = launch["name"]
    ident = name.removeprefix("wt-")
    consoles: list[_Console] = []
    with ExitStack() as stack:
        try:
            started = time.monotonic()
            first = stack.enter_context(Console(
                launch["argv"], env=launch["env"], cwd=launch["target"],
            ))
            consoles.append(first)
            _wait_for(
                lambda: launch["capture"].is_file()
                and b"RESUME-FIXTURE-READY" in first.output,
                "Cold Open did not reach copied setup", consoles, [launch],
            )
            record_property("cold_open_seconds", time.monotonic() - started)
            original = launch["capture"].read_bytes()
            child = first.pin(json.loads(original.decode("utf-8-sig"))["pid"])
            observed: Observation | None = None

            def clients(count: int) -> bool:
                nonlocal observed
                result = observe_psmux(name)
                observed = result
                return result.status == "present" and result.clients == count

            _wait_for(lambda: clients(1), "First attachment was not observed", consoles, [launch])
            assert observed is not None
            incarnation, panes = observed.incarnation, observed.panes
            started = time.monotonic()
            second = stack.enter_context(Console(
                launch["argv"], env=launch["env"], cwd=launch["target"],
            ))
            consoles.append(second)
            _wait_for(lambda: clients(2), "Open did not reattach to the live pane", consoles, [launch])
            record_property("reattach_seconds", time.monotonic() - started)
            assert launch["capture"].read_bytes() == original
            assert not first.exited(child)
            after = observe_psmux(name)
            assert after.incarnation == incarnation and after.panes == panes

            started = time.monotonic()
            listing = sessions.mux_status_many([ident])[ident]
            assert listing.observed and listing.exists and listing.clients == 2
            assert sessions.has_mux_session(ident)
            assert mux_attached_clients.mux_attached_clients("psmux.exe", name) == 2
            assert mux_discovery.confirm_native_session({
                "mux_bin": "psmux.exe", "mux_session": name,
                "session_incarnation": incarnation, "worktree_path": str(launch["target"]),
            }, connected_only=True) == {"applied": True, "attached_clients": 2}
            elapsed = time.monotonic() - started
            record_property("consumer_listing_seconds", elapsed)
            assert elapsed < 5

            first.send(b"\x02d")
            assert first.wait() == 0
            _wait_for(lambda: clients(1), "Other attachment was lost", consoles, [launch])
            assert not first.exited(child)
            assert launch["capture"].read_bytes() == original
            second.send(b"\x02d")
            assert second.wait() == 0
            _wait_for(lambda: clients(0), "Detached live session became absent", consoles, [launch])
            assert sessions.mux_status_many([ident], strict=True)[ident].exists
            (launch["root"] / "release").touch()
            _wait_for(
                lambda: observe_psmux(name).status == "absent" and first.exited(child),
                "Owned session did not retire after explicit release", consoles, [launch],
            )
        finally:
            (launch["root"] / "release").touch()
            subprocess.run(
                [psmux, *namespace, "kill-session", "-t", name],
                env=env, cwd=env["USERPROFILE"], capture_output=True, text=True,
                timeout=10, check=False, creationflags=_NO_WINDOW,
            )


def test_native_owner_preserves_observation_refusal(tmp_path: Path) -> None:
    (tmp_path / "owner.py").write_text(OWNER, encoding="utf-8")
    env = os.environ.copy()
    executable = native_python_owner(tmp_path, env)
    result = subprocess.run(
        [str(executable), "-I", "-X", "utf8", "-m",
         "agent_remote_driver.mux_observation", "--observe"],
        input=json.dumps({"version": 1, "session": ""}),
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20,
        check=False, creationflags=_NO_WINDOW,
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "contract": "copilot-driver.mux-observation", "version": 1,
        "status": "unsupported", "reason": "unsupported-target",
        "incarnation": "", "clients": None, "panes": [],
    }


def _native_compiler() -> Path:
    compiler = next((
        Path(os.environ["SystemRoot"]) / "Microsoft.NET" / framework / "v4.0.30319/csc.exe"
        for framework in ("Framework64", "Framework")
        if (Path(os.environ["SystemRoot"]) / "Microsoft.NET" / framework
            / "v4.0.30319/csc.exe").is_file()
    ), None)
    assert compiler, "The Windows .NET Framework compiler is required for the native version fixture"
    return compiler


@pytest.mark.parametrize("version", [(3, 3, 5, 7), (3, 4, 0, 17)])
def test_private_mux_preserves_selected_backend_version(
    tmp_path: Path, version: tuple[int, int, int, int],
) -> None:
    from agent_remote_driver.windows_mux_version import (
        MINIMUM_VERSION, file_version, require_creation_binary,
    )

    env = _test_env(tmp_path)
    source = tmp_path / "backend.cs"
    source.write_text(
        'using System.Reflection;\n'
        '[assembly: AssemblyFileVersion("' + ".".join(map(str, version)) + '")]\n'
        'class Backend { static int Main() { return 0; } }\n',
        encoding="utf-8",
    )
    backend = tmp_path / "backend.exe"
    built = subprocess.run(
        [str(_native_compiler()), "/nologo", "/target:exe", "/out:" + str(backend), str(source)],
        env=env, capture_output=True, text=True, timeout=20, check=False,
        creationflags=_NO_WINDOW,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    _private_psmux(tmp_path / "mux-bin", str(backend), "version-fixture", env)
    shim = str(tmp_path / "mux-bin" / "psmux.exe")
    assert file_version(shim) == file_version(str(backend)) == version
    if version < MINIMUM_VERSION:
        with pytest.raises(RuntimeError, match="require version 3.4"):
            require_creation_binary(shim, search_roots=())
    else:
        assert require_creation_binary(shim, search_roots=()) == shim


def test_private_mux_preserves_creation_refusal_without_version_metadata(tmp_path: Path) -> None:
    from agent_remote_driver.windows_mux_version import require_creation_binary

    backend = tmp_path / "backend.exe"
    backend.write_bytes(b"fixture without a version resource")
    assert file_version(str(backend)) is None
    _private_psmux(tmp_path / "mux-bin", str(backend), "version-fixture", _test_env(tmp_path))
    with pytest.raises(RuntimeError, match="require version 3.4"):
        require_creation_binary(str(tmp_path / "mux-bin" / "psmux.exe"), search_roots=())
