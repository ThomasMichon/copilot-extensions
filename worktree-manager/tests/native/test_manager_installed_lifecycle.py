"""Installed project entry -> real Manager controls -> native CLI continuity."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import pytest
import yaml

import test_manager_installed_tty as installed

PROJECT = "tty-harness"
KNOWLEDGE = "fixture-knowledge"
PROMPTS = (
    "LIFECYCLE_FRESH_71",
    "LIFECYCLE_OPEN_72",
    "LIFECYCLE_DETACHED_73",
    "LIFECYCLE_RESUMED_74",
)


@contextmanager
def _provider():
    requests = []

    class Provider(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.connection.settimeout(3)
            length = int(self.headers["Content-Length"])
            if not 0 < length <= 1 << 20:
                self.send_error(413)
                return
            request = json.loads(self.rfile.read(length))
            requests.append(request)
            content = json.dumps(request)
            reply = max(
                (i + 1 for i, prompt in enumerate(PROMPTS) if prompt in content), default=0
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            message = {
                "id": "lifecycle-fixture",
                "object": "chat.completion.chunk",
                "model": "gpt-4.1",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "content": f"LIFECYCLE_REPLY_{reply}",
                        },
                        "finish_reason": "stop",
                    }
                ],
            }
            self.wfile.write(
                ("data: " + json.dumps(message) + "\n\ndata: [DONE]\n\n").encode(),
            )
            self.wfile.flush()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    failure = None
    try:
        yield server.server_port, requests
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            server.shutdown()
            server.server_close()
            thread.join(5)
            if thread.is_alive():
                raise RuntimeError("The private inference endpoint did not stop")
        except BaseException as error:
            if failure is None:
                raise
            failure.add_note(f"Inference endpoint cleanup: {error}")


@contextmanager
def _capture_failure(root, record_property):
    try:
        yield
    except BaseException as error:
        try:
            evidence = installed._failure_evidence(root)
            for pattern in (
                "lifecycle-*.terminal.txt",
                "knowledge-*.stdout.txt",
                "knowledge-*.stderr.txt",
                "lifecycle-bootstrap.stdout.txt",
                "lifecycle-bootstrap.stderr.txt",
                "lifecycle-bootstrap.custody.json",
                "lifecycle-launch.jsonl",
                "lifecycle-processes.jsonl",
                "lifecycle-conversation.json",
                "lifecycle-pane-*.stdout.txt",
                "lifecycle-pane-*.stderr.txt",
            ):
                for path in (root / "diagnostics").glob(pattern):
                    text = (
                        _terminal_tail(path)
                        if pattern.startswith("lifecycle-")
                        else installed._diagnostic_excerpt(path, 8192)
                    )
                    evidence[path.name] = {"text": text}
            record_property("installed-manager-lifecycle-diagnostics", json.dumps(evidence))
        except Exception as diagnostic_error:
            error.add_note(f"Lifecycle diagnostic capture: {diagnostic_error}")
        raise


def _terminal_tail(path):
    info = path.lstat()
    if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Terminal evidence must be an ordinary owned file")
    if not path.is_file() or info.st_size > 1 << 20:
        raise ValueError("Terminal evidence exceeds its retained bound")
    with path.open("rb") as stream:
        stream.seek(max(0, info.st_size - 8192))
        return installed._visible(stream.read(8192))


def _manager_ready(output):
    visible = installed._visible(output)
    header = visible.rfind("Worktree Manager")
    current = visible[header:] if header >= 0 else ""
    return (
        b"\x1b[?1049h" in output
        and "host tty-fixture" in current
        and PROJECT in current
        and "worktrees" in current.casefold()
    )


def _require_mux_venue():
    from agent_remote_driver import windows_mux_processes
    from agent_remote_driver.mux_observation_types import ObservationUnavailable

    try:
        servers = windows_mux_processes.servers()
    except (ObservationUnavailable, OSError) as error:
        raise RuntimeError(
            "Private lifecycle mux scope is unconfirmed; use a disposable Windows runner",
        ) from error
    if servers:
        raise RuntimeError(
            "Private lifecycle needs an empty mux-server cohort; use a disposable Windows runner",
        )


def _job_member(console, handle):
    import ctypes
    from ctypes import wintypes

    query = console.api.IsProcessInJob
    query.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    query.restype = wintypes.BOOL
    inside = wintypes.BOOL()
    if not query(handle, console.job.handle, ctypes.byref(inside)):
        raise ctypes.WinError(ctypes.get_last_error())
    return bool(inside.value)


def _process_arguments(handle):
    import ctypes
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.USHORT),
            ("maximum", wintypes.USHORT),
            ("buffer", ctypes.c_void_p),
        ]

    query = ctypes.WinDLL("ntdll").NtQueryInformationProcess
    query.argtypes = [
        wintypes.HANDLE,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
    ]
    query.restype = wintypes.LONG
    size = wintypes.ULONG()
    query(handle, 60, None, 0, ctypes.byref(size))
    if not ctypes.sizeof(UnicodeString) <= size.value <= 65536:
        raise RuntimeError("Owned command metadata is unavailable or exceeds its bound")
    buffer = ctypes.create_string_buffer(size.value)
    if query(handle, 60, buffer, size.value, ctypes.byref(size)) != 0:
        raise RuntimeError("Owned command metadata query failed")
    value = UnicodeString.from_buffer(buffer)
    start = ctypes.addressof(buffer)
    if (
        value.length % 2
        or value.length > value.maximum
        or not value.buffer
        or not start <= value.buffer <= start + len(buffer)
        or value.buffer + value.length > start + len(buffer)
    ):
        raise RuntimeError("Owned command metadata returned an invalid buffer")
    command = ctypes.wstring_at(value.buffer, value.length // 2)
    parse = ctypes.WinDLL("shell32").CommandLineToArgvW
    parse.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(wintypes.LPWSTR)
    count = ctypes.c_int()
    arguments = parse(command, ctypes.byref(count))
    if not arguments:
        raise RuntimeError("Owned command metadata could not be parsed")
    release = ctypes.WinDLL("kernel32").LocalFree
    release.argtypes, release.restype = [ctypes.c_void_p], ctypes.c_void_p
    try:
        if not 0 < count.value <= 256:
            raise RuntimeError("Owned argument count exceeds its bound")
        values = [arguments[index] for index in range(count.value)]
    finally:
        release(arguments)
    module = next(
        (values[i + 1] for i, value in enumerate(values[:-1]) if value == "-m"),
        None,
    )
    cache = next((value for value in values if value.startswith("pycache_prefix=")), "")
    return {
        "program": values[0][:2048],
        "module": module if module in {"agent_worktrees", "worktree_manager"} else None,
        "verb": next(
            (
                value
                for value in values
                if value
                in {
                    "picker-bootstrap",
                    "coordination-readiness",
                    "get",
                }
            ),
            None,
        ),
        "project_matches": any(
            value == "--project" and values[index + 1] == PROJECT
            for index, value in enumerate(values[:-1])
        ),
        "python_flags": [value for value in values if value in {"-I", "-B", "-S"}],
        "cache_namespace": Path(cache.split("=", 1)[1]).name if cache else None,
    }


def _case_environment(root):
    env = installed._environment(root)
    identities = {
        "SESSION_ID",
        "WORKTREE_ID",
        "WORKTREE_DIR",
        "WT_SESSION",
        "TERM_PROGRAM",
        "OPENAI_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GITHUB_API_TOKEN",
        "AGENT_WORKTREES_LAUNCH_ID",
        "AGENT_WORKTREES_BINSTUB_STARTED",
        "AGENT_WORKTREES_PICKER_FRAME_HEALTH",
        "AGENT_WORKTREES_LAUNCH_TRACE",
    }
    for name in list(env):
        key = name.upper()
        if key in identities or (
            key.startswith(("COPILOT_", "PSMUX_", "TMUX"))
            and not key.startswith("COPILOT_EXTENSIONS_")
        ):
            env.pop(name)
    copilot_home = Path(env["USERPROFILE"]) / ".copilot"
    copilot_home.mkdir(parents=True, exist_ok=True)
    data = root / "x"
    data.mkdir()
    env.update(
        {
            "COPILOT_HOME": str(copilot_home),
            "PSMUX_DATA_DIR": str(data),
            "TMUX_TMPDIR": str(data),
            "PSMUX_CONFIG_FILE": os.devnull,
            "PSMUX_NO_WARM": "1",
            "WORKTREE_PANE_MIN_RUNTIME": "0",
            "WORKTREE_PANE_WAIT_TIMEOUT": "0",
            "AGENT_WORKTREES_LAUNCH_ID": "installed-lifecycle-" + root.name,
            "AGENT_WORKTREES_LAUNCH_TRACE": str(root / "diagnostics" / "lifecycle-launch.jsonl"),
        }
    )
    return env


def _events(path, owner):
    if not path.resolve().is_relative_to(owner.resolve()):
        raise RuntimeError("Conversation evidence escaped the private Copilot home")
    if not path.is_file():
        return []
    if path.is_symlink() or path.stat().st_size > 4 << 20:
        raise RuntimeError("Native conversation evidence is indirect or exceeds its bound")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines(True)
        if line.endswith("\n")
    ]


class _Witness:
    def __init__(self, root, env, manager_python, engine, copilot, budget):
        self.root, self.env = root, env
        self.manager_python = manager_python
        self.engine, self.copilot = engine, copilot
        self.budget = budget
        self.consoles = []
        self.native_handles = {}
        self.history = None
        self.turn_observation = None
        self.stack = ExitStack()

    def wait(self, check, label, timeout=30):
        deadline = min(time.monotonic() + timeout, self.budget.execution_deadline)
        while time.monotonic() < deadline:
            value = check()
            if value:
                return value
            for console in self.consoles:
                if console.read_error:
                    raise RuntimeError(console.read_error)
            time.sleep(0.05)
        screens = "\n".join(installed._visible(c.output)[-4000:] for c in self.consoles)
        try:
            self._capture_processes()
        except Exception as diagnostic_error:
            screens += f"\nOwned-process diagnostic: {diagnostic_error}"
        raise TimeoutError(f"{label}\n{screens}")

    def _capture_processes(self):
        from agent_procutil._windows_caller_owned import _job_pids

        rows = []
        for console in self.consoles:
            if console.job is None or not console.job.handle:
                continue
            for pid in _job_pids(console.job.handle):
                if len(rows) >= 16 or time.monotonic() >= self.budget.execution_deadline:
                    break
                handle = None
                try:
                    handle = console.pin(pid)
                    if console.exited(handle) or not _job_member(console, handle):
                        continue
                    birth = console.birth(handle)
                    row = {
                        "pid": pid,
                        "birth": birth,
                        "image": str(installed._image(console, handle)),
                        **_process_arguments(handle),
                    }
                    if not console.exited(handle):
                        rows.append(row)
                except (OSError, RuntimeError) as error:
                    if handle is None and pid not in _job_pids(console.job.handle):
                        continue
                    rows.append({"census_pid": pid, "observation_error": type(error).__name__})
                finally:
                    if handle in console.handles:
                        console._close(handle)
        rows.sort(key=lambda row: row.get("verb") == "picker-bootstrap")
        path = self.root / "diagnostics" / "lifecycle-processes.jsonl"
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows)
            + json.dumps(
                {
                    "observation": "bounded-owned-process-sample",
                    "complete": False,
                    "limit": 16,
                    "captured": len(rows),
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def manager(self):
        stub = Path(self.env["USERPROFILE"]) / ".local" / "bin" / (PROJECT + ".cmd")
        console = self.stack.enter_context(
            installed._owned_console(
                installed._project_entry_command("cmd", stub),
                env=self.env,
                cwd=self.root / PROJECT,
            )
        )
        self.consoles.append(console)
        self.wait(
            lambda: _manager_ready(console.output),
            "The installed project entry did not render Manager",
        )
        return console

    def native(self):
        from agent_procutil._windows_caller_owned import _job_pids

        selected = {}
        for console in self.consoles:
            if console.job is None or not console.job.handle:
                continue
            for pid in _job_pids(console.job.handle):
                try:
                    handle = console.pin(pid)
                except OSError:
                    if pid not in _job_pids(console.job.handle):
                        continue
                    raise
                try:
                    if (
                        console.exited(handle)
                        or not _job_member(
                            console,
                            handle,
                        )
                        or not installed._image(
                            console,
                            handle,
                        ).samefile(self.copilot)
                    ):
                        console._close(handle)
                        continue
                    identity = (pid, console.birth(handle))
                    if identity in self.native_handles:
                        console._close(handle)
                    else:
                        self.native_handles[identity] = (console, handle)
                    selected[identity] = self.native_handles[identity]
                except BaseException:
                    if handle in console.handles:
                        console._close(handle)
                    raise
        return selected

    def one_native(self):
        selected = self.native()
        if len(selected) > 1:
            raise RuntimeError(f"Manager admitted duplicate native CLI owners: {list(selected)}")
        return next(iter(selected)) if selected else None

    def turn(self, prompt, number, console):
        def finished():
            paths = (
                [self.history]
                if self.history
                else sorted(
                    (Path(self.env["COPILOT_HOME"]) / "session-state").glob("*/events.jsonl"),
                )
            )
            matches = []
            histories = []
            for path in paths:
                events = _events(path, Path(self.env["COPILOT_HOME"]))
                texts = [e["data"]["content"] for e in events if e["type"] == "user.message"]
                turn_ends = sum(e["type"] == "assistant.turn_end" for e in events)
                histories.append({
                    "session": path.parent.name,
                    "events": len(events),
                    "prompt_seen": prompt in texts,
                    "turn_ends": turn_ends,
                    "reply_seen": any(
                        e["type"] == "assistant.message"
                        and e["data"].get("content") == f"LIFECYCLE_REPLY_{number}"
                        for e in events
                    ),
                })
                if (
                    prompt in texts
                    and turn_ends >= number
                ):
                    matches.append(path)
            self.turn_observation = {
                "turn": number,
                "histories": histories,
                "reply_in_terminal": f"LIFECYCLE_REPLY_{number}" in installed._visible(console.output),
            }
            if len(matches) > 1:
                raise RuntimeError("One Manager operation produced multiple conversations")
            if not matches:
                return None
            self.history = matches[0]
            if not self.turn_observation["reply_in_terminal"]:
                rendered = self._reply_in_pane(number)
                self.turn_observation["reply_in_pane"] = rendered
                if not rendered:
                    return None
            return self.one_native()

        return self.wait(finished, f"Native conversation did not finish {prompt}", timeout=60)

    def _reply_in_pane(self, number):
        record = self.records(f"records-rendering-{number}")
        rendered = installed._run(
            [
                self.root / "mux" / "psmux.exe", "capture-pane", "-p",
                "-t", "wt-" + record["id"], "-S", "-60",
            ],
            self.env,
            self.root,
            self.root / "diagnostics",
            f"lifecycle-pane-{number}",
            timeout=5,
        )
        return f"LIFECYCLE_REPLY_{number}" in rendered

    def records(self, label):
        code = (
            "import json;from agent_worktrees import config,tracking;"
            f"rows=tracking.list_records(config.tracking_dir({PROJECT!r}));"
            "print(json.dumps([{'id':r.worktree_id,'path':r.worktree_path,"
            "'head':r.resolved_head_session,"
            "'sessions':[s.session_id for s in r.sessions or []]}"
            " for r in rows if r.kind=='session']))"
        )
        rows = json.loads(
            installed._run(
                [self.engine, "-I", "-B", "-c", code],
                self.env,
                self.root,
                self.root / "diagnostics",
                label,
                timeout=30,
            )
        )
        if len(rows) != 1:
            raise RuntimeError(f"Manager did not create exactly one worktree: {rows}")
        (record,) = rows
        if (
            not Path(record["path"]).resolve().is_relative_to(self.root.resolve())
            or record["head"] != self.history.parent.name
            or record["sessions"] != [self.history.parent.name]
        ):
            raise RuntimeError(f"Native worktree/head/history binding is inconsistent: {record}")
        return record

    def select(self, console, record, verb):
        self.wait(
            lambda: record["id"][-4:] in installed._visible(console.output),
            "The created worktree did not appear in real Manager discovery",
        )
        console.send(b"/")
        console.send(record["id"][-4:].encode() + b"\r")
        before = len(console.output)
        console.send(b"\t\r")
        self.wait(
            lambda: verb in installed._visible(console.output[before:]),
            f"Real Manager discovery did not offer {verb}",
        )
        console.send(b"\r")

    def close_cli(self, console, identity):
        owner, handle = self.native_handles[identity]
        before = len(console.output)
        console.send(b"/exit\r")
        self.wait(lambda: owner.exited(handle), "Copilot did not exit normally")
        self.wait(
            lambda: (
                console.exited(console.process.process)
                or "New worktree" in installed._visible(console.output[before:])
            ),
            "The native launcher neither exited nor returned to Manager",
        )
        if not console.exited(console.process.process):
            console.send(b"q")
            self.wait(
                lambda: "Leave the worktree picker?"
                in installed._visible(console.output[before:]),
                "Manager did not acknowledge a normal quit",
            )
            console.send(b"y")
        if console.wait(15000) != 0:
            raise RuntimeError("The normal native/Manager exit returned nonzero")

    def __enter__(self):
        return self

    def __exit__(self, *error):
        failure = error[1]
        try:
            return self.stack.__exit__(*error)
        except BaseException as cleanup_error:
            failure = cleanup_error
            raise
        finally:
            if self.turn_observation is not None:
                try:
                    (self.root / "diagnostics" / "lifecycle-conversation.json").write_text(
                        json.dumps(self.turn_observation), encoding="utf-8",
                    )
                except OSError as write_error:
                    if failure is None:
                        raise
                    failure.add_note(f"Conversation diagnostic write: {write_error}")
            for index, console in enumerate(self.consoles):
                try:
                    (self.root / "diagnostics" / f"lifecycle-{index}.terminal.txt").write_bytes(
                        bytes(console.output),
                    )
                except OSError as write_error:
                    if failure is None:
                        raise
                    failure.add_note(f"Terminal diagnostic write: {write_error}")
            if failure is not None:
                from plugin_test_containment import UnreapedProcessError

                if not isinstance(failure, UnreapedProcessError):
                    try:
                        self._diagnose_bootstrap()
                    except UnreapedProcessError:
                        raise
                    except BaseException as diagnostic_error:
                        failure.add_note(f"Bootstrap diagnostic: {diagnostic_error}")

    def _diagnose_bootstrap(self):
        program = """
