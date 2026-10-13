"""Contract tests for the shared update-run telemetry library (``libs/update-telemetry``).

They live in this suite because agent-worktrees consumes the library as a path
dependency; the schemas and golden fixtures stay with the library itself.
"""

from __future__ import annotations

import errno
import itertools
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import update_telemetry as ut
from update_telemetry import run as run_mod

_PLUGIN = Path(__file__).resolve().parents[1]


def _lib_root() -> Path:
    """The library checkout: the editable source, a plugin-local copy, or the repo's."""
    candidates = (
        Path(ut.__file__).resolve().parents[2],
        _PLUGIN / "libs" / "update-telemetry",
        _PLUGIN.parents[1] / "libs" / "update-telemetry",
    )
    for candidate in candidates:
        if (candidate / "schemas").is_dir() and (candidate / "fixtures").is_dir():
            return candidate
    raise AssertionError("libs/update-telemetry (schemas + fixtures) not found")


LIB = _lib_root()
SCHEMAS = LIB / "schemas"
FIXTURES = LIB / "fixtures"
ENV_KEYS = (ut.ENV_RUN_ID, ut.ENV_EVENTS, ut.ENV_OWNER)


@pytest.fixture
def telemetry_env(tmp_path):
    return {ut.ENV_HOME: str(tmp_path / "home")}


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))


_TYPES = {
    "object": dict, "array": list, "string": str, "integer": int,
    "boolean": bool, "null": type(None),
}


