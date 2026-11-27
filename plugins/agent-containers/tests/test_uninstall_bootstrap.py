"""Prepared-only Containers removal through real isolated native helpers."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import time

import pytest

from .test_containers_legacy_runtime_uninstall import cases

pytestmark = [
    pytest.mark.portfolio_tier("T2"), pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
]


@dataclass
class Removal:
    payload: Path
    root: Path
    env: dict[str, str]
    calls: Path
    poison: Path
    uv: Path
    durable: dict[str, bytes]


@pytest.fixture
def removal(tmp_path: Path) -> Removal:
    payload = Path(cases._isolated_payload(tmp_path))
    root = tmp_path / "runtime"
    durable: dict[str, bytes] = cases._fixture(root, payload=payload)
    env: dict[str, str] = cases._environment(tmp_path / "home")
    tools = tmp_path / "tools"
    tools.mkdir()
    actual_uv = shutil.which("uv")
    assert actual_uv
    uv = tools / ("uv.exe" if os.name == "nt" else "uv")
    shutil.copy2(actual_uv, uv)
    poison = tmp_path / "ambient-python-used"
    for name in ("python", "python3", "py"):
        entry = tools / (name + (".exe" if os.name == "nt" else ""))
        entry.write_bytes(
            b"not an executable interpreter\n" if os.name == "nt" else
            b'#!/bin/sh\nprintf forbidden > "$CONTAINERS_TEST_AMBIENT_USED"\nexit 97\n'
        )
        entry.chmod(0o700)
    if os.name != "nt":
        for name in ("bash", "dirname", "wc", "cat", "mktemp", "mkfifo", "sleep", "rm", "rmdir", "mkdir", "ps"):
            executable = shutil.which(name)
            assert executable, name
            (tools / name).symlink_to(executable)
    calls = tmp_path / "uv-calls"
    env.update(
        PATH=str(tools), CONTAINERS_TEST_UV_CALLS=str(calls),
        CONTAINERS_TEST_AMBIENT_USED=str(poison), COPILOT_EXT_NO_FLOCK="1",
        UV_PYTHON="unrelated-python", VIRTUAL_ENV=str(root),
        UV_CONFIG_FILE="missing-relative.toml", UV_NO_CONFIG="false",
    )
    for suffix in ("sh", "ps1"):
        provider = payload / f"scripts/bootstrap-uv.{suffix}"
        text = provider.read_text(encoding="utf-8")
        if suffix == "sh":
            needle = '    exec "$@"\n'
            observation = '    printf "%s\\n" "${*:2}" >> "$CONTAINERS_TEST_UV_CALLS"\n'
        else:
            needle = "    $result = & $InvokeCaptured @options\n"
            observation = (
                "    [IO.File]::AppendAllText($env:CONTAINERS_TEST_UV_CALLS, "
                '($Arguments -join \' \') + "`n", [Text.UTF8Encoding]::new($false))\n'
            )
        assert text.count(needle) == 1
        provider.write_text(text.replace(needle, observation + needle), encoding="utf-8", newline="\n")
    return Removal(payload, root, env, calls, poison, uv, durable)


@pytest.mark.parametrize("host", cases.HOSTS)
def test_uninstall_reuses_prepared_python_without_ambient_fallback(
    removal: Removal, host: str, record_property: Callable[[str, object], None],
) -> None:
    samples = []
    for _ in range(2):
        started = time.monotonic()
        result = cases._run(host, removal.root, removal.env, payload=removal.payload)
        samples.append(time.monotonic() - started)
        assert result.returncode == 0, result.stdout + result.stderr
        assert not removal.poison.exists()
        assert not (removal.root / "versions/1.2.3").exists()
        assert not (removal.root / "deploy-manifest.json").exists()
        assert {name: (removal.root / name).read_bytes() for name in removal.durable} == removal.durable
    assert removal.calls.read_text(encoding="utf-8").splitlines() == [
        "--version",
        "python find --no-project --system --managed-python --no-python-downloads cpython@3.11.18",
    ] * 2
    record_property("containers_uninstall_bootstrap", json.dumps({
        "shell": Path(host).stem, "samples_seconds": samples, "uv_calls": 4,
        "managed_lookups": 2, "network": "disabled",
        "process_observation": "native-current-user" if os.name == "nt" else "fixture-owned",
    }, sort_keys=True))


@pytest.mark.parametrize("host", cases.HOSTS)
def test_missing_uv_refuses_before_helper_or_transaction(removal: Removal, host: str) -> None:
    removal.uv.rename(removal.uv.with_name("unavailable-" + removal.uv.name))
    before = {
        str(path.relative_to(removal.root)): path.read_bytes()
        for path in removal.root.rglob("*") if path.is_file()
    }
    result = cases._run(host, removal.root, removal.env, payload=removal.payload)
    assert result.returncode != 0
    assert not removal.calls.exists() and not removal.poison.exists()
    assert not (removal.root / ".install.lock").exists()
    assert {
        str(path.relative_to(removal.root)): path.read_bytes()
        for path in removal.root.rglob("*") if path.is_file()
    } == before


@pytest.mark.parametrize("host", cases.HOSTS)
def test_unavailable_process_observation_refuses_without_removing_bytes(removal: Removal, host: str) -> None:
    cases._snapshot(removal.root)
    (removal.root / ".install.lock").touch()
    leases = cases._component("versioned_runtime_leases")
    for directory in (removal.root, removal.root / "snapshots"):
        with leases.batch_removal_custody(directory, ["1.2.3"]):
            pass
    with leases._kernel_lease(
        removal.root, "redirects", kind="publication", shared=False, timeout=0,
    ):
        pass
    before = {
        str(path.relative_to(removal.root)): path.read_bytes()
        for path in removal.root.rglob("*") if path.is_file()
    }
    removal.env["MACHINES_TEST_CENSUS_UNAVAILABLE"] = "1"
    result = cases._run(host, removal.root, removal.env, payload=removal.payload)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Fixture-owned process census is unavailable" in result.stdout + result.stderr
    assert not removal.poison.exists()
    assert {
        str(path.relative_to(removal.root)): path.read_bytes()
        for path in removal.root.rglob("*") if path.is_file()
    } == before
