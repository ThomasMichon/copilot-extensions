"""Staged POSIX entries retain their native owners and explicit SDK evidence."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from test_command_evidence import _recent, _run
from test_command_evidence import payload as command_payload
from test_command_reports import _script

payload = command_payload
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Native Linux staged-report contract")


def test_unactivated_archive_preserves_native_fork_signal_and_thread_behavior(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    direct, wrapped = _script(
        payload, tmp_path,
        "import json,logging,os,signal,threading,time\n"
        "events=[]\n"
        "def launch():\n"
        "    child=os.fork()\n"
        "    if child==0: os._exit(0)\n"
        "    assert os.waitpid(child,0)[1]==0\n"
        "    events.append('fork')\n"
        "def interrupt(number,frame): launch()\n"
        "signal.signal(signal.SIGALRM,interrupt)\n"
        "signal.setitimer(signal.ITIMER_REAL,.03)\n"
        "try:\n"
        "    deadline=time.monotonic()+2\n"
        "    while not events and time.monotonic()<deadline: time.sleep(.005)\n"
        "    assert events==['fork']\n"
        "finally: signal.setitimer(signal.ITIMER_REAL,0)\n"
        "signal.signal(signal.SIGINT,signal.default_int_handler)\n"
        "try: signal.raise_signal(signal.SIGINT)\n"
        "except KeyboardInterrupt: events.append('caught')\n"
        "launch()\n"
        "logging.error('private-unactivated-error')\n"
        "print(json.dumps([threading.active_count(),os.fork.__module__,events]))\n",
    )
    assert direct.returncode == wrapped.returncode == 0
    assert json.loads(wrapped.stdout) == [1, "posix", ["fork", "caught", "fork"]]
    evidence = _recent(payload)
    assert evidence["automatic_reports"] == {"supported": False, "scope": "reader-platform"}
    assert evidence["records"] == [] and evidence["exists"] is False


def test_unactivated_automatic_reports_do_not_disable_the_explicit_sdk(
    payload: tuple[Path, dict[str, str]],
) -> None:
    result = _run(payload, "run", "fixture-command", "fixture-source", "fixture", "--", "reported")
    assert result.returncode == 0 and result.stdout == result.stderr == b""
    evidence = _recent(payload)
    assert evidence["automatic_reports"] == {"supported": False, "scope": "reader-platform"}
    record, = evidence["records"]
    assert record["code"] == "selection-failed" and record["exit_code"] == 0
