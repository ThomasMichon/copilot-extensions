"""Actor-free native form rendering and exact completion evidence."""

import importlib.util
from pathlib import Path
import sys

import pytest


@pytest.fixture
def forms(monkeypatch):
    source = Path(__file__).resolve().parents[2] / "plugins/agent-bridge/src/agent_bridge/mux_question_forms.py"
    spec = importlib.util.spec_from_file_location("promotion_mux_forms", source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_native_other_expansion_preserves_question_but_requires_exact_typed_input(forms):
    before = (
        "Copilot needs information.\nChoose\nColor:\n❯ red\nblue\nOther (type your answer)\n"
        "↑/↓ select · enter accept"
    )
    after = before.replace("❯ red", "red").replace(
        "Other (type your answer)", "❯ Other (type your answer below)",
    ) + "\n❯ blue"
    assert forms.parse_form(before).same_question(forms.parse_form(after, typed="blue"))
    with pytest.raises(ValueError):
        forms.parse_form(after)
    with pytest.raises(ValueError):
        forms.parse_form(after, typed="red")


@pytest.mark.parametrize("value", [
    pytest.param("a\nb", id="linefeed"), pytest.param("a\rb", id="return"),
    pytest.param("\x1b[A", id="escape"), pytest.param("\x00", id="nul"),
    pytest.param("\u2028", id="unicode-separator"), pytest.param("", id="empty"),
    pytest.param(";", id="command-separator"),
])
def test_mux_input_cannot_encode_terminal_controls(forms, value):
    with pytest.raises(ValueError):
        forms.answer_text(value)


def test_native_echo_is_exact_and_new(forms):
    panel = "Copilot needs information.\nChoose\nColor:\n❯ red\nblue\n↑/↓ select · enter accept"
    form = forms.parse_form(panel)
    completion = "Asked user Choose\n└ Color: blue\nWorking"
    assert forms.completion_echo(panel, completion, form, "blue")
    assert not forms.completion_echo(completion, completion, form, "blue")
    assert not forms.completion_echo(panel, completion, form, "red")
    assert not forms.completion_echo(
        panel, completion.replace("Choose", "Choose another deployment"), form, "blue",
    )
    assert not forms.completion_echo(
        panel, completion.replace("Color: blue", "Color: Do not select: blue"), form, "blue",
    )
    assert not forms.completion_echo(
        panel, completion.replace("Color: blue", "Another field: blue"), form, "blue",
    )
    timed = completion.replace("Choose\n", "Choose     5s\n")
    assert forms.completion_echo(panel, timed, form, "blue")
    assert not forms.completion_echo(completion, timed, form, "blue")
    assert not forms.completion_echo(
        panel, timed.replace("Choose", "Choose another deployment"), form, "blue",
    )
    pending = " ○ Asking user Waiting for response7s\n────\n" + panel
    assert forms.stable_screen(pending) == forms.stable_screen(pending.replace("7s", "8s"))
    assert forms.stable_screen(pending) != forms.stable_screen(pending.replace("Choose", "Changed"))
    greeting = "│  █ ▘▝ █  Check for mistakes.│\n" + pending
    assert forms.stable_screen(greeting) == forms.stable_screen(greeting.replace("▘▝", "╴╶"))
