"""Source, candidate capture and wheel closure fixtures for Manager adapters."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import tomllib
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any, Callable, Iterator, cast

import pytest

from worktree_manager import self_install

if TYPE_CHECKING:
    from installer_wheel_snapshot import WheelInstallInputs


def runtime_installer() -> ModuleType:
    return cast(Callable[[], ModuleType], self_install._runtime_installer)()


def source(tmp_path: Path, version: str = "1.2.3-dev1") -> tuple[Path, Path, Path]:
    payload = tmp_path / "source"
    package = payload / "src" / "worktree_manager"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (package / "__main__.py").write_text("", encoding="utf-8")
    (payload / "pyproject.toml").write_text(
        f'[project]\nname = "copilot-extensions-worktree-manager"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    uv = tmp_path / "uv"
    uv.touch()
    return payload, tmp_path / "root", uv


def capture(adapter: ModuleType, monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, str, dict[str, Any]]]:
    calls: list[tuple[Path, str, dict[str, Any]]] = []

    @contextmanager
    def candidate(root: Path, version: str, **kwargs: Any) -> Iterator[SimpleNamespace]:
        calls.append((root, version, kwargs))
        yield SimpleNamespace(slot=root / "versions" / version, reused=False)

    monkeypatch.setattr(adapter.engine, "numbered_candidate", candidate)
    return calls


def bundled_wheel_source(tmp_path: Path) -> tuple[Path, Path, Path]:
    payload, root, _ = source(tmp_path)
    manager = Path(__file__).resolve().parents[1]
    data = tomllib.loads((manager / "pyproject.toml").read_text(encoding="utf-8"))
    declaration = data["tool"]["uv"]["sources"]["dbus-next"]
    wheel = Path(declaration["path"])
    (payload / wheel).parent.mkdir(parents=True)
    shutil.copyfile(manager / wheel, payload / wheel)
    with (payload / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            'dependencies = ["dbus-next==0.2.3 ; sys_platform == \'linux\'"]\n'
            '[tool.uv.sources]\ndbus-next = {path = ' + json.dumps(wheel.as_posix()) + '}\n'
        )
    return payload, root, wheel


def attribute_source(adapter: ModuleType, payload: Path, version: str = "1.2.3-dev1") -> str:
    (payload / "plugin.json").write_text(json.dumps({
        "name": "worktree-manager", "version": version,
    }), encoding="utf-8")
    digest = adapter.runtime_source.assembly_hash(payload)
    generation: str = adapter.runtime_source.runtime_version(version, "a" * 40, digest)
    (payload / "runtime-source.json").write_text(json.dumps({
        "schema": adapter.runtime_source.SOURCE_SCHEMA, "version": 1,
        "plugin": "worktree-manager", "package_version": version,
        "source_commit": "a" * 40, "source_tree": "b" * 40,
        "assembly_hash": digest, "runtime_version": generation,
    }), encoding="utf-8")
    return generation


@contextmanager
def closed_inputs(
    tmp_path: Path, content: bytes = b"constructor fixture",
    extra_wheels: tuple[tuple[str, str, str, bytes], ...] = (),
) -> Iterator[WheelInstallInputs]:
    from installer_wheel_snapshot import WheelInstallInputs, retain_wheel_inputs

    filename = "copilot_extensions_worktree_manager-1.2.3.dev1-py3-none-any.whl"
    archives, expected, distributions = {}, {}, []
    for name, version, filename, content in (
        ("copilot-extensions-worktree-manager", "1.2.3.dev1", filename, content), *extra_wheels,
    ):
        wheel = tmp_path / filename
        wheel.write_bytes(content)
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        archives[filename], expected[filename] = wheel, digest
        distributions.append((name, version, filename, digest))
    with retain_wheel_inputs(
        archives, expected, max_wheels=len(archives),
        max_archive_bytes=1 << 20, max_total_bytes=1 << 20,
        max_snapshot_bytes=1 << 20, deadline=time.monotonic() + 120,
    ) as snapshot:
        yield WheelInstallInputs(
            snapshot, '{"fixture":"complete-manager-closure"}',
            tuple(distributions),
        )
