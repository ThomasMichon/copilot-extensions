"""A settled native executor failure must not require restarting the host."""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import contextmanager

import agent_procutil
import pytest
from update_driver import RequestSpec, TrustedRunner, enqueue, kernel_boot_identity, status
from test_process import collect, eventually, worker


pytestmark = pytest.mark.skipif(
    os.name != "nt" and not sys.platform.startswith("linux"),
    reason="This independent process-exit witness covers native Windows and Linux",
)


@contextmanager
def _product_exit(pid):
    if os.name == "nt":
        from agent_procutil.daemon_broker import WindowsDaemonProcess

        observer = WindowsDaemonProcess(pid, ["fixture-timeout-product"])
        try:
            yield lambda: observer.wait(timeout=5) is not None
        finally:
            observer.close()
    else:
        import select
        from agent_procutil._linux_pidfd import open_pidfd

        descriptor = open_pidfd(pid)
        try:
            yield lambda: bool(select.select([descriptor], [], [], 5)[0])
        finally:
            os.close(descriptor)


def _stop_fixture(process):
    if process.poll() is None:
        process.terminate()
    process.communicate(timeout=5)


def test_native_timeout_allows_a_later_install_on_the_same_boot(area):
    root = area / "state"
    boot_before = kernel_boot_identity()
    marker = area / "timed-out-product-started"
    later_marker = area / "later-product-completed"
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=area, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **agent_procutil.no_window_kwargs(),
    )
    first_worker = None
    later_worker = None
    try:
        first = enqueue(
            RequestSpec("fixture", argv=("native-timeout",)),
            runner=TrustedRunner(
                (
                    sys.executable, "-c",
                    "from pathlib import Path; import os,sys,time; "
                    "marker=Path(sys.argv[1]); temporary=marker.with_suffix('.pending'); "
                    "temporary.write_text(str(os.getpid())); temporary.replace(marker); time.sleep(60)",
                    str(marker),
                ),
                str(area), timeout=5,
            ),
            root=root,
        )
        first_worker = worker(area)
        eventually(marker.is_file)
        with _product_exit(int(marker.read_text())) as exited:
            first_worker.communicate(timeout=15)
            assert exited(), "the exact timed-out fixture product did not exit"
        failed = status(first.id, root=root)
        assert failed.state != "running"
        assert failed.exit_code != 0
        assert failed.error
        assert unrelated.poll() is None
        assert kernel_boot_identity() == boot_before

        later = enqueue(
            RequestSpec("fixture", argv=("after-settled-failure",)),
            runner=TrustedRunner(
                (
                    sys.executable, "-c",
                    "from pathlib import Path; import sys; "
                    "Path(sys.argv[1]).write_text('completed')",
                    str(later_marker),
                ),
                str(area),
            ),
            root=root,
        )
        later_worker = worker(area)
        collect(later_worker)
        assert status(later.id, root=root).exit_code == 0
        assert later_marker.read_text() == "completed"
        assert status(first.id, root=root).exit_code != 0
        assert kernel_boot_identity() == boot_before
        assert unrelated.poll() is None
    finally:
        for process in (first_worker, later_worker, unrelated):
            if process is not None:
                _stop_fixture(process)
