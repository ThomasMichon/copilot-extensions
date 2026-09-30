"""Regression test for ``live_turn_probe.py``'s identity-precheck stop
wrapper (``_stop_daemon_identity_safe``, agent-bridge-unified-zdd-cutover
Phase 6).

``agent-bridge service stop`` is NOT identity-safe by itself (it kills the
union of pid-file/port-holder/lock-holder pids before checking identity,
only verifying afterward that no bridge process remains). The wrapper
gathers the same three candidate sources and refuses to call ``service
stop`` at all if any live candidate doesn't identify as an agent-bridge
process. This test exercises that decision logic directly -- with a fake
``python`` binary standing in for the real interpreter subprocess calls --
so it never depends on a real agent-bridge install; it does not, and
cannot from outside, close the separate PID-reuse window inside
``service stop``'s own kill call (see the wrapper's own docstring).
"""
from __future__ import annotations

import importlib.util
import stat
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scenarios" / "agent-bridge-cutover" / "fixtures" / "live_turn_probe.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("live_turn_probe", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def probe():
    return _load_module()


def _fake_python(tmp_path: Path, candidates_line: str, service_stop_marker: Path) -> str:
    """A stand-in ``python`` whose ``-m agent_bridge ...`` and ``-c ...``
    invocations are scripted: the identity-precheck snippet always prints
    the same candidates/unverified line, and a real ``service stop`` call
    (if ever reached) writes a marker file so the test can assert it was
    NOT invoked on the reject path.
    """
    bin_path = tmp_path / "fake-python"
    bin_path.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = -c ]; then\n"
        f"  echo \"{candidates_line}\"\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = -m ] && [ \"$2\" = agent_bridge ] && [ \"$3\" = service ] && [ \"$4\" = stop ]; then\n"
        f"  touch {service_stop_marker.as_posix()}\n"
        "  echo '[OK] agent-bridge stopped'\n"
        "  exit 0\n"
        "fi\n"
        "echo 'unexpected invocation' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    bin_path.chmod(bin_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(bin_path)


def test_accepts_a_recognized_live_daemon_and_calls_service_stop(tmp_path, probe):
    marker = tmp_path / "service-stop-called"
    fake_python = _fake_python(tmp_path, "[4242]|[]", marker)

    ok, detail = probe._stop_daemon_identity_safe(fake_python)

    assert ok is True, detail
    assert marker.exists(), "service stop should have been invoked on the accept path"


def test_rejects_an_unrelated_live_pid_without_calling_service_stop(tmp_path, probe):
    marker = tmp_path / "service-stop-called"
    # candidates include a live pid (e.g. a stale pid-file entry) that does
    # NOT identify as agent-bridge -- the precheck must refuse outright.
    fake_python = _fake_python(tmp_path, "[4242, 9999]|[9999]", marker)

    ok, detail = probe._stop_daemon_identity_safe(fake_python)

    assert ok is False
    assert "9999" in detail
    assert not marker.exists(), "service stop must NEVER be invoked once any candidate fails identity"


def test_no_candidates_is_treated_as_safe_to_proceed(tmp_path, probe):
    marker = tmp_path / "service-stop-called"
    fake_python = _fake_python(tmp_path, "[]|[]", marker)

    ok, detail = probe._stop_daemon_identity_safe(fake_python)

    assert ok is True, detail
    assert marker.exists()
