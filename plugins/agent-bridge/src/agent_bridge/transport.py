"""Transport -- spawn Copilot ACP agent processes (local + SSH).

SSH connections are managed by the shared ssh-manager library, which
provides ControlMaster multiplexing on Unix and direct SSH fallback on
Windows. Multiple ACP sessions to the same host share a single master
connection.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ssh_manager import SSHProfileSource, get_default_manager

from .connect import ConnectError, ConnectStage, ConnectTracker
from .procgroup import safe_killpg

log = logging.getLogger("agent-bridge")

# Max bytes for a single newline-delimited ACP JSON-RPC frame read from an agent
# subprocess's stdout. asyncio's StreamReader defaults to 64 KiB per line, which
# a large tool result (e.g. a full Hue scene export) can exceed in one
# `session/update` frame -- overflowing readline(), killing the bridge's ACP
# receive loop, and surfacing to the user as "Connection closed" even though the
# agent process is alive and the tool succeeded. Mirror the acp library's 50 MB
# default (acp.core.DEFAULT_STDIO_BUFFER_LIMIT_BYTES).
_ACP_STDIO_LIMIT_BYTES = 50 * 1024 * 1024


def _check_port_alive(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """Check if a local TCP port is listening."""
    import socket as _socket

    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except (ConnectionRefusedError, _socket.timeout, OSError):
        return False


def _creation_flags() -> int:
    """Return subprocess creation flags for the current platform.

    On Windows, ``CREATE_NO_WINDOW`` prevents console allocation failures
    (STATUS_DLL_INIT_FAILED / 0xC0000142) when spawning console subsystem
    executables from a headless background service like agent-bridge.
    """
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


@dataclass
class PluginRef:
    """A CLI plugin to inject into a dispatched agent's launch.

    Neutral, transport-agnostic reference shared between agent-bridge (which
    *decides* a related-repo plugin set) and namespace resolvers (which *stage*
    the payload onto their target and fold ``--plugin-dir`` into the launch
    command). ``source`` is any ``copilot plugin install`` source
    (``plugin@marketplace`` | ``owner/repo`` | ``owner/repo:path`` | git URL).
    ``enable`` mirrors the CodeSpace-plugin manifest semantics (install-and-
    enable vs install-only); resolvers that only do ``--plugin-dir`` may ignore
    it.
    """

    source: str
    enable: bool = True


@dataclass
class SpawnTarget:
    """Where and how to spawn an agent process."""

    type: str = "local"  # "local", "ssh", or "command"
    cwd: str | None = None
    host: str | None = None  # SSH alias (from machines.yaml)
    user: str | None = None
    copilot_path: str | None = None
    copilot_args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    project: str | None = None  # agent-worktrees project (binstub name)
    ssh_shell: str | None = None  # remote shell (e.g. "pwsh", "bash")
    worktree_id: str | None = None  # resume a specific worktree
    caller_worktree: str | None = None  # #2178: caller worktree that requested a
    #                                     bridge spawn (recorded on the new worktree)
    spawn_command: list[str] | None = None  # raw command for provider agents
    codespace: dict | None = None  # structured CodeSpace metadata (#177): {name,
    #                                repo, acp_command, workspace_folder} -- lets
    #                                the daemon route a CS agent through the
    #                                CodeSpaceSpawner without parsing spawn_command
    auth_hooks: list[dict] = field(default_factory=list)  # serializable auth hook dicts

    def to_json(self) -> str:
        """Serialize for DB persistence."""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> SpawnTarget:
        """Deserialize from DB."""
        data: dict[str, Any] = json.loads(raw)
        return cls(**data)


class AgentProcess:
    """Wraps an asyncio subprocess running copilot --acp --stdio."""

    def __init__(self, proc: asyncio.subprocess.Process, target: SpawnTarget) -> None:
        self.proc = proc
        self.target = target

    @property
    def pid(self) -> int | None:
        return self.proc.pid

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None

    async def write(self, data: bytes) -> None:
        """Write data to the process stdin."""
        if self.proc.stdin:
            self.proc.stdin.write(data)
            await self.proc.stdin.drain()

    async def readline(self) -> bytes:
        """Read a line from the process stdout."""
        if self.proc.stdout:
            return await self.proc.stdout.readline()
        return b""

    async def kill(self) -> None:
        """Terminate the subprocess and its entire child tree.

        ``proc.terminate()`` only reaps the direct child -- on Windows that is
        the ``cmd.exe`` batch wrapper, which orphans the ``pwsh -> copilot`` (or
        ``python -> ssh``) tree beneath it, leaving processes that hold the
        worktree directory open. Kill the whole tree instead.
        """
        if not self.alive:
            return
        pid = self.proc.pid
        if sys.platform == "win32":
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/PID", str(pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                async with asyncio.timeout(5):
                    await killer.wait()
            except (TimeoutError, OSError, ProcessLookupError):
                pass
        else:
            # POSIX: the agent spawns use start_new_session, so the child is a
            # process-group leader -- signal the whole group. Guard against
            # ever signaling the bridge's own group (see procgroup / #1001):
            # if the child unexpectedly shares our group, fall back to the
            # direct child only.
            if not safe_killpg(pid, signal.SIGTERM):
                try:
                    self.proc.terminate()
                except ProcessLookupError:
                    pass
        # Reap the direct child handle.
        try:
            async with asyncio.timeout(5):
                await self.proc.wait()
        except (TimeoutError, ProcessLookupError):
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass


def _wrap_batch_for_windows(
    args: list[str], env: dict[str, str],
) -> list[str]:
    """Wrap .cmd/.bat executables with cmd.exe on Windows.

    ``asyncio.create_subprocess_exec`` uses ``CreateProcess`` which
    cannot execute batch files directly.  When the resolved executable
    ends with ``.cmd`` or ``.bat``, we prepend ``cmd.exe /d /s /c`` so
    that ``CreateProcess`` receives a real PE executable.

    On non-Windows platforms this is a no-op.
    """
    if sys.platform != "win32":
        return args

    exe = args[0]
    resolved = shutil.which(exe, path=env.get("PATH"))
    target_path = resolved or exe

    if target_path.lower().endswith((".cmd", ".bat")):
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        args = [comspec, "/d", "/s", "/c", target_path, *args[1:]]
        log.debug("Wrapped batch file for Windows: %s", " ".join(args))

    elif resolved:
        # Use the fully resolved path even for non-batch executables
        args = [resolved, *args[1:]]

    return args


async def _resolve_worktree(
    target: SpawnTarget, env: dict[str, str],
) -> dict:
    """Run ``agent-worktrees resolve --json`` to get a launch plan.

    Calls the agent-worktrees Python module directly (bypassing the
    .cmd binstub and cmd.exe) to avoid console allocation issues when
    running from a headless background service on Windows.

    Returns the parsed JSON plan dict.
    """
    # Replicate the binstub's Python + PYTHONPATH setup
    home = os.path.expanduser("~")
    aw_venv = os.path.join(home, ".agent-worktrees", ".venv")
    aw_lib = os.path.join(home, ".agent-worktrees", "lib")

    if sys.platform == "win32":
        python = os.path.join(aw_venv, "Scripts", "python.exe")
    else:
        python = os.path.join(aw_venv, "bin", "python")

    if not os.path.exists(python):
        raise RuntimeError(
            f"agent-worktrees venv not found at {python}"
        )

    # Set PYTHONPATH so agent_worktrees module is importable,
    # and WORKTREE_PROJECT so it resolves the right project config.
    # Clear VIRTUAL_ENV/PYTHONHOME to avoid the bridge's own venv
    # polluting the agent-worktrees subprocess (they may use different
    # Python versions).
    env = dict(env)
    env["PYTHONPATH"] = aw_lib
    env["PYTHONUTF8"] = "1"
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONHOME", None)
    if target.project:
        env["WORKTREE_PROJECT"] = target.project

    base_args = [python, "-m", "agent_worktrees", "resolve", "--json", "--no-resume"]
    creating_new = not target.worktree_id
    if target.worktree_id:
        base_args.extend(["--worktree-id", target.worktree_id])
    else:
        base_args.append("--new")

    # New-worktree extras that a stale runtime may not recognize (argparse exits
    # non-zero on an unknown flag). Kept OUT of base_args so the fallback below
    # can drop them wholesale and still resolve. --bridge marks the worktree
    # agent-owned; --caller-worktree records the caller for the Picker (#2178).
    new_extra: list[str] = []
    if creating_new:
        new_extra.append("--bridge")
        if target.caller_worktree:
            new_extra.extend(["--caller-worktree", target.caller_worktree])

    async def _run(extra: list[str]):
        argv = base_args + extra
        log.info("Resolving worktree: %s", " ".join(argv))
        p = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            creationflags=_creation_flags(),
        )
        out, err = await p.communicate()
        return p.returncode, out, err

    # A bridge-spawned new worktree is agent-owned -> mark it kind=bridge so the
    # Picker hides it by default and routine cleanup leaves it alone. A stale
    # local agent-worktrees runtime won't recognize --bridge / --caller-worktree
    # (argparse exits non-zero); detect that and retry without the extras so the
    # spawn still resolves (the worktree just isn't bridge-marked / caller-linked).
    returncode, stdout, stderr = await _run(new_extra)
    if (creating_new and returncode != 0 and new_extra
            and any(f in stderr.decode(errors="replace")
                    for f in ("--bridge", "--caller-worktree"))):
        log.info("local agent-worktrees lacks new resolve flags; retrying bare")
        returncode, stdout, stderr = await _run([])

    if stderr:
        for line in stderr.decode(errors="replace").strip().splitlines():
            log.debug("resolve stderr: %s", line)

    if returncode != 0:
        err_text = stderr.decode(errors="replace").strip()
        raise RuntimeError(
            f"Worktree resolve failed (exit {returncode}): {err_text}"
        )

    try:
        plan = json.loads(stdout.decode())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            f"Worktree resolve returned invalid JSON: {exc}"
        ) from exc

    return plan


def _extract_json_object(text: str) -> dict | None:
    """Parse the first top-level JSON object from possibly-noisy text.

    A remote shell may prepend MOTD/banner lines before the binstub's JSON,
    so fall back to extracting the outermost ``{...}`` span if the whole
    string is not valid JSON.
    """
    text = text.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


async def _resolve_worktree_remote(
    manager: Any, target: SpawnTarget, *, timeout: float = 120.0,
) -> dict:
    """Resolve the remote worktree plan over SSH to learn its id + work_dir.

    Mirrors :func:`_resolve_worktree` (the local path) for SSH targets: runs
    the project binstub's ``resolve`` subcommand on the remote host over the
    shared ControlMaster connection and parses the JSON launch plan. This lets
    the bridge bind ``worktree_id``/``cwd`` onto the session target for remote
    sessions -- without it, an SSH session persists a null ``worktree_id`` and
    never links back to its worktree (managed/live state, duplicate cards).

    The binstub ``resolve`` subcommand emits clean JSON (it bypasses the
    launch-session scripts), but the JSON object is still extracted defensively
    in case a remote shell prepends banner noise.

    Returns the parsed plan dict. Raises ``RuntimeError`` on failure; the
    caller treats failure as non-fatal (falls back to a direct ``--new``
    launch).
    """
    if not target.project:
        raise RuntimeError("remote resolve requires target.project")
    base_args = [target.project, "resolve", "--json", "--no-resume"]
    creating_new = not target.worktree_id
    if target.worktree_id:
        base_args.extend(["--worktree-id", target.worktree_id])
    else:
        base_args.append("--new")

    # New-worktree extras a version-skewed remote may not recognize; kept out of
    # base_args so the fallback can drop them wholesale (#2178).
    new_extra: list[str] = []
    if creating_new:
        new_extra.append("--bridge")
        if target.caller_worktree:
            new_extra.extend(["--caller-worktree", target.caller_worktree])

    async def _run(extra: list[str]):
        cmd = " ".join(shlex.quote(a) for a in base_args + extra)
        log.info("Resolving remote worktree on %s: %s", target.host, cmd)
        return await manager.exec_command(target.host, cmd, timeout=timeout)

    # A bridge-spawned new worktree is agent-owned -> mark it kind=bridge so the
    # remote Picker hides it by default and routine cleanup leaves it alone.
    # An older remote agent-worktrees won't recognize --bridge / --caller-worktree
    # (argparse exits non-zero); detect that and retry without the extras so a
    # version-skewed remote still spawns. Mirrors the data_ssh --classify fallback.
    result = await _run(new_extra)
    if (creating_new and not result.timed_out and result.exit_code != 0
            and new_extra
            and any(f in (result.stderr or "")
                    for f in ("--bridge", "--caller-worktree"))):
        log.info("remote %s lacks new resolve flags; retrying bare", target.host)
        result = await _run([])

    if result.timed_out:
        raise RuntimeError(f"remote worktree resolve timed out after {timeout}s")
    if result.exit_code != 0:
        raise RuntimeError(
            f"remote worktree resolve failed (exit {result.exit_code}): "
            f"{result.stderr.strip()[:400]}"
        )

    plan = _extract_json_object(result.stdout)
    if plan is None:
        raise RuntimeError(
            "remote worktree resolve returned no JSON object: "
            f"{result.stdout.strip()[:400]}"
        )
    return plan


async def _resolve_remote_existing_cwd(
    manager: Any, target: SpawnTarget, *, timeout: float = 10.0,
) -> str | None:
    """Ask an SSH target for a directory that exists there.

    Used only as a fallback when worktree resolution cannot provide the real
    checkout path. ACP validates ``cwd`` during ``new_session``/``load_session``,
    so a verified remote home/current directory is safer than a templated guess.
    """
    if not target.host:
        return None
    if target.ssh_shell in ("pwsh", "powershell", "cmd"):
        exe = "powershell" if target.ssh_shell in ("powershell", "cmd") else "pwsh"
        script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$candidates = @($env:USERPROFILE, $HOME, (Get-Location).Path, 'C:\')
foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Container)) {
        [Console]::Out.Write($candidate)
        exit 0
    }
}
exit 1
"""
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        cmd = f"{exe} -NoProfile -EncodedCommand {encoded}"
    else:
        cmd = (
            'if [ -n "${HOME:-}" ] && [ -d "$HOME" ]; then '
            'printf %s "$HOME"; else pwd; fi'
        )

    try:
        result = await manager.exec_command(target.host, cmd, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 -- best-effort fallback probe
        log.warning("Remote cwd fallback probe failed for %s: %s", target.host, exc)
        return None
    if result.timed_out or result.exit_code != 0:
        detail = "timed out" if result.timed_out else f"exit {result.exit_code}"
        stderr = (result.stderr or "").strip()
        if stderr:
            detail = f"{detail}: {stderr[:200]}"
        log.warning("Remote cwd fallback probe failed for %s (%s)", target.host, detail)
        return None

    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    return lines[-1] if lines else None


async def resolve_local_launch(
    target: SpawnTarget,
    *,
    tracker: ConnectTracker | None = None,
    session_id: str = "",
) -> tuple[list[str], str | None, dict[str, str]]:
    """Resolve a local spawn into a concrete launch plan ``(args, cwd, env)``.

    Extracted from :func:`spawn_local` so the same worktree-resolve + arg-building
    logic can feed either a directly-owned child (``spawn_local``) or a
    **Session-Host-owned** child (the session_host launcher). Returns the argv
    (already batch-wrapped on Windows), the working directory, and the full child
    environment.
    """
    tracker = tracker or ConnectTracker(session_id=session_id)
    env = os.environ.copy()
    # Strip bridge's venv vars so child processes use their own Python
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONHOME", None)
    env.update(target.env)

    if target.project:
        # Stage 6: create/resume the worktree. Failures propagate (no retry).
        with tracker.stage(ConnectStage.WORKTREE, f"project={target.project}"):
            plan = await _resolve_worktree(target, env)

        launch = plan.get("launch", plan)
        work_dir = launch.get("work_dir")
        cmd = launch.get("cmd", [])
        plan_env = launch.get("env", {})
        worktree_id = launch.get("worktree_id")

        if not cmd:
            raise RuntimeError("Worktree resolve returned empty cmd")

        # Store resolved values back into target for DB persistence
        if worktree_id and not target.worktree_id:
            target.worktree_id = worktree_id
        if work_dir and not target.cwd:
            target.cwd = work_dir

        # Merge plan environment into the process env
        env.update(plan_env)

        # Append ACP protocol args + any extra copilot args
        args = cmd + ["--acp", "--stdio"] + target.copilot_args
        log.info(
            "Resolved copilot launch from worktree plan: %s (cwd=%s, worktree=%s)",
            " ".join(args), work_dir, worktree_id,
        )
    else:
        if not target.cwd:
            raise ValueError("Local agent without 'project' requires 'cwd'")
        copilot = target.copilot_path or _find_copilot()
        args = [copilot, "--acp", "--stdio"] + target.copilot_args
        work_dir = target.cwd
        log.info("Resolved local agent launch: %s (cwd=%s)", " ".join(args), work_dir)

    args = _wrap_batch_for_windows(args, env)
    return args, work_dir, env


async def spawn_local(
    target: SpawnTarget,
    *,
    tracker: ConnectTracker | None = None,
    session_id: str = "",
) -> AgentProcess:
    """Spawn a Copilot ACP agent as a local subprocess.

    When a ``project`` is configured, uses a two-step flow (resolve worktree ->
    exec copilot with ``--acp --stdio``); without it, runs copilot directly.
    The launch-plan resolution lives in :func:`resolve_local_launch`.
    """
    args, work_dir, env = await resolve_local_launch(
        target, tracker=tracker, session_id=session_id,
    )

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=work_dir or None,
        env=env,
        creationflags=_creation_flags(),
        start_new_session=(sys.platform != "win32"),
        limit=_ACP_STDIO_LIMIT_BYTES,
    )

    return AgentProcess(proc, target)


