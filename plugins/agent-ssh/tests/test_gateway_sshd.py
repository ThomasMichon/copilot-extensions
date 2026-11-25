import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

from fleet_manager.ssh_directory import DirectoryUpdate
from dropin_registry import atomic_write_text
import pytest

from agent_ssh import gateway_authorization as authorization
from agent_ssh import gateway_provider
from agent_ssh import gateway_sshd as ingress
from agent_ssh import _native_listener as listener
from test_gateway_provider import fixture_files as _fixture_files, key as _key

Document = dict[str, Any]
fixture_files = _fixture_files
key = _key


def config(root: Path) -> Document:
    return {
        "schema": "agent-ssh.gateway-native-ingress", "version": 1,
        "policy": root / "policy.json", "directory": root / "directory.json",
        "machine": "worker-a", "environment": "linux",
        "public_key_file": root / "identity.pub", "host_key_file": root / "host",
        "state_dir": root / "ingress", "sshd": root / "sshd",
        "ssh_keygen": root / "ssh-keygen", "port": 24107,
    }


def admitted_update(policy_path: Path, directory: Document) -> DirectoryUpdate:
    from fleet_manager.planning import canonical

    return DirectoryUpdate.parse(canonical({
        "schema": "agent-ssh.service-directory", "version": 1,
        "policy": json.loads(policy_path.read_text(encoding="utf-8")),
        "directory": directory,
    }))


