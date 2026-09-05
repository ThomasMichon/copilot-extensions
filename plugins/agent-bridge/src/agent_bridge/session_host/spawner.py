"""The **Spawner seam** -- the one per-boundary abstraction of the unified
remote-runner design (see the effort's "one Session Host, two seams, one client").

Every "own copilot in a process we don't directly hold" case -- **local**,
**elevated**, **machine-mesh SSH**, **CodeSpace** -- runs the *same* Session Host
component and is driven by the *same* frontend client. The only per-boundary
difference is captured here:

* **how the Host is bootstrapped** on the far side (this module's ``spawn``), and
* **how a local TCP port is made to point at it** (the ``refresh_endpoint``
  closure on the returned :class:`SpawnedHost`).

Because a port-forward makes a remote endpoint look local, the frontend **always**
dials ``127.0.0.1:<local_port>`` and speaks the seq/ack protocol -- there is no
per-boundary transport in the ACP hot path. ``refresh_endpoint`` is what the
liveness-driven reattach driver calls on ``disconnected`` before it redials: a
no-op for a local Host, a re-establish-the-forward for SSH / CodeSpace.

Phase P2a ships this seam with the **local** implementation only (a refactor of
the shipped ``launch_session_host`` path) plus the connect-auth **nonce**;
``ElevatedSpawner`` / ``SshSpawner`` / ``CodeSpaceSpawner`` are additive later
slices that reuse this exact interface.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import secrets
import shlex
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agent_procutil import no_window_flags

from .launcher import launch_session_host

log = logging.getLogger("agent-bridge.session-host.spawner")


class RemoteHostDeadError(RuntimeError):
    """The far side authoritatively reports that a recorded Host is dead."""


class RemoteSpawnCleanupPendingError(RuntimeError):
    """A launched far-side Host could not be confirmed dead after failure."""


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _resolve_provision_command() -> str | None:
    """Resolve the CodeSpace relay/auth-helper provision command.

    Process-boundary **only** (#1643): shell out to the ``agent-codespaces``
    binstub (``provision-command``) so the command comes from agent-codespaces'
    **own** venv -- a fix there reaches the dispatch path with **no agent-bridge
    redeploy** (retires the #733 class). There is **no** in-process
    ``agent_codespaces`` import fallback: the daemon runs from its own isolated
    venv where a provider package is neither importable nor on ``PATH``. Returns
    the command string, or ``None`` when unavailable (binstub absent or the CLI
    fails -- the caller skips the best-effort step).
    """
    import shutil
    import subprocess

    binstub = shutil.which("agent-codespaces")  # marketplace-isolation: allow provider-management
    if not binstub:
        log.debug(
            "agent-codespaces binstub absent -- skipping relay-helper redeploy "
            "on the dispatch path"
        )
        return None
    try:
        r = subprocess.run(
            [binstub, "provision-command"],
            capture_output=True, text=True, timeout=15,
            creationflags=no_window_flags(),
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
        log.debug(
            "agent-codespaces provision-command exited %s -- skipping "
            "relay-helper redeploy", r.returncode,
        )
    except Exception:
        log.debug(
            "agent-codespaces provision-command CLI failed -- skipping "
            "relay-helper redeploy", exc_info=True,
        )
    return None


@dataclass
class SpawnedHost:
    """A launched Session Host + everything the frontend needs to reach it.

    ``local_port`` is always a loopback port on *this* machine (directly bound
    for a local/elevated Host, or the near end of a forward for a remote one).
    ``nonce`` is the connect-auth token to present on ATTACH. ``refresh_endpoint``
    re-establishes the local port before a reattach redial (no-op for local).
    ``endpoint`` is the durable, JSON-serializable descriptor a restarted
    frontend uses to re-forward from :class:`~..session_host.host_index.HostIndex`
    alone (no live Spawner needed) -- empty for a local Host whose port never
    moves. ``forward`` retains a live ``-L`` process so it is not GC'd. ``relay``
    retains any dedicated credential-relay ``-R`` supervisors for teardown with
    the Host/child lifetime.
    """

    local_port: int
    host_pid: int
    child_pid: int
    protocol_version: int
    boundary: str = "local"
    nonce: str = ""
    state_file: str = ""
    proc: Any = None
    endpoint: dict = field(default_factory=dict)
    forward: Any = None
    relay: Any = None
    _refresh: Callable[[], Awaitable[None]] | None = None

    async def refresh_endpoint(self) -> None:
        """Re-point ``local_port`` at the Host before a reattach redial.

        No-op for a same-machine (local/elevated) Host whose port never moves;
        for a forwarded (SSH/CodeSpace) Host this re-establishes the ``-L``
        forward after a transport drop.
        """
        if self._refresh is not None:
            await self._refresh()

    async def aclose(self) -> None:
        """Best-effort teardown of owned forwards."""
        for relay in _as_list(self.relay):
            with contextlib.suppress(Exception):
                await relay.stop()
        if self.forward is not None:
            with contextlib.suppress(Exception):
                await self.forward.cancel()


@runtime_checkable
class HostSpawner(Protocol):
    """Bootstraps a Session Host across one boundary and returns how to reach it.

    Implementations differ only in *where* the Host runs and *how* a local port
    is wired to it; the frontend client that drives the returned host is the same
    for all of them.
    """

    boundary: str

    async def spawn(
        self,
        child_argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        session_id: str = "",
    ) -> SpawnedHost:
        ...


def new_nonce() -> str:
    """A fresh connect-auth nonce (URL-safe, 32 hex chars)."""
    return secrets.token_hex(16)


def _safe_session_id(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "session")[:48]


class LocalSpawner:
    """Spawn a survivable Session Host on **this** machine (the shipped path).

    Wraps :func:`launch_session_host` (run off the event loop) and mints a
    per-Host connect nonce so a stray same-user process cannot drive the child
    by dialing the loopback port. The endpoint is a direct loopback port, so
    ``refresh_endpoint`` is a no-op.
    """

    boundary = "local"

    def __init__(self, *, unexpected_reap_seconds: float = 60.0,
                 active_reap_seconds: float = 0.0,
                 ready_timeout: float = 90.0) -> None:
        # Bound on how long an idle, front-less child lingers after an unexpected
        # disconnect before the host self-reaps it (#48). Handed to the launched
        # host process. 0 disables the unexpected-grace self-reap.
        self._unexpected_reap_seconds = unexpected_reap_seconds
        # Bound on how long an ACTIVE (mid-turn) front-less child is held after
        # an unexpected disconnect before the host lets it go (#145). 0 disables.
        self._active_reap_seconds = active_reap_seconds
        # Bound on how long we wait for the launched host process to bind its
        # loopback port + write its state file (its own cold start), NOT the
        # ACP handshake that follows. Wired from timeouts.session_host_ready so a
        # heavy/elevated singleton launch does not spuriously fail LAUNCH_ACP on
        # a tight 30s budget.
        self._ready_timeout = ready_timeout

    async def spawn(
        self,
        child_argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        session_id: str = "",
    ) -> SpawnedHost:
        from .. import __version__

        nonce = new_nonce()
        handle = await asyncio.to_thread(
            launch_session_host, child_argv, cwd=cwd, env=env, nonce=nonce,
            session_id=session_id, host_version=__version__,
            ready_timeout=self._ready_timeout,
            unexpected_reap_seconds=self._unexpected_reap_seconds,
            active_reap_seconds=self._active_reap_seconds,
        )
        return SpawnedHost(
            local_port=handle.port,
            host_pid=handle.host_pid,
            child_pid=handle.child_pid,
            protocol_version=handle.protocol_version,
            boundary=self.boundary,
            nonce=nonce,
            state_file=handle.state_file,
            proc=handle.proc,
            _refresh=None,
        )


# Env var carrying the connect-auth nonce to the remote host process (kept off
# the command line so it does not leak to ``ps``). Mirrors launcher._NONCE_ENV.
_NONCE_ENV = "AGENT_BRIDGE_SESSION_HOST_NONCE"


@runtime_checkable
class RemoteTransport(Protocol):
    """Far-side operations a remote (CodeSpace / mesh) Spawner needs.

    A concrete transport knows how to move a file to the far side, run a shell
    command there, and describe the SSH config a local ``-L`` forward should use
    to reach it. Everything boundary-specific (``gh codespace cp`` vs ``scp``,
    ``agent-codespaces ssh`` vs ssh-manager exec) lives behind this seam; the
    Spawner orchestration below is transport-agnostic.
    """

    boundary: str

    async def push_file(self, local_path: str, remote_path: str) -> None:
        """Copy a local file to ``remote_path`` on the far side."""
        ...

    async def path_exists(self, remote_path: str) -> bool:
        """True if ``remote_path`` already exists on the far side."""
        ...

    async def run(
        self, command: str, *, timeout: float = 60.0,
    ) -> tuple[int, str, str]:
        """Run a shell command on the far side; return ``(rc, stdout, stderr)``."""
        ...

    def ssh_config(self) -> Any:
        """The :class:`ssh_manager.SSHConfig` a ``-L`` forward should dial."""
        ...


def build_remote_launch(
    bundle_remote: str,
    state_remote: str,
    log_remote: str,
    child_argv: list[str],
    *,
    nonce: str = "",
    cwd: str | None = None,
    session_id: str = "",
    host_version: str = "",
    reverse_forwards: list[str] | None = None,
    unexpected_reap_seconds: float = 60.0,
    active_reap_seconds: float = 0.0,
) -> str:
    """Assemble the far-side bash command that launches a survivable Host.

    ``setsid nohup … </dev/null &`` detaches the Host from the launch SSH channel
    so it **outlives the channel closing** (the POSIX survival seam, validated in
    the #145 live proof), while ``PR_SET_PDEATHSIG`` inside the Host still ties
    the copilot child's life to the Host. The nonce rides in via the env (off the
    command line). ``unexpected_reap_seconds`` / ``active_reap_seconds`` bound how
    long a front-less idle / active child is held before the detached Host lets
    it go (so a reconnecting front can resume). Paths are POSIX (the far side is
    Linux).
    """
    import posixpath

    state_dir = posixpath.dirname(state_remote)
    dirs = sorted({
        posixpath.dirname(p)
        for p in (state_remote, log_remote)
        if posixpath.dirname(p)
    })
    prep = ""
    if dirs:
        prep = (
            "mkdir -p "
            + " ".join(shlex.quote(d) for d in dirs)
            + " || exit 1; "
        )
    if state_dir:
        prep += (
            f"chmod 700 {shlex.quote(state_dir)} || exit 1; "
            f"rm -f {shlex.quote(state_remote)} || exit 1; "
        )
    host_cmd = (
        f"python3 {shlex.quote(bundle_remote)} --port 0 "
        f"--state-file {shlex.quote(state_remote)} "
        f"--unexpected-reap-seconds {unexpected_reap_seconds} "
        f"--active-reap-seconds {active_reap_seconds} "
    )
    if session_id:
        host_cmd += f"--session-id {shlex.quote(session_id)} "
    if host_version:
        host_cmd += f"--host-version {shlex.quote(host_version)} "
    for spec in reverse_forwards or []:
        host_cmd += f"--reverse-forward {shlex.quote(spec)} "
    if cwd:
        host_cmd += f"--cwd {shlex.quote(cwd)} "
    host_cmd += "-- " + " ".join(shlex.quote(a) for a in child_argv)
    env_prefix = f"{_NONCE_ENV}={shlex.quote(nonce)} " if nonce else ""
    launch = (
        f"{env_prefix}setsid nohup {host_cmd} "
        f"</dev/null >{shlex.quote(log_remote)} 2>&1 & echo launched"
    )
    return f"bash -lc {shlex.quote(prep + launch)}"


class CodeSpaceSpawner:
    """Bootstrap a survivable Session Host on the far side of a remote boundary.

    Ships the content-hashed host bundle (cache-hit skips re-shipping), launches
    it detached on the far side, reads back the remote port from the Host's state
    file, and stands up an ``ssh -N -L`` forward so the frontend dials
    ``127.0.0.1:<local_port>`` exactly as for a local Host. ``refresh_endpoint``
    re-establishes the forward after a transport drop; ``endpoint`` captures how
    to rebuild it from the host index alone after a frontend restart.

    Boundary-agnostic given a :class:`RemoteTransport`; named for its first
    consumer (CodeSpaces). The mesh ``SshSpawner`` is the same class with an
    ssh-manager-backed transport.
    """

    def __init__(
        self,
        transport: RemoteTransport,
        *,
        remote_dir: str = "/tmp/agent-bridge",  # noqa: S108 -- remote CS path, not a local temp
        reverse_forwards: list[str] | None = None,
        ready_timeout: float = 90.0,
        launch_timeout: float = 60.0,
        unexpected_reap_seconds: float = 60.0,
        active_reap_seconds: float = 0.0,
        require_relay_ready: bool = False,
        relay_ready_timeout: float = 5.0,
    ) -> None:
        self._transport = transport
        self.boundary = getattr(transport, "boundary", "codespace")
        self._remote_dir = remote_dir.rstrip("/")
        self._reverse_forwards = list(reverse_forwards or [])
        self._ready_timeout = ready_timeout
        self._launch_timeout = launch_timeout
        self._unexpected_reap_seconds = unexpected_reap_seconds
        self._active_reap_seconds = active_reap_seconds
        self._require_relay_ready = require_relay_ready
        self._relay_ready_timeout = relay_ready_timeout

    @property
    def transport(self) -> RemoteTransport:
        """Remote execution seam used by shared pre-launch policy."""
        return self._transport

    async def spawn(
        self,
        child_argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        session_id: str = "",
    ) -> SpawnedHost:
        from ssh_manager import LocalForward

        from .. import __version__
        from . import protocol as proto
        from .bundle import build_session_host_bundle
        from .endpoints import (
            CredentialRelayReadinessError,
            endpoint_from_ssh_config,
            relay_forwards_from_ssh_config,
            relay_ports_from_reverse_forwards,
            wait_for_relay_serving,
        )

        nonce = new_nonce()
        bundle_path, _sha = await asyncio.to_thread(build_session_host_bundle)
        remote_bundle = f"{self._remote_dir}/{bundle_path.name}"
        # Cache by content hash: only ship when the far side lacks this bundle.
        if not await self._transport.path_exists(remote_bundle):
            await self._transport.push_file(str(bundle_path), remote_bundle)

        # Import the staged archive with site-packages disabled before starting
        # it detached. This catches an incomplete bundle immediately instead of
        # waiting the full readiness timeout for a process that already crashed.
        preflight = (
            f"python3 -S {shlex.quote(remote_bundle)} --help >/dev/null"
        )
        preflight_rc, preflight_out, preflight_err = await self._transport.run(
            preflight, timeout=30.0,
        )
        if preflight_rc != 0:
            raise RuntimeError(
                "remote Session Host bundle preflight failed "
                f"(rc={preflight_rc}): {preflight_err or preflight_out}"
            )

        # Re-assert the CodeSpace-side ADO/git auth helpers before launching the
        # dispatched agent (dotfiles #733 T2). The interactive `agent-codespaces
        # ssh` connect path deploys these via Stage-4 `_provision_relay_helpers`,
        # but the Session-Host DISPATCH path bypasses that codepath -- so without
        # this a dispatched agent inherits whatever helper the box already has.
        # After a CodeSpace reboot that is the VS Code extension's own helper,
        # whose shebang points at a since-deleted node (`bad interpreter`),
        # breaking `git push`/PR to ADO. Overwrite it with our stable-shebang,
        # relay-first wrapper. Codespace boundary only; idempotent + best-effort
        # (a failure here must never block the launch -- the launch's own auth
        # verification surfaces a genuinely broken relay).
        if self.boundary == "codespace":
            provision_cmd = await asyncio.to_thread(_resolve_provision_command)
            if provision_cmd:
                try:
                    prov_rc, _pout, prov_err = await self._transport.run(
                        provision_cmd, timeout=30.0,
                    )
                    if prov_rc != 0:
                        log.warning(
                            "CodeSpace relay-helper (re)deploy exited %s: %s",
                            prov_rc, (prov_err or "").strip(),
                        )
                except Exception:
                    log.warning(
                        "CodeSpace relay-helper (re)deploy failed (dispatch path)",
                        exc_info=True,
                    )

        safe_sid = _safe_session_id(session_id)
        ts = int(time.time() * 1000)
        state_remote = await self._authority_path(session_id)
        log_remote = f"{self._remote_dir}/host-{safe_sid}-{ts}.log"
        reverse = list(self._reverse_forwards)
        get_reverse = getattr(self._transport, "reverse_forwards", None)
        if callable(get_reverse):
            reverse += list(get_reverse() or [])

        launch = build_remote_launch(
            remote_bundle, state_remote, log_remote, child_argv,
            nonce=nonce, cwd=cwd, session_id=session_id,
            host_version=__version__, reverse_forwards=reverse,
            unexpected_reap_seconds=self._unexpected_reap_seconds,
            active_reap_seconds=self._active_reap_seconds,
        )
        rc, out, err = await self._transport.run(
            launch, timeout=self._launch_timeout,
        )
        if rc != 0:
            raise RuntimeError(
                f"remote Session Host launch failed (rc={rc}): {err or out}"
            )

        try:
            state = await self._poll_state(
                state_remote,
                log_remote,
                session_id=session_id,
                nonce=nonce,
            )
        except Exception as exc:
            if not await self._abort_remote_launch(state_remote, session_id):
                raise RemoteSpawnCleanupPendingError(
                    f"remote Session Host launch failed and cleanup is "
                    f"inconclusive for {session_id}; retaining target ownership"
                ) from exc
            raise
        remote_port = int(state["port"])
        host_pid = int(state["pid"])
        child_pid = int(state["child_pid"])
        protocol_version = int(state.get("protocol_version", proto.PROTOCOL_VERSION))

        config = self._transport.ssh_config()
        # Also allow the transport to contribute reverse-forwards (e.g. the
        # credential relay) so a detached far-side Host that outlives its launch
        # channel keeps a live relay for the whole session (rush build / ADO).
        forward = LocalForward(config, remote_port)
        try:
            local_port = await forward.establish()
        except Exception as exc:
            with contextlib.suppress(Exception):
                await forward.cancel()
            if not await self._abort_remote_launch(state_remote, session_id):
                raise RemoteSpawnCleanupPendingError(
                    f"remote Session Host forwarding failed and cleanup is "
                    f"inconclusive for {session_id}; retaining target ownership"
                ) from exc
            raise
        from ..relay_state import get_live_relay_port
        relays = relay_forwards_from_ssh_config(
            config,
            reverse,
            serving_probe_for_port=self._serving_probe_for_port,
            host_port_resolver=get_live_relay_port,
        )
        relay_ports = relay_ports_from_reverse_forwards(reverse)
        started_relays = []
        for relay, relay_port in zip(relays, relay_ports, strict=True):
            try:
                await relay.start()
                if self._require_relay_ready:
                    try:
                        await wait_for_relay_serving(
                            self._serving_probe_for_port(
                                relay_port,
                                fail_open=False,
                            ),
                            timeout=self._relay_ready_timeout,
                        )
                    except Exception as exc:
                        raise CredentialRelayReadinessError(
                            "credential relay readiness probe failed for "
                            f"remote loopback port {relay_port}"
                        ) from exc
            except Exception as exc:
                with contextlib.suppress(Exception):
                    await relay.stop()
                if self._require_relay_ready:
                    for prior in started_relays:
                        with contextlib.suppress(Exception):
                            await prior.stop()
                    with contextlib.suppress(Exception):
                        await forward.cancel()
                    if not await self._abort_remote_launch(
                        state_remote, session_id,
                    ):
                        raise RemoteSpawnCleanupPendingError(
                            "required credential relay failed and remote "
                            f"Session Host cleanup is inconclusive for {session_id}; "
                            "retaining target ownership"
                        ) from exc
                    if isinstance(exc, CredentialRelayReadinessError):
                        raise
                    raise CredentialRelayReadinessError(
                        "credential relay reverse-forward failed before ACP "
                        f"startup for session {session_id}"
                    ) from exc
                log.warning(
                    "Credential relay supervisor failed to start for "
                    "session %s (boundary=%s); continuing auth-light",
                    session_id, self.boundary, exc_info=True,
                )
                continue
            started_relays.append(relay)

        extra = {}
        get_extra = getattr(self._transport, "endpoint_extra", None)
        if callable(get_extra):
            extra = get_extra() or {}
        endpoint = endpoint_from_ssh_config(
            config, remote_port, local_port, kind=self.boundary,
            reverse_forwards=reverse, extra=extra,
        )

        async def _refresh() -> None:
            await forward.refresh()

        log.info(
            "CodeSpace Session Host up: session=%s boundary=%s "
            "local=127.0.0.1:%d -> remote:%d (host_pid=%s child_pid=%s)",
            session_id, self.boundary, local_port, remote_port,
            host_pid, child_pid,
        )
        return SpawnedHost(
            local_port=local_port,
            host_pid=host_pid,
            child_pid=child_pid,
            protocol_version=protocol_version,
            boundary=self.boundary,
            nonce=nonce,
            state_file=state_remote,
            endpoint=endpoint,
            forward=forward,
            relay=started_relays,
            _refresh=_refresh,
        )

    async def _abort_remote_launch(
        self,
        state_remote: str,
        session_id: str,
    ) -> bool:
        """Identity-check and reap a Host after post-launch setup fails."""
        script = f"""
import json
import os
import signal
import time

path = {state_remote!r}
session_id = {session_id!r}
try:
    with open(path, encoding="utf-8") as handle:
        state = json.load(handle)
except FileNotFoundError:
    print("__ABSENT__")
    raise SystemExit(0)

if state.get("session_id") != session_id:
    raise SystemExit(43)
host_pid = int(state["host_pid"])
child_pid = int(state["child_pid"])
boot_id = str(state["boot_id"])
host_start = str(state["host_start_ticks"])
child_start = str(state["child_start_ticks"])

def start_ticks(pid):
    try:
        return open(f"/proc/{{pid}}/stat", encoding="utf-8").read().split()[21]
    except (FileNotFoundError, IndexError, OSError):
        return ""

try:
    current_boot = open(
        "/proc/sys/kernel/random/boot_id", encoding="utf-8"
    ).read().strip()
except OSError:
    raise SystemExit(44)
if current_boot != boot_id:
    raise SystemExit(45)

host_current = start_ticks(host_pid)
child_current = start_ticks(child_pid)
if host_current and host_current != host_start:
    raise SystemExit(46)
if child_current and child_current != child_start:
    raise SystemExit(47)

for sig in (signal.SIGTERM, signal.SIGKILL):
    if start_ticks(host_pid) == host_start:
        try:
            os.killpg(host_pid, sig)
        except ProcessLookupError:
            pass
    for pid, expected in ((host_pid, host_start), (child_pid, child_start)):
        if start_ticks(pid) != expected:
            continue
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
    time.sleep(0.25)
if (
    start_ticks(host_pid) == host_start
    or start_ticks(child_pid) == child_start
):
    raise SystemExit(42)
try:
    os.unlink(path)
except FileNotFoundError:
    pass
print("__REAPED__")
"""
        command = f"python3 -c {shlex.quote(script)}"
        try:
            rc, out, _err = await self._transport.run(
                command,
                timeout=30.0,
            )
        except Exception:
            log.warning(
                "Could not clean a partially launched remote Session Host for %s",
                session_id,
                exc_info=True,
            )
            return False
        confirmed = (
            rc == 0
            and (
                "__REAPED__" in (out or "")
                or "__ABSENT__" in (out or "")
            )
        )
        if not confirmed:
            log.warning(
                "Partial remote Session Host cleanup was inconclusive for %s "
                "(rc=%s, output=%r)",
                session_id,
                rc,
                out,
            )
        return confirmed

    async def abort_spawned(
        self,
        spawned: SpawnedHost,
        session_id: str,
    ) -> bool:
        """Reap one launched remote Host and remove its authority record."""
        return await self._abort_remote_launch(
            spawned.state_file,
            session_id,
        )

    async def _authority_path(self, session_id: str) -> str:
        get_home = getattr(self._transport, "home_dir", None)
        if callable(get_home):
            home = await get_home()
            return (
                f"{home}/.agent-bridge/session-hosts/"
                f"host-{_safe_session_id(session_id)}.json"
            )
        return (
            f"{self._remote_dir}/hosts/"
            f"host-{_safe_session_id(session_id)}.json"
        )

    async def can_inspect_without_wake(self) -> bool:
        is_running = getattr(self._transport, "is_running", None)
        if callable(is_running):
            return bool(await is_running())
        return True

    async def recover_record(self, session_id: str):
        """Rebuild a HostRecord from the far-side authority file.

        Used when the frontend DB survived but its local HostIndex did not. The
        record is adopted only after the far side confirms both host and child
        PIDs are alive; transport failure is inconclusive and leaves recovery
        for a later attempt.
        """
        from .endpoints import endpoint_from_ssh_config
        from .host_index import HostRecord

        state_remote = await self._authority_path(session_id)
        state_q = shlex.quote(state_remote)
        rc, out, err = await self._transport.run(
            f"if test -f {state_q}; then cat {state_q}; "
            "else printf __MISSING__; fi",
            timeout=30.0,
        )
        if rc != 0:
            raise ConnectionError(
                f"Remote Session Host authority read failed for {session_id} "
                f"(rc={rc}): {err or out}"
            )
        if (out or "").strip() == "__MISSING__":
            log.info(
                "No remote Session Host authority record for %s",
                session_id,
            )
            return None
        if not out:
            raise ConnectionError(
                f"Remote Session Host authority read was empty for {session_id}"
            )
        try:
            state = json.loads(out)
            recorded_session = str(state["session_id"])
            host_pid = int(state.get("host_pid", state["pid"]))
            child_pid = int(state["child_pid"])
            remote_port = int(state["port"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ConnectionError(
                f"Remote Session Host authority record is invalid for {session_id}"
            )
        state_name = str(state.get("state") or "")
        if (
            int(state.get("version", 0)) != 2
            or recorded_session != session_id
            or host_pid <= 1
            or child_pid <= 1
            or remote_port <= 0
        ):
            raise ConnectionError(
                "Remote Session Host authority mismatch for %s (version=%s, "
                "recorded=%s, secured=%s, host_pid=%s, child_pid=%s, port=%s)"
                % (
                    session_id,
                    state.get("version"),
                    recorded_session,
                    bool(state.get("nonce")),
                    host_pid,
                    child_pid,
                    remote_port,
                )
            )
        if state_name not in {"running", "stopped", "child_exited"}:
            raise ConnectionError(
                f"Remote Session Host state is unknown for {session_id}: "
                f"{state_name!r}"
            )
        nonce = str(state.get("nonce") or "")
        if state_name == "running" and not nonce:
            raise ConnectionError(
                f"Running remote Session Host authority is unsecured for "
                f"{session_id}"
            )

        boot_id = str(state.get("boot_id") or "")
        host_start = str(state.get("host_start_ticks") or "")
        child_start = str(state.get("child_start_ticks") or "")
        if not boot_id or not host_start or not child_start:
            raise ConnectionError(
                f"Remote Session Host authority lacks process identity for "
                f"{session_id}"
            )
        alive_cmd = (
            f"b=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true); "
            f"h=$(awk '{{print $22}}' /proc/{host_pid}/stat 2>/dev/null || true); "
            f"c=$(awk '{{print $22}}' /proc/{child_pid}/stat 2>/dev/null || true); "
            f"test \"$b\" = {shlex.quote(boot_id)} && "
            f"test \"$h\" = {shlex.quote(host_start)} && hv=1 || hv=0; "
            f"test \"$b\" = {shlex.quote(boot_id)} && "
            f"test \"$c\" = {shlex.quote(child_start)} && cv=1 || cv=0; "
            'printf "%s:%s" "$hv" "$cv"'
        )
        async def _probe_liveness() -> tuple[bool, bool, str]:
            alive_rc, alive_out, _alive_err = await self._transport.run(
                alive_cmd,
                timeout=30.0,
            )
            liveness_text = (alive_out or "").strip()
            if (
                alive_rc != 0
                or liveness_text not in {"0:0", "0:1", "1:0", "1:1"}
            ):
                raise ConnectionError(
                    f"Remote Session Host liveness probe was inconclusive for "
                    f"{session_id} (rc={alive_rc}, output={alive_out!r})"
                )
            host_flag, child_flag = liveness_text.split(":", 1)
            return host_flag == "1", child_flag == "1", liveness_text

        host_alive, child_alive, liveness_text = await _probe_liveness()
        should_reap = (
            state_name == "stopped"
            or not host_alive
            or (not child_alive and state_name != "child_exited")
        )
        if should_reap and (host_alive or child_alive):
            # Any asymmetric survivor is unsafe: a child without its Host is
            # unreachable, and a Host without its child can never serve ACP.
            # Process identity was proven above, so reap the recorded group and
            # both individual PIDs, then re-probe before declaring death.
            cleanup = (
                f"kill -TERM -- -{host_pid} 2>/dev/null || "
                f"kill -TERM {host_pid} 2>/dev/null || true; "
                f"kill -TERM {child_pid} 2>/dev/null || true; "
                "sleep 0.2; "
                f"kill -KILL -- -{host_pid} 2>/dev/null || "
                f"kill -KILL {host_pid} 2>/dev/null || true; "
                f"kill -KILL {child_pid} 2>/dev/null || true"
            )
            cleanup_rc, _cleanup_out, cleanup_err = await self._transport.run(
                cleanup,
                timeout=30.0,
            )
            if cleanup_rc != 0:
                raise ConnectionError(
                    f"Remote Session Host cleanup failed for {session_id}: "
                    f"{cleanup_err}"
                )
            host_alive, child_alive, liveness_text = await _probe_liveness()
            if host_alive or child_alive:
                raise ConnectionError(
                    f"Remote Session Host cleanup could not verify death for "
                    f"{session_id} (liveness={liveness_text})"
                )
            log.warning(
                "Reaped asymmetric remote Session Host tree (host=%s child=%s "
                "session=%s)",
                host_pid,
                child_pid,
                session_id,
            )
        if should_reap:
            log.info(
                "Remote Session Host authority record for %s is not live "
                "(host_pid=%s child_pid=%s liveness=%s state=%s)",
                session_id,
                host_pid,
                child_pid,
                liveness_text,
                state.get("state"),
            )
            raise RemoteHostDeadError(
                f"Remote Session Host is dead for {session_id} "
                f"(host_pid={host_pid}, child_pid={child_pid})"
            )

        reverse = [
            str(spec)
            for spec in (state.get("reverse_forwards") or [])
            if spec
        ]
        config = self._transport.ssh_config()
        extra = {}
        get_extra = getattr(self._transport, "endpoint_extra", None)
        if callable(get_extra):
            extra = get_extra() or {}
        endpoint = endpoint_from_ssh_config(
            config,
            remote_port,
            0,
            kind=self.boundary,
            reverse_forwards=reverse,
            extra=extra,
        )
        return HostRecord(
            session_id=session_id,
            port=0,
            host_pid=host_pid,
            child_pid=child_pid,
            host_version=str(state.get("host_version") or ""),
            protocol_version=int(state.get("protocol_version", 1)),
            state_file=state_remote,
            created_at=float(state.get("created_at") or 0.0),
            nonce=nonce,
            boundary=self.boundary,
            endpoint=endpoint,
            extra={
                "recovered_from_remote": True,
                "remote_authority_v2": True,
                "child_executable": str(state.get("child_executable") or ""),
                "cwd": str(state.get("cwd") or ""),
            },
        )

    def _serving_probe_for_port(
        self,
        relay_port: int,
        *,
        fail_open: bool = True,
    ) -> Callable[[], Awaitable[bool]]:
        """Build a best-effort far-side relay-serving probe for Session-Host use.

        A false result means the SSH process is alive but the CodeSpace-side
        relay listener at ``relay_port`` did not answer a real relay-protocol
        round-trip (see :func:`build_relay_ping_probe_command`) -- e.g. it is
        not accepting connections, or something is accepting but not actually
        speaking the relay protocol (a stale/misrouted listener) -- so the
        relay supervisor should re-establish. Probe transport failures return
        True: the probe is a health hint, not a reason to churn an otherwise
        live relay during a transient command-channel failure.
        """

        async def _probe() -> bool:
            from .endpoints import build_relay_ping_probe_command

            probe = build_relay_ping_probe_command(relay_port)
            try:
                rc, out, _err = await self._transport.run(
                    f"bash -lc {shlex.quote(probe)}",
                    timeout=5.0,
                )
            except Exception:
                if fail_open:
                    return True
                raise
            return rc == 0 and "OK" in (out or "")

        return _probe

    async def _poll_state(
        self,
        state_remote: str,
        log_remote: str,
        *,
        session_id: str,
        nonce: str,
    ) -> dict[str, Any]:
        """Poll the far-side state file until the Host reports port + child."""
        deadline = time.time() + self._ready_timeout
        cmd = f"cat {shlex.quote(state_remote)} 2>/dev/null || true"
        while time.time() < deadline:
            _rc, out, _err = await self._transport.run(cmd, timeout=15.0)
            out = (out or "").strip()
            if out:
                try:
                    data = json.loads(out)
                except json.JSONDecodeError:
                    data = {}
                if (
                    data.get("port")
                    and data.get("child_pid")
                    and data.get("session_id") == session_id
                    and data.get("nonce") == nonce
                ):
                    return data
            await asyncio.sleep(0.5)
        # Surface the Host's own log to explain a launch that never got ready.
        tail = ""
        try:
            _rc, tail, _e = await self._transport.run(
                f"tail -n 40 {shlex.quote(log_remote)} 2>/dev/null || true",
                timeout=15.0,
            )
        except Exception:
            pass  # best-effort diagnostics only
        raise TimeoutError(
            f"remote Session Host did not report ready within "
            f"{self._ready_timeout}s (state={state_remote}); log tail:\n{tail}"
        )