def _breadcrumb_prelude(session_id: str) -> str:
    """A POSIX snippet that records arrival on the target device.

    Appended (best-effort) to ``$AGENT_BRIDGE_CONNECT_LOG`` (default
    ``$HOME/.agent-bridge/connect.log``) the moment the remote shell runs --
    *before* the binstub/worktree/Copilot steps. If a later step hangs or
    fails, a human can SSH in and confirm from this log that the connection
    reached the device (and roughly when), distinguishing an unreachable host
    from an on-device failure. Creates the log dir if needed and never aborts
    the command (wrapped in ``( ... ) || true``).
    """
    sid = shlex.quote(session_id or "-")
    log_expr = '"${AGENT_BRIDGE_CONNECT_LOG:-$HOME/.agent-bridge/connect.log}"'
    ts = '"$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"'
    host = '"$(hostname 2>/dev/null || echo \\?)"'
    return (
        f'( mkdir -p "$(dirname {log_expr})" 2>/dev/null; '
        f"printf '%s agent-bridge reached-device session=%s pid=%s host=%s\\n' "
        f"{ts} {sid} \"$$\" {host} >> {log_expr} 2>/dev/null ) || true"
    )


def _build_remote_cmd(target: SpawnTarget, session_id: str = "") -> str:
    """Build the remote command string for SSH execution.

    Two modes:
    - With ``project``: uses the project binstub (handles setup scripts,
      vault credentials, copilot resolution on the remote side).
    - Without ``project``: cd + export + exec copilot (legacy).

    A device-arrival breadcrumb (see :func:`_breadcrumb_prelude`) is prepended
    so a failed/hung launch can still be diagnosed as "reached the device".
    """
    copilot = target.copilot_path or "copilot"
    breadcrumb = _breadcrumb_prelude(session_id)

    if target.project:
        # ``--json`` marks the launch as non-interactive: it forces the
        # binstub's ``resolve`` step to skip the TTY picker and resolve the
        # worktree deterministically (by ``--worktree-id`` or ``--new``).
        # Without it, ``resolve`` treats a no-TTY SSH spawn as "no worktree
        # specified" and aborts before Copilot launches -- the ACP client then
        # sees the closed stdio as a ``LAUNCH_ACP`` "Connection closed" failure.
        if target.worktree_id:
            # Session roll: resume existing worktree, skip Copilot session
            # resume (bridge manages ACP sessions independently)
            binstub_args = [
                target.project, "--json", "--worktree-id", target.worktree_id,
                "--no-mux", "--no-update", "--no-resume",
                "--", "--acp", "--stdio",
            ]
        else:
            binstub_args = [
                target.project, "--json", "--new", "--no-mux", "--no-update",
                "--", "--acp", "--stdio",
            ]
        if target.copilot_args:
            binstub_args.extend(target.copilot_args)
        # PowerShell -- the default OpenSSH shell on native Windows targets
        # (lambda-core, borealis) -- treats a *bare* ``--`` as its
        # end-of-parameters sigil and drops it, stripping the ACP passthrough
        # separator before the project binstub sees it (the binstub then
        # forwards ``--acp --stdio ...`` to argparse, which rejects them, #985).
        # A *quoted* ``'--'`` is a literal argument in both bash and
        # PowerShell, so force-quote the separator; shlex.quote leaves a bare
        # ``--`` unquoted.
        binstub_cmd = " ".join(
            "'--'" if a == "--" else shlex.quote(a)
            for a in binstub_args
        )
        # The breadcrumb prelude and ``export K=V`` are POSIX shell syntax.
        # Native Windows SSH targets run PowerShell, which cannot parse the
        # bash subshell in the breadcrumb ( ``( ... ) || true`` ): pwsh
        # raises a ParserError and aborts the *entire* launch command before
        # the binstub runs (#985). For a non-POSIX shell, skip the
        # best-effort breadcrumb and emit any env vars in the shell's syntax.
        shell = (target.ssh_shell or "bash").lower()
        if shell in ("pwsh", "powershell"):
            if target.env:
                prefix = "".join(
                    f"$env:{k} = '{v.replace(chr(39), chr(39) * 2)}'; "
                    for k, v in target.env.items()
                )
                pwsh_script = f"{prefix}{binstub_cmd}"
            else:
                pwsh_script = binstub_cmd
            # The Windows OpenSSH sshd DefaultShell on these dev boxes is
            # ``cmd.exe`` (the OpenSSH default), NOT PowerShell -- so a bare
            # pwsh-syntax command string would be handed to cmd.exe, which
            # cannot parse ``$env:K = 'v'`` assignments and does not strip the
            # quoted ``'--'`` ACP separator. The launch aborts before Copilot
            # starts and the ACP client sees closed stdio ("Connection closed"
            # at stage LAUNCH_ACP, #985 follow-up). Invoke PowerShell
            # *explicitly* via ``-EncodedCommand`` (base64 UTF-16LE): this is
            # quoting-proof and independent of the remote DefaultShell -- it
            # runs correctly whether sshd hands the line to cmd.exe or pwsh.
            #
            # ``-WindowStyle Hidden`` keeps this ACP-stdio pwsh headless. When a
            # remote Windows sshd execs a console-subsystem child without a
            # console (the non-PTY exec path we use), Windows otherwise allocates
            # a *visible* console window for it -- so every inbound dispatch pops
            # a pwsh window on the target box (dotfiles#403). Hidden costs nothing
            # for a stdio-piped ACP agent (stdio is inherited, not the window).
            exe = "powershell" if shell == "powershell" else "pwsh"
            encoded = base64.b64encode(
                pwsh_script.encode("utf-16-le")
            ).decode("ascii")
            return f"{exe} -NoProfile -WindowStyle Hidden -EncodedCommand {encoded}"
        # Prepend env exports (e.g. auth hook vars) so they're available
        # to the binstub and all child processes in the SSH session
        if target.env:
            exports = " && ".join(
                f"export {k}={shlex.quote(v)}" for k, v in target.env.items()
            )
            return f"{breadcrumb} && {exports} && {binstub_cmd}"
        return f"{breadcrumb} && {binstub_cmd}"

    if not target.cwd:
        raise ValueError("SSH agent without 'project' requires 'cwd'")
    parts = [breadcrumb, f"cd {shlex.quote(target.cwd)}"]
    if target.env:
        for k, v in target.env.items():
            parts.append(f"export {k}={shlex.quote(v)}")
    copilot_cmd = f"exec {shlex.quote(copilot)} --acp --stdio"
    if target.copilot_args:
        copilot_cmd += " " + " ".join(shlex.quote(a) for a in target.copilot_args)
    parts.append(copilot_cmd)
    return " && ".join(parts)


