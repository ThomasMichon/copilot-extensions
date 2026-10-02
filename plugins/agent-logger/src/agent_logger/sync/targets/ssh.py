"""SSH sync target (and its tunnel variant).

Publishes the source tree to ``<user@host>:<remote_path>/<machine>/`` using
``rsync`` over SSH. The ``ssh-tunnel`` variant routes through a jump host via
``-o ProxyJump=...`` -- generalized from the multi-machine system's Cloudflare-tunnel
transport, with no deployment-specific hostnames baked in.

rsync is required on both ends. ``doctor`` verifies the local rsync/ssh
binaries and that the host answers a batch-mode SSH probe.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

from agent_logger.sync.detritus import discover_session_detritus
from agent_logger.sync.targets.base import (
    NO_WINDOW_KWARGS,
    DoctorResult,
    PushResult,
    Target,
    rsync_session_filters,
)

_TIMEOUT = 120
_IS_WINDOWS = platform.system() == "Windows"


def _ssh_executable() -> str:
    """The ``ssh`` binary to pair with the resolved ``rsync``.

    On Windows, an MSYS2/Cygwin-runtime ``rsync.exe`` (the only ``rsync``
    distribution generally available there) that spawns a *different-runtime*
    ``ssh`` for its own ``-e ssh`` child -- e.g. the native Win32 OpenSSH
    client -- corrupts the rsync protocol handshake across that runtime
    boundary: the SSH session itself completes and exchanges bytes, but
    rsync reports "connection unexpectedly closed (0 bytes received so
    far)" because what it reads back was never valid rsync protocol data.
    Preferring a sibling ``ssh`` binary installed alongside the resolved
    ``rsync`` (same bin directory, hence same runtime) avoids the boundary
    crossing entirely. Falls back to a bare ``"ssh"`` (plain ``PATH``
    resolution) when no such sibling exists -- the common POSIX case, and
    any Windows rsync distribution that already ships its own matching ssh
    as the only one on ``PATH``.
    """
    if _IS_WINDOWS:
        rsync_path = shutil.which("rsync")
        if rsync_path:
            sibling = Path(rsync_path).with_name("ssh.exe")
            if sibling.is_file():
                return str(sibling)
    return "ssh"


def _quote_executable(path: str) -> str:
    """Quote *path* for embedding in rsync's ``-e`` command string.

    rsync re-splits ``-e``'s argument on whitespace (respecting simple
    quoting) to build the command it execs, so an unquoted executable path
    containing a space -- e.g. an MSYS2 install under ``C:\\Program
    Files\\...`` -- would otherwise be split into multiple bogus words.
    """
    return f'"{path}"' if " " in path else path


class SshTarget(Target):
    """rsync-over-ssh to an arbitrary ``user@host:path``."""

    name = "ssh"

    def _host(self) -> str:
        return self.options.get("host", "")

    def _remote_path(self) -> str:
        return self.options.get("remote_path", "").rstrip("/")

    def _proxy_jump(self) -> str:
        # ``ssh`` uses proxy_jump directly; ``ssh-tunnel`` reads tunnel_host.
        return self.options.get("proxy_jump") or self.options.get("tunnel_host", "")

    def _ssh_opts(self) -> list[str]:
        opts = ["-o", "BatchMode=yes"]
        jump = self._proxy_jump()
        if jump:
            opts += ["-o", f"ProxyJump={jump}"]
        timeout = int(self.options.get("connect_timeout", 10))
        opts += ["-o", f"ConnectTimeout={timeout}"]
        return opts

    def push(
        self, source: Path, machine: str, include_sessions: set[str] | None = None
    ) -> PushResult:
        host = self._host()
        if not host:
            return PushResult(ok=False, detail="ssh target requires a host")
        if shutil.which("rsync") is None:
            return PushResult(ok=False, detail="rsync not found on PATH")
        try:
            detritus = discover_session_detritus(source, include_sessions)
        except OSError as exc:
            return PushResult(ok=False, detail=f"detritus discovery failed: {exc}")
        remote = f"{host}:{self._remote_path()}/{machine}/"
        ssh_cmd = _quote_executable(_ssh_executable()) + " " + " ".join(self._ssh_opts())
        for _ in range(2):
            cmd = [
                "rsync",
                "-az",
                "--delete",
                *(["--delete-excluded"] if include_sessions is None else []),
                *rsync_session_filters(include_sessions, detritus.roots),
                "-e",
                ssh_cmd,
                f"{source}/",
                remote,
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=_TIMEOUT,
                    check=False,
                    **NO_WINDOW_KWARGS,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return PushResult(ok=False, detail=f"rsync failed: {exc}")
            if proc.returncode != 0:
                return PushResult(ok=False, detail=proc.stderr.strip()[:300])
            try:
                latest = discover_session_detritus(source, include_sessions)
            except OSError as exc:
                return PushResult(
                    ok=False,
                    detail=f"detritus revalidation failed: {exc}",
                )
            if latest.roots == detritus.roots:
                detritus = latest
                break
            detritus = latest
        else:
            return PushResult(
                ok=False,
                detail="source detritus changed during publication; retry",
            )
        return PushResult(
            ok=True,
            detail=f"-> {remote}",
            excluded_file_count=detritus.file_count,
            excluded_byte_count=detritus.byte_count,
            excluded_roots=tuple(str(root) for root in detritus.roots),
            excluded_measurement_complete=detritus.measurement_complete,
        )

    def doctor(self) -> DoctorResult:
        result = DoctorResult(ok=True)
        result.add("host configured", bool(self._host()), self._host())
        result.add("rsync present", shutil.which("rsync") is not None, "")
        ssh_exe = _ssh_executable()
        ssh_present = ssh_exe != "ssh" or shutil.which("ssh") is not None
        result.add("ssh present", ssh_present, "")
        if self._host() and ssh_present:
            try:
                proc = subprocess.run(
                    [ssh_exe, *self._ssh_opts(), self._host(), "true"],
                    capture_output=True,
                    timeout=15,
                    check=False,
                    **NO_WINDOW_KWARGS,
                )
                result.add("ssh reachable", proc.returncode == 0, "")
            except (OSError, subprocess.TimeoutExpired) as exc:
                result.add("ssh reachable", False, str(exc))
        return result

    def describe(self) -> str:
        jump = self._proxy_jump()
        via = f" via {jump}" if jump else ""
        return f"{self.name}: {self._host()}:{self._remote_path()}/{{machine}}{via}"


class SshTunnelTarget(SshTarget):
    """``ssh`` routed through a configured jump host (``tunnel_host``)."""

    name = "ssh-tunnel"

