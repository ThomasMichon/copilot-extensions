"""Ingest sync target -- a generic rsync-daemon sink with optional HTTP notify.

This is the shape a bespoke processing service exposes: every machine pushes
its raw sessions to a shared rsync-daemon module (``host::module/path`` or an
``rsync://`` URL), and the service is optionally pinged over HTTP after a
successful push so it can crunch immediately instead of waiting for its poll.

Generalized from the multi-machine system engine's rsync-daemon transport and its
permanent-record notify -- no multi-machine system hostnames, modules, or auth specifics
are baked in.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from agent_logger.sync.detritus import discover_session_detritus
from agent_logger.sync.notify import post_notify
from agent_logger.sync.targets import base as sync_base
from agent_logger.sync.targets.base import (
    NO_WINDOW_KWARGS,
    DoctorResult,
    PushResult,
    SourceIdentityLike,
    Target,
    rsync_session_filters,
)

_TIMEOUT = 180


class IngestTarget(Target):
    """rsync-daemon push plus an optional post-push HTTP notify."""

    name = "ingest"

    def _url(self) -> str:
        """rsync destination: ``rsync://host/module/path`` or ``host::module/path``."""
        return self.options.get("url", "").rstrip("/")

    def _password_file(self) -> str:
        return self.options.get("password_file", "")

    def _notify_url(self) -> str:
        return self.options.get("notify_url", "")

    def _rsync_env(self) -> dict:
        env = dict(os.environ)
        pw = self._password_file()
        if pw:
            env["RSYNC_PASSWORD_FILE"] = pw
        return env

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
                    "identity-admission unsupported for the ingest target: "
                    "no receiver-side atomic admission check exists yet"
                ),
            )
        url = self._url()
        if not url:
            return PushResult(ok=False, detail="ingest target requires a url")
        # ingest speaks rsync's own daemon protocol directly -- it never
        # shells out to ssh, so a WSL distro with rsync but no ssh client
        # must still be eligible.
        runtime = sync_base.resolve_rsync_runtime(require_ssh=False)
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
        dest = f"{url}/{machine}/"
        pw = self._password_file()
        pw_arg = pw
        staged_pw_path: str | None = None
        if pw and runtime.use_wsl:
            # DrvFS (the /mnt/c/... bridge) normally exposes a Windows file
            # as group/world-readable regardless of its Windows ACL; rsync
            # refuses a --password-file with permissions looser than
            # owner-only. Stage a 0600 copy inside WSL's own filesystem
            # instead of just converting the path.
            staged_pw_path = sync_base.stage_wsl_secret_file(pw)
            if staged_pw_path is None:
                return PushResult(
                    ok=False,
                    detail="failed to stage password-file for WSL rsync",
                )
            pw_arg = staged_pw_path
        try:
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
                ]
                if pw:
                    cmd += [f"--password-file={pw_arg}"]
                cmd += [source_arg, dest]
                # The WSL-wrapped process always emits UTF-8; text=True's
                # locale decoder can raise UnicodeDecodeError on non-ASCII
                # diagnostics there, so decode explicitly for that case.
                # rsync diagnostics can also carry arbitrary (non-UTF-8)
                # filename bytes, so use replacement rather than strict
                # decoding -- a malformed byte must never crash the push.
                decode_kwargs = (
                    {"encoding": "utf-8", "errors": "replace"}
                    if runtime.use_wsl
                    else {"text": True}
                )
                try:
                    proc = subprocess.run(
                        cmd,
                        capture_output=True,
                        timeout=_TIMEOUT,
                        env=self._rsync_env(),
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
        finally:
            if staged_pw_path:
                sync_base.cleanup_wsl_staged_file(staged_pw_path)

        self._notify(machine)
        return PushResult(
            ok=True,
            detail=f"-> {dest}",
            excluded_file_count=detritus.file_count,
            excluded_byte_count=detritus.byte_count,
            excluded_roots=tuple(str(root) for root in detritus.roots),
            excluded_measurement_complete=detritus.measurement_complete,
        )

    def _notify(self, machine: str) -> None:
        """Best-effort HTTP ping so the consumer can crunch immediately."""
        post_notify(
            self._notify_url(),
            machine,
            bearer_token_file=self.options.get("bearer_token_file", ""),
            timeout=5,
        )

    def doctor(self) -> DoctorResult:
        result = DoctorResult(ok=True)
        result.add("url configured", bool(self._url()), self._url())
        if sync_base.wsl_rsync_available(require_ssh=False):
            result.add("rsync present", True, "via WSL")
        else:
            result.add("rsync present", shutil.which("rsync") is not None, "")
        pw = self._password_file()
        if pw:
            result.add("password file exists", Path(pw).is_file(), pw)
        return result

    def describe(self) -> str:
        notify = " (+notify)" if self._notify_url() else ""
        return f"{self.name}: {self._url()}/{{machine}}{notify}"