def _validate(value, schema: dict, path: str = "$") -> list[str]:
    """Minimal JSON-Schema subset (type/const/enum/required/properties/items)."""
    errors: list[str] = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in enum")
    if "type" in schema:
        kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        ok = any(
            isinstance(value, _TYPES[k]) and not (k == "integer" and isinstance(value, bool))
            for k in kinds
        )
        if not ok:
            return [*errors, f"{path}: {type(value).__name__} is not {kinds}"]
    if isinstance(value, str) and len(value) > schema.get("maxLength", len(value)):
        errors.append(f"{path}: too long")
    if isinstance(value, dict):
        errors += [f"{path}: missing {k}" for k in schema.get("required", []) if k not in value]
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in props:
                errors += _validate(item, props[key], f"{path}.{key}")
            elif extra is False:
                errors.append(f"{path}: unexpected {key}")
            elif isinstance(extra, dict):
                errors += _validate(item, extra, f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors += _validate(item, schema["items"], f"{path}[{index}]")
    return errors


def _status(telemetry_env) -> dict:
    home = Path(telemetry_env[ut.ENV_HOME])
    return json.loads((home / "update-status.json").read_text("utf-8"))


def test_owner_vs_participant_env(telemetry_env):
    owner = ut.Run.from_env_or_new("manager", environ=telemetry_env)
    assert owner.is_owner and owner.enabled
    assert telemetry_env[ut.ENV_RUN_ID] == owner.run_id
    assert Path(telemetry_env[ut.ENV_EVENTS]).is_absolute()
    assert telemetry_env[ut.ENV_OWNER] == f"manager:{os.getpid()}"
    assert _status(telemetry_env)["run"]["outcome"] == ut.IN_PROGRESS

    child = ut.Run.from_env_or_new("engine", environ=dict(telemetry_env))
    assert not child.is_owner
    assert child.run_id == owner.run_id
    assert child.events_path == owner.events_path
    child.target("runtime_reconcile", "agent-x@copilot-extensions", ut.VERIFIED_CURRENT)
    assert child.finish() == 0

    assert owner.finish() == 0
    assert not any(key in telemetry_env for key in ENV_KEYS), "owner restores the environment"
    events, errors = ut.read_events(owner.events_path)
    assert errors == 0
    assert {e["proc"] for e in events} == {"manager", "engine"}
    status = _status(telemetry_env)
    assert status["run"]["outcome"] == ut.VERIFIED_CURRENT
    assert "agent-x@copilot-extensions" in status["plugins"]
    assert status["last_success"]["run_id"] == owner.run_id


def test_timeout_maps_to_timeout(telemetry_env):
    run = ut.Run.from_env_or_new("engine", environ=telemetry_env)
    with pytest.raises(subprocess.TimeoutExpired):
        with run.phase("runtime_reconcile", action="install.sh update"):
            raise subprocess.TimeoutExpired(["install.sh"], 5)
    end = [e for e in ut.read_events(run.events_path)[0] if e["event"] == "end"][-1]
    assert end["outcome"] == ut.TIMEOUT
    assert end["error"]["code"] == "timeout"
    assert run.finish(ut.TIMEOUT) == 6


def test_permission_error_has_path_op(telemetry_env, tmp_path):
    run = ut.Run.from_env_or_new("engine", environ=telemetry_env)
    denied = str(tmp_path / "owned-by-someone-else" / "file.dll")
    with pytest.raises(PermissionError):
        with run.phase("payload_refresh", action="copy"):
            raise PermissionError(errno.EACCES, "Permission denied", denied)
    end = [e for e in ut.read_events(run.events_path)[0] if e["event"] == "end"][-1]
    assert end["outcome"] == ut.COMMAND_FAILED
    assert end["error"]["code"] == ut.PERMISSION_DENIED
    assert end["error"]["path"] == denied
    assert end["error"]["op"] == "copy"
    assert run.finish(ut.COMMAND_FAILED, error=end["error"]) == 1
    fix = _status(telemetry_env)["last_failure"]["fix"]
    assert "permissions" in fix and "close" not in fix.lower()


def _os_error(errno_value, *, winerror=None, cls=OSError):
    exc = cls(errno_value, "simulated", "/some/path")
    if winerror is not None:
        exc.winerror = winerror
    return exc


@pytest.mark.parametrize(("exc", "outcome", "code"), [
    (_os_error(errno.EIO), ut.COMMAND_FAILED, ut.IO_ERROR),
    (_os_error(errno.EACCES, cls=PermissionError), ut.COMMAND_FAILED, ut.PERMISSION_DENIED),
    (_os_error(errno.EPERM, cls=PermissionError), ut.COMMAND_FAILED, ut.PERMISSION_DENIED),
    (_os_error(errno.EACCES, winerror=5), ut.COMMAND_FAILED, ut.PERMISSION_DENIED),
    (_os_error(errno.EACCES, winerror=32), ut.BLOCKED_BY_LOCK, ut.SHARING_VIOLATION),
    (_os_error(errno.EACCES, winerror=33), ut.BLOCKED_BY_LOCK, "lock_violation"),
    (ut.LockTimeout("could not lock x"), ut.BLOCKED_BY_LOCK, ut.LOCK_TIMEOUT),
    (_os_error(errno.ENOENT), ut.COMMAND_FAILED, f"os_error_{errno.ENOENT}"),
], ids=["posix-eio", "posix-eacces", "posix-eperm", "win-access-denied",
        "win-sharing-violation", "win-lock-violation", "lock-timeout", "enoent"])
def test_os_error_classification_needs_contention_evidence(exc, outcome, code):
    actual, error = ut.classify_exception(exc)
    assert (actual, error["code"]) == (outcome, code)


def test_only_contention_evidence_is_blocked_by_lock():
    assert ut.outcome_for_installer_exit(75)[0] == ut.BLOCKED_BY_LOCK
    assert "close" not in ut.outcomes.fix_for(ut.COMMAND_FAILED, None, ut.IO_ERROR).lower()


_LOCKER = """
import pathlib
import sys
sys.path.insert(0, sys.argv[1])
import update_telemetry as ut
from update_telemetry import run
try:
    with run.file_lock(pathlib.Path(sys.argv[2]), 0.2):
        print("acquired")
except ut.LockTimeout:
    print("lock_timeout")
"""


def test_file_lock_timeout_is_lock_timeout(tmp_path):
    lock = tmp_path / "held.lock"
    with run_mod.file_lock(lock, 1.0):
        child = subprocess.run(
            [sys.executable, "-c", _LOCKER, str(LIB / "src"), str(lock)],
            capture_output=True, text=True, timeout=60, check=False,
        )
    assert child.stdout.strip() == "lock_timeout", child.stderr


def test_keyboard_interrupt_is_interrupted():
    outcome, error = ut.classify_exception(KeyboardInterrupt())
    assert outcome == ut.COMMAND_FAILED
    assert ut.exit_code_for(outcome, error["code"]) == 130


def test_redaction(telemetry_env):
    pat = "gh" + "p_" + "Q" * 36
    fine = "github" + "_pat_" + "Z" * 40
    scheme = "Bea" + "rer"
    header_value = scheme + " " + "s3cr3tVALUE" * 3
    texts = [
        "-c http.extraheader=AUTHORIZATION: " + header_value,
        "Authorization: " + header_value,
        scheme + " " + "abcDEF123456789xyz",
        "token " + pat,
        "fine-grained " + fine,
        "https://user:" + "pa55" + "word@example.com/repo.git",
    ]
    redacted = [ut.redact(t) for t in texts]
    leaked = [r for r in redacted if "s3cr3tVALUE" in r or "Q" * 20 in r or "Z" * 20 in r
              or "abcDEF123456789xyz" in r or "pa55word" in r]
    assert not leaked
    assert ut.redact("plain message") == "plain message"

    run = ut.Run.from_env_or_new("engine", environ=telemetry_env)
    run.emit("payload_refresh", "run", "info", argv=["git", texts[0]], action=texts[1],
             error={"code": "x", "message": texts[3]}, details={"note": texts[4]})
    run.finish()
    raw = run.events_path.read_text(encoding="utf-8")
    assert "s3cr3tVALUE" not in raw
    assert ("Q" * 20) not in raw
    assert ("Z" * 20) not in raw


_APPENDER = """
import sys
sys.path.insert(0, sys.argv[1])
import update_telemetry as ut
run = ut.Run.from_env_or_new(sys.argv[2])
for i in range(500):
    run.emit("runtime_reconcile", "run", "info", details={"i": i, "pad": "x" * 200})
"""


def test_two_process_appends_parse(telemetry_env):
    owner = ut.Run.from_env_or_new("manager", environ=telemetry_env)
    child_env = {**os.environ, **telemetry_env}
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _APPENDER, str(LIB / "src"), proc], env=child_env
        )
        for proc in ("engine", "installer")
    ]
    assert [p.wait(timeout=120) for p in procs] == [0, 0]
    events, errors = ut.read_events(owner.events_path)
    assert errors == 0
    infos = [e for e in events if e["event"] == "info"]
    assert len(infos) == 1000
    for proc in ("engine", "installer"):
        seqs = [e["seq"] for e in infos if e["proc"] == proc]
        assert seqs == sorted(seqs) and len(seqs) == 500
    owner.finish()


