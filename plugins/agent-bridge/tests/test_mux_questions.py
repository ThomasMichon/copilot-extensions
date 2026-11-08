"""Inspected mux effects through authenticated HTTP, with no ambient mux."""

from __future__ import annotations

import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agent_bridge.auth import BearerAuthMiddleware
from agent_bridge.live_questions import LiveQuestions
from agent_bridge.live_representation import LiveEventStore
from agent_bridge.mux_question_forms import MuxQuestionError, answer_text, completion_echo, parse_form, stable_screen
from agent_bridge.mux_questions import MuxQuestions
from agent_bridge.routes.mux_questions import router


def panel(cursor=0, typed=""):
    options = ["Blue", "Green", "Other (type your answer below)" if cursor == 2 else "Other (type your answer)"]
    return "\n".join([
        "Current turn", "Copilot needs information.", "Choose a fixture color",
        "Color:", *[("❯ " if i == cursor else "  ") + label for i, label in enumerate(options)],
        "↑↓ select · enter accept · esc cancel",
        *(["❯ " + (typed or "Type your answer...")] if cursor == 2 else []),
    ])


class FixtureBackend:
    def __init__(self):
        self.cursor = 0
        self.text = ""
        self.done = False
        self.screen = None
        self.keys = []
        self.identity = {"pane": "%7", "process": "fixture-incarnation"}
        self.before_key = None

    def binding(self, *_):
        return dict(self.identity)

    def capture(self, *_):
        if self.screen is not None:
            return self.screen
        if self.done:
            value = self.text or ["Blue", "Green"][self.cursor]
            return "Asked user Choose a fixture color\n└ Color: " + value + "\nWorking"
        return panel(self.cursor, self.text)

    def key(self, binding, value, *, literal=False):
        if self.before_key:
            self.before_key()
        self.keys.append((value, literal))
        if literal:
            self.text = value
        elif value == "Down":
            self.cursor += 1
        elif value == "Up":
            self.cursor -= 1
        elif value == "Enter":
            self.done = True


@pytest.fixture
def mux_api(tmp_db):
    tmp_db.register_live_session(
        "native-1", machine=None, cwd=None, worktree_id="fixture-worktree", repo=None,
        branch=None, pid=123, process_started_at=1.0, role=None, now=time.time(),
    )
    app = FastAPI()
    app.state.db = tmp_db
    app.state.live_questions = LiveQuestions()
    app.state.live_event_store = LiveEventStore()
    log = app.state.live_event_store.get_or_create("native-1", worktree_id="fixture-worktree")
    log.append("ask_user_request", {
        "tool_call_id": "fixture-question", "message": "Choose a fixture color",
        "requested_schema": {"type": "object", "properties": {"color": {
            "type": "string", "enum": ["Blue", "Green"],
        }}},
        "read_only": True,
    })
    app.state.mux_questions = MuxQuestions()
    backend = app.state.mux_questions.backend = FixtureBackend()
    app.add_middleware(BearerAuthMiddleware, token="synthetic-secret")
    app.include_router(router)
    scheme = "Bear" + "er "
    headers = {"Authorization": scheme + "synthetic-secret"}
    assert headers["Authorization"].endswith("synthetic-secret")
    with TestClient(app, headers=headers) as client:
        yield SimpleNamespace(
            client=client, db=tmp_db, state=app.state, backend=backend, log=log,
        )


BASE = "/api/v1/live-sessions/native-1/questions/mux"


def inspect(mux_api):
    response = mux_api.client.get(BASE)
    assert response.status_code == 200, response.text
    return response.json()["inspection_id"]


