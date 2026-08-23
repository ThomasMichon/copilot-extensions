"""Runtime for cross-plugin *registered* pivots in the Textual picker.

A registered pivot (see :mod:`picker_tui.pivots`) declares a ``list`` command
that prints a JSON array of entries, and an ``actions`` set of argv templates.
This module runs those commands -- always as a subprocess against the
contributing plugin's CLI on ``PATH``, never a cross-venv Python import -- so
the picker stays decoupled from the plugin's runtime.

:class:`RegisteredPivotRuntime` keeps the picker responsive: the ``list``
command runs on a daemon thread and the result is cached per machine, so the
render loop only ever reads a snapshot. Everything degrades gracefully -- a
missing CLI, a non-zero exit, or malformed JSON becomes an ``error`` state the
pivot surfaces, never an exception that breaks the picker.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
from collections.abc import Mapping, Sequence

from agent_procutil import no_window_flags

from .pivots import RegisteredPivot, format_template, parse_list_payload

#: Hard cap on how long a pivot's ``list``/action command may run.
LIST_TIMEOUT = 20.0
ACTION_TIMEOUT = 30.0
#: Overall watchdog for a one-shot (non-``subscribe``) streaming ``list``: a
#: stalled producer is killed after this many seconds, but rows already received
#: are kept. A ``subscribe`` (held/live) stream has no overall deadline.
STREAM_TIMEOUT = 30.0

_CREATE_NO_WINDOW = no_window_flags()


def _kill_proc_tree(proc: subprocess.Popen) -> None:
    """Best-effort terminate a streaming child (and its process group on posix,
    so a ``list --stream`` that shelled out to ``gh`` dies with it). Mirrors
    :func:`picker_tui.data_ssh._kill_proc_tree`; never raises."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                proc.terminate()
        else:
            proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=2)
    except Exception:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            pass


def _resolve_argv(template: Sequence[str], ctx: Mapping[str, object]) -> list[str]:
    """Substitute placeholders and resolve argv[0] via ``PATH`` (so a bare
    ``agent-dispatch`` runs regardless of the picker's own venv)."""
    argv = format_template(template, ctx)
    if argv:
        resolved = shutil.which(argv[0])
        if resolved:
            argv = [resolved, *argv[1:]]
    return argv


def _parse_ndjson_line(raw: str):
    """Parse one NDJSON line to a dict, tolerating banner noise / blank lines.

    A login shell can emit a line of noise before the JSON, so locate the first
    ``{`` and decode from there. Returns ``None`` when the line carries no JSON
    object (blank, banner, partial, or a bare ``[...]`` array). Mirrors
    :func:`picker_tui.data_ssh._parse_ndjson_line`."""
    if not raw:
        return None
    s = raw.strip()
    i = s.find("{")
    if i < 0:
        return None
    try:
        return json.loads(s[i:])
    except Exception:
        return None


def _is_stream_unsupported(stderr: str) -> bool:
    """True when a provider's argparse rejected the trailing ``--stream`` (an
    older CLI that predates the streaming producer), so the runtime falls back
    to the one-shot ``list``."""
    s = (stderr or "").lower()
    return "unrecognized arguments" in s and "--stream" in s


def _stream_entry(obj: Mapping) -> dict:
    """Extract the entry dict from a streaming ``row``/``delta`` frame.

    The canonical shape nests the entry under ``entry`` (``row``/``wt`` accepted
    as aliases for symmetry with the built-in worktrees producer). As a last
    resort the frame's own fields (minus the reserved ``type``) are treated as
    the entry inline. Always returns a ``dict`` (possibly empty)."""
    for key in ("entry", "row", "wt"):
        val = obj.get(key)
        if isinstance(val, dict):
            return val
    return {k: v for k, v in obj.items() if k != "type"}



