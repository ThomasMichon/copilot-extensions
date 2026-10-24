"""Bounded registered-provider startup scheduling and terminal error ownership."""
from __future__ import annotations

from collections import deque
import threading


class PivotRequestsMixin:
    def _init_requests(self):
        self._pending_requests = deque()
        self._starting_scopes = {}
        self._request_context = threading.local()
        self._scope_generations = {}
        self._active_requests = {}
        self._reconnecting_scopes = set()
        self._refresh_pending = set()

    def ensure_many(self, machines):
        for machine in dict.fromkeys(machines):
            self.ensure(machine)

    def _cancel_pending_lists(self):
        with self._lock:
            self._refresh_pending.clear()
            while self._pending_requests:
                machine, generation = self._pending_requests.popleft()
                if self._active_requests.get(machine) == generation:
                    self._inflight.discard(machine)
                    self._active_requests.pop(machine, None)
                    rows = self._cache.get(machine, ("idle", [], ""))[1]
                    self._cache[machine] = ("error", rows, "provider view closed")

    def invalidate(self, machine=None):
        """Fence cached reads; the next ensure starts a fresh selected source."""
        with self._lock:
            scopes = tuple(self._cache.keys() | self._inflight) if machine is None else (machine,)
            if machine is None:
                self._gen += 1
                self._cache.clear()
                self._summaries.clear()
            else:
                self._scope_generations[machine] = self._scope_generations.get(machine, 0) + 1
                self._cache.pop(machine, None)
                self._summaries.pop(machine, None)
        for scope in scopes:
            self._stop_scope_read(scope, refetch=False)

    def refresh_read(self, machine):
        """Coalesce explicit reloads while retaining rows and other source channels."""
        with self._lock:
            if self._closed.is_set():
                return
            self._scope_generations[machine] = self._scope_generations.get(machine, 0) + 1
        self._stop_scope_read(machine, refetch=True)

    def _stop_scope_read(self, machine, *, refetch):
        from .tasks import _kill_proc_tree

        with self._lock:
            if machine in self._reconnecting_scopes:
                self._reconnecting_scopes.discard(machine)
                self._inflight.discard(machine)
                self._active_requests.pop(machine, None)
            running = machine in self._inflight
            if refetch and running:
                self._refresh_pending.add(machine)
        with self._procs_lock:
            procs = [proc for proc in self._procs if getattr(proc, "_picker_scope", None) == machine]
        for proc in procs:
            _kill_proc_tree(proc)
        if refetch and not running:
            self._schedule_list(machine, force=True)

    def _generation_matches(self, machine, generation):
        if generation is None:
            return True
        if isinstance(generation, tuple):
            return (
                generation[:2] == (self._gen, self._scope_generations.get(machine, 0))
                and self._active_requests.get(machine) == generation
            )
        return generation == self._gen

    def _schedule_list(self, machine, *, force=False):
        with self._lock:
            if self._closed.is_set() or machine in self._inflight:
                return
            if not force and machine in self._cache:
                return
            self._inflight.add(machine)
            generation = (self._gen, self._scope_generations.get(machine, 0), object())
            self._active_requests[machine] = generation
            self._pending_requests.append((machine, generation))
        self._start_pending_lists()

    def _start_pending_lists(self):
        while True:
            starts = []
            with self._lock:
                while self._pending_requests and len(self._starting_scopes) < 4 and not self._closed.is_set():
                    machine, generation = self._pending_requests.popleft()
                    self._starting_scopes[machine] = generation
                    starts.append((machine, generation))
            if not starts:
                return
            failed = False
            for machine, generation in starts:
                try:
                    threading.Thread(
                        target=self._run_scheduled_list, args=(machine, generation),
                        name="pivot-list", daemon=True,
                    ).start()
                except Exception as error:
                    failed = True
                    with self._lock:
                        self._cache[machine] = (
                            "error", [], f"Provider worker could not start: {str(error)[:160]}",
                        )
                        self._inflight.discard(machine)
                        self._starting_scopes.pop(machine, None)
                        self._active_requests.pop(machine, None)
            if not failed:
                return

    def _release_startup(self, machine):
        generation = getattr(self._request_context, "generation", None)
        with self._lock:
            if generation is None or self._starting_scopes.get(machine) == generation:
                self._starting_scopes.pop(machine, None)
        self._start_pending_lists()

    def _run_scheduled_list(self, machine, generation):
        self._request_context.generation = generation
        self._request_context.machine = machine
        try:
            with self._lock:
                if self._closed.is_set() or not self._generation_matches(machine, generation):
                    return
            self._run_list(machine, generation)
        except Exception as error:
            with self._lock:
                if self._generation_matches(machine, generation):
                    self._subscribe_live[machine] = False
                    rows = self._cache.get(machine, ("idle", [], ""))[1]
                    self._cache[machine] = (
                        "error", rows, f"{type(error).__name__}: {str(error).strip()[:160]}",
                    )
        finally:
            refetch = False
            with self._lock:
                if (
                    self._active_requests.get(machine) == generation
                    and machine not in self._reconnecting_scopes
                ):
                    self._inflight.discard(machine)
                    self._active_requests.pop(machine, None)
                    refetch = machine in self._refresh_pending and not self._closed.is_set()
                    self._refresh_pending.discard(machine)
            self._release_startup(machine)
            if refetch:
                self._schedule_list(machine, force=True)

    def _finish(self, machine, cache, summary):
        """Publish only this request's terminal result and retain failed cached rows."""
        with self._lock:
            generation = getattr(self._request_context, "generation", None)
            if not self._generation_matches(machine, generation):
                return
            if cache[0] == "error" and not cache[1]:
                cache = (cache[0], self._cache.get(machine, ("idle", [], ""))[1], cache[2])
            self._cache[machine] = cache
            self._summaries[machine] = summary
            self._inflight.discard(machine)