@pytest.fixture
def native_preparation(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name == "nt":
        authorization._windows_sid.cache_clear()
    monkeypatch.setattr(ingress, "_account", lambda: "owner")
    monkeypatch.setattr(ingress, "_principal", lambda account: account)
    monkeypatch.setattr(ingress, "_executable", Mock())
    monkeypatch.setattr(ingress, "_host_key", Mock())
    monkeypatch.setattr(ingress, "subprocess", SimpleNamespace(
        run=Mock(return_value=Mock(returncode=0)), DEVNULL=subprocess.DEVNULL,
    ))


@pytest.mark.parametrize("matches", [True, False])
def test_notification_policy_is_checked_before_public_identity_or_native_effects(
    monkeypatch: pytest.MonkeyPatch, matches: bool,
) -> None:
    local = Mock(policy_digest="selected-local-policy")
    remote = Mock(policy_digest="selected-local-policy" if matches else "another-policy")
    policy_type = Mock()
    policy_type.from_dict.side_effect = (local, remote)
    owner = authorization
    monkeypatch.setattr(gateway_provider, "_sdk", lambda: (policy_type, None, lambda value: value))
    read = Mock(return_value={"local": True})
    monkeypatch.setattr(gateway_provider, "_read", read)
    project = Mock(return_value={"selected": True})
    monkeypatch.setattr(owner, "_projection", project)
    update = Mock(spec=DirectoryUpdate)
    update.to_dict.return_value = {"policy": {"remote": True}, "directory": {"revision": 7}}
    scope: Document = {
        "policy_path": Path("selected-policy"), "machine": "worker-a",
        "environment": "linux", "public_key_file": Path("selected-public-key"),
    }
    if matches:
        assert owner.projection_from_update(update, **scope) == {"selected": True}
        project.assert_called_once_with(
            local, {"revision": 7}, machine="worker-a",
            environment="linux", public_key_file=scope["public_key_file"],
        )
    else:
        with pytest.raises(PermissionError, match="selected local policy"):
            owner.projection_from_update(update, **scope)
        project.assert_not_called()
    read.assert_called_once_with(scope["policy_path"])


def test_native_inbound_update_catches_up_without_profiles_or_directory_staging(
    tmp_path: Path, native_preparation: None,
) -> None:
    _, directory = fixture_files(tmp_path, outbound=False)
    selected = config(tmp_path)
    staged = selected["directory"].read_bytes()
    peer = directory["machines"].pop()
    update = admitted_update(selected["policy"], directory)
    native = ingress.prepare_update(selected, update)
    authorized = selected["state_dir"] / "authorization" / "authorized_keys.gateway"
    assert key(2) not in authorized.read_text(encoding="utf-8")
    native_before = native.read_bytes(), native.stat().st_mtime_ns
    directory["machines"].append(peer)
    directory["revision"] += 1
    update = admitted_update(selected["policy"], directory)
    assert ingress.prepare_update(selected, update) == native
    assert "restrict,pty " + key(2) in authorized.read_text(encoding="utf-8")
    keys_before = authorized.read_bytes(), authorized.stat().st_mtime_ns
    ingress.prepare_update(selected, update)
    assert (authorized.read_bytes(), authorized.stat().st_mtime_ns) == keys_before
    assert (native.read_bytes(), native.stat().st_mtime_ns) == native_before
    directory["machines"].pop()
    directory["revision"] += 1
    ingress.prepare_update(selected, admitted_update(selected["policy"], directory))
    assert key(2) not in authorized.read_text(encoding="utf-8")
    assert selected["directory"].read_bytes() == staged
    assert not (tmp_path / "ssh").exists()
    assert not (tmp_path / ".agent-ssh-sources").exists()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows reader/atomic-replacement sharing")
def test_native_inbound_update_survives_an_observer_read_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, directory = fixture_files(tmp_path, outbound=False)
    peer = directory["machines"].pop()
    scope: Document = {
        "policy_path": tmp_path / "policy.json", "machine": "worker-a",
        "environment": "linux", "public_key_file": tmp_path / "identity.pub",
    }
    initial = authorization.projection_from_update(
        admitted_update(tmp_path / "policy.json", directory), **scope,
    )
    state = tmp_path / "authorization"
    target, _ = authorization.apply_projection(initial, state)
    before = target.read_bytes()
    directory["machines"].append(peer)
    directory["revision"] += 1
    desired = authorization.projection_from_update(
        admitted_update(tmp_path / "policy.json", directory), **scope,
    )
    contended, release = threading.Event(), threading.Event()
    observed: list[PermissionError] = []
    failures: list[BaseException] = []
    outcomes: list[tuple[Path, bool]] = []

    def observe_write(
        path: str | os.PathLike[str], content: str, *, encoding: str = "utf-8",
    ) -> Path:
        try:
            return atomic_write_text(path, content, encoding=encoding)
        except PermissionError as error:
            if Path(path) == target:
                observed.append(error)
                contended.set()
                assert release.wait(5), "Fixture reader did not release its owned handle"
            raise

    def publish() -> None:
        try:
            outcomes.append(authorization.apply_projection(desired, state))
        except BaseException as error:
            failures.append(error)

    monkeypatch.setattr(authorization, "atomic_write_text", observe_write)
    writer = threading.Thread(target=publish)
    try:
        with target.open("rb") as reader:
            writer.start()
            assert contended.wait(5), "Fixture did not reach actual native replacement contention"
            assert getattr(observed[0], "winerror", None) in (5, 32)
            assert reader.read() == before
    finally:
        release.set()
        if writer.ident is not None:
            writer.join(5)
    assert not writer.is_alive(), "Fixture publication did not settle"
    assert not failures, failures
    assert outcomes == [(target, True)]
    assert key(2) in target.read_text(encoding="utf-8")


@pytest.mark.parametrize("condition", ["access", "sharing", "other-error", "staging", "other-file", "posix"])
def test_authorization_retry_is_limited_to_native_selected_file_replacement(
    monkeypatch: pytest.MonkeyPatch, condition: str,
) -> None:
    root = Path("selected-authorization")
    target = root / "authorized_keys.gateway"
    destination = None if condition == "staging" else str(target)
    if condition == "other-file":
        destination = str(root / "another-owner")
    error = PermissionError(13, "fixture contention", "staging", None, destination)
    setattr(error, "winerror", 32 if condition == "sharing" else 13 if condition == "other-error" else 5)
    publish = Mock(side_effect=(error, target))
    clock = SimpleNamespace(monotonic=Mock(return_value=0.0), sleep=Mock())
    monkeypatch.setattr(authorization, "sys", SimpleNamespace(platform="linux" if condition == "posix" else "win32"))
    monkeypatch.setattr(authorization, "time", clock)
    monkeypatch.setattr(authorization, "_authorization_text", Mock(return_value="retained"))
    monkeypatch.setattr(authorization, "atomic_write_text", publish)
    if condition in ("access", "sharing"):
        authorization._publish_authorization(root, {}, "retained", "replacement")
        assert publish.call_count == 2 and clock.sleep.call_count == 1
    else:
        with pytest.raises(PermissionError) as refused:
            authorization._publish_authorization(root, {}, "retained", "replacement")
        assert refused.value is error and publish.call_count == 1
        clock.sleep.assert_not_called()


@pytest.mark.parametrize("condition", ["changed", "deadline"])
def test_authorization_retry_preserves_changed_state_and_its_finite_deadline(
    monkeypatch: pytest.MonkeyPatch, condition: str,
) -> None:
    root = Path("selected-authorization")
    error = PermissionError(13, "fixture contention", "staging", None, str(root / "authorized_keys.gateway"))
    setattr(error, "winerror", 5)
    publish = Mock(side_effect=error)
    read = Mock(side_effect=("retained", "changed")) if condition == "changed" else Mock(return_value="retained")
    clock = SimpleNamespace(monotonic=Mock(side_effect=(0.0, 2.0)), sleep=Mock())
    monkeypatch.setattr(authorization, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(authorization, "time", clock)
    monkeypatch.setattr(authorization, "_authorization_text", read)
    monkeypatch.setattr(authorization, "atomic_write_text", publish)
    with pytest.raises(ValueError if condition == "changed" else PermissionError):
        authorization._publish_authorization(root, {}, "retained", "replacement")
    assert publish.call_count == (1 if condition == "changed" else 2)
    assert clock.sleep.call_count == 1


@pytest.mark.parametrize("change", ["untyped", "policy", "local-key", "rollback", "equal-changed"])
def test_native_inbound_update_refuses_unselected_or_stale_inputs(
    tmp_path: Path, native_preparation: None, change: str,
) -> None:
    _, directory = fixture_files(tmp_path)
    selected = config(tmp_path)
    directory["revision"] = 2
    ingress.prepare_update(selected, admitted_update(selected["policy"], directory))
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in selected["state_dir"].rglob("*") if path.is_file()
    }
    candidate = admitted_update(selected["policy"], directory)
    update: object = candidate
    if change == "untyped":
        update = candidate.to_dict()
    elif change == "policy":
        policy = json.loads(selected["policy"].read_text(encoding="utf-8"))
        policy["revision"] = "changed-local-policy"
        selected["policy"].write_text(json.dumps(policy), encoding="utf-8")
    elif change == "local-key":
        selected["public_key_file"].write_text(key(9), encoding="ascii")
    else:
        if change == "rollback":
            directory["revision"] = 1
        else:
            directory["machines"].pop()
        update = admitted_update(selected["policy"], directory)
    with pytest.raises((ValueError, PermissionError)):
        ingress.prepare_update(selected, update)
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == value for path, value in before.items())
    assert not (tmp_path / "ssh").exists()