class RegisteredPivotRuntime:
    """Background loader + action runner for one registered pivot.

    Thread-safe: the render loop calls :meth:`ensure` / :meth:`get` on every
    frame; a single daemon thread per machine fetches the ``list`` output.
    """

    def __init__(self, pivot: RegisteredPivot):
        self.pivot = pivot
        self._lock = threading.Lock()
        # machine -> (state, rows, error). state: loading|ready|error.
        self._cache: dict[object, tuple[str, list, str]] = {}
        # machine -> summary dict (D1: the header/footer line's substitution
        # source, e.g. budget headroom). Parallel to ``_cache`` so ``get`` keeps
        # its 3-tuple contract; ``get_summary`` reads this.
        self._summaries: dict[object, dict] = {}
        self._inflight: set[object] = set()
        # Generation counter (guarded by ``_lock``): bumped on every
        # :meth:`invalidate` so a one-shot fetch that was already in flight when
        # the cache was invalidated (e.g. by a steer/action that mutated the
        # queue) drops its now-stale result instead of overwriting the
        # just-cleared cache -- the invalidate-vs-inflight lost-update that left
        # the Tasks pivot showing pre-action state until a manual reload.
        self._gen: int = 0
        # D2: live streaming children tracked for teardown -- a held ``subscribe``
        # stream must be killed on picker exit so no ``list --stream`` is
        # orphaned. Guarded by its own lock; ``_closed`` short-circuits any spawn
        # that races :meth:`close`.
        self._procs: list[subprocess.Popen] = []
        self._procs_lock = threading.Lock()
        self._closed = threading.Event()

    # -- listing -------------------------------------------------------------

    def ensure(self, machine: object) -> None:
        """Kick off a background ``list`` fetch for ``machine`` if not already
        cached or in flight. Cheap + idempotent -- safe to call every frame."""
        with self._lock:
            if machine in self._cache or machine in self._inflight:
                return
            self._inflight.add(machine)
            gen = self._gen
        threading.Thread(
            target=self._run_list, args=(machine, gen), daemon=True
        ).start()

    def get(self, machine: object) -> tuple[str, list, str]:
        """The cached ``(state, rows, error)`` for ``machine`` (``idle`` before
        :meth:`ensure` has been called, ``loading`` while a fetch is running)."""
        with self._lock:
            if machine in self._cache:
                return self._cache[machine]
            if machine in self._inflight:
                return ("loading", [], "")
        return ("idle", [], "")

    def get_summary(self, machine: object) -> dict:
        """The cached summary dict for ``machine`` (D1) -- the substitution source
        for the pivot's ``summary`` header line. ``{}`` when the provider emitted
        a bare array (no summary) or nothing has loaded yet."""
        with self._lock:
            return dict(self._summaries.get(machine, {}))

    def invalidate(self, machine: object = None) -> None:
        """Drop cached results so the next :meth:`ensure` refetches. ``None``
        clears every machine (used after an action mutates the queue).

        Bumps :attr:`_gen` so a one-shot fetch already in flight when this runs
        discards its stale result instead of racing it back into the cache (see
        :meth:`_run_list`)."""
        with self._lock:
            self._gen += 1
            if machine is None:
                self._cache.clear()
                self._summaries.clear()
            else:
                self._cache.pop(machine, None)
                self._summaries.pop(machine, None)

    def repoll(self, machine: object) -> None:
        """Force a background, swap-in-place refetch for ``machine``.

        Unlike :meth:`ensure` it refetches even when ``machine`` is already
        cached; unlike :meth:`invalidate` + :meth:`ensure` it does **not** clear
        the cache first, so the current rows stay visible (no ``loading``
        flicker) until the fresh result lands and swaps in. A fetch already in
        flight is left to finish (idempotent). This is the registered-pivot
        analogue of the worktree loader's silent repoll (#1421): it lets an open
        Tasks pivot pick up tasks/cards created by *another* session (e.g. a
        claimer posting a steer card) without a manual reload or a restart.

        No-op for a ``stream`` pivot -- a streaming/``subscribe`` provider is
        already live (its held child applies deltas), so a forced refetch would
        just spawn a redundant stream."""
        if self._closed.is_set() or self.pivot.stream:
            return
        with self._lock:
            if machine in self._inflight:
                return
            self._inflight.add(machine)
            gen = self._gen
        threading.Thread(
            target=self._run_list, args=(machine, gen), daemon=True
        ).start()

    def close(self) -> None:
        """Tear down every tracked streaming child (picker exit). Idempotent --
        a held ``subscribe`` stream reader unblocks when its pipe closes, so no
        ``list --stream`` process is orphaned after the picker quits."""
        self._closed.set()
        with self._procs_lock:
            procs = list(self._procs)
            self._procs.clear()
        for proc in procs:
            _kill_proc_tree(proc)

    def _spawn_stream(self, argv: Sequence[str]) -> subprocess.Popen:
        """Spawn a tracked, killable streaming child (line-buffered stdout so
        rows surface as the provider flushes them). Registered in ``self._procs``
        so :meth:`close` tears it down. Mirrors
        :meth:`picker_tui.data_ssh.RemoteLoader._spawn_stream`."""
        if self._closed.is_set():
            raise RuntimeError("closed")
        kwargs: dict = dict(
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            bufsize=1,
        )
        if os.name == "posix":
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | _CREATE_NO_WINDOW  # headless-guard: allow: own process group for Ctrl-C signal delivery (+ no-window)
            )
        proc = subprocess.Popen(list(argv), **kwargs)
        with self._procs_lock:
            self._procs.append(proc)
        # Close the spawn/close race: if close() fired between the guard and the
        # Popen, it never saw this child -- kill it now.
        if self._closed.is_set():
            _kill_proc_tree(proc)
        return proc

    def _untrack(self, proc: subprocess.Popen) -> None:
        with self._procs_lock:
            if proc in self._procs:
                self._procs.remove(proc)

    def _run_list(self, machine: object, gen: object = None) -> None:
        ctx = {"machine": "" if machine is None else str(machine)}
        # D2: a ``stream`` pivot runs the streaming runner, which writes the
        # cache progressively (and, for ``subscribe``, keeps applying live
        # deltas). It self-heals to the one-shot path when the provider doesn't
        # understand ``--stream``. A non-stream pivot keeps the original
        # compute-then-write-once contract.
        if self.pivot.stream:
            self._run_list_stream(machine, ctx)
            return
        state, rows, err, summary = self._exec_list(ctx)
        with self._lock:
            self._inflight.discard(machine)
            # Drop a result whose fetch was invalidated mid-flight (a steer /
            # action cleared the cache after this fetch started) so stale data
            # can't overwrite the just-invalidated cache. The next ``ensure``
            # (cache miss, no longer in flight) refetches the current state.
            if gen is not None and gen != self._gen:
                return
            self._cache[machine] = (state, rows, err)
            self._summaries[machine] = summary

    def _run_list_stream(self, machine: object, ctx: Mapping[str, object]) -> None:
        """Streaming ``list`` runner (D2): Popen ``list … --stream``, consume the
        NDJSON envelope, and write the per-machine cache **in place** as frames
        arrive so the render loop paints progressively and (with ``subscribe``)
        live-updates.

        Envelope (line-delimited, one JSON object per flushed line):

        * ``begin`` -- optional roster hint (``count``); no cache effect.
        * ``row`` / ``entry`` -- an entry object (under ``entry``/``row``/``wt``
          or inline); accumulated by the pivot's ``id_field``. The first row
          flips the cache to ``ready``.
        * ``summary`` -- the D1 header-line substitution source.
        * ``delta`` -- an entry to add/replace in place (keyed by ``id_field``).
        * ``removed`` -- drop the entry named by ``id`` (or the entry's id).
        * ``done`` -- terminal success. ``error`` -- terminal failure (``message``).

        Falls back to the one-shot :meth:`_exec_list` when the provider's
        argparse rejects ``--stream`` (an older CLI) or emits a plain JSON array
        with no envelope, so ``stream: true`` is always safe to declare."""
        argv = _resolve_argv((*self.pivot.list_cmd, "--stream"), ctx)
        if not argv:
            self._finish(machine, ("error", [], "empty list command"), {})
            return
        try:
            proc = self._spawn_stream(argv)
        except FileNotFoundError:
            self._finish(machine, ("error", [], f"{argv[0]} not found on PATH"), {})
            return
        except Exception as exc:
            self._finish(machine, ("error", [], str(exc)[:200]), {})
            return

        by_id: dict[str, dict] = {}
        order: list[str] = []
        summary: dict = {}
        ready = False
        done = False
        saw_envelope = False
        err_frame = ""
        raw_lines: list[str] = []
        id_field = self.pivot.id_field

        # No overall deadline for a held ``subscribe`` stream (it runs until the
        # picker exits); a one-shot stream is watchdogged so a stalled producer
        # can't wedge the loader thread.
        timer: threading.Timer | None = None
        if not self.pivot.subscribe:
            timer = threading.Timer(STREAM_TIMEOUT, lambda: _kill_proc_tree(proc))
            timer.daemon = True
            timer.start()

        def publish() -> None:
            with self._lock:
                self._cache[machine] = ("ready", [by_id[i] for i in order], "")
                self._summaries[machine] = dict(summary)

        try:
            for raw in proc.stdout:  # type: ignore[union-attr]
                if self._closed.is_set():
                    break
                raw_lines.append(raw)
                obj = _parse_ndjson_line(raw)
                if obj is None:
                    continue
                typ = obj.get("type")
                if typ == "begin":
                    saw_envelope = True
                elif typ in ("row", "entry", "delta"):
                    saw_envelope = True
                    entry = _stream_entry(obj)
                    rid = entry.get(id_field)
                    if rid is None:
                        continue
                    rid = str(rid)
                    if rid not in by_id:
                        order.append(rid)
                    by_id[rid] = entry
                    ready = True
                    publish()
                elif typ == "removed":
                    saw_envelope = True
                    rid = obj.get("id")
                    if rid is None:
                        rid = _stream_entry(obj).get(id_field)
                    if rid is not None:
                        rid = str(rid)
                        if rid in by_id:
                            del by_id[rid]
                            order[:] = [i for i in order if i != rid]
                            publish()
                elif typ == "summary":
                    saw_envelope = True
                    raw_summary = obj.get("summary")
                    summary = dict(raw_summary) if isinstance(raw_summary, Mapping) else {
                        k: v for k, v in obj.items() if k != "type"
                    }
                    if ready:
                        publish()
                elif typ == "done":
                    saw_envelope = True
                    done = True
                elif typ == "error":
                    saw_envelope = True
                    err_frame = str(obj.get("message") or obj.get("error") or "stream error")
                    break
        except Exception as exc:
            if not err_frame:
                err_frame = str(exc)[:200]
        finally:
            if timer is not None:
                timer.cancel()
            try:
                _, stderr = proc.communicate(timeout=5)
            except Exception:
                _kill_proc_tree(proc)
                try:
                    _, stderr = proc.communicate(timeout=5)
                except Exception:
                    stderr = ""
            self._untrack(proc)

        if err_frame:
            self._finish(machine, ("error", [], err_frame[:200]), {})
            return
        if ready or done:
            # Fully or partially resolved (empty roster included) -- keep rows.
            self._finish(
                machine,
                ("ready", [by_id[i] for i in order], ""),
                dict(summary),
            )
            return
        # No envelope was spoken. Fall back to the one-shot list: an old CLI
        # rejects ``--stream`` (argparse), or the provider emitted a plain array.
        if _is_stream_unsupported(stderr) or not saw_envelope:
            state, rows, err, one_summary = self._exec_list(ctx)
            self._finish(machine, (state, rows, err), one_summary)
            return
        detail = (stderr or "").strip().splitlines()
        msg = detail[-1] if detail else f"exit {proc.returncode}"
        self._finish(machine, ("error", [], msg[:200]), {})

    def _finish(
        self,
        machine: object,
        cache: tuple[str, list, str],
        summary: dict,
    ) -> None:
        """Atomically write a terminal ``(state, rows, error)`` + summary and drop
        the in-flight marker (the streaming runner's single write-back point)."""
        with self._lock:
            self._cache[machine] = cache
            self._summaries[machine] = summary
            self._inflight.discard(machine)


    def _exec_list(self, ctx: Mapping[str, object]) -> tuple[str, list, str, dict]:
        argv = _resolve_argv(self.pivot.list_cmd, ctx)
        if not argv:
            return ("error", [], "empty list command", {})
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=LIST_TIMEOUT, check=False
            )
        except FileNotFoundError:
            return ("error", [], f"{argv[0]} not found on PATH", {})
        except (OSError, subprocess.SubprocessError) as exc:
            return ("error", [], str(exc)[:200], {})
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip().splitlines()
            msg = detail[-1] if detail else f"exit {proc.returncode}"
            return ("error", [], msg[:200], {})
        try:
            data = json.loads(proc.stdout or "[]")
        except ValueError:
            return ("error", [], "list command did not print JSON", {})
        # D1: accept a bare array (back-compat) OR a {entries, summary} object.
        rows, summary = parse_list_payload(data)
        return ("ready", rows, "", summary)

    # -- actions -------------------------------------------------------------

    def run_action(self, action, ctx: Mapping[str, object]) -> tuple[bool, str]:
        """Run one action's argv template against ``ctx``. Returns
        ``(ok, message)`` -- never raises, so the caller can surface the result
        in the status line."""
        return self.run_resolved(format_template(action.run, ctx))

    def run_resolved(self, argv: Sequence[str]) -> tuple[bool, str]:
        """Run an **already-substituted** argv (A5 form path + the shared sync
        run path). Resolves ``argv[0]`` on ``PATH`` and executes, returning
        ``(ok, message)`` and never raising. No placeholder substitution happens
        here -- the caller has already resolved every token (a form's
        ``{field.<name>}`` + entry tokens in one safe pass via
        :func:`format_form_template`, or a plain template via
        :func:`format_template`)."""
        argv = list(argv)
        if argv:
            resolved = shutil.which(argv[0])
            if resolved:
                argv = [resolved, *argv[1:]]
        if not argv:
            return (False, "empty action command")
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=ACTION_TIMEOUT, check=False
            )
        except FileNotFoundError:
            return (False, f"{argv[0]} not found on PATH")
        except (OSError, subprocess.SubprocessError) as exc:
            return (False, str(exc)[:200])
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip().splitlines()
            return (False, (detail[-1] if detail else f"exit {proc.returncode}")[:200])
        return (True, (proc.stdout or "").strip()[:200])

    def run_action_stream(
        self,
        action,
        ctx: Mapping[str, object],
        on_frame,
        should_cancel=None,
    ) -> tuple[bool, str]:
        """Run a **progress-reporting** action (D4): Popen the action argv and
        consume its NDJSON progress envelope, calling ``on_frame(pct, msg)`` for
        each ``{"type":"progress","pct":..,"msg":..}`` line. Terminal frames are
        ``{"type":"done"[,"message"]}`` (success) and ``{"type":"error",
        "message":..}`` (failure). Returns ``(ok, final_message)`` and never
        raises, so the caller can surface the outcome once the modal closes.

        ``should_cancel`` (optional callable) is polled between frames; when it
        returns truthy the child is killed and the run reports cancellation. The
        child is tracked like a streaming ``list`` (via :meth:`_spawn_stream`), so
        :meth:`close` also tears it down on picker exit. Falls back to treating a
        non-envelope exit as a plain sync result so a mis-declared ``progress``
        action still completes."""
        argv = _resolve_argv(action.run, ctx)
        if not argv:
            return (False, "empty action command")
        try:
            proc = self._spawn_stream(argv)
        except FileNotFoundError:
            return (False, f"{argv[0]} not found on PATH")
        except Exception as exc:
            return (False, str(exc)[:200])

        # A blocked stdout readline can't observe ``should_cancel`` between
        # frames, so a background poller actively kills the child when cancel is
        # requested (or the runtime is closing) -- the kill closes the pipe and
        # unblocks the reader promptly, even for a hung action.
        stop_poll = threading.Event()

        def _poll_cancel() -> None:
            while not stop_poll.wait(0.15):
                if (should_cancel is not None and should_cancel()) or self._closed.is_set():
                    _kill_proc_tree(proc)
                    return

        poller: threading.Thread | None = None
        if should_cancel is not None:
            poller = threading.Thread(target=_poll_cancel, daemon=True)
            poller.start()

        ok = False
        done = False
        final_msg = ""
        saw_envelope = False
        raw_lines: list[str] = []
        try:
            for raw in proc.stdout:  # type: ignore[union-attr]
                if (should_cancel is not None and should_cancel()) or self._closed.is_set():
                    break
                raw_lines.append(raw)
                obj = _parse_ndjson_line(raw)
                if obj is None:
                    continue
                typ = obj.get("type")
                if typ == "progress":
                    saw_envelope = True
                    pct = obj.get("pct")
                    try:
                        pct = float(pct) if pct is not None else None
                    except (TypeError, ValueError):
                        pct = None
                    try:
                        on_frame(pct, str(obj.get("msg") or ""))
                    except Exception:
                        pass
                elif typ == "done":
                    saw_envelope = True
                    ok = True
                    done = True
                    final_msg = str(obj.get("message") or obj.get("msg") or "done")
                    break
                elif typ == "error":
                    saw_envelope = True
                    ok = False
                    done = True
                    final_msg = str(obj.get("message") or obj.get("error") or "error")
                    break
        except Exception as exc:
            final_msg = str(exc)[:200]
        finally:
            stop_poll.set()
            cancelled = (should_cancel is not None and should_cancel()) or self._closed.is_set()
            if cancelled and not done:
                _kill_proc_tree(proc)
            try:
                _, stderr = proc.communicate(timeout=5)
            except Exception:
                _kill_proc_tree(proc)
                try:
                    _, stderr = proc.communicate(timeout=5)
                except Exception:
                    stderr = ""
            self._untrack(proc)

        if cancelled and not done:
            return (False, "cancelled")
        if done:
            return (ok, final_msg[:200])
        # No terminal envelope frame. Fall back to the process exit + output so a
        # non-progress CLI (or one that only printed a blob) still resolves.
        rc = proc.returncode
        if saw_envelope or rc == 0:
            tail = "".join(raw_lines).strip().splitlines()
            return (rc == 0, (tail[-1] if tail else final_msg)[:200])
        detail = (stderr or "").strip().splitlines()
        return (False, (detail[-1] if detail else f"exit {rc}")[:200])


