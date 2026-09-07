from __future__ import annotations

import pytest

from agent_dispatch import worker_charter
from agent_dispatch.__main__ import build_parser


def _args(argv):
    return build_parser().parse_args(argv)


def test_available_charters_lists_autopilot():
    assert worker_charter.available_charters() == ["autopilot"]


def test_charter_text_returns_the_full_policy_prose():
    text = worker_charter.charter_text(worker_charter.AUTOPILOT_CHARTER_NAME)
    assert "contract-net" in text.lower()
    assert "DUPLICATE check" in text
    assert "done-criteria" in text.lower() or "done_criteria" in text
    assert "progress" in text.lower()


def test_charter_text_unknown_name_raises_keyerror():
    with pytest.raises(KeyError):
        worker_charter.charter_text("nope")


def test_cli_charter_show_prints_full_text(capsys):
    args = _args(["charter", "show", "autopilot"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "contract-net" in out.lower()


def test_cli_charter_show_unknown_name_errors(capsys):
    args = _args(["charter", "show", "nope"])
    assert args.func(args) == 2
    err = capsys.readouterr().err
    assert "no worker charter named" in err
