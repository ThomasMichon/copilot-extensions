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


@pytest.mark.parametrize("child_count", [1, 4])
def test_native_owned_child_cpu_increases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, child_count: int,
) -> None:
    psutil = pytest.importorskip("psutil", reason="native CPU observation needs the metrics extra")
    topology_calls = []
    native_calls = []
    per_sample_native_calls = []
    original_topology = psutil._ppid_map
    if sys.platform == "win32":
        # All three Python aliases reach this same C enumeration entrypoint.
        # c_call profiling observes it regardless of which alias PPID uses.
        native_parent_table = psutil._psplatform.cext.ppid_map
        assert original_topology is native_parent_table
        assert psutil._psplatform.ppid_map is native_parent_table
        original_sample = OwnedProcessCpuSampler._sample_sync

        def profile(frame, event, arg):
            if event == "c_call" and arg is native_parent_table:
                native_calls.append(1)

        def counted_sample(self):
            before = len(native_calls)
            previous_profile = sys.getprofile()
            sys.setprofile(profile)
            try:
                return original_sample(self)
            finally:
                sys.setprofile(previous_profile)
                per_sample_native_calls.append(len(native_calls) - before)

        monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", counted_sample)

    def topology():
        topology_calls.append(1)
        return original_topology()

    def no_per_record_enumeration(*args, **kwargs):
        pytest.fail("native sampling must share one parent snapshot")

    monkeypatch.setattr(psutil, "_ppid_map", topology)
    monkeypatch.setattr(psutil.Process, "children", no_per_record_enumeration)
    release = tmp_path / "release"
    ready = tmp_path / "ready"
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
        "children=[subprocess.Popen([sys.executable,'-c',sys.argv[2],str(release)],"
        "**no_window_kwargs()) for _ in range(int(sys.argv[4]))];"
        "pathlib.Path(sys.argv[5]).write_text('ready');"
        "\nwhile not release.exists(): time.sleep(0.01)"
        "\nfor child in children: child.wait(timeout=10)"
    )
    # Windows venv redirectors would add launcher processes to the fixture tree.
    fixture_python = getattr(sys, "_base_executable", sys.executable)
    process = subprocess.Popen(  # noqa: S603 - interpreter and scripts are fixture-owned
        [fixture_python, "-c", root_code, str(release), child_code, str(SOURCE),
         str(child_count), str(ready)],
        **no_window_kwargs(),
    )
    try:
        deadline = time.monotonic() + 8
        while not ready.exists():
            assert process.poll() is None
            assert time.monotonic() < deadline, "owned fixture did not become ready"
            time.sleep(0.01)
        identity = ProcessIdentity(process.pid, psutil.Process(process.pid).create_time())
        thread_reads: list[tuple[int, float]] = []

        def thread_clock() -> float:
            value = time.thread_time()
            thread_reads.append((threading.get_ident(), value))
            return value

        sampler = OwnedProcessCpuSampler(identity, thread_clock=thread_clock)

        async def run() -> None:
            first = await sampler.sample()
            # Include real interpreter/console descendants without mocking them.
            assert len(first.identities) >= child_count + 1
            assert first.observed_cpu_seconds is not None
            await asyncio.sleep(1)
            second = await sampler.sample()
            assert second.observed_cpu_seconds is not None
            assert second.identities == first.identities
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
            if sys.platform == "win32":
                before_cached = len(native_calls)
                previous_profile = sys.getprofile()
                sys.setprofile(profile)
            try:
                assert await sampler.sample() is second
            finally:
                if sys.platform == "win32":
                    sys.setprofile(previous_profile)
                    assert len(native_calls) == before_cached
            assert len(topology_calls) == second.sample.sequence == 2
            if sys.platform == "win32":
                # Cold discovery validates every newly retained non-root PID.
                assert per_sample_native_calls == [len(first.identities), 1]
                assert len(native_calls) == len(first.identities) + 1
                print(
                    f"native parent tables: retained={len(second.identities)} "
                    f"cold={per_sample_native_calls[0]} "
                    f"warm={per_sample_native_calls[1]} cached=0",
                )

        asyncio.run(asyncio.wait_for(run(), timeout=8))
    finally:
        release.write_text("finish")
        process.wait(timeout=12)
    assert process.returncode == 0
