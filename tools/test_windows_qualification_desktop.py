"""Actor-free controls for the hosted desktop capability adapter."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import windows_qualification_desktop as desktop


ROOT = Path(__file__).resolve().parents[1]
NONCE = "a" * 32


def evidence():
    witnesses = [
        {"kind": kind, "hwnd": hwnd, "source_thread": thread, "received": received,
         "generated_ms": received * 100, "object_id": 0, "child_id": 0}
        for kind, hwnd, thread, received in (
            ("show", 20, 22, 2), ("foreground", 20, 22, 3),
            ("foreground", 10, 12, 4), ("destroy", 20, 22, 5),
        )
    ]
    return {
        "version": 1, "kind": "desktop-capability", "nonce": NONCE,
        "outcome": "qualified", "visible_consent_consumed": True,
        "cleanup_confirmed": True, "cleanup_errors": [],
        "original_foreground": {"hwnd": 10, "pid": 11, "thread": 12, "birth": 13},
        "preflight": {
            "session": 1, "console_session": 1, "station": "WinSta0",
            "thread_desktop": "Default", "input_desktop": "Default",
            "station_visible": True, "foreground_visible": True, "same_session": True,
            "inherited_job_membership": True, "elevated": False, "ssh": False,
            "remote_session": False, "remote_control": False,
        },
        "control_attempt": {
            "cleanup_confirmed": True, "cleanup_errors": [], "exit": 0,
            "reader_error": None, "readers_alive": [False, False],
        },
        "canary": {
            "kind": "canary-result", "nonce": NONCE, "passed": True, "hwnd": 20, "thread": 22,
            "shown": {
                "kind": "shown", "nonce": NONCE, "hwnd": 20, "thread": 22,
                "identity": {"pid": 21, "birth": 23},
            },
            "raw_witnesses": witnesses,
        },
        "calibration": {
            "sealed": True, "hooks_closed": True, "hook_count": 2, "samples": 5,
            "maximum_gap": .02, "error": None, "callback_error": None,
            "cleanup_error": None, "overflow": False, "passed": False,
            "started": 1, "ended": 6, "raw_events": witnesses, "violations": witnesses,
        },
    }


@pytest.mark.parametrize("changes", [
    {"GITHUB_ACTIONS": "false"}, {"RUNNER_ENVIRONMENT": "self-hosted"},
    {"RUNNER_TEMP": ""}, {"WINDOWS_QUALIFICATION_DESKTOP": ""},
    {"COPILOT_EXTENSIONS_TEST_CONTAINED": "1"},
])
def test_hosted_admission_refuses_before_desktop_access(tmp_path, changes):
    env = {
        "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_TEMP": str(tmp_path), "WINDOWS_QUALIFICATION_DESKTOP": "1",
    }
    desktop.require_hosted_owner(env, system="Windows", contained=False)
    with pytest.raises(ValueError, match="disposable hosted owner"):
        desktop.require_hosted_owner({**env, **changes}, system="Windows", contained=False)
    with pytest.raises(ValueError):
        desktop.require_hosted_owner(env, system="Linux", contained=False)


@pytest.mark.parametrize("field,value", [
    ("outcome", "unsupported"), ("nonce", "b" * 32),
    ("visible_consent_consumed", False), ("cleanup_confirmed", False),
    ("cleanup_errors", ["unconfirmed"]),
])
def test_result_requires_this_request_completed_control_and_cleanup(field, value):
    report = evidence()
    desktop.validate_evidence(report, NONCE)
    report[field] = value
    with pytest.raises(ValueError):
        desktop.validate_evidence(report, NONCE)


@pytest.mark.parametrize("kind", ["show", "foreground", "destroy"])
def test_missing_raw_calibration_witness_never_qualifies(kind):
    report = evidence()
    report["calibration"]["raw_events"] = [
        event for event in report["calibration"]["raw_events"] if event["kind"] != kind
    ]
    with pytest.raises(RuntimeError, match="witness"):
        desktop.validate_evidence(report, NONCE)


@pytest.mark.parametrize("section,field,value", [
    ("calibration", "samples", 0), ("calibration", "maximum_gap", .6),
    ("calibration", "sealed", False), ("calibration", "hook_count", 1),
    ("calibration", "callback_error", "observer lost"),
    ("control_attempt", "cleanup_confirmed", False),
    ("control_attempt", "readers_alive", [True, False]),
    ("preflight", "input_desktop", "private"), ("preflight", "session", 0),
    ("preflight", "same_session", False), ("preflight", "elevated", True),
])
def test_unobservable_session_coverage_or_cleanup_never_qualifies(section, field, value):
    report = evidence()
    report[section][field] = value
    with pytest.raises((ValueError, RuntimeError)):
        desktop.validate_evidence(report, NONCE)


def fake_helper(monkeypatch, *, missing=None, unavailable=False, cleanup=False):
    helper = desktop.helpers(ROOT)
    template = evidence()
    state = {"actions": [], "children": 0, "pin_closed": False}
    pin = SimpleNamespace(birth=13, gone=lambda: False)
    pin.close = lambda: state.update(pin_closed=True)
    api = SimpleNamespace(
        foreground=lambda: 10, window_identity=lambda hwnd: (11, 12) if hwnd == 10 else (21, 22),
        pin=lambda _pid: pin, identity=lambda: {"pid": 31, "birth": 32},
        user=SimpleNamespace(IsWindow=lambda _hwnd: False),
    )

    def preflight():
        if unavailable:
            raise helper.ObservationBlocked("desktop unavailable")
        return template["preflight"]

    api.require_desktop = preflight

    class Observer:
        def __init__(self, _api, _epoch):
            self.sealed = self.hooks_closed = True
            self.cleanup_error = None
            self.thread = SimpleNamespace(is_alive=lambda: False)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def packet(self):
            result = copy.deepcopy(template["calibration"])
            if not state["actions"]:
                result["violations"] = []
            return result

    class Child:
        def __init__(self, _api, argv, request, output, name, *, evidence):
            assert argv[-2:] == ["canary", "--allow-visible-control-once"]
            assert request["nonce"] == NONCE and request["visible_controls"] == 1
            state["children"] += 1
            self.evidence = evidence
            self.responses = [
                template["canary"]["shown"], {"kind": "restoring", "nonce": NONCE},
                {key: value for key, value in template["canary"].items()
                 if key not in {"shown", "raw_witnesses"}},
            ]

        def admit(self, *_args):
            return {"identity": {"pid": 21, "birth": 23}}

        def send(self, action):
            state["actions"].append(action)
            return 0, 0

        def line(self):
            return self.responses.pop(0)

        def settled(self, *_args):
            pass

        def close(self):
            self.evidence.update(template["control_attempt"])
            if cleanup:
                self.evidence["cleanup_confirmed"] = False

    def wait(predicate, _deadline, stage, _observer):
        answer = predicate()
        if not answer:
            raise helper.ObservationBlocked("missing " + stage)
        return answer

    def raw(_observer, kind, hwnd, *_args):
        return [
            row for row in template["canary"]["raw_witnesses"]
            if row["kind"] == kind and row["hwnd"] == hwnd and (kind, hwnd) != missing
        ]

    fake = SimpleNamespace(
        **{key: getattr(helper, key) for key in (
            "ObservationBlocked", "write_new", "require_time", "validate_calibration", "__file__",
        )},
        new_api=lambda: api, Child=Child, DesktopObserver=Observer, wait_for=wait, raw_after=raw,
        restore_incomplete_control=lambda *_args: None,
    )
    monkeypatch.setattr(desktop, "time", SimpleNamespace(
        monotonic=desktop.time.monotonic, sleep=lambda _seconds: None,
    ))
    return fake, state


@pytest.mark.parametrize("missing", [None, ("show", 20), ("foreground", 20),
                                    ("foreground", 10), ("destroy", 20)])
def test_adapter_uses_one_existing_canary_and_requires_every_witness(tmp_path, monkeypatch, missing):
    helper, state = fake_helper(monkeypatch, missing=missing)
    report = desktop.capture(ROOT, tmp_path / "packet", NONCE, helper=helper)
    assert report["outcome"] == ("qualified" if missing is None else "failed")
    assert report["cleanup_confirmed"] and state["pin_closed"] and state["children"] == 1
    assert state["actions"].count("show") == 1
    assert json.loads((tmp_path / "packet/desktop-capability.json").read_text())["outcome"] == report["outcome"]
    if missing is None:
        assert state["actions"] == ["show", "restore", "close"]
        desktop.validate_evidence(report, NONCE, helper=helper)


def test_unavailable_desktop_never_starts_a_control_or_becomes_a_pass(tmp_path, monkeypatch):
    helper, state = fake_helper(monkeypatch, unavailable=True)
    report = desktop.capture(ROOT, tmp_path / "packet", NONCE, helper=helper)
    assert report["outcome"] == "unsupported" and report["cleanup_confirmed"]
    assert not report["visible_consent_consumed"] and state["children"] == 0
    with pytest.raises(ValueError):
        desktop.validate_evidence(report, NONCE, helper=helper)


def test_control_cleanup_failure_cannot_qualify_or_retry(tmp_path, monkeypatch):
    helper, state = fake_helper(monkeypatch, cleanup=True)
    report = desktop.capture(ROOT, tmp_path / "packet", NONCE, helper=helper)
    assert report["outcome"] == "failed" and not report["cleanup_confirmed"]
    assert state["children"] == 1 and state["pin_closed"]
    with pytest.raises(FileExistsError):
        desktop.capture(ROOT, tmp_path / "packet", NONCE, helper=helper)
    assert state["children"] == 1
