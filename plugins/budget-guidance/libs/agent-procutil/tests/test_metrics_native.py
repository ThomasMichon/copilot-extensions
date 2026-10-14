from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from agent_procutil import no_window_kwargs
from agent_procutil.metrics import OwnedProcessCpuSampler, ProcessIdentity

SOURCE = Path(__file__).resolve().parents[1] / "src"


def test_default_and_metrics_imports_do_not_load_optional_backend() -> None:
    result = subprocess.run(  # noqa: S603 - interpreter and source path are fixture-owned
        [sys.executable, "-c",
         "import sys; sys.path.insert(0,sys.argv[1]); "
         "import agent_procutil,agent_procutil.metrics; "
         "print('psutil' in sys.modules)", str(SOURCE)],
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_native_owned_child_cpu_increases(tmp_path: Path) -> None:
    psutil = pytest.importorskip("psutil", reason="native CPU observation needs the metrics extra")
    release = tmp_path / "release"
    child_code = (
        "import pathlib,sys,time;"
        "release=pathlib.Path(sys.argv[1]);"
        "\nwhile not release.exists():"
        "\n end=time.monotonic()+0.04"
        "\n while time.monotonic()<end: pass"
        "\n time.sleep(0.02)"
    )
    root_code = (
        "import pathlib,subprocess,sys,time;"
        "sys.path.insert(0,sys.argv[3]);"
        "from agent_procutil import no_window_kwargs;"
        "release=pathlib.Path(sys.argv[1]);"
        "child=subprocess.Popen([sys.executable,'-c',sys.argv[2],str(release)],"
        "**no_window_kwargs());"
        "\nwhile not release.exists(): time.sleep(0.01)"
        "\nchild.wait(timeout=10)"
    )
    process = subprocess.Popen(  # noqa: S603 - interpreter and scripts are fixture-owned
        [sys.executable, "-c", root_code, str(release), child_code, str(SOURCE)],
        **no_window_kwargs(),
    )
    try:
        identity = ProcessIdentity(process.pid, psutil.Process(process.pid).create_time())
        thread_reads: list[tuple[int, float]] = []

        def thread_clock() -> float:
            value = time.thread_time()
            thread_reads.append((threading.get_ident(), value))
            return value

        sampler = OwnedProcessCpuSampler(identity, thread_clock=thread_clock)

        async def run() -> None:
            first = await sampler.sample()
            while len(first.identities) < 2:
                await asyncio.sleep(1)
                first = await sampler.sample()
            assert first.observed_cpu_seconds is not None
            await asyncio.sleep(1)
            second = await sampler.sample()
            assert second.observed_cpu_seconds is not None
            assert second.observed_cpu_seconds > first.observed_cpu_seconds
            assert second.coverage == "observed_lower_bound"
            assert not second.truncated
            assert not second.issues
            for observation in (first, second):
                sample = observation.sample
                assert sample is not None and sample.state == "succeeded"
                assert sample.started_at is not None and sample.completed_at is not None
                assert sample.wall_seconds is not None and sample.wall_seconds >= 0
                assert sample.thread_cpu_seconds is not None and sample.thread_cpu_seconds >= 0
                start, end = thread_reads[2 * (sample.sequence - 1):2 * sample.sequence]
                assert start[0] == end[0] != threading.get_ident()
                assert sample.thread_cpu_seconds == end[1] - start[1]
                assert not sample.issues
                assert sampler.current_sample is second.sample
            assert first.sample.instance == second.sample.instance
            assert first.sample.sequence < second.sample.sequence
            assert await sampler.sample() is second

        asyncio.run(asyncio.wait_for(run(), timeout=8))
    finally:
        release.write_text("finish")
        process.wait(timeout=12)
    assert process.returncode == 0
