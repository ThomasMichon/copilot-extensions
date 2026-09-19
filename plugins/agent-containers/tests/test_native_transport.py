"""Native container ownership shares the provider's lifecycle admission."""

import pytest

from agent_containers import lease, native_claims
from agent_containers.__main__ import main
from agent_containers.native_transport import _ports


@pytest.mark.parametrize("reply_timeout", [False, True])
def test_remote_preparation_applies_environment_without_consuming_program_stdin(tmp_path, monkeypatch, reply_timeout):
    import asyncio
    import io
    import shlex
    from types import SimpleNamespace
    from agent_containers import __main__ as cli, native_transport

    command = tmp_path / "node-command"
    command.write_text("node -", encoding="utf-8")
    program = b"process.stdout.write('prepared\\n');\r\n"
    output, errors = io.BytesIO(), io.BytesIO()
    closed = []

    class Channel:
        returncode = 0

        async def communicate(self, *, input):
            assert input == program
            return b"prepared\n", b""

    class Manager:
        async def ensure_connected(self, *args):
            pass

        async def exec_command(self, *args, **kwargs):
            return SimpleNamespace(exit_code=0)

        async def open_stdio_channel(self, name, rendered):
            assert name == "fixture"
            assert shlex.split(rendered) == [
                "exec", "bash", "-lc", ". '/prepared env'; rm -f '/prepared env'; node -",
            ]
            return Channel()

        async def close_stdio_channel(self, *args):
            closed.append("channel")

        async def disconnect_all(self):
            pass

    class Forward:
        is_alive = True

        def __init__(self, *args, **kwargs):
            pass

        async def establish(self):
            pass

        async def cancel(self):
            pass

    monkeypatch.setattr(native_transport, "ConnectionManager", Manager)
    monkeypatch.setattr(native_transport, "LocalForward", Forward)
    monkeypatch.setattr(cli, "cleanup_remote_env", lambda *args: None)
    monkeypatch.setattr(native_transport, "sys", SimpleNamespace(
        stdin=SimpleNamespace(buffer=io.BytesIO(program)),
        stdout=SimpleNamespace(buffer=output),
        stderr=io.TextIOWrapper(errors, encoding="utf-8", write_through=True),
    ))
    if reply_timeout:
        real_wait_for = asyncio.wait_for

        async def expire(awaitable, timeout):
            return await real_wait_for(awaitable, timeout=0)

        monkeypatch.setattr(native_transport.asyncio, "wait_for", expire)
    args = SimpleNamespace(name="fixture", command="remote-exec", command_file=str(command), stdin=True, timeout=10)
    prepared = {
        "ssh": {"host_alias": "fixture"}, "reverse_forwards": ["54321:127.0.0.1:12345"],
        "remote_env": "/prepared env", "user": "fixture",
    }
    assert asyncio.run(native_transport._run(args, prepared, None)) == (124 if reply_timeout else 0)
    if reply_timeout:
        assert not output.getvalue()
        assert b"execution outcome is uncertain" in errors.getvalue()
    else:
        assert output.getvalue() == b"prepared\n" and not errors.getvalue()
    assert closed == ["channel"]
    assert args.native_cleanup_complete is True


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(lease, "ensure_state_dir", lambda: None)
    for key, value in {
        "_LOCK_FILE": "lock", "LEASE_FILE": "leases.json", "_LEASE_DETAILS_FILE": "details.json",
        "_DEPLOY_HOLDS_FILE": "holds.json", "_SESSION_ADMISSIONS_FILE": "admissions.json",
    }.items():
        monkeypatch.setattr(lease, key, tmp_path / value)
    return tmp_path


