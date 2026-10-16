"""Guest installation identity does not depend on the entry shell."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "installation_context.py"


@pytest.fixture
def primitive(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("guest_installation_context", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def guest(primitive: ModuleType, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setattr(primitive, "_is_wsl_guest", lambda: True)
    monkeypatch.setattr(primitive, "_guest_machine_id", lambda: "a" * 32)
    current = primitive._current_environment

    def environment(**kwargs: Any) -> tuple[dict[str, Any], Path]:
        return current(**{**kwargs, "platform": kwargs.get("platform") or "posix"})

    monkeypatch.setattr(primitive, "_current_environment", environment)
    return primitive


def _current(guest: ModuleType, profile: Path, environment: dict[str, str]) -> dict:
    return guest._current_environment(
        environment=environment, os_profile=profile, platform="posix", wsl_distro=None,
    )[0]


@pytest.mark.installation_context_smoke
def test_direct_and_ssh_identity_agree_after_explicit_registration(
    guest: ModuleType, tmp_path: Path,
) -> None:
    direct = {"WSL_DISTRO_NAME": "Example Distro"}
    before = _current(guest, tmp_path, direct)
    first = guest.register_guest_environment(
        "Example Distro", environment=direct, os_profile=tmp_path,
    )
    assert first["created"] is True
    path = guest._guest_environment_path(tmp_path)
    contents, modified = path.read_bytes(), path.stat().st_mtime_ns
    for _ in range(2):
        assert _current(guest, tmp_path, {}) == before
        assert _current(guest, tmp_path, direct) == before
        repeated = guest.register_guest_environment(
            "example distro", environment=direct, os_profile=tmp_path,
        )
        assert repeated["created"] is False
        assert path.read_bytes() == contents
        assert path.stat().st_mtime_ns == modified
    assert not (path.parent / ".guest-environment.lock").exists()


def test_missing_guest_identity_refuses_without_creating_state(
    guest: ModuleType, tmp_path: Path,
) -> None:
    with pytest.raises(guest.InstallationContextError, match="guest-identity-unregistered"):
        _current(guest, tmp_path, {})
    with pytest.raises(guest.InstallationContextError, match="guest-identity-unregistered"):
        guest.inspect_guest_environment(
            distribution="Any selected name", environment={}, os_profile=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(guest.InstallationContextError, match="matching named direct"):
        guest.register_guest_environment("Example", environment={}, os_profile=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_foreign_guest_and_conflicting_observation_cannot_reuse_registration(
    guest: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct = {"WSL_DISTRO_NAME": "Example"}
    guest.register_guest_environment("Example", environment=direct, os_profile=tmp_path)
    path = guest._guest_environment_path(tmp_path)
    before = path.read_bytes()
    with pytest.raises(guest.InstallationContextError, match="guest-identity-conflict"):
        _current(guest, tmp_path, {"WSL_DISTRO_NAME": "Other"})
    monkeypatch.setattr(guest, "_guest_machine_id", lambda: "b" * 32)
    with pytest.raises(guest.InstallationContextError, match="guest-identity-foreign"):
        _current(guest, tmp_path, {})
    assert path.read_bytes() == before


@pytest.mark.parametrize("registered", [False, True])
def test_guest_status_selector_only_compares_independently_resolved_identity(
    guest: ModuleType, tmp_path: Path, registered: bool,
) -> None:
    direct = {"WSL_DISTRO_NAME": "Example"}
    if registered:
        guest.register_guest_environment("Example", environment=direct, os_profile=tmp_path)
    observation = {} if registered else direct
    with pytest.raises(guest.InstallationContextError, match="guest-identity-conflict"):
        guest.inspect_guest_environment(
            distribution="Other", environment=observation, os_profile=tmp_path,
        )
    result = guest.inspect_guest_environment(
        distribution="example", environment=observation, os_profile=tmp_path,
    )
    assert result["environment"]["wslDistro"] == "Example"


@pytest.mark.parametrize("competing", ["Other", "EXAMPLE"])
def test_registration_rechecks_identity_after_lock_admission(
    guest: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, competing: str,
) -> None:
    original_enter = guest._DirectoryLock.__enter__
    registration = {
        "schema": guest.GUEST_ENVIRONMENT_SCHEMA, "version": 1,
        "distribution": competing, "machineId": "a" * 32,
        "homeRealPath": str(tmp_path),
    }

    def enter(lock: Any) -> Any:
        result = original_enter(lock)
        guest._atomic_write_json(guest._guest_environment_path(tmp_path), registration, lock=lock)
        return result

    monkeypatch.setattr(guest._DirectoryLock, "__enter__", enter)
    if competing == "Other":
        with pytest.raises(guest.InstallationContextError, match="guest-identity-conflict"):
            guest.register_guest_environment(
                "Example", environment={"WSL_DISTRO_NAME": "Example"}, os_profile=tmp_path,
            )
    else:
        result = guest.register_guest_environment(
            "Example", environment={"WSL_DISTRO_NAME": "Example"}, os_profile=tmp_path,
        )
        assert result["created"] is False
        assert result["environment"]["wslDistro"] == competing
    path = guest._guest_environment_path(tmp_path)
    assert json.loads(path.read_text(encoding="utf-8")) == registration
    assert not (path.parent / ".guest-environment.lock").exists()


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("schema", "another-owner"),
    ("homeRealPath", "/another-profile"), ("machineId", "invalid"),
])
def test_registration_identity_and_schema_are_validated(
    guest: ModuleType, tmp_path: Path, field: str, value: object,
) -> None:
    direct = {"WSL_DISTRO_NAME": "Example"}
    guest.register_guest_environment("Example", environment=direct, os_profile=tmp_path)
    path = guest._guest_environment_path(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data[field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(guest.InstallationContextError, match="guest-identity-(invalid|foreign)"):
        _current(guest, tmp_path, {})
    assert path.read_bytes() == before


def test_native_or_container_execution_does_not_inherit_a_wsl_identity(
    guest: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(guest, "_is_wsl_guest", lambda: False)
    assert _current(guest, tmp_path, {"WSL_DISTRO_NAME": "Inherited"})["wslDistro"] is None
    with pytest.raises(guest.InstallationContextError, match="real WSL guest"):
        guest.register_guest_environment(
            "Inherited", environment={"WSL_DISTRO_NAME": "Inherited"}, os_profile=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("marker,cgroup,release,expected", [
    ("", "0::/init.scope", "6.1-microsoft-standard-WSL2", True),
    ("/.dockerenv", "", "6.1-microsoft-standard-WSL2", False),
    ("/run/.containerenv", "", "6.1-microsoft-standard-WSL2", False),
    ("", "0::/docker/example", "6.1-microsoft-standard-WSL2", False),
    ("", "0::/init.scope", "6.1-generic", False),
])
def test_guest_detection_uses_os_evidence_not_shell_variables(
    primitive: ModuleType, monkeypatch: pytest.MonkeyPatch,
    marker: str, cgroup: str, release: str, expected: bool,
) -> None:
    original_is_file, original_read = Path.is_file, Path.read_text

    def is_file(path: Path) -> bool:
        if path.as_posix() in {"/.dockerenv", "/run/.containerenv"}:
            return path.as_posix() == marker
        return original_is_file(path)

    def read(path: Path, *args: object, **kwargs: object) -> str:
        if path.as_posix() == "/proc/1/cgroup":
            return cgroup
        if path.as_posix() == "/proc/sys/kernel/osrelease":
            return release
        return original_read(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(primitive.sys, "platform", "linux")
        context.setenv("WSL_DISTRO_NAME", "Untrusted inherited value")
        context.setattr(Path, "is_file", is_file)
        context.setattr(Path, "read_text", read)
        actual = primitive._is_wsl_guest()
    assert actual is expected


def test_runtime_owner_is_not_guest_identity_evidence(guest: ModuleType, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    owner = runtime / "deploy-manifest.json"
    owner.write_text(json.dumps({
        "commandLifecycle": {"state": "complete", "owner": {"wslDistro": "Example"}},
    }), encoding="utf-8")
    before = owner.read_bytes()
    with pytest.raises(guest.InstallationContextError, match="guest-identity-unregistered"):
        _current(guest, tmp_path, {})
    assert owner.read_bytes() == before
    assert not guest._guest_environment_path(tmp_path).exists()


def test_distribution_names_do_not_acquire_lossy_unicode_aliases(
    guest: ModuleType, tmp_path: Path,
) -> None:
    guest.register_guest_environment(
        "Example-\u00df", environment={"WSL_DISTRO_NAME": "Example-\u00df"}, os_profile=tmp_path,
    )
    with pytest.raises(guest.InstallationContextError, match="guest-identity-conflict"):
        _current(guest, tmp_path, {"WSL_DISTRO_NAME": "Example-SS"})


@pytest.mark.skipif(os.name != "posix", reason="POSIX registration permissions")
def test_shared_write_registration_is_rejected(guest: ModuleType, tmp_path: Path) -> None:
    guest.register_guest_environment(
        "Example", environment={"WSL_DISTRO_NAME": "Example"}, os_profile=tmp_path,
    )
    path = guest._guest_environment_path(tmp_path)
    path.chmod(0o660)
    with pytest.raises(guest.InstallationContextError, match="writable only by this user"):
        _current(guest, tmp_path, {})


@pytest.mark.parametrize("default,expected,status,matches", [
    ("Example", "example", "ready", True),
    ("Other", "Example", "mismatch", False),
    (None, "Example", "unavailable", None),
    (None, None, "ready", None),
])
def test_default_drift_is_reported_without_retargeting_or_writing(
    primitive: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    default: str | None, expected: str | None, status: str, matches: bool | None,
) -> None:
    monkeypatch.setattr(primitive, "_is_wsl_guest", lambda: False)
    monkeypatch.setattr(primitive, "_host_default_wsl_distribution", lambda: default)
    before = list(tmp_path.iterdir())
    for _ in range(2):
        result = primitive.inspect_guest_environment(
            distribution=expected, environment={}, os_profile=tmp_path,
        )
        assert result["status"] == status
        assert result["hostDefault"]["distribution"] == default
        assert result["hostDefault"]["matches"] is matches
        assert result["environment"]["wslDistro"] is None
        assert list(tmp_path.iterdir()) == before
