"""Closed-wheel children retain their canonical owner without a source graph."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import importlib
from pathlib import Path
import subprocess
import sys
import time
from types import ModuleType
from typing import TYPE_CHECKING, Any, BinaryIO, TypedDict, cast

import pytest

from test_installer_closed_wheels import engine as engine, wheel_bytes as wheel_bytes  # noqa: PLC0414
from installer_wheel_inputs import WheelError, WheelFacts, WheelLimits, inspect_archive
from installer_wheel_snapshot import WheelInstallInputs, retain_wheel_inputs

if TYPE_CHECKING:
    from installer_engine import FrozenInputs


class _InputChanges(TypedDict, total=False):
    name: str
    version: str
    extra: dict[str, bytes]


def test_ordinary_child_keeps_frozen_source_and_relative_imports(
    engine: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "source-procutil"
    package = root / "src/agent_procutil"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname="agent-procutil"\nversion="1.0"\n', encoding="utf-8"
    )
    (package / "__init__.py").write_text(
        "from .sibling import VALUE\n", encoding="utf-8"
    )
    (package / "sibling.py").write_text("VALUE = 37\n", encoding="utf-8")
    seen: list[ModuleType] = []

    def run(argv: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        module = cast(ModuleType, options["procutil"])
        assert module.__file__ is not None
        assert module.VALUE == 37
        assert Path(module.__file__) == package / "__init__.py"
        seen.append(module)
        return subprocess.CompletedProcess(argv, 0, "ordinary", "")

    monkeypatch.setattr(engine, "run_owned", run)
    result = engine._child(
        ["fixture-child"],
        inputs=engine.FrozenInputs(root, (root,), "fixture"),
        descriptor=None,
        timeout=1,
    )
    assert result.stdout == "ordinary" and len(seen) == 1


@contextmanager
def process_inputs(
    engine: ModuleType,
    tmp_path: Path,
    *,
    name: str = "agent-procutil",
    version: str = "1.0",
    extra: dict[str, bytes] | None = None,
    unrelated: bool = False,
) -> Iterator[FrozenInputs]:
    source = tmp_path / "application"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[project]\nname="fixture-app"\nversion="1.0"\n'
        '[tool.uv.sources]\nmissing={path="../absent-development-source"}\n',
        encoding="utf-8",
    )
    filename = "agent_procutil-1.0-py3-none-any.whl"
    archive = tmp_path / filename
    package = (
        Path(__file__).resolve().parents[1] / "libs/agent-procutil/src/agent_procutil"
    )
    content = (
        {
            "agent_procutil/" + path.relative_to(package).as_posix(): path.read_bytes()
            for path in package.rglob("*.py")
        }
        if extra is None
        else extra
    )
    archive.write_bytes(
        wheel_bytes(
            name="agent_procutil"
            if "agent_procutil/__init__.py" in content
            else "other",
            prefix="agent_procutil-1.0.dist-info/",
            metadata=f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\n\n".encode(),
            extra=content,
        )
    )
    digest = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    archives, expected = {filename: archive}, {filename: digest}
    distributions: tuple[tuple[str, str, str, str], ...] = (
        ("agent-procutil", "1.0", filename, digest),
    )
    if unrelated:
        other_name = "fixture_unrelated-1.0-py3-none-any.whl"
        other = tmp_path / other_name
        other.write_bytes(
            wheel_bytes(
                name="fixture_unrelated",
                prefix="fixture_unrelated-1.0.dist-info/",
                metadata=b"Metadata-Version: 2.3\nName: fixture-unrelated\nVersion: 1.0\n\n",
            )
        )
        other_digest = "sha256:" + hashlib.sha256(other.read_bytes()).hexdigest()
        archives[other_name], expected[other_name] = other, other_digest
        distributions += (("fixture-unrelated", "1.0", other_name, other_digest),)
    with retain_wheel_inputs(
        archives,
        expected,
        max_wheels=2,
        max_archive_bytes=1 << 20,
        max_total_bytes=1 << 20,
        max_snapshot_bytes=1 << 20,
        deadline=time.monotonic() + 30,
    ) as snapshot:
        wheels = WheelInstallInputs(
            snapshot,
            '{"fixture":true}',
            distributions,
        )
        yield cast(
            "FrozenInputs",
            engine.FrozenInputs(
                source,
                (source,),
                "fixture",
                wheel_inputs=wheels,
                closed_wheel_source=True,
            ),
        )


def test_closed_wheel_failed_import_retires_partial_namespace(
    engine: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    before = set(sys.modules)
    monkeypatch.setattr(
        engine, "run_owned", lambda *a, **k: pytest.fail("failed import launched")
    )
    with process_inputs(
        engine,
        tmp_path,
        extra={
            "agent_procutil/__init__.py": b"from . import sibling\nraise RuntimeError('fixture import failure')\n",
            "agent_procutil/sibling.py": b"VALUE = 1\n",
        },
    ) as inputs:
        with pytest.raises(RuntimeError, match="fixture import failure"):
            engine._child(["fixture-child"], inputs=inputs, descriptor=None, timeout=1)
    assert not {
        name
        for name in set(sys.modules) - before
        if name.startswith("_installer_procutil_")
    }


@pytest.mark.parametrize("outcome", ["success", "failure", "timeout"])
def test_closed_wheel_child_retains_real_process_package_until_settlement(
    engine: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    outcome: str,
) -> None:
    import installer_engine_process as processes

    owned: list[tuple[str, Path]] = []
    failure: subprocess.SubprocessError | None = {
        "failure": subprocess.CalledProcessError(
            7, ["fixture-child"], "output", "failure"
        ),
        "timeout": subprocess.TimeoutExpired(["fixture-child"], 1, "output", "timeout"),
    }.get(outcome)

    def run(argv: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        module = cast(ModuleType, options["procutil"])
        assert module.__file__ is not None
        path = Path(module.__file__)
        owned.append((module.__name__, path))
        assert path.is_file()
        streaming = importlib.import_module(module.__name__ + ".streaming")
        assert streaming.__file__ is not None
        assert Path(streaming.__file__).is_file()
        packet = processes._guardian_packet(
            module,
            Path(engine.runtime.__file__),
            None,
        )
        assert packet
        assert options["construction_fd"] == 19
        assert options["allow_breakaway"] is True
        assert isinstance(inputs.wheel_inputs, WheelInstallInputs)
        assert options["deadline"] == inputs.wheel_inputs.deadline
        assert 0 < options["timeout"] <= 1
        if failure is not None:
            raise failure
        return subprocess.CompletedProcess(argv, 0, "output", "")

    monkeypatch.setattr(engine, "run_owned", run)
    monkeypatch.setattr(
        engine,
        "_procutil",
        lambda _inputs: pytest.fail("closed wheels traversed source packages"),
    )
    with process_inputs(engine, tmp_path) as inputs:
        if failure is None:
            result = engine._child(
                ["fixture-child"],
                inputs=inputs,
                descriptor=19,
                timeout=1,
                allow_breakaway=True,
            )
            assert result.stdout == "output"
        else:
            with pytest.raises(type(failure)) as caught:
                engine._child(
                    ["fixture-child"],
                    inputs=inputs,
                    descriptor=19,
                    timeout=1,
                    allow_breakaway=True,
                )
            assert caught.value is failure
        assert inputs.packages == (inputs.plugin,)
    assert len(owned) == 1
    namespace, path = owned[0]
    assert not path.parent.exists()
    assert not any(
        name == namespace or name.startswith(namespace + ".") for name in sys.modules
    )


def test_closed_wheel_retries_share_retained_process_package(
    engine: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import installer_wheel_snapshot as snapshots

    paths: list[Path] = []
    counts = {"digest": 0, "inspection": 0, "load": 0}
    original_digest, original_inspect = snapshots._digest, inspect_archive
    original_load = engine._load_procutil

    def digest(
        stream: BinaryIO,
        limit: int,
        deadline: float,
        destination: BinaryIO | None = None,
    ) -> tuple[str, int]:
        counts["digest"] += 1
        return original_digest(stream, limit, deadline, destination)

    def inspect(
        archive: BinaryIO,
        *,
        limits: WheelLimits,
        deadline: float,
        record_algorithms: frozenset[str] = frozenset({"sha256"}),
    ) -> WheelFacts:
        counts["inspection"] += 1
        return original_inspect(
            archive,
            limits=limits,
            deadline=deadline,
            record_algorithms=record_algorithms,
        )

    def load(path: Path) -> ModuleType:
        counts["load"] += 1
        return cast(ModuleType, original_load(path))

    def run(argv: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        path = Path(options["procutil"].__file__)
        assert path.is_file()
        paths.append(path)
        if len(paths) == 1:
            raise subprocess.CalledProcessError(1, argv, "", "fixture transient")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(engine, "run_owned", run)
    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)
    with process_inputs(engine, tmp_path, unrelated=True) as inputs:
        monkeypatch.setattr(snapshots, "_digest", digest)
        monkeypatch.setattr(snapshots, "inspect_archive", inspect)
        monkeypatch.setattr(engine, "_load_procutil", load)
        engine._child(
            ["fixture-child"],
            inputs=inputs,
            descriptor=None,
            timeout=1,
            delays=(0,),
            transient="fixture transient",
        )
        assert counts == {"digest": 1, "inspection": 1, "load": 1}
        assert len(paths) == 2 and paths[0] == paths[1]
        engine._child(["fixture-child"], inputs=inputs, descriptor=None, timeout=1)
        assert counts == {"digest": 2, "inspection": 2, "load": 2}
        assert len(paths) == 3 and paths[-1] != paths[0]
    assert all(not path.exists() for path in paths)


@pytest.mark.parametrize(
    "condition",
    ["missing", "wrong-name", "wrong-version", "changed-binding", "expired"],
)
def test_closed_wheel_owner_refuses_before_launch(
    engine: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    condition: str,
) -> None:
    changes: _InputChanges = {}
    if condition == "missing":
        changes["extra"] = {"other/__init__.py": b"VALUE = 1\n"}
    elif condition == "wrong-name":
        changes["name"] = "unrelated"
    elif condition == "wrong-version":
        changes["version"] = "2.0"
    monkeypatch.setattr(
        engine, "run_owned", lambda *a, **k: pytest.fail("invalid owner launched")
    )
    monkeypatch.setattr(
        engine, "_procutil", lambda *a: pytest.fail("invalid owner fell back")
    )
    with process_inputs(engine, tmp_path, **changes) as inputs:
        wheels = inputs.wheel_inputs
        assert isinstance(wheels, WheelInstallInputs)
        if condition == "changed-binding":
            name, version, filename, _digest = wheels.distributions[0]
            inputs = replace(
                inputs,
                wheel_inputs=replace(
                    wheels,
                    distributions=((name, version, filename, "sha256:" + "0" * 64),),
                ),
            )
        if condition == "expired":
            inputs = replace(
                inputs,
                wheel_inputs=replace(
                    wheels,
                    snapshot=replace(wheels.snapshot, _deadline=time.monotonic() - 1),
                ),
            )
        with pytest.raises(WheelError):
            engine._child(["fixture-child"], inputs=inputs, descriptor=None, timeout=1)