@pytest.mark.parametrize("lease_owner", [None, "owner", "foreign"])
def test_remote_preparation_owner_admission_precedes_effects(state, monkeypatch, lease_owner):
    import os
    import time
    from types import SimpleNamespace
    from agent_containers import __main__ as cli, native_transport
    from agent_containers.config import ContainersConfig

    monkeypatch.setattr("ssh_manager.locks.locks_dir", lambda: state / "locks")
    monkeypatch.setattr(lease, "list_containers", lambda _: [SimpleNamespace(name="fixture", is_running=True)])
    if lease_owner is not None:
        lease._write_leases({"fixture": lease.Lease(
            "fixture", lease_owner, os.getpid(), lease._this_host(), time.time(), time.time(),
        )})
    before = lease.LEASE_FILE.read_bytes() if lease.LEASE_FILE.exists() else None
    effects = []

    def prepare(args):
        effects.append("prepare")
        assert lease.active_session_admissions(args.name)
        for borrower in (["another-owner", "owner"] if lease_owner is None else ["another-owner"]):
            with pytest.raises(lease.ProviderAdmissionError, match="active provider session"):
                lease.borrow(ContainersConfig(), borrower, container=args.name)
        return {}

    async def run(*args):
        effects.append("run")
        return 0

    monkeypatch.setattr(cli, "_require_live_relay_port", lambda: effects.append("relay") or 12345)
    monkeypatch.setattr(cli, "_prepare_session_host", prepare)
    monkeypatch.setattr(native_transport, "_run", run)
    result = main([
        "remote-exec", "fixture", "--owner", "owner", "--command-file", "node-command",
        "--stdin", "--require-relay",
    ])
    assert result == (75 if lease_owner == "foreign" else 0)
    assert effects == ([] if lease_owner == "foreign" else ["relay", "prepare", "run"])
    assert not lease.active_session_admissions("fixture")
    assert (lease.LEASE_FILE.read_bytes() if lease.LEASE_FILE.exists() else None) == before


def test_remote_preparation_requires_explicit_owner():
    with pytest.raises(SystemExit) as rejected:
        main(["remote-exec", "fixture", "--command-file", "node-command", "--stdin"])
    assert rejected.value.code == 2


@pytest.mark.parametrize("owner", ["", " "])
def test_owner_bound_admission_rejects_blank_owner(state, owner):
    with pytest.raises(lease.ProviderAdmissionError, match="nonblank"):
        with lease.session_admission("fixture", expected_owner=owner):
            pytest.fail("blank owner entered preparation")
    assert not lease.active_session_admissions("fixture")


def test_native_claim_survives_transport_loss_and_blocks_acp_and_lifecycle(state):
    identity = ("execution", "generation", "owner")
    native_claims.reserve("fixture", identity, "container-id")
    native_claims.reserve("fixture", identity, "container-id")
    with pytest.raises(lease.ProviderAdmissionError):
        native_claims.reserve("fixture", identity, "replacement-container-id")
    with pytest.raises(lease.ProviderAdmissionError):
        with lease.session_admission("fixture"):
            pytest.fail("ACP acquired native-owned venue")
    with pytest.raises(lease.ProviderAdmissionError):
        with lease.deploy_hold("fixture", "remove"):
            pytest.fail("lifecycle acquired native-owned venue")
    with lease.session_admission("fixture", native_identity=identity):
        pass
    native_claims.mark_launch("fixture", identity)
    with pytest.raises(lease.ProviderAdmissionError):
        native_claims.retire("fixture", identity)
    with pytest.raises(lease.ProviderAdmissionError):
        native_claims.retire("fixture", ("execution", "other", "owner"), {"retired": True})
    native_claims.retire("fixture", identity, {"retired": True, "executionId": "execution", "generation": "generation"})
    assert native_claims.retirement("fixture", identity)["containerId"] == "container-id"
    with lease.session_admission("fixture"):
        pass
    with pytest.raises(lease.ProviderAdmissionError):
        native_claims.reserve("fixture", identity, "container-id")


def test_native_claim_loss_is_not_an_empty_container(state):
    native_claims.reserve("fixture", ("execution", "generation", "owner"), "container-id")
    native_claims._path().unlink()
    with pytest.raises(lease.ProviderAdmissionError, match="lost"):
        with lease.session_admission("fixture"):
            pass


def test_unlaunched_native_abort_requires_confirmed_infrastructure_cleanup(state):
    identity = ("execution", "generation", "owner")
    native_claims.reserve("fixture", identity, "container-id")
    native_claims.infrastructure("fixture", identity, stopped=False)
    with pytest.raises(lease.ProviderAdmissionError, match="cleanup proof"):
        native_claims.retire("fixture", identity)
    native_claims.infrastructure("fixture", identity, stopped=True)
    native_claims.retire("fixture", identity)
    assert native_claims.retirement("fixture", identity)["noLaunch"] is True


def test_native_requires_descriptor_without_falling_back_to_acp():
    with pytest.raises(SystemExit):
        main(["native-transport", "fixture", "--owner", "owner", "--execution-id", "execution", "--generation", "generation"])


@pytest.mark.parametrize("values", [["0:1"], ["65536:1"], ["1:1", "1:2"], ["host:1:2"]])
def test_native_container_forward_validation(values):
    with pytest.raises(ValueError):
        _ports(values)
