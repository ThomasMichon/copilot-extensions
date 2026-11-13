"""Run the common broker and exact native child in the caller's existing pane."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from typing import TYPE_CHECKING, Callable

from agent_procutil import contained_test_mode, no_window_kwargs

from .backend import HeadedBackend
from .staging import stage_extension
from .receipt import PROCESS_RECEIPT
from ..native_arguments import VALUE_OPTIONS
from ..session_host.host import SessionHost
from ..session_host.launcher import (
    _ACQUISITION_ENV, _NONCE_ENV, _STARTUP_ENV,
    _require_receiving_policy, _resolve_child_exe, _write_host_state,
)
from ..session_host.osutil import process_start_time
from ..session_host.startup_admission import StartupAdmissionClient
from ..session_host import protocol

if TYPE_CHECKING:
    from .native_handoff import WindowsNativeHandoff

BINDING_ENV = "COPILOT_DRIVER_HEADED_BINDING"
CLIENT_ENV = "COPILOT_DRIVER_HEADED_CLIENT"


def prepare_headed_command(
    child_argv: list[str], *, session_id: str, attempt_directory: str | os.PathLike[str],
    state_file: str | os.PathLike[str], cwd: str,
) -> list[str]:
    """Retain the executing core under the caller's original attempt, not its venv."""
    _native_request(child_argv, session_id)
    if not all(Path(path).is_absolute() for path in (attempt_directory, state_file, cwd)):
        raise ValueError("Headed execution requires absolute caller-owned paths")
    from agent_procutil._interpreter import physical_executable
    from ..session_host.bundle import build_session_host_bundle

    interpreter = physical_executable(getattr(sys, "_base_executable", sys.executable))
    if interpreter is None:
        raise RuntimeError("Independent headed execution requires a physical base interpreter")
    archive, _ = build_session_host_bundle(Path(attempt_directory) / "runtime")
    executable = [interpreter, "-I", "-S", str(archive)]
    selected = subprocess.run(
        [*executable, "--support-json"], capture_output=True, text=True, timeout=15,
        **no_window_kwargs(),
    )
    try:
        support = json.loads(selected.stdout)
        if (
            selected.returncode != 0
            or support.get("contract") != "agent-remote-driver.session-host"
            or type(support.get("version")) is not int or support["version"] != 1
            or type(support.get("protocol_version")) is not int
            or support["protocol_version"] != protocol.PROTOCOL_VERSION
            or any(
                type(support.get(key)) is not list
                or not all(type(item) is str for item in support[key])
                or required not in support[key]
                for key, required in (
                    ("modes", "headed"), ("transports", "tcp-frame"), ("lifetime_policies", "cli"),
                    ("startup_admission", "live-owner-v1"),
                    ("headed_process_receipts", PROCESS_RECEIPT),
                )
            )
        ):
            raise ValueError("unsupported selected headed core")
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise RuntimeError("Selected core does not support headed execution") from error
    return [
        *executable, "--mode", "headed", "--session-id", session_id,
        "--attempt-directory", str(attempt_directory), "--state-file", str(state_file),
        "--cwd", cwd, "--", *child_argv,
    ]


def _native_request(argv: list[str], session_id: str) -> str | None:
    if not argv or not isinstance(session_id, str) or not 1 <= len(session_id) <= 256:
        raise ValueError("Headed execution requires the caller's selected native session")
    identities, prompts = [], []
    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument == "--":
            break
        name, separator, value = argument.partition("=")
        if name in {"--acp", "--stdio", "-p", "--prompt", "--ahp", "--connect", "--continue"}:
            raise ValueError("Headed execution requires interactive native arguments")
        if name in {"--session-id", "--resume", "-i", "--interactive"}:
            if not separator:
                index += 1
                if index >= len(argv):
                    raise ValueError("Missing native session or interactive prompt argument")
                value = argv[index]
            (identities if name in {"--session-id", "--resume"} else prompts).append(value)
        elif not separator and (name in VALUE_OPTIONS or name == "-C"):
            index += 1
            if index >= len(argv):
                raise ValueError(f"Missing native value for {name}")
        index += 1
    if identities != [session_id] or len(prompts) > 1:
        raise ValueError("Headed execution requires one matching native session and at most one seed")
    return prompts[0] if prompts else None


