"""SSH sync target (and its tunnel variant).

Publishes the source tree to ``<user@host>:<remote_path>/<machine>/`` using
``rsync`` over SSH. The ``ssh-tunnel`` variant routes through a jump host via
``-o ProxyJump=...`` -- generalized from the facility's Cloudflare-tunnel
transport, with no facility-specific hostnames baked in.

rsync is required on both ends. ``doctor`` verifies the local rsync/ssh
binaries and that the host answers a batch-mode SSH probe.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from agent_logger.sync.targets.base import (
    NO_WINDOW_KWARGS,
    DoctorResult,
    PushResult,
    Target,
    rsync_session_filters,
)

_TIMEOUT = 120


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
        remote = f"{host}:{self._remote_path()}/{machine}/"
        ssh_cmd = "ssh " + " ".join(self._ssh_opts())
        cmd = [
            "rsync",
            "-az",
            "--delete",
            *rsync_session_filters(include_sessions),
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
        return PushResult(ok=True, detail=f"-> {remote}")

    def doctor(self) -> DoctorResult:
        result = DoctorResult(ok=True)
        result.add("host configured", bool(self._host()), self._host())
        result.add("rsync present", shutil.which("rsync") is not None, "")
        result.add("ssh present", shutil.which("ssh") is not None, "")
        if self._host() and shutil.which("ssh"):
            try:
                proc = subprocess.run(
                    ["ssh", *self._ssh_opts(), self._host(), "true"],
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
