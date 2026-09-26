# VENDOR_POINTER: source=libs/work-coalescing-singleton kind=src-passthrough
"""Vendor-pointer passthrough stub for the ``work-coalescing-singleton`` shared lib
(agent-cli-lazy-dispatch Phase 2's dev-branch vendoring mechanism -- see
tools/sync-vendored-libs.py's own module docstring for the full
"src-passthrough" pointer design).

Every import of this package resolves through ordinary Python import
machinery straight to the canonical ``libs/work-coalescing-singleton/src/work_coalescing_singleton`` tree -- do NOT
hand-edit this file; regenerate it via
``python tools/sync-vendored-libs.py --pointerize <plugin> work-coalescing-singleton``.
A production (main-branch) release never ships this stub:
``tools/materialize_main.py`` expands it into a real, byte-identical copy
at promotion time.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path


def _find_repo_root(start):
    """Walk upward from ``start`` for the monorepo root -- the first
    ancestor carrying both a ``libs/`` and a ``plugins/`` directory. This
    stub only ever runs inside a full dev-branch checkout of that monorepo
    (a real release materializes it into a real copy first), so this
    signature is a safe, stable way to locate it without depending on this
    file's own exact nesting depth under ``plugins/<plugin>/libs/<lib>/``."""
    for candidate in (start, *start.parents):
        if (candidate / "libs").is_dir() and (candidate / "plugins").is_dir():
            return candidate
    return None


_here = Path(__file__).resolve()
_repo_root = _find_repo_root(_here)
if _repo_root is None:
    raise ImportError(
        __name__ + ": vendor-pointer passthrough stub could not locate the "
        "monorepo root (an ancestor with both libs/ and plugins/) -- this "
        "stub only works inside a full dev-branch checkout; a real release "
        "must materialize it into a real copy first (tools/materialize_main.py)."
    )

_canonical_pkg_dir = _repo_root / "libs" / "work-coalescing-singleton" / "src" / "work_coalescing_singleton"
_canonical_init = _canonical_pkg_dir / "__init__.py"
if not _canonical_init.is_file():
    raise ImportError(
        __name__ + ": canonical source not found at " + str(_canonical_init)
    )

# Guard against a stale __pycache__ hit (copilot-extensions#3802): CPython's
# default SourceFileLoader validates a cached .pyc by source mtime + size,
# which a filesystem with coarse mtime resolution can satisfy even when the
# source content genuinely changed between two edits -- silently serving
# old bytecode for canonical's __init__.py AND every nested submodule this
# stub's own submodule_search_locations exposes (e.g. client.py/server.py),
# defeating this whole mechanism's "editing canonical takes effect
# immediately" guarantee. Clearing canonical's own __pycache__ here forces
# a fresh compile on every process that imports this stub -- this stub
# only ever runs in a full dev-branch checkout (never shipped), so the
# small recompute cost is a non-issue.
_pycache = _canonical_pkg_dir / "__pycache__"
if _pycache.is_dir():
    shutil.rmtree(_pycache, ignore_errors=True)

# Standard "self-replacing module" technique: CPython's import machinery
# re-fetches ``sys.modules[name]`` AFTER this file's own exec finishes (see
# ``importlib._bootstrap._load_unlocked``), so swapping the entry here mid-
# init correctly hands the REAL, canonical module back to whatever
# triggered this import (``import work_coalescing_singleton`` and ``from work_coalescing_singleton import x`` both
# resolve to it) -- this stub's own module object is discarded.
_spec = importlib.util.spec_from_file_location(
    __name__, _canonical_init, submodule_search_locations=[str(_canonical_pkg_dir)]
)
_module = importlib.util.module_from_spec(_spec)
sys.modules[__name__] = _module
_spec.loader.exec_module(_module)
