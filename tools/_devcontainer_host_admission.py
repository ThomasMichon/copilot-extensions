"""Host-wide admission-lease coordination for ``run_tests_in_devcontainer.py``.

Closes the third Phase 2 residual gap: ``tools/run-plugin-tests.py``'s own
``--admission-wait`` lease lives under ``$HOME``/``$XDG_CACHE_HOME``, which
is a fresh per-container tmpfs for every wrapped invocation -- so two
wrapped runs (or a wrapped run and a bare ``run-plugin-tests.py``
invocation) never actually contend for the same host-wide heavy-test
slot the way two bare invocations would. This module acquires that SAME
lease on the HOST, before the container does any real work, and holds it
for the run's entire lifetime -- so the two invocation styles correctly
serialize against each other. The lock dir/service name come from
``tools/_admission_protocol.py``, a shared module both this file and
``run-plugin-tests.py`` import, so neither duplicates that contract by
hand.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LEASE_LIB = REPO / "libs" / "single-instance-lease" / "src"
sys.path.insert(0, str(LEASE_LIB))
from single_instance_lease import AlreadyRunningError, SingleInstance  # noqa: E402

from _admission_protocol import ADMISSION_SERVICE as _ADMISSION_SERVICE  # noqa: E402
from _admission_protocol import admission_dir  # noqa: E402

# A real run takes the host-wide lease in `run-plugin-tests.py` itself;
# `--guards`, `--collect-only`, and `--prepare-only` all skip it
# (`needs_admission = not guards and not collect_only and not
# prepare_only`); `--list` returns even earlier, before that script ever
# reaches its own admission check.
_SKIPS_ADMISSION = frozenset({"--list", "--guards", "--collect-only", "--prepare-only"})


def needs_admission(passthrough: list[str], canonicalize) -> bool:
    """Mirrors `run-plugin-tests.py`'s own admission-skip logic (see
    `_SKIPS_ADMISSION`) -- never gate a run that script wouldn't gate
    either."""
    return not any(
        canonicalize(arg.partition("=")[0]) in _SKIPS_ADMISSION
        for arg in passthrough
    )


def resolve_admission_wait(passthrough: list[str], canonicalize) -> float:
    """Last-occurrence-wins `--admission-wait` value (matching argparse
    semantics) -- default 0.0 (fail fast), the same default
    `run-plugin-tests.py` itself uses. Rejects a malformed value
    immediately (`SystemExit`), same as that script's own argparse would
    -- silently falling back to the default here would let a bad value
    reach `acquire()` (and so start real container work, or get masked
    by an unrelated `[BUSY]` error) before the inner runner ever gets a
    chance to reject it itself."""
    value = 0.0
    for i, arg in enumerate(passthrough):
        name, eq, value_str = arg.partition("=")
        if canonicalize(name) != "--admission-wait":
            continue
        if not eq:
            value_str = passthrough[i + 1] if i + 1 < len(passthrough) else ""
        try:
            value = float(value_str)
        except ValueError:
            raise SystemExit(f"--admission-wait: invalid float value: {value_str!r}") from None
    return value


def acquire(wait_seconds: float) -> SingleInstance:
    """Acquire the host-wide test-runner lease, waiting up to
    `wait_seconds` (0 = fail fast, matching `run-plugin-tests.py`'s own
    semantics). Raises `SystemExit` -- not `ValueError`/`AlreadyRunningError`
    -- for every caller-facing failure, so the caller needs no extra
    except clause and never sees a raw traceback for a bad CLI value."""
    if wait_seconds < 0:
        raise SystemExit(f"--admission-wait must be non-negative, got {wait_seconds:g}")
    lease = SingleInstance(admission_dir(), service=_ADMISSION_SERVICE)
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            lease.acquire()
            return lease
        except AlreadyRunningError as exc:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(0.25, remaining))
                continue
            raise SystemExit(
                f"[BUSY] Another heavy plugin test run is active on the "
                f"host: {exc}. Use --admission-wait SECONDS to wait for it."
            ) from exc
