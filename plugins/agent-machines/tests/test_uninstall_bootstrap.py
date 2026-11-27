"""Prepared-only legacy removal with real finite ownership and isolated state."""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

from .managed_command_fixture import HEADLESS, snapshot
from .legacy_uninstall_fixture import isolate_process_observation
from .test_cell_lifecycle_bootstrap import LifecycleFixture
from .test_installer_bootstrap import (
    bootstrap as bootstrap,
    installer as installer,
    installer_python as installer_python,
    lifecycle as lifecycle,
)
from .test_legacy_runtime_uninstall import _component, _fixture, _snapshot

pytestmark = [
    pytest.mark.portfolio_tier("T2"), pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
]


@pytest.fixture
def removal(installer: LifecycleFixture) -> LifecycleFixture:
    scripts = installer.payload / "scripts"
    profile = scripts / "uninstall-profile.json"
    declaration = json.loads(profile.read_text())
    module = "finite_fixture_" + hashlib.sha256(str(installer.payload).encode()).hexdigest()[:16]
    declaration["module"] = module
    profile.write_text(json.dumps(declaration) + "\n")
    package = installer.payload / "src" / module
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    isolate_process_observation(installer.payload)
    if os.name != "nt":
        executable = shutil.which("ps")
        assert executable
        (installer.tools / "ps").symlink_to(executable)
    return installer


def _run(fixture: LifecycleFixture, root: Path) -> subprocess.CompletedProcess[str]:
    scripts = fixture.payload / "scripts"
    if fixture.host == "bash":
        command = [fixture.shell, str(scripts / "init.sh"), "uninstall",
                   "--install-dir", str(root)]
    else:
        command = [fixture.shell, "-NoProfile", "-File", str(scripts / "init.ps1"),
                   "-Action", "uninstall", "-InstallDir", str(root)]
    return subprocess.run(
        command, capture_output=True, text=True, env=fixture.env, cwd=fixture.home,
        timeout=35, creationflags=HEADLESS,
    )


def _inert_remover(fixture: LifecycleFixture) -> None:
    (fixture.payload / "scripts/installer_engine_uninstall.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "Path(os.environ['LIFECYCLE_PROOF']).write_text('unexpected removal entry')\n"
        "raise SystemExit(93)\n",
    )