@pytest.mark.parametrize("selection,expected", [({"option": 2}, "Green"), ({"text": "custom answer"}, "custom answer")])
def test_http_choice_and_freeform_are_verified_audited_and_single_use(mux_api, selection, expected):
    token = inspect(mux_api)
    def admitted_before_effect():
        rows = mux_api.db.execute_read("SELECT state,answer FROM mux_question_answers")
        assert [(row["state"], row["answer"]) for row in rows] == [("admitted", expected)]
    mux_api.backend.before_key = admitted_before_effect
    body = {"inspection_id": token, "sender": "delegated-controller", **selection}
    response = mux_api.client.post(BASE, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "answered" and response.json()["accepted"] is True
    assert mux_api.backend.keys[-1] == ("Enter", False)
    count = len(mux_api.backend.keys)
    assert mux_api.client.post(BASE, json=body).status_code == 409
    assert len(mux_api.backend.keys) == count
    audit = mux_api.client.get(BASE + "/answers/" + token).json()
    assert audit["answer"] == expected and audit["sender"] == "delegated-controller"
    assert audit["state"] == "answered"
    events = [event for event in mux_api.log.snapshot_history()[1] if event.event == "mux_question_answer"]
    assert [event.data["phase"] for event in events] == ["admitted", "outcome"]
    mux_api.state.db.get_live_session = lambda _: None
    assert mux_api.client.get(BASE + "/answers/" + token).json()["state"] == "answered"


@pytest.mark.parametrize("change", ["screen", "identity", "resolved", "permission", "superseded"])
def test_terminal_first_and_changed_inputs_never_send_keys(mux_api, change):
    token = inspect(mux_api)
    if change == "identity":
        mux_api.backend.identity["process"] = "replacement"
    elif change == "screen":
        mux_api.backend.screen = panel().replace("Choose", "Different")
    elif change == "resolved":
        mux_api.backend.done = True
    elif change == "permission":
        mux_api.log.append("permission_request", {"tool_call_id": "permission"})
    else:
        mux_api.log.append("tool_call_update", {"tool_call_id": "fixture-question", "status": "completed"})
    response = mux_api.client.post(BASE, json={
        "inspection_id": token, "sender": "controller", "option": 1,
    })
    assert response.status_code in {200, 409}
    if response.status_code == 200:
        assert response.json()["state"] == "raced"
    assert mux_api.backend.keys == []


def test_restart_expiry_and_other_session_tokens_have_no_input_authority(mux_api):
    token = inspect(mux_api)
    mux_api.state.mux_questions.inspections[token].expires -= 61
    body = {"inspection_id": token, "sender": "controller", "option": 1}
    assert mux_api.client.post(BASE, json=body).status_code == 409
    token = inspect(mux_api)
    mux_api.state.mux_questions.inspections[token].session_id = "other"
    assert mux_api.client.post(BASE, json={**body, "inspection_id": token}).status_code == 409
    mux_api.state.mux_questions = MuxQuestions()
    assert mux_api.client.post(BASE, json={**body, "inspection_id": token}).status_code == 409
    assert mux_api.backend.keys == []


@pytest.mark.parametrize("failure", ["key", "question-prefix", "answer-suffix"])
def test_key_failure_is_unknown_and_blocks_reinspection_after_restart(mux_api, failure):
    token = inspect(mux_api)
    def fail():
        raise MuxQuestionError("synthetic key outcome unknown")
    if failure == "key":
        mux_api.backend.before_key = fail
    else:
        capture = mux_api.backend.capture
        def mismatched_echo(*args):
            screen = capture(*args)
            if mux_api.backend.done:
                if failure == "question-prefix":
                    return screen.replace("fixture color", "fixture color for another deployment")
                return screen.replace("Color: Blue", "Color: Do not select: Blue")
            return screen
        mux_api.backend.capture = mismatched_echo
    result = mux_api.client.post(BASE, json={
        "inspection_id": token, "sender": "controller", "option": 1,
    }).json()
    assert result["state"] == "unverified" and result["accepted"] is None
    assert mux_api.client.get(BASE).status_code == 409
    mux_api.state.mux_questions = MuxQuestions()
    assert mux_api.client.get(BASE).status_code == 409


def test_pane_replacement_after_navigation_stops_before_enter(mux_api):
    token = inspect(mux_api)
    mux_api.backend.before_key = lambda: mux_api.backend.identity.update(process="replacement")
    response = mux_api.client.post(BASE, json={
        "inspection_id": token, "sender": "controller", "option": 2,
    })
    assert response.json()["state"] == "unverified"
    assert mux_api.backend.keys == [("Down", False)]


def test_preissued_inspection_cannot_bypass_an_uncertain_sibling_attempt(mux_api):
    first, second = inspect(mux_api), inspect(mux_api)
    mux_api.state.mux_questions.inspections[second].fingerprint = "different-observed-screen"
    def fail():
        raise MuxQuestionError("synthetic uncertain input")
    mux_api.backend.before_key = fail
    body = {"sender": "controller", "option": 1}
    assert mux_api.client.post(BASE, json={**body, "inspection_id": first}).json()["state"] == "unverified"
    original = mux_api.db.execute_read
    # A different serving generation can have observed no admission before the
    # first write committed. The SQL mutation boundary must still refuse it.
    mux_api.db.execute_read = lambda sql, params=(): (
        [] if "SELECT inspection_id" in sql else original(sql, params)
    )
    assert mux_api.client.post(BASE, json={**body, "inspection_id": second}).status_code == 409
    assert mux_api.backend.keys == []


def test_same_screen_has_one_admission_even_with_two_inspection_tokens(mux_api):
    first, second = inspect(mux_api), inspect(mux_api)
    body = {"sender": "controller", "option": 1}
    assert mux_api.client.post(BASE, json={**body, "inspection_id": first}).json()["state"] == "answered"
    assert mux_api.client.post(BASE, json={**body, "inspection_id": second}).status_code == 409
    assert mux_api.backend.keys == [("Enter", False)]


def test_authentication_and_malformed_permission_mixed_answers_have_no_effect(mux_api):
    assert mux_api.client.get(BASE, headers={"Authorization": "invalid"}).status_code == 401
    token = inspect(mux_api)
    for fields in (
        {"option": 1, "text": "two"}, {"option": True}, {"option": 13},
        {"option": 1, "permission": True}, {"text": "first\nsecond"},
        {"text": "\x1b[A"}, {"option": 3},
    ):
        response = mux_api.client.post(BASE, json={
            "inspection_id": token, "sender": "controller", **fields,
        })
        assert response.status_code in {409, 422}, response.text
    assert mux_api.backend.keys == []


def test_native_answering_and_multifield_or_permission_evidence_prevent_fallback(mux_api):
    from agent_bridge.live_questions import QuestionObservation

    mux_api.state.live_questions.publish("native-1", QuestionObservation.model_validate({
        "generation": "extension", "pid": 123, "process_started_at": 1.0,
        "requests": [{
            "request_id": "native-request", "family": "elicitation", "answerable": True,
            "message": "Choose", "requested_schema": {"type": "object", "properties": {}},
        }],
    }))
    assert mux_api.client.get(BASE).status_code == 409
    mux_api.state.live_questions = LiveQuestions()
    mux_api.log.append("ask_user_request", {
        "tool_call_id": "fixture-question", "message": "Choose",
        "requested_schema": {"properties": {"first": {}, "second": {}}},
    })
    assert mux_api.client.get(BASE).status_code == 409
    assert mux_api.backend.keys == []


@pytest.mark.parametrize("screen", [
    panel().replace("Color:", "First:\nSecond:"),
    panel().replace("Green", "❯ Green"),
    panel().replace("Color:", "Permission approval:"),
    panel().replace("Color:", "Question 1 of 2:"),
    panel().replace("Green", "Blue"),
    panel(2, "already typed"),
    panel() + "\nCopilot needs information.\nColor:\n❯ Yes",
])
def test_ambiguous_rendering_never_parses_as_an_answerable_form(screen):
    with pytest.raises(MuxQuestionError):
        parse_form(screen)


def test_completion_requires_a_new_exact_answer_echo_not_old_history():
    form = parse_form(panel())
    old = "Asked user Choose a fixture color\n└ Color: Blue\n"
    assert not completion_echo(old, old + "Working", form, "Blue")
    assert not completion_echo("", old, form, "Green")
    assert completion_echo("", old, form, "Blue")
    for mismatched in (
        "Asked user Choose a fixture color for another deployment\n└ Color: Green\n",
        "Asked user Choose a fixture color\n└ Color: Do not select: Green\n",
        "Asked user Choose a fixture color\n└ Other field: Green\n",
        "Asked user Choose a fixture color\n└ Green\n",
        "Asked user Choose a fixture color\nWorking\n└ Color: Green\n",
    ):
        assert not completion_echo("", mismatched, form, "Green")
    assert completion_echo("", old.replace("Asked user", "● Asked user"), form, "Blue")
    assert completion_echo(old, old + old, form, "Blue")
    assert answer_text("-literal option-looking text") == "-literal option-looking text"


def test_completion_accepts_only_right_aligned_native_duration_annotations():
    form = parse_form(panel())
    old = "● Asked user Choose a fixture color\n  └ Color: Blue\n"
    for suffix in ("     5s", "     1m 34s"):
        completed = old.replace("color\n", "color" + suffix + "\n")
        assert completion_echo("", completed, form, "Blue")
        assert not completion_echo(old, completed, form, "Blue")
        assert not completion_echo("", completed, form, "Green")
    for suffix in (" 5s", "     unrelated", " for another deployment     5s", "     5s extra"):
        assert not completion_echo("", old.replace("color\n", "color" + suffix + "\n"), form, "Blue")


def test_only_known_native_decorations_are_volatile():
    pending = panel().removeprefix("Current turn\n")
    screen = " ○ Asking user Waiting for response7s\n\n────\n" + pending
    expected = stable_screen(screen)
    for elapsed in ("", "8s", "10s", "1m 34s"):
        assert stable_screen(screen.replace("7s", elapsed)) == expected
    for changed in (
        screen.replace("response7s", "permission8s"),
        screen.replace("7s", "8s extra"),
        screen.replace("────", "Changed history"),
        screen.replace("❯ Blue", "Blue"),
        screen.replace("Choose", "Different"),
        screen.replace("Blue", "Edited"),
    ):
        assert stable_screen(changed) != expected
    quoted = " ○ Asking user Waiting for response7s\nQuoted history\n" + pending
    assert stable_screen(quoted) != stable_screen(quoted.replace("7s", "8s"))
    inside = panel().replace("Choose", "○ Asking user Waiting for response7s")
    assert stable_screen(inside) != stable_screen(inside.replace("7s", "8s"))
    greeting = "│  █ ▘▝ █  Check for mistakes.│  Other text│\n" + screen
    assert stable_screen(greeting) == stable_screen(greeting.replace("▘▝", "╴╶"))
    assert stable_screen(greeting) != stable_screen(greeting.replace("Other text", "Changed"))
    assert stable_screen(greeting) != stable_screen(greeting.replace("▘▝", "??"))


def test_http_clock_ticks_preserve_the_same_one_use_inspected_screen(mux_api, monkeypatch):
    capture = mux_api.backend.capture
    ticks = iter(range(7, 100))

    def ticking_capture(*args):
        value = capture(*args)
        if mux_api.backend.done:
            return value
        return (
            f" ○ Asking user Waiting for response{next(ticks)}s\n\n────\n"
            + value.removeprefix("Current turn\n")
        )

    monkeypatch.setattr(mux_api.backend, "capture", ticking_capture)
    first, second = inspect(mux_api), inspect(mux_api)
    inspections = mux_api.state.mux_questions.inspections
    assert inspections[first].fingerprint == inspections[second].fingerprint
    body = {"inspection_id": first, "sender": "controller", "option": 1}
    response = mux_api.client.post(BASE, json=body)
    assert response.status_code == 200 and response.json()["accepted"] is True, response.text
    assert mux_api.backend.keys == [("Enter", False)]
    body["inspection_id"] = second
    assert mux_api.client.post(BASE, json=body).status_code == 409
    assert mux_api.backend.keys == [("Enter", False)]
