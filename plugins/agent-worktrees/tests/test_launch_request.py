"""Remote requests reuse target seed authority and stable creation identity."""

import base64
import json
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent_worktrees import launch_request as request, launch_seed_state, tracking, tracking_write
from agent_worktrees import remote_seed_launch


def _intent(kind="resume", text="line one\n'\";$() \u2603"):
    return dict(version=1, request_id="a" * 32, kind=kind,
                worktree_id="wt-a" if kind == "resume" else None, text=text, no_mux=False)


@pytest.fixture
def authority(tmp_path, monkeypatch):
    monkeypatch.setattr(request.cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(request.cfg, "detect_platform", lambda: "windows")
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    endpoint = tracking_write.rendezvous_fields(server)
    monkeypatch.setattr(
        launch_seed_state, "_dispatch",
        lambda verb, args, **kwargs: tracking_write.dispatch(
            verb, args, read_lock_data=lambda: endpoint, ensure_monitor=None, boot_wait_s=0,
            **kwargs,
        ),
    )
    config = SimpleNamespace(machine="test", default_repo=SimpleNamespace(worktree_root=tmp_path / "trees"))
    try:
        yield config
    finally:
        server.close()


def test_resume_admitted_once_via_real_daemon_then_finished_never_replays(tmp_path, authority):
    record = tracking.create_new_record("wt-a", "worktree/wt-a", str(tmp_path / "tree"),
                                       "demo", "test", "windows", tmp_path)
    intent = _intent()
    first = request.execute(authority, intent)
    second = request.execute(authority, intent)
    assert first == second
    seed = launch_seed_state.peek(record.yaml_path)
    assert seed.text == intent["text"] and seed.seed_id == intent["request_id"]
    assert request.inspect(intent["request_id"])["ready"]
    taken = launch_seed_state.take(record.yaml_path, seed_id=seed.seed_id)
    assert launch_seed_state.finish(record.yaml_path, taken)["finished"]
    with pytest.raises(ValueError, match="completed"):
        request.execute(authority, intent)
    assert launch_seed_state.peek(record.yaml_path) is None
    assert request.inspect(intent["request_id"])["state"] == "completed-or-superseded"


def test_resume_new_intent_can_replace_old_but_old_request_cannot_restore_it(tmp_path, authority):
    record = tracking.create_new_record("wt-a", "worktree/wt-a", str(tmp_path / "tree"),
                                       "demo", "test", "windows", tmp_path)
    first = _intent()
    request.execute(authority, first)
    replacement = {**first, "request_id": "b" * 32, "text": "replacement"}
    request.execute(authority, replacement)
    with pytest.raises(ValueError, match="superseded"):
        request.execute(authority, first)
    assert launch_seed_state.peek(record.yaml_path).text == "replacement"


def test_new_response_loss_reuses_allocated_worktree_not_new_creation(tmp_path, authority, monkeypatch):
    from agent_worktrees import worktree_creation

    created = []
    def create(config, **kwargs):
        timestamp, suffix = kwargs["allocation"]
        wid = f"test-win-{timestamp}-{suffix}"
        created.append(wid)
        record = tracking.create_new_record(wid, f"worktree/{wid}", str(tmp_path / "tree"),
                                           "demo", "test", "windows", tmp_path)
        launch_seed_state.stage(record.yaml_path, kind="new", text=kwargs["pending_seed"],
                                seed_id=kwargs["pending_seed_id"])
        raise OSError("response lost after creation")
    monkeypatch.setattr(worktree_creation, "_create_worktree_core", create)
    with pytest.raises(OSError):
        request.execute(authority, _intent("new"))
    receipt = request.execute(authority, _intent("new"))
    assert created == [receipt["worktree_id"]]
    assert launch_seed_state.peek(tmp_path / f"{created[0]}.yaml").kind == "new"


def test_new_admission_uses_real_creation_core_with_fixed_allocation(tmp_path, authority, monkeypatch):
    from test_worktree_creation_seed import _create_config, _stub_create_worktree_core_internals

    config = _create_config(tmp_path)
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    first = request.execute(config, _intent("new"))
    second = request.execute(config, _intent("new"))
    assert first == second
    records = tracking.list_records(tmp_path)
    assert len(records) == 1
    assert records[0].worktree_id.endswith("a" * 32)
    seed = launch_seed_state.peek(records[0].yaml_path)
    assert seed.seed_id == "a" * 32 and seed.kind == "new"
    launch_seed_state.remove(records[0].yaml_path, remove_record=True)
    with pytest.raises(ValueError, match="instead of recreating"):
        request.execute(config, _intent("new"))


def test_unaccepted_new_cannot_recreate_removed_worktree(tmp_path, authority, monkeypatch):
    from agent_worktrees import worktree_creation

    def interrupted(config, **kwargs):
        raise OSError("interrupted before record publication")
    monkeypatch.setattr(worktree_creation, "_create_worktree_core", interrupted)
    with pytest.raises(OSError):
        request.execute(authority, _intent("new"))
    with pytest.raises(ValueError, match="instead of recreating"):
        request.execute(authority, _intent("new"))


@pytest.mark.parametrize("finished", [False, True])
def test_interrupted_replacement_seed_revision_fences_old_admission(tmp_path, authority, finished):
    record = tracking.create_new_record("wt-a", "worktree/wt-a", str(tmp_path / "tree"),
                                       "demo", "test", "windows", tmp_path)
    intent = _intent()
    fingerprint = __import__("hashlib").sha256(
        json.dumps(intent, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    request._dispatch("launch_request_admit", {
        "tracking_dir": str(tmp_path), "request_id": intent["request_id"],
        "fingerprint": fingerprint, "kind": "resume", "worktree_id": "wt-a",
        "timestamp": "20261010-120000",
    })
    launch_seed_state._write(record.yaml_path, launch_seed_state.LaunchSeed(
        "b" * 32, "resume", "replacement", 1,
    ))
    if finished:
        tracking._atomic_write(launch_seed_state.state_path(record.yaml_path), json.dumps({
            "version": 1, "finished": True, "seed_id": "b" * 32, "revision": 2,
        }))
    with pytest.raises(ValueError, match="changed after admission"):
        request.execute(authority, intent)
    if finished:
        assert launch_seed_state.peek(record.yaml_path) is None
    else:
        assert launch_seed_state.peek(record.yaml_path).text == "replacement"


def test_decode_round_trip_preserves_shell_metacharacters():
    intent = _intent()
    encoded = base64.b64encode(json.dumps(intent, ensure_ascii=False).encode()).decode()
    assert request.decode(encoded) == intent
    with pytest.raises(ValueError):
        request.decode(encoded + "!")


def test_changed_request_cannot_reuse_an_admitted_identity(tmp_path, authority):
    tracking.create_new_record("wt-a", "worktree/wt-a", str(tmp_path / "tree"),
                               "demo", "test", "windows", tmp_path)
    intent = _intent()
    request.execute(authority, intent)
    with pytest.raises(ValueError, match="different intent"):
        request.execute(authority, {**intent, "text": "changed"})


def test_structured_cli_uses_no_selector_and_returns_no_seed_text(tmp_path, authority, monkeypatch, capsys):
    import argparse
    from agent_worktrees import resolve_cli

    tracking.create_new_record("wt-a", "worktree/wt-a", str(tmp_path / "tree"),
                               "demo", "test", "windows", tmp_path)
    monkeypatch.setattr(request.cfg, "load_config", lambda: authority)
    parsers = argparse.ArgumentParser().add_subparsers(dest="command")
    resolve_cli.add_parsers(parsers)
    encoded = base64.b64encode(json.dumps(_intent()).encode()).decode()
    args = parsers.choices["resolve"].parse_args(["--json", "--launch-request-b64", encoded])
    assert resolve_cli.cmd_resolve(args) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["seed_id"] == _intent()["request_id"]
    assert "text" not in receipt


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_remote_admission_sends_text_only_in_encoded_request(shell, monkeypatch):
    monkeypatch.setattr(remote_seed_launch.cfg, "project_name", lambda: "demo")
    seen = []
    def run(argv, **kwargs):
        seen.append(argv)
        command = argv[-1]
        if shell == "pwsh":
            command = base64.b64decode(command.split()[-1]).decode("utf-16le")
            encoded = command.split("'")[-2]
        else:
            import shlex
            inner = shlex.split(command)[-1]
            encoded = shlex.split(inner)[-1]
        intent = request.decode(encoded)
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "version": 1, "request_id": intent["request_id"], "seed_id": intent["request_id"],
            "worktree_id": "wt-a", "seed_kind": "resume",
        }))
    monkeypatch.setattr(remote_seed_launch.subprocess, "run", run)
    text = _intent()["text"]
    plan = remote_seed_launch.prepare(None, "target", shell, ["--worktree-id", "wt-a"], text)
    assert text not in seen[0][-1]
    assert text not in plan["remote_command"]
    assert plan["seed_pending"] and plan["seed_kind"] == "resume"