def _target(component, outcome, required=True, phase="runtime_reconcile"):
    return {"v": 1, "ts": "2026-01-01T00:00:00.000Z", "run_id": "r", "seq": 1, "pid": 1,
            "proc": "engine", "phase": phase, "component": component, "event": "end",
            "outcome": outcome, "details": {"required": required}}


def test_fold_precedence():
    order = [ut.VERIFIED_CURRENT, ut.REQUIRES_RESTART, ut.IN_PROGRESS,
             ut.CONFIGURATION_LIMITATION, ut.BLOCKED_BY_LOCK, ut.AUTH_REQUIRED,
             ut.TIMEOUT, ut.COMMAND_FAILED]
    assert sorted(order, key=ut.SEVERITY.__getitem__) == order
    for lower, higher in itertools.pairwise(order):
        events = [_target("a@m", higher), _target("b@m", lower)]
        assert ut.run_outcome(ut.fold_targets(events)) == higher
    events = [
        _target("a@m", ut.VERIFIED_CURRENT),
        _target("b@m", ut.COMMAND_FAILED, required=False),
        _target("c@m", ut.SKIPPED),
        _target("d@m", ut.REQUIRES_RESTART),
    ]
    status = ut.fold(events, run={"id": "r", "owner": "engine:1"})
    assert status["run"]["outcome"] == ut.REQUIRES_RESTART
    assert status["run"]["exit_code"] == 0
    assert status["restart_required"] is True
    assert [a["component"] for a in status["advisories"]] == ["b@m"]
    assert status["plugins"]["c@m"]["outcome"] == ut.SKIPPED
    same = [_target("a@m", ut.VERIFIED_CURRENT, phase="payload_refresh"),
            _target("a@m", ut.TIMEOUT)]
    assert ut.fold_targets(same)["a@m"]["outcome"] == ut.TIMEOUT


