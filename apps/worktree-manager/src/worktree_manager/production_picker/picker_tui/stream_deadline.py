"""Separate stream publication/silence and finite process clocks."""

import threading


class StreamDeadline:
    def __init__(self, *, subscribe, timeout, process_timeout, retire):
        self.lock = threading.RLock()
        self.timed_out = threading.Event()
        self.process_timed_out = threading.Event()
        self.initial_read_complete = False
        self._subscribe = subscribe
        self._timeout = timeout
        self._retire = retire
        self._generation = 0
        self._closed = False
        self._timer = self._arm(timeout, self._expired)
        self._process_timer = (
            None if subscribe else
            self._arm(process_timeout, lambda: self._expired(process=True))
        )

    @staticmethod
    def _arm(timeout, callback):
        timer = threading.Timer(timeout, callback)
        timer.daemon = True
        timer.start()
        return timer

    def _expired(self, generation=0, *, process=False):
        with self.lock:
            if self._closed or self._subscribe and self.initial_read_complete:
                return
            if not process and generation != self._generation:
                return
            self.timed_out.set()
            if process:
                self.process_timed_out.set()
        self._retire()

    def commit(self):
        with self.lock:
            self.initial_read_complete = True
            if self._subscribe:
                self._timer.cancel()

    def progress(self):
        with self.lock:
            if self._subscribe or self._closed or self.timed_out.is_set():
                return
            self._timer.cancel()
            self._generation += 1
            generation = self._generation
            self._timer = self._arm(self._timeout, lambda: self._expired(generation))

    def close(self):
        with self.lock:
            self._closed = True
            self._timer.cancel()
            if self._process_timer is not None:
                self._process_timer.cancel()
