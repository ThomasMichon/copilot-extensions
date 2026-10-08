"""Supervised credential-relay reverse-forward channel.

``SupervisedRelayForward`` owns a dedicated ``ssh -N -R`` process for a
host-local credential relay, plus a small monitor loop that re-establishes the
forward when the process dies or an optional serving probe says the far side is
not usable. The channel uses the same :class:`SSHConfig` as the
ConnectionManager, including a CodeSpace's ``gh cs ssh`` ``ProxyCommand`` via
``-F`` config files, but it does **not** share the coordination/ACP SSH
connection's lifetime.

The agent-bridge detached Session-Host path uses this seam to keep credential
relay lifetime independent from frontend reattach. An optional
``host_port_resolver`` lets the host-side target of the ``-R`` follow a relay
that rebinds a new port across a daemon restart, while the CodeSpace-listen
port stays stable (dotfiles #855).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .config_sources import SSHConfig
from .forward import build_forward_ssh_args
from .locks import process_identity
from .process import terminate_ssh_process_tree
from .proxy import create_ssh_subprocess

log = logging.getLogger("ssh-manager.relay")

_ESTABLISH_ATTEMPTS = 4
_READY_SETTLE_MAX = 0.25
_REMOTE_FORWARD_FAILURE_MARKERS = (
    "remote port forwarding failed",
    "warning: remote port forwarding failed for listen port",
)


@dataclass(frozen=True)
class _SettleResult:
    ready: bool
    stderr: str
    remote_forward_failed: bool = False


class SupervisedRelayForward:
    """Dedicated, self-healing ``ssh -N -R`` channel for a credential relay.

    ``start()`` establishes one reverse-forward process and launches a monitor
    task. The host-side supervisor's reliable native signals are process death
    and establish-time remote-forward-failure stderr. When supplied,
    ``serving_probe`` is an injection point for a future end-to-end,
    CodeSpace-side serving check; a false result is treated as unhealthy and
    causes the process to be killed and re-established with bounded exponential
    backoff.

    The remote bind is loopback-to-loopback:
    ``<relay_port>:127.0.0.1:<host_port>`` -- symmetric by default, but when a
    ``host_port_resolver`` is supplied the host-side target is re-resolved live
    on each (re-)establish so it follows a relay that rebinds after a daemon
    restart, while the CodeSpace-listen ``relay_port`` stays stable (#855).
    ``ExitOnForwardFailure=yes`` is set: the channel carries only this ``-R``,
    so a failed remote bind leaves nothing worth keeping. Without it OpenSSH
    stays connected with no forward -- silently, since a CodeSpace's
    ``LogLevel=quiet`` hides the warning and the bind reply often arrives after
    the readiness window -- and the far side has no relay until something
    kills the process. With it, ssh exits and the monitor re-establishes the
    forward with backoff, including once a stale far-side listener (a previous
    connection the CodeSpace hasn't reaped yet) lets go of the port.
    ``establish()`` still watches stderr during the readiness window and
    retries a bind failure it sees there.

    ``reconnect_gate`` (optional) is consulted before every monitor-driven
    re-establish. Reconnecting through a ProxyCommand such as ``gh codespace
    ssh`` can start a stopped venue, so a gate that returns ``False`` (the
    venue is stopped) retires the supervisor instead of reconnecting; a gate
    that raises is inconclusive and the attempt is skipped (no connect) and
    retried after backoff. The initial ``start()`` is never gated.
    """

    def __init__(
        self,
        config: SSHConfig,
        relay_port: int,
        *,
        monitor_interval: float = 15.0,
        backoff_base: float = 1.0,
        backoff_max: float = 30.0,
        ready_timeout: float = 40.0,
        serving_probe: Callable[[], Awaitable[bool]] | None = None,
        host_port_resolver: Callable[[], int] | None = None,
        on_pid_change: Callable[[], None] | None = None,
        reconnect_gate: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self._config = config
        self._relay_port = int(relay_port)
        self._host_port_resolver = host_port_resolver
        self._established_host_port: int | None = None
        self._monitor_interval = float(monitor_interval)
        self._backoff_base = float(backoff_base)
        self._backoff_max = float(backoff_max)
        self._ready_timeout = float(ready_timeout)
        self._serving_probe = serving_probe
        self._on_pid_change = on_pid_change
        self._reconnect_gate = reconnect_gate
        self._proc: asyncio.subprocess.Process | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._stopped = False
        self._retired = False
        self._cleanup_tasks: set[asyncio.Task[None]] = set()

    @property
    def is_alive(self) -> bool:
        """Whether the supervised ``ssh -N -R`` process is currently running."""
        return self._proc is not None and self._proc.returncode is None

    @property
    def retired(self) -> bool:
        """Whether the monitor gave up because the reconnect gate said stop."""
        return self._retired

    @property
    def process_pid(self) -> int | None:
        """The current ``ssh -N -R`` child pid, when one is live."""
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return None
        pid = getattr(proc, "pid", None)
        return pid if isinstance(pid, int) and pid > 0 else None

    @property
    def process_birth_identity(self) -> str | None:
        pid = self.process_pid
        return process_identity(pid) if isinstance(pid, int) and pid > 0 else None

    def _notify_pid_change(self) -> None:
        callback = self._on_pid_change
        if callback is None:
            return
        try:
            callback()
        except Exception as exc:  # noqa: BLE001 - best-effort state refresh
            log.warning("Credential relay pid-change callback failed: %s", exc)

    def _resolve_host_port(self) -> int:
        """Resolve the host-side ``-R`` target port.

        Defaults to the (stable) CodeSpace-listen ``relay_port`` so behavior is
        unchanged without a resolver. When a ``host_port_resolver`` is supplied,
        its positive result wins -- so the host target follows a relay that
        rebinds a new port after a daemon restart while the listen port (which
        the agent's ``LC_GIT_CREDENTIAL_RELAY`` is frozen to) stays put (#855).
        """
        if self._host_port_resolver is not None:
            try:
                resolved = int(self._host_port_resolver() or 0)
            except Exception:  # noqa: BLE001 - resolver is best-effort
                resolved = 0
            if resolved > 0:
                return resolved
        return self._relay_port

    async def establish(self) -> None:
        """Spawn the reverse-forward process and wait for it to stay alive.

        There is no local ``-L`` socket to probe for a reverse-only forward.
        Readiness is therefore "the process stayed alive during a short settle
        window and did not report a remote-forward bind failure on stderr". If
        the remote bind fails while ssh stays alive, the process is killed and
        retried after a short bounded backoff so a stale far-side listener has
        time to release. If ssh exits immediately, stderr is captured and
        surfaced as a :class:`ConnectionError`.
        """
        if self.is_alive:
            return
        if self._proc is not None:
            self._proc = None

        host_port = self._resolve_host_port()
        spec = f"{self._relay_port}:127.0.0.1:{host_port}"
        last_err = ""
        for attempt in range(1, _ESTABLISH_ATTEMPTS + 1):
            args = build_forward_ssh_args(
                self._config,
                None,
                None,
                reverse_forwards=[spec],
                exit_on_forward_failure=True,
            )
            log.debug(
                "Establishing credential relay reverse-forward "
                "(attempt %d/%d): %s",
                attempt,
                _ESTABLISH_ATTEMPTS,
                " ".join(args),
            )
            proc = await create_ssh_subprocess(
                *args,
                config=self._config,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            self._proc = proc
            self._notify_pid_change()
            try:
                settled = await self._wait_settled(proc)
            except asyncio.CancelledError:
                await self._kill(proc)
                if self._proc is proc:
                    self._proc = None
                raise
            if settled.ready:
                self._established_host_port = host_port
                self._notify_pid_change()
                log.info(
                    "Credential relay reverse-forward up on %s "
                    "(-R %d:127.0.0.1:%d)",
                    self._config.ssh_target,
                    self._relay_port,
                    host_port,
                )
                return

            stderr = settled.stderr or await self._drain_stderr(proc)
            last_err = stderr or last_err or "ssh exited"
            exited_early = proc.returncode is not None
            await self._kill(proc)
            if self._proc is proc:
                self._proc = None

            # This channel runs with ExitOnForwardFailure, so an early exit is
            # almost always the remote bind failing -- and under a CodeSpace's
            # LogLevel=quiet ssh may exit without saying so. Retry either way.
            if (settled.remote_forward_failed or exited_early) \
                    and attempt < _ESTABLISH_ATTEMPTS:
                delay = min(
                    self._backoff_max,
                    max(2.0, self._backoff_base * (2 ** (attempt - 1))),
                )
                log.warning(
                    "Credential relay remote -R bind failed on %s "
                    "(attempt %d/%d); retrying in %.1fs: %s",
                    self._config.ssh_target,
                    attempt,
                    _ESTABLISH_ATTEMPTS,
                    delay,
                    stderr.strip() or "remote port forwarding failed",
                )
                await self._sleep(delay)
                continue
            break

        raise ConnectionError(
            "credential relay reverse-forward to "
            f"{self._config.ssh_target} did not come up: {last_err}"
        )

    async def start(self) -> None:
        """Establish the relay channel and start the self-healing monitor."""
        self._stopped = False
        self._retired = False
        await self.establish()
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(
                self._monitor(), name="ssh-manager-relay-monitor"
            )

    async def stop(self) -> None:
        """Stop monitoring and tear down the relay process (idempotent)."""
        self._stopped = True
        task = self._monitor_task
        self._monitor_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._cancel_process()

    def stop_nowait(self) -> None:
        """Synchronously stop monitoring and kill the relay's process tree.

        For teardown paths that cannot await. Cancels the monitor (so it can
        never reconnect), kills the ``ssh`` root together with its
        ProxyCommand descendants (e.g. ``gh codespace ssh``), and -- when an
        event loop is running -- schedules the full async cleanup as well.
        Idempotent.
        """
        self._stopped = True
        task = self._monitor_task
        self._monitor_task = None
        if task is not None and not task.done():
            task.cancel()
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        if proc.returncode is None:
            self._kill_tree_nowait(proc)
        self._notify_pid_change()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        cleanup = loop.create_task(self._kill(proc))
        self._cleanup_tasks.add(cleanup)
        cleanup.add_done_callback(self._cleanup_tasks.discard)

    @staticmethod
    def _kill_tree_nowait(proc: asyncio.subprocess.Process) -> None:
        pid = getattr(proc, "pid", None)
        if sys.platform != "win32" and isinstance(pid, int) and pid > 0:
            # The ssh root leads its own session/group (start_new_session), so
            # the group kill also reaches its ProxyCommand children. Never
            # signal a group that is not the root's own (or is ours).
            try:
                pgid = os.getpgid(pid)
                if pgid == pid and pgid != os.getpgrp():
                    os.killpg(pgid, signal.SIGKILL)
                    return
            except OSError:
                pass
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass

    async def _monitor(self) -> None:
        try:
            while not self._stopped:
                try:
                    await self._sleep(self._monitor_interval)
                    reason = await self._restart_reason()
                    if reason is not None:
                        if not await self._restart_with_backoff(reason):
                            return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("Credential relay monitor iteration failed: %s", exc)
        except asyncio.CancelledError:
            raise

    async def _reconnect_allowed(self) -> bool | None:
        """Consult the reconnect gate: True=go, False=retire, None=unknown."""
        if self._stopped:
            return False
        gate = self._reconnect_gate
        if gate is None:
            return True
        try:
            return bool(await gate())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                "Credential relay reconnect gate for %s was inconclusive; "
                "not reconnecting this attempt: %s",
                self._config.ssh_target,
                exc,
            )
            return None

    async def _restart_reason(self) -> str | None:
        if not self.is_alive:
            return "process exited"
        if self._established_host_port is not None:
            current = self._resolve_host_port()
            if current != self._established_host_port:
                return (
                    "host relay port changed "
                    f"({self._established_host_port} -> {current})"
                )
        if self._serving_probe is None:
            return None
        try:
            if await self._serving_probe():
                return None
            return "serving probe failed"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Credential relay serving probe failed: %s", exc)
            return "serving probe raised"

    async def _restart_with_backoff(self, reason: str) -> bool:
        """Re-establish the relay; return ``False`` when the monitor should end."""
        failures = 0
        while True:
            await self._cancel_process()
            allowed = await self._reconnect_allowed()
            if allowed is False:
                if not self._stopped:
                    self._retired = True
                    log.info(
                        "Credential relay to %s retired (%s): the remote venue "
                        "is stopped, so reconnecting would restart it",
                        self._config.ssh_target,
                        reason,
                    )
                return False
            if allowed is None:
                failures += 1
                await self._sleep(min(
                    self._backoff_max,
                    self._backoff_base * (2 ** (failures - 1)),
                ))
                continue
            try:
                log.warning(
                    "Credential relay reverse-forward unhealthy (%s); re-establishing",
                    reason,
                )
                await self.establish()
                return True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                delay = min(
                    self._backoff_max,
                    self._backoff_base * (2 ** (failures - 1)),
                )
                log.warning(
                    "Credential relay reverse-forward re-establish failed "
                    "(attempt %d): %s; retrying in %.1fs",
                    failures,
                    exc,
                    delay,
                )
                await self._sleep(delay)

    async def _wait_settled(
        self, proc: asyncio.subprocess.Process,
    ) -> _SettleResult:
        stderr_parts: list[str] = []
        settle = min(self._ready_timeout, _READY_SETTLE_MAX)
        deadline = asyncio.get_running_loop().time() + max(0.0, settle)
        while True:
            stderr = await self._read_stderr_available(proc)
            if stderr:
                stderr_parts.append(stderr)
                combined = "".join(stderr_parts)
                if self._remote_forward_failed(combined):
                    return _SettleResult(False, combined.strip(), True)
            combined = "".join(stderr_parts)
            if proc.returncode is not None:
                return _SettleResult(False, combined.strip(), False)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return _SettleResult(True, combined.strip(), False)
            await asyncio.sleep(min(0.05, remaining))

    @staticmethod
    def _remote_forward_failed(stderr: str) -> bool:
        lower = stderr.lower()
        return any(marker in lower for marker in _REMOTE_FORWARD_FAILURE_MARKERS)

    @staticmethod
    async def _read_stderr_available(
        proc: asyncio.subprocess.Process,
        *,
        timeout: float = 0.01,
    ) -> str:
        if proc.stderr is None:
            return ""
        try:
            raw = await asyncio.wait_for(proc.stderr.read(4096), timeout=timeout)
        # On Python <=3.10 ``asyncio.wait_for`` raises ``asyncio.TimeoutError``
        # (a ``concurrent.futures.TimeoutError``), which is NOT the builtin
        # ``TimeoutError`` -- so it must be caught explicitly or a routine
        # "no stderr yet" poll escapes and fails relay establish (dotfiles
        # #1549). ``forward.py`` already does this; keep the two in lockstep.
        except (asyncio.TimeoutError, TimeoutError, OSError):
            return ""
        return raw.decode(errors="replace")

    async def _cancel_process(self) -> None:
        proc = self._proc
        if proc is not None:
            await self._kill(proc)
            self._proc = None
            self._notify_pid_change()

    @staticmethod
    async def _drain_stderr(proc: asyncio.subprocess.Process) -> str:
        if proc.stderr is None:
            return ""
        try:
            raw = await asyncio.wait_for(proc.stderr.read(4096), timeout=2.0)
        except (asyncio.TimeoutError, TimeoutError, OSError):
            return ""
        return raw.decode(errors="replace").strip()

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is None:
            await terminate_ssh_process_tree(proc)
        try:
            await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except (asyncio.TimeoutError, TimeoutError, ProcessLookupError):
            pass

    async def _sleep(self, delay: float) -> None:
        await asyncio.sleep(delay)