def test_wrong_login_user_refuses_before_host_key_or_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(authorization, "projection", Mock(return_value={"user": "owner"}))
    monkeypatch.setattr(ingress, "_account", lambda: "different-owner")
    derive = Mock()
    monkeypatch.setattr(ingress, "_host_key", derive)
    with pytest.raises(PermissionError, match="login user"):
        ingress.prepare(config(tmp_path))
    derive.assert_not_called()
    assert not (tmp_path / "ingress").exists()


@pytest.mark.parametrize("value", ["relative/path", "C:\\bad\npath", "/bad%h", '/bad"path'])
def test_native_paths_are_literal_and_absolute(value: str) -> None:
    with pytest.raises(ValueError):
        ingress._absolute(value)


def test_config_is_standalone_and_never_enables_forwarding(tmp_path: Path) -> None:
    text = ingress._native_config(config(tmp_path), "owner", tmp_path / "authorized")
    assert "AllowUsers owner\n" in text
    assert "DisableForwarding yes\n" in text
    assert "StrictModes yes\n" in text
    assert "PasswordAuthentication no\n" in text
    assert "KbdInteractiveAuthentication no\n" in text
    assert "PermitUserRC no\n" in text
    assert "Port 24107\n" in text and "ListenAddress 127.0.0.1\n" in text
    assert "Include " not in text


