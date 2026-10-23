"""Opt-in disposable KeePass/CLI proof; never targets an installed daemon."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import site
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs

from agent_vault.keepassxc import KeePassXCBackend
from agent_vault.rendezvous import read_endpoint_strict

ENTRY = "Identity/PR-attribution"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


@dataclass
class DisposableVault:
    """Importable fixture boundary for the caller's combined AW integration."""

    root: Path
    env: dict[str, str]
    argv: list[str]
    database: str
    process: subprocess.Popen = field(repr=False)
    expected_key: str | None = field(repr=False)
    master_password: str = field(repr=False)
    entry: str = ENTRY

    def request(self, payload: dict) -> dict:
        endpoint = read_endpoint_strict(Path(self.env.get("AGENT_VAULT_RUN_DIR", self.root / "run")))
        require(endpoint is not None, "Disposable endpoint not ready")
        require(endpoint.pid == self.process.pid, "Refusing a non-fixture daemon")
        tcp = endpoint.usable(lambda transport: transport == "tcp")
        with contextlib.ExitStack() as stack:
            if tcp is not None:
                host, port = tcp.tcp_host_port
                require(host == "127.0.0.1" and port > 0,
                        "Refusing a non-loopback fixture endpoint")
                connection = stack.enter_context(
                    socket.create_connection((host, port), timeout=10)
                )
            else:
                require(os.name != "nt", "Disposable Windows daemon has no TCP endpoint")
                unix = endpoint.usable(lambda transport: transport == "unix")
                require(unix is not None, "Disposable daemon has no local Unix endpoint")
                require(Path(unix.address).resolve().is_relative_to(self.root),
                        "Refusing a Unix socket outside the fixture")
                connection = stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
                connection.settimeout(10)
                connection.connect(unix.address)
            connection.sendall((json.dumps(payload) + "\n").encode())
            with connection.makefile("rb") as stream:
                return json.loads(stream.readline())

    def run(self, *args: str, env: dict[str, str] | None = None):
        return subprocess.run(
            [*self.argv, *args], env=env or self.env, cwd=self.root,
            capture_output=True, text=True, timeout=30,
            **no_window_kwargs(),
        )


