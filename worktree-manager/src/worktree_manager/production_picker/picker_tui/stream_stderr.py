"""A bounded diagnostic reader; the registered runtime retains process custody."""

import threading


STDERR_LIMIT = 1024 * 1024


class StreamStderr:
    def __init__(self, process, retire):
        self.stream = process.stderr
        self.retire = retire
        self.output = bytearray()
        self.error = ""
        self.started = False
        self.thread = threading.Thread(
            target=self._read, name="registered-stream-stderr", daemon=True,
        )
        # communicate() still drains trailing stdout, but must not race this reader.
        process.stderr = None

    def start(self):
        try:
            self.thread.start()
            self.started = True
        except Exception as exc:
            self._fail(f"stderr reader could not start: {exc}")

    def _fail(self, message):
        self.error = message
        try:
            self.retire()
        except Exception as exc:
            self.error += f"; child retirement failed: {exc}"

    def _read(self):
        try:
            binary = getattr(self.stream, "buffer", self.stream)
            read = getattr(binary, "read1", binary.read)
            while chunk := read(4096):
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="replace")
                if len(self.output) + len(chunk) > STDERR_LIMIT:
                    self._fail(f"stderr output exceeded {STDERR_LIMIT} bytes")
                    return
                self.output.extend(chunk)
        except Exception as exc:
            self._fail(f"stderr reader failed: {exc}")

    def finish(self):
        if self.started:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                self._fail("stderr reader did not settle")
                self.thread.join(timeout=5)
            if self.thread.is_alive():
                return False
        try:
            self.stream.close()
        except Exception as exc:
            self.error = f"stderr pipe cleanup failed: {exc}"
            return False
        return True

    def text(self):
        return self.output.decode("utf-8", errors="replace")


def settle_stream(runtime, process):
    stderr, error = "", ""
    drain = getattr(process, "_picker_stderr", None)
    from .tasks import _kill_proc_tree

    try:
        try:
            _, stderr = process.communicate(timeout=5)
        except Exception:
            _kill_proc_tree(process)
            _, stderr = process.communicate(timeout=5)
    except Exception as exc:
        error = f"stream child cleanup failed: {exc}"
    settled = drain is None or drain.finish()
    if drain is not None:
        stderr = drain.text() if settled else ""
        error = drain.error or error
    if settled and process.returncode is not None:
        try:
            runtime._untrack(process)
        except Exception as exc:
            error = f"stream child cleanup failed: {exc}"
    else:
        error = error or "stream child or stderr reader did not settle"
    return stderr or "", error


def close_streams(runtime):
    from .tasks import _kill_proc_tree

    with runtime._procs_lock:
        processes = list(runtime._procs)
    failures = []
    for process in processes:
        try:
            _kill_proc_tree(process)
            drain = getattr(process, "_picker_stderr", None)
            if drain is not None and not drain.finish():
                raise RuntimeError(drain.error or "stderr reader did not settle")
            if process.poll() is None:
                raise RuntimeError("stream child did not settle")
            runtime._untrack(process)
        except Exception as exc:
            failures.append(str(exc))
    if failures:
        raise RuntimeError("; ".join(failures))
