"""Real target-local sshd/key admission; no existing listener or identities."""

import getpass
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

import pytest
from agent_procutil import no_window_kwargs
from fleet_manager.gateway_ssh_policy import GatewaySSHPolicy

from agent_ssh import gateway_authorization as authorization
from agent_ssh.__main__ import main
from native_ssh_auth import authenticate
from test_gateway_authorization import arguments
from test_gateway_provider import key


def test_running_native_sshd_consumes_key_changes_and_denies_forwarding(native_target_root, capsys):
    tmp_path = native_target_root
    sshd, keygen, ssh = (shutil.which(name) for name in ("sshd", "ssh-keygen", "ssh"))
    if not all((sshd, keygen, ssh)):
        pytest.skip("Native OpenSSH server, client and key generator are required")
    if os.name != "nt" and os.getuid() == 0:
        pytest.skip("This fixture proves a non-root current-user listener")
    argv, directory = arguments(tmp_path)
    keys = tmp_path / "keys"
    if os.name == "nt":
        authorization._windows_create_directory(keys)
    else:
        keys.mkdir(mode=0o700)
    for name in ("host", "local", "peer"):
        subprocess.run(
            [keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(keys / name)],
            check=True, capture_output=True, timeout=10, **no_window_kwargs(),
        )
    public = lambda name: " ".join((keys / (name + ".pub")).read_text(encoding="ascii").split()[:2])
    directory["machines"][0].update(public_key=public("local"), host_key=public("host"))
    directory["machines"][0]["user"] = getpass.getuser()
    directory["machines"][1]["public_key"] = public("peer")
    (tmp_path / "identity.pub").write_text(public("local") + "\n", encoding="ascii")
    source = tmp_path / "directory.json"
    source.write_text(json.dumps(directory), encoding="utf-8")
    assert main(argv + ["--apply"]) == 0
    authorized = Path(json.loads(capsys.readouterr().out)["authorization_file"])
    with socket.socket() as selected:
        selected.bind(("127.0.0.1", 0))
        port = selected.getsockname()[1]
    config = keys / "sshd_config"
    config.write_text("\n".join((
        "Port " + str(port), "ListenAddress 127.0.0.1", "AddressFamily inet",
        'HostKey "' + (keys / "host").as_posix() + '"',
        'AuthorizedKeysFile "' + authorized.as_posix() + '"',
        'PidFile "' + (keys / "sshd.pid").as_posix() + '"',
        "PubkeyAuthentication yes", "PasswordAuthentication no",
        "KbdInteractiveAuthentication no", "AuthenticationMethods publickey",
        "StrictModes yes", "AllowTcpForwarding yes", "AllowAgentForwarding yes",
        "LogLevel VERBOSE", "",
    )), encoding="utf-8")
    subprocess.run(
        [sshd, "-t", "-f", str(config)], check=True, capture_output=True,
        timeout=10, **no_window_kwargs(),
    )
    empty_config = keys / "client_config"
    empty_config.write_text("", encoding="ascii")
    known_hosts = keys / "known_hosts"
    known_hosts.write_text("authorization-fixture " + public("host") + "\n", encoding="ascii")
    client = [
        ssh, "-F", str(empty_config), "-p", str(port), "-l", getpass.getuser(),
        "-i", str(keys / "peer"), "-o", "HostKeyAlias=authorization-fixture",
        "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + str(known_hosts),
        "-o", "GlobalKnownHostsFile=none", "-o", "IdentitiesOnly=yes",
        "-o", "IdentityAgent=none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
        "-o", "PreferredAuthentications=publickey", "-o", "ForwardAgent=no",
    ]
    with (keys / "sshd.log").open("wb") as log:
        server = subprocess.Popen(
            [sshd, "-D", "-e", "-f", str(config)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, **no_window_kwargs(),
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                assert server.poll() is None, (keys / "sshd.log").read_text(encoding="utf-8")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    assert time.monotonic() < deadline, "Native fixture did not start"
                    time.sleep(0.05)

            def login():
                return authenticate(client, keys)

            result = login()
            assert result.authenticated, result.diagnostic + (keys / "sshd.log").read_text(encoding="utf-8")
            denied = subprocess.run(
                [*client, "-W", "127.0.0.1:" + str(port), "127.0.0.1"],
                capture_output=True, text=True, timeout=10, **no_window_kwargs(),
            )
            assert denied.returncode != 0 and "administratively prohibited" in denied.stderr
            directory["revision"] += 1
            directory["machines"][1]["public_key"] = key(3)
            source.write_text(json.dumps(directory), encoding="utf-8")
            assert main(argv + ["--apply"]) == 0
            capsys.readouterr()
            refused = login()
            assert not refused.authenticated and refused.native_exit == 255
            assert "Permission denied (publickey)" in refused.diagnostic
            directory["revision"] += 1
            directory["machines"][1]["public_key"] = public("peer")
            source.write_text(json.dumps(directory), encoding="utf-8")
            assert main(argv + ["--apply"]) == 0
            capsys.readouterr()
            assert login().authenticated
            policy_path = tmp_path / "policy.json"
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["machines"][0]["inbound"] = False
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            directory["policy_digest"] = GatewaySSHPolicy.from_dict(policy).policy_digest
            directory["revision"] += 1
            source.write_text(json.dumps(directory), encoding="utf-8")
            assert main(argv + ["--apply"]) == 0
            capsys.readouterr()
            refused = login()
            assert not refused.authenticated and refused.native_exit == 255
            assert "Permission denied (publickey)" in refused.diagnostic
            assert server.poll() is None
        finally:
            if server.poll() is None:
                server.terminate()
            server.wait(timeout=10)
