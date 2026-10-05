"""Tests for ``output.write_real_stdout``'s resilience to a broken stdout
handle, used by both ``_emit_plan`` (resolve's launch-plan handoff) and
``_json_output`` (the shared ``--json`` envelope writer).

Reproduces a live crash: these writers used to go straight to
``sys.__stdout__.write()`` + ``.flush()`` with no error handling. When the
hosting terminal/console has already torn down the real stdout handle
(observed on Windows as ``OSError: [Errno 22] Invalid argument`` from
``flush()``), that propagated as an unhandled traceback, crashing the whole
resume with no useful plan delivered and no actionable message.

The fix writes directly to the real OS fd (bypassing the buffered
TextIOWrapper and its separate flush step) so a fault can never leave an
indeterminate amount already delivered -- the first naive fix (try the
buffered write, catch OSError, replay through a raw fd) could duplicate
already-flushed bytes and was caught in review; these tests guard against
that regression too.
"""

from __future__ import annotations

import io
import json

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import output


@pytest.fixture(autouse=True)
def _no_project(monkeypatch):
    # Keep _emit_plan's payload minimal/hermetic regardless of host state.
    monkeypatch.setattr(m.cfg, "active_project", lambda: None)


def test_emit_plan_writes_to_real_stdout(capfd):
    plan = {"action": "noop"}
    m._emit_plan(plan)
    out, _err = capfd.readouterr()
    assert json.loads(out.strip()) == plan


def test_write_real_stdout_uses_swapped_stringio_for_capture(monkeypatch):
    """``capture_json_output`` swaps ``sys.__stdout__`` for a StringIO; that
    in-memory case must go through the Python-level stream, not fd 1, or the
    capture would always come back empty."""
    buf = io.StringIO()
    monkeypatch.setattr(output.sys, "__stdout__", buf)
    output.write_real_stdout('{"ok": true}\n')
    assert buf.getvalue() == '{"ok": true}\n'


def test_write_real_stdout_never_replays_on_a_broken_real_handle(monkeypatch, capfd):
    """A broken real console handle must report a single clear failure, not
    attempt a buffered write that could partially succeed and then be
    duplicated by a raw-fd replay."""
    def _boom(_fd, _data):
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(output.os, "write", _boom)
    with pytest.raises(SystemExit) as excinfo:
        output.write_real_stdout('{"action": "noop"}\n')
    assert excinfo.value.code == 1
    out, err = capfd.readouterr()
    assert out == ""  # nothing was ever written/duplicated
    assert "broken" in err.lower()
