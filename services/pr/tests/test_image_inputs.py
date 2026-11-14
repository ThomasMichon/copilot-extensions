"""Real retained-wheel context controls; no image daemon or feed access."""
from __future__ import annotations

from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import sys
import time
from typing import TYPE_CHECKING, Any, Callable, Iterator, cast
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
if TYPE_CHECKING:
    from tools.pr_service_image_inputs import ImageInputs, prepare_image_inputs
    from tools.runtime_profile import ProfileError, RuntimeProfile, SCHEMA, validate_profile
    from tools.wheel_input_fixture import wheel_bytes
else:
    from pr_service_image_inputs import ImageInputs, prepare_image_inputs
    from runtime_profile import ProfileError, SCHEMA, validate_profile
    from wheel_input_fixture import wheel_bytes
from installer_wheel_inputs import WheelError, WheelLimits
from installer_wheel_snapshot import WheelSnapshot, retain_wheel_inputs


BASE = "python@sha256:" + "a" * 64
LIMITS = WheelLimits(1 << 20, 1 << 20, 1 << 20, 1 << 16, 100, 100, 1 << 16)
NAMES = ["agent-pull-requests-service", "agent-pull-requests", "fleet-manager", "agent-worktrees"]


def fixture_inputs(
    tmp_path: Path, *, omit: str | None = None, platform: str = "linux",
) -> tuple[RuntimeProfile, dict[str, Path]]:
    environment = {
        "implementation_name": "cpython", "implementation_version": "3.11.9",
        "os_name": "posix", "platform_machine": "x86_64", "platform_release": "6.0",
        "platform_system": "Linux", "platform_version": "6.0",
        "python_full_version": "3.11.9", "platform_python_implementation": "CPython",
        "python_version": "3.11", "sys_platform": platform,
    }
    archives: dict[str, Path]
    wheels: list[dict[str, Any]]
    archives, wheels = {}, []
    for name in NAMES:
        if name == omit:
            continue
        prefix = name.replace("-", "_")
        filename = prefix + "-1.0-py3-none-any.whl"
        data = cast(Callable[..., bytes], wheel_bytes)(
            name=prefix, prefix=prefix + "-1.0.dist-info/",
            metadata=f"Metadata-Version: 2.3\nName: {name}\nVersion: 1.0\n\n".encode(),
        )
        path = tmp_path / filename
        path.write_bytes(data)
        archives[filename] = path
        wheels.append({
            "name": name, "version": "1.0", "filename": filename,
            "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
            "requires_python": "", "provides_extras": [], "selected_extras": [],
            "requires_dist": [],
        })
    record = {
        "schema": SCHEMA, "version": 1,
        "release": {"identity": "synthetic-release", "sha256": "sha256:" + "b" * 64},
        "mode": "source", "artifact": None, "component": "pr-service", "backend": "cpu",
        "approval_source": "synthetic-authority", "acquisition_policy": "synthetic-policy",
        "target": {"device": "cpu", "environment": environment, "tags": ["py3-none-any"]},
        "roots": [wheel["name"] + "==1.0" for wheel in wheels], "wheels": wheels,
    }
    return validate_profile(record), archives


@contextmanager
def retained(
    tmp_path: Path, **changes: Any,
) -> Iterator[tuple[RuntimeProfile, WheelSnapshot, float]]:
    profile, archives = fixture_inputs(tmp_path, **changes)
    deadline = time.monotonic() + 60
    with cast(Callable[..., AbstractContextManager[WheelSnapshot]], retain_wheel_inputs)(
        archives, {wheel["filename"]: wheel["sha256"] for wheel in profile.record()["wheels"]},
        max_wheels=10, max_archive_bytes=LIMITS.max_archive_bytes,
        max_total_bytes=LIMITS.max_archive_bytes, max_snapshot_bytes=LIMITS.max_archive_bytes,
        deadline=deadline,
    ) as snapshot:
        yield profile, snapshot, deadline


