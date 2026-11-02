"""Real Windows publishers retain whole-action custody inside stamp/build phases."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "legacy_publisher_native_fixture", PLUGIN / "tests/test_legacy_runtime_uninstall.py",
)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Real Windows native publisher proof")


def _host() -> str:
    assert native.HOSTS, "An existing PowerShell host is required"
    return native.HOSTS[0]


def _start(host: str, root: Path, env: dict[str, str], action: str) -> subprocess.Popen[str]:
    assert root == Path(env["HOME"]) / ".agent-machines"
    return subprocess.Popen(  # noqa: S603 -- exact native publisher and fixture root
        [
            host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(PLUGIN / "scripts/init.ps1"), "-Action", action,
        ],
        env=env, cwd=native.ROOT, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _observe_owned_transaction(root: Path, publisher: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline and publisher.poll() is None:
        try:
            with (root / ".install.lock").open("r+b"):
                pass
        except PermissionError:
            return
        except FileNotFoundError:
            pass
        time.sleep(0.02)
    raise AssertionError("Actual native publisher never held the shared transaction")


def test_actual_stamp_retains_transaction_inside_stamp_helper(tmp_path: Path) -> None:
    host = _host()
    env = native._environment(tmp_path / "home")
    root = Path(env["HOME"]) / ".agent-machines"
    root.mkdir()
    digest = hashlib.sha256(str(root).lower().encode("utf-8")).hexdigest().upper()[:24]
    mutex = "Local\\CopilotExtensions.AgentMachines.Stamp." + digest
    script = (
        f"$m=[Threading.Mutex]::new($false,'{mutex}'); [void]$m.WaitOne();"
        "[Console]::Out.WriteLine('held'); [Console]::Out.Flush();"
        "[Console]::In.ReadLine() | Out-Null; $m.ReleaseMutex(); $m.Dispose()"
    )
    holder = subprocess.Popen(  # noqa: S603 -- exact owned per-stamp helper barrier
        [host, "-NoProfile", "-NonInteractive", "-Command", script], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    publisher = None
    try:
        assert holder.stdout.readline().strip() == "held"
        publisher = _start(host, root, env, "stamp")
        _observe_owned_transaction(root, publisher)
        contender = native._run(host, None, env, action="stamp-binstub")
        assert contender.returncode != 0
        assert "transaction lock" in contender.stdout + contender.stderr
        assert publisher.poll() is None
        assert not (root / "payload-dir").exists()
    finally:
        holder.communicate("\n", timeout=10)
        if publisher is not None:
            stdout, stderr = publisher.communicate(timeout=35)
            assert publisher.returncode == 0, stdout + stderr
    assert (root / "payload-dir").is_file()
    assert (Path(env["HOME"]) / ".local/bin/agent-machines.cmd").is_file()
    with (root / ".install.lock").open("r+b"):
        pass


def test_actual_constructor_retains_transaction_until_failed_admission(tmp_path: Path) -> None:
    host = _host()
    env = native._environment(tmp_path / "home")
    assert shutil.which("uv", path=env["PATH"]), "Existing uv required; never install packages"
    assert not (PLUGIN / "runtime-source.json").exists(), (
        "This release-layout control requires an unambiguous package generation"
    )
    package = (PLUGIN / "pyproject.toml").read_text(encoding="utf-8")
    matched = re.search(r'^\s*version\s*=\s*"([^"]+)"', package, re.MULTILINE)
    assert matched
    version = matched.group(1)
    root = Path(env["HOME"]) / ".agent-machines"
    root.mkdir()
    leases = native._component("versioned_runtime_leases")
    with leases.construction_lease(root, version, timeout=0):
        publisher = _start(host, root, env, "provision")
        contender = None
        still_running = False
        candidate_exists = False
        try:
            _observe_owned_transaction(root, publisher)
            contender = native._run(host, None, env, action="stamp-binstub")
            still_running = publisher.poll() is None
            candidate_exists = (root / "versions" / version).exists()
        finally:
            # The canonical constructor's finite lease deadline expires while
            # this real holder remains held. No package build can be admitted.
            stdout, stderr = publisher.communicate(timeout=90)
        diagnostic = stdout + stderr
        assert contender is not None
        assert contender.returncode != 0, diagnostic + contender.stdout + contender.stderr
        assert "transaction lock" in contender.stdout + contender.stderr, diagnostic
        assert still_running, diagnostic
        assert not candidate_exists, diagnostic
        assert publisher.returncode != 0
        assert "construction" in diagnostic.lower(), diagnostic
        assert not (root / "versions" / version).exists()
    with (root / ".install.lock").open("r+b"):
        pass
