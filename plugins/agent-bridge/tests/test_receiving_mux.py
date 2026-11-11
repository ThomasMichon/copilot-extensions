"""Portable mux attempts retain native identity and never replay uncertain effects."""

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import zipfile

import pytest

from agent_bridge import receiving_mux


def _payload(entries=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in (entries or {"support.json": "{}", "driver.pyz": "fixture"}).items():
            entry = zipfile.ZipInfo()
            entry.filename = name
            archive.writestr(entry, value)
    return output.getvalue()


def _request(tmp_path, payload):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return {
        "version": 1, "attempt": str(uuid.uuid4()), "session_id": str(uuid.uuid4()),
        "cwd": str(workspace.resolve()), "state_root": str((tmp_path.parent / "mux-receiving").resolve()),
        "support_sha256": hashlib.sha256(payload).hexdigest(),
        "policy_context": json.dumps({
            "schema": "agent-bridge.receiving-policy", "version": 1, "marketplaceId": "",
        }),
        "scope_id": "provider-workspace", "copilot_args": [],
    }


@pytest.fixture
def native(monkeypatch):
    calls = []

    def run(argv, *, cwd):
        calls.append((argv, cwd))
        return subprocess.CompletedProcess(argv, 0, "$1:1234|%2\n", "")

    monkeypatch.setattr(receiving_mux, "_mux_binary", lambda: "tmux")
    monkeypatch.setattr(receiving_mux, "_run_tmux", run)
    monkeypatch.setattr(receiving_mux, "_socket_identity", lambda path: {"selected": "socket"})
    monkeypatch.setattr(receiving_mux, "_policy", lambda request, support: None)
    monkeypatch.setattr(receiving_mux, "_require_core", lambda support: None)
    return calls


def test_creation_and_reuse_observe_the_same_native_incarnation(tmp_path, native, monkeypatch):
    payload = _payload()
    request = _request(tmp_path, payload)
    created = receiving_mux.receive(request, payload)
    assert created["ok"] and not created["reused"]
    assert created["native"] == {"incarnation": "$1:1234", "pane": "%2"}
    attempt = Path(request["state_root"]) / request["attempt"]
    (attempt / "support" / "driver.pyz").unlink()
    monkeypatch.setattr(receiving_mux, "_require_core", lambda *a: pytest.fail("retained reuse needs no new core"))
    reused = receiving_mux.receive(request, payload)
    assert reused["ok"] and reused["reused"]
    assert [call[0][3] for call in native] == ["new-session", "display-message"]
    assert native[0][0][:3] == ["tmux", "-S", created["mux_socket"]]
    assert native[1][0][-2] == created["native"]["pane"]
    assert "send-keys" not in native[0][0]
    assert json.loads((Path(request["state_root"]) / request["attempt"] / "receipt.json").read_text())["phase"] == "created"


def test_registration_is_private_to_the_original_attempt_and_never_on_argv(tmp_path, native, monkeypatch):
    payload = _payload()
    request = {
        **_request(tmp_path, payload),
        "registration": {"token": "synthetic-registration-value", "port": 12345},
        "driven_by": "fixture orchestrator",
    }
    receipt = receiving_mux.receive(request, payload)
    attempt = Path(request["state_root"]) / request["attempt"]
    assert (attempt / "bridge" / "auth.yaml").read_text() == "token: synthetic-registration-value\n"
    assert json.loads((attempt / "bridge" / "active.json").read_text())["active"]["port"] == 12345
    assert "synthetic-registration-value" not in json.dumps(native)
    assert "registration" not in receipt
    monkeypatch.chdir(request["cwd"])
    executed = []
    monkeypatch.setattr(receiving_mux.os, "execvpe", lambda *args: executed.append(args))
    receiving_mux.child(attempt)
    assert executed[0][2]["AGENT_BRIDGE_CONFIG_DIR"] == str(attempt / "bridge")
    assert executed[0][2]["AGENT_BRIDGE_DRIVEN_BY"] == "fixture orchestrator"
    assert executed[0][2]["AGENT_BRIDGE_DRIVER_OWNER"] == "bridge-mux-" + request["attempt"]
    assert "synthetic-registration-value" not in json.dumps(executed[0][1])
    assert receiving_mux.receive(request, payload)["reused"] is True
    with pytest.raises(ValueError, match="different launch inputs"):
        receiving_mux.receive({**request, "registration": {**request["registration"], "port": 12346}}, payload)
    assert len(native) == 2  # create and exact reuse, never replacement


def test_unknown_creation_is_retained_and_never_replayed(tmp_path, native, monkeypatch):
    payload = _payload()
    request = _request(tmp_path, payload)

    def unknown(*args, **kwargs):
        native.append(args)
        raise subprocess.TimeoutExpired("tmux", 30)

    monkeypatch.setattr(receiving_mux, "_run_tmux", unknown)
    first = receiving_mux.receive(request, payload)
    second = receiving_mux.receive(request, payload)
    assert first["pending"] and second["pending"]
    assert first["phase"] == second["phase"] == "unknown"
    assert len(native) == 1


def test_retry_cannot_change_inputs_or_adopt_a_replacement(tmp_path, native, monkeypatch):
    payload = _payload()
    request = _request(tmp_path, payload)
    receiving_mux.receive(request, payload)
    with pytest.raises(ValueError, match="different launch inputs"):
        receiving_mux.receive({**request, "scope_id": "different"}, payload)
    monkeypatch.setattr(receiving_mux, "_run_tmux", lambda *a, **k: subprocess.CompletedProcess(a, 0, "$2:1235|%3", ""))
    with pytest.raises(ValueError, match="will not be replayed"):
        receiving_mux.receive(request, payload)
    assert len(native) == 1


def test_retry_refuses_a_replaced_socket_even_with_the_same_mux_name(tmp_path, native, monkeypatch):
    payload = _payload()
    request = _request(tmp_path, payload)
    receiving_mux.receive(request, payload)
    monkeypatch.setattr(receiving_mux, "_socket_identity", lambda path: {"different": "socket"})
    with pytest.raises(ValueError, match="socket was replaced"):
        receiving_mux.receive(request, payload)
    assert len(native) == 1


def test_policy_is_fresh_before_creation_and_before_reuse(tmp_path, native, monkeypatch):
    payload = _payload()
    request = _request(tmp_path, payload)
    checked = []
    monkeypatch.setattr(receiving_mux, "_policy", lambda req, support: checked.append(req["cwd"]))
    receiving_mux.receive(request, payload)

    def deny(*args):
        raise ValueError("repository now denies hosting")

    monkeypatch.setattr(receiving_mux, "_policy", deny)
    with pytest.raises(ValueError, match="now denies"):
        receiving_mux.receive(request, payload)
    assert checked == [request["cwd"]] * 3
    assert len(native) == 1


def test_prerequisite_failure_resumes_only_the_same_precreation_attempt(tmp_path, native, monkeypatch):
    payload = _payload()
    request = {**_request(tmp_path, payload), "ensure_mux": True}
    calls = []

    def prepare(req, support):
        calls.append(req["attempt"])
        if len(calls) == 1:
            raise ValueError("fixture prerequisite unavailable")

    monkeypatch.setattr(receiving_mux, "_prepare_mux", prepare)
    with pytest.raises(ValueError, match="prerequisite"):
        receiving_mux.receive(request, payload)
    receipt = Path(request["state_root"]) / request["attempt"] / "receipt.json"
    assert json.loads(receipt.read_text())["phase"] == "ready"
    assert native == []
    assert receiving_mux.receive(request, payload)["ok"] is True
    assert calls == [request["attempt"]] * 2
    assert len(native) == 1


def test_concurrent_ready_attempt_cannot_create_another_native_session(tmp_path, native, monkeypatch):
    payload = _payload()
    request = {**_request(tmp_path, payload), "ensure_mux": True}
    overlapping = []

    def prepare(req, support):
        overlapping.append(receiving_mux.receive(request, payload))
        assert native == []

    monkeypatch.setattr(receiving_mux, "_prepare_mux", prepare)
    assert receiving_mux.receive(request, payload)["ok"] is True
    assert len(overlapping) == 1 and overlapping[0]["pending"] is True
    assert len(native) == 1


def test_setup_cannot_launch_after_repository_policy_changes(tmp_path, native, monkeypatch):
    payload = _payload()
    request = {**_request(tmp_path, payload), "ensure_mux": True}
    denied = [False]

    def policy(*args):
        if denied[0]:
            raise ValueError("late denial")

    monkeypatch.setattr(receiving_mux, "_policy", policy)
    monkeypatch.setattr(receiving_mux, "_prepare_mux", lambda *args: denied.__setitem__(0, True))
    with pytest.raises(ValueError, match="late denial"):
        receiving_mux.receive(request, payload)
    assert native == []


def test_packaged_setup_uses_the_canonical_noninteractive_owner_without_guest_imports(
    tmp_path, monkeypatch,
):
    from agent_bridge.receiving_bundle import build_mux_support

    payload, _ = build_mux_support()
    support = tmp_path / "support"
    support.mkdir()
    receiving_mux._unpack(payload, support)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["timeout"] == 180
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False
        return subprocess.CompletedProcess(argv, 0)

    def which(binary):
        if binary == "tmux":
            return "/bin/tmux" if calls else None
        return "/bin/" + binary if binary in {"sudo", "apt-get"} else None

    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr("shutil.which", which)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setitem(sys.modules, "agent_worktrees", None)
    receiving_mux._prepare_mux({"ensure_mux": True}, support)
    assert calls == [["/bin/sudo", "-n", "apt-get", "install", "-y", "tmux"]]


def test_denial_creates_no_native_session_and_retries_do_not_launch(tmp_path, native, monkeypatch):
    payload = _payload()
    request = _request(tmp_path, payload)
    monkeypatch.setattr(receiving_mux, "_policy", lambda *args: (_ for _ in ()).throw(ValueError("denied")))
    with pytest.raises(ValueError, match="denied"):
        receiving_mux.receive(request, payload)
    monkeypatch.setattr(receiving_mux, "_policy", lambda *args: None)
    result = receiving_mux.receive(request, payload)
    assert result["pending"] and not result["ok"]
    assert native == []


@pytest.mark.parametrize("name", ["../outside", "/absolute", "a\\b", "a//b"])
def test_archive_refuses_unsafe_paths_before_native_creation(tmp_path, native, name):
    payload = _payload({name: "bad"})
    with pytest.raises(ValueError, match="Unsafe"):
        receiving_mux.receive(_request(tmp_path, payload), payload)
    assert native == []


@pytest.mark.parametrize("args", [
    ["--session-id=other"], ["--resume", "other"], ["-i", "seed"], ["--plugin-dir", "/old"],
    ["--acp"], ["-C", "/different"],
    ["--", "seed"], ["-pseed"], ["implicit-seed"],
])
def test_caller_cannot_replace_the_native_identity_mode_or_seed(tmp_path, args):
    request = _request(tmp_path, _payload())
    with pytest.raises(ValueError, match="cannot replace"):
        receiving_mux._request({**request, "copilot_args": args})


def test_bad_archive_identity_has_no_state_effect(tmp_path, native):
    payload = _payload()
    request = _request(tmp_path, payload)
    with pytest.raises(ValueError, match="differs"):
        receiving_mux.receive(request, payload + b"changed")
    assert not (Path(request["state_root"]) / request["attempt"]).exists()
    assert native == []


def test_actual_packaged_child_enforces_changed_policy_without_a_guest_suite(tmp_path):
    from agent_procutil import no_window_flags
    from agent_bridge.receiving_bundle import build_mux_support

    payload, _identity = build_mux_support()
    request = _request(tmp_path, payload)
    attempt = tmp_path / "attempt"
    support = attempt / "support"
    support.mkdir(parents=True)
    receiving_mux._unpack(payload, support)
    (attempt / "launch.json").write_text(json.dumps(request), encoding="utf-8")
    policy = Path(request["cwd"]) / ".agent-worktrees" / "config.yaml"
    policy.parent.mkdir()
    policy.write_text("expose_agent: false\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(support / "receiver.py"), "child", str(attempt)],
        cwd=request["cwd"], capture_output=True, text=True, timeout=15,
        env={"PATH": str(tmp_path / "no-executables"), **{
            key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ
        }},
        creationflags=no_window_flags(),
    )
    assert result.returncode != 0
    assert "AgentHostingDenied" in result.stderr
    assert "expose_agent: false" in result.stderr


def test_child_rechecks_policy_then_preserves_literal_args_and_scoped_driver(tmp_path, monkeypatch):
    payload = _payload()
    request = _request(tmp_path, payload)
    request["copilot_args"] = ["--model", "literal model with spaces"]
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    (attempt / "support").mkdir()
    (attempt / "support" / "driver.pyz").write_text("fixture", encoding="utf-8")
    (attempt / "launch.json").write_text(json.dumps(request), encoding="utf-8")
    monkeypatch.chdir(request["cwd"])
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "unrelated-parent")
    monkeypatch.setenv("WORKTREE_LAUNCH_ID", "unrelated-launch")
    calls = []

    def execute(binary, argv, env):
        calls.append(("execute", binary, argv, env))

    monkeypatch.setattr(receiving_mux, "_policy", lambda *args: calls.append(("policy",)))
    monkeypatch.setattr(receiving_mux.os, "execvpe", execute)
    receiving_mux.child(attempt)
    assert [call[0] for call in calls] == ["policy", "execute"]
    _kind, _binary, argv, env = calls[1]
    assert argv[4:8] == receiving_mux._core_executable(attempt / "support")
    core, child = argv[:argv.index("--")], argv[argv.index("--") + 1:]
    assert core[core.index("--mode") + 1] == "headed"
    assert core[core.index("--lifetime-policy") + 1] == "cli"
    assert core[core.index("--session-id") + 1] == request["session_id"]
    assert core[core.index("--attempt-directory") + 1] == str(attempt / "driver-core")
    assert core[core.index("--state-file") + 1] == str(attempt / "driver-core" / "host.json")
    assert core[core.index("--cwd") + 1] == request["cwd"]
    assert core[core.index("--repository-policy-context") + 1] == request["policy_context"]
    assert argv[-2:] == request["copilot_args"]
    assert child[:3] == ["copilot", "--session-id", request["session_id"]]
    assert env["AGENT_BRIDGE_NATIVE_SESSION_ID"] == request["session_id"]
    assert env["AGENT_REMOTE_DRIVER_CONFIG_DIR"] == str(attempt / "driver")
    assert "COPILOT_AGENT_SESSION_ID" not in env
    assert "WORKTREE_LAUNCH_ID" not in env


