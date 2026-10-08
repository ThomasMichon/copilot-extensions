"""run_background_capture's bounded mode (max_output): an untrusted command that
floods its pipes is stopped and reported, never buffered without limit."""
from __future__ import annotations

import sys
import time

import pytest

from agent_dispatch import procutil


def test_output_within_the_limit_is_returned_whole():
    done = procutil.run_background_capture(
        [sys.executable, "-c", "import sys; sys.stdout.write('a' * 5000); sys.stderr.write('b' * 10)"],
        timeout=20, max_output=10_000)
    assert done is not None and (len(done.stdout), done.stderr, done.returncode) == (5000, "b" * 10, 0)


def test_a_flood_is_stopped_and_raised_within_its_timeout():
    started = time.monotonic()
    with pytest.raises(procutil.OutputLimitExceeded):
        procutil.run_background_capture(
            [sys.executable, "-c", "import sys\nwhile True: sys.stderr.write('x' * 65536)"],
            timeout=20, max_output=100_000)
    assert time.monotonic() - started < 15


def test_a_bounded_capture_that_times_out_returns_none():
    assert procutil.run_background_capture(
        [sys.executable, "-c", "import time; time.sleep(30)"], timeout=1, max_output=1000) is None