async def spawn_ssh(
    target: SpawnTarget,
    *,
    tracker: ConnectTracker | None = None,
    connect_timeout: float | None = None,
    session_id: str = "",
) -> AgentProcess:
    """Spawn a Copilot ACP agent on a remote machine via SSH.

    Uses ssh-manager's ConnectionManager for ControlMaster multiplexing.
    The manager maintains a persistent master connection per host, and
    subsequent ACP sessions multiplex over it (on Unix). On Windows,
    falls back to direct SSH (no multiplexing).

    Auth hooks from the machine topology are applied automatically:
    - Port forwards (-R) are passed to the master connection
    - Environment variables are injected into the remote command
    - Local service liveness is checked before connecting

    SSH hardening (BatchMode, -T, ConnectTimeout, ServerAliveInterval)
    is handled by ssh-manager's base args.

    When ``connect_timeout`` is set, the SSH connect (stage SSH_TO_TARGET) is
    retried with backoff until the deadline -- patience for a booting
    codespace / wake-on-LAN / ProxyJump host. Without it, a single attempt is
    made (fast fail), preserving legacy behavior. ``tracker`` records
    per-stage checkpoints.
    """
    if not target.host:
        raise ValueError("SSH target requires a host (SSH alias)")

    tracker = tracker or ConnectTracker(session_id=session_id)

    # Stage 4 (prep side): resolve auth hooks into port forwards and env vars.
    # The local auth-relay port liveness is the early-warning signal -- if it is
    # down, remote auth cannot work.
    tracker.started(ConnectStage.TARGET_AUTH_ENV, f"host={target.host}")
    port_forwards: list[str] = []
    auth_env: dict[str, str] = {}
    dead_ports: list[int] = []
    for hook in target.auth_hooks:
        local_port = hook.get("local_port", 0)
        remote_port = hook.get("remote_port") or local_port
        hook_name = hook.get("name", "unknown")
        if local_port:
            if not _check_port_alive(local_port):
                dead_ports.append(local_port)
                log.warning(
                    "Auth hook '%s': local port %d is not listening -- "
                    "skipping port forward (auth may not work on remote)",
                    hook_name, local_port,
                )
            else:
                port_forwards.append(f"-R {remote_port}:127.0.0.1:{local_port}")
                log.info(
                    "Auth hook '%s': forwarding remote:%d -> local:%d",
                    hook_name, remote_port, local_port,
                )
        hook_env = hook.get("env", {})
        if hook_env:
            auth_env.update(hook_env)
            log.info(
                "Auth hook '%s': injecting env vars: %s",
                hook_name, list(hook_env.keys()),
            )
    if dead_ports:
        tracker.failed(
            ConnectStage.TARGET_AUTH_ENV,
            f"auth relay local port(s) not listening: {dead_ports}",
            retryable=False,
        )
    else:
        tracker.reached(
            ConnectStage.TARGET_AUTH_ENV,
            f"forwards={len(port_forwards)} env={list(auth_env.keys())}",
        )

    # Merge auth env into target env (auth hooks have lowest precedence)
    if auth_env:
        merged = dict(auth_env)
        merged.update(target.env)
        target.env = merged

    manager = get_default_manager()
    source = SSHProfileSource(host_alias=target.host, user=target.user)

    # Stage 3: establish the SSH connection -- patient (retry to deadline) when
    # connect_timeout is set, else a single fast attempt.
    tracker.started(ConnectStage.SSH_TO_TARGET, f"host={target.host}")
    deadline = (time.monotonic() + connect_timeout) if connect_timeout else None
    attempt = 0
    backoff = 2.0
    while True:
        attempt += 1
        try:
            await manager.ensure_connected(
                target.host, source, port_forwards=port_forwards or None,
            )
            tracker.reached(
                ConnectStage.SSH_TO_TARGET, f"host={target.host} attempt={attempt}"
            )
            break
        except (ConnectionError, TimeoutError) as exc:
            # Transient: the host may still be booting / waking. Retry until the
            # deadline, then fail fast with a staged, retryable error.
            now = time.monotonic()
            if deadline is None or now + backoff >= deadline:
                tracker.failed(
                    ConnectStage.SSH_TO_TARGET,
                    f"Failed to establish SSH connection to {target.host}: {exc}",
                    retryable=True,
                )
                raise ConnectError(
                    ConnectStage.SSH_TO_TARGET,
                    f"Failed to establish SSH connection to {target.host}: {exc}",
                    retryable=True,
                    cause=exc,
                ) from exc
            log.info(
                "SSH connect to %s not ready (attempt %d): %s -- retrying in %.0fs",
                target.host, attempt, exc, backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 15.0)

    # Bind the worktree identity for remote sessions (parity with the local
    # spawn path). Resolve the remote worktree up-front so worktree_id + cwd
    # persist onto the session target -- otherwise SSH sessions store a null
    # worktree_id and never link back to their worktree, which breaks the
    # bridge's session<->worktree linkage (managed/live state, duplicate NF
    # cards). ``resolve --new`` creates the worktree; _build_remote_cmd then
    # takes its resume branch (--worktree-id) so the binstub launches into the
    # just-created worktree (no second worktree). If resolve fails, keep the
    # legacy direct --new launch path but surface the failure as a connection
    # checkpoint and probe the target for an existing cwd so ACP validation does
    # not fail on a templated, non-existent home directory.
    if target.project and (not target.worktree_id or not target.cwd):
        tracker.started(ConnectStage.WORKTREE, f"resolve project={target.project}")
        try:
            plan = await _resolve_worktree_remote(manager, target)
            launch = plan.get("launch", plan)
            wt_id = launch.get("worktree_id")
            work_dir = launch.get("work_dir")
            if wt_id:
                target.worktree_id = wt_id
            if work_dir and not target.cwd:
                target.cwd = work_dir
            if not target.cwd:
                fallback = await _resolve_remote_existing_cwd(manager, target)
                if fallback:
                    target.cwd = fallback
                    log.warning(
                        "Remote worktree resolve for %s returned no cwd; "
                        "using verified fallback cwd=%s",
                        target.host, fallback,
                    )
            log.info(
                "Bound remote worktree for %s: id=%s cwd=%s",
                target.host, wt_id, target.cwd,
            )
            tracker.reached(
                ConnectStage.WORKTREE,
                f"worktree={target.worktree_id or '(unbound)'} cwd={target.cwd or '(none)'}",
            )
        except Exception as exc:  # noqa: BLE001 -- non-fatal, see above
            detail = (
                f"remote worktree resolve failed for {target.host}: {exc}; "
                "falling back to direct launch"
            )
            log.warning("%s", detail)
            if not target.cwd:
                fallback = await _resolve_remote_existing_cwd(manager, target)
                if fallback:
                    target.cwd = fallback
                    detail += f" with verified cwd={fallback}"
                    log.warning(
                        "Using verified remote fallback cwd for %s: %s",
                        target.host, fallback,
                    )
                else:
                    detail += "; no verified cwd available"
            tracker.failed(ConnectStage.WORKTREE, detail, retryable=False)

    # Stages 5-7 happen remotely inside the binstub; the device breadcrumb
    # (in the remote command) is the on-device proof of arrival.
    remote_cmd = _build_remote_cmd(target, session_id=session_id)
    log.info("Spawning SSH agent on %s: %s", target.host, remote_cmd)

    proc = await manager.open_stdio_channel(target.host, remote_cmd)
    return AgentProcess(proc, target)


