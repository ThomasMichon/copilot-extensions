from __future__ import annotations

import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from runtime_profile import ProfileError
from runtime_source_capture import capture


def transaction(code: str, tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(argv=[sys.executable, "-I", "-S", "-c", code],
                           environment=dict(os.environ), cwd=tmp_path)


def test_capture_inclusive_dual_stream_limits(tmp_path: Path) -> None:
    result = capture(transaction("import os;os.write(1,b'x'*100);os.write(2,b'y'*100)", tmp_path), tmp_path,
                     max_stdout=100, max_stderr=100, deadline=time.monotonic() + 10)
    assert result == (b"x" * 100, b"y" * 100)


@pytest.mark.parametrize("stream", [1, 2])
def test_capture_stops_overflow_and_settles_delayed_descendants(tmp_path: Path, stream: int) -> None:
    destination = tmp_path / "late.txt"
    child = "import time,pathlib;time.sleep(2);pathlib.Path(" + repr(str(destination)) + ").write_text('late')"
    code = (
        "import os,subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-I','-S','-c'," + repr(child) + "],"
        "creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0);"
        f"os.write({stream},b'x'*101);time.sleep(30)"
    )
    started = time.monotonic()
    with pytest.raises(ProfileError, match="capture budget"):
        capture(transaction(code, tmp_path), tmp_path,
                max_stdout=100, max_stderr=100, deadline=time.monotonic() + 10)
    assert time.monotonic() - started < 8
    time.sleep(2.2)
    assert not destination.exists()


def test_capture_deadline_settles_descendants(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="capture"):
        capture(transaction("import time;time.sleep(30)", tmp_path), tmp_path,
                max_stdout=100, max_stderr=100, deadline=time.monotonic() + 1)