def _extension_command(argv: list[str], plugin: Path) -> list[str]:
    index = 1
    experimental = False
    while index < len(argv):
        argument = argv[index]
        if argument == "--":
            break
        experimental = experimental or argument == "--experimental"
        index += 2 if argument in VALUE_OPTIONS | {"-C", "--resume"} else 1
    additions = ["--plugin-dir", str(plugin)]
    if not experimental:
        additions.append("--experimental")
    return [*argv[:index], *additions, *argv[index:]]


class _PaneChild:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.started = asyncio.Event()
        self.job = None
        self._cleanup_task: asyncio.Task | None = None

    def launch(self, argv: list[str], *, native_handoff=None, **kwargs) -> None:
        if self.proc is not None:
            raise RuntimeError("A native incarnation cannot be launched twice")
        if native_handoff is None:
            # headless-guard: allow: the interactive child inherits the caller's existing pane console
            self.proc = subprocess.Popen(argv, **kwargs)
        else:
            self.proc, self.job = native_handoff.launch(argv, **kwargs)
        self.started.set()

    def _settle_handoff(self) -> None:
        from agent_procutil.owned_process import OwnedCleanupError

        try:
            self.job.terminate_and_wait(5)
            self.proc.wait(timeout=5)
        except BaseException as error:
            raise OwnedCleanupError(self.proc, self.job) from error
        self.job = None

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc is not None else None

    @property
    def returncode(self) -> int | None:
        return self.proc.poll() if self.proc is not None else None

    async def wait(self) -> int:
        await self.started.wait()
        while (result := self.returncode) is None:
            await asyncio.sleep(0.05)
        if self.job is not None or self._cleanup_task is not None:
            if self._cleanup_task is None:
                self._cleanup_task = asyncio.create_task(asyncio.to_thread(self._settle_handoff))
            await asyncio.shield(self._cleanup_task)
        return result

    async def terminate(self) -> None:
        if self.proc is not None and self.returncode is None:
            # The retained Popen owns this exact child; never re-resolve a PID or mux name.
            self.proc.kill()
        await self.wait()


def _interactive_console() -> bool:
    if sys.stdin is None or not sys.stdin.isatty():
        return False
    if os.name == "nt":
        import ctypes

        return bool(ctypes.windll.kernel32.GetConsoleCP())
    return True


async def run_headed(
    child_argv: list[str], *, session_id: str, attempt_directory: str | os.PathLike[str],
    state_file: str | os.PathLike[str], cwd: str | None = None,
    env: dict[str, str] | None = None, port: int = 0, ws_port: int | None = None,
    nonce: str = "", policy_context: str | None = None,
    before_exec: Callable[[], None] | None = None,
    auth_wait: dict | None = None,
    native_handoff: WindowsNativeHandoff | None = None,
) -> int:
    """Run legacy headed execution, or an explicitly source-authorized receiver."""
    if native_handoff is not None:
        from .native_handoff import WindowsNativeHandoff

        if (
            type(native_handoff) is not WindowsNativeHandoff
            or os.name != "nt" or auth_wait is not None
        ):
            raise ValueError("Native handoff requires the Windows non-auth headed creation owner")
    arguments = dict(
        session_id=session_id, attempt_directory=attempt_directory, state_file=state_file,
        cwd=cwd, env=env, port=port, ws_port=ws_port, nonce=nonce,
        policy_context=policy_context, before_exec=before_exec,
        native_handoff=native_handoff,
    )
    if auth_wait is None:
        return await _run_headed_once(child_argv, **arguments)
    from .auth_child import require_auth_custody
    from .auth_wait import AuthWaitReceiver

    require_auth_custody()
    _native_request(child_argv, session_id)
    resumed_argv = _seedless_resume(child_argv, session_id)
    receiver = AuthWaitReceiver(session_id, auth_wait)
    await receiver.serve()
    try:
        argv = child_argv
        while True:
            result = await _run_headed_once(argv, **arguments, auth_receiver=receiver)
            if not receiver.stopped_for_auth:
                return result
            await receiver.next_grant()
            # A cold resume is a new process, never a repetition of the launch seed.
            argv = resumed_argv
            arguments["auth_resume"] = True
            arguments["before_exec"] = None
            arguments["env"] = {
                key: value for key, value in (env or {}).items()
                if key not in {_STARTUP_ENV, _ACQUISITION_ENV}
            }
    finally:
        await receiver.close()


