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
from agent_logger.sync.targets import base as sync_base
from agent_logger.sync.targets.base import (
    NO_WINDOW_KWARGS,
    DoctorResult,
    PushResult,
    SourceIdentityLike,
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
    Files\\...`` -- would otherwise be split into multiple bogus words. An
    apostrophe needs the same treatment: even with no space, rsync's parser
    treats it as an opening quote and rejects the command for having no
    closing one (e.g. ``C:\\Users\\O'Brien\\...``).
    """
    return f'"{path}"' if (" " in path or "'" in path) else path


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
        self,
        source: Path,
        machine: str,
        include_sessions: set[str] | None = None,
        *,
        batch_mode: bool = False,
        source_identity: SourceIdentityLike | None = None,
    ) -> PushResult:
        if source_identity is not None:
            return PushResult(
                ok=False,
                detail=(
                    "identity-admission unsupported for the ssh target: no "
                    "receiver-side atomic admission check exists yet"
                ),
            )
        host = self._host()
        if not host:
            return PushResult(ok=False, detail="ssh target requires a host")
        runtime = sync_base.resolve_rsync_runtime()
        if not runtime.use_wsl and shutil.which("rsync") is None:
            return PushResult(ok=False, detail="rsync not found on PATH")
        try:
            detritus = discover_session_detritus(source, include_sessions)
        except OSError as exc:
            return PushResult(ok=False, detail=f"detritus discovery failed: {exc}")
        source_arg = runtime.source_arg(source)
        if source_arg is None:
            return PushResult(
                ok=False, detail="failed to convert source path for WSL rsync"
            )
        remote = f"{host}:{self._remote_path()}/{machine}/"
        # A WSL-wrapped rsync runs ssh inside the same WSL runtime -- a bare
        # "ssh" there is WSL's own, never a cross-runtime mismatch -- so the
        # sibling-ssh resolution below only applies to the native-Windows
        # fallback (no WSL available).
        ssh_exe = "ssh" if runtime.use_wsl else _ssh_executable()
        ssh_cmd = _quote_executable(ssh_exe) + " " + " ".join(self._ssh_opts())
        for _ in range(2):
            cmd = [
                *runtime.command_prefix,
                "rsync",
                "-az",
                "--delete",
                *(["--delete-excluded"] if include_sessions is None else []),
                *rsync_session_filters(
                    include_sessions, detritus.roots, batch_mode=batch_mode
                ),
                "-e",
                ssh_cmd,
                source_arg,
                remote,
            ]
            try:
                # The WSL-wrapped process always emits UTF-8; text=True's
                # locale decoder can raise UnicodeDecodeError on non-ASCII
                # diagnostics there, so decode explicitly for that case.
                # rsync/ssh diagnostics can also carry arbitrary (non-UTF-8)
                # filename bytes, so use replacement rather than strict
                # decoding -- a malformed byte must never crash the push.
                decode_kwargs = (
                    {"encoding": "utf-8", "errors": "replace"}
                    if runtime.use_wsl
                    else {"text": True}
                )
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=_TIMEOUT,
                    check=False,
                    **decode_kwargs,
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
        use_wsl = sync_base.wsl_rsync_available()
        if use_wsl:
            result.add("rsync present", True, "via WSL")
            ssh_exe = "ssh"
            ssh_cmd = ["wsl.exe", "-e", ssh_exe]
            ssh_present = True
            result.add("ssh present", True, "via WSL")
        else:
            result.add("rsync present", shutil.which("rsync") is not None, "")
            ssh_exe = _ssh_executable()
            ssh_cmd = [ssh_exe]
            ssh_present = ssh_exe != "ssh" or shutil.which("ssh") is not None
            result.add("ssh present", ssh_present, "")
        if self._host() and ssh_present:
            try:
                proc = subprocess.run(
                    [*ssh_cmd, *self._ssh_opts(), self._host(), "true"],
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