def test_native_entrypoint_preserves_protocol_stdout_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(ingress, "load", Mock(side_effect=ValueError("fixture invalid")))
    assert ingress.main(["--config", str(tmp_path / "selected.json")]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "[FAIL]" in captured.err


def test_host_pin_is_compared_with_the_private_keys_actual_public_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = config(tmp_path)
    monkeypatch.setattr(authorization, "_private", Mock())
    monkeypatch.setattr(ingress, "_executable", Mock())
    run = Mock(return_value=Mock(returncode=0, stdout=(key(3) + " public-comment\n").encode()))
    monkeypatch.setattr(ingress, "subprocess", SimpleNamespace(run=run, DEVNULL=subprocess.DEVNULL))
    with pytest.raises(ValueError, match="admitted target pin"):
        ingress._host_key(selected, key(4))
    ingress._host_key(selected, key(3))
    assert run.call_args.kwargs["stdin"] == subprocess.DEVNULL
    assert run.call_args.kwargs["timeout"] == 10


def test_configuration_fields_are_closed_and_environment_is_native(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = config(tmp_path)
    selected = {name: str(value) if isinstance(value, Path) else value for name, value in selected.items()}
    path = tmp_path / "config.json"
    monkeypatch.setattr(authorization, "_private", Mock())
    monkeypatch.setattr(ingress, "_environment", lambda: "linux")
    path.write_text(json.dumps(selected), encoding="utf-8")
    assert ingress.load(path)["state_dir"] == tmp_path / "ingress"
    selected["extra"] = True
    path.write_text(json.dumps(selected), encoding="utf-8")
    with pytest.raises(ValueError, match="configuration"):
        ingress.load(path)
    selected.pop("extra")
    selected["environment"] = "windows"
    path.write_text(json.dumps(selected), encoding="utf-8")
    with pytest.raises(ValueError, match="environment"):
        ingress.load(path)


@pytest.mark.skipif(os.name != "nt", reason="Windows OpenSSH descriptor inheritance")
def test_new_native_root_removes_only_ancestor_descriptor_state(monkeypatch: pytest.MonkeyPatch) -> None:
    carrier = "C28FC6F98A2C44ABBBD89D6A3037D0D9_POSIX_FD_STATE"
    monkeypatch.setenv(carrier, "synthetic-ancestor-state")
    monkeypatch.setenv(carrier + "_OTHER", "preserve")
    monkeypatch.setenv("SSH_AUTH_SOCK", "preserve-agent")
    result = ingress._process_environment()
    assert not any(name.casefold() == carrier.casefold() for name in result)
    assert result[carrier + "_OTHER"] == "preserve"
    assert result["SSH_AUTH_SOCK"] == "preserve-agent"
    assert os.environ[carrier] == "synthetic-ancestor-state"


def test_native_authentication_probe_never_requests_a_command_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import native_ssh_auth

    child = Mock()
    child.poll.return_value = None
    child.wait.return_value = 1

    def start(argv: list[str], **kwargs: Any) -> Mock:
        kwargs["stderr"].write(b'Authenticated to fixture using "publickey".\n')
        kwargs["stderr"].flush()
        return child

    spawn = Mock(side_effect=start)
    monkeypatch.setattr(native_ssh_auth, "subprocess", SimpleNamespace(Popen=spawn, DEVNULL=subprocess.DEVNULL))
    result = native_ssh_auth.authenticate(["ssh", "-F", "empty-config"], tmp_path)
    assert spawn.call_args.args[0] == [
        "ssh", "-F", "empty-config", "-v", "-N", "-T", "127.0.0.1",
    ]
    assert result.authenticated and result.native_exit == 1
    child.terminate.assert_called_once()


@pytest.mark.parametrize(
    "occupancy", ["owned", "wildcard", "duplicate", "same-owner-duplicate", "other-port", "truncated"],
)
def test_native_listener_table_requires_one_exact_loopback_owner(occupancy: str) -> None:
    rows = [(2, 0x0100007F, socket.htons(24107), 0, 0, 71)]
    if occupancy == "wildcard":
        rows.append((2, 0, socket.htons(24107), 0, 0, 72))
    elif occupancy == "duplicate":
        rows.append((2, 0x0100007F, socket.htons(24107), 0, 0, 72))
    elif occupancy == "same-owner-duplicate":
        rows.append(rows[0])
    elif occupancy == "other-port":
        rows[0] = (2, 0x0100007F, socket.htons(24108), 0, 0, 71)
    raw = struct.pack("<I", len(rows)) + b"".join(struct.pack("<6I", *row) for row in rows)
    if occupancy == "truncated":
        with pytest.raises(OSError, match="incomplete"):
            listener._table_pid(raw[:-1], 24107)
    else:
        assert listener._table_pid(raw, 24107) == (71 if occupancy == "owned" else None)


@pytest.mark.parametrize("condition", ["ready", "foreign", "changed", "dead", "unavailable", "no-banner"])
def test_foreground_readiness_never_adopts_another_listener(
    monkeypatch: pytest.MonkeyPatch, condition: str,
) -> None:
    process, job = Mock(pid=71), Mock()
    process.poll.return_value = 1 if condition == "dead" else None
    observe = Mock(side_effect=(71, 72) if condition == "changed" else None, return_value=71)
    if condition == "foreign":
        observe.return_value = 72
    if condition == "unavailable":
        observe.side_effect = OSError("no attribution")
    banner = Mock(return_value=condition != "no-banner")
    monkeypatch.setattr(listener, "listener_pid", observe)
    monkeypatch.setattr(listener, "ssh_banner", banner)
    owner = ingress.ForegroundIngress(process, job, 24107)
    assert owner.ready() is (condition == "ready")
    if condition in ("foreign", "dead", "unavailable"):
        banner.assert_not_called()
    job.terminate_and_wait.assert_not_called()
    owner.close()
    owner.close()
    job.terminate_and_wait.assert_called_once_with(5.0)
    assert not owner.ready()


def test_foreground_cleanup_failure_retains_the_original_custody() -> None:
    process, job = Mock(pid=71), Mock()
    job.terminate_and_wait.side_effect = (OSError("unconfirmed"), None)
    owner = ingress.ForegroundIngress(process, job, 24107)
    with pytest.raises(OSError, match="unconfirmed"):
        owner.close()
    process.wait.assert_not_called()
    owner.close()
    assert job.terminate_and_wait.call_count == 2
    process.wait.assert_called_once_with(timeout=5.0)


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("sshd", 5), KeyboardInterrupt()])
def test_foreground_cleanup_resumes_child_wait_after_the_job_is_settled(
    failure: BaseException,
) -> None:
    process, job = Mock(pid=71), Mock()
    process.wait.side_effect = (failure, 0)
    job.terminate_and_wait.side_effect = (None, RuntimeError("Job handle is already closed"))
    owner = ingress.ForegroundIngress(process, job, 24107)
    with pytest.raises(type(failure)):
        owner.close()
    assert not owner.ready()
    owner.close()
    owner.close()
    job.terminate_and_wait.assert_called_once_with(5.0)
    assert process.wait.call_count == 2


