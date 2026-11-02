"""Hosted-only native jump-chain visibility and retirement witness."""

from __future__ import annotations

import asyncio
import base64
import ctypes
import json
import os
import platform
import subprocess
import sys
import sysconfig
import time
from ctypes import wintypes
from dataclasses import replace
from pathlib import Path


def require_hosted_windows() -> None:
    if not (
        os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("RUNNER_ARCH", "").casefold() == "arm64"
    ):
        raise RuntimeError("Native jump qualification requires a GitHub-hosted Windows ARM64 runner")


def profile_text(proxy_command: str) -> str:
    return (
        "Host jump-target.invalid\n"
        "  HostName target.invalid\n"
        "  ProxyJump jump-first.invalid,jump-last.invalid\n"
        "Host jump-first.invalid\n"
        f"  ProxyCommand {proxy_command}\n"
        "Host jump-last.invalid\n"
        "  HostName last.invalid\n"
        "Host *\n"
        "  User probe\n"
        "  BatchMode yes\n"
        "  IdentityFile none\n"
        "  IdentityAgent none\n"
        "  UserKnownHostsFile NUL\n"
        "  StrictHostKeyChecking yes\n"
    )


class NativeCensus:
    def __init__(self) -> None:
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
        ]
        self.kernel.QueryInformationJobObject.restype = wintypes.BOOL
        self.kernel.IsProcessInJob.argtypes = [
            wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
        ]
        self.kernel.IsProcessInJob.restype = wintypes.BOOL
        self.handles: dict[int, int] = {}

    def pin(self, jobs: list) -> None:
        class Members(ctypes.Structure):
            _fields_ = [
                ("assigned", wintypes.DWORD), ("count", wintypes.DWORD),
                ("pids", ctypes.c_size_t * 64),
            ]

        for job in jobs:
            members = Members()
            if not self.kernel.QueryInformationJobObject(
                int(job.handle), 3, ctypes.byref(members), ctypes.sizeof(members), None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if members.count > 64:
                raise RuntimeError("Native fixture Job census exceeded its budget")
            for pid in members.pids[:members.count]:
                if pid in self.handles:
                    continue
                handle = self.kernel.OpenProcess(0x100000 | 0x1000, False, pid)
                if not handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                member = wintypes.BOOL()
                if not self.kernel.IsProcessInJob(handle, int(job.handle), ctypes.byref(member)):
                    self.kernel.CloseHandle(handle)
                    raise ctypes.WinError(ctypes.get_last_error())
                if not member.value:
                    self.kernel.CloseHandle(handle)
                    raise RuntimeError("Native fixture identity left its owning Job")
                self.handles[pid] = handle

    def retired(self) -> bool:
        return all(
            self.kernel.WaitForSingleObject(handle, 3000) == 0
            for handle in self.handles.values()
        )

    def close(self) -> None:
        for handle in self.handles.values():
            self.kernel.CloseHandle(handle)
        self.handles.clear()


def serve_proxy(marker: Path) -> None:
    require_hosted_windows()
    from windows_proxy_probe import serve_proxy as observe_console

    observe_console(marker)
    time.sleep(60)


def window_identity(window: int) -> dict:
    require_hosted_windows()
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(window, ctypes.byref(pid))
    name = ctypes.create_unicode_buffer(256)
    user.GetClassNameW(window, name, len(name))
    evidence = {"window": window, "pid": pid.value, "class": name.value}
    handle = kernel.OpenProcess(0x1000, False, pid.value) if pid.value else None
    if not handle:
        return {**evidence, "image_query_error": ctypes.get_last_error()}
    try:
        image = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(image))
        if not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
            return {**evidence, "image_query_error": ctypes.get_last_error()}
        return {**evidence, "image": Path(image.value).name}
    finally:
        kernel.CloseHandle(handle)


