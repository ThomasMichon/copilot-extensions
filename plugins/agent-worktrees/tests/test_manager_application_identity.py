"""Normal Manager admission accepts owned applications, not wrapper names alone."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import zipfile

import pytest

from agent_worktrees import manager_recovery as recovery
from test_manager_recovery import adopted as adopted


_PACKAGE = "copilot-extensions-worktree-manager"


def _wheel(application: SimpleNamespace, *, name: str = _PACKAGE, version: str = "0.6.35.dev1") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "copilot_extensions_worktree_manager-0.6.35.dev1.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n",
        )
        archive.writestr(
            "worktree_manager/source_config.py",
            f"DEFAULT_REPO = {str(application.repo)!r}\nDEFAULT_REF = 'application-release'\n"
            "raise AssertionError('package source must not execute during admission')\n",
        )
    return output.getvalue()


def _record(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def application(adopted: SimpleNamespace) -> SimpleNamespace:
    source_version = "0.6.35-dev1+source." + "a" * 40 + "." + "b" * 16
    generation = source_version + ".profile." + "c" * 16
    slot = adopted.root / "versions" / generation
    (slot / "wheels").mkdir(parents=True)
    adopted.slot = slot
    adopted.wheel = slot / "wheels" / "copilot_extensions_worktree_manager-0.6.35.dev1-py3-none-any.whl"
    content = _wheel(adopted)
    adopted.wheel.write_bytes(content)
    (slot / "pyproject.toml").write_text(
        '[project]\nname="uv-application-copilot-extensions-worktree-manager"\n'
        'version="0.6.35-dev1"\n'
        'dependencies=["copilot-extensions-worktree-manager==0.6.35.dev1"]\n'
        '[tool.uv.sources]\n'
        'copilot-extensions-worktree-manager={path="wheels/'
        + adopted.wheel.name + '"}\n',
        encoding="utf-8",
    )
    source = {
        "schema": "copilot-extensions.runtime-source", "version": 1,
        "plugin": "worktree-manager", "package_version": "0.6.35-dev1",
        "source_commit": "a" * 40, "source_tree": "d" * 40,
        "assembly_hash": "b" * 64, "runtime_version": source_version,
    }
    _write(slot / "runtime-source.json", source)
    _write(slot / "application.json", {
        "name": "worktree-manager", "module": "worktree_manager.__main__:console_entry",
        "construction": {
            "identity": {"runtime_source": source, "runtime_version": source_version},
            "runtime_version": generation, "local_packages": [_PACKAGE],
            "wheel_inputs": {
                "lock_id": "sha256:" + "c" * 64,
                "binding": {
                    "component": "worktree-manager",
                    "release": {"identity": "source:" + "a" * 40, "sha256": "sha256:" + "b" * 64},
                    "wheels": [{
                        "name": _PACKAGE, "version": "0.6.35.dev1",
                        "filename": adopted.wheel.name,
                        "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
                    }],
                },
            },
        },
    })
    (adopted.root / "current-version").write_text(generation, encoding="utf-8")
    return adopted


def test_normal_controller_reads_application_owned_source_without_executing_it(
    application: SimpleNamespace,
) -> None:
    selected = recovery.controller_selection()
    assert selected.adoption is not None
    source = selected.adoption.with_source()
    assert (source.repo, source.ref) == (str(application.repo), "application-release")
    assert application.wheel in dict(source.evidence)
    selected.verify()


def test_application_preserves_explicit_manager_source_configuration(application: SimpleNamespace) -> None:
    _config = application.root / "config.toml"
    _config.write_text('[source]\nref="explicit-pin"\n', encoding="utf-8")
    selected = recovery.adopted_manager()
    assert selected is not None
    source = selected.with_source()
    assert (source.repo, source.ref) == (str(application.repo), "explicit-pin")


def test_healthy_application_does_not_acquire_or_repair(
    application: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recovery, "_healthy", lambda *_args: True)
    monkeypatch.setattr(recovery, "_runtime_api", lambda: pytest.fail("healthy application was rebuilt"))
    assert recovery.recover() == {"status": "current"}
    assert not (application.root / ".installer-inputs").exists()


@pytest.mark.parametrize("dependency", [
    "another-package==0.6.35.dev1",
    "copilot-extensions-worktree-manager==0.6.34.dev1",
    "copilot-extensions-worktree-manager>=0.6.35.dev1",
])
def test_application_wrapper_requires_actual_locked_manager_dependency(
    application: SimpleNamespace, dependency: str,
) -> None:
    path = application.slot / "pyproject.toml"
    path.write_text(path.read_text().replace(_PACKAGE + "==0.6.35.dev1", dependency))
    with pytest.raises(recovery.RecoveryRefused, match="Manager application"):
        recovery.controller_selection()


@pytest.mark.parametrize(("name", "version"), [
    ("another-package", "0.6.35.dev1"), (_PACKAGE, "0.6.34.dev1"),
])
def test_application_checks_actual_wheel_metadata_not_just_wrapper_or_filename(
    application: SimpleNamespace, name: str, version: str,
) -> None:
    content = _wheel(application, name=name, version=version)
    application.wheel.write_bytes(content)
    path = application.slot / "application.json"
    record = _record(path)
    record["construction"]["wheel_inputs"]["binding"]["wheels"][0]["sha256"] = (
        "sha256:" + hashlib.sha256(content).hexdigest()
    )
    _write(path, record)
    with pytest.raises(recovery.RecoveryRefused, match="wheel metadata"):
        recovery.adopted_manager()


@pytest.mark.parametrize("field", ["name", "component", "source", "local-packages"])
def test_application_checks_its_own_component_and_source_association(
    application: SimpleNamespace, field: str,
) -> None:
    path = application.slot / "application.json"
    record = _record(path)
    if field == "name":
        record["name"] = "another-component"
    elif field == "component":
        record["construction"]["wheel_inputs"]["binding"]["component"] = "another-component"
    elif field == "local-packages":
        record["construction"]["local_packages"] = _PACKAGE
    else:
        record["construction"]["identity"]["runtime_source"]["source_tree"] = "e" * 40
    _write(path, record)
    with pytest.raises(recovery.RecoveryRefused, match="owner or source identity"):
        recovery.adopted_manager()


def test_application_wheel_must_match_its_source_profile(application: SimpleNamespace) -> None:
    application.wheel.write_bytes(application.wheel.read_bytes() + b"changed")
    with pytest.raises(recovery.RecoveryRefused, match="source profile"):
        recovery.adopted_manager()


@pytest.mark.parametrize("member", ["application.json", "wheels/copilot_extensions_worktree_manager-0.6.35.dev1-py3-none-any.whl"])
def test_application_admission_keeps_its_original_metadata_and_wheel_evidence(
    application: SimpleNamespace, member: str,
) -> None:
    selected = recovery.adopted_manager()
    assert selected is not None
    path = application.slot.joinpath(*member.split("/"))
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(recovery.RecoveryRefused, match="changed"):
        selected.verify()


def test_wrong_legacy_package_is_still_refused(adopted: SimpleNamespace) -> None:
    (adopted.slot / "pyproject.toml").write_text(
        '[project]\nname="another-package"\nversion="0.6.35-dev1"\n',
        encoding="utf-8",
    )
    with pytest.raises(recovery.RecoveryRefused, match="another package"):
        recovery.adopted_manager()
