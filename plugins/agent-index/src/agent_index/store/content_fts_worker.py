"""FTS worker transport and cross-process file ownership for ContentStore."""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys

from agent_procutil import no_window_kwargs

from .source_layout import SourceLayoutError


def fts_retryable(message: str) -> bool:
    """The established LanceDB conflict classifier, shared across the child boundary."""
    message = message.lower()
    return "retryable" in message or "commit conflict" in message


class _ContentFtsWorkerMixin:
    """Own child-process execution and file-lock lifetime; state stays on the store."""

    @contextlib.contextmanager
    def _fts_file_lock(self):
        """Best-effort *cross-process* exclusive lock around ``create_fts_index``.

        The in-process ``_fts_lock`` cannot coordinate with a *different* OS
        process (e.g. a standalone ``agent-index reindex`` run, or a serve process that
        died mid-build) issuing its own LanceDB ``CreateIndex``. Two concurrent
        ``CreateIndex`` transactions livelock LanceDB's optimistic-concurrency
        resolver and can leave the index stuck on a version forever (#1818).

        Yields ``True`` if the lock was acquired, ``False`` if another process
        holds it -- in which case the caller should skip this rebuild rather
        than pile on with a competing ``CreateIndex``.
        """
        try:
            import fcntl
        except ImportError:  # non-POSIX: fall back to in-process serialization
            yield True
            return

        lock_path = os.path.join(self._db_path, ".fts_index.lock")
        fd = None
        acquired = False
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                acquired = False
            yield acquired
        finally:
            if fd is not None:
                if acquired:
                    with contextlib.suppress(OSError):
                        fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _run_fts_build(self, *, full: bool, timeout: float = 300.0) -> None:
        """Update the BM25 FTS index in a FRESH subprocess (optimize [+ create]).

        LanceDB's sync ``create_fts_index`` bridges to a module-singleton
        background asyncio loop; inside the agent-index server's full lifespan that
        bridge DEADLOCKS (#3587) -- the worker blocks forever on the
        background-loop result while the loop sits idle, so uvicorn never binds
        and the service is DOWN. A fresh interpreter has a pristine background
        loop, so the build completes normally out of process. Running it out of
        process ALSO makes the build KILLABLE with a hard timeout (never-strand,
        #1208) and lets us ``optimize()`` (compact) the table first -- a full
        reindex leaves hundreds of tiny fragments/versions, the pathological
        state that made the in-process build crawl before it deadlocked.

        ``full`` controls whether a from-scratch ``create_fts_index(...,
        replace=True)`` runs after ``optimize()``. Per LanceDB's own docs
        (Reindexing / Incremental Reindexing), ``optimize()`` already performs
        compaction, retention pruning, *and* an incremental update of any
        existing vector/scalar/FTS index against newly-ingested rows -- it does
        NOT require ``create_fts_index`` to pick up new data. Forcing
        ``replace=True`` on every dirty-triggered rebuild discards the existing
        BM25 index and rebuilds it from scratch over the *entire* content
        table regardless of how small the actual delta is. Only the first-ever
        build, a recovery from an index that was never successfully created
        (``_fts_available`` False), or the caller's own escalation from a
        non-retryable incremental failure (#2951, see
        ``_rebuild_fts_locked``), needs the full ``replace=True`` path.

        On the ``full`` path, an ``optimize()`` failure is swallowed (only
        logged) because ``create_fts_index(replace=True)`` still runs
        afterward and is the actual source of correctness there. On the
        incremental (``full=False``) path there is no follow-up
        ``create_fts_index`` call, so ``optimize()`` succeeding IS the only
        thing that updates the index -- swallowing its failure there would let
        the caller believe the rebuild succeeded while the on-disk index
        silently went stale. So the incremental path lets ``optimize()``
        raise, causing the subprocess to exit non-zero and the caller's
        existing retry/backoff logic to treat it as a genuine failure.
        """
        if self._source_domain is not None:
            with self._source_domain.fts_handoff() as nonce:
                code = (
                    "import sys,lancedb; "
                    "from agent_index.store.source_layout import child_fts_owner; "
                    "\nwith child_fts_owner(sys.argv[1],sys.argv[3]):"
                    "\n t=lancedb.connect(sys.argv[1]).open_table(sys.argv[2]); t.optimize()"
                    "\n if sys.argv[4]=='1': t.create_fts_index('content',replace=True)"
                )
                proc = subprocess.run(
                    [sys.executable, "-I", "-B", "-c", code,
                     self._db_path, self._table_name, nonce, "1" if full else "0"],
                    capture_output=True, text=True, timeout=timeout, check=False,
                    **no_window_kwargs(),
                )
                if proc.returncode != 0:
                    reason = (
                        "commit conflict" if fts_retryable(proc.stderr[-4096:])
                        else "unclassified child failure"
                    )
                    raise SourceLayoutError(
                        f"source-aware FTS builder failed (rc={proc.returncode}): {reason}"
                    )
            return
        if full:
            body = (
                "try:\n"
                "    t.optimize()\n"
                "except Exception as e:\n"
                "    print(f'optimize skipped: {e}', file=sys.stderr)\n"
                "t.create_fts_index('content', replace=True)\n"
            )
        else:
            body = "t.optimize()\n"
        code = (
            "import sys, lancedb\n"
            "t = lancedb.connect(sys.argv[1]).open_table(sys.argv[2])\n"
            + body
        )
        proc = subprocess.run(
            [sys.executable, "-B", "-c", code, self._db_path, self._table_name],
            capture_output=True,
            text=True,
            timeout=timeout,
            **no_window_kwargs(),
        )
        if proc.returncode != 0:
            # Surface the child's stderr so the caller's retry loop can still
            # distinguish a (retryable) LanceDB commit conflict from a hard error.
            raise RuntimeError(
                f"FTS build subprocess failed (rc={proc.returncode}): "
                f"{proc.stderr.strip()[-500:]}"
            )