def observe_canary(console_python: Path) -> dict:
    require_hosted_windows()
    from agent_procutil import spawn_sync_in_kill_on_close_job
    from windows_proxy_probe import terminal_windows

    baseline = set(terminal_windows())
    process, job = spawn_sync_in_kill_on_close_job(
        [str(console_python), "-c", "import sys; sys.stdin.buffer.read(1)"],
        require_job=True, allow_breakaway=False, stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        # headless-guard: allow hosted-only visible observer sensitivity control.
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    if job is None:
        process.kill()
        process.wait(timeout=5)
        raise RuntimeError("Observer canary has no native Job custody")
    try:
        deadline = time.monotonic() + 5
        visible: dict[int, dict] = {}
        while not visible:
            for window in set(terminal_windows()) - baseline:
                identity = window_identity(window)
                if identity["pid"] == process.pid:
                    visible[window] = identity
            if visible:
                break
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("Visible-window observer did not detect its owned canary")
            time.sleep(0.01)
        evidence = {
            "process": process.pid,
            "windows": [visible[window] for window in sorted(visible)],
        }
    finally:
        try:
            if process.stdin is not None:
                process.stdin.write(b"\n")
                process.stdin.close()
            process.wait(timeout=5)
        finally:
            job.terminate_and_wait(5)
            job.close()
    deadline = time.monotonic() + 5
    while set(terminal_windows()) & visible.keys():
        if time.monotonic() >= deadline:
            raise RuntimeError("Owned observer canary window did not retire")
        time.sleep(0.01)
    return evidence


async def verify_cached_profile_refresh(root: Path, client: Path) -> dict:
    require_hosted_windows()
    from ssh_manager.config_sources import SSHProfileSource, ssh_option
    from ssh_manager.proxy_jump import prepare_windows_launch

    profile = root / "cached-profile.config"
    if profile.exists():
        raise RuntimeError("Native cache control will not replace an existing profile")
    common = (
        "  User probe\n  BatchMode yes\n  CanonicalizeHostname no\n"
        "  IdentityFile none\n  IdentityAgent none\n"
        "  UserKnownHostsFile NUL\n  StrictHostKeyChecking yes\n"
    )
    profile.write_text(
        "Host cached-target.invalid\n  HostName cached-old.invalid\n" + common,
        encoding="utf-8",
    )
    source = SSHProfileSource(
        "cached-target.invalid", config_file=str(profile),
        ssh_executable=str(client), resolve_timeout=15,
    )
    cached = await asyncio.to_thread(source.get_ssh_config)
    profile.write_text(
        "Host cached-target.invalid\n  HostName cached-new.invalid\n"
        "  ProxyJump cache-first.invalid,cache-last.invalid\n" + common,
        encoding="utf-8",
    )
    arguments = (str(client), "-F", str(profile), "cached-target.invalid")
    _args, refreshed = await prepare_windows_launch(arguments, cached, str(client))
    command = "controlled-programmatic-proxy --stdio"
    explicit = replace(cached, proxy_command=command)
    _args, programmatic = await prepare_windows_launch(arguments, explicit, str(client))
    _args, extra = await prepare_windows_launch(
        arguments, replace(cached, extra_options={"ProxyCommand": command}), str(client),
    )
    cli_args, cli = await prepare_windows_launch(
        (*arguments[:-1], "-oProxyCommand=none", arguments[-1]), explicit, str(client),
    )
    evidence = {
        "same_file": refreshed.config_file == cached.config_file == str(profile),
        "source_cache_unchanged": source.get_ssh_config() is cached,
        "cached_hostname": ssh_option(cached, "hostname"),
        "refreshed_hostname": ssh_option(refreshed, "hostname"),
        "refreshed_jump": ssh_option(refreshed, "proxyjump"),
        "programmatic_proxy_preserved": programmatic.proxy_command == command,
        "programmatic_proxy_option_preserved": extra.proxy_command == command,
        "cli_proxy_override_preserved": (
            "-oProxyCommand=none" in cli_args
            and cli.proxy_command in (None, "none")
            and ssh_option(cli, "proxycommand") in (None, "none")
            and ssh_option(cli, "proxyjump") in (None, "none")
        ),
    }
    if not (
        evidence["same_file"] and evidence["source_cache_unchanged"]
        and evidence["cached_hostname"] == "cached-old.invalid"
        and evidence["refreshed_hostname"] == "cached-new.invalid"
        and evidence["refreshed_jump"] == "cache-first.invalid,cache-last.invalid"
        and evidence["programmatic_proxy_preserved"]
        and evidence["programmatic_proxy_option_preserved"]
        and evidence["cli_proxy_override_preserved"]
    ):
        raise RuntimeError(
            f"Native cached-profile refresh failed: {evidence}; "
            f"CLI proxy={cli.proxy_command!r}/{ssh_option(cli, 'proxycommand')!r}, "
            f"jump={ssh_option(cli, 'proxyjump')!r}",
        )
    return evidence


async def drive(root: Path) -> dict:
    require_hosted_windows()
    from agent_ssh.probe import _probe_windows
    from windows_proxy_probe import DESCRIPTOR_STATE, console_window, terminal_windows

    from ssh_manager import proxy
    from ssh_manager.config_sources import SSHProfileSource
    from ssh_manager.process import terminate_ssh_process_tree

    client = Path(os.environ["SSH_MANAGER_SELECTED_CLIENT"]).resolve(strict=True)
    console_python = Path(sys._base_executable).with_name("python.exe")
    if not console_python.is_file():
        raise RuntimeError("The selected Python has no console-subsystem peer")
    canary = await asyncio.to_thread(observe_canary, console_python)
    await asyncio.sleep(0.1)
    home = Path(os.environ["USERPROFILE"])
    config_file = home / ".ssh" / "config"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    if config_file.exists():
        raise RuntimeError("Native fixture will not replace an existing SSH profile")
    previous_path = os.environ.get("PATH", "")
    previous_state = os.environ.get(DESCRIPTOR_STATE)
    original_create = proxy._create_prepared_ssh
    original_spawn = proxy._ProxyBroker._spawn_child
    original_track = proxy._track_owner_job
    original_resolve = SSHProfileSource._resolve_effective_config
    processes: dict[int, object] = {}
    jobs: list = []
    missing_jobs = []
    ssh_processes: set[int] = set()
    query_seconds: list[float] = []

    def record_profile(self):
        started = time.monotonic()
        try:
            return original_resolve(self)
        finally:
            query_seconds.append(time.monotonic() - started)

    async def record_create(*args, **kwargs):
        process = await original_create(*args, **kwargs)
        processes[process.pid] = process
        ssh_processes.add(process.pid)
        return process

    async def record_spawn(self):
        process, job = await original_spawn(self)
        processes[process.pid] = process
        if self._ssh_route is None:
            if job is None:
                missing_jobs.append(process.pid)
            else:
                jobs.append(job)
        return process, job

    def record_track(process, job):
        processes[process.pid] = process
        if job is None:
            missing_jobs.append(process.pid)
        else:
            jobs.append(job)
        original_track(process, job)

    baseline = terminal_windows()
    observed: dict[int, bool] = {}
    window_events: dict[int, dict] = {}
    foreground_changed = False
    stop = asyncio.Event()
    observation_started = time.monotonic()
    phase = "before-cycles"

    async def monitor():
        nonlocal foreground_changed
        while not stop.is_set():
            for window, foreground in terminal_windows().items():
                if window not in baseline:
                    observed[window] = observed.get(window, False) or foreground
                    if window not in window_events:
                        window_events[window] = {
                            **window_identity(window), "first_phase": phase,
                            "first_seconds": round(time.monotonic() - observation_started, 6),
                        }
                    window_events[window].update({
                        "last_phase": phase,
                        "last_seconds": round(time.monotonic() - observation_started, 6),
                    })
                if foreground and not baseline.get(window, False):
                    foreground_changed = True
            await asyncio.sleep(0.01)

    watcher = asyncio.create_task(monitor())
    cycles = []
    try:
        os.environ["PATH"] = str(client.parent) + os.pathsep + previous_path
        os.environ[DESCRIPTOR_STATE] = base64.b64encode(b"\x00\x00\x00\x00\x01\x01\x01\x00").decode()
        proxy._create_prepared_ssh = record_create
        proxy._ProxyBroker._spawn_child = record_spawn
        proxy._track_owner_job = record_track
        SSHProfileSource._resolve_effective_config = record_profile
        refresh_control = await verify_cached_profile_refresh(root, client)
        refresh_control["metadata_queries"] = len(query_seconds)
        for index, mode in enumerate(("timeout", "cancel")):
            phase = f"{mode}-launch"
            processes.clear()
            ssh_processes.clear()
            jobs.clear()
            missing_jobs.clear()
            query_seconds.clear()
            version_queries = proxy._client_shell.cache_info().misses
            cycle_started = time.monotonic()
            marker = root / f"jump-{index}.json"
            command = subprocess.list2cmdline([
                console_python.as_posix(), Path(__file__).resolve().as_posix(),
                "--proxy", marker.as_posix(),
            ])
            config_file.write_text(profile_text(command), encoding="utf-8")
            census = NativeCensus()
            task = asyncio.create_task(_probe_windows(
                "jump-target.invalid", 8 if index == 0 else 15, config_file=str(config_file),
            ))
            try:
                async def marker_ready():
                    while not marker.is_file():
                        if task.done():
                            raise RuntimeError(f"Native probe ended before proxy readiness: {task.result()}")
                        await asyncio.sleep(0.01)

                await asyncio.wait_for(marker_ready(), 10)
                if missing_jobs or len(ssh_processes) != 3:
                    raise RuntimeError("The full native SSH jump chain was not owned")
                census.pin(jobs)
                phase = f"{mode}-ready"
                if len(census.handles) < 7:
                    raise RuntimeError("Native census did not include SSH and proxy-client descendants")
                if mode == "cancel":
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    else:
                        raise RuntimeError("Native probe cancellation was not preserved")
                elif await task is not False:
                    raise RuntimeError("Synthetic unresponsive transport did not reach its deadline")
                if not await asyncio.to_thread(census.retired):
                    raise RuntimeError("Native probe cleanup left a pinned process alive")
                evidence = json.loads(marker.read_text(encoding="utf-8"))
                cycles.append({
                    "kind": mode, "ssh_processes": len(ssh_processes),
                    "pinned_processes": len(census.handles), "cleanup_confirmed": True,
                    "pinned_process_ids": sorted(census.handles),
                    "metadata_queries": len(query_seconds),
                    "metadata_seconds": round(sum(query_seconds), 6),
                    "version_queries": proxy._client_shell.cache_info().misses - version_queries,
                    "elapsed_seconds": round(time.monotonic() - cycle_started, 6),
                    **evidence,
                })
            finally:
                phase = f"{mode}-cleanup"
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                for process in processes.values():
                    await terminate_ssh_process_tree(process)
                census.close()
    finally:
        proxy._create_prepared_ssh = original_create
        proxy._ProxyBroker._spawn_child = original_spawn
        proxy._track_owner_job = original_track
        SSHProfileSource._resolve_effective_config = original_resolve
        os.environ["PATH"] = previous_path
        if previous_state is None:
            os.environ.pop(DESCRIPTOR_STATE, None)
        else:
            os.environ[DESCRIPTOR_STATE] = previous_state
        stop.set()
        await watcher
    return {
        "parent_console_handle": console_window(), "parent_machine": platform.machine(),
        "parent_platform": sysconfig.get_platform(),
        "parent_python_version": platform.python_version(),
        "observer_canary": canary, "observed_windows": list(window_events.values()),
        "cached_profile_refresh": refresh_control,
        "observer_control_detected_and_retired": True,
        "cycles": cycles, "new_visible_terminal_windows": len(observed),
        "terminal_took_foreground": foreground_changed,
    }


if __name__ == "__main__":
    if sys.argv[1] == "--proxy":
        serve_proxy(Path(sys.argv[2]))
    else:
        result_path = Path(sys.argv[2])
        try:
            require_hosted_windows()
            if sys.prefix == sys.base_prefix:
                import site
                site.addsitedir(sys.argv[3])
            result = {"ok": True, **asyncio.run(drive(Path(sys.argv[1])))}
        except Exception as error:
            causes = []
            cause = error.__cause__ or error.__context__
            while cause is not None and len(causes) < 8:
                causes.append(f"{type(cause).__name__}: {cause}")
                cause = cause.__cause__ or cause.__context__
            result_path.write_text(json.dumps({
                "ok": False, "error": f"{type(error).__name__}: {error}",
                "causes": causes,
            }), encoding="utf-8")
            raise
        result_path.write_text(json.dumps(result), encoding="utf-8")
