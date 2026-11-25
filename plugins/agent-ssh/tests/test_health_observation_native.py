"""Finite real-client control without an SSH account, key or production route."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import run_caller_owned

from agent_ssh import gateway_authorization, gateway_sshd, health_observation
from agent_ssh.__main__ import main


@pytest.mark.skipif(shutil.which("ssh") is None, reason="Native OpenSSH client unavailable")
def test_real_cli_bounds_a_live_unresponsive_ssh_route(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    profile = tmp_path / "direct.conf"
    identity = tmp_path / "synthetic key"
    known_hosts = tmp_path / "known hosts"
    identity.write_text("not a private key", encoding="utf-8")
    known_hosts.write_text("# no host keys\n", encoding="utf-8")
    stopped = threading.Event()
    accepted = threading.Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        port = listener.getsockname()[1]
        profile.write_text(
            "Host fixture\n  HostName 127.0.0.1\n  User fixture\n"
            f'  Port {port}\n  IdentityFile "{identity}"\n',
            encoding="utf-8",
        )
        original = {path: path.read_bytes() for path in (profile, identity, known_hosts)}

        def serve() -> None:
            try:
                connection, _ = listener.accept()
            except OSError:
                return
            with connection:
                accepted.set()
                connection.sendall(b"SSH-2.0-fixture\r\n")
                stopped.wait(5)

        server = threading.Thread(target=serve)
        server.start()
        started = time.monotonic()
        try:
            code = main([
                "observe", "--alias", "fixture", "--profile", str(profile),
                "--known-hosts", str(known_hosts), "--timeout-ms", "2000", "--json",
            ])
            elapsed = time.monotonic() - started
        finally:
            stopped.set()
            server.join(timeout=6)
        assert not server.is_alive()
        assert accepted.is_set(), "The real SSH client must reach the private fixture"
        assert code == 1 and elapsed < 3
        result = json.loads(capsys.readouterr().out)
        assert result["reason"] == "timeout" and result["observed_at_ms"] is None
        assert {path: path.read_bytes() for path in original} == original


@pytest.mark.skipif(
    os.environ.get("AGENT_SSH_HEALTH_NATIVE") != "1",
    reason="Explicit disposable SSH server and test-key approval required",
)
@pytest.mark.timeout(100)
def test_native_authenticated_action_and_refusals(
    native_target_root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    sshd, keygen, ssh = (shutil.which(name) for name in ("sshd", "ssh-keygen", "ssh"))
    if sshd is None or keygen is None or ssh is None:
        pytest.skip("Existing native OpenSSH server, key generator and client required")
    if os.name == "nt":
        monkeypatch.setenv(
            "C28FC6F98A2C44ABBBD89D6A3037D0D9_POSIX_FD_STATE", "synthetic-ancestor-state",
        )
    root = native_target_root / "health ssh"
    if os.name == "nt":
        gateway_authorization._windows_create_directory(root)
    else:
        root.mkdir(mode=0o700)
    native_env = {
        key: value for key, value in os.environ.items()
        if not key.endswith("_POSIX_FD_STATE")
    }

    def finite(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return run_caller_owned(
            argv, timeout=5, max_stdout_bytes=16384, max_stderr_bytes=16384,
            env=native_env,
        )

    for name in ("host", "accepted", "denied"):
        assert finite([
            keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(root / name),
        ]).returncode == 0, "Disposable key generation failed"
    if os.name == "nt":
        # A non-elevated sshd needs its inherited private-directory ACL.
        host_bytes = (root / "host").read_bytes()
        (root / "host").unlink()
        (root / "host").write_bytes(host_bytes)
    authorized = root / "authorized_keys"
    authorized.write_bytes((root / "accepted.pub").read_bytes())
    authorized.chmod(0o600)
    known_hosts = root / "known_hosts"
    empty_hosts = root / "empty_hosts"
    with socket.socket() as choice:
        choice.bind(("127.0.0.1", 0))
        port = choice.getsockname()[1]
    host_key = " ".join((root / "host.pub").read_text(encoding="ascii").split()[:2])
    known_hosts.write_text(f"[127.0.0.1]:{port} {host_key}\n", encoding="ascii")
    empty_hosts.write_text("# deliberately untrusted\n", encoding="ascii")
    profiles = []
    account = gateway_sshd._account()
    for name in ("accepted", "denied"):
        profile = root / (name + ".conf")
        profile.write_text(
            f"Host fixture\n  HostName 127.0.0.1\n  User {account}\n"
            f'  Port {port}\n  IdentityFile "{root / name}"\n',
            encoding="utf-8",
        )
        profiles.append(profile)
    config = root / "sshd_config"
    lines = [
        f"Port {port}", "ListenAddress 127.0.0.1", "AddressFamily inet",
        f'HostKey "{(root / "host").as_posix()}"',
        f'AuthorizedKeysFile "{authorized.as_posix()}"',
        f'PidFile "{(root / "sshd.pid").as_posix()}"',
        "PubkeyAuthentication yes", "PasswordAuthentication no",
        "KbdInteractiveAuthentication no", "AuthenticationMethods publickey",
        "StrictModes yes", "AllowTcpForwarding no", "AllowAgentForwarding no",
        "X11Forwarding no", "PermitTunnel no", "PermitUserRC no",
        "PermitUserEnvironment no", "AuthorizedKeysCommand none",
        "LogLevel ERROR", "LoginGraceTime 10",
    ]
    if os.name != "nt":
        lines.extend(("UsePAM no", "PermitRootLogin no"))
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert finite([sshd, "-t", "-f", str(config)]).returncode == 0, "Fixture configuration refused"
    project, wheels, installed_root = (root / name for name in ("project", "wheels", "installed"))
    project.mkdir()
    wheels.mkdir()
    source = Path(__file__).resolve().parents[1]
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(source / name, project / name)
    shutil.copytree(
        source / "src", project / "src",
        ignore=shutil.ignore_patterns("*.egg-info", "__pycache__"),
    )
    built = run_caller_owned(
        [sys.executable, "-I", "-c",
         "from setuptools.build_meta import build_wheel; "
         f"build_wheel({str(wheels)!r})"],
        cwd=project, env=native_env, timeout=30,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert built.returncode == 0, "Private provider wheel could not be built"
    with zipfile.ZipFile(next(wheels.glob("*.whl"))) as archive:
        archive.extractall(installed_root)
    original = {
        path: path.read_bytes() for path in root.iterdir()
        if path.is_file()
    }
    settled = threading.Event()

    def serve() -> None:
        with pytest.raises(subprocess.TimeoutExpired):
            run_caller_owned(
                [sshd, "-D", "-e", "-f", str(config)], timeout=None,
                budget=OwnedExecutionBudget(deadline=time.monotonic() + 43, cleanup_reserve=3),
                max_stdout_bytes=16384, max_stderr_bytes=16384,
                on_settled=settled.set, env=native_env,
            )

    with ThreadPoolExecutor(max_workers=1) as workers:
        server = workers.submit(serve)
        try:
            startup = time.monotonic() + 5
            while True:
                if server.done():
                    server.result()
                    pytest.fail("Native fixture exited before readiness")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    assert time.monotonic() < startup, "Native fixture did not become ready"
                    time.sleep(0.05)
            installed_script = """
