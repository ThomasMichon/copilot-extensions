"""Process-exit helper that dodges a CPython 3.13 interpreter-shutdown crash.

On Windows, CPython 3.13 has been observed to crash deterministically with an
access violation inside ``Py_FinalizeEx``'s object-finalizer dispatch at
interpreter shutdown -- reproduced identically across many unrelated Python
tools via crash-dump analysis, so it is a generic CPython teardown-path bug
rather than anything specific to this tool. Since the crash only fires
*after* a tool's real work is already done, it can be dodged by skipping
CPython's own shutdown sequence: run any registered ``atexit`` callbacks
ourselves (so buffered writes -- e.g. log handlers -- still flush), flush the
streams we write directly, then call ``os._exit()`` instead of letting
``main()``'s return/``SystemExit`` reach ``Py_FinalizeEx``.

Scoped narrowly to the affected runtime -- Windows + CPython 3.13.x: the
crash has only been confirmed there (reproduced across two 3.13 patch
releases), and ``os._exit()`` is a strictly worse default for every other
interpreter/version (it forgoes whatever ordinary interpreter-shutdown
safety exists for a bug with no evidence of occurring there).
"""

from __future__ import annotations

import atexit
import os
import sys
from typing import Callable

_AFFECTED_RUNTIME = (
    sys.platform == "win32"
    and sys.implementation.name == "cpython"
    and sys.version_info[:2] == (3, 13)
)


def run_and_exit(main: Callable[[], int]) -> None:
    """Call ``main()``, then exit -- via ``os._exit()`` on the affected
    runtime, normally everywhere else -- instead of always letting normal
    interpreter shutdown run.
    """
    try:
        code = main()
    except SystemExit as exc:
        code = exc.code
    except KeyboardInterrupt:
        if not _AFFECTED_RUNTIME:
            raise
        # Affected runtime: Ctrl+C must not be allowed to propagate into
        # normal interpreter shutdown either -- that would still risk the
        # very crash this workaround exists to dodge. Use the conventional
        # 128+SIGINT exit code (130); no traceback -- that's what a normal
        # KeyboardInterrupt at the top level doesn't produce either.
        code = 130
    except Exception:
        if not _AFFECTED_RUNTIME:
            raise
        # Affected runtime: an arbitrary (non-SystemExit, non-Interrupt)
        # exception must not be allowed to propagate either -- letting it
        # reach the interpreter's own top-level handler still ends in
        # normal shutdown, i.e. the very crash this workaround exists to
        # dodge. Set the exit code FIRST, then best-effort print the
        # traceback ourselves (what the interpreter would otherwise do) --
        # if stderr is closed/broken and printing itself raises, that must
        # not prevent reaching the same hard-exit path below.
        code = 1
        try:
            import traceback

            traceback.print_exc()
        except Exception:
            pass

    if not _AFFECTED_RUNTIME:
        sys.exit(code)

    if not isinstance(code, int):
        if code is not None:
            # Match `sys.exit()`'s own behavior for a non-int, non-None
            # argument: print it to stderr (best-effort -- must not prevent
            # reaching the hard exit below) before mapping it to exit code 1.
            try:
                print(code, file=sys.stderr)
            except Exception:
                pass
        code = 0 if code is None else 1
    # Run registered atexit callbacks (e.g. log-handler flush/close) ourselves
    # -- they're ordinary Python calls that happen *before* Py_FinalizeEx ever
    # starts, so they're unaffected by the crash we're dodging -- then flush
    # the streams we write directly before bypassing normal shutdown. Nested
    # try/finally so that a raising callback or a failed flush can never skip
    # the hard exit -- it must always happen, or the process could fall
    # through to the very CPython teardown path this workaround exists to
    # avoid.
    try:
        atexit._run_exitfuncs()
    finally:
        try:
            sys.stdout.flush()
        finally:
            try:
                sys.stderr.flush()
            finally:
                os._exit(code)
