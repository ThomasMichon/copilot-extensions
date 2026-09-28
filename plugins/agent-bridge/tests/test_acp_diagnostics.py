"""Unit coverage for :mod:`agent_bridge.acp_diagnostics`.

Regression context: GitHub Copilot CLI 1.0.89-1 wrote exactly one
pipe-buffer's worth (64 KiB) of a ``session/new`` ACP response and exited(0)
mid-message. The third-party ``acp`` library's own ``NdjsonTransport.receive()``
logged the resulting JSON parse failure (a bare
``logging.exception("Error parsing JSON-RPC message")`` on the root logger)
but then just swallowed it and kept looping -- the connection subsequently
died with a generic ``ConnectionError("Connection closed")``, and the two
facts were only ever linked by a human manually correlating log timestamps.
These tests exercise the capture/describe helpers that fold the acp library's
own diagnostic straight into the raised error instead.

This module is imported directly (not via the package's ``conftest.py``,
which has a pre-existing, unrelated environment issue resolving
``agent_procutil`` in a fresh worktree) so it stays runnable standalone.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "agent_bridge" / "acp_diagnostics.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("agent_bridge.acp_diagnostics", _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    # Dataclass field resolution needs the module registered before exec.
    sys.modules.setdefault("agent_bridge.acp_diagnostics", module)
    spec.loader.exec_module(module)
    return module


acp_diagnostics = _load_module()


def _simulate_acp_transport_parse_failure(message: str = "boom") -> None:
    """Mimic exactly what ``acp``'s ``NdjsonTransport.receive()`` does on a
    JSON parse failure -- a bare module-level ``logging.exception`` call with
    this precise message, landing on the root logger."""
    try:
        raise ValueError(message)
    except Exception:
        logging.exception(acp_diagnostics.ACP_PARSE_ERROR_MESSAGE)


def test_capture_records_a_matching_parse_failure() -> None:
    with acp_diagnostics.capture_acp_parse_errors() as records:
        _simulate_acp_transport_parse_failure(
            "Unterminated string starting at: line 1 column 65412"
        )
    assert len(records) == 1
    assert records[0].exc_type == "ValueError"
    assert "Unterminated string" in records[0].exc_message


def test_capture_ignores_unrelated_log_records() -> None:
    with acp_diagnostics.capture_acp_parse_errors() as records:
        logging.getLogger("something.else").error("an unrelated error")
        logging.getLogger().warning(acp_diagnostics.ACP_PARSE_ERROR_MESSAGE)  # wrong level
    assert records == []


def test_capture_handler_does_not_leak_onto_root_logger() -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    with acp_diagnostics.capture_acp_parse_errors():
        assert len(root.handlers) == len(before) + 1
    assert root.handlers == before


def test_capture_removes_handler_even_on_exception() -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    with pytest.raises(RuntimeError):
        with acp_diagnostics.capture_acp_parse_errors():
            raise RuntimeError("attempt failed for an unrelated reason")
    assert root.handlers == before


def test_describe_truncated_child_empty_when_nothing_captured() -> None:
    assert acp_diagnostics.describe_truncated_child([]) == ""


def test_describe_truncated_child_singular_and_reports_the_detail() -> None:
    with acp_diagnostics.capture_acp_parse_errors() as records:
        _simulate_acp_transport_parse_failure(
            "Unterminated string starting at: line 1 column 65412"
        )
    note = acp_diagnostics.describe_truncated_child(records)
    assert "1 malformed/truncated JSON-RPC message " in note  # singular, no trailing 's'
    assert "ValueError" in note
    assert "not a real transport drop" in note


def test_describe_truncated_child_pluralizes_multiple_records() -> None:
    with acp_diagnostics.capture_acp_parse_errors() as records:
        _simulate_acp_transport_parse_failure("first")
        _simulate_acp_transport_parse_failure("second")
    note = acp_diagnostics.describe_truncated_child(records)
    assert "2 malformed/truncated JSON-RPC messages" in note
    # Describes the LAST captured record's detail, not the first.
    assert "second" in note
    assert "first" not in note
