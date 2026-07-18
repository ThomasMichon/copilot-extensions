"""Tests for ``--prompt-file`` / stdin prompt resolution on ``send``/``create``.

Multi-line dispatch prompts must not have to transit the shell's argv, where
embedded quotes/newlines get mangled (notably PowerShell word-splitting a prompt
at the first embedded double-quote -- see #250). ``--prompt-file <path>`` (and
``--prompt-file -`` for stdin) sidesteps argv entirely.
"""

from __future__ import annotations

from io import StringIO

import pytest

import agent_bridge.__main__ as m


def _parse(argv):
    return m.build_parser().parse_args(argv)


# --- parser accepts the new shapes ------------------------------------------

def test_send_accepts_prompt_file_without_positional():
    args = _parse(["send", "agent-x", "--prompt-file", "p.txt"])
    assert args.target == "agent-x"
    assert args.prompt is None
    assert args.prompt_file == "p.txt"


def test_send_still_accepts_positional_prompt():
    args = _parse(["send", "agent-x", "hello there"])
    assert args.prompt == "hello there"
    assert args.prompt_file is None


def test_create_accepts_prompt_file():
    args = _parse(["create", "agent-x", "--prompt-file", "-"])
    assert args.target == "agent-x"
    assert args.prompt is None
    assert args.prompt_file == "-"


def test_create_allows_no_prompt_at_all():
    args = _parse(["create", "agent-x"])
    assert args.prompt is None
    assert args.prompt_file is None


# --- _resolve_prompt behavior -----------------------------------------------

def test_resolve_reads_file(tmp_path):
    f = tmp_path / "prompt.md"
    body = 'Line 1 with "quotes" and a -> arrow\nLine 2\n'
    f.write_text(body, encoding="utf-8")
    args = _parse(["send", "agent-x", "--prompt-file", str(f)])
    assert m._resolve_prompt(args, required=True) == body


def test_resolve_reads_stdin(monkeypatch):
    args = _parse(["send", "agent-x", "--prompt-file", "-"])
    monkeypatch.setattr(m.sys, "stdin", StringIO('from "stdin"\nmore\n'))
    assert m._resolve_prompt(args, required=True) == 'from "stdin"\nmore\n'


def test_resolve_prefers_positional_when_no_file():
    args = _parse(["send", "agent-x", "hi"])
    assert m._resolve_prompt(args, required=True) == "hi"


def test_resolve_rejects_both_positional_and_file(tmp_path, capsys):
    f = tmp_path / "p.txt"
    f.write_text("body", encoding="utf-8")
    args = _parse(["send", "agent-x", "positional", "--prompt-file", str(f)])
    with pytest.raises(SystemExit) as exc:
        m._resolve_prompt(args, required=True)
    assert exc.value.code == 2
    assert "not both" in capsys.readouterr().err


def test_resolve_required_missing_errors(capsys):
    args = _parse(["send", "agent-x"])
    with pytest.raises(SystemExit) as exc:
        m._resolve_prompt(args, required=True)
    assert exc.value.code == 2
    assert "No prompt given" in capsys.readouterr().err


def test_resolve_optional_missing_returns_none():
    args = _parse(["create", "agent-x"])
    assert m._resolve_prompt(args, required=False) is None


def test_resolve_missing_file_errors(capsys):
    args = _parse(["send", "agent-x", "--prompt-file", "/no/such/prompt/file"])
    with pytest.raises(SystemExit) as exc:
        m._resolve_prompt(args, required=True)
    assert exc.value.code == 2
    assert "cannot read" in capsys.readouterr().err