@pytest.mark.parametrize("reason", ["missing", "unsupported"])
def test_unavailable_core_never_creates_mux_or_enters_a_legacy_launch(tmp_path, native, monkeypatch, reason):
    payload = _payload()
    request = _request(tmp_path, payload)

    def refuse(support):
        raise ValueError(reason)

    monkeypatch.setattr(receiving_mux, "_require_core", refuse)
    monkeypatch.setattr(receiving_mux, "_prepare_mux", lambda *a: pytest.fail("no prerequisite effects"))
    monkeypatch.setattr(receiving_mux, "_registration", lambda *a: pytest.fail("no registration effects"))
    monkeypatch.setattr(receiving_mux.os, "execvpe", lambda *a: pytest.fail("no legacy native launch"))
    with pytest.raises(ValueError, match=reason):
        receiving_mux.receive(request, payload)
    assert native == []
    attempt = Path(request["state_root"]) / request["attempt"]
    assert json.loads((attempt / "receipt.json").read_text())["phase"] == "ready"


@pytest.mark.parametrize("changed", [
    {"contract": "legacy"}, {"version": True}, {"protocol_version": True},
    {"protocol_version": 2}, {"modes": ["headless"]}, {"modes": "headed"},
    {"transports": []}, {"lifetime_policies": ["acp"]}, {"headed_process_receipts": []},
])
def test_core_declaration_is_checked_at_the_actual_receiving_executable(tmp_path, monkeypatch, changed):
    support = tmp_path / "support"
    support.mkdir()
    (support / "driver.pyz").write_text("fixture", encoding="utf-8")
    declaration = {
        "contract": "agent-remote-driver.session-host", "version": 1, "protocol_version": 1,
        "modes": ["headless", "headed"], "transports": ["tcp-frame"],
        "lifetime_policies": ["acp", "cli"], "headed_process_receipts": ["native-child-v1"],
    }
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["timeout"] == 15
        return subprocess.CompletedProcess(argv, 0, json.dumps(declaration), "")

    monkeypatch.setattr(receiving_mux.subprocess, "run", run)
    receiving_mux._require_core(support)
    declaration.update(changed)
    with pytest.raises(ValueError, match="does not support"):
        receiving_mux._require_core(support)
    assert calls == [[*receiving_mux._core_executable(support), "--support-json"]] * 2


def test_packaged_core_is_selected_after_copy_without_a_guest_suite(tmp_path, monkeypatch):
    from agent_bridge.receiving_bundle import build_mux_support
    from agent_remote_driver.session_host import bundle

    original = bundle.build_session_host_bundle
    construction = tmp_path / "construction"
    monkeypatch.setattr(bundle, "build_session_host_bundle", lambda: original(construction))
    payload, _ = build_mux_support()
    support = tmp_path / "receiving"
    support.mkdir()
    receiving_mux._unpack(payload, support)
    for path in construction.iterdir():
        path.unlink()
    construction.rmdir()
    receiving_mux._require_core(support)
