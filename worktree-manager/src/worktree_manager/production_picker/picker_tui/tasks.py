"""Runtime for cross-plugin *registered* pivots in the Textual picker.

A registered pivot (see :mod:`picker_tui.pivots`) declares a ``list`` command
that prints a JSON array of entries, and an ``actions`` set of argv templates.
This module runs those commands -- always as a subprocess against the
contributing plugin's CLI on ``PATH``, never a cross-venv Python import -- so
the picker stays decoupled from the plugin's runtime.

That decoupling has to cover the child's *environment* too: the picker itself
is one plugin's own process (agent-worktrees), so its environment can carry
agent-worktrees-scoped identity vars (``COPILOT_PLUGIN_ROOT``,
``COPILOT_EXTENSIONS_CONTEXT``, and friends). A pivot's ``list``/action
command is almost always a *different* plugin's CLI (e.g. ``agent-dispatch``);
inheriting those vars unchanged makes that CLI's own installation-context
self-check see a foreign plugin root and reject the invocation outright (a
"payload context mismatch" that has nothing to do with the actual command).
Every subprocess this module spawns therefore runs under
:func:`_child_process_env`, never the picker's raw ``os.environ``.

:class:`RegisteredPivotRuntime` keeps the picker responsive: the ``list``
command runs on a daemon thread and the result is cached per machine, so the
render loop only ever reads a snapshot. Everything degrades gracefully -- a
missing CLI, a non-zero exit, or malformed JSON becomes an ``error`` state the
pivot surfaces, never an exception that breaks the picker.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
from collections.abc import Mapping, Sequence

from agent_procutil import no_window_flags, spawn_sync_in_kill_on_close_job
from agent_procutil.owned_process import run_caller_owned

from ... import picker_review
from .command_transport import (
    _CHILD_ENV_UNSET as _CHILD_ENV_UNSET,
    child_process_env as _child_process_env,
    command_result,
    resolve_command as _resolve_command,
)
from .configuration_context import run_list
from .pivots import RegisteredPivot, format_template, parse_list_payload as parse_list_payload
from .pivot_requests import PivotRequestsMixin

#: Hard cap on how long a pivot's ``list``/action command may run.
LIST_TIMEOUT = 20.0
ACTION_TIMEOUT = 30.0
#: Hard cap for a ``create_action`` field's ``options_command`` -- this runs
#: synchronously (off-thread, see `_run_bg`) right before a modal opens, so it
#: is bounded tighter than a full action's own timeout: a slow/hung vocabulary
#: source should degrade to the field's "Other…" free-text fallback promptly,
#: not stall opening the dialog.
OPTIONS_COMMAND_TIMEOUT = 5.0
STRICT_OPTIONS_COMMAND_TIMEOUT = 20.0

#: Phase 0 (render-perf follow-up, #4762, 2026-10-01):
#: a held ``subscribe`` stream's producer can exit (crash, or a plain EOF with
#: no ``done``/``error`` frame) without that ever being distinguished from a
#: genuinely-still-live channel -- ``repoll()`` used to no-op unconditionally
#: whenever ``pivot.subscribe`` was set, trusting the channel was still open
#: forever. These bound the reconnect policy: how many times
#: :meth:`RegisteredPivotRuntime._run_list_stream` re-spawns a dropped
#: ``subscribe`` channel before giving up and falling back to ordinary
#: one-shot repolling, and the backoff between attempts.
SUBSCRIBE_MAX_RECONNECT_ATTEMPTS = 5
SUBSCRIBE_RECONNECT_BACKOFF_SECS = 2.0

def resolve_dynamic_options(argv: Sequence[str], *, strict: bool = False) -> list[str]:
    """Run a ``create_action`` field's ``options_command`` and return its
    JSON array of strings, or (by default) ``[]`` on failure (not found, non-zero exit,
    timeout, malformed/non-array output) -- this is a soft, best-effort
    enrichment: a field that declares ``options_command`` always has
    ``allow_other`` auto-forced true by
    :func:`pivot_create_action.parse_create_action`, so an empty result still
    leaves the field answerable via its "Other…" free-text fallback rather
    than ever blocking the create dialog from opening. Strict configuration
    choices use the ordinary read-query budget and raise on failure instead."""
    argv = list(argv)
    if not argv:
        if strict:
            raise ValueError("empty options command")
        return []
    if picker_review.active():
        if strict:
            raise ValueError(picker_review.MESSAGE)
        return []
    try:
        argv = _resolve_command(argv)
        proc = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=STRICT_OPTIONS_COMMAND_TIMEOUT if strict else OPTIONS_COMMAND_TIMEOUT,
            check=False, env=_child_process_env(), creationflags=no_window_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if strict:
            raise ValueError(f"options command failed: {exc}") from exc
        return []
    if proc.returncode != 0:
        if strict:
            detail = (proc.stderr or "").strip() or f"exit {proc.returncode}"
            raise ValueError(f"options command failed: {detail}")
        return []
    try:
        data = json.loads(proc.stdout or "[]")
    except ValueError as exc:
        if strict:
            raise ValueError("options command returned invalid JSON") from exc
        return []
    if not isinstance(data, list) or any(not isinstance(item, str) for item in data):
        if strict:
            raise ValueError("options command must return a JSON array of strings")
        return []
    if strict and (not data or any(not item.strip() for item in data)):
        raise ValueError("options command returned no usable choices")
    return data


def resolve_form_fields(
    fields: Sequence[Mapping[str, object]], *, strict: bool = False
) -> list[dict[str, object]]:
    resolved = []
    for field in fields:
        merged = dict(field)
        command = field.get("options_command")
        if command:
            from .pivot_actions import _as_argv

            try:
                argv = _as_argv(command, where="form field options_command")
                live = resolve_dynamic_options(argv, strict=strict)
            except ValueError as exc:
                raise ValueError(
                    f"field {field['name']!r} options_command: {exc}"
                ) from exc
            if live:
                merged["options"] = live
        resolved.append(merged)
    return resolved


#: Overall watchdog for a one-shot (non-``subscribe``) streaming ``list``: a
#: stalled producer is killed after this many seconds, but rows already received
#: are kept. A ``subscribe`` (held/live) stream has no overall deadline.
STREAM_TIMEOUT = 30.0

_CREATE_NO_WINDOW = no_window_flags()


def _kill_proc_tree(proc: subprocess.Popen) -> None:
    """Best-effort terminate a streaming child (and its process group on posix,
    so a ``list --stream`` that shelled out to ``gh`` dies with it). Mirrors
    :func:`picker_tui.data_ssh._kill_proc_tree`; never raises."""
    job = getattr(proc, "_picker_job", None)
    if job is not None:
        job.close()
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
    return _resolve_command(format_template(template, ctx))


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


def prewarm_optional_modules() -> None:
    """Import the modules a registered pivot's first switch/render needs,
    synchronously in the caller's own thread.

    ``engine.PickerScreen._machine_key_map`` lazily imports ``data_ssh`` (and,
    transitively, its own ``roster``/``provider_sources``/``source_identity``)
    on first use, so a picker with no registered pivots never pays that cost.
    But that meant the *first* switch onto a registered pivot (e.g. Tasks)
    paid a real, synchronous multi-module import on the render/key-handling
    thread -- profiling a real tab-switch keypress showed it accounting for
    roughly 40% of the total switch latency, exactly the momentary freeze
    reported against this pivot (see the Tasks-pane-UX-overhaul effort's own
    bug entry). A pure import has no side effects, so warming it here makes
    that cost disappear from the keypress entirely instead of relocating it.

    Also imports ``pivots`` (profiled at ~0.6s cold, non-trivial):
    ``_setup_live_pivots()``'s own registry scan imports it moments later on
    this same thread, but the FIRST render-thread call that needs it --
    ``_wt_submenu_verbs()``'s cross-plugin action loop, reached the instant
    the operator opens ANY worktree row's Actions menu -- can race ahead of
    that scan now that the picker paints (and accepts keys) immediately.
    Warming it here, ahead of that scan, closes the same class of gap
    ``self.src.LOCAL`` had (#picker-menu-open-latency) instead of leaving
    another render-thread caller to pay for it first.

    Call this directly from a thread that is *already* off the UI thread
    (e.g. ``engine.py``'s ``_setup_live_pivots``) so the import reliably
    finishes before pivots are installed/activated -- spawning ANOTHER
    background thread here instead would only shrink the freeze window
    rather than close it: CPython's per-module import lock would still make
    a render-thread caller block on the same import if a keypress landed
    mid-warm-up. A caller reachable from the UI thread (the shared non-live
    setup/reload path) must wrap this call in its own worker thread itself
    instead."""
    try:
        from . import data_ssh  # noqa: F401
    except Exception:
        pass
    try:
        from . import pivots  # noqa: F401
    except Exception:
        pass


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



class RegisteredPivotRuntime(PivotRequestsMixin):
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
        # Phase 0: whether a ``subscribe`` pivot's held channel is CURRENTLY
        # believed live for a given machine -- distinct from ``pivot.subscribe``
        # (the manifest's static declaration), which never reflects whether the
        # channel actually dropped. ``repoll()`` only skips a machine while this
        # is True; a dropped/exhausted channel clears it so normal repolling
        # resumes. ``_subscribe_retries`` counts consecutive reconnect attempts
        # since the last successful ``ready`` frame, reset on success.
        self._subscribe_live: dict[object, bool] = {}
        self._subscribe_retries: dict[object, int] = {}
        self._init_requests()

    # -- listing -------------------------------------------------------------

    def ensure(self, machine: object) -> None:
        """Kick off a background ``list`` fetch for ``machine`` if not already
        cached or in flight. Cheap + idempotent -- safe to call every frame."""
        self._schedule_list(machine)

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

        No-op only while a ``subscribe`` pivot's channel is CURRENTLY believed
        live (``_subscribe_live``) -- its held child channel applies deltas in
        place, so a forced refetch would spawn a redundant second channel. Once
        that channel has dropped and exhausted its own reconnect attempts (Phase
        0, #4762), ``_subscribe_live`` clears and this
        resumes normal one-shot repolling -- a ``subscribe`` pivot's manifest
        declaration alone no longer freezes it forever. A one-shot ``stream``
        pivot (streams once then exits) is NOT already-live, so it is repolled
        like any other -- the streaming runner re-runs and swaps rows in via
        :meth:`_finish`."""
        if self._closed.is_set():
            return
        # Default to "treat as live" (no-op) when a subscribe pivot hasn't
        # recorded anything yet -- e.g. repoll() called before its first
        # ensure() -- matching the manifest's own declared intent until a
        # fetch has actually run and proven otherwise. Only an EXPLICIT
        # ``False`` (recorded by a genuinely exhausted reconnect budget, Phase
        # 0) clears this no-op.
        if self.pivot.subscribe and self._subscribe_live.get(machine, True):
            return
        self._schedule_list(machine, force=True)

    def close(self) -> None:
        """Tear down every tracked streaming child (picker exit). Idempotent --
        a held ``subscribe`` stream reader unblocks when its pipe closes, so no
        ``list --stream`` process is orphaned after the picker quits."""
        self._closed.set()
        self._cancel_pending_lists()
        with self._procs_lock:
            procs = list(self._procs)
            self._procs.clear()
        for proc in procs:
            _kill_proc_tree(proc)
        self._join_scope_retirements()

    def _spawn_stream(self, argv: Sequence[str]) -> subprocess.Popen:
        """Spawn a tracked, killable streaming child (line-buffered stdout so
        rows surface as the provider flushes them). Registered in ``self._procs``
        so :meth:`close` tears it down. Mirrors
        :meth:`picker_tui.data_ssh.RemoteLoader._spawn_stream`."""
        picker_review.require_writable()
        if self._closed.is_set():
            raise RuntimeError("closed")
        kwargs: dict = dict(
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            bufsize=1,
            env=_child_process_env(),
        )
        if os.name == "posix":
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | _CREATE_NO_WINDOW  # headless-guard: allow: own process group for Ctrl-C signal delivery (+ no-window)
            )
        proc, job = spawn_sync_in_kill_on_close_job(
            list(argv), require_job=True, allow_breakaway=False, **kwargs,
        )
        proc._picker_job = job
        proc._picker_scope = getattr(self._request_context, "machine", None)
        with self._procs_lock:
            self._procs.append(proc)
        # Close the spawn/close race: if close() fired between the guard and the
        # Popen, it never saw this child -- kill it now.
        with self._lock:
            current = self._generation_matches(
                proc._picker_scope, getattr(self._request_context, "generation", None),
            )
        if self._closed.is_set() or not current:
            _kill_proc_tree(proc)
        return proc

    def _untrack(self, proc: subprocess.Popen) -> None:
        with self._procs_lock:
            if proc in self._procs:
                self._procs.remove(proc)
        job = getattr(proc, "_picker_job", None)
        if job is not None:
            job.close()

    def _run_list(self, machine: object, gen: object = None) -> None:
        run_list(self, machine, gen)

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
        with no envelope, so ``stream: true`` is always safe to declare.

        For a ``subscribe`` pivot, any termination of this stream -- an
        explicit ``error`` frame, a plain EOF with no ``done``/``error`` (the
        gap Phase 0, #4762, closes), or even an
        unexpected ``done`` -- is treated as the held channel dropping, not as
        a normal finish: :meth:`_handle_subscribe_drop` decides whether to
        reconnect (bounded retries + backoff, keeping current rows visible) or
        demote the pivot back to ordinary repolling."""
        if self.pivot.subscribe:
            self._subscribe_live[machine] = True
        # The whole point of `subscribe` is a held channel receiving live
        # deltas -- without `--subscribe` on argv, the provider runs its
        # one-shot envelope and exits immediately, and every "termination"
        # (a totally normal exit, not a drop) falls through to
        # `_handle_subscribe_drop`'s reconnect path: the pivot ends up
        # re-spawning the CLI on every backoff tick instead of ever holding
        # one process open (#4840 review).
        stream_flags = ("--stream", "--subscribe") if self.pivot.subscribe else ("--stream",)
        argv = format_template((*self.pivot.list_cmd, *stream_flags), ctx)
        if not argv:
            self._subscribe_live[machine] = False
            self._finish(machine, ("error", [], "empty list command"), {})
            return
        try:
            argv = _resolve_command(argv)
            proc = self._spawn_stream(argv)
        except FileNotFoundError:
            self._subscribe_live[machine] = False
            self._finish(machine, ("error", [], f"{argv[0]} not found on PATH"), {})
            return
        except Exception as exc:
            self._subscribe_live[machine] = False
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
        timed_out = threading.Event()
        first_read_lock = threading.Lock()
        initial_read_complete = False
        def expired():
            with first_read_lock:
                if self.pivot.subscribe and initial_read_complete:
                    return
                timed_out.set()
            _kill_proc_tree(proc)
        timer = threading.Timer(STREAM_TIMEOUT, expired)
        timer.daemon = True
        timer.start()

        def publish() -> None:
            nonlocal initial_read_complete
            with first_read_lock:
                if timed_out.is_set():
                    return
                with self._lock:
                    generation = getattr(self._request_context, "generation", None)
                    if not self._generation_matches(machine, generation):
                        return
                    self._cache[machine] = ("ready", [by_id[i] for i in order], "")
                    self._summaries[machine] = dict(summary)
                initial_read_complete = True
                if self.pivot.subscribe:
                    timer.cancel()
            self._release_startup(machine)

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
                    if ready or done:
                        publish()
                elif typ == "done":
                    saw_envelope = True
                    done = True
                    if not ready:
                        # A held `subscribe` channel keeps running past `done`
                        # (it's not EOF) -- an empty initial board must still
                        # publish now, or the pivot stays `loading` forever
                        # waiting for a `row` that may never come (#4840
                        # review). A non-empty board already published via
                        # `row`/`delta` above. Deliberately does NOT set
                        # `ready` -- that flag also drives `had_rows` for the
                        # subscribe-reconnect retry budget (`_handle_
                        # subscribe_drop`), which must still treat an
                        # always-empty channel as never having delivered a
                        # real row, so it exhausts normally rather than
                        # reconnecting forever.
                        publish()
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

        if timed_out.is_set():
            self._subscribe_live[machine] = False
            self._finish(machine, (
                "error", [by_id[i] for i in order],
                f"{self.pivot.label}: no completed initial read within {STREAM_TIMEOUT:g}s; press r to retry",
            ), summary)
            return
        if err_frame:
            self._handle_subscribe_drop(
                machine, ctx, ("error", [], err_frame[:200]), {}, had_rows=ready
            )
            return
        if ready or done:
            # Fully or partially resolved (empty roster included) -- keep rows.
            self._handle_subscribe_drop(
                machine, ctx,
                ("ready", [by_id[i] for i in order], ""),
                dict(summary), had_rows=ready,
            )
            return
        # No envelope was spoken. Fall back to the one-shot list: an old CLI
        # rejects ``--stream`` (argparse), or the provider emitted a plain array.
        # A permanent demotion (the CLI doesn't understand --stream at all), not
        # a transient drop -- never worth a subscribe reconnect attempt.
        if _is_stream_unsupported(stderr) or not saw_envelope:
            self._subscribe_live[machine] = False
            state, rows, err, one_summary = self._exec_list(ctx)
            self._finish(machine, (state, rows, err), one_summary)
            return
        detail = (stderr or "").strip().splitlines()
        msg = detail[-1] if detail else f"exit {proc.returncode}"
        self._handle_subscribe_drop(
            machine, ctx, ("error", [], msg[:200]), {}, had_rows=ready
        )

    def _handle_subscribe_drop(
        self,
        machine: object,
        ctx: Mapping[str, object],
        result: tuple[str, list, str],
        summary: dict,
        *,
        had_rows: bool,
    ) -> None:
        """Common tail for :meth:`_run_list_stream` (Phase 0,
        #4762, 2026-10-01): publish the terminal
        result, then -- only for a ``subscribe`` pivot, whose channel is
        supposed to stay open until the picker exits -- decide whether this
        termination (an ``error`` frame, a plain EOF, or even an unexpected
        ``done``) is a transient drop worth reconnecting, or whether the
        reconnect budget is exhausted and the pivot should fall back to
        ordinary repolling instead of silently freezing on stale rows forever.

        A non-``subscribe`` pivot (one-shot ``stream``) just finishes
        normally -- EOF ending its lifecycle is the designed, not an
        exceptional, outcome."""
        generation = getattr(self._request_context, "generation", None)
        with self._lock:
            if not self._generation_matches(machine, generation):
                return
        self._finish(machine, result, summary)
        if not self.pivot.subscribe:
            return
        with self._lock:
            if not self._generation_matches(machine, generation) or machine in self._resetting_scopes:
                return
            if had_rows:
                self._subscribe_retries[machine] = 0
            attempts = self._subscribe_retries.get(machine, 0)
            self._subscribe_live[machine] = False
            if self._closed.is_set() or attempts >= SUBSCRIBE_MAX_RECONNECT_ATTEMPTS:
                return
            self._subscribe_retries[machine] = attempts + 1
            self._inflight.add(machine)
            self._reconnecting_scopes.add(machine)

        def _reconnect() -> None:
            if self._closed.wait(SUBSCRIBE_RECONNECT_BACKOFF_SECS):
                return
            with self._lock:
                if not self._generation_matches(machine, generation):
                    return
                self._inflight.discard(machine)
                self._reconnecting_scopes.discard(machine)
            self._schedule_list(machine, force=True)

        threading.Thread(target=_reconnect, daemon=True).start()

    def _exec_list(self, ctx: Mapping[str, object]) -> tuple[str, list, str, dict]:
        from .pivot_list_command import exec_list
        return exec_list(self, ctx)

    # -- actions -------------------------------------------------------------

    def run_action(self, action, ctx: Mapping[str, object]) -> tuple[bool, str]:
        """Run one action's argv template against ``ctx``. Returns
        ``(ok, message)`` -- never raises, so the caller can surface the result
        in the status line."""
        return self.run_resolved(format_template(action.run, ctx))

    def run_resolved(
        self, argv: Sequence[str], *, preserve_output: bool = False,
    ) -> tuple[bool, str]:
        """Run an **already-substituted** argv (A5 form path + the shared sync
        run path). Resolves ``argv[0]`` on ``PATH`` and executes, returning
        ``(ok, message)`` and never raising. No placeholder substitution happens
        here -- the caller has already resolved every token (a form's
        ``{field.<name>}`` + entry tokens in one safe pass via
        :func:`format_form_template`, or a plain template via
        :func:`format_template`). ``preserve_output`` retains full receipts on
        both success and failure for full-content contributed command results."""
        proc, error = self._invoke_resolved(
            argv, timeout=ACTION_TIMEOUT, **({"preserve_output": True} if preserve_output else {}),
        )
        if proc is None:
            return False, error
        return command_result(proc, preserve_output=preserve_output)

    def preview_resolved(self, argv: Sequence[str]) -> tuple[bool, str]:
        """Keep successful stdout separate from diagnostics for strict JSON parsing."""
        return self.run_json_resolved(argv, timeout=LIST_TIMEOUT)

    def run_json_resolved(
        self, argv: Sequence[str], *, timeout: float | None = None,
    ) -> tuple[bool, str]:
        """Run a declared JSON command without mixing successful stdout and stderr."""
        proc, error = self._invoke_resolved(
            argv, timeout=ACTION_TIMEOUT if timeout is None else timeout, preserve_output=True,
        )
        if proc is None:
            return False, error
        if proc.returncode:
            return False, "\n".join(
                part for part in (proc.stdout.strip(), proc.stderr.strip(), f"exit {proc.returncode}")
                if part
            )
        return True, proc.stdout

    def _invoke_resolved(
        self, argv: Sequence[str], *, timeout: float, preserve_output: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str] | None, str]:
        argv = list(argv)
        if picker_review.active():
            return None, picker_review.MESSAGE
        if not argv:
            return None, "empty action command"
        try:
            argv = _resolve_command(argv)
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout,
                check=False, env=_child_process_env(), creationflags=no_window_flags(),
            )
        except FileNotFoundError:
            return None, f"{argv[0]} not found on PATH"
        except subprocess.TimeoutExpired as exc:
            if not preserve_output:
                return None, str(exc)[:200]

            def text(value):
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""

            parts = [text(exc.stdout).strip()]
            if exc.stderr:
                parts.append("stderr:\n" + text(exc.stderr).strip())
            parts.append(f"Command timed out after {timeout:g}s")
            return None, "\n\n".join(part for part in parts if part)
        except (OSError, subprocess.SubprocessError) as exc:
            return None, str(exc)[:200]
        return proc, ""

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
        if picker_review.active():
            return False, picker_review.MESSAGE
        argv = format_template(action.run, ctx)
        if not argv:
            return (False, "empty action command")
        try:
            argv = _resolve_command(argv)
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


def run_config_section(
    action, ctx: Mapping[str, object], *, fields: Mapping[str, object] | None = None
) -> tuple[bool, str]:
    """Run a contributed :class:`~picker_tui.pivots.ConfigSection` against
    ``ctx`` as a subprocess (argv[0] resolved on ``PATH``). Returns
    ``(ok, message)`` and never raises, so the picker can surface the outcome in
    its status line. Mirrors :func:`run_worktree_action` -- a config section is
    the same run-on-Enter shape, but rides the ⚙ Configuration menu rather than
    a worktree row, and is global (not per-worktree) in scope."""
    if picker_review.active():
        return False, picker_review.MESSAGE
    if fields is None:
        argv = format_template(action.run, ctx)
    else:
        from .pivot_actions import format_form_template

        # Field values are inserted once and never reinterpreted as tokens.
        argv = format_form_template(action.run, ctx, fields)
    if not argv:
        return (False, "empty config command")
    try:
        argv = _resolve_command(argv)
        if fields is None:
            proc = run_caller_owned(
                argv, timeout=ACTION_TIMEOUT, env=_child_process_env(),
                max_stdout_bytes=8 * 1024 * 1024, max_stderr_bytes=256 * 1024,
            )
        else:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=ACTION_TIMEOUT,
                check=False, env=_child_process_env(), creationflags=no_window_flags(),
            )
    except FileNotFoundError:
        return (False, f"{argv[0]} not found on PATH")
    except subprocess.TimeoutExpired as exc:
        if fields is None:
            return False, f"{getattr(action, 'label', 'Configuration')} read timed out after {exc.timeout:g}s."
        return False, str(exc)[:200]
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
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
    if picker_review.active():
        return False, picker_review.MESSAGE
    argv = format_template(action.run, ctx)
    if not argv:
        return (False, "empty action command")
    try:
        argv = _resolve_command(argv)
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=ACTION_TIMEOUT,
            check=False, env=_child_process_env(), creationflags=no_window_flags(),
        )
    except FileNotFoundError:
        return (False, f"{argv[0]} not found on PATH")
    except (OSError, subprocess.SubprocessError) as exc:
        return (False, str(exc)[:200])
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return (False, (detail[-1] if detail else f"exit {proc.returncode}")[:200])
    return (True, (proc.stdout or "").strip()[:200])
