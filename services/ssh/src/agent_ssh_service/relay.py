"""Linux forward-only OpenSSH child of the existing registered service host."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading

from fleet_manager.planning import canonical

from .config import SSHServiceError, absolute, check
from .store import DirectoryStore


def private(path: Path, *, directory=False) -> None:
    absolute(str(path))
    info = path.stat()
    check((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
          and (directory or info.st_nlink == 1)
          and info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
          "unsafe-native-relay-artifact")


def write(path: Path, content: bytes) -> None:
    absolute(str(path))
    descriptor, pending = tempfile.mkstemp(prefix=".relay-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def authorization(rows: list[dict]) -> bytes:
    """Never reuse target-login keys as relay credentials or allow session channels."""
    destinations = [f'permitopen="127.0.0.1:{row["reverse_port"]}"' for row in rows]
    lines = []
    for row in rows:
        options = [
            "restrict", "port-forwarding", 'command="/bin/false"',
            f'permitlisten="127.0.0.1:{row["reverse_port"]}"', *destinations,
        ]
        lines.append(",".join(options) + " " + row["relay_public_key"] + "\n")
    return "".join(lines).encode("ascii")


def native_config(selected: dict, root: Path) -> bytes:
    def quoted(path):
        text = str(path)
        check(not any(char in text for char in '"%\r\n'), "invalid-native-relay-path")
        return '"' + text + '"'

    return ("\n".join((
        "# agent-ssh-service native forward-only relay v1",
        "Port " + str(selected["port"]),
        "ListenAddress " + selected["listen_host"],
        "AddressFamily inet", "PidFile none",
        "HostKey " + quoted(selected["host_key_file"]),
        "HostKeyAlgorithms ssh-ed25519",
        "AuthorizedKeysFile " + quoted(root / "authorized_keys"),
        "AuthorizedKeysCommand none", "TrustedUserCAKeys none",
        "AuthorizedPrincipalsFile none", "AllowUsers " + selected["user"],
        "PubkeyAuthentication yes", "AuthenticationMethods publickey",
        "PasswordAuthentication no", "KbdInteractiveAuthentication no",
        "HostbasedAuthentication no", "GSSAPIAuthentication no", "UsePAM no",
        "PermitRootLogin no", "PermitEmptyPasswords no", "StrictModes yes",
        "AllowTcpForwarding yes", "GatewayPorts no",
        "AllowStreamLocalForwarding no", "AllowAgentForwarding no",
        "X11Forwarding no", "PermitTunnel no", "PermitTTY no",
        "PermitUserEnvironment no", "PermitUserRC no", "MaxSessions 0",
        "UseDNS no", "LoginGraceTime 30", "MaxAuthTries 3",
        "MaxStartups 8:30:16", "LogLevel VERBOSE", "",
    ))).encode("utf-8")


def verify_host_pin(derived: str, expected: str) -> None:
    lines = derived.strip().splitlines()
    check(len(lines) == 1 and " ".join(lines[0].split()[:2]) == expected,
          "native-relay-host-pin-mismatch")


def owns_listener(pid: int, host: str, port: int) -> bool:
    """Observe this unreaped child's actual listening socket, never a banner."""
    expected = ("0100007F" if host == "127.0.0.1" else "00000000") + f":{port:04X}"
    inodes = {
        row.split()[9] for row in Path("/proc/net/tcp").read_text("ascii").splitlines()[1:]
        if row.split()[1] == expected and row.split()[3] == "0A"
    }
    for entry in Path(f"/proc/{pid}/fd").iterdir():
        try:
            target = os.readlink(entry)
        except FileNotFoundError:
            continue
        if target.startswith("socket:[") and target[8:-1] in inodes:
            return True
    return False