import json,sys
from pathlib import Path
from worktree_manager import engine_client,harness_state
from worktree_manager.production_picker import engine_group_b
root=Path(sys.argv[1]).resolve()
if not harness_state.home().resolve().is_relative_to(root):
    raise RuntimeError("Bootstrap diagnostic escaped the private home")
command=engine_client.engine_base_command()
data={
    "diagnostic":"direct-selected-Manager-read-not-project-handoff",
    "engine_program":command[0] if command else None,
    "projects":[{"name":p.name,"config_dir":p.config_dir,"anchor":p.anchor}
                for p in harness_state.build_projects()],
}
print(json.dumps(data),flush=True)
if command and Path(command[0]).resolve().is_relative_to(root):
    try:
        observed=engine_group_b.picker_bootstrap(sys.argv[2],timeout=5)
        config=observed.get("project_config",{})
        data["bootstrap"]={
            "project":observed.get("project"),
            "project_config":{k:config.get(k) for k in
                              ("machine","configured_machine","config_dir","repo_dir")},
        }
    except engine_client.EngineRetirementError:
        raise
    except engine_client.EngineError as error:
        data["bootstrap_error"]=str(error)
else:
    data["bootstrap_error"]="No private selected engine; no backend executed"
print(json.dumps(data),flush=True)
"""
        installed._run(
            [self.manager_python, "-I", "-B", "-c", program, self.root, PROJECT],
            self.env,
            self.root / PROJECT,
            self.root / "diagnostics",
            "lifecycle-bootstrap",
            timeout=10,
        )


def _identity():
    from agent_procutil import no_window_kwargs

    def git(argument):
        return subprocess.run(
            ["git", "-C", str(installed.REPO), "rev-parse", argument],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
            env=installed._git_environment(),
            **no_window_kwargs(),
        ).stdout.strip()

    commit = git("HEAD")
    receipt = installed.REPO / "worktree-manager" / "runtime-source.json"
    source = (
        json.loads(receipt.read_text(encoding="utf-8"))["source_commit"]
        if receipt.is_file()
        else commit
    )
    return {
        "candidate_commit": commit,
        "candidate_tree": git("HEAD^{tree}"),
        "source_commit": source,
        "main_before": git("HEAD^"),
        "run_id": int(os.environ.get("GITHUB_RUN_ID", "1")),
        "run_attempt": int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")),
    }


def _project(root, env, engine, copilot):
    project = root / PROJECT
    project.mkdir()
    diagnostics = root / "diagnostics"
    installed._run(
        ["git", "init", "-q", "-b", "main", project],
        env,
        root,
        diagnostics,
        "project-init",
        timeout=30,
    )
    arguments = installed._harness_registration(engine, project, root / "payloads")
    arguments[arguments.index("--no-agent")] = "--agent"
    config = project / ".agent-worktrees" / "config.yaml"
    value = yaml.safe_load(config.read_text(encoding="utf-8"))
    launch = [
        str(copilot),
        "--experimental",
        "--screen-reader",
        "--no-auto-update",
        "--disable-builtin-mcps",
        "--no-color",
    ]
    value["launch"] = {"windows": launch}
    config.write_text(yaml.safe_dump(value), encoding="utf-8")
    installed._run(arguments, env, project, diagnostics, "register-project", timeout=30)
    knowledge = root / KNOWLEDGE
    knowledge.mkdir()
    installed._run(
        ["git", "init", "-q", "-b", "main", knowledge],
        env,
        root,
        diagnostics,
        "knowledge-init",
        timeout=30,
    )
    (knowledge / ".agent-worktrees").mkdir()
    (knowledge / ".agent-worktrees" / "config.yaml").write_text(
        yaml.safe_dump({"project": KNOWLEDGE, "expose_agent": False}),
        encoding="utf-8",
    )
    for name, repository in (("project", project), ("knowledge", knowledge)):
        for label, args in (
            ("add", ["add", "-A"]),
            (
                "commit",
                [
                    "-c",
                    "user.name=Lifecycle Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "-qm",
                    "Representative lifecycle fixture",
                ],
            ),
        ):
            installed._run(
                ["git", "-C", repository, *args],
                env,
                root,
                diagnostics,
                name + "-" + label,
                timeout=30,
            )
    installed._run(
        [
            engine,
            "-I",
            "-B",
            "-m",
            "agent_worktrees",
            "repos",
            "add",
            KNOWLEDGE,
            knowledge,
            "--class",
            "worktree",
            "--default-branch",
            "main",
            "--no-agent",
        ],
        env,
        project,
        diagnostics,
        "knowledge-register",
        timeout=30,
    )
    binding = Path(env["USERPROFILE"]) / ("." + PROJECT) / "config.yaml"
    configuration = yaml.safe_load(binding.read_text(encoding="utf-8"))
    configuration["knowledge_repo"] = KNOWLEDGE
    binding.write_text(yaml.safe_dump(configuration), encoding="utf-8")
    readiness = json.loads(
        installed._run(
            [engine, "-I", "-B", "-m", "agent_worktrees", "coordination-readiness"],
            env,
            project,
            diagnostics,
            "knowledge-readiness",
            timeout=30,
        )
    )
    if readiness["ready"] is not True or not Path(readiness["state_root"]["path"]).samefile(
        knowledge,
    ):
        raise RuntimeError("The installed harness did not resolve its real private knowledge repo")
    selected = installed._run(
        [
            engine,
            "-I",
            "-B",
            "-c",
            "import json;from agent_worktrees import config,repos;"
            f"config.set_active_project({PROJECT!r});"
            "print(json.dumps({'launch':config.load_config().default_repo.launch['windows'],"
            f"'agent':repos.find_repo({PROJECT!r}).agent}}))",
        ],
        env,
        project,
        diagnostics,
        "selected-native-launch",
        timeout=30,
    )
    if json.loads(selected) != {"launch": launch, "agent": True}:
        raise RuntimeError(
            "Installed Worktrees did not select the interactive fixture and native launch declaration"
        )


def test_lifecycle_diagnostics_retain_the_constructor_refusal(tmp_path):
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    (diagnostics / "install-engine.stderr.txt").write_text(
        "RuntimeError: controlled constructor refusal\n",
        encoding="utf-8",
    )
    records = []
    with pytest.raises(RuntimeError, match="outer construction failed"):
        with _capture_failure(tmp_path, lambda name, value: records.append((name, value))):
            raise RuntimeError("outer construction failed")
    assert len(records) == 1
    evidence = json.loads(records[0][1])
    assert "controlled constructor refusal" in evidence["install-engine.stderr.txt"]["text"]


@pytest.mark.parametrize("phase", ["missing-history", "unfinished", "rendering-only"])
def test_lifecycle_conversation_diagnostics_distinguish_history_from_rendering(tmp_path, phase):
    from types import SimpleNamespace

    (tmp_path / "diagnostics").mkdir()
    home = tmp_path / "copilot-home"
    history = home / "session-state" / "fixture-session" / "events.jsonl"
    if phase != "missing-history":
        history.parent.mkdir(parents=True)
        events = [{"type": "user.message", "data": {"content": PROMPTS[0]}}]
        if phase == "rendering-only":
            events += [
                {"type": "assistant.message", "data": {"content": "LIFECYCLE_REPLY_1"}},
                {"type": "assistant.turn_end", "data": {}},
            ]
        history.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    witness = _Witness(tmp_path, {"COPILOT_HOME": str(home)}, None, None, None, None)
    witness._diagnose_bootstrap = lambda: None
    witness._reply_in_pane = lambda _number: False

    def wait(check, *_args, **_kwargs):
        assert check() is None
        raise TimeoutError("fixture unfinished turn")

    witness.wait = wait
    records = []
    with pytest.raises(TimeoutError, match="fixture unfinished turn"):
        with _capture_failure(tmp_path, lambda _name, value: records.append(json.loads(value))), witness:
            witness.turn(PROMPTS[0], 1, SimpleNamespace(output=b"cursor redraw"))
    observed = json.loads(records[0]["lifecycle-conversation.json"]["text"])
    assert observed["reply_in_terminal"] is False
    if phase == "missing-history":
        assert observed["histories"] == []
    else:
        (row,) = observed["histories"]
        assert row["prompt_seen"] is True
        assert row["turn_ends"] == (1 if phase == "rendering-only" else 0)
        assert row["reply_seen"] is (phase == "rendering-only")


@pytest.mark.parametrize("reply", ["LIFECYCLE_REPLY_1", "unfinished"])
def test_lifecycle_completed_turn_uses_the_bound_rendered_pane(tmp_path, monkeypatch, reply):
    from types import SimpleNamespace

    home = tmp_path / "copilot-home"
    history = home / "session-state" / "fixture-session" / "events.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text("".join(json.dumps(event) + "\n" for event in (
        {"type": "user.message", "data": {"content": PROMPTS[0]}},
        {"type": "assistant.message", "data": {"content": "LIFECYCLE_REPLY_1"}},
        {"type": "assistant.turn_end", "data": {}},
    )), encoding="utf-8")
    witness = _Witness(tmp_path, {"COPILOT_HOME": str(home)}, None, None, None, None)
    witness.wait = lambda check, *_args, **_kwargs: check()
    witness.one_native = lambda: ("native-pid", "native-birth")
    calls = []

    def records(_label):
        assert witness.history == history
        return {"id": "fixture-worktree"}

    def capture(command, env, cwd, diagnostics, label, timeout):
        calls.append(command)
        assert env is witness.env and cwd == tmp_path and timeout == 5
        return reply

    witness.records = records
    monkeypatch.setattr(installed, "_run", capture)
    result = witness.turn(PROMPTS[0], 1, SimpleNamespace(output=b"cursor redraw"))
    assert result == (("native-pid", "native-birth") if reply == "LIFECYCLE_REPLY_1" else None)
    assert calls == [[
        tmp_path / "mux" / "psmux.exe", "capture-pane", "-p",
        "-t", "wt-fixture-worktree", "-S", "-60",
    ]]


def test_lifecycle_diagnostic_write_cannot_mask_unsettled_custody(tmp_path):
    from plugin_test_containment import UnreapedProcessError

    original = UnreapedProcessError("fixture-owned process remains unobserved")

    def failed_report(*_):
        raise OSError("controlled diagnostic write failure")

    with pytest.raises(UnreapedProcessError) as caught:
        with _capture_failure(tmp_path, failed_report):
            raise original
    assert caught.value is original
    assert any("controlled diagnostic write failure" in note for note in original.__notes__)


def test_lifecycle_environment_drops_foreign_identity_mux_and_inference_credentials(
    tmp_path, monkeypatch
):
    inherited = {
        "USERPROFILE": str(tmp_path / "h"),
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "1",
        "SESSION_ID": "foreign-session",
        "WORKTREE_ID": "foreign-worktree",
        "WORKTREE_DIR": "foreign-directory",
        "COPILOT_CLI_PID": "123",
        "PSMUX_DATA_DIR": "foreign-mux-directory",
        "PSMUX_PANE": "foreign-pane",
        "TMUX": "foreign-mux",
        "OPENAI_API_KEY": "synthetic-credential",
        "AGENT_WORKTREES_LAUNCH_ID": "foreign-launch",
        "AGENT_WORKTREES_LAUNCH_TRACE": "foreign-trace",
        "AGENT_WORKTREES_PICKER_FRAME_HEALTH": "foreign-health",
        "AGENT_WORKTREES_BINSTUB_STARTED": "foreign-start",
    }
    monkeypatch.setattr(installed, "_environment", lambda _: dict(inherited))
    env = _case_environment(tmp_path)
    assert not set(env) & {
        "SESSION_ID",
        "WORKTREE_ID",
        "WORKTREE_DIR",
        "COPILOT_CLI_PID",
        "PSMUX_PANE",
        "TMUX",
        "OPENAI_API_KEY",
        "AGENT_WORKTREES_PICKER_FRAME_HEALTH",
        "AGENT_WORKTREES_BINSTUB_STARTED",
    }
    assert env["COPILOT_EXTENSIONS_TEST_CONTAINED"] == "1"
    assert Path(env["PSMUX_DATA_DIR"]).is_relative_to(tmp_path)
    assert Path(env["COPILOT_HOME"]).is_relative_to(tmp_path)
    assert Path(env["AGENT_WORKTREES_LAUNCH_TRACE"]).is_relative_to(tmp_path)
    assert env["AGENT_WORKTREES_LAUNCH_ID"] != inherited["AGENT_WORKTREES_LAUNCH_ID"]
    assert inherited["PSMUX_DATA_DIR"] == "foreign-mux-directory"


def test_lifecycle_manager_readiness_requires_the_current_project_binding():
    bound = b"Worktree Manager | host tty-fixture | tty-harness\nWORKTREES"
    loading = b"Worktree Manager | host pending\nWORKTREES"
    alternate = b"\x1b[?1049h"
    assert _manager_ready(alternate + bound)
    assert not _manager_ready(alternate + bound + loading)
    assert _manager_ready(alternate + loading + bound)


def test_lifecycle_terminal_diagnostics_retain_the_recent_refusal(tmp_path):
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    (diagnostics / "lifecycle-0.terminal.txt").write_text(
        "error: old mapping warning\n" * 300
        + "Refusal: actual launch did not begin\n"
        + "redraw payload\n" * 300,
        encoding="utf-8",
    )
    records = []
    with pytest.raises(RuntimeError, match="native failure"):
        with _capture_failure(tmp_path, lambda name, value: records.append(value)):
            raise RuntimeError("native failure")
    text = json.loads(records[0])["lifecycle-0.terminal.txt"]["text"]
    assert "Refusal: actual launch did not begin" in text
    assert len(text.encode("utf-8")) <= 8192


@pytest.mark.parametrize("observation", ["empty", "live", "unconfirmed"])
def test_lifecycle_mux_venue_refuses_existing_or_unconfirmed_servers(monkeypatch, observation):
    from agent_remote_driver import windows_mux_processes
    from agent_remote_driver.mux_observation_types import ObservationUnavailable

    def observe():
        if observation == "unconfirmed":
            raise ObservationUnavailable("controlled incomplete census")
        return {123: "controlled birth"} if observation == "live" else {}

    monkeypatch.setattr(windows_mux_processes, "servers", observe)
    if observation == "empty":
        _require_mux_venue()
    else:
        with pytest.raises(RuntimeError, match="disposable Windows runner"):
            _require_mux_venue()


@pytest.mark.skipif(os.name != "nt", reason="Native owned process metadata")
@pytest.mark.timeout(30)
def test_lifecycle_process_observation_uses_owned_handles_and_omits_inline_code(tmp_path):
    from agent_procutil.owned_budget import OwnedExecutionBudget

    (tmp_path / "diagnostics").mkdir()
    env = _case_environment(tmp_path)
    budget = OwnedExecutionBudget.from_timeout(20, cleanup_reserve=5)
    with installed._owned_console(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            "import time;print('READY',flush=True);time.sleep(30)",
        ],
        env=env,
        cwd=tmp_path,
    ) as console:
        witness = _Witness(
            tmp_path,
            env,
            Path(sys.executable),
            Path(sys.executable),
            Path(sys.executable),
            budget,
        )
        witness.consoles.append(console)
        witness.wait(lambda: b"READY" in console.output, "metadata probe startup", timeout=10)
        outside = console.pin(os.getpid())
        try:
            assert not _job_member(console, outside)
        finally:
            console._close(outside)
        witness._capture_processes()
        text = (tmp_path / "diagnostics" / "lifecycle-processes.jsonl").read_text(
            encoding="utf-8",
        )
        records = [json.loads(line) for line in text.splitlines()]
        rows = [row for row in records if "program" in row]
        assert any(
            row["pid"] == console.process.pid
            and row["birth"] == console.birth(console.process.process)
            for row in rows
        )
        assert "time.sleep" not in text
        assert all(row["module"] is None for row in rows)
        assert records[-1]["complete"] is False


@pytest.mark.skipif(os.name != "nt", reason="Native installed Windows Manager lifecycle")
@pytest.mark.timeout(900)
def test_installed_manager_creates_resumes_and_opens_one_real_cli(record_testsuite_property):
    from agent_procutil.daemon_instances import daemon_test_lifecycle
    from agent_procutil.owned_budget import OwnedExecutionBudget
    from plugin_test_command_safety import private_mux_args
    from plugin_test_containment import SANDBOX_ENV, TestSandbox
    from test_resume_pane_startup import _private_psmux

    copilot = Path(os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE", ""))
    psmux = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux.exe")
    assert copilot.is_absolute() and copilot.is_file(), "Select the real native Copilot executable"
    assert psmux and shutil.which("pwsh"), "Real PSMux and PowerShell 7 are required"
    budget = OwnedExecutionBudget.from_timeout(720, cleanup_reserve=30)
    _require_mux_venue()
    identity = _identity()
    previous_deadline = installed._WORK_DEADLINE
    installed._WORK_DEADLINE = budget.execution_deadline
    before_host = installed._host_state()
    try:
        with (
            TestSandbox(prefix="lc-", dir=Path(os.environ[SANDBOX_ENV])) as raw,
            _capture_failure(Path(raw), record_testsuite_property),
        ):
            root = Path(raw)
            namespace = private_mux_args(root)[1]
            env = _case_environment(root)
            _private_psmux(root / "mux", psmux, namespace, env)
            installed.prepare(root, identity)
            for component in ("manager", "engine"):
                installed.construct(root, identity, component)
            prepared = json.loads((root / "prepared.json").read_text(encoding="utf-8"))
            manager_origin, manager = installed._installed(
                Path(env["WORKTREE_MANAGER_ROOT"]),
                Path(prepared["payloads"]["worktree-manager"]),
                "worktree-manager",
                env,
                root / "diagnostics",
            )
            engine_origin, engine = installed._installed(
                Path(env["AGENT_WORKTREES_HOME"]),
                Path(prepared["payloads"]["agent-worktrees"]),
                "agent-worktrees",
                env,
                root / "diagnostics",
            )
            home = Path(env["USERPROFILE"])
            copilot_home = home / ".copilot"
            copilot_home.mkdir(exist_ok=True)
            (copilot_home / "config.json").write_text(
                json.dumps(
                    {
                        "trustedFolders": [str(root)],
                        "experimental": True,
                    }
                ),
                encoding="utf-8",
            )
            env.update(
                {
                    "COPILOT_HOME": str(copilot_home),
                    "COPILOT_OFFLINE": "true",
                    "COPILOT_PROVIDER_TYPE": "openai",
                    "COPILOT_PROVIDER_MODEL_ID": "gpt-4.1",
                    "COPILOT_MODEL": "gpt-4.1",
                    "NO_PROXY": "127.0.0.1,localhost",
                }
            )
            _project(root, env, engine, copilot)
            cleanup = []
            with (
                daemon_test_lifecycle(
                    owner="installed-manager-lifecycle",
                    registry_root=root / "daemon-instances",
                    deadline=budget.execution_deadline,
                    cleanup_results=cleanup,
                ) as lifecycle,
                _provider() as (port, requests),
            ):
                env.update(lifecycle)
                env["AGENT_WORKTREES_STATUS_MONITOR"] = "1"
                env["COPILOT_PROVIDER_BASE_URL"] = f"http://127.0.0.1:{port}"
                with _Witness(root, env, manager, engine, copilot, budget) as witness:
                    first = witness.manager()
                    first.send(b"\r")
                    witness.wait(
                        lambda: "Creates on tty-fixture Win" in installed._visible(first.output),
                        "The real New-worktree dialog did not open",
                    )
                    before = len(first.output)
                    first.send(b"\t")
                    witness.wait(
                        lambda: len(first.output) > before,
                        "The New dialog did not render the focus transition",
                    )
                    first.send(PROMPTS[0].encode())
                    witness.wait(
                        lambda: PROMPTS[0] in installed._visible(first.output[before:]),
                        "The New dialog did not render the entered prompt",
                    )
                    before = len(first.output)
                    first.send(b"\r")
                    witness.wait(
                        lambda: len(first.output) > before,
                        "The New dialog did not render accepting the prompt",
                    )
                    first.send(b"\r")
                    original = witness.turn(PROMPTS[0], 1, first)
                    record = witness.records("records-created")
                    actual = installed._run(
                        ["git", "-C", record["path"], "rev-parse", "--show-toplevel"],
                        env,
                        root,
                        root / "diagnostics",
                        "real-created-worktree",
                        timeout=30,
                    )
                    assert Path(actual).samefile(record["path"])

                    second = witness.manager()
                    witness.select(second, record, "Open")
                    second.send(PROMPTS[1].encode() + b"\r")
                    assert witness.turn(PROMPTS[1], 2, second) == original
                    if not first.exited(first.process.process):
                        first.send(b"\x02d")
                    assert first.wait(15000) == 0
                    assert witness.one_native() == original
                    second.send(PROMPTS[2].encode() + b"\r")
                    assert witness.turn(PROMPTS[2], 3, second) == original
                    assert witness.records("records-opened") == record
                    witness.close_cli(second, original)
                    assert not witness.one_native()

                    resumed = witness.manager()
                    witness.select(resumed, record, "Resume")
                    replacement = witness.wait(
                        witness.one_native, "Resume did not start a real CLI", timeout=60
                    )
                    assert replacement != original
                    resumed.send(PROMPTS[3].encode() + b"\r")
                    assert witness.turn(PROMPTS[3], 4, resumed) == replacement
                    assert witness.records("records-resumed") == record
                    assert [
                        event["data"]["content"]
                        for event in _events(witness.history, copilot_home)
                        if event["type"] == "user.message"
                    ] == list(PROMPTS)
                    assert any(
                        all(prompt in json.dumps(request) for prompt in PROMPTS)
                        for request in requests
                    )
                    witness.close_cli(resumed, replacement)
                    assert not witness.one_native()
                    conversation = witness.history.parent.name
                rejected = installed._negative_control(
                    manager,
                    lambda: installed._witness(
                        "cmd",
                        home / ".local" / "bin" / (PROJECT + ".cmd"),
                        env,
                        root / PROJECT,
                        root / "diagnostics",
                        manager,
                        engine,
                        control=True,
                    ),
                )
                assert rejected, "A successful install concealed an unusable Manager entry"
            assert all(result.status == "settled" for result in cleanup), cleanup
            record_testsuite_property(
                "installed-manager-lifecycle",
                json.dumps(
                    {
                        "identity": identity,
                        "profile": "installed-core",
                        "installation": {"manager": manager_origin, "engine": engine_origin},
                        "worktree": record["id"],
                        "conversation": conversation,
                        "native_before_open": original,
                        "native_after_resume": replacement,
                        "create": True,
                        "open_existing": True,
                        "detach_preserved": True,
                        "normal_exit": True,
                        "resume_continued": True,
                        "launch_refusal": True,
                        "elapsed_seconds": time.monotonic() - (budget.deadline - 720),
                    }
                ),
            )
        assert installed._host_state() == before_host
        assert time.monotonic() <= budget.deadline
    finally:
        installed._WORK_DEADLINE = previous_deadline
