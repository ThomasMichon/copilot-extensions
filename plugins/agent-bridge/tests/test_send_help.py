"""Regression: `agent-bridge send --help` must not advertise the removed
``--new`` flag (#468).

The flag is retained (hidden) so the removal handler can emit a friendly
redirect when someone still passes it, but it must not appear in the help
text for the ``send`` subcommand.
"""

import pytest

from agent_bridge.__main__ import build_parser


def _send_subparser_help() -> str:
    parser = build_parser()
    for action in parser._actions:
        subparsers = getattr(action, "choices", None)
        if isinstance(subparsers, dict) and "send" in subparsers:
            return subparsers["send"].format_help()
    pytest.fail("could not locate the `send` subparser")


def test_send_help_does_not_advertise_new_flag():
    help_text = _send_subparser_help()
    assert "--new" not in help_text


def test_send_still_accepts_hidden_new_flag():
    # Retained so the removal handler can catch it and redirect the user.
    parser = build_parser()
    args = parser.parse_args(["send", "target", "hello", "--new"])
    assert getattr(args, "new", False) is True