def prepare(
    profile: RuntimeProfile, snapshot: WheelSnapshot, deadline: float, **changes: Any,
) -> AbstractContextManager[ImageInputs]:
    options: dict[str, Any] = dict(python_base=BASE, limits=LIMITS, deadline=deadline) | changes
    return cast(AbstractContextManager[ImageInputs], prepare_image_inputs(
        profile, snapshot, **options,
    ))


def test_context_contains_only_recipe_complete_wheels_and_exact_lock(tmp_path: Path) -> None:
    secret = tmp_path / "credential.txt"
    secret.write_text("must-not-enter-context")
    with retained(tmp_path) as (profile, snapshot, deadline):
        with prepare(profile, snapshot, deadline) as result:
            context = result.path
            assert result.python_base == BASE
            assert result.lock_id == profile.lock_id
            assert {path.name for path in context.iterdir()} == {"Dockerfile", "requirements.lock", "wheels"}
            assert {path.name for path in (context / "wheels").iterdir()} == set(snapshot.paths)
            for filename, source in snapshot.paths.items():
                assert (context / "wheels" / filename).read_bytes() == source.read_bytes()
            lock = (context / "requirements.lock").read_text().splitlines()
            assert lock == [
                wheel["name"] + "==1.0 --hash=" + wheel["sha256"]
                for wheel in sorted(profile.record()["wheels"], key=lambda wheel: wheel["name"])
            ]
            recipe = (context / "Dockerfile").read_text()
            assert "--no-index" in recipe and "--require-hashes" in recipe
            assert "USER 65532:65532" in recipe
        assert not context.exists()


@pytest.mark.parametrize("base", ["python:3.11", "https://example.test/python", "python@sha256:abc", None])
def test_unpinned_or_invalid_base_refuses(tmp_path: Path, base: str | None) -> None:
    with retained(tmp_path) as (profile, snapshot, deadline):
        with pytest.raises(ProfileError, match="pinned"):
            with prepare(profile, snapshot, deadline, python_base=base):
                pytest.fail("invalid base admitted")


@pytest.mark.parametrize("name", NAMES)
def test_required_distributions_cannot_be_omitted(tmp_path: Path, name: str) -> None:
    with retained(tmp_path, omit=name) as (profile, snapshot, deadline):
        with pytest.raises(ProfileError, match="required"):
            with prepare(profile, snapshot, deadline):
                pytest.fail("incomplete PR closure admitted")


def test_foreign_platform_refuses(tmp_path: Path) -> None:
    with retained(tmp_path, platform="win32") as (profile, snapshot, deadline):
        with pytest.raises(ProfileError, match="Linux"):
            with prepare(profile, snapshot, deadline):
                pytest.fail("foreign target admitted")


@pytest.mark.parametrize("deadline", [float("inf"), float("nan"), 0, True])
def test_no_unbounded_or_expired_deadline(tmp_path: Path, deadline: float) -> None:
    with retained(tmp_path) as (profile, snapshot, _):
        with pytest.raises(ProfileError, match="deadline"):
            with prepare(profile, snapshot, deadline):
                pytest.fail("unbounded operation admitted")


def test_builder_failure_cleans_owned_context_and_preserves_wheel_custody(tmp_path: Path) -> None:
    with retained(tmp_path) as (profile, snapshot, deadline):
        with pytest.raises(RuntimeError, match="builder failed"):
            with prepare(profile, snapshot, deadline) as result:
                context = result.path
                raise RuntimeError("builder failed")
        assert not context.exists()
        cast(Callable[[], None], snapshot.verify)()


def test_closed_snapshot_cannot_supply_context(tmp_path: Path) -> None:
    with retained(tmp_path) as (profile, snapshot, deadline):
        pass
    with pytest.raises((WheelError, ValueError, OSError)):
        with prepare(profile, snapshot, deadline):
            pytest.fail("released wheel custody admitted")


