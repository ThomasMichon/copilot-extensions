"""Actor-free response-shape admission; native and HTTP tests remain separate."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def questions(monkeypatch):
    source = (
        Path(__file__).resolve().parents[2]
        / "plugins/agent-bridge/src/agent_bridge/live_question_validation.py"
    )
    spec = importlib.util.spec_from_file_location("promotion_live_questions", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_form_response_admission_preserves_fields_and_decline_semantics(questions):
    question = SimpleNamespace(
        family="elicitation", request_id="native-request", message="Choose", answerable=True,
        reason=None,
        requested_schema={"properties": {"color": {"type": "string"}}, "required": ["color"]},
    )
    def answer(**values):
        return SimpleNamespace(
            generation="extension", request_id="native-request", family="elicitation",
            sender="delegated-controller", **{"action": "accept", "content": {}, **values},
        )
    questions.validate_answer(question, answer(content={"color": "a freeform choice"}))
    questions.validate_answer(question, answer(action="decline"))
    for invalid in (
        {"content": {}}, {"content": {"other": "blue"}},
        {"content": {"color": {"nested": "blue"}}},
        {"content": {"color": [1]}},
        {"action": "cancel", "content": {"color": "blue"}},
    ):
        with pytest.raises(ValueError):
            questions.validate_answer(question, answer(**invalid))
    with pytest.raises(ValueError):
        questions.validate_answer(
            SimpleNamespace(**{**vars(question), "answerable": False}), answer(),
        )


def test_text_input_requires_an_allowed_answer_and_never_uses_form_actions(questions):
    question = SimpleNamespace(
        family="user_input", request_id="native-request", message="Choose", answerable=True,
        choices=["blue", "red"], allow_freeform=False, reason=None,
    )
    def answer(**values):
        return SimpleNamespace(
            generation="extension", request_id="native-request", family="user_input",
            sender="delegated-controller", **{"action": "accept", "content": {}, **values},
        )
    questions.validate_answer(question, answer(content={"answer": "blue"}))
    for invalid in (
        {"content": {"answer": "green"}}, {"content": {"answer": 1}},
        {"content": {"other": "blue"}}, {"action": "decline"}, {"action": "cancel"},
    ):
        with pytest.raises(ValueError):
            questions.validate_answer(question, answer(**invalid))
    questions.validate_answer(
        SimpleNamespace(**{**vars(question), "allow_freeform": True}),
        answer(content={"answer": "green"}),
    )
