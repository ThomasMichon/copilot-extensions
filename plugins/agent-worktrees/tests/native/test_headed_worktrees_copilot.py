"""Opt-in native Copilot through the Worktrees launch owner, not a synthetic child."""

from __future__ import annotations

import asyncio
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import threading
import time
from uuid import uuid4

import pytest

from agent_procutil import no_window_kwargs
from agent_procutil._interpreter import physical_executable
from agent_procutil.owned_process import run_caller_owned
from agent_remote_driver.session_host.client import SessionHostClient
from agent_remote_driver.session_host.osutil import pid_alive, process_start_time

pytestmark = [
    pytest.mark.timeout(180),
    pytest.mark.skipif(
        os.name != "nt" or os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1",
        reason="Explicitly select the isolated native Windows Copilot lane",
    ),
]


def _until(check, label, timeout=30, diagnostic=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = check()
        except AssertionError as error:
            raise AssertionError(str(error) + ("\n" + diagnostic() if diagnostic else "")) from error
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError(label + ("\n" + diagnostic() if diagnostic else ""))


@pytest.mark.parametrize("control", ["broker", "native-input", "manager-no-mux", "manager-mux"])
def test_worktrees_native_fresh_and_resume_keep_one_history_and_real_receipts(tmp_path, control):
    _qualify_native_worktrees(tmp_path, control)


def _qualify_native_worktrees(
    tmp_path, control, *, manager_script=None, manager_environment=None,
    manager_python=None, verify_reuse=True, worktrees_python=None,
    worktrees_root=None, worktrees_environment=None,
):
    from plugin_test_command_safety import private_mux_args

    executable = os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE")
    binary = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux.exe")
    shell = shutil.which("pwsh")
    assert executable and Path(executable).is_absolute() and Path(executable).is_file()
    assert binary and shell, "Select existing native Copilot, PSMux and PowerShell"
    installed = worktrees_python is not None
    if installed:
        assert control == "broker" and worktrees_root is not None
        assert Path(worktrees_python).is_file()
        assert worktrees_environment is not None
    owner_python = str(worktrees_python) if installed else physical_executable(
        getattr(sys, "_base_executable", sys.executable),
    )
    assert owner_python
    source_root = Path(__file__).resolve().parents[4]
    site_packages = None if installed else sysconfig.get_paths()["purelib"]
    prefix = [str(worktrees_python) if installed else sys.executable, "-I", "-B", "-X", "utf8"]
    home, checkout = tmp_path / "copilot-home", tmp_path / "checkout"
    home.mkdir()
    checkout.mkdir()
    temporary = tmp_path / "temp"
    temporary.mkdir()
    local_app_data = tmp_path / "local-app-data"
    local_app_data.mkdir()
    mux_data = tmp_path / "psmux-data"
    mux_data.mkdir()
    manager = control.startswith("manager-")
    worktree_id = "driver-fixture-" + uuid4().hex
    manager_target = "wt-" + worktree_id
    (home / "config.json").write_text(
        json.dumps({"trustedFolders": [str(checkout)]}), encoding="utf-8",
    )
    (home / ".psmux.conf").write_text("set -g warm off\n", encoding="utf-8")
    requests = []

    class Provider(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            body = {
                "id": "fixture", "object": "chat.completion.chunk", "model": "gpt-4.1",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": "FIXTURE"},
                             "finish_reason": "stop"}],
            }
            self.wfile.write(("data: " + json.dumps(body) + "\n\ndata: [DONE]\n\n").encode())
            self.wfile.flush()

    provider = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    provider.daemon_threads = True
    environment = {
        key: value for key, value in (
            worktrees_environment if installed else os.environ
        ).items()
        if (key.startswith("COPILOT_EXTENSIONS_") or not key.startswith(("COPILOT_", "OTEL_")))
        and key not in {
            "GH_TOKEN", "GITHUB_TOKEN", "GITHUB_API_TOKEN", "TMUX", "TMUX_PANE",
            "PSMUX_PANE", "PSMUX_SESSION", "PSMUX_TARGET_SESSION", "PSMUX_SESSION_NAME",
            "PSMUX_DEFAULT_SESSION", "PSMUX_CLAUDE_TEAMMATE_MODE", "WT_SESSION", "TERM_PROGRAM",
        }
    }
    environment.update(
        COPILOT_EXTENSIONS_TEST_CONTAINED="1", COPILOT_HOME=str(home),
        COPILOT_OFFLINE="true", COPILOT_PROVIDER_TYPE="openai",
        COPILOT_PROVIDER_BASE_URL=f"http://127.0.0.1:{provider.server_port}",
        COPILOT_PROVIDER_MODEL_ID="gpt-4.1", COPILOT_MODEL="gpt-4.1", USE_TGREP="false",
        HOME=str(home), USERPROFILE=str(home),
        PSMUX_DATA_DIR=str(mux_data), PSMUX_CONFIG_FILE=os.devnull,
        PSMUX_NO_WARM="1", PSMUX_SERVER_DEBUG="1",
        WORKTREE_PANE_MIN_RUNTIME="0", WORKTREE_PANE_WAIT_TIMEOUT="0",
        LOCALAPPDATA=str(local_app_data), TEMP=str(temporary), TMP=str(temporary),
        WORKTREE_MANAGER_ROOT=str(tmp_path / "manager-runtime"), UV_OFFLINE="1",
        UV_CACHE_DIR=str(tmp_path / "uv-cache"),
        UV_PROJECT_ENVIRONMENT=str(tmp_path / "manager-environment"),
    )
    environment.update(manager_environment or {})
    mux_directory = tmp_path / "discovery"
    mux_directory.mkdir()
    namespace = private_mux_args(tmp_path)
    namespace_file = mux_directory / "namespace.txt"
    namespace_file.write_text(namespace[1], encoding="utf-8")
    compiler = next((path for framework in ("Framework64", "Framework") if (
        path := Path(os.environ.get("WINDIR", "")) / "Microsoft.NET" /
        framework / "v4.0.30319" / "csc.exe"
    ).is_file()), None)
    assert compiler, "The fixture's private mux adapter requires the system C# compiler"
    source = mux_directory / "Mux.cs"
    source.write_text(
        r'''
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
class Mux {
    static string Quote(string value) {
        return "\"" + Regex.Replace(
            Regex.Replace(value, "(\\\\*)\"", "$1$1\\\""), "(\\\\+)$", "$1$1") + "\"";
    }
    static int Main(string[] args) {
        if (args.Length == 0 || args.Any(value =>
            value == "-L" || value == "-S" || value == ";" || value == "kill-server"))
            return 2;
        var reads = new[] {
            "list-sessions", "has-session", "display-message", "list-panes", "list-windows"
        };
        var writes = new[] { "new-session", "set-option", "source-file" };
        if (writes.Contains(args[0])) {
            var selector = args[0] == "new-session" ? "-s" : "-t";
            var index = Array.IndexOf(args, selector);
            if (args.Count(value => value == selector) != 1 || index + 1 >= args.Length ||
                args[index + 1] != MANAGER_TARGET ||
                args.Contains("-g"))
                return 2;
            File.AppendAllText(COMMAND_LOG,
                              args[0] + Environment.NewLine);
        } else if (!reads.Contains(args[0]) &&
                   !(args.Length == 1 && args[0] == "--version")) {
            return 2;
        }
        using (var child = Process.Start(new ProcessStartInfo {
            FileName = BINARY,
            Arguments = String.Join(" ", new[] {
                "-L", File.ReadAllText(NAMESPACE_FILE)
            }.Concat(args).Select(Quote)),
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true
        })) {
            var stdout = child.StandardOutput.ReadToEndAsync();
            var stderr = child.StandardError.ReadToEndAsync();
            if (!child.WaitForExit(5000)) { child.Kill(); child.WaitForExit(); return 2; }
            if (!System.Threading.Tasks.Task.WaitAll(
                new System.Threading.Tasks.Task[] { stdout, stderr }, 5000))
                return 2;
            Console.Out.Write(stdout.Result);
            Console.Error.Write(stderr.Result);
            if (writes.Contains(args[0])) {
                File.AppendAllText(COMMAND_LOG,
                    "exit=" + child.ExitCode + " " + stderr.Result + Environment.NewLine);
            }
            return child.ExitCode;
        }
    }
}
'''.replace("BINARY", '@"' + str(binary).replace('"', '""') + '"')
        .replace("MANAGER_TARGET", '@"' + manager_target + '"')
        .replace("COMMAND_LOG", '@"' + str(tmp_path / "mux-commands.txt").replace('"', '""') + '"')
        .replace("NAMESPACE_FILE", '@"' + str(namespace_file).replace('"', '""') + '"'),
        encoding="utf-8",
    )
    compiled = run_caller_owned(
        [str(compiler), "/nologo", f"/out:{mux_directory / 'psmux.exe'}", str(source)],
        env=environment, cwd=tmp_path, timeout=30, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    environment["PATH"] = os.pathsep.join((str(mux_directory), environment.get("PATH", "")))
    native = str(uuid4())
    prompts = ['Worktrees original\nsingle \' double " & $literal', "fresh broker", "resumed broker"]
    if control == "native-input":
        prompts.insert(2, "ISOLATED native keyboard input")
    outer_target = "headed-fixture-" + uuid4().hex
    target = manager_target if control == "manager-mux" else outer_target

    def query(action, *arguments):
        assert action in {
            "new-session", "has-session", "display-message", "kill-session", "capture-pane", "send-keys",
        }
        mux_arguments = arguments[:arguments.index("--")] if action == "new-session" else arguments
        assert not any(value in {"-L", "-S", ";", "kill-server", "server"} for value in mux_arguments)
        selector = "-s" if action == "new-session" else "-t"
        assert list(arguments).count(selector) == 1
        assert arguments[arguments.index(selector) + 1] in {outer_target, target}
        if action == "send-keys":
            assert arguments in (
                ("-t", target, "-l", "--", prompts[2]), ("-t", target, "Enter"),
            )
        return subprocess.run(
            [binary, *namespace, action, *arguments], env=environment, cwd=home,
            capture_output=True, text=True, encoding="utf-8", timeout=5, **no_window_kwargs(),
        )

    def runtime(script, *arguments):
        result = run_caller_owned(
            [*prefix, "-c", script, *map(str, arguments)], env=environment, cwd=checkout,
            timeout=30, max_stdout_bytes=65536, max_stderr_bytes=65536,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    if installed:
        selected = runtime(
            "import json,sys;from pathlib import Path;"
            "from agent_worktrees import config,launch_seed_exec;import agent_remote_driver;"
            "root=Path(sys.argv[1]);environment=Path(sys.prefix);"
            "assert config.runtime_install_dir()==root;"
            "assert config.install_dir()==root;"
            "assert config.venv_python().samefile(sys.executable);"
            "assert Path(launch_seed_exec.__file__).is_relative_to(environment);"
            "assert Path(agent_remote_driver.__file__).is_relative_to(environment);"
            "print(json.dumps(str(environment)))",
            worktrees_root,
        )
        assert Path(selected) == Path(worktrees_python).parent.parent

    def manager_mapping():
        result = run_caller_owned(
            [str(manager_python), "-I", "-B", "-X", "utf8", "-c",
             "import json,sys;from worktree_manager.mux_mapping_registry import get_mapping;"
             "print(json.dumps(get_mapping('native-copilot',sys.argv[1],strict=True)))",
             worktree_id],
            env=environment, cwd=checkout, timeout=10,
            max_stdout_bytes=8192, max_stderr_bytes=4096,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    def pane_process(selected_target=None):
        observed = query("display-message", "-p", "-t", selected_target or target, "#{pane_pid}")
        if not observed.stdout.strip().isdigit():
            return observed.stderr
        pid = int(observed.stdout.strip())
        detail = run_caller_owned(
            [shell, "-NoProfile", "-Command",
             "$all=@(Get-CimInstance Win32_Process);"
             f"$front=@({pid});$selected=@();"
             "for($depth=0;$depth -lt 5 -and $front.Count;$depth++){"
             "$level=@($all|Where-Object {$front -contains [int]$_.ProcessId});"
             "$selected+=$level;"
             "$front=@($all|Where-Object {$level.ProcessId -contains [int]$_.ParentProcessId}"
             "|ForEach-Object {[int]$_.ProcessId})};$selected | "
             "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"],
            env=environment, cwd=home, timeout=10,
            max_stdout_bytes=8192, max_stderr_bytes=4096,
        )
        return detail.stdout + detail.stderr

    async def broker(state, *, prompt=None, stop=False, refused=False):
        client = await SessionHostClient.connect(port=state["port"])
        try:
            hello = await client.attach(nonce=state["nonce"].encode())
            assert hello.child_pid == state["child_pid"]
            if stop:
                await client.terminate()
                return
            for request_id, method, params in (
                (1, "initialize", {"protocolVersion": 1}),
                (2, "session/prompt", {
                    "sessionId": native, "prompt": [{"type": "text", "text": prompt}],
                }),
            ):
                await client.write((json.dumps({
                    "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
                }) + "\n").encode())
                while True:
                    cursor, frame = await asyncio.wait_for(anext(client.frames()), 30)
                    reply = json.loads(frame)
                    await client.ack(cursor)
                    if reply.get("id") == request_id:
                        if request_id == 2 and refused:
                            assert reply.get("error", {}).get("code") == -32001, reply
                        else:
                            assert "result" in reply, reply
                        break
            await client.detach(reapable=True)
        finally:
            await client.close()

    identities, state = {}, None
    histories = []
    retired_mapping = None
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    try:
        initialized = runtime(
            "import json,sys;from pathlib import Path;from agent_worktrees import tracking,config as cfg;"
            "checkout=Path(sys.argv[1]);project=cfg.project_dir('native-copilot');"
            "project.mkdir(parents=True,exist_ok=True);"
            "(project/'config.yaml').write_text(json.dumps({'repo_name':'native-copilot','machine':'fixture',"
            "'repos':{'native-copilot':{'anchor':str(checkout),'default_branch':'dev'}}}),encoding='utf-8');"
            "manager=sys.argv[3].startswith('manager-');"
            "tracking_path=project/'worktrees' if manager else checkout;"
            "tracking_path.mkdir(parents=True,exist_ok=True);"
            "record=tracking.create_new_record(sys.argv[2],'worktree/native-copilot',str(checkout),"
            "'native-copilot','fixture',cfg.detect_platform() if manager else 'win32',"
            "tracking_path);print(json.dumps(str(record.yaml_path)))",
            checkout, worktree_id, control,
        )
        record = Path(initialized)
        if manager:
            selected = runtime(
                "import json;from agent_worktrees import config;"
                "print(json.dumps(str(config.install_dir())))",
            )
            runtime_root = Path(selected)
            assert runtime_root.is_relative_to(Path(os.environ["COPILOT_EXTENSIONS_TEST_SANDBOX"]))
            scripts = runtime_root / "scripts"
            scripts.mkdir(parents=True, exist_ok=True)
            for name in ("launch-command.ps1", "reconcile-machine-settings.ps1"):
                shutil.copyfile(source_root / "plugins" / "agent-worktrees" / "scripts" / name, scripts / name)
            resolver = runtime_root / "bin" / "resolve-runtime.ps1"
            resolver.parent.mkdir(parents=True, exist_ok=True)
            resolver.write_text(
                "$AwPy = '" + sys.executable.replace("'", "''") + "'\n", encoding="utf-8",
            )
            environment.update(
                AGENT_RT_ROOT=str(runtime_root), WORKTREE_NO_UPDATE="1",
                WORKTREE_NO_RECONCILE="1", WORKTREE_NO_PROVISION="1",
            )
            if manager_script is None:
                manager_bin = tmp_path / "manager-runtime" / "bin"
                shutil.copytree(source_root / "worktree-manager" / "bin", manager_bin)
                manager_script = manager_bin / "launch-session.ps1"
                assert manager_script.read_bytes() == (
                    source_root / "worktree-manager" / "bin" / "launch-session.ps1"
                ).read_bytes()
            assert manager_script.is_file()
        for leg in ("fresh", "resume"):
            leg_started = time.monotonic()
            print(f"Native {control} {leg}: starting", flush=True)
            namespace = private_mux_args(tmp_path)
            namespace_file.write_text(namespace[1], encoding="utf-8")
            outer_target = "headed-fixture-" + uuid4().hex
            target = manager_target if control == "manager-mux" else outer_target
            identities = {}
            plan = runtime(
                "import json,sys;from pathlib import Path;from uuid import uuid4;"
                "record=Path(sys.argv[1]);"
                "attempt,resolution=uuid4().hex,uuid4().hex;"
                "print(json.dumps({'attempt':attempt,'resolution':resolution}))",
                record,
            )
            attempt = record.parent / "launch-attempts" / plan["attempt"]
            native_args = [
                "--session-id" if leg == "fresh" else "--resume", native,
                "--screen-reader", "--no-auto-update", "--disable-builtin-mcps",
                "--no-custom-instructions", "--no-color", "--no-ask-user",
                "--log-dir", str(tmp_path / ("logs-" + leg)),
            ]
            if leg == "fresh":
                native_args += ["-i", prompts[0]]
            invocation = (
                "    from agent_worktrees import launch_seed_exec,launch_execution\n"
                f"    code=launch_seed_exec.launch(Path({str(record)!r}),{[executable, *native_args]!r},"
                "admission_only=True,"
                f"correlation=launch_execution.Correlation({plan['attempt']!r},{plan['resolution']!r}))\n"
            )
            if manager:
                runtime(
                    "import json,sys;from agent_worktrees import config;"
                    "path=config.project_dir('native-copilot')/'config.yaml';"
                    "value=json.loads(path.read_text(encoding='utf-8'));"
                    "value['repos']['native-copilot']['launch']={'windows':json.loads(sys.argv[1])};"
                    "path.write_text(json.dumps(value),encoding='utf-8');print('true')",
                    json.dumps([executable, *native_args]),
                )
                manager_command = [
                    shell, "-NoProfile", "-NonInteractive", "-File",
                    str(manager_script),
                    "--project", "native-copilot", "--worktree-id", worktree_id,
                    "--no-update", "--no-resume", "--no-fast-forward",
                ]
                if control == "manager-no-mux":
                    manager_command.append("--no-mux")
                invocation = (
                    "    import subprocess\n"
                    "    # headless-guard: allow the real Manager consumer to inherit the fixture's private ConPTY\n"
                    f"    code=subprocess.call({manager_command!r})\n"
                )
                previous_attempts = set(record.parent.glob("launch-attempts/*"))
            driver = tmp_path / (leg + ".py")
            result_file = tmp_path / (leg + "-result.txt")
            owner_log = tmp_path / (leg + "-owner.log")
            owner_ready = tmp_path / (leg + "-owner-ready.txt")

            def caller_diagnostics():
                return "\n".join([
                    owner_log.read_text(encoding="utf-8") if owner_log.exists() else "owner not started",
                    query("capture-pane", "-p", "-t", outer_target).stdout[-8192:],
                    pane_process(outer_target),
                    *[
                        path.read_text(encoding="utf-8", errors="replace")[-8192:]
                        for path in (temporary / "worktree-setup-logs").glob("setup-*.log")
                    ],
                ])

            driver.write_text(
                f"with open({str(owner_log)!r},'w',encoding='utf-8') as log:\n"
                "  log.write('original owner started\\n');log.flush()\n"
                "  import sys,traceback\n"
                "  try:\n"
                "    import ctypes,msvcrt,os,site,json\n    from pathlib import Path\n"
                "    log.write(json.dumps({key:os.environ.get(key) for key in "
                "('TMUX','TEMP','LOCALAPPDATA','WORKTREE_NO_MUX')})+'\\n');log.flush()\n"
                "    import shutil\n"
                f"    assert Path(os.environ['LOCALAPPDATA']) == Path({str(local_app_data)!r})\n"
                f"    assert shutil.which('psmux.exe') == {str(mux_directory / 'psmux.exe')!r}\n"
                "    sys.stderr=log\n"
                "    os.dup2(log.fileno(),2)\n"
                "    set_stderr=ctypes.windll.kernel32.SetStdHandle\n"
                "    set_stderr.argtypes=[ctypes.c_ulong,ctypes.c_void_p]\n"
                "    assert set_stderr(-12,msvcrt.get_osfhandle(2))\n"
                + (f"    site.addsitedir({site_packages!r})\n" if not installed else "")
                + "    import time\n"
                f"    while not Path({str(owner_ready)!r}).exists(): time.sleep(0.05)\n"
                + invocation +
                "  except BaseException:\n"
                "    traceback.print_exc(file=log);code=3\n"
                f"  result=Path({str(result_file)!r})\n"
                "  temporary=result.with_suffix('.tmp')\n"
                "  temporary.write_text(str(code),encoding='utf-8')\n"
                "  temporary.replace(result)\n"
                "sys.exit(code)\n",
                encoding="utf-8",
            )
            compile(driver.read_text(encoding="utf-8"), str(driver), "exec")
            caller_command = [
                owner_python, "-I", *([] if installed else ["-S"]), "-B", str(driver),
            ]
            caller_environment = [
                argument for name in ("PATH", "LOCALAPPDATA", "TEMP", "TMP")
                for argument in ("-e", f"{name}={environment[name]}")
            ]
            assert query("has-session", "-t", outer_target).returncode == 1
            if control == "manager-mux":
                assert query("has-session", "-t", target).returncode == 1
            launched = query(
                "new-session", "-d", "-s", outer_target, "-c", str(checkout),
                *caller_environment, "--", *caller_command,
            )
            assert launched.returncode == 0, launched.stderr + (
                owner_log.read_text(encoding="utf-8") if owner_log.exists() else ""
            )
            observed = query(
                "display-message", "-p", "-t", outer_target, "#{session_id}:#{session_created}|#{pane_id}",
            )
            assert observed.returncode == 0 and "|" in observed.stdout
            identities[outer_target] = observed.stdout.strip()
            owner_ready.write_text("ready", encoding="utf-8")
            startup_seen = None
            startup_diagnostic = None

            def bound():
                nonlocal attempt, startup_seen, startup_diagnostic
                if control == "manager-mux" and target not in identities:
                    observed = query(
                        "display-message", "-p", "-t", target,
                        "#{session_id}:#{session_created}|#{pane_id}",
                    )
                    if observed.returncode == 0 and "|" in observed.stdout:
                        identities[target] = observed.stdout.strip()
                        startup_seen = time.monotonic()
                if result_file.exists():
                    assert control == "manager-mux" and result_file.read_text(encoding="utf-8") == "0", (
                        owner_log.read_text(encoding="utf-8")
                    )
                if manager:
                    candidates = set(record.parent.glob("launch-attempts/*")) - previous_attempts
                    paths = [path for candidate in candidates for path in candidate.glob("*/headed.json")]
                else:
                    paths = list(attempt.glob("*/headed.json"))
                if (
                    not paths and startup_seen is not None and startup_diagnostic is None
                    and time.monotonic() - startup_seen >= 10
                ):
                    startup_diagnostic = (
                        query("capture-pane", "-p", "-t", target).stdout[-8192:]
                        + "\n" + pane_process()
                    )
                if paths:
                    assert len(paths) == 1, "The caller created more than one headed broker"
                    attempt = paths[0].parent.parent
                    try:
                        value = json.loads(paths[0].read_text(encoding="utf-8"))
                    except (FileNotFoundError, PermissionError):
                        return None
                    if value["state"] == "running":
                        if control == "manager-mux" and target not in identities:
                            return None
                        return value

            state = _until(
                bound, f"{leg}: Worktrees launch did not bind the common broker",
                timeout=60 if manager else 30,
                diagnostic=lambda: (
                    (startup_diagnostic or "No pre-broker startup snapshot")
                    + "\n" + query("capture-pane", "-p", "-t", target).stdout[-8192:]
                    + "\n" + pane_process()
                    + "\n" + query("capture-pane", "-p", "-t", outer_target).stdout[-8192:]
                    + "\n" + pane_process(outer_target)
                    + "\n" + json.dumps([
                        {key: value for key, value in json.loads(
                            path.read_text(encoding="utf-8")
                        ).items() if key in {
                            "state", "pid", "child_pid", "child_exit_code", "control_error",
                            "host_process_start_time", "child_process_start_time",
                        }}
                        for path in attempt.glob("*/headed.json")
                    ])
                    + "\n" + json.dumps([
                        [{"phase": event["phase"], "details": {
                            key: value for key, value in event.get("details", {}).items()
                            if key in {"pid", "boundary", "exit_code", "process_exit_code", "receipt_uncertain"}
                        }} for event in json.loads(
                            path.read_text(encoding="utf-8")
                        ).get("events", [])]
                        for path in record.parent.glob("launch-attempts/*/*.execution.json")
                    ])
                    + "\n" + json.dumps([
                        str(path.relative_to(record.parent))
                        for path in record.parent.glob("launch-attempts/**/*.json")
                    ])
                    + "\n" + query(
                        "display-message", "-p", "-t", target,
                        "#{pane_pid}|#{pane_current_command}|#{pane_start_command}|#{pane_dead}",
                    ).stdout[-4096:]
                    + "\n" + (owner_log.read_text(encoding="utf-8") if owner_log.exists() else "owner not started")
                    + "\n" + "\n".join(
                        path.read_text(encoding="utf-8", errors="replace")[-4096:]
                        for path in (tmp_path / ("logs-" + leg)).glob("*.log")
                    )
                    + "\n" + "\n".join(
                        path.read_text(encoding="utf-8", errors="replace")[-8192:]
                        for path in (
                            *(home / ".psmux").glob("*.log"),
                            *(temporary / "worktree-setup-logs").glob("setup-*.log"),
                            *((tmp_path / "mux-commands.txt",)
                              if (tmp_path / "mux-commands.txt").exists() else ()),
                        )
                    )
                ),
            )
            histories.append(state)
            print(f"Native {control} {leg}: broker bound in {time.monotonic() - leg_started:.2f}s",
                  flush=True)
            assert state["session_id"] == native and state["restoration"] == "unqualified"
            assert pid_alive(state["pid"]) and pid_alive(state["child_pid"])
            if control == "manager-mux":
                assert target in identities
                _until(
                    result_file.exists, f"{leg}: nested Manager caller did not return",
                    timeout=60, diagnostic=caller_diagnostics,
                )
                assert result_file.read_text(encoding="utf-8") == "0"
                _until(
                    lambda: query("has-session", "-t", outer_target).returncode == 1,
                    f"{leg}: original Manager caller pane did not close",
                )
                assert pid_alive(state["pid"]) and pid_alive(state["child_pid"])
                if manager_python is not None:
                    live_mapping = _until(
                        lambda: (value if (value := manager_mapping()) and value["live"] else None),
                        f"{leg}: installed Manager did not register its live CLI pane",
                    )
                    incarnation, pane_id = identities[target].split("|")
                    assert live_mapping["project"] == "native-copilot"
                    assert live_mapping["worktree_id"] == worktree_id
                    assert Path(live_mapping["worktree_path"]).samefile(checkout)
                    assert Path(live_mapping["mux_bin"]).samefile(mux_directory / "psmux.exe")
                    assert live_mapping["mux_session"] == target
                    assert live_mapping["session_incarnation"] == incarnation
                    assert live_mapping["panes"] == [
                        {"pane_id": pane_id, "role": "cli", "live": True},
                    ]
                    if retired_mapping is not None:
                        assert live_mapping["mapping_revision"] > retired_mapping["mapping_revision"]
                        assert incarnation != retired_mapping["session_incarnation"]
            event_file = home / "session-state" / native / "events.jsonl"

            def messages():
                if not event_file.is_file():
                    return []
                return [
                    json.loads(line) for line in event_file.read_text(encoding="utf-8").splitlines(True)
                    if line.endswith("\n")
                ]

            expected_before = 1 if leg == "fresh" else len(prompts) - 1
            _until(
                lambda: sum(event["type"] == "assistant.turn_end" for event in messages()) >= expected_before,
                f"{leg}: native history did not restore or finish its original seed",
            )
            if control == "manager-mux" and verify_reuse:
                identities.pop(outer_target)
                outer_target = "headed-fixture-" + uuid4().hex
                result_file.unlink()
                owner_ready.unlink()
                owner_log.unlink()
                assert query("has-session", "-t", outer_target).returncode == 1
                reused = query(
                    "new-session", "-d", "-s", outer_target, "-c", str(checkout),
                    *caller_environment, "--", *caller_command,
                )
                assert reused.returncode == 0, reused.stderr
                observed = query(
                    "display-message", "-p", "-t", outer_target,
                    "#{session_id}:#{session_created}|#{pane_id}",
                )
                assert observed.returncode == 0 and "|" in observed.stdout
                identities[outer_target] = observed.stdout.strip()
                owner_ready.write_text("ready", encoding="utf-8")
                _until(
                    result_file.exists, f"{leg}: Manager reuse caller did not return",
                    timeout=60, diagnostic=caller_diagnostics,
                )
                assert result_file.read_text(encoding="utf-8") == "0", owner_log.read_text(encoding="utf-8")
                _until(
                    lambda: query("has-session", "-t", outer_target).returncode == 1,
                    f"{leg}: Manager reuse caller pane did not close",
                )
                observed = query(
                    "display-message", "-p", "-t", target,
                    "#{session_id}:#{session_created}|#{pane_id}",
                )
                assert observed.returncode == 0 and observed.stdout.strip() == identities[target]
                assert state["child_process_start_time"] == process_start_time(state["child_pid"])
                assert state["host_process_start_time"] == process_start_time(state["host_pid"])
            asyncio.run(broker(state, prompt=prompts[1] if leg == "fresh" else prompts[-1]))
            expected = prompts[:2] if leg == "fresh" else prompts
            if control == "native-input" and leg == "fresh":
                assert query("send-keys", "-t", target, "-l", "--", prompts[2]).returncode == 0
                assert query("send-keys", "-t", target, "Enter").returncode == 0
                _until(
                    lambda: sum(event["type"] == "assistant.turn_end" for event in messages()) >= 3,
                    "Native keyboard input did not complete in the original conversation",
                    diagnostic=lambda: query("capture-pane", "-p", "-t", target).stdout[-8192:],
                )
                asyncio.run(broker(state, prompt="MUST NOT ACCEPT AFTER NATIVE INPUT", refused=True))
                assert pid_alive(state["child_pid"]) and pid_alive(state["pid"])
                expected = prompts[:3]
            assert [event["data"]["content"] for event in messages() if event["type"] == "user.message"] == expected
            execution_files = list(attempt.glob("*.execution.json"))
            execution_file, = execution_files

            def spawned_receipt():
                receipt = json.loads(execution_file.read_text(encoding="utf-8"))
                return receipt if any(
                    event["phase"] == "process-spawned" for event in receipt["events"]
                ) else None

            backend = _until(
                spawned_receipt, f"{leg}: native execution did not publish its process receipt",
                diagnostic=lambda: (
                    execution_file.read_text(encoding="utf-8") + "\n"
                    + query("capture-pane", "-p", "-t", target).stdout[-8192:]
                    + "\n" + pane_process()
                ),
            )
            spawned, = [event for event in backend["events"] if event["phase"] == "process-spawned"]
            assert spawned["details"]["pid"] == state["child_pid"] != state["host_pid"]
            birth = spawned["details"]["process_start_time"]
            assert isinstance(birth, str) and birth
            assert birth == state["child_process_start_time"] == process_start_time(state["child_pid"])
            assert state["host_process_start_time"] == process_start_time(state["host_pid"])
            assert runtime(
                "import json,sys;from pathlib import Path;from agent_worktrees import launch_seed_state;"
                "print(json.dumps(launch_seed_state.peek(Path(sys.argv[1]),read_only=True) is None))", record,
            ) is True
            print(f"Native {control} {leg}: history/receipts in {time.monotonic() - leg_started:.2f}s",
                  flush=True)
            asyncio.run(broker(state, stop=True))
            _until(
                lambda: not any(pid_alive(state[key]) for key in ("pid", "child_pid", "extension_pid")),
                f"{leg}: exact fixture native processes did not retire",
            )
            _until(result_file.exists, f"{leg}: original Worktrees owner did not report its exit")
            state_path, = attempt.glob("*/headed.json")
            exited = json.loads(state_path.read_text(encoding="utf-8"))
            assert exited["state"] == "child_exited"
            exit_code = exited["child_exit_code"]
            assert type(exit_code) is int

            def returned():
                receipt = json.loads(execution_file.read_text(encoding="utf-8"))
                return receipt if receipt["events"][-1]["phase"] == "execution-returned" else None

            finished = _until(returned, f"{leg}: native execution did not publish its exit receipt")
            terminal = finished["events"][-1]
            assert terminal["phase"] == "execution-returned"
            assert terminal["details"] == {
                "exit_code": exit_code, "process_exit_code": exit_code, "receipt_uncertain": False,
            }
            assert int(result_file.read_text(encoding="utf-8")) == (
                0 if control == "manager-mux" else exit_code
            )
            pane_output = []

            def pane_closed():
                captured = query("capture-pane", "-p", "-t", target)
                if captured.returncode == 0:
                    pane_output[:] = [captured.stdout[-8192:]]
                return query("has-session", "-t", target).returncode == 1

            _until(pane_closed, f"{leg}: original fixture pane did not close")
            if control == "manager-mux" and manager_python is not None:
                retired_mapping = _until(
                    lambda: (value if (value := manager_mapping()) and not value["live"] else None),
                    f"{leg}: installed Manager did not retain the CLI exit tombstone",
                )
                assert retired_mapping["session_incarnation"] == incarnation
                assert retired_mapping["mapping_revision"] > live_mapping["mapping_revision"]
                assert retired_mapping["panes"] == [
                    {"pane_id": pane_id, "role": "cli", "live": False},
                ]
                observed_end = retired_mapping["observed_end"]
                assert observed_end["pane_id"] == pane_id
                assert observed_end["session_incarnation"] == incarnation
                assert observed_end["exit_code"] == exit_code, "\n".join(pane_output)
                assert observed_end["observed_at"]
            identities, state = {}, None
            print(f"Native {control} {leg}: retired in {time.monotonic() - leg_started:.2f}s",
                  flush=True)
        assert (
            histories[0]["child_pid"], histories[0]["child_process_start_time"],
        ) != (histories[1]["child_pid"], histories[1]["child_process_start_time"])
        assert len(requests) == len(prompts)
        assert list((home / "session-state").glob("*/events.jsonl")) == [
            home / "session-state" / native / "events.jsonl",
        ]
        assert not (home / "permissions-config.json").exists()
        if control == "manager-mux":
            commands = (tmp_path / "mux-commands.txt").read_text(encoding="utf-8").splitlines()
            assert commands.count("new-session") == 2
            assert "set-option" in commands and "source-file" in commands
    finally:
        try:
            if state is not None and pid_alive(state["pid"]):
                asyncio.run(broker(state, stop=True))
            for owned_target in dict.fromkeys((outer_target, target)):
                present = query("has-session", "-t", owned_target)
                assert present.returncode in (0, 1), present.stderr
                if present.returncode == 0:
                    observed = query(
                        "display-message", "-p", "-t", owned_target,
                        "#{session_id}:#{session_created}|#{pane_id}",
                    )
                    assert observed.returncode == 0 and observed.stdout.strip() == identities.get(owned_target)
                    assert query("kill-session", "-t", owned_target).returncode == 0
        finally:
            provider.shutdown()
            provider.server_close()
            thread.join(timeout=5)
