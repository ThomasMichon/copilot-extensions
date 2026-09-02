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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .config_sources import SSHConfig
from .forward import build_forward_ssh_args
from .process import ssh_subprocess_kwargs, terminate_ssh_process_tree

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
    ``ExitOnForwardFailure`` is
    deliberately omitted so a transient remote bind collision does not make ssh
    exit. Because OpenSSH can then leave the process alive after a failed
    remote ``-R`` bind, ``establish()`` watches stderr during the readiness
    window and retries if that failure is observed.
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
        self._proc: asyncio.subprocess.Process | None = None
        self._monitor_task: asyncio.Task[None] | None = None

    @property
    def is_alive(self) -> bool:
        """Whether the supervised ``ssh -N -R`` process is currently running."""
        return self._proc is not None and self._proc.returncode is None

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
            )
            log.debug(
                "Establishing credential relay reverse-forward "
                "(attempt %d/%d): %s",
                attempt,
                _ESTABLISH_ATTEMPTS,
                " ".join(args),
            )
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                **ssh_subprocess_kwargs(),
            )
            self._proc = proc
            try:
                settled = await self._wait_settled(proc)
            except asyncio.CancelledError:
                await self._kill(proc)
                if self._proc is proc:
                    self._proc = None
                raise
            if settled.ready:
                self._established_host_port = host_port
                log.info(
                    "Credential relay reverse-forward up on %s "
                    "(-R %d:127.0.0.1:%d)",
                    self._config.ssh_target,
                    self._relay_port,
                    host_port,
                )
                return

            stderr = settled.stderr or await self._drain_stderr(proc)
            last_err = stderr or "ssh exited"
            await self._kill(proc)
            if self._proc is proc:
                self._proc = None

            if settled.remote_forward_failed and attempt < _ESTABLISH_ATTEMPTS:
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
        await self.establish()
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(
                self._monitor(), name="ssh-manager-relay-monitor"
            )

    async def stop(self) -> None:
        """Stop monitoring and tear down the relay process (idempotent)."""
        task = self._monitor_task
        self._monitor_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._cancel_process()

    async def _monitor(self) -> None:
        try:
            while True:
                try:
                    await self._sleep(self._monitor_interval)
                    reason = await self._restart_reason()
                    if reason is not None:
                        await self._restart_with_backoff(reason)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("Credential relay monitor iteration failed: %s", exc)
        except asyncio.CancelledError:
            raise

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

    async def _restart_with_backoff(self, reason: str) -> None:
        failures = 0
        while True:
            await self._cancel_process()
            try:
                log.warning(
                    "Credential relay reverse-forward unhealthy (%s); re-establishing",
                    reason,
                )
                await self.establish()
                return
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
        self._proc = None
        if proc is not None:
            await self._kill(proc)

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
