"""Real lifecycle-selected native OpenSSH listener, with no production changes."""

import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

import pytest
from agent_procutil import direct_python, no_window_kwargs
from fleet_manager.gateway_ssh_policy import GatewaySSHPolicy

from agent_ssh import gateway_authorization as authorization
from agent_ssh import gateway_sshd as ingress
from native_ssh_auth import authenticate
from test_gateway_provider import fixture_files
from test_gateway_sshd import config


def _windows_context():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.ProcessIdToSessionId.argtypes = [
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.ProcessIdToSessionId.restype = wintypes.BOOL
    session = wintypes.DWORD()
    assert kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session))
    assert session.value != 0, "Select an actual user-logon qualification lane"
    return {
        "sid": authorization._windows_sid(), "session": session.value,
        "elevated": False,
    }


def _windows_context_command():
    script = (
        "$i=[Security.Principal.WindowsIdentity]::GetCurrent();"
        "$p=[Security.Principal.WindowsPrincipal]::new($i);"
        "[pscustomobject]@{sid=$i.User.Value;"
        "session=[Diagnostics.Process]::GetCurrentProcess().SessionId;"
        "elevated=$p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)}"
        "|ConvertTo-Json -Compress"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return "powershell.exe -NoProfile -NonInteractive -EncodedCommand " + encoded


@pytest.mark.skipif(os.name != "nt", reason="Windows SSH outer-shell command parsing")
@pytest.mark.timeout(15)
@pytest.mark.parametrize("shell", ["cmd", "powershell", "pwsh"])
def test_windows_command_probe_preserves_context_through_outer_shell(shell):
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip("Selected Windows outer shell is unavailable")
    arguments = ["/d", "/s", "/c"] if shell == "cmd" else [
        "-NoProfile", "-NonInteractive", "-Command",
    ]
    completed = subprocess.run(
        [executable, *arguments, _windows_context_command()],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=10, **no_window_kwargs(),
    )
    assert completed.returncode == 0, "Native outer-shell command did not complete successfully"
    if json.loads(completed.stdout) != _windows_context():
        pytest.fail("Native outer shell did not preserve the selected user context")


@pytest.fixture(params=[
    pytest.param(False, id="synthetic-pair"),
    pytest.param(True, id="vault-pair", marks=pytest.mark.skipif(
        os.environ.get("AGENT_SSH_VAULT_NATIVE") != "1",
        reason="opt-in composed disposable Vault/native SSH proof",
    )),
])
def native_peer_key(request, native_target_root):
    if not request.param:
        yield None
        return
    path = Path(__file__).resolve().parents[2] / "agent-vault" / "tests" / "test_identity_key_keepass_live.py"
    spec = importlib.util.spec_from_file_location("_ssh_disposable_vault", path)
    helper = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = helper
    spec.loader.exec_module(helper)
    with helper.disposable_vault(native_target_root / "native vault", seed_owner=False) as fixture:
        entry = "SSH/native-peer"
        prepared = fixture.run(
            "ssh-key", "prepare", "--entry", entry, "--require-running",
            "--initialize", "--confirm-initialization", "--json",
        )
        assert prepared.returncode == 0, "Disposable Vault pair preparation failed"
        metadata = json.loads(prepared.stdout)
        assert metadata["status"] == "created"
        database_before = hashlib.sha256(Path(fixture.database).read_bytes()).digest()
        command = (
            "ssh-key", "install", "--entry", entry, "--directory",
            str(fixture.root / "protected pair"), "--require-running",
            "--expected-fingerprint", metadata["fingerprint"], "--json",
        )
        installed = fixture.run(*command)
        assert installed.returncode == 0, "Disposable pinned key installation failed"
        result = json.loads(installed.stdout)
        assert result["status"] == "installed"
        private = Path(result["private_key_path"])
        before = (private.read_bytes(), private.stat().st_mtime_ns)
        repeated = fixture.run(*command)
        assert repeated.returncode == 0, "Fresh Vault installation reader failed"
        assert json.loads(repeated.stdout)["status"] == "reused"
        assert (private.read_bytes(), private.stat().st_mtime_ns) == before
        assert fixture.request({"action": "lock", "kpdb": fixture.database})["ok"] is True
        refused = fixture.run(*command)
        assert refused.returncode == 1, "Locked Vault setup used existing local files"
        assert json.loads(refused.stdout)["error"] == "needs_unlock"
        yield result
        assert (private.read_bytes(), private.stat().st_mtime_ns) == before
        assert hashlib.sha256(Path(fixture.database).read_bytes()).digest() == database_before
        output = prepared.stdout + installed.stdout + repeated.stdout + refused.stdout
        output += prepared.stderr + installed.stderr + repeated.stderr + refused.stderr
        output += (fixture.root / "daemon.log").read_text(encoding="utf-8")
        assert "BEGIN OPENSSH PRIVATE KEY" not in output, "Fixture private key leaked"
        assert not (fixture.root / "cache-root").exists(), "Fixture populated an identity cache"


@pytest.mark.timeout(60)
def test_native_listener_login_refresh_withdrawal_and_preservation(native_target_root, native_peer_key):
    sshd, keygen, ssh = (shutil.which(name) for name in ("sshd", "ssh-keygen", "ssh"))
    if not all((sshd, keygen, ssh)):
        pytest.skip("Native OpenSSH server, client and key generator are required")
    if os.name != "nt" and os.geteuid() == 0:
        pytest.skip("This fixture requires a non-root current-user OpenSSH server")
    root = native_target_root / "native ingress"
    if os.name == "nt":
        authorization._windows_create_directory(root)
    else:
        root.mkdir(mode=0o700)
    _, directory = fixture_files(root)
    for name in ("host", "local", "peer", "new-peer"):
        if name == "peer" and native_peer_key is not None:
            continue
        subprocess.run(
            [keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(root / name)],
            check=True, capture_output=True, timeout=10, **no_window_kwargs(),
        )
    if os.name == "nt":
        # ssh-keygen adds its own ACL; model the selected Vault's private file.
        private = (root / "host").read_bytes()
        (root / "host").unlink()
        (root / "host").write_bytes(private)

    def public(name):
        if name == "peer" and native_peer_key is not None:
            return native_peer_key["public_key"]
        return " ".join((root / (name + ".pub")).read_text(encoding="ascii").split()[:2])

    policy_path, directory_path = root / "policy.json", root / "directory.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    for row in policy["machines"]:
        row["environment"] = ingress._environment()
    for row in directory["machines"]:
        row["environment"] = ingress._environment()
    directory["policy_digest"] = GatewaySSHPolicy.from_dict(policy).policy_digest
    directory["machines"][0].update(
        public_key=public("local"), host_key=public("host"), user=ingress._account(),
    )
    directory["machines"][1]["public_key"] = public("peer")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    directory_path.write_text(json.dumps(directory), encoding="utf-8")
    (root / "identity.pub").write_text(public("local") + "\n", encoding="ascii")
    selected = config(root)
    with socket.socket() as choice:
        choice.bind(("127.0.0.1", 0))
        port = choice.getsockname()[1]
    selected.update(
        environment=ingress._environment(), sshd=Path(sshd), ssh_keygen=Path(keygen), port=port,
    )
    config_path = root / "selected.json"
    config_path.write_text(json.dumps({
        name: str(value) if isinstance(value, Path) else value for name, value in selected.items()
    }), encoding="utf-8")
    if os.name != "nt":
        config_path.chmod(0o600)
    unrelated = root / "unrelated-authorized-keys"
    unrelated.write_text("retain-unrelated-bootstrap-key\n", encoding="ascii")
    client_config = root / "client_config"
    client_config.write_text("", encoding="ascii")
    known_hosts = root / "known_hosts"
    known_hosts.write_text("native-ingress " + public("host") + "\n", encoding="ascii")

    def client_arguments(identity="peer"):
        private_key = (
            Path(native_peer_key["private_key_path"])
            if identity == "peer" and native_peer_key is not None else root / identity
        )
        return [
            ssh, "-F", str(client_config), "-l", ingress._account(), "-p", str(port),
            "-i", str(private_key),
            "-o", "HostKeyAlias=native-ingress", "-o", "StrictHostKeyChecking=yes",
            "-o", 'UserKnownHostsFile="' + str(known_hosts) + '"', "-o", "GlobalKnownHostsFile=none",
            "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
            "-o", "PreferredAuthentications=publickey",
        ]

    def client(identity="peer"):
        return authenticate(client_arguments(identity), root)

    with (root / "native.log").open("wb") as log:
        python, native_env = direct_python(sys.executable)
        server = subprocess.Popen(
            [python, "-m", "agent_ssh.gateway_sshd", "--config", str(config_path)],
            env={
                **os.environ, **native_env,
                **({"C28FC6F98A2C44ABBBD89D6A3037D0D9_POSIX_FD_STATE": "synthetic-ancestor-state"}
                   if os.name == "nt" else {}),
            }, cwd=root,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, **no_window_kwargs(),
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                assert server.poll() is None, (root / "native.log").read_text(encoding="utf-8")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    assert time.monotonic() < deadline, "Native ingress did not start"
                    time.sleep(0.05)
            first = client()
            assert first.authenticated, first.diagnostic + (root / "native.log").read_text(encoding="utf-8")
            if os.name == "nt":
                expected, command = _windows_context(), _windows_context_command()
            else:
                expected, command = str(os.geteuid()), "id -u"
            completed = subprocess.run(
                [*client_arguments(), "-T", "127.0.0.1", command],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=15, **no_window_kwargs(),
            )
            assert completed.returncode == 0, "Native SSH command did not complete successfully"
            actual = json.loads(completed.stdout) if os.name == "nt" else completed.stdout.strip()
            if actual != expected:
                pytest.fail("Native SSH command did not preserve the selected user context")
            target = selected["state_dir"] / "authorization" / "authorized_keys.gateway"
            native_config = selected["state_dir"] / "sshd_config"
            before = {
                path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (target, native_config)
            }
            ingress.prepare(ingress.load(config_path))
            assert client().authenticated
            assert all((path.read_bytes(), path.stat().st_mtime_ns) == value for path, value in before.items())
            with (root / "collision.log").open("wb") as collision_log:
                collision = subprocess.Popen(
                    [python, "-m", "agent_ssh.gateway_sshd", "--config", str(config_path)],
                    env={**os.environ, **native_env}, cwd=root,
                    stdin=subprocess.DEVNULL, stdout=collision_log, stderr=collision_log,
                    **no_window_kwargs(),
                )
                try:
                    code = collision.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    pytest.fail("Native contention did not exit: " + (root / "collision.log").read_text(encoding="utf-8"))
                finally:
                    if collision.poll() is None:
                        collision.terminate()
                    collision.wait(timeout=5)
            assert code == 2 and "already bound" in (root / "collision.log").read_text(encoding="utf-8").lower()
            assert server.poll() is None and client().authenticated
            denied = subprocess.run(
                [*client_arguments(), "-W", "127.0.0.1:22", "127.0.0.1"],
                capture_output=True, text=True, timeout=10, **no_window_kwargs(),
            )
            assert denied.returncode != 0 and "administratively prohibited" in denied.stderr
            directory["revision"] += 1
            directory["machines"][1]["public_key"] = public("new-peer")
            directory_path.write_text(json.dumps(directory), encoding="utf-8")
            ingress.prepare(ingress.load(config_path))
            refused = client()
            assert not refused.authenticated and refused.native_exit == 255
            assert "Permission denied (publickey)" in refused.diagnostic
            assert client("new-peer").authenticated
            before_keys = target.read_bytes()
            directory["revision"] = 1
            directory_path.write_text(json.dumps(directory), encoding="utf-8")
            with pytest.raises(ValueError, match="older than"):
                ingress.prepare(ingress.load(config_path))
            assert target.read_bytes() == before_keys
            directory["revision"] = 2
            directory["machines"][0]["host_key"] = public("peer")
            directory_path.write_text(json.dumps(directory), encoding="utf-8")
            with pytest.raises(ValueError, match="admitted target pin"):
                ingress.prepare(ingress.load(config_path))
            assert target.read_bytes() == before_keys
            directory["machines"][0]["host_key"] = public("host")
            policy["machines"][0]["inbound"] = False
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            directory["policy_digest"] = GatewaySSHPolicy.from_dict(policy).policy_digest
            directory["revision"] += 1
            directory_path.write_text(json.dumps(directory), encoding="utf-8")
            ingress.prepare(ingress.load(config_path))
            refused = client("new-peer")
            assert not refused.authenticated and refused.native_exit == 255
            assert "Permission denied (publickey)" in refused.diagnostic
            assert target.read_text(encoding="utf-8").splitlines()[1:] == []
            assert unrelated.read_text(encoding="ascii") == "retain-unrelated-bootstrap-key\n"
            assert server.poll() is None
        finally:
            if server.poll() is None:
                server.terminate()
            server.wait(timeout=10)
        deadline = time.monotonic() + 5
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    assert time.monotonic() < deadline, "Native listener survived its owner"
                    time.sleep(0.05)
            except OSError:
                break