import importlib.resources, json, pathlib, sys
installed = pathlib.Path(sys.argv.pop(1))
sys.path.insert(0, str(installed))
import agent_ssh.__main__ as cli
assert pathlib.Path(cli.__file__).is_relative_to(installed)
support = json.loads(importlib.resources.files('agent_ssh').joinpath(
    'health-observation-v1.json').read_text(encoding='utf-8'))
assert support == {
    'schema': 'agent-ssh.health.observation', 'version': 1,
    'operations': ['observe'], 'routes': ['direct-key-file'], 'max_timeout_ms': 5000,
}
raise SystemExit(cli.main(sys.argv[1:]))
"""
            installed = run_caller_owned(
                [sys.executable, "-I", "-c", installed_script, str(installed_root),
                 "observe", "--alias", "fixture", "--profile", str(profiles[0]),
                 "--known-hosts", str(known_hosts), "--json"],
                cwd=root, env=native_env, timeout=7,
                max_stdout_bytes=16384, max_stderr_bytes=16384,
            )
            assert installed.returncode == 0, "Private installed provider did not complete"
            installed_result = json.loads(installed.stdout)
            assert installed_result["state"] == "ok" and installed_result["direction"] == "outbound"
            assert type(installed_result["observed_at_ms"]) is int and installed.stderr == ""
            for profile, hosts, success in (
                (profiles[0], known_hosts, True),
                (profiles[0], empty_hosts, False),
                (profiles[1], known_hosts, False),
            ):
                diagnostic = finite(health_observation.command(
                    ssh, health_observation.read_route(profile, "fixture"), hosts,
                ))
                assert diagnostic.returncode == (0 if success else 255), {
                    "case": profile.stem, "hosts": hosts.name,
                    "returncode": diagnostic.returncode,
                    "host_refused": "Host key verification failed" in diagnostic.stderr,
                    "auth_refused": "Permission denied" in diagnostic.stderr,
                }
                if not success:
                    expected = "Host key verification failed" if hosts == empty_hosts else "Permission denied"
                    assert expected in diagnostic.stderr, "Native refusal did not reach its intended boundary"
                before = time.time_ns() // 1_000_000
                started = time.monotonic()
                code = main([
                    "observe", "--alias", "fixture", "--profile", str(profile),
                    "--known-hosts", str(hosts), "--timeout-ms", "5000", "--json",
                ])
                assert time.monotonic() - started < 5
                captured = capsys.readouterr()
                result = json.loads(captured.out)
                assert captured.err == ""
                assert result["direction"] == "outbound"
                if success:
                    assert code == 0 and result["state"] == "ok", result
                    stamp = result["observed_at_ms"]
                    assert type(stamp) is int and before <= stamp <= time.time_ns() // 1_000_000
                else:
                    assert code == 1 and result["reason"] == "route-failed", {
                        "case": profile.stem, "hosts": hosts.name, "result": result,
                    }
                    assert result["observed_at_ms"] is None
                assert all(path.read_bytes() == value for path, value in original.items()), "Fixture inputs changed"
        finally:
            server.result(timeout=45)
            assert settled.is_set(), "Exact native server scope did not settle"
            with socket.socket() as check:
                check.settimeout(0.5)
                assert check.connect_ex(("127.0.0.1", port)) != 0, "Fixture listener survived cleanup"
