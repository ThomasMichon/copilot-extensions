"""Bounded reusable PowerShell host for installation-context tests."""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path


class PowerShellTestHost:
    """Run each request in a fresh runspace inside one PowerShell process."""

    def __init__(
        self,
        executable: str,
        host_script: Path,
        script_path: Path,
        *,
        timeout_seconds: int = 30,
    ) -> None:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._command = (
            executable,
            "-NoProfile",
            "-NoLogo",
            "-NonInteractive",
            "-File",
            str(script_path),
        )
        self._response_timeout = timeout_seconds + 5
        self._process = subprocess.Popen(
            [
                executable,
                "-NoProfile",
                "-NoLogo",
                "-NonInteractive",
                "-File",
                str(host_script),
                "-ScriptPath",
                str(script_path),
                "-TimeoutSeconds",
                str(timeout_seconds),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            creationflags=creationflags,
        )
        self._lock = threading.Lock()
        self._next_request_id = 1

    def run(
        self,
        arguments: tuple[str, ...],
        environment_overrides: dict[str, str] | None,
    ) -> subprocess.CompletedProcess[str]:
        assert self._process.stdin is not None
        assert self._process.stdout is not None
        with self._lock:
            if self._process.poll() is not None:
                raise AssertionError(self._failure_message("exited before a request"))
            request_id = self._next_request_id
            self._next_request_id += 1
            self._process.stdin.write(
                json.dumps(
                    {
                        "id": request_id,
                        "arguments": arguments,
                        "environment": environment_overrides or {},
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
            self._process.stdin.flush()

            response_line: list[str] = []

            def read_response() -> None:
                response_line.append(self._process.stdout.readline())

            reader = threading.Thread(target=read_response, daemon=True)
            reader.start()
            reader.join(timeout=self._response_timeout)
            if reader.is_alive():
                self._terminate()
                raise subprocess.TimeoutExpired(
                    [*self._command, *arguments],
                    timeout=self._response_timeout,
                )
            if not response_line or not response_line[0]:
                raise AssertionError(self._failure_message("closed without a response"))
            response = json.loads(response_line[0])
            if response.get("id") != request_id:
                raise AssertionError(
                    f"PowerShell test host response id mismatch: {response!r}"
                )
            return subprocess.CompletedProcess(
                [*self._command, *arguments],
                int(response["returncode"]),
                str(response["stdout"]),
                str(response["stderr"]),
            )

    def close(self) -> None:
        with self._lock:
            if self._process.poll() is None and self._process.stdin is not None:
                try:
                    self._process.stdin.write('{"shutdown":true}\n')
                    self._process.stdin.flush()
                    self._process.wait(timeout=5)
                except (BrokenPipeError, subprocess.TimeoutExpired):
                    self._terminate()
            self._close_pipes()

    def _failure_message(self, reason: str) -> str:
        stderr = ""
        if self._process.stderr is not None and self._process.poll() is not None:
            stderr = self._process.stderr.read()
        return f"PowerShell test host {reason}: {stderr}"

    def _terminate(self) -> None:
        if self._process.poll() is None:
            self._process.kill()
            self._process.wait(timeout=5)

    def _close_pipes(self) -> None:
        for stream in (
            self._process.stdin,
            self._process.stdout,
            self._process.stderr,
        ):
            if stream is not None:
                stream.close()
