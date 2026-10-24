"""Experimental reference implementation; see the parent README."""

import getpass
import importlib.util
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import subprocess
import threading
import time
import unittest
import uuid


SERVICE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("native_policy", SERVICE / "gateway_liveness.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def free_port():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


def listening(port):

    for line in Path("/proc/net/tcp").read_text().splitlines()[1:]:
        fields = line.split()
        address, local_port = fields[1].split(":")
        if fields[3] == "0A" and address == "0100007F" and int(local_port, 16) == port:
            return True
    return False


class Blackhole:
    def __init__(self, target):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(0.2)
        self.port = self.listener.getsockname()[1]
        self.target = target
        self.drop = threading.Event()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run)
        self.thread.start()

    def run(self):
        client = backend = None
        try:
            while not self.stop.is_set():
                try:
                    client, _ = self.listener.accept()
                    break
                except socket.timeout:
                    continue
            if client is None:
                return
            backend = socket.create_connection(("127.0.0.1", self.target), timeout=3)
            while not self.stop.is_set():
                if self.drop.is_set():
                    self.stop.wait(0.1)
                    continue
                ready, _, _ = select.select([client, backend], [], [], 0.1)
                for source in ready:
                    data = source.recv(65536)
                    if not data:
                        return
                    (backend if source is client else client).sendall(data)
        finally:
            for connection in (client, backend):
                if connection is not None:
                    connection.close()

    def close(self):
        self.stop.set()
        self.thread.join(5)
        self.listener.close()
        if self.thread.is_alive():
            raise RuntimeError("Owned blackhole fixture did not stop")


@unittest.skipUnless(os.name == "posix" and os.geteuid() == 0 and
                     Path("/usr/sbin/sshd").is_file(),
                     "requires an explicitly authorized isolated Linux root sshd fixture")
class NativeLivenessTests(unittest.TestCase):
    def test_dead_reverse_owner_expires_and_responsive_session_survives(self):
        root = SERVICE / "tests" / ("_native-liveness-" + uuid.uuid4().hex)
        root.mkdir(mode=0o700)
        clients = []
        server = proxy = None
        log = None
        passed = False
        try:
            for name in ("host", "client"):
                subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                                "-f", str(root / name)], check=True, timeout=15)
            authorized = root / "authorized_keys"
            authorized.write_bytes((root / "client.pub").read_bytes())
            port, reverse, responsive = free_port(), free_port(), free_port()
            user = os.environ.get("SUDO_USER") or getpass.getuser()
            if user == "root":
                self.fail("Run via sudo from the existing non-root Gateway account")
            config = root / "sshd_config"
            config.write_text(
                f"Port {port}\nListenAddress 127.0.0.1\n"
                f"HostKey {root / 'host'}\nPidFile {root / 'pid'}\n"
                f"AuthorizedKeysFile {authorized}\nStrictModes no\nUsePAM yes\n"
                "PasswordAuthentication no\nKbdInteractiveAuthentication no\n"
                "AllowTcpForwarding yes\nGatewayPorts no\nLogLevel ERROR\n"
                f"AllowUsers {user}\n", encoding="utf-8")

            import pwd
            account = pwd.getpwnam(user)
            for path in (root, authorized):
                os.chown(path, account.pw_uid, account.pw_gid)
            os.chmod(authorized, 0o600)
            log = (root / "server.log").open("wb")
            server = subprocess.Popen(
                ["/usr/sbin/sshd", "-D", "-e", "-f", str(config)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
            deadline = time.monotonic() + 5
            while not listening(port) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertIsNone(server.poll())

            def runner(argv):
                if argv == ["reload-owned-fixture"]:
                    server.send_signal(signal.SIGHUP)
                    time.sleep(0.3)
                    self.assertIsNone(server.poll())
                    return ""
                return policy.run(argv)

            owner = policy.Policy(config, "/usr/sbin/sshd", ["reload-owned-fixture"], runner)
            owner.apply("install", user)
            known = root / "known_hosts"
            fields = (root / "host.pub").read_text().split()
            known.write_text(f"fixture {fields[0]} {fields[1]}\n", encoding="ascii")

            def connect(local_port, remote_port):
                command = [
                    "ssh", "-F", "/dev/null", "-N", "-T", "-p", str(local_port),
                    "-i", str(root / "client"), "-o", "IdentitiesOnly=yes",
                    "-o", "IdentityAgent=none", "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=yes", "-o", "HostKeyAlias=fixture",
                    "-o", f"UserKnownHostsFile={known}", "-o", "ServerAliveInterval=0",
                    "-o", "ExitOnForwardFailure=yes",
                    "-R", f"127.0.0.1:{remote_port}:127.0.0.1:{port}",
                    f"{user}@127.0.0.1",
                ]
                child = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=log)
                clients.append(child)
                return child

            healthy = connect(port, responsive)
            proxy = Blackhole(port)
            dead = connect(proxy.port, reverse)
            deadline = time.monotonic() + 8
            while not (listening(reverse) and listening(responsive)) and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertTrue(listening(reverse))
            self.assertTrue(listening(responsive))
            proxy.drop.set()
            started = time.monotonic()
            while listening(reverse) and time.monotonic() - started < 65:
                time.sleep(0.5)
            elapsed = time.monotonic() - started
            self.assertFalse(listening(reverse), "Unresponsive reverse owner did not expire")
            self.assertLess(elapsed, 65)
            self.assertIsNone(healthy.poll(), "Responsive authenticated session was terminated")
            self.assertTrue(listening(responsive))
            replacement = connect(port, reverse)
            deadline = time.monotonic() + 8
            while not listening(reverse) and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertTrue(listening(reverse))
            self.assertIsNone(replacement.poll())
            print(f"NATIVE_STALE_REVERSE_RELEASE_SECONDS={elapsed:.3f}; RESPONSIVE_SURVIVED=true")
            self.assertIsNone(dead.poll(), "Fixture must blackhole, not close, the old client")
            passed = True
        finally:
            if proxy is not None:
                proxy.close()
            for child in clients:
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=10)
            if server is not None:
                if server.poll() is None:
                    server.terminate()
                server.wait(timeout=10)
            if log is not None:
                log.close()
                if not passed:
                    print("ISOLATED_SSH_FIXTURE_FAILURE_LOG",
                          (root / "server.log").read_text(errors="replace")[-6000:])
            shutil.rmtree(root)
