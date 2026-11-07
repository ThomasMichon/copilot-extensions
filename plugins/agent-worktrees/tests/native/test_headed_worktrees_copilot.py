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


@pytest.mark.parametrize("control", ["broker", "native-input"])
def test_worktrees_native_fresh_and_resume_keep_one_history_and_real_receipts(tmp_path, control):
    from plugin_test_command_safety import private_mux_args

    executable = os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE")
    binary = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux.exe")
    shell = shutil.which("pwsh")
    assert executable and Path(executable).is_absolute() and Path(executable).is_file()
    assert binary and shell, "Select existing native Copilot, PSMux and PowerShell"
    owner_python = physical_executable(getattr(sys, "_base_executable", sys.executable))
    assert owner_python
    site_packages = sysconfig.get_paths()["purelib"]
    prefix = [sys.executable, "-I", "-B", "-X", "utf8"]
    home, checkout = tmp_path / "copilot-home", tmp_path / "checkout"
    home.mkdir()
    checkout.mkdir()
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
        key: value for key, value in os.environ.items()
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
        PSMUX_DATA_DIR=str(tmp_path / "psmux-data"), PSMUX_CONFIG_FILE=os.devnull,
        PSMUX_NO_WARM="1", WORKTREE_PANE_MIN_RUNTIME="0",
    )
    mux_directory = tmp_path / "discovery"
    mux_directory.mkdir()
    namespace = private_mux_args(tmp_path)
    environment["DRIVER_FIXTURE_MUX_NAMESPACE"] = namespace[1]
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
using System.Linq;
using System.Text.RegularExpressions;
class Mux {
    static string Quote(string value) {
        return "\"" + Regex.Replace(
            Regex.Replace(value, "(\\\\*)\"", "$1$1\\\""), "(\\\\+)$", "$1$1") + "\"";
    }
    static int Main(string[] args) {
        if (args.Length == 0 || !new[] {
            "list-sessions", "has-session", "display-message", "list-panes", "list-windows"
        }.Contains(args[0]) || args.Any(value => value == "-L" || value == "-S"))
            return 2;
        using (var child = Process.Start(new ProcessStartInfo {
            FileName = BINARY,
            Arguments = String.Join(" ", new[] {
                "-L", Environment.GetEnvironmentVariable("DRIVER_FIXTURE_MUX_NAMESPACE")
            }.Concat(args).Select(Quote)),
            UseShellExecute = false, CreateNoWindow = true
        })) {
            if (!child.WaitForExit(5000)) { child.Kill(); child.WaitForExit(); return 2; }
            return child.ExitCode;
        }
    }
}
'''.replace("BINARY", '@"' + str(binary).replace('"', '""') + '"'),
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
    target = "headed-fixture-" + uuid4().hex

    def query(action, *arguments):
        assert action in {
            "new-session", "has-session", "display-message", "kill-session", "capture-pane", "send-keys",
        }
        assert not any(value in {"-L", "-S", ";", "kill-server", "server"} for value in arguments)
        selector = "-s" if action == "new-session" else "-t"
        assert list(arguments).count(selector) == 1
        assert arguments[arguments.index(selector) + 1] == target
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

    def pane_process():
        observed = query("display-message", "-p", "-t", target, "#{pane_pid}")
        if not observed.stdout.strip().isdigit():
            return observed.stderr
        pid = int(observed.stdout.strip())
        detail = run_caller_owned(
            [shell, "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' | "
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

    identity, state = None, None
    histories = []
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    try:
        initialized = runtime(
            "import json,sys;from pathlib import Path;from agent_worktrees import tracking,config as cfg;"
            "checkout=Path(sys.argv[1]);project=cfg.project_dir('native-copilot');"
            "project.mkdir(parents=True,exist_ok=True);"
            "(project/'config.yaml').write_text(json.dumps({'repo_name':'native-copilot','machine':'fixture',"
            "'repos':{'native-copilot':{'anchor':str(checkout),'default_branch':'dev'}}}),encoding='utf-8');"
            "record=tracking.create_new_record(sys.argv[2],'worktree/native-copilot',str(checkout),"
            "'native-copilot','fixture','win32',checkout);print(json.dumps(str(record.yaml_path)))",
            checkout, tmp_path.name,
        )
        record = Path(initialized)
        for leg in ("fresh", "resume"):
            namespace = private_mux_args(tmp_path)
            environment["DRIVER_FIXTURE_MUX_NAMESPACE"] = namespace[1]
            target = "headed-fixture-" + uuid4().hex
            plan = runtime(
                "import json,sys;from pathlib import Path;from uuid import uuid4;"
                "from agent_worktrees import launch_seed_state;"
                "record=Path(sys.argv[1]);"
                "seed=launch_seed_state.stage(record,kind='new',text=sys.argv[2]) if sys.argv[3]=='fresh' else None;"
                "attempt,resolution=uuid4().hex,uuid4().hex;"
                "print(json.dumps({'attempt':attempt,'resolution':resolution,"
                "'seed':seed.seed_id if seed else None}))",
                record, prompts[0], leg,
            )
            attempt = record.parent / "launch-attempts" / plan["attempt"]
            native_args = [
                "--session-id" if leg == "fresh" else "--resume", native,
                "--screen-reader", "--no-auto-update", "--disable-builtin-mcps",
                "--no-custom-instructions", "--no-color", "--no-ask-user",
                "--log-dir", str(tmp_path / ("logs-" + leg)),
            ]
            driver = tmp_path / (leg + ".py")
            result_file = tmp_path / (leg + "-result.txt")
            owner_log = tmp_path / (leg + "-owner.log")
            driver.write_text(
                "import contextlib,ctypes,msvcrt,os,site,sys,traceback\nfrom pathlib import Path\n"
                f"with Path({str(owner_log)!r}).open('w',encoding='utf-8') as log,"
                "contextlib.redirect_stderr(log):\n"
                "  try:\n"
                "    os.dup2(log.fileno(),2)\n"
                "    set_stderr=ctypes.windll.kernel32.SetStdHandle\n"
                "    set_stderr.argtypes=[ctypes.c_ulong,ctypes.c_void_p]\n"
                "    assert set_stderr(-12,msvcrt.get_osfhandle(2))\n"
                "    log.write('original owner started\\n');log.flush()\n"
                f"    site.addsitedir({site_packages!r})\n"
                "    from agent_worktrees import launch_seed_exec,launch_execution\n"
                f"    code=launch_seed_exec.launch(Path({str(record)!r}),{[executable, *native_args]!r},"
                f"invoke=True,seed_id={plan['seed']!r},"
                f"correlation=launch_execution.Correlation({plan['attempt']!r},{plan['resolution']!r}))\n"
                "  except BaseException:\n"
                "    traceback.print_exc(file=log);code=3\n"
                f"  result=Path({str(result_file)!r})\n"
                "  temporary=result.with_suffix('.tmp')\n"
                "  temporary.write_text(str(code),encoding='utf-8')\n"
                "  temporary.replace(result)\n"
                "sys.exit(code)\n",
                encoding="utf-8",
            )
            assert query("has-session", "-t", target).returncode == 1
            launched = query(
                "new-session", "-d", "-s", target, "-c", str(checkout),
                "--", owner_python, "-I", "-B", str(driver),
            )
            assert launched.returncode == 0, launched.stderr
            observed = query(
                "display-message", "-p", "-t", target, "#{session_id}:#{session_created}|#{pane_id}",
            )
            assert observed.returncode == 0 and "|" in observed.stdout
            identity = observed.stdout.strip()

            def bound():
                if result_file.exists():
                    raise AssertionError(owner_log.read_text(encoding="utf-8"))
                paths = list(attempt.glob("*/headed.json"))
                if paths:
                    try:
                        value = json.loads(paths[0].read_text(encoding="utf-8"))
                    except (FileNotFoundError, PermissionError):
                        return None
                    if value["state"] == "running":
                        return value

            state = _until(
                bound, f"{leg}: Worktrees launch did not bind the common broker",
                diagnostic=lambda: (
                    query("capture-pane", "-p", "-t", target).stdout[-8192:]
                    + "\n" + pane_process()
                    + "\n" + json.dumps([
                        {key: value for key, value in json.loads(
                            path.read_text(encoding="utf-8")
                        ).items() if key in {
                            "state", "pid", "child_pid", "child_exit_code", "control_error",
                            "host_process_start_time", "child_process_start_time",
                        }}
                        for path in attempt.glob("*/headed.json")
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
                ),
            )
            histories.append(state)
            assert state["session_id"] == native and state["restoration"] == "unqualified"
            assert pid_alive(state["pid"]) and pid_alive(state["child_pid"])
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
            executions = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in execution_files
            ]
            backend, = executions
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
            finished = json.loads(execution_files[0].read_text(encoding="utf-8"))
            terminal = finished["events"][-1]
            assert terminal["phase"] == "execution-returned"
            assert terminal["details"] == {
                "exit_code": exit_code, "process_exit_code": exit_code, "receipt_uncertain": False,
            }
            assert int(result_file.read_text(encoding="utf-8")) == exit_code
            _until(
                lambda: query("has-session", "-t", target).returncode == 1,
                f"{leg}: original fixture pane did not close",
            )
            identity, state = None, None
        assert (
            histories[0]["child_pid"], histories[0]["child_process_start_time"],
        ) != (histories[1]["child_pid"], histories[1]["child_process_start_time"])
        assert len(requests) == len(prompts)
        assert list((home / "session-state").glob("*/events.jsonl")) == [
            home / "session-state" / native / "events.jsonl",
        ]
        assert not (home / "permissions-config.json").exists()
    finally:
        try:
            if state is not None and pid_alive(state["pid"]):
                asyncio.run(broker(state, stop=True))
            present = query("has-session", "-t", target)
            assert present.returncode in (0, 1), present.stderr
            if present.returncode == 0:
                observed = query(
                    "display-message", "-p", "-t", target, "#{session_id}:#{session_created}|#{pane_id}",
                )
                assert identity is not None and observed.returncode == 0 and observed.stdout.strip() == identity
                assert query("kill-session", "-t", target).returncode == 0
        finally:
            provider.shutdown()
            provider.server_close()
            thread.join(timeout=5)
