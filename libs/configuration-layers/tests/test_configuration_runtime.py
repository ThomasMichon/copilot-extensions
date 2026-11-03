"""The installed shell reader is the completion authority, not a copied helper."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "libs/configuration-layers/src"))
from configuration_context import runtime  # noqa: E402
from configuration_layers import ConfigurationError  # noqa: E402


def deploy(tmp_path: Path) -> tuple[Path, Path]:
    suffix = "ps1" if os.name == "nt" else "sh"
    resolver = tmp_path / "consumer" / "bin" / f"resolve-runtime.{suffix}"
    resolver.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "libs/versioned-runtime" / resolver.name, resolver)
    return tmp_path / "owner", resolver


@pytest.mark.skipif(os.name == "nt", reason="real POSIX resolver; Windows command contract covered separately")
@pytest.mark.parametrize("complete", [False, True])
def test_actual_deployed_resolver_is_completion_gated_and_read_only(tmp_path: Path, complete: bool) -> None:
    root, resolver = deploy(tmp_path)
    python = root / "versions/1.2.3/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    (root / "current-version").write_text("1.2.3", encoding="utf-8")
    if complete:
        (python.parent.parent / ".install-complete.json").write_text(
            '{"version": "1.2.3", "completed_at": "2026-01-01", "pid": 0}', encoding="utf-8",
        )
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert runtime.deployed_runtime_python(root, resolver=resolver, environment={}) == (
        python if complete else None
    )
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before
    assert not (resolver.parent.parent / "scripts").exists()


def test_absent_resolver_never_starts_anything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("no resolver"))
    assert runtime.deployed_runtime_python(tmp_path, resolver=tmp_path / "absent", environment={}) is None


def test_undeclared_read_only_resolver_fails_before_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = tmp_path / "resolver"
    resolver.write_text("echo unsafe", encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("undeclared contract"))
    with pytest.raises(ConfigurationError, match="read-only contract"):
        runtime.deployed_runtime_python(tmp_path, resolver=resolver, environment={})


@pytest.mark.parametrize(
    "output", [b"relative/python", b"/foreign/bin/python", b"\xff", b"x" * 65537],
    ids=["relative", "foreign", "utf8", "oversize"],
)
def test_resolver_output_is_bounded_and_stays_in_qualified_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: bytes,
) -> None:
    root, resolver = deploy(tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, output, b""))
    with pytest.raises(ConfigurationError):
        runtime.deployed_runtime_python(root, resolver=resolver, environment={"SystemRoot": str(tmp_path)})


@pytest.mark.parametrize("failure", ["refusal", "timeout", "missing-host"])
def test_real_resolver_failure_is_not_optional_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    root, resolver = deploy(tmp_path)

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 15)
        if failure == "missing-host":
            raise FileNotFoundError("host unavailable")
        return subprocess.CompletedProcess(argv, 1, b"", b"refused")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ConfigurationError):
        runtime.deployed_runtime_python(root, resolver=resolver, environment={"SystemRoot": str(tmp_path)})


@pytest.mark.parametrize("windows", [False, True])
def test_native_command_has_explicit_read_only_routing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows: bool,
) -> None:
    root, resolver = deploy(tmp_path)
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="nt" if windows else "posix"))
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", run)
    assert runtime.deployed_runtime_python(
        root, resolver=resolver, environment={"SystemRoot": str(tmp_path), "AGENT_RT_ROOT": "foreign"},
    ) is None
    argv, options = calls[0]
    assert options["timeout"] == 15
    assert options["stdin"] == subprocess.DEVNULL
    environment = cast(Mapping[str, str], options["env"])
    assert environment["AGENT_RT_ROOT"] == str(root)
    assert environment["AGENT_RT_RESOLVE_TRACE_MODE"] == "read-only"
    if windows:
        assert options["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW")
        assert "-NonInteractive" in argv
        assert "-TraceMode read-only" in argv[-1]
        assert str(resolver) not in argv[-1]
    else:
        assert argv[0] == "/bin/sh"
        assert argv[-1] == str(resolver)
