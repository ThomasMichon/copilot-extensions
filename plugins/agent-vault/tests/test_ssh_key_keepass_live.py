"""Opt-in real Vault/CLI contract using only disposable local credentials."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_vault import ssh_key
from agent_vault.keepassxc import KeePassXCBackend
from .test_identity_key_keepass_live import disposable_vault, require

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_VAULT_SSH_REAL_KEEPASS") != "1",
    reason="opt-in disposable SSH key/KeePass proof",
)
ENTRY = "SSH/node-a"


@pytest.fixture(autouse=True)
def hidden_fixture_children(monkeypatch):
    original = subprocess.Popen

    def hidden(*args, **kwargs):
        kwargs.setdefault("creationflags", no_window_kwargs().get("creationflags", 0))
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", hidden)


def test_real_whole_pair_creation_public_only_and_fresh_process_reuse(tmp_path):
    with disposable_vault(tmp_path / "fixture", seed_owner=False) as fixture:
        command = ("ssh-key", "prepare", "--entry", ENTRY, "--require-running", "--json")
        missing = fixture.run(*command)
        require(missing.returncode == 1, "Ordinary preparation initialized a missing identity")
        require(json.loads(missing.stdout).get("error") == "entry_missing", "Absence was not explicit")
        created = fixture.run(*command, "--initialize", "--confirm-initialization")
        require(created.returncode == 0, "Disposable first creation failed")
        metadata = json.loads(created.stdout)
        require(metadata.get("status") == "created", "First identity was not created explicitly")
        backend = KeePassXCBackend()
        backend.set_password(fixture.database, fixture.master_password)
        value = backend.read_identity_key(fixture.database, ENTRY, time.monotonic() + 10)
        require(value is not None, "Whole key record did not persist")
        require(ssh_key._read_record(value) == {
            name: metadata[name] for name in ("public_key", "fingerprint")
        }, "Persisted private/public key pair did not match public output")
        before = hashlib.sha256(Path(fixture.database).read_bytes()).digest()
        repeated = fixture.run(*command)
        require(repeated.returncode == 0, "Fresh reuse process failed")
        observed = json.loads(repeated.stdout)
        require(observed == dict(metadata, status="reused"), "Fresh reuse changed public identity")
        require(hashlib.sha256(Path(fixture.database).read_bytes()).digest() == before,
                "Ordinary reuse rewrote the encrypted database")
        log = (fixture.root / "daemon.log").read_text(encoding="utf-8")
        require(value not in created.stdout + repeated.stdout + log, "Protected record leaked")
        require("BEGIN OPENSSH PRIVATE KEY" not in created.stdout + repeated.stdout + log,
                "Private key material leaked")
        require(not list(fixture.root.rglob("id_ed25519*")), "Preparation wrote plaintext key files")
        require(not (fixture.root / "cache-root").exists(), "Preparation populated a local cache")
        locked = fixture.request({"action": "lock", "kpdb": fixture.database})
        require(locked.get("ok") is True, "Disposable lock failed")
        locked_read = fixture.run(*command)
        require(locked_read.returncode == 1, "Locked preparation unexpectedly succeeded")
        require(json.loads(locked_read.stdout).get("error") == "needs_unlock", "Lock was not explicit")


def test_real_legacy_attachment_entry_is_not_replaced(tmp_path):
    with disposable_vault(tmp_path / "fixture", seed_owner=False) as fixture:
        backend = KeePassXCBackend()
        backend.set_password(fixture.database, fixture.master_password)
        added, _ = backend.add_entry(fixture.database, ENTRY)
        require(added, "Disposable legacy entry creation failed")
        imported, _ = backend.import_attachment(fixture.database, ENTRY, "legacy.pub", b"fixture only")
        require(imported, "Disposable legacy attachment failed")
        before = hashlib.sha256(Path(fixture.database).read_bytes()).digest()
        result = fixture.run(
            "ssh-key", "prepare", "--entry", ENTRY, "--initialize", "--confirm-initialization",
            "--require-running", "--json",
        )
        require(result.returncode == 1, "Legacy entry was adopted as a new SSH identity")
        require(json.loads(result.stdout).get("error") == "malformed_key", "Legacy conflict was hidden")
        require(hashlib.sha256(Path(fixture.database).read_bytes()).digest() == before,
                "Preparation replaced a legacy entry")


def test_real_local_installation_fresh_reuse_recovery_and_locked_refusal(tmp_path):
    with disposable_vault(tmp_path / "fixture", seed_owner=False) as fixture:
        destination = fixture.root / "local keys"
        command = (
            "ssh-key", "install", "--entry", ENTRY, "--directory", str(destination),
            "--require-running", "--json",
        )
        missing = fixture.run(*command)
        require(missing.returncode == 1, "Installation initialized a missing identity")
        require(json.loads(missing.stdout).get("error") == "entry_missing", "Absence was not explicit")
        require(not destination.exists(), "Missing record created local key state")
        created = fixture.run(
            "ssh-key", "prepare", "--entry", ENTRY, "--require-running",
            "--initialize", "--confirm-initialization", "--json",
        )
        require(created.returncode == 0, "Disposable identity initialization failed")
        public = json.loads(created.stdout)["public_key"]
        database_before = hashlib.sha256(Path(fixture.database).read_bytes()).digest()
        installed = fixture.run(*command)
        require(installed.returncode == 0, "Disposable native key installation failed")
        result = json.loads(installed.stdout)
        require(result.get("status") == "installed", "First materialization was not reported")
        key = Path(result["private_key_path"])
        key_before = key.stat().st_mtime_ns
        repeated = fixture.run(*command)
        require(repeated.returncode == 0, "Fresh installation reader failed")
        require(json.loads(repeated.stdout).get("status") == "reused", "Fresh reader did not reuse")
        require(key.stat().st_mtime_ns == key_before, "Reuse rewrote the private key")
        Path(result["public_key_path"]).unlink()
        recovered = fixture.run(*command)
        require(recovered.returncode == 0, "Matching private key recovery failed")
        require(json.loads(recovered.stdout).get("status") == "recovered", "Recovery was not explicit")
        require(key.stat().st_mtime_ns == key_before, "Recovery rewrote the private key")
        executable = shutil.which("ssh-keygen")
        require(executable is not None, "Native OpenSSH prerequisite unavailable for live qualification")
        derived = subprocess.run(
            [executable, "-y", "-f", str(key)], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=10, **no_window_kwargs(),
        )
        require(derived.returncode == 0, "Native OpenSSH refused the installed private key")
        require(derived.stdout.strip() == public, "Native OpenSSH derived a different identity")
        require(hashlib.sha256(Path(fixture.database).read_bytes()).digest() == database_before,
                "Installation rewrote the encrypted database")
        locked = fixture.request({"action": "lock", "kpdb": fixture.database})
        require(locked.get("ok") is True, "Disposable lock failed")
        refused = fixture.run(*command)
        require(refused.returncode == 1, "Installation bypassed the locked owner using local files")
        require(json.loads(refused.stdout).get("error") == "needs_unlock", "Lock was not explicit")
        output = installed.stdout + installed.stderr + repeated.stdout + recovered.stdout
        output += (fixture.root / "daemon.log").read_text(encoding="utf-8")
        require("BEGIN OPENSSH PRIVATE KEY" not in output, "Private key material leaked")
        require(not (fixture.root / "cache-root").exists(), "Installation created a protected cache")
