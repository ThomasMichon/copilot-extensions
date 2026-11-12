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
        self.failed = threading.Event()
        self._failure_lock = threading.Lock()
        self._on_failure = None
        self.consumer_finished = threading.Event()
        self.settle_lock = threading.Lock()
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
        with self._failure_lock:
            self.error = message
            self.failed.set()
            handler = self._on_failure
        if handler is not None:
            try:
                handler(message)
            except Exception as exc:
                self.error += f"; failure notification failed: {exc}"
        try:
            self.retire()
        except Exception as exc:
            self.error += f"; child retirement failed: {exc}"

    def on_failure(self, handler):
        with self._failure_lock:
            self._on_failure = handler
            error = self.error
        if error:
            handler(error)

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
    drain = getattr(process, "_picker_stderr", None)
    if drain is None:
        return _settle_stream(runtime, process, drain)
    with drain.settle_lock:
        drain.consumer_finished.clear()
        try:
            return _settle_stream(runtime, process, drain)
        finally:
            drain.consumer_finished.set()


def _settle_stream(runtime, process, drain):
    stderr, error = "", ""
    stdout_settled = False
    from .tasks import _kill_proc_tree

    try:
        try:
            _, stderr = process.communicate(timeout=5)
        except Exception:
            _kill_proc_tree(process)
            _, stderr = process.communicate(timeout=5)
        stdout_settled = True
    except Exception as exc:
        error = f"stream child cleanup failed: {exc}"
    settled = drain is None or drain.finish()
    if drain is not None:
        stderr = drain.text() if settled else ""
        error = drain.error or error
    if settled and stdout_settled and process.returncode is not None:
        try:
            runtime._untrack(process)
        except Exception as exc:
            error = f"stream child cleanup failed: {exc}"
    else:
        error = error or "stream child, stdout or stderr reader did not settle"
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
            if not _stdout_closed(process):
                if drain is None or not drain.consumer_finished.wait(timeout=5):
                    raise RuntimeError("stdout pipe or reader did not settle")
                if not _stdout_closed(process):
                    settle_stream(runtime, process)
                if not _stdout_closed(process):
                    raise RuntimeError("stdout pipe or reader did not settle")
            if process.poll() is None:
                raise RuntimeError("stream child did not settle")
            runtime._untrack(process)
        except Exception as exc:
            failures.append(str(exc))
    if failures:
        raise RuntimeError("; ".join(failures))


def _stdout_closed(process):
    pipe = getattr(process, "stdout", None)
    reader = getattr(process, "_stdout_thread", None)
    return (
        (pipe is None or pipe.closed)
        and (reader is None or not reader.is_alive())
    )


def watch_list_failure(runtime, process, machine, generation):
    drain = getattr(process, "_picker_stderr", None)
    if drain is None:
        return

    def failed(message):
        with runtime._lock:
            if runtime._closed.is_set() or not runtime._generation_matches(machine, generation):
                return
            rows = runtime._cache.get(machine, ("idle", [], ""))[1]
            runtime._cache[machine] = ("error", rows, message[:200])
            runtime._subscribe_live[machine] = False

    drain.on_failure(failed)