def test_missing_retained_wheel_refuses_without_reopening_original(tmp_path: Path) -> None:
    with retained(tmp_path) as (profile, snapshot, deadline):
        from dataclasses import replace

        missing = replace(snapshot, archives=dict(list(snapshot.archives.items())[1:]))
        with pytest.raises(ProfileError, match="wheel set"):
            with prepare(profile, missing, deadline):
                pytest.fail("missing retained archive admitted")


def test_corrupt_wheel_bytes_refuse_even_with_profile_matching_digest(tmp_path: Path) -> None:
    profile, archives = fixture_inputs(tmp_path)
    record = profile.record()
    wheel = record["wheels"][0]
    archives[wheel["filename"]].write_bytes(b"not a wheel")
    wheel["sha256"] = "sha256:" + hashlib.sha256(b"not a wheel").hexdigest()
    profile = validate_profile(record)
    deadline = time.monotonic() + 60
    with cast(Callable[..., AbstractContextManager[WheelSnapshot]], retain_wheel_inputs)(
        archives, {item["filename"]: item["sha256"] for item in record["wheels"]},
        max_wheels=10, max_archive_bytes=LIMITS.max_archive_bytes,
        max_total_bytes=LIMITS.max_archive_bytes, max_snapshot_bytes=LIMITS.max_archive_bytes,
        deadline=deadline,
    ) as snapshot:
        with pytest.raises(ProfileError):
            with prepare(profile, snapshot, deadline):
                pytest.fail("matching digest substituted for actual wheel inspection")