def test_fragmented_banner_uses_one_total_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    connection.recv.side_effect = [b"S", b"S", b"H"]
    monkeypatch.setattr(listener, "socket", SimpleNamespace(
        create_connection=Mock(return_value=connection),
    ))
    monkeypatch.setattr(listener, "time", SimpleNamespace(
        monotonic=Mock(side_effect=[0.0, 0.1, 0.6, 1.1]),
    ))
    assert not listener.ssh_banner(24107)
    assert connection.recv.call_count == 2
    assert connection.settimeout.call_args_list[0].args[0] == pytest.approx(0.9)
    assert connection.settimeout.call_args_list[1].args[0] == pytest.approx(0.4)


@pytest.mark.parametrize("reply", [b"SSH-2.0-\r\n", b"SSH-2.0-" + b"x" * 248, b"HTTP/1.1 200 OK\r\n"])
def test_banner_requires_a_complete_bounded_ssh_identification(
    monkeypatch: pytest.MonkeyPatch, reply: bytes,
) -> None:
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    connection.recv.return_value = reply
    monkeypatch.setattr(listener, "socket", SimpleNamespace(
        create_connection=Mock(return_value=connection),
    ))
    assert not listener.ssh_banner(24107)


def test_foreground_start_refuses_an_unsupported_platform_before_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ingress, "sys", SimpleNamespace(platform="linux"))
    prepare = Mock()
    monkeypatch.setattr(ingress, "prepare", prepare)
    with pytest.raises(OSError, match="currently requires Windows"):
        ingress.start({})
    prepare.assert_not_called()