def test_exit_code_table():
    assert ut.exit_code_for(ut.VERIFIED_CURRENT) == 0
    assert ut.exit_code_for(ut.REQUIRES_RESTART) == 0
    assert ut.exit_code_for(ut.COMMAND_FAILED) == 1
    assert ut.EXIT_USAGE == 2
    assert ut.exit_code_for(ut.CONFIGURATION_LIMITATION) == 3
    assert ut.exit_code_for(ut.AUTH_REQUIRED) == 4
    assert ut.exit_code_for(ut.BLOCKED_BY_LOCK) == 5
    assert ut.exit_code_for(ut.TIMEOUT) == 6
    assert ut.exit_code_for(ut.COMMAND_FAILED, ut.INTERRUPTED) == 130
    assert ut.outcome_for_installer_exit(0) == (ut.VERIFIED_CURRENT, None)
    assert ut.outcome_for_installer_exit(75)[0] == ut.BLOCKED_BY_LOCK
    assert ut.outcome_for_installer_exit(76)[0] == ut.COMMAND_FAILED
    assert ut.outcome_for_installer_exit(124)[0] == ut.TIMEOUT
    assert ut.outcome_for_installer_exit(3)[0] == ut.COMMAND_FAILED
    for code in (0, 1, 2, 3, 4, 5, 6, 130):
        outcome, error_code = ut.outcome_for_exit_code(code)
        assert ut.exit_code_for(outcome, error_code) == code
    assert ut.outcome_for_exit_code(2) == (ut.COMMAND_FAILED, ut.USAGE)


def test_schema_examples(telemetry_env):
    event_schema, status_schema = _schema("update-event"), _schema("update-status")
    example_event = {"v": 1, "ts": "2026-10-10T04:12:03.512Z",
                     "run_id": "20261010T041200Z-1234-abcd", "seq": 42, "pid": 1234,
                     "proc": "engine", "phase": "runtime_reconcile",
                     "component": "agent-bridge@copilot-extensions", "event": "end",
                     "action": "install.ps1 update", "duration_ms": 17840,
                     "outcome": "verified_current", "changed": True, "exit_code": 0}
    assert _validate(example_event, event_schema) == []
    example_status = json.loads((FIXTURES / "status-example.json").read_text("utf-8"))
    assert _validate(example_status, status_schema) == []

    run = ut.Run.from_env_or_new("engine", environ=telemetry_env)
    run.enter("payload_refresh")
    run.target("payload_refresh", "a@copilot-extensions", ut.VERIFIED_CURRENT, changed=True)
    run.enter("runtime_reconcile")
    run.target("runtime_reconcile", "b@copilot-extensions", ut.TIMEOUT, detail="timed out",
               error={"code": "timeout", "message": "timed out"})
    assert run.outcome == ut.TIMEOUT
    assert run.finish() == 6
    events, errors = ut.read_events(run.events_path)
    assert errors == 0
    assert all(_validate(e, event_schema) == [] for e in events)
    status = _status(telemetry_env)
    assert _validate(status, status_schema) == []
    assert [p["phase"] for p in status["run"]["phases"]] == [
        "payload_refresh", "runtime_reconcile"]
    assert status["run"]["phases"][1]["outcome"] == ut.TIMEOUT
    assert status["last_failure"]["outcome"] == ut.TIMEOUT
    assert status["last_failure"]["fix"]