@pytest.mark.parametrize("separate_profiles", [False, True])
def test_generated_context_installs_offline_into_fresh_environment(
    tmp_path: Path, separate_profiles: bool,
) -> None:
    from agent_procutil.owned_process import run_caller_owned

    environment = tmp_path / "installed"
    created = run_caller_owned(
        [sys.executable, "-I", "-m", "venv", str(environment)],
        timeout=60, max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert created.returncode == 0, created.stderr
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    with ExitStack() as stack:
        additional = {}
        if separate_profiles:
            profile, snapshot, native, native_wheels, deadline = stack.enter_context(
                separate_application_inputs(tmp_path),
            )
            additional["additional_inputs"] = ((native, native_wheels),)
        else:
            profile, snapshot, deadline = stack.enter_context(retained(tmp_path))
        with prepare(profile, snapshot, deadline, **additional) as result:
            command = [
                str(python), "-I", "-m", "pip", "install", "--no-index",
                "--find-links", str(result.path / "wheels"), "--only-binary=:all:",
                "--require-hashes", "--no-cache-dir", "--disable-pip-version-check",
                "-r", str(result.path / "requirements.lock"),
            ]
            installed = run_caller_owned(
                command, cwd=tmp_path, timeout=30,
                max_stdout_bytes=65536, max_stderr_bytes=65536,
            )
            assert installed.returncode == 0, installed.stderr
            imported = run_caller_owned(
                [str(python), "-I", "-c",
                 "import agent_pull_requests_service,agent_pull_requests,fleet_manager,agent_worktrees"],
                cwd=tmp_path, timeout=10, max_stdout_bytes=65536, max_stderr_bytes=65536,
            )
            assert imported.returncode == 0, imported.stderr
            with (result.path / "wheels" / next(iter(snapshot.paths))).open("ab") as stream:
                stream.write(b"tampered")
            refused = run_caller_owned(
                [*command, "--force-reinstall"], cwd=tmp_path, timeout=30,
                max_stdout_bytes=65536, max_stderr_bytes=65536,
            )
            assert refused.returncode != 0
            assert "HASHES" in refused.stderr.upper()


@contextmanager
def separate_application_inputs(
    tmp_path: Path, *, conflict: bool = False, different_target: bool = False,
) -> Iterator[tuple[RuntimeProfile, WheelSnapshot, RuntimeProfile, WheelSnapshot, float]]:
    profile, archives = fixture_inputs(tmp_path)
    original = profile.record()
    service = original | {
        "wheels": [wheel for wheel in original["wheels"] if wheel["name"] != "agent-worktrees"],
    }
    service["roots"] = [wheel["name"] + "==1.0" for wheel in service["wheels"]]
    native = profile.record() | {"component": "agent-worktrees"}
    native["wheels"] = [
        wheel for wheel in native["wheels"] if wheel["name"] in {"agent-worktrees", "fleet-manager"}
    ]
    native["roots"] = [wheel["name"] + "==1.0" for wheel in native["wheels"]]
    native_archives = dict(archives)
    if conflict:
        wheel = next(wheel for wheel in native["wheels"] if wheel["name"] == "fleet-manager")
        data = cast(Callable[..., bytes], wheel_bytes)(
            name="fleet_manager", prefix="fleet_manager-1.0.dist-info/",
            metadata=b"Metadata-Version: 2.3\nName: fleet-manager\nVersion: 1.0\nSummary: different\n\n",
        )
        path = tmp_path / "different.whl"
        path.write_bytes(data)
        native_archives[wheel["filename"]] = path
        wheel["sha256"] = "sha256:" + hashlib.sha256(data).hexdigest()
    if different_target:
        native["target"]["environment"]["platform_machine"] = "aarch64"
    deadline = time.monotonic() + 60
    with ExitStack() as stack:
        selected = []
        for record, paths in ((service, archives), (native, native_archives)):
            frozen = validate_profile(record)
            expected = {wheel["filename"]: wheel["sha256"] for wheel in record["wheels"]}
            snapshot = stack.enter_context(cast(Callable[..., AbstractContextManager[WheelSnapshot]], retain_wheel_inputs)(
                {name: paths[name] for name in expected}, expected,
                max_wheels=10, max_archive_bytes=LIMITS.max_archive_bytes,
                max_total_bytes=LIMITS.max_archive_bytes, max_snapshot_bytes=LIMITS.max_archive_bytes,
                deadline=deadline,
            ))
            selected.append((frozen, snapshot))
        yield selected[0][0], selected[0][1], selected[1][0], selected[1][1], deadline


def test_separate_native_profile_composes_without_inventing_merged_approval(tmp_path: Path) -> None:
    with separate_application_inputs(tmp_path) as (service, service_wheels, native, native_wheels, deadline):
        originals = service.canonical, native.canonical
        with prepare(service, service_wheels, deadline, additional_inputs=((native, native_wheels),)) as result:
            assert result.profile_lock_ids == (service.lock_id, native.lock_id)
            assert result.lock_id not in result.profile_lock_ids
            assert (service.canonical, native.canonical) == originals
            assert len(list((result.path / "wheels").iterdir())) == len(NAMES)
            lock = (result.path / "requirements.lock").read_text().splitlines()
            assert {line.split("==")[0] for line in lock} == set(NAMES)
            assert len(lock) == len(NAMES)
            context = result.path
        assert not context.exists()
        cast(Callable[[], None], service_wheels.verify)()
        cast(Callable[[], None], native_wheels.verify)()


@pytest.mark.parametrize("budget", ["archive", "expanded"])
def test_composed_profiles_share_one_aggregate_byte_budget(tmp_path: Path, budget: str) -> None:
    with separate_application_inputs(tmp_path) as (service, service_wheels, native, native_wheels, deadline):
        paths = dict(service_wheels.paths) | dict(native_wheels.paths)
        size = sum(path.stat().st_size for path in paths.values())
        if budget == "expanded":
            size = 0
            for path in paths.values():
                with zipfile.ZipFile(path) as archive:
                    size += sum(member.file_size for member in archive.infolist())
        limits = replace(
            LIMITS, **{"max_archive_bytes" if budget == "archive" else "max_expanded_bytes": size - 1},
        )
        with pytest.raises(ProfileError):
            with prepare(
                service, service_wheels, deadline, limits=limits,
                additional_inputs=((native, native_wheels),),
            ):
                pytest.fail("separate profiles multiplied the image byte budget")


def test_composed_profiles_refuse_different_bytes_for_shared_distribution(tmp_path: Path) -> None:
    with separate_application_inputs(tmp_path, conflict=True) as (
        service, service_wheels, native, native_wheels, deadline,
    ):
        with pytest.raises(ProfileError, match="shared|conflicting"):
            with prepare(service, service_wheels, deadline, additional_inputs=((native, native_wheels),)):
                pytest.fail("conflicting shared distribution admitted")


def test_composed_profiles_refuse_different_runtime_targets(tmp_path: Path) -> None:
    with separate_application_inputs(tmp_path, different_target=True) as (
        service, service_wheels, native, native_wheels, deadline,
    ):
        with pytest.raises(ProfileError, match="different targets"):
            with prepare(service, service_wheels, deadline, additional_inputs=((native, native_wheels),)):
                pytest.fail("foreign native runtime target admitted")


@contextmanager
def manager_inputs(
    tmp_path: Path, reference: RuntimeProfile, deadline: float, *, collision: str | None,
) -> Iterator[tuple[RuntimeProfile, WheelSnapshot]]:
    name = "copilot-extensions-worktree-manager"
    prefix = name.replace("-", "_")
    filename = prefix + "-1.0-py3-none-any.whl"
    data = cast(Callable[..., bytes], wheel_bytes)(
        name=prefix, prefix=prefix + "-1.0.dist-info/",
        metadata=f"Metadata-Version: 2.3\nName: {name}\nVersion: 1.0\n\n".encode(),
        extra={collision: b"overwrite\n"} if collision else None,
    )
    path = tmp_path / filename
    path.write_bytes(data)
    record = reference.record() | {
        "component": "worktree-manager", "roots": [name + "==1.0"],
        "wheels": [{
            "name": name, "version": "1.0", "filename": filename,
            "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
            "requires_python": "", "provides_extras": [], "selected_extras": [], "requires_dist": [],
        }],
    }
    with cast(Callable[..., AbstractContextManager[WheelSnapshot]], retain_wheel_inputs)(
        {filename: path}, {filename: record["wheels"][0]["sha256"]},
        max_wheels=10, max_archive_bytes=LIMITS.max_archive_bytes,
        max_total_bytes=LIMITS.max_archive_bytes, max_snapshot_bytes=LIMITS.max_archive_bytes,
        deadline=deadline,
    ) as snapshot:
        yield validate_profile(record), snapshot


@pytest.mark.parametrize("manager_first", [False, True])
@pytest.mark.parametrize("collision", [
    "fleet_manager/__init__.py",
    "fleet_manager",
    "copilot_extensions_worktree_manager-1.0.data/purelib/fleet_manager/__init__.py",
])
def test_composed_profiles_preserve_canonical_payload_ownership(
    tmp_path: Path, manager_first: bool, collision: str,
) -> None:
    with separate_application_inputs(tmp_path) as (service, service_wheels, native, native_wheels, deadline):
        with manager_inputs(tmp_path, service, deadline, collision=collision) as manager:
            selections: tuple[tuple[RuntimeProfile, WheelSnapshot], ...] = (
                (service, service_wheels), (native, native_wheels),
            )
            selections = (manager, *selections) if manager_first else (*selections, manager)
            with pytest.raises(ProfileError, match="ownership collision"):
                with prepare(*selections[0], deadline, additional_inputs=selections[1:]):
                    pytest.fail("split profiles bypassed canonical payload ownership")


def test_identical_shared_manager_is_checked_once_without_self_collision(tmp_path: Path) -> None:
    with separate_application_inputs(tmp_path) as (service, service_wheels, native, native_wheels, deadline):
        with manager_inputs(tmp_path, service, deadline, collision=None) as manager:
            with prepare(
                service, service_wheels, deadline,
                additional_inputs=(manager, (native, native_wheels), manager),
            ) as result:
                assert len(list((result.path / "wheels").iterdir())) == len(NAMES) + 1
                assert result.profile_lock_ids == (service.lock_id, manager[0].lock_id, native.lock_id)
