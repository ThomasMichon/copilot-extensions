"""Per-launch data preparation; presentation still uses the existing Inbox."""

from __future__ import annotations

import threading


class _StartedLoader:
    def __init__(self, loader):
        self._loader = loader

    def start(self, focus_keys=None):
        pass

    def __getattr__(self, name):
        return getattr(self._loader, name)


class PreparedSource:
    def __init__(self, source):
        self._source = source
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._started = False
        self._snapshot = None
        self._loader = None
        self._snapshot_taken = False
        self._loader_taken = False
        self._error = None

    def start(self):
        with self._lock:
            if self._started or self._closed.is_set():
                return
            self._started = True
        threading.Thread(target=self._prepare, name="picker-initial-source", daemon=True).start()

    def _prepare(self):
        loader = None
        try:
            snapshot = self._source.source_snapshot()
            if self._closed.is_set():
                return
            loader = self._source.make_loader(snapshot)
            with self._lock:
                if self._closed.is_set():
                    loader.cancel()
                    return
                self._loader = loader
                self._snapshot = snapshot
            from .picker_tui.engine_helpers import start_loader

            local = next((source.key for source in snapshot if source.local), None)
            start_loader(loader, focus_keys={local} if local else None)
        except Exception as error:
            self._error = error
            if loader is not None:
                loader.cancel()
        finally:
            self._ready.set()

    def _wait(self):
        while not self._closed.is_set():
            if self._ready.wait(0.05):
                if self._error is not None:
                    raise self._error
                return
        raise RuntimeError("Picker source preparation was cancelled")

    def source_snapshot(self):
        self._wait()
        with self._lock:
            if not self._snapshot_taken:
                self._snapshot_taken = True
                return self._snapshot
        return self._source.source_snapshot()

    def make_loader(self, snapshot=None):
        self._wait()
        with self._lock:
            if not self._loader_taken and snapshot is self._snapshot:
                self._loader_taken = True
                return _StartedLoader(self._loader)
        return self._source.make_loader(snapshot) if snapshot is not None else self._source.make_loader()

    def close(self):
        self._closed.set()
        with self._lock:
            loader = self._loader
        if loader is not None:
            loader.cancel()

    def __getattr__(self, name):
        return getattr(self._source, name)