async def spawn(
    target: SpawnTarget,
    *,
    tracker: ConnectTracker | None = None,
    connect_timeout: float | None = None,
    session_id: str = "",
) -> AgentProcess:
    """Spawn an ACP agent process (local, SSH, or command)."""
    if target.type == "command" or target.spawn_command:
        return await spawn_raw(target, tracker=tracker, session_id=session_id)
    if target.type == "ssh":
        return await spawn_ssh(
            target, tracker=tracker, connect_timeout=connect_timeout,
            session_id=session_id,
        )
    return await spawn_local(target, tracker=tracker, session_id=session_id)


async def spawn_raw(
    target: SpawnTarget,
    *,
    tracker: ConnectTracker | None = None,
    session_id: str = "",
) -> AgentProcess:
    """Spawn an ACP agent via a raw command.

    Used for provider agents that handle their own transport (e.g.
    agent-codespaces wraps SSH connection and copilot launch internally).
    The command is expected to speak ACP protocol on stdin/stdout.
    """
    if not target.spawn_command:
        raise ValueError("Command target requires spawn_command")

    env = os.environ.copy()
    # Strip bridge's venv vars so child processes use their own Python
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONHOME", None)
    env.update(target.env)

    args = _wrap_batch_for_windows(list(target.spawn_command), env)
    log.info("Spawning command agent: %s", " ".join(args))

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        creationflags=_creation_flags(),
        start_new_session=(sys.platform != "win32"),
        limit=_ACP_STDIO_LIMIT_BYTES,
    )

    return AgentProcess(proc, target)


def _find_copilot() -> str:
    """Find the copilot CLI binary."""
    # Check environment override
    path = os.environ.get("COPILOT_PATH")
    if path:
        return path

    # Default to "copilot" on PATH
    return "copilot"


async def shutdown_ssh() -> None:
    """Disconnect all SSH master connections.

    Called during app shutdown, after ACP sessions are stopped.
    Safe to call even if no connections exist.
    """
    try:
        manager = get_default_manager()
        await manager.disconnect_all()
    except Exception:
        log.warning("Error during SSH connection shutdown", exc_info=True)
