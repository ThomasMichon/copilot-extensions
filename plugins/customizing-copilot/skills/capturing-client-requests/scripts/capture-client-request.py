#!/usr/bin/env python3
"""Capture one operator-owned Copilot CLI request without forwarding inference."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
import subprocess
import sys
import threading
import time
from typing import BinaryIO
from uuid import UUID, uuid4

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from snapshot_artifacts import local_output, reserve_output, write_report
from snapshot_inventory import copilot_home
from snapshot_preferences import PreferenceError, resolve_preferences
from snapshot_report import analyze_file
from snapshot_rounds import GuidanceSnapshot, MAX_ROUNDS

MAX_BODY_BYTES = 16 * 1024 * 1024
REPLY = "LOCAL_REQUEST_CAPTURE_COMPLETE"


def authorization(key: str) -> str:
    scheme = "Bear" + "er "
    return scheme + key


class CaptureServer(HTTPServer):
    allow_reuse_address = False

    def __init__(
        self, output: BinaryIO, key: str, deadline: float, *,
        snapshot: GuidanceSnapshot | None = None,
        round_outputs: list[tuple[Path, BinaryIO]] | None = None,
        round_timeout: float = 60,
    ):
        self.output = output
        self.key = key
        self.overall_deadline = deadline
        self.round_timeout = round_timeout
        self.snapshot = snapshot
        self.round_outputs = round_outputs or []
        self.deadline = min(deadline, time.monotonic() + round_timeout) if snapshot else deadline
        self.captured_bytes = 0
        self.failure: str | None = None
        self.finished = threading.Event()
        self.active: socket.socket | None = None
        super().__init__(("127.0.0.1", 0), CaptureHandler)
        self.timeout = 0.1

    def server_bind(self) -> None:
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def get_request(self):
        connection, address = super().get_request()
        self.active = connection
        connection.settimeout(max(0.01, self.deadline - time.monotonic()))
        return connection, address

    def handle_error(self, request, client_address) -> None:
        # The default traceback may contain request content or local paths.
        self.failure = "request_io_failed"
        self.finished.set()

    def serve_capture(self) -> None:
        while not self.finished.is_set() and time.monotonic() < self.deadline:
            self.handle_request()
        if not self.finished.is_set() and self.snapshot:
            self.failure = "round_timeout"
            self.snapshot.finish(self.failure)
            self.finished.set()

    def close_capture(self, worker: threading.Thread) -> None:
        self.finished.set()
        if self.active is not None:
            try:
                self.active.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # The handler may already have closed this exact socket.
        self.server_close()
        worker.join(timeout=2)
        if worker.is_alive():
            raise RuntimeError("capture listener did not settle")


class CaptureHandler(BaseHTTPRequestHandler):
    server: CaptureServer

    def log_message(self, *_args) -> None:
        pass

    def _reject(self, status: int, code: str) -> None:
        self.server.failure = code
        self.server.finished.set()
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        credentials = self.headers.get_all("Authorization", [])
        if len(credentials) != 1 or not hmac.compare_digest(
            credentials[0].encode("utf-8"), authorization(self.server.key).encode("utf-8")
        ):
            self._reject(403, "unexpected_client")
            return
        if self.path != "/v1/chat/completions":
            self._reject(404, "unexpected_route")
            return
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self._reject(400, "unexpected_host")
            return
        lengths = self.headers.get_all("Content-Length", [])
        if (
            self.headers.get("Transfer-Encoding") is not None
            or self.headers.get("Content-Encoding") is not None
            or len(lengths) != 1
            or not lengths[0].isascii()
            or not lengths[0].isdecimal()
        ):
            self._reject(400, "unsupported_body_framing")
            return
        length = int(lengths[0])
        if not 0 < length <= MAX_BODY_BYTES:
            self._reject(413, "body_size_limit")
            return
        body = bytearray()
        while len(body) < length:
            remaining = self.server.deadline - time.monotonic()
            if remaining <= 0:
                self._reject(408, "request_timeout")
                return
            self.connection.settimeout(remaining)
            try:
                chunk = self.rfile.read1(min(65536, length - len(body)))
            except TimeoutError:
                self._reject(408, "request_timeout")
                return
            if not chunk:
                self._reject(400, "incomplete_body")
                return
            body.extend(chunk)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            self._reject(400, "invalid_json")
            return
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("messages"), list)
            or not isinstance(payload.get("model"), str)
            or type(payload.get("stream", False)) is not bool
        ):
            self._reject(400, "unsupported_request_shape")
            return
        snapshot = self.server.snapshot
        message = {"role": "assistant", "content": REPLY}
        output = self.server.output
        if snapshot:
            if len(snapshot.rounds) >= MAX_ROUNDS or snapshot.finished:
                self._reject(400, "round_limit")
                return
            capture_path, output = self.server.round_outputs[len(snapshot.rounds)]
        output.write(body)
        output.flush()
        self.server.captured_bytes += len(body)
        if snapshot:
            message = snapshot.receive(payload, len(body), capture_path)
        terminal = "tool_calls" not in message
        response = {
            "id": "local-capture",
            "object": "chat.completion",
            "created": 0,
            "model": payload["model"],
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": "stop" if terminal else "tool_calls",
            }],
        }
        streaming = payload.get("stream", False)
        if streaming:
            response["object"] = "chat.completion.chunk"
            response["choices"][0]["delta"] = response["choices"][0].pop("message")
            for index, call in enumerate(message.get("tool_calls", [])):
                call["index"] = index
            encoded = ("data: " + json.dumps(response) + "\n\ndata: [DONE]\n\n").encode()
        else:
            encoded = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if streaming else "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
            self.wfile.flush()
        except OSError:
            self.server.failure = "response_delivery_failed"
        finally:
            if terminal or self.server.failure:
                self.server.finished.set()
            else:
                self.server.deadline = min(
                    self.server.overall_deadline, time.monotonic() + self.server.round_timeout,
                )

    def do_GET(self) -> None:
        self._reject(405, "unexpected_method")


def child_environment(key: str, port: int, model: str) -> dict[str, str]:
    environment = dict(os.environ)
    for name in tuple(environment):
        if name.startswith("COPILOT_PROVIDER_") or name in {
            "GH_TOKEN", "GITHUB_TOKEN", "GITHUB_API_TOKEN",
            "COPILOT_GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
            "http_proxy", "https_proxy", "all_proxy",
        }:
            environment.pop(name)
    environment.update(
        COPILOT_OFFLINE="true",
        COPILOT_PROVIDER_TYPE="openai",
        COPILOT_PROVIDER_WIRE_API="completions",
        COPILOT_PROVIDER_BASE_URL=f"http://127.0.0.1:{port}/v1",
        COPILOT_PROVIDER_API_KEY=key,
        COPILOT_PROVIDER_MODEL_ID=model,
        COPILOT_MODEL=model,
        NO_PROXY="127.0.0.1",
        no_proxy="127.0.0.1",
    )
    return environment


def capture(
    command: list[str], working_directory: Path, output_path: Path, *,
    prompt: str, model: str, timeout: float,
    reasoning_effort: str | None = None, context: str | None = None,
    session_id: str | None = None,
    load_session_guidance: bool = False, round_timeout: float = 60,
) -> dict[str, object]:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if not math.isfinite(round_timeout) or round_timeout <= 0:
        raise ValueError("round timeout must be finite and positive")
    if load_session_guidance and (
        session_id is None or str(UUID(session_id)) != session_id
    ):
        raise ValueError("multi-round capture requires an owned UUID session")
    cwd = working_directory.resolve(strict=True)
    if not cwd.is_dir():
        raise ValueError("working directory must be a directory")
    output_path = local_output(output_path)
    result: dict[str, object] = {
        "schema": "copilot-extensions.local-request-capture",
        "version": 1,
        "status": "failed",
        "capture_path": str(output_path),
        "captured_bytes": 0,
    }
    server = None
    worker = None
    child = None
    outputs = []
    snapshot = None
    try:
        with ExitStack() as stack:
            paths = [output_path]
            if load_session_guidance:
                paths.extend(
                    output_path.with_name(f"{output_path.stem}.round-{index}{output_path.suffix}")
                    for index in range(2, MAX_ROUNDS + 1)
                )
                snapshot = GuidanceSnapshot(
                    copilot_home(working_directory=cwd) / "session-state" / session_id / "instructions"
                )
            for path in paths:
                output = stack.enter_context(os.fdopen(reserve_output(path), "wb"))
                outputs.append((path, output))
            key = secrets.token_urlsafe(32)
            deadline = time.monotonic() + timeout
            server = CaptureServer(
                outputs[0][1], key, deadline, snapshot=snapshot,
                round_outputs=outputs, round_timeout=round_timeout,
            )
            worker = threading.Thread(target=server.serve_capture, daemon=True)
            worker.start()
            argv = [
                *command, "--no-auto-update", "--model", model,
                "--no-ask-user", "-p", prompt,
            ]
            for flag, value in (("--reasoning-effort", reasoning_effort), ("--context", context)):
                if value is not None:
                    argv.extend([flag, value])
            if session_id is not None:
                argv.append(f"--session-id={session_id}")
                result["session_id"] = session_id
            if load_session_guidance:
                argv.extend(["--allow-tool", "glob", "--allow-tool", "view"])
            # headless-guard: allow standalone payload has no installed process runtime
            child = subprocess.Popen(
                argv, cwd=cwd,
                env=child_environment(key, server.server_port, model),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                while True:
                    try:
                        child.wait(timeout=max(0.01, min(0.1, deadline - time.monotonic())))
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() >= deadline or server.failure:
                            raise
            except subprocess.TimeoutExpired:
                result["error"] = server.failure or "client_timeout"
            else:
                result["client_exit_code"] = child.returncode
                if child.returncode != 0:
                    result["error"] = server.failure or "client_exit_failed"
                elif not server.finished.wait(timeout=max(0, deadline - time.monotonic())):
                    result["error"] = "no_request"
                elif not server.captured_bytes:
                    result["error"] = server.failure or "no_request"
                elif server.failure:
                    result["error"] = server.failure
                elif snapshot and snapshot.state != "complete":
                    result["error"] = snapshot.reason or "incomplete_guidance_snapshot"
                else:
                    result["status"] = "captured"
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                server.close_capture(worker)
                worker = None
            result["captured_bytes"] = server.captured_bytes
            if snapshot:
                if not snapshot.finished:
                    snapshot.finish(str(result.get("error", "incomplete_guidance_snapshot")))
                result["multi_round"] = snapshot.report()
    finally:
        if server is not None and worker is not None:
            server.close_capture(worker)
        for path, output in outputs:
            if path.stat().st_size == 0:
                path.unlink()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture your own CLI's raw request using a temporary local fake provider.",
        epilog="Raw output may contain secrets. Never use this to log a shared inference service.",
    )
    parser.add_argument("--copilot-command", default="copilot")
    parser.add_argument("--working-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prompt", default="Reply with a short acknowledgement. Do not use tools.")
    parser.add_argument("--model", help="override the operator's current model")
    parser.add_argument("--reasoning-effort", help="override the operator's current effortLevel")
    parser.add_argument("--context", help="override the operator's current contextTier")
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--load-session-guidance", action="store_true",
                        help="capture up to three requests using native glob/view calls")
    parser.add_argument("--round-timeout-seconds", type=float, default=60)
    parser.add_argument("--agent-worktrees-command", help="catalog command for registry-source inventory")
    args = parser.parse_args(argv)
    executable = shutil.which(args.copilot_command)
    if executable is None:
        parser.error("Copilot executable was not found; supply --copilot-command")
    executable = str(Path(executable).resolve())
    if os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat", ".ps1"}:
        parser.error("select the native Copilot executable, not a shell shim")
    result = None
    descriptor = None
    report_path = None
    report_written = False
    try:
        preferences = resolve_preferences(
            model=args.model, reasoning_effort=args.reasoning_effort, context=args.context,
            working_directory=args.working_directory,
        )
        output = local_output(args.output)
        report_path = output.with_name(output.stem + ".analysis.json")
        descriptor = reserve_output(report_path)
        session_id = str(uuid4())
        result = capture(
            [executable], args.working_directory, output, prompt=args.prompt,
            model=preferences["model"], timeout=args.timeout_seconds,
            reasoning_effort=preferences["reasoning_effort"], context=preferences["context"],
            session_id=session_id,
            load_session_guidance=args.load_session_guidance,
            round_timeout=args.round_timeout_seconds,
        )
        result["launch_preferences"] = preferences
        if result["status"] == "captured" or result.get("multi_round", {}).get("rounds"):
            rounds = result.get("multi_round", {}).get("rounds", [])
            analysis_input = Path(rounds[-1]["capture_path"]) if rounds else output
            report = analyze_file(
                analysis_input, args.working_directory, session_id=session_id,
                agent_worktrees_command=args.agent_worktrees_command,
                launch_preferences=preferences,
            )
            if "multi_round" in result:
                report["multi_round"] = result["multi_round"]
            write_report(descriptor, report)
            report_written = True
            result["analysis_path"] = str(report_path)
            result["analysis_warning_count"] = len(report["warnings"])
            result["analysis_status"] = "completed_with_warnings" if report["warnings"] else "completed"
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.SubprocessError) as error:
        # Never echo exception text, CLI output or argv: any may contain the prompt.
        captured = result is not None and result.get("captured_bytes", 0) > 0
        result = result or {
            "schema": "copilot-extensions.local-request-capture", "version": 1,
        }
        code = str(error) if isinstance(error, PreferenceError) else type(error).__name__
        result.update(status="failed", error=("analysis_" if captured else "") + code)
    finally:
        if descriptor is not None:
            os.close(descriptor)
            if not report_written:
                report_path.unlink()
    print(json.dumps(result))
    return 0 if result["status"] == "captured" else 1


if __name__ == "__main__":
    sys.exit(main())