@contextlib.contextmanager
def disposable_vault(
    root: Path, *, seed_owner: bool = True, runtime_environment: dict[str, str] | None = None,
):
    """Yield an isolated live fixture, then retire only its owned child."""
    backend = KeePassXCBackend()
    if not backend.available:
        pytest.skip("KeePassXC CLI is not available")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    env = {
        name: value for name, value in os.environ.items()
        if not name.startswith(("AGENT_VAULT", "VAULT_", "IDENTITY_", "AGENT_WORKTREES_"))
        and name not in ("KPDB", "COPILOT_PLUGIN_ROOT")
    }
    env["PYTHONPATH"] = os.pathsep.join([
        env.get("PYTHONPATH", ""), *site.getsitepackages(),
    ])
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
        location = root / name.lower()
        location.mkdir(exist_ok=True)
        env[name] = str(location)
    installation = uuid.uuid4().hex
    database = str(root / "disposable.kdbx")
    env.update({
        "KPDB": database,
        "VAULT_GROUP": "",
        "AGENT_VAULT": "",
        "AGENT_VAULT_HOME": str(root / "vault-home"),
        "AGENT_VAULT_INSTALLATION_ID": installation,
        "AGENT_VAULT_CONFIG": str(root / "vault-config.json"),
        "AGENT_VAULT_RUN_DIR": str(root / "run"),
        "AGENT_VAULT_PORT": "0",
        "AGENT_VAULT_CACHE": "1",
        "AGENT_VAULT_CACHE_DIR": str(root / "cache-root"),
        "AGENT_VAULT_PID": str(root / "daemon.pid"),
        "AGENT_VAULT_LOG": str(root / "daemon.log"),
        "AGENT_VAULT_PIPE": rf"\\.\pipe\agent-vault-fixture-{installation}",
        "AGENT_VAULT_SOCKET": str(root / "daemon.sock"),
        "AGENT_VAULT_HOST": "127.0.0.1",
        "AGENT_VAULT_WINDOWS_RUN_DIR": str(root / "absent-windows-run"),
        "VAULT_NONINTERACTIVE": "1",
    })
    env.update(runtime_environment or {})
    for name in ("AGENT_VAULT_PID", "AGENT_VAULT_LOG"):
        Path(env[name]).parent.mkdir(parents=True, exist_ok=True)
    master = base64.b64encode(os.urandom(32)).decode()
    created = subprocess.run(
        [backend._cli_path, "db-create", "-q", "-p", "-t", "100", database],
        input=master + "\n" + master + "\n", capture_output=True,
        text=True, env=env, cwd=root, timeout=15,
        **no_window_kwargs(),
    )
    require(created.returncode == 0, "Disposable database creation failed")
    backend.set_password(database, master)
    expected_key = None
    if seed_owner:
        expected_key = base64.b64encode(os.urandom(32)).decode()
        backend.create_identity_key(database, ENTRY, expected_key, time.monotonic() + 15)
        observed = backend.read_identity_key(database, ENTRY, time.monotonic() + 10)
        require(observed == expected_key, "Disposable seed did not persist authoritatively")
    # Retain base-executable PID custody while loading the test venv's .pth files.
    bootstrap = (
        "import runpy,site;"
        f"[site.addsitedir(path) for path in {site.getsitepackages()!r}];"
        "runpy.run_module('agent_vault.service',run_name='__main__',alter_sys=True)"
    )
    process = subprocess.Popen(
        [
            sys._base_executable if os.name == "nt" else sys.executable,
            "-c", bootstrap, "--foreground", "--tcp-port", "0",
        ],
        env=env, cwd=root, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **no_window_kwargs(),
    )
    fixture = DisposableVault(
        root, env, [sys.executable, "-m", "agent_vault"], database,
        process, expected_key, master,
    )
    try:
        deadline = time.monotonic() + 10
        while not (Path(env["AGENT_VAULT_RUN_DIR"]) / "endpoint.json").exists():
            require(process.poll() is None, "Disposable daemon exited before readiness")
            require(time.monotonic() < deadline, "Disposable daemon readiness timed out")
            time.sleep(0.05)
        ready = fixture.request({"action": "ping", "kpdb": database})
        require(ready.get("ok") is True, "Disposable daemon not responsive")
        unlocked = fixture.request({"action": "unlock", "kpdb": database, "password": master})
        require(unlocked.get("ok") is True, "Disposable unlock failed")
        yield fixture
    finally:
        if process.poll() is None:
            try:
                fixture.request({"action": "stop"})
                process.wait(timeout=5)
            except (OSError, RuntimeError, AssertionError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        fixture.master_password = ""


@pytest.mark.skipif(
    os.environ.get("AGENT_VAULT_IDENTITY_REAL_KEEPASS") != "1",
    reason="opt-in disposable real KeePass proof",
)
def test_real_existing_owner_cli_setup_and_locked_fresh_reader(tmp_path):
    with disposable_vault(tmp_path / "fixture") as fixture:
        context = fixture.run("identity-key", "context", "--json")
        require(context.returncode == 0, "Disposable context failed")
        metadata = json.loads(context.stdout)
        scoped = dict(fixture.env, **metadata["env"])
        cache_root = Path(metadata["env"]["AGENT_VAULT_CACHE_DIR"])
        require(not cache_root.exists(), "Context created protected cache state")
        setup = fixture.run("identity-key", "setup", "--entry", ENTRY, "--json", env=scoped)
        result = json.loads(setup.stdout)
        if setup.returncode != 0:
            code = result.get("error")
            safe_codes = {
                "needs_unlock", "entry_missing", "malformed_key", "ambiguous_entry",
                "vault_read_failed", "service_unavailable", "cache_unavailable",
                "cache_read_failed", "cache_update_failed", "context_invalid",
            }
            require(False, "Disposable CLI setup failed: " + (
                code if isinstance(code, str) and code in safe_codes else "invalid_response"
            ))
        require(json.loads(setup.stdout) == {
            "version": 1, "status": "reused", "cached": True,
        }, "Unexpected disposable setup contract")
        require(fixture.expected_key not in setup.stdout + setup.stderr,
                "Setup exposed fixture key material")
        locked = fixture.request({"action": "lock", "kpdb": fixture.database})
        require(locked.get("ok") is True, "Disposable lock failed")
        read = fixture.run("get", ENTRY, "password", "--cache-only", env=scoped)
        require(read.returncode == 0, "Fresh locked cache-only read failed")
        require(read.stdout.strip() == fixture.expected_key, "Fresh reader selected another key")
        log = (fixture.root / "daemon.log").read_text(encoding="utf-8")
        require(fixture.expected_key not in log, "Daemon log exposed fixture key material")


@pytest.mark.skipif(
    os.environ.get("AGENT_VAULT_IDENTITY_REAL_KEEPASS") != "1",
    reason="opt-in disposable real KeePass proof",
)
def test_real_first_owner_is_explicit_create_only_and_retry_reuses(tmp_path):
    with disposable_vault(tmp_path / "fixture", seed_owner=False) as fixture:
        context = fixture.run("identity-key", "context", "--json")
        require(context.returncode == 0, "Disposable context failed")
        scoped = dict(fixture.env, **json.loads(context.stdout)["env"])
        missing = fixture.run("identity-key", "setup", "--entry", ENTRY, "--json", env=scoped)
        require(missing.returncode != 0, "Ordinary setup initialized a missing owner")
        require(json.loads(missing.stdout).get("error") == "entry_missing",
                "Missing owner was not distinguished from read failure")
        created = fixture.run(
            "identity-key", "setup", "--entry", ENTRY, "--json",
            "--initialize-owner", "--confirm-first-owner", env=scoped,
        )
        require(created.returncode == 0, "Disposable first-owner initialization failed")
        require(json.loads(created.stdout).get("status") == "created",
                "First owner was not created explicitly")
        first = fixture.run("get", ENTRY, "password", "--cache-only", env=scoped)
        require(first.returncode == 0, "Created owner not available to fresh cache reader")
        key = first.stdout.strip()
        require(len(base64.b64decode(key, validate=True)) == 32, "Created owner key is malformed")
        repeated = fixture.run("identity-key", "setup", "--entry", ENTRY, "--json", env=scoped)
        require(repeated.returncode == 0, "Ordinary repeat setup failed")
        require(json.loads(repeated.stdout).get("status") == "reused", "Repeat setup rotated owner")
        second = fixture.run("get", ENTRY, "password", "--cache-only", env=scoped)
        require(second.returncode == 0 and second.stdout.strip() == key,
                "Repeat setup changed owner key material")
        require(key not in created.stdout + repeated.stdout, "Setup JSON exposed fixture owner key")


@pytest.mark.skipif(
    os.environ.get("AGENT_VAULT_IDENTITY_REAL_KEEPASS") != "1",
    reason="opt-in disposable real KeePass proof",
)
def test_independent_services_same_database_return_and_cache_one_owner(tmp_path):
    program = """
import hashlib,json,sys,time
from pathlib import Path
from agent_vault import keepassxc
from agent_vault.service import VaultService
data=json.loads(sys.stdin.readline())
service=VaultService()
service.cli.set_password(data['database'],data['master'])
original_read=service.cli.read_identity_key
first_read=[True]
def synchronized_read(*args):
 value=original_read(*args)
 if first_read[0]:
  first_read[0]=False
  if value is not None: raise RuntimeError('fixture entry was not absent')
  Path(data['ready']).write_text('ready',encoding='ascii')
  if sys.stdin.readline().strip()!='GO': raise RuntimeError('fixture barrier failed')
 return value
service.cli.read_identity_key=synchronized_read
original_run=keepassxc.subprocess.run
adds=[0]
def observed_run(argv,**kwargs):
 if len(argv)>1 and argv[1]=='add': adds[0]+=1
 return original_run(argv,**kwargs)
keepassxc.subprocess.run=observed_run
result=service.handle_request({
 'action':'identity-key-setup','kpdb':data['database'],'entry':data['entry'],
 'group':'','initialize_owner':True,'confirm_first_owner':True,
})
receipt={'ok':result.get('ok'),'status':result.get('status'),'code':result.get('code'),'adds':adds[0]}
if result.get('ok'):
 cached=service.cache[(data['database'],data['entry'],'password')]
 receipt['digest']=hashlib.sha256(result['value'].encode()).hexdigest()
 receipt['cached_digest']=hashlib.sha256(cached.encode()).hexdigest()
print(json.dumps(receipt))
"""
    with disposable_vault(tmp_path / "fixture", seed_owner=False) as fixture:
        backend = KeePassXCBackend()
        backend.set_password(fixture.database, fixture.master_password)
        parent = backend._run(
            fixture.database, ["mkdir", "-q", fixture.database, "Identity"],
        )
        require(parent is not None and parent.returncode == 0, "Fixture parent group failed")
        workers = []
        try:
            for index in range(2):
                env = dict(fixture.env)
                env["AGENT_VAULT_HOME"] = str(fixture.root / f"independent-cell-{index}")
                env["AGENT_VAULT_INSTALLATION_ID"] = uuid.uuid4().hex
                worker = subprocess.Popen(
                    [sys.executable, "-c", program],
                    env=env, cwd=fixture.root, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    **no_window_kwargs(),
                )
                workers.append(worker)
                worker.stdin.write(json.dumps({
                    "database": fixture.database, "master": fixture.master_password,
                    "entry": ENTRY, "ready": str(fixture.root / f"worker-{index}.ready"),
                }) + "\n")
                worker.stdin.flush()
            deadline = time.monotonic() + 10
            while not all((fixture.root / f"worker-{index}.ready").exists() for index in range(2)):
                require(all(worker.poll() is None for worker in workers),
                        "Independent fixture worker exited before barrier")
                require(time.monotonic() < deadline, "Independent fixture barrier timed out")
                time.sleep(0.05)
            for worker in workers:
                worker.stdin.write("GO\n")
                worker.stdin.flush()
            receipts = []
            for worker in workers:
                stdout, _stderr = worker.communicate(timeout=20)
                require(worker.returncode == 0, "Independent fixture worker failed")
                receipt = json.loads(stdout)
                require(receipt.get("ok") is True, "Independent initialization did not succeed")
                receipts.append(receipt)
            require(sorted(receipt["status"] for receipt in receipts) == ["created", "reused"],
                    "Independent services did not converge on one first owner")
            require(sum(receipt["adds"] for receipt in receipts) == 1,
                    "Independent services issued competing KeePass snapshot adds")
            context = fixture.run("identity-key", "context", "--json")
            require(context.returncode == 0, "Disposable context failed")
            env = dict(fixture.env, **json.loads(context.stdout)["env"])
            setup = fixture.run("identity-key", "setup", "--entry", ENTRY, "--json", env=env)
            require(setup.returncode == 0, "Final authoritative population failed")
            final = fixture.run("get", ENTRY, "password", "--cache-only", env=env)
            require(final.returncode == 0, "Final fresh cached read failed")
            digest = hashlib.sha256(final.stdout.strip().encode()).hexdigest()
            require(all(
                receipt["digest"] == receipt["cached_digest"] == digest for receipt in receipts
            ), "An independent service returned or cached a subsequently overwritten key")
        finally:
            for worker in workers:
                if worker.poll() is None:
                    worker.terminate()
                    worker.wait(timeout=5)


@pytest.mark.parametrize("case", ["owned", "foreign-socket", "wrong-pid"])
def test_fixture_unix_fallback_is_pid_checked_and_path_scoped(monkeypatch, tmp_path, case):
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "os", SimpleNamespace(name="posix"))
    address = tmp_path / "fixture.sock"
    if case == "foreign-socket":
        address = tmp_path.parent / "foreign.sock"
    unix = SimpleNamespace(address=str(address))
    endpoint = SimpleNamespace(
        pid=43 if case == "wrong-pid" else 42,
        usable=lambda predicate: unix if predicate("unix") else None,
    )
    monkeypatch.setattr(module, "read_endpoint_strict", lambda path: endpoint)
    operations = []

    class FixtureSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            operations.append("closed")

        def settimeout(self, seconds):
            operations.append(("timeout", seconds))

        def connect(self, target):
            operations.append(("connect", target))

        def sendall(self, data):
            operations.append(("send", json.loads(data)))

        def makefile(self, mode):
            return io.BytesIO(b'{"ok":true}\n')

    monkeypatch.setattr(socket, "socket", lambda *args: FixtureSocket())
    monkeypatch.setattr(socket, "AF_UNIX", getattr(socket, "AF_UNIX", -1), raising=False)
    fixture = DisposableVault(
        tmp_path, {}, [], str(tmp_path / "fixture.kdbx"),
        SimpleNamespace(pid=42), None, "",
    )
    if case == "owned":
        assert fixture.request({"action": "ping"}) == {"ok": True}
        assert ("connect", str(address)) in operations
        assert ("send", {"action": "ping"}) in operations
        assert operations[-1] == "closed"
    else:
        with pytest.raises(AssertionError, match="Refusing"):
            fixture.request({"action": "ping"})
        assert operations == []