def test_real_powershell_transport_preserves_encoded_intent(tmp_path):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")
    intent = _intent()
    encoded = base64.b64encode(json.dumps(intent, ensure_ascii=False).encode()).decode()
    script = (
        "import base64,json,sys;"
        "print(json.dumps(json.loads(base64.b64decode(sys.argv[1])),ensure_ascii=False))"
    )
    command = remote_seed_launch.shell_command("pwsh", [sys.executable, "-c", script, encoded])
    result = subprocess.run(
        [pwsh, "-NoProfile", "-EncodedCommand", command.split()[-1]],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == intent


def test_real_posix_transport_preserves_encoded_intent():
    if sys.platform == "win32":
        pytest.skip("Native POSIX shell invocation is validated in the SSH lane")
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash is unavailable")
    intent = _intent()
    encoded = base64.b64encode(json.dumps(intent, ensure_ascii=False).encode()).decode()
    script = (
        "import base64,json,sys;"
        "print(json.dumps(json.loads(base64.b64decode(sys.argv[1])),ensure_ascii=False))"
    )
    command = remote_seed_launch.shell_command("bash", [sys.executable, "-c", script, encoded])
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == intent


def test_remote_old_engine_refuses_without_interactive_fallback(monkeypatch):
    monkeypatch.setattr(remote_seed_launch.cfg, "project_name", lambda: "demo")
    commands = []
    def refused(argv, **kwargs):
        commands.append(argv)
        return SimpleNamespace(returncode=2, stdout="", stderr="unrecognized arguments: --launch-request-b64")
    monkeypatch.setattr(remote_seed_launch.subprocess, "run", refused)
    with pytest.raises(RuntimeError, match="unrecognized arguments"):
        remote_seed_launch.prepare(None, "target", "bash", ["--new"], "task")
    assert len(commands) == 1


def test_remote_error_envelope_wins_over_creation_progress(monkeypatch):
    monkeypatch.setattr(remote_seed_launch.cfg, "project_name", lambda: "demo")
    monkeypatch.setattr(remote_seed_launch.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=3, stdout=json.dumps({"error": "seed storage failed"}),
        stderr="Creating worktree...",
    ))
    with pytest.raises(RuntimeError, match="seed storage failed"):
        remote_seed_launch.prepare(None, "target", "bash", ["--new"], "task")


@pytest.mark.parametrize("shell", ["fish", "ksh", "unknown"])
def test_unsupported_shell_is_rejected_before_remote_admission(shell):
    with pytest.raises(ValueError, match="supported explicit shell"):
        remote_seed_launch.shell_command(shell, ["demo", "resolve"])


@pytest.mark.parametrize("selector", [["--new"], ["--base"], ["--worktree-id", "wt-a"],
                                      ["--codename", "one-two"]])
def test_structured_request_rejects_ordinary_selector_before_mutation(selector, capfd):
    import argparse
    from agent_worktrees import resolve_cli

    parsers = argparse.ArgumentParser().add_subparsers()
    resolve_cli.add_parsers(parsers)
    encoded = base64.b64encode(json.dumps(_intent()).encode()).decode()
    args = parsers.choices["resolve"].parse_args(
        ["--json", "--launch-request-b64", encoded, *selector],
    )
    assert resolve_cli.cmd_resolve(args) == 3
    assert "Structured launch requests require" in json.loads(capfd.readouterr().out)["error"]