def _seedless_resume(argv: list[str], session_id: str) -> list[str]:
    result = [argv[0]]
    index = 1
    while index < len(argv):
        argument = argv[index]
        name, separator, _ = argument.partition("=")
        if name == "--":
            # Positional launch text is not silently carried across recovery.
            if index != len(argv) - 1:
                raise ValueError("Auth recovery cannot replay positional launch input")
            break
        if name in {"--session-id", "--resume", "-i", "--interactive"}:
            if not separator:
                index += 1
        else:
            if not name.startswith("-") or name.startswith("-i") and not name.startswith("--"):
                raise ValueError("Auth recovery cannot replay positional launch input")
            result.append(argument)
            if not separator and (name in VALUE_OPTIONS or name == "-C"):
                index += 1
                result.append(argv[index])
        index += 1
    result.extend(("--resume", session_id))
    _native_request(result, session_id)
    return result


async def _run_headed_once(
    child_argv: list[str], *, session_id: str, attempt_directory: str | os.PathLike[str],
    state_file: str | os.PathLike[str], cwd: str | None = None,
    env: dict[str, str] | None = None, port: int = 0, ws_port: int | None = None,
    nonce: str = "", policy_context: str | None = None,
    before_exec: Callable[[], None] | None = None, auth_receiver=None,
    auth_resume: bool = False,
    native_handoff: WindowsNativeHandoff | None = None,
) -> int:
    startup_prompt = _native_request(child_argv, session_id)
    cwd = str(Path(cwd or os.getcwd()).resolve())
    if not 0 <= port <= 65535 or ws_port is not None and not 0 <= ws_port <= 65535:
        raise ValueError("Headed execution requires valid loopback ports")
    _require_receiving_policy(cwd, policy_context)
    if ws_port is not None:
        from ..session_host.ws_transport import WebSocketTransport
    if auth_receiver is None:
        owner = _PaneChild()
    else:
        from .auth_child import AuthPaneChild

        owner = AuthPaneChild()
    backend = HeadedBackend(
        owner, session_id=session_id, nonce=secrets.token_hex(32), startup_prompt=startup_prompt,
        auth_wait=auth_receiver,
    )
    nonce = nonce or (env or {}).get(_NONCE_ENV) or os.environ.get(_NONCE_ENV) or secrets.token_hex(32)
    plugin = stage_extension(Path(attempt_directory))
    child_env = {**os.environ, **(env or {})}
    startup_context = None if auth_resume else child_env.get(_STARTUP_ENV)
    if not auth_resume and child_env.get(_ACQUISITION_ENV) is not None and before_exec is None:
        raise ValueError("Original launch context requires its receiving owner")
    startup = StartupAdmissionClient(startup_context) if startup_context is not None else None
    for key in ("COPILOT_PLUGIN_ROOT", _NONCE_ENV, _ACQUISITION_ENV, _STARTUP_ENV):
        child_env.pop(key, None)
    host, websocket = None, None
    tasks: list[asyncio.Task] = []
    state: dict = {}
    expiry_task = None

    def publish(status: str) -> None:
        state["state"] = status
        if auth_receiver is not None:
            state["auth_wait"] = auth_receiver.declaration()
        _write_host_state(Path(state_file), state)

    async def expire() -> None:
        await auth_receiver.expiry()
        publish("auth_stopping")
        await backend.freeze_auth()
        await backend.retire_auth()
        publish("auth_wait" if auth_receiver.hold is None else "auth_held")

    try:
        await backend.serve()
        child_env[BINDING_ENV] = json.dumps(backend.declaration())
        child_env[CLIENT_ENV] = json.dumps({
            "contract": "copilot-driver.headed-client", "version": 1,
            "sessionId": session_id, "cwd": cwd, "stateFile": str(Path(state_file).absolute()),
            "nonce": nonce, "hostPid": os.getpid(),
            "client": str(plugin / "extensions" / "copilot-driver-headed" / "host-client.mjs"),
        })
        argv = _extension_command(_resolve_child_exe(child_argv, child_env.get("PATH")), plugin)
        _require_receiving_policy(cwd, policy_context)
        if startup is not None:
            startup.check(child_argv, cwd, session_id, {**os.environ, **(env or {})})
        if before_exec is not None:
            before_exec()
        if os.name == "nt" and not contained_test_mode() and not (
            auth_receiver is not None and auth_resume
        ):
            from ..winjob import setup_kill_on_close_job

            if not setup_kill_on_close_job(allow_breakaway=False):
                raise RuntimeError("The headed coordinator could not contain its native child")
        # headless-guard: allow: the interactive child inherits the caller's existing pane console
        native_kwargs = dict(
            cwd=cwd, env=child_env, **({} if _interactive_console() else no_window_kwargs()),
        )
        if auth_receiver is None:
            owner.launch(argv, native_handoff=native_handoff, **native_kwargs)
        else:
            if not auth_receiver.authorized():
                raise RuntimeError("Source authorization expired before native launch")
            owner.launch(argv, **native_kwargs)
            expiry_task = asyncio.create_task(expire())
            tasks.append(expiry_task)
        if startup is not None:
            startup.close()
            startup = None
        state.update({
            "version": 2, "mode": "headed", "session_id": session_id,
            "cwd": cwd, "headed_client": ["native-send-v1"],
            "process_receipt": PROCESS_RECEIPT,
            "pid": os.getpid(), "host_pid": os.getpid(), "child_pid": owner.pid,
            "host_process_start_time": process_start_time(os.getpid()),
            "child_process_start_time": process_start_time(owner.pid),
            "protocol_version": protocol.PROTOCOL_VERSION, "nonce": nonce,
            "lifetime_policy": "cli", "automatic_idle_retirement": "unavailable",
            "extension_assets": str(plugin), "restoration": "unqualified",
        })
        publish("native_starting")
        native_exit = asyncio.create_task(owner.wait())
        binding = asyncio.create_task(backend.bound.wait())
        tasks.extend((native_exit, binding))
        done, _ = await asyncio.wait(
            (native_exit, binding, *([expiry_task] if expiry_task is not None else [])),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if expiry_task is not None and expiry_task in done:
            await expiry_task
            return await native_exit
        if native_exit in done:
            if auth_receiver is not None and auth_receiver.stopped_for_auth:
                await expiry_task
            state["child_exit_code"] = native_exit.result()
            if auth_receiver is None or not auth_receiver.stopped_for_auth:
                publish("child_exited")
            return native_exit.result()
        host = SessionHost(backend, nonce=nonce, lifetime_policy="cli")
        state["port"] = await host.serve(port=port)
        state["extension_pid"] = backend.extension_pid
        if ws_port is not None:
            websocket = WebSocketTransport(state["port"], nonce)
            await websocket.serve(ws_port)
            state["websocket"] = {
                "contract": "acp.v1", "url": f"ws://127.0.0.1:{websocket.port}/acp",
                "nonce": websocket.nonce,
            }
        if auth_receiver is not None and auth_receiver.authorized():
            auth_receiver.phase = "running"
        publish("control_unavailable" if backend.lost.is_set() else "running")
        lost = asyncio.create_task(backend.lost.wait())
        tasks.append(lost)
        done, _ = await asyncio.wait(
            (native_exit, lost, *([expiry_task] if expiry_task is not None else [])),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if expiry_task is not None and expiry_task in done:
            await expiry_task
        if not native_exit.done() and (
            auth_receiver is None or not auth_receiver.stopped_for_auth
        ):
            publish("control_unavailable")
        if expiry_task is not None and not native_exit.done():
            done, _ = await asyncio.wait(
                (native_exit, expiry_task), return_when=asyncio.FIRST_COMPLETED,
            )
            if expiry_task in done:
                await expiry_task
        result = await native_exit
        if auth_receiver is not None and auth_receiver.stopped_for_auth:
            await expiry_task
        state["child_exit_code"] = result
        if auth_receiver is None or not auth_receiver.stopped_for_auth:
            publish("child_exited")
        return result
    except Exception:
        if owner.proc is None:
            raise
        if auth_receiver is not None:
            auth_receiver.preserve_hold("coordinator-unavailable")
            await owner.terminate()
            auth_receiver.stopped(None)
            publish("auth_held")
            return await owner.wait()
        # A broker/bootstrap failure after native creation is not authority to
        # retire a user's interactive session or retry its original seed.
        state["control_error"] = "coordinator-unavailable"
        try:
            publish("control_unavailable")
        except OSError:
            pass
        return await owner.wait()
    finally:
        try:
            if auth_receiver is not None and owner.proc is not None:
                # Cancellation must not leave the native scope alive during transport drainage.
                await owner.terminate()
        finally:
            if startup is not None:
                startup.close()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                if websocket is not None:
                    await websocket.close(drain=True)
            finally:
                try:
                    if host is not None:
                        await host.close()
                finally:
                    try:
                        await backend.close()
                    finally:
                        if owner.proc is not None and (
                            native_handoff is not None
                            or auth_receiver is not None or owner.returncode is None
                        ):
                            await owner.terminate()
