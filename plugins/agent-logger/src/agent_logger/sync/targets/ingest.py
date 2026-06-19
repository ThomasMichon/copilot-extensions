"""Ingest sync target -- a generic rsync-daemon sink with optional HTTP notify.

This is the shape a bespoke processing service exposes: every machine pushes
its raw sessions to a shared rsync-daemon module (``host::module/path`` or an
``rsync://`` URL), and the service is optionally pinged over HTTP after a
successful push so it can crunch immediately instead of waiting for its poll.

Generalized from the facility engine's rsync-daemon transport and its
permanent-record notify -- no facility hostnames, modules, or auth specifics
are baked in.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from agent_logger.sync.targets.base import DoctorResult, PushResult, Target

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

    def push(self, source: Path, machine: str) -> PushResult:
        url = self._url()
        if not url:
            return PushResult(ok=False, detail="ingest target requires a url")
        if shutil.which("rsync") is None:
            return PushResult(ok=False, detail="rsync not found on PATH")
        dest = f"{url}/{machine}/"
        cmd = ["rsync", "-az", "--delete"]
        pw = self._password_file()
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
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return PushResult(ok=False, detail=f"rsync failed: {exc}")
        if proc.returncode != 0:
            return PushResult(ok=False, detail=proc.stderr.strip()[:300])

        self._notify(machine)
        return PushResult(ok=True, detail=f"-> {dest}")

    def _notify(self, machine: str) -> None:
        """Best-effort HTTP ping so the consumer can crunch immediately."""
        notify_url = self._notify_url()
        if not notify_url:
            return
        url = notify_url.replace("{machine}", machine)
        try:
            req = urllib.request.Request(url, method="POST")
            token_file = self.options.get("bearer_token_file", "")
            if token_file and Path(token_file).is_file():
                token = Path(token_file).read_text(encoding="utf-8").strip()
                req.add_header("Authorization", f"Bearer {token}")
            urllib.request.urlopen(req, timeout=5)
        except (urllib.error.URLError, OSError, ValueError):
            pass

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
