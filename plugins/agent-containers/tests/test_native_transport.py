"""Native container ownership shares the provider's lifecycle admission."""

import pytest

from agent_containers import lease, native_claims
from agent_containers.__main__ import main
from agent_containers.native_transport import _ports


def test_remote_preparation_applies_environment_without_consuming_program_stdin(tmp_path, monkeypatch):
    import asyncio
    import io
    import shlex
    from types import SimpleNamespace
    from agent_containers import __main__ as cli, native_transport

    command = tmp_path / "node-command"
    command.write_text("node -", encoding="utf-8")
    program = b"process.stdout.write('prepared\\n');\r\n"
    output, errors = io.BytesIO(), io.BytesIO()

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
            pass

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
        stdout=SimpleNamespace(buffer=output), stderr=SimpleNamespace(buffer=errors),
    ))
    args = SimpleNamespace(name="fixture", command="remote-exec", command_file=str(command), stdin=True, timeout=10)
    prepared = {
        "ssh": {"host_alias": "fixture"}, "reverse_forwards": ["54321:127.0.0.1:12345"],
        "remote_env": "/prepared env", "user": "fixture",
    }
    assert asyncio.run(native_transport._run(args, prepared, None)) == 0
    assert output.getvalue() == b"prepared\n" and not errors.getvalue()
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
