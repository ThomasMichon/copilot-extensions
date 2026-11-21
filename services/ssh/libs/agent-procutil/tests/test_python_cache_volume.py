"""Native filesystem backlog fixture; process/runtime proofs remain scoped controls."""
import importlib.util
import marshal
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable, cast

import pytest

from agent_procutil import python_cache_legacy as legacy
from agent_procutil import python_cache_prefixes as prefixes
from python_cache_pyc_fixtures import native_format
from test_python_cache_prefixes import area as area, collect, namespace


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows backlog fixture")
def test_owned_backlog_drains_across_real_entry_budgets(
    area: Path, record_testsuite_property: Callable[[str, object], None],
) -> None:
    if not cast(Any, native_format)():
        pytest.skip("native bytecode format is outside the qualified set")
    targets = [namespace(area, number=number) for number in range(1, 9)]
    old = time.time() - 10 * 86400
    for target in targets:
        mirror = target.joinpath(*legacy._mirror_parts(area), "versions", "1.0")
        for number in range(32):
            source = area / "versions" / "1.0" / f"part{number}.py"
            code = compile("payload = " + repr("x" * 32768), str(source), "exec")
            value = importlib.util.MAGIC_NUMBER + b"\0" * 12 + marshal.dumps(code)
            path = mirror / f"part{number}.{sys.implementation.cache_tag}.pyc"
            path.write_bytes(value)
            os.utime(path, (old, old))
    expected = sum(path.stat().st_size for target in targets for path in target.rglob("*.pyc"))
    removed, passes, cursor = 0, 0, ""
    started = time.monotonic()
    for _ in range(20):
        result = collect(
            area, apply=True, after=cursor,
            budget=prefixes.Budget(namespaces=2, entries=300, seconds=5),
        )
        removed += result.bytes
        passes += 1
        cursor = result.next_cursor
        if not any(target.exists() for target in targets):
            break
    elapsed = time.monotonic() - started
    assert passes > 1 and removed == expected
    assert not any(target.exists() for target in targets)
    record_testsuite_property("fixture_logical_bytes_removed", removed)
    record_testsuite_property("fixture_passes", passes)
    record_testsuite_property("fixture_elapsed_seconds", elapsed)
    record_testsuite_property("fixture_logical_bytes_per_second", removed / elapsed)
