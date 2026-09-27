from __future__ import annotations

import pytest

from agent_dispatch import worker_charter
from agent_dispatch.__main__ import build_parser


def _args(argv):
    return build_parser().parse_args(argv)


def test_available_charters_lists_autopilot_and_operating_procedures():
    assert worker_charter.available_charters() == ["autopilot", "operating-procedures"]


def test_charter_text_returns_the_full_policy_prose():
    text = worker_charter.charter_text(worker_charter.AUTOPILOT_CHARTER_NAME)
    assert "contract-net" in text.lower()
    assert "DUPLICATE check" in text
    assert "done-criteria" in text.lower() or "done_criteria" in text
    assert "progress" in text.lower()


def test_operating_procedures_charter_covers_the_universal_contract():
    text = worker_charter.charter_text(worker_charter.OPERATING_PROCEDURES_CHARTER_NAME)
    assert "never prose" in text.lower() or "never turn-ending prose" in text.lower()
    assert "terminal, steered, or waited" in text.lower()
    assert "fail fast" in text.lower() or "fail loud" in text.lower()
    assert "declared, not improvised" in text.lower() or "not improvised" in text.lower()


def test_operating_procedures_text_export_matches_charter_lookup():
    assert worker_charter.OPERATING_PROCEDURES_TEXT == worker_charter.charter_text(
        worker_charter.OPERATING_PROCEDURES_CHARTER_NAME
    )


def test_charter_text_unknown_name_raises_keyerror():
    with pytest.raises(KeyError):
        worker_charter.charter_text("nope")


def test_cli_charter_show_prints_full_text(capsys):
    args = _args(["charter", "show", "autopilot"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "contract-net" in out.lower()


def test_cli_charter_show_prints_operating_procedures(capsys):
    args = _args(["charter", "show", "operating-procedures"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "terminal, steered, or waited" in out.lower()


def test_cli_charter_show_unknown_name_errors(capsys):
    args = _args(["charter", "show", "nope"])
    assert args.func(args) == 2
    err = capsys.readouterr().err
    assert "no worker charter named" in err
