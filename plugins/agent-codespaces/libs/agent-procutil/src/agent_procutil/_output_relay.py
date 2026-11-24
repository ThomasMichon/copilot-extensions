"""Finite byte relays isolate writes to borrowed, potentially blocking descriptors."""

from __future__ import annotations

import json
import base64
import os
import queue
import subprocess
import sys
import threading
import time
from io import BufferedReader
from pathlib import Path
from typing import BinaryIO, cast


_THREAD_START_CODE = getattr(threading.Thread.start, "__code__", None)


class _OutputRelayStartupError(RuntimeError):
    """Presentation startup failed before creation or after verified retirement."""

    def __init__(self, primary: Exception) -> None:
        super().__init__("native output relay startup failed")
        self.primary = primary


class OutputRelayCleanupError(RuntimeError):
    """The caller retains the exact relay whose retirement is unverified."""

    def __init__(self, relay: ByteRelay) -> None:
        super().__init__("native output relay cleanup is unverified")
        self.relay = relay


class ByteRelay:
    def __init__(self, descriptor: int, *, deadline: float | None = None) -> None:
        from . import no_window_kwargs, spawn_sync_in_kill_on_close_job

        try:
            self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=64)
            self._stop = threading.Event()
            self._errors: list[BaseException] = []
            self._native_error: OSError | None = None
            self._reported = threading.Event()
            self.last_progress = 0.0
            self._ending = False
            self._closed = False
            self._writer: threading.Thread | None = None
            self._reader: threading.Thread | None = None
            self._startup_unverified = False
            retained = globals().get("_retained_source_bytes")
            if retained is None:
                command = [sys.executable, "-I", "-B", str(Path(__file__).resolve())]
            else:
                if type(retained) is not bytes or not 0 < len(retained) <= 1024 * 1024:
                    raise ValueError("retained output relay source is invalid")
                encoded = base64.b64encode(retained).decode("ascii")
                bootstrap = (
                    "import base64\n"
                    "__file__='_output_relay.py'\n"
                    "exec(compile(base64.b64decode(" + repr(encoded) + ",validate=True),__file__,'exec'))"
                )
                if len(bootstrap.encode("utf-16-le")) // 2 > 30000:
                    raise ValueError("retained output relay exceeds its command budget")
                command = [sys.executable, "-I", "-B", "-c", bootstrap]
        except Exception as error:
            raise _OutputRelayStartupError(error) from error
        self.process, self.job = spawn_sync_in_kill_on_close_job(
            [*command, "--copy", str(os.getpid())],
            executable=sys.executable,
            require_job=os.name == "nt", allow_breakaway=False,
            stdin=subprocess.PIPE, stdout=descriptor, stderr=subprocess.PIPE,
            close_fds=True, **no_window_kwargs(),
        )
        try:
            self._writer = threading.Thread(
                target=self._write, name=f"output-relay-{self.process.pid}", daemon=False,
            )
            self._reader = threading.Thread(
                target=self._report, name=f"output-receipt-{self.process.pid}", daemon=False,
            )
            self._start(self._reader)
            self._start(self._writer)
        except BaseException as error:
            self.abort(10 if deadline is None else max(0, deadline - time.monotonic()))
            if isinstance(error, Exception):
                raise _OutputRelayStartupError(error) from error
            raise

    def _start(self, worker: threading.Thread) -> None:
        try:
            worker.start()
        except BaseException as error:
            traceback = error.__traceback__
            while traceback is not None and traceback.tb_next is not None:
                traceback = traceback.tb_next
            # Retained installer bundles do not include the owned-process module.
            refused = (
                type(error) is RuntimeError
                and str(error) in {"can't start new thread", "thread.__init__() not called"}
                and traceback is not None
                and traceback.tb_frame.f_code is _THREAD_START_CODE
                and traceback.tb_frame.f_locals.get("self") is worker
            )
            self._startup_unverified = worker.ident is None and not refused
            raise

    def _write(self) -> None:
        stream = cast(BinaryIO, self.process.stdin)
        try:
            while not self._stop.is_set():
                try:
                    chunk = self._queue.get(timeout=.1)
                except queue.Empty:
                    continue
                if chunk is None:
                    break
                data = memoryview(chunk)
                while data and not self._stop.is_set():
                    count = os.write(stream.fileno(), data)
                    if count <= 0:
                        raise OSError("output relay input made no progress")
                    data = data[count:]
        except BaseException as error:
            self._errors.append(error)
        finally:
            stream.close()

    def _report(self) -> None:
        try:
            while line := cast(BinaryIO, self.process.stderr).readline(8192):
                if line == b"p\n":
                    self.last_progress = time.monotonic()
                    continue
                try:
                    detail = json.loads(line)
                except (ValueError, UnicodeError):
                    raise OSError("native output relay returned an invalid error receipt") from None
                if (
                    not isinstance(detail, dict) or set(detail) != {"errno", "message"}
                    or type(detail["errno"]) not in (int, type(None))
                    or not isinstance(detail["message"], str)
                ):
                    raise OSError("native output relay returned an invalid error receipt")
                self._native_error = OSError(detail["errno"], detail["message"])
        except BaseException as error:
            self._errors.append(error)
        finally:
            self._reported.set()

    def check(self) -> None:
        if self._closed:
            return
        code = self.process.poll()
        if self._native_error is not None:
            raise self._native_error
        if not self._reported.is_set() and (code is not None or self._errors):
            return
        if code is not None and code != 0:
            raise OSError(f"native output relay exited {code} without an error receipt")
        if self._errors:
            raise self._errors[0]

    def offer(self, chunk: bytes) -> bool:
        self.check()
        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            return False
        return True

    def end(self) -> bool:
        if not self._ending:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                return False
            self._ending = True
        self.check()
        return (
            self.process.poll() is not None
            and all(worker is None or not worker.is_alive() for worker in (self._writer, self._reader))
        )

    def close(self) -> None:
        self.check()
        if (
            any(worker is not None and worker.is_alive() for worker in (self._writer, self._reader))
            or self.process.poll() is None
        ):
            raise RuntimeError("native output relay has not finished")
        if self.job is not None:
            self.job.terminate_and_wait(10)
            self.job = None
        cast(BinaryIO, self.process.stderr).close()
        self._closed = True

    def abort(self, timeout: float) -> None:
        if self._closed:
            return
        self._stop.set()
        deadline = time.monotonic() + timeout
        try:
            if self.job is not None:
                self.job.terminate_and_wait(timeout)
                self.job = None
            elif self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=max(0, deadline - time.monotonic()))
            for worker in (self._writer, self._reader):
                if worker is not None and worker.ident is not None:
                    worker.join(max(0, deadline - time.monotonic()))
            if any(
                worker is not None and worker.is_alive() for worker in (self._writer, self._reader)
            ):
                raise TimeoutError("native output relay reader/writer did not exit")
            if self._startup_unverified:
                raise RuntimeError("native output relay thread startup is unverified")
            cast(BinaryIO, self.process.stdin).close()
            cast(BinaryIO, self.process.stderr).close()
            self._closed = True
        except BaseException as error:
            raise OutputRelayCleanupError(self) from error


def _copy(parent_pid: int) -> int:
    stop = threading.Event()

    def observe_parent() -> None:
        while not stop.wait(.1):
            if os.name != "nt" and os.getppid() != parent_pid:
                os._exit(1)

    observer = threading.Thread(target=observe_parent, name="output-relay-owner", daemon=True)
    observer.start()
    try:
        while chunk := cast(BufferedReader, sys.stdin.buffer).read1(4096):
            data = memoryview(chunk)
            while data:
                count = os.write(1, data)
                if count <= 0:
                    raise OSError("output adapter made no progress")
                os.write(2, b"p\n")
                data = data[count:]
        return 0
    except OSError as error:
        receipt = json.dumps({"errno": error.errno, "message": str(error)[:512]}).encode("ascii")
        os.write(2, receipt + b"\n")
        return 1
    finally:
        stop.set()
        observer.join()


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--copy":
        raise SystemExit("native output relay requires its owning parent")
    raise SystemExit(_copy(int(sys.argv[2])))