def run_config_section(action, ctx: Mapping[str, object]) -> tuple[bool, str]:
    """Run a contributed :class:`~picker_tui.pivots.ConfigSection` against
    ``ctx`` as a subprocess (argv[0] resolved on ``PATH``). Returns
    ``(ok, message)`` and never raises, so the picker can surface the outcome in
    its status line. Mirrors :func:`run_worktree_action` -- a config section is
    the same run-on-Enter shape, but rides the ⚙ Configuration menu rather than
    a worktree row, and is global (not per-worktree) in scope."""
    argv = _resolve_argv(action.run, ctx)
    if not argv:
        return (False, "empty config command")
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=ACTION_TIMEOUT, check=False
        )
    except FileNotFoundError:
        return (False, f"{argv[0]} not found on PATH")
    except (OSError, subprocess.SubprocessError) as exc:
        return (False, str(exc)[:200])
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return (False, (detail[-1] if detail else f"exit {proc.returncode}")[:200])
    return (True, (proc.stdout or "").strip()[:200])


def run_worktree_action(action, ctx: Mapping[str, object]) -> tuple[bool, str]:
    """Run a contributed :class:`~picker_tui.pivots.WorktreeAction` against
    ``ctx`` as a subprocess (argv[0] resolved on ``PATH``). Returns
    ``(ok, message)`` and never raises, so the picker can surface the outcome in
    its status line. Mirrors :meth:`RegisteredPivotRuntime.run_action` but is
    stand-alone: worktree actions are not bound to a pivot's runtime."""
    argv = _resolve_argv(action.run, ctx)
    if not argv:
        return (False, "empty action command")
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=ACTION_TIMEOUT, check=False
        )
    except FileNotFoundError:
        return (False, f"{argv[0]} not found on PATH")
    except (OSError, subprocess.SubprocessError) as exc:
        return (False, str(exc)[:200])
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return (False, (detail[-1] if detail else f"exit {proc.returncode}")[:200])
    return (True, (proc.stdout or "").strip()[:200])
