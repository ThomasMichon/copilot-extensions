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
from agent_logger.sync.targets.base import (
    NO_WINDOW_KWARGS,
    DoctorResult,
    PushResult,
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
        self, source: Path, machine: str, include_sessions: set[str] | None = None
    ) -> PushResult:
        url = self._url()
        if not url:
            return PushResult(ok=False, detail="ingest target requires a url")
        if shutil.which("rsync") is None:
            return PushResult(ok=False, detail="rsync not found on PATH")
        try:
            detritus = discover_session_detritus(source, include_sessions)
        except OSError as exc:
            return PushResult(ok=False, detail=f"detritus discovery failed: {exc}")
        dest = f"{url}/{machine}/"
        pw = self._password_file()
        for _ in range(2):
            cmd = [
                "rsync",
                "-az",
                "--delete",
                *(["--delete-excluded"] if include_sessions is None else []),
                *rsync_session_filters(include_sessions, detritus.roots),
            ]
            if pw:
                cmd += [f"--password-file={pw}"]
            cmd += [f"{source}/", dest]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=_TIMEOUT,
                    env=self._rsync_env(),
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
        result.add("rsync present", shutil.which("rsync") is not None, "")
        pw = self._password_file()
        if pw:
            result.add("password file exists", Path(pw).is_file(), pw)
        return result

    def describe(self) -> str:
        notify = " (+notify)" if self._notify_url() else ""
        return f"{self.name}: {self._url()}/{{machine}}{notify}"