class NativeRelay:
    def __init__(self, store: DirectoryStore):
        check(sys.platform == "linux" and os.geteuid() != 0,
              "native-relay-requires-nonroot-linux")
        import pwd

        self.store = store
        self.selected = store.config.to_dict()["relay"]
        check(pwd.getpwuid(os.geteuid()).pw_name == self.selected["user"],
              "native-relay-login-user-mismatch")
        self.root = store.root / "relay"
        self.lock = threading.Lock()
        self.process = self.custody = None

    def prepare(self) -> None:
        selected = self.selected
        private(absolute(selected["host_key_file"]))
        from agent_procutil.owned_process import run_caller_owned

        key = run_caller_owned(
            [selected["ssh_keygen"], "-y", "-P", "", "-f", selected["host_key_file"]],
            timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096,
        )
        check(key.returncode == 0, "native-relay-host-pin-mismatch")
        verify_host_pin(key.stdout, selected["host_public_key"])
        if not self.root.exists():
            self.root.mkdir(mode=0o700)
        private(self.root, directory=True)
        check({path.name for path in self.root.iterdir()} <= {
            "owner.json", "sshd_config", "authorized_keys", "authorization.sha256",
        }, "unowned-native-relay-artifact")
        for path in self.root.iterdir():
            private(path)
        owner = canonical({
            "schema": "agent-ssh.native-relay", "version": 1,
            **{name: self.store.config.authority[name] for name in (
                "fleet", "gateway", "slot", "controller",
            )}, "relay": selected,
        })
        self.owner = owner
        binding = self.root / "owner.json"
        if binding.exists():
            check(binding.read_bytes() == owner, "native-relay-owner-mismatch")
        else:
            check(not any(self.root.iterdir()), "unowned-native-relay-directory")
            write(binding, owner)
        content = native_config(selected, self.root)
        self.configuration = content
        config = self.root / "sshd_config"
        if config.exists():
            check(config.read_bytes() == content, "native-relay-config-drift")
        else:
            write(config, content)
        result = run_caller_owned(
            [selected["sshd"], "-t", "-f", str(config)],
            timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096,
        )
        check(result.returncode == 0, "native-relay-config-refused")
        self.reconcile()

    def reconcile(self) -> None:
        with self.lock:
            target, receipt = self.root / "authorized_keys", self.root / "authorization.sha256"
            for path in (self.root / "owner.json", self.root / "sshd_config", target, receipt):
                if path.exists():
                    private(path)
            check((self.root / "owner.json").read_bytes() == self.owner
                  and (self.root / "sshd_config").read_bytes() == self.configuration,
                  "native-relay-owner-or-config-drift")
            desired = authorization(self.store.relay_snapshot())
            digest = hashlib.sha256(desired).hexdigest().encode("ascii")
            if target.exists():
                observed = target.read_bytes()
                check(observed == desired or (
                    receipt.exists()
                    and hashlib.sha256(observed).hexdigest().encode("ascii") == receipt.read_bytes()
                ), "native-relay-authorization-drift")
            else:
                check(not receipt.exists(), "native-relay-authorization-missing")
            if not target.exists() or target.read_bytes() != desired:
                write(target, desired)
            write(receipt, digest)

    async def run(self, stop: asyncio.Event, ready: asyncio.Event) -> None:
        from agent_procutil import no_window_kwargs
        from agent_procutil._caller_owned import _require_posix_support
        from agent_procutil.native_child import NativeChildCustody

        _require_posix_support()
        if stop.is_set():
            return
        self.prepare()
        process = subprocess.Popen(
            [self.selected["sshd"], "-D", "-e", "-f", str(self.root / "sshd_config")],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=None,
            cwd=self.root, start_new_session=True, **no_window_kwargs(),
        )
        self.process = process
        # Keep the original leader unreaped until its complete descendant group settles.
        try:
            self.custody = NativeChildCustody(process.pid)
        except BaseException:
            from agent_procutil._caller_owned import _confirm_group
            from agent_procutil.owned_process import _retire_posix_group

            _retire_posix_group(process)
            _confirm_group(process)
            process.wait(timeout=2)
            raise
        try:
            deadline = asyncio.get_running_loop().time() + 10
            while not stop.is_set():
                self.custody.assert_owned()
                exited = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                check(exited is None, "native-relay-exited")
                try:
                    listening = owns_listener(
                        process.pid, self.selected["listen_host"], self.selected["port"],
                    )
                except (FileNotFoundError, PermissionError) as exc:
                    exited = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    check(exited is None, "native-relay-exited")
                    raise SSHServiceError("native-relay-listener-unobservable") from exc
                if listening:
                    ready.set()
                check(ready.is_set() or asyncio.get_running_loop().time() < deadline,
                      "native-relay-listener-unconfirmed")
                check(not ready.is_set() or listening, "native-relay-listener-lost")
                try:
                    await asyncio.wait_for(stop.wait(), 0.1 if not ready.is_set() else 1)
                except asyncio.TimeoutError:
                    pass
        finally:
            process.returncode = self.custody.retire()
            logging.getLogger(__name__).info("ssh-native-relay-stopped code=owned-group-settled")
