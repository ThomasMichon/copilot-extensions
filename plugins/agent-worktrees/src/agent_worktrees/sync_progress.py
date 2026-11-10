"""Optional D4 progress for the existing Sync operation."""

from __future__ import annotations

import json
import sys
import threading

HEARTBEAT_SECONDS = 10


def result_ok(result: dict) -> bool:
    if "results" in result:
        return all(result_ok(member) for member in result["results"])
    if "ok" in result:
        return result["ok"] is True
    return bool(result.get("updated")) or result.get("reason") == "up-to-date"


class SyncProgress:
    def __init__(self):
        self._output = sys.stdout
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._message = "Loading Sync configuration"
        self._failure: Exception | None = None
        self._thread: threading.Thread | None = None

    def _write(self, frame):
        print(json.dumps(frame), file=self._output, flush=True)

    def stage(self, message: str) -> None:
        with self._lock:
            if self._failure is not None:
                raise self._failure
            self._message = message
            self._write({"type": "progress", "pct": None, "msg": message})

    def _heartbeat(self):
        while not self._stopped.wait(HEARTBEAT_SECONDS):
            try:
                with self._lock:
                    self._write({
                        "type": "progress", "pct": None,
                        "msg": f"Waiting: {self._message}",
                    })
            except Exception as error:
                self._failure = error
                self._stopped.set()

    def __enter__(self):
        self.stage(self._message)
        self._thread = threading.Thread(
            target=self._heartbeat, name="sync-progress", daemon=True,
        )
        self._thread.start()
        return self

    def _stop(self):
        self._stopped.set()
        if self._thread is not None:
            self._thread.join()

    def finish(self, result: dict) -> int:
        self._stop()
        ok = result_ok(result)
        with self._lock:
            if self._failure is not None:
                raise self._failure
            self._write({
                "type": "done" if ok else "error",
                "message": result.get("reason") or ("Sync complete" if ok else "Sync failed"),
                "result": result,
            })
        return 0 if ok else 1

    def __exit__(self, *_exc):
        self._stop()