def _assert_no_acquisition(fixture: LifecycleFixture) -> None:
    calls = fixture.calls.read_text().splitlines() if fixture.calls.exists() else []
    assert not any("install" in call or "pip" in call for call in calls)
    assert not fixture.poison.exists()


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
def test_uninstall_reuses_prepared_python_and_preserves_durable_state(
    removal: LifecycleFixture, record_property: Callable[[str, object], None],
) -> None:
    if os.name != "nt" and removal.host != "bash":
        pytest.skip("POSIX finite cleanup requires Bash; PowerShell removal is native Windows")
    root = removal.home / "runtime"
    durable = _fixture(root, payload=removal.payload)
    payload_snapshot = _snapshot(root)
    leases = _component("versioned_runtime_leases")
    with leases.batch_removal_custody(root, ["1.2.3"]):
        pass
    with leases.batch_removal_custody(root / "snapshots", ["1.2.3"]):
        pass
    persistent = {
        path: (path.stat().st_dev, path.stat().st_ino)
        for directory in (root, root / "snapshots")
        for path in directory.glob(".*-leases/*.lock")
    }
    assert persistent
    samples = []
    for _ in range(2):
        started = time.monotonic()
        result = _run(removal, root)
        samples.append(time.monotonic() - started)
        assert result.returncode == 0, result.stdout + result.stderr
        assert {name: (root / name).read_bytes() for name in durable} == durable
    assert not (root / "versions/1.2.3").exists()
    assert not payload_snapshot.exists()
    assert not (root / "deploy-manifest.json").exists()
    assert {path: (path.stat().st_dev, path.stat().st_ino) for path in persistent} == persistent
    _assert_no_acquisition(removal)
    calls = removal.calls.read_text().splitlines()
    assert calls.count(
        "python find --no-project --system --managed-python --no-python-downloads cpython@3.11.18",
    ) == 2
    uv_calls = sum(call == "--version" or call.startswith("python ") for call in calls)
    assert uv_calls == 6
    record_property("machines_uninstall_bootstrap", json.dumps({
        "shell": removal.host, "materialized": removal.support == removal.payload / "scripts",
        "samples_seconds": samples, "uv_calls": uv_calls, "managed_lookups": 2,
        "network": "disabled",
        "process_observation": "native-current-user" if os.name == "nt" else "fixture-owned",
    }, sort_keys=True))


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
def test_uninstall_missing_uv_refuses_before_removal_without_ambient_fallback(
    removal: LifecycleFixture,
) -> None:
    root = removal.home / "runtime"
    _fixture(root, payload=removal.payload)
    _inert_remover(removal)
    uv = next(path for path in removal.tools.iterdir() if path.stem == "uv")
    uv.rename(uv.with_name("unavailable-uv" + uv.suffix))
    before = snapshot(root)
    result = _run(removal, root)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Prepared uv" in result.stdout + result.stderr
    assert snapshot(root) == before
    assert not removal.proof.exists()
    _assert_no_acquisition(removal)


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
def test_uninstall_retains_live_generation_lease_refusal(
    removal: LifecycleFixture,
) -> None:
    if os.name != "nt" and removal.host != "bash":
        pytest.skip("POSIX finite cleanup requires Bash; PowerShell removal is native Windows")
    root = removal.home / "runtime"
    _fixture(root, payload=removal.payload)
    (root / ".install.lock").touch()
    leases = _component("versioned_runtime_leases")
    with leases.batch_removal_custody(root, ["1.2.3"]):
        pass
    before = snapshot(root)
    with leases.batch_removal_custody(root, ["1.2.3"]):
        result = _run(removal, root)
        assert result.returncode != 0, result.stdout + result.stderr
        diagnostic = (result.stdout + result.stderr).lower()
        assert any(word in diagnostic for word in (
            "custody", "lease", "construction", "retention", "publication",
        )), diagnostic
    assert snapshot(root) == before
    _assert_no_acquisition(removal)


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
def test_uninstall_refuses_unavailable_process_observation(
    removal: LifecycleFixture,
) -> None:
    if os.name != "nt" and removal.host != "bash":
        pytest.skip("POSIX finite cleanup requires Bash; PowerShell removal is native Windows")
    root = removal.home / "runtime"
    _fixture(root, payload=removal.payload)
    _snapshot(root)
    (root / ".install.lock").touch()
    leases = _component("versioned_runtime_leases")
    for directory in (root, root / "snapshots"):
        with leases.batch_removal_custody(directory, ["1.2.3"]):
            pass
    with leases._kernel_lease(root, "redirects", kind="publication", shared=False, timeout=0):
        pass
    before = snapshot(root)
    removal.env["MACHINES_TEST_CENSUS_UNAVAILABLE"] = "1"
    result = _run(removal, root)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Fixture-owned process census is unavailable" in result.stdout + result.stderr
    assert snapshot(root) == before
    _assert_no_acquisition(removal)


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
def test_uninstall_rejects_selected_python_within_removal_root(
    removal: LifecycleFixture,
) -> None:
    root = removal.home / "runtime"
    _fixture(root, payload=removal.payload)
    _inert_remover(removal)
    environment = root / "candidate"
    result = subprocess.run(
        [str(removal.python), "-I", "-B", "-m", "venv", "--without-pip", str(environment)],
        capture_output=True, text=True, env=removal.env, cwd=removal.home,
        timeout=20, creationflags=HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    selected = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    removal.env["MACHINES_TEST_SELECTED_PYTHON"] = str(selected)
    suffix = "sh" if removal.host == "bash" else "ps1"
    provider = removal.support / f"bootstrap-uv.{suffix}"
    text = provider.read_text()
    if removal.host == "bash":
        needle = '    exec "$@"\n'
        override = (
            '    if [[ "${2:-}" == python && "${3:-}" == find ]]; then\n'
            '        printf "%s\\n" "$MACHINES_TEST_SELECTED_PYTHON"; return 0\n'
            '    fi\n'
        )
    else:
        needle = "    $result = & $InvokeCaptured @options\n"
        override = (
            "    if ($Arguments[0] -eq 'python' -and $Arguments[1] -eq 'find') {\n"
            "        return [pscustomobject]@{ExitCode=0; "
            "Stdout=$env:MACHINES_TEST_SELECTED_PYTHON; Stderr=''}\n"
            "    }\n"
        )
    assert text.count(needle) == 1
    provider.write_text(text.replace(needle, override + needle))
    before = snapshot(root)
    result = _run(removal, root)
    assert result.returncode != 0, result.stdout + result.stderr
    diagnostic = result.stdout + result.stderr
    assert "outside runtime" in diagnostic and "prefix closure" in diagnostic
    assert snapshot(root) == before
    assert not removal.proof.exists()
    _assert_no_acquisition(removal)