def test_golden_fold():
    events, errors = ut.read_events(FIXTURES / "golden-events.jsonl")
    expected = json.loads((FIXTURES / "golden-status.json").read_text("utf-8"))
    run = dict(expected["run"])
    for key in ("outcome", "exit_code", "phases"):
        run.pop(key)
    run["event_parse_errors"] = errors
    previous = {"last_success": expected["last_success"]}
    actual = ut.fold(events, run=run, previous=previous, updated_at=expected["updated_at"])
    assert actual == expected
    assert _validate(actual, _schema("update-status")) == []


def test_fail_open_when_home_unwritable(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    telemetry_env = {ut.ENV_HOME: str(blocker / "home")}
    run = ut.Run.from_env_or_new("engine", environ=telemetry_env)
    assert not run.enabled
    run.enter("payload_refresh")
    run.target("payload_refresh", "a@m", ut.TIMEOUT)
    with run.phase("prune"):
        pass
    assert run.finish() == 6
    assert ut.ENV_RUN_ID not in telemetry_env


def test_status_write_error_never_raises(telemetry_env, monkeypatch):
    run = ut.Run.from_env_or_new("engine", environ=telemetry_env)

    def boom(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(run_mod, "write_json_atomic", boom)
    monkeypatch.setattr(run_mod, "append_line", boom)
    run.target("runtime_reconcile", "a@m", ut.VERIFIED_CURRENT)
    assert run.finish() == 0
    assert ut.ENV_RUN_ID not in telemetry_env


def test_prune_keeps_newest_runs(telemetry_env):
    runs = Path(telemetry_env[ut.ENV_HOME]) / "runs"
    for index in range(25):
        (runs / f"20200101T0000{index:02d}Z-1-abcd").mkdir(parents=True)
    run = ut.Run.from_env_or_new("engine", environ=telemetry_env)
    run.finish()
    names = sorted(p.name for p in runs.iterdir())
    assert len(names) == run_mod.KEEP_RUNS
    assert run.run_id in names
    assert "20200101T000000Z-1-abcd" not in names


@pytest.mark.parametrize("field", [
    "action", "component", "phase", "argv", "details",
    "error.message", "error.path", "error.op", "error.code",
])
@pytest.mark.parametrize("filler", ["x", "\u00e9", "\U0001f600", "\x01"])
def test_oversize_event_fits_line_limit(telemetry_env, field, filler):
    huge = filler * 20000
    fields = {"error": {"code": "c", "message": "m", "path": "p", "op": "o"}}
    phase, component = "runtime_reconcile", "a@m"
    if field == "phase":
        phase = huge
    elif field == "component":
        component = huge
    elif field == "argv":
        fields["argv"] = [huge, huge]
    elif field == "details":
        fields["details"] = {"blob": huge}
    elif field.startswith("error."):
        fields["error"][field.split(".", 1)[1]] = huge
    else:
        fields[field] = huge
    run = ut.Run.from_env_or_new("engine", environ=telemetry_env)
    run.emit(phase, component, "end", outcome=ut.VERIFIED_CURRENT, **fields)
    run.finish()
    raw = run.events_path.read_bytes().splitlines()
    assert all(len(line) + 1 <= run_mod.MAX_LINE for line in raw)
    events = [json.loads(line) for line in raw]
    assert any(e["event"] == "end" and e.get("outcome") == ut.VERIFIED_CURRENT
               and e["run_id"] == run.run_id and e["phase"] != "run" for e in events)


def _record_of_size(size: int) -> dict:
    record = {"v": 1, "event": "info", "action": ""}
    base = len(json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    record["action"] = "a" * (size - base)
    return record


def test_line_limit_counts_the_newline():
    exact = _record_of_size(run_mod.MAX_LINE - 1)
    line = run_mod.encode_event(dict(exact))
    assert line == json.dumps(exact, separators=(",", ":"), ensure_ascii=False)
    assert len(line.encode("utf-8")) + 1 == run_mod.MAX_LINE

    over = _record_of_size(run_mod.MAX_LINE)
    line = run_mod.encode_event(dict(over))
    assert len(line.encode("utf-8")) + 1 <= run_mod.MAX_LINE
    assert json.loads(line)["action"] != over["action"]


def test_written_lines_never_exceed_the_limit(telemetry_env):
    run = ut.Run.from_env_or_new("engine", environ=telemetry_env)
    for pad in range(run_mod.MAX_LINE - 400, run_mod.MAX_LINE + 50, 7):
        run.emit("runtime_reconcile", "a@m", "info", action="x" * pad)
    run.finish()
    data = run.events_path.read_bytes()
    assert b"\r" not in data, "events are written byte-exact (no newline translation)"
    assert all(len(line) <= run_mod.MAX_LINE for line in data.splitlines(keepends=True))


@pytest.mark.parametrize(("outcome", "target", "expected"), [
    (ut.COMMAND_FAILED, None, ut.COMMAND_FAILED),
    (ut.VERIFIED_CURRENT, None, ut.VERIFIED_CURRENT),
    (ut.VERIFIED_CURRENT, ut.TIMEOUT, ut.TIMEOUT),
    (None, ut.BLOCKED_BY_LOCK, ut.BLOCKED_BY_LOCK),
])
def test_finish_outcome_reaches_the_open_step(telemetry_env, outcome, target, expected):
    run = ut.Run.from_env_or_new("engine", environ=telemetry_env)
    run.enter("prune")
    if target:
        run.target("prune", "a@m", target)
    run.finish(outcome)
    phases = _status(telemetry_env)["run"]["phases"]
    assert [(p["phase"], p["outcome"]) for p in phases] == [("prune", expected)]


def test_explicit_skipped_phase_stays_skipped(telemetry_env):
    run = ut.Run.from_env_or_new("engine", environ=telemetry_env)
    with run.phase("verify") as result:
        result.outcome = ut.SKIPPED
    run.finish()
    assert _status(telemetry_env)["run"]["phases"][0]["outcome"] == ut.SKIPPED


def test_older_overlapping_run_never_overwrites_newer_status(tmp_path):
    home = str(tmp_path / "home")
    run_a = ut.Run.from_env_or_new("engine", environ={ut.ENV_HOME: home})
    time.sleep(0.01)
    run_b = ut.Run.from_env_or_new("engine", environ={ut.ENV_HOME: home})
    assert run_b.is_owner and run_b.run_id != run_a.run_id
    run_b.target("runtime_reconcile", "b@m", ut.VERIFIED_CURRENT)
    assert run_b.finish() == 0
    run_a.target("runtime_reconcile", "a@m", ut.TIMEOUT)
    assert run_a.finish() == 6, "the exit code is unaffected"
    status = _status({ut.ENV_HOME: home})
    assert status["run"]["id"] == run_b.run_id
    assert status["last_success"]["run_id"] == run_b.run_id
    assert status["last_failure"] is None


def test_newer_run_ordering_tie_breaks_on_run_id():
    started = "2026-01-01T00:00:00.000Z"
    previous = {"run": {"id": "20260101T000000Z-2-bbbb", "started_at": started}}
    assert run_mod._newer_run(previous, "20260101T000000Z-1-aaaa", started) == (
        "20260101T000000Z-2-bbbb")
    assert run_mod._newer_run(previous, "20260101T000000Z-3-cccc", started) is None
    assert run_mod._newer_run(previous, "20260101T000000Z-2-bbbb", started) is None
    assert run_mod._newer_run(None, "x", started) is None