def test_windows_entrypoint_keeps_the_foreground_owner_until_child_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected, owner = config(tmp_path), Mock()
    owner.process.wait.return_value = 17
    start = Mock(return_value=owner)
    monkeypatch.setattr(ingress, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(ingress, "load", Mock(return_value=selected))
    monkeypatch.setattr(ingress, "start", start)
    assert ingress.main(["--config", str(tmp_path / "selected.json")]) == 17
    start.assert_called_once_with(selected)
    owner.close.assert_called_once_with()


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows Job and TCP ownership")
def test_foreground_native_listener_custody_two_cycles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil import direct_python, spawn_sync_in_kill_on_close_job

    console = tmp_path / "console.txt"
    code = (
        "import ctypes,socket,sys\n"
        "from pathlib import Path\n"
        "Path(sys.argv[2]).write_text(str(ctypes.windll.kernel32.GetConsoleWindow()))\n"
        "s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)\n"
        "s.bind(('127.0.0.1',int(sys.argv[1])));s.listen()\n"
        "while True:\n"
        " c,_=s.accept()\n"
        " try:c.sendall(b'SSH-2.0-owned-fixture\\r\\n')\n"
        " finally:c.close()\n"
    )
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    selected = config(tmp_path)
    selected["port"] = port
    monkeypatch.setattr(ingress, "prepare", lambda value: tmp_path / "sshd_config")
    calls: list[tuple[list[str], Document]] = []
    python, environment = direct_python(sys.executable)

    def spawn(argv: list[str], **kwargs: Any) -> Any:
        calls.append((argv, kwargs))
        kwargs["env"] = {**kwargs["env"], **environment}
        return spawn_sync_in_kill_on_close_job(
            [python, "-c", code, str(port), str(console)], **kwargs,
        )

    monkeypatch.setattr(ingress, "spawn_sync_in_kill_on_close_job", spawn)
    selected["state_dir"] = tmp_path
    for _ in range(2):
        owner = ingress.start(selected)
        try:
            deadline = time.monotonic() + 10
            while not owner.ready() and time.monotonic() < deadline:
                assert owner.process.poll() is None
                time.sleep(0.025)
            assert owner.ready(), (owner.process.pid, listener.listener_pid(port))
            assert listener.listener_pid(port) == owner.process.pid
            assert console.read_text() == "0"
            foreign_process = Mock(pid=owner.process.pid + 1)
            foreign_process.poll.return_value = None
            foreign = ingress.ForegroundIngress(foreign_process, Mock(), port)
            assert not foreign.ready()
        finally:
            owner.close()
        assert owner.process.poll() is not None
        assert listener.listener_pid(port) is None
    assert len(calls) == 2
    for argv, kwargs in calls:
        assert argv == [str(selected["sshd"]), "-D", "-e", "-f", str(tmp_path / "sshd_config")]
        assert kwargs["require_job"] is True and kwargs["allow_breakaway"] is False
        assert kwargs["cwd"] == tmp_path


def test_foreground_start_uses_the_current_update_without_replaying_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected, update = config(tmp_path), object()
    process, job = Mock(), Mock()
    prepare, prepare_update = Mock(), Mock(return_value=tmp_path / "sshd_config")
    spawn = Mock(return_value=(process, job))
    monkeypatch.setattr(ingress, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(ingress, "_check_endpoint", Mock())
    monkeypatch.setattr(ingress, "prepare", prepare)
    monkeypatch.setattr(ingress, "prepare_update", prepare_update)
    monkeypatch.setattr(ingress, "spawn_sync_in_kill_on_close_job", spawn)
    owner = ingress.start(selected, update=update)
    prepare.assert_not_called()
    prepare_update.assert_called_once_with(selected, update)
    assert owner.process is process and owner.job is job
