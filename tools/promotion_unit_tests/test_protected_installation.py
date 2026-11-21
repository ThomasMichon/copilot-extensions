"""Protected installation contracts with in-memory native observation actors."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import sys
from types import ModuleType, SimpleNamespace
from typing import Any, ClassVar, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs/versioned-runtime"))
sys.path.insert(0, str(ROOT / "libs/installer-engine"))
import installer_native_executable as executable  # noqa: E402
import installer_protected_source as protected  # noqa: E402
import installer_engine_health as health  # noqa: E402


@dataclass
class Memory:
    files: dict[str, bytes] = field(default_factory=dict)
    foreign: set[str] = field(default_factory=set)
    default_acl: set[str] = field(default_factory=set)
    identity: bool = True


class MemoryPath(PurePosixPath):
    memory: ClassVar[Memory]

    def stat(self) -> os.stat_result:
        content = self.memory.files.get(str(self))
        mode = stat.S_IFDIR | 0o755 if content is None else stat.S_IFREG | 0o700
        return cast(
            os.stat_result,
            SimpleNamespace(
                st_mode=mode,
                st_dev=1,
                st_ino=1,
                st_nlink=1,
                st_uid=1001 if str(self) in self.memory.foreign else 0,
                st_gid=0,
                st_size=len(content or b""),
                st_file_attributes=0,
            ),
        )

    def lstat(self) -> os.stat_result:
        return self.stat()

    def open(self, mode: str = "r") -> io.BytesIO:
        assert mode == "rb", "the native model never writes"
        return io.BytesIO(self.memory.files[str(self)])

    def is_file(self) -> bool:
        return str(self) in self.memory.files

    def is_symlink(self) -> bool:
        return False


def image(*, wide: bool = True, program: int = 1) -> bytes:
    size, width = (64, 56) if wide else (52, 32)
    content = bytearray(size + width)
    content[:16] = b"\x7fELF" + bytes((2 if wide else 1, 1, 1)) + bytes(9)
    struct.pack_into("<HHI", content, 16, 2, 62 if wide else 3, 1)
    struct.pack_into("<Q" if wide else "<I", content, 32 if wide else 28, size)
    struct.pack_into("<HHH", content, 52 if wide else 40, size, width, 1)
    struct.pack_into("<I", content, size, program)
    return bytes(content)


@pytest.fixture
def memory(monkeypatch: pytest.MonkeyPatch) -> Memory:
    model = Memory()
    monkeypatch.setattr(MemoryPath, "memory", model, raising=False)
    monkeypatch.setattr(protected, "Path", MemoryPath)
    monkeypatch.setattr(protected, "_metadata", lambda path: path.lstat())
    monkeypatch.setattr(protected, "_root_identity", lambda: model.identity)
    monkeypatch.setattr(
        protected, "_MOUNTINFO", MemoryPath("/unit-protected/mountinfo")
    )
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "base_prefix", "/unit-protected/os")
    monkeypatch.setattr(sys, "path", sys.path[:])
    model.files["/unit-protected/mountinfo"] = b"1 0 0:1 / / rw - ext4 fixture rw\n"
    original_scan = os.scandir

    def scan(path: Any) -> Any:
        if not isinstance(path, MemoryPath):
            return original_scan(path)
        children = set()
        for name in model.files:
            candidate = MemoryPath(name)
            if candidate != path and candidate.is_relative_to(path):
                children.add(str(path / candidate.relative_to(path).parts[0]))
        return nullcontext(
            iter(SimpleNamespace(path=name) for name in sorted(children))
        )

    def acl(path: Any, _name: str, *, follow_symlinks: bool = True) -> bytes:
        return b"default ACL" if str(path) in model.default_acl else b""

    monkeypatch.setattr(os, "scandir", scan)
    monkeypatch.setattr(os, "getxattr", acl, raising=False)
    return model


def policy(memory: Memory) -> tuple[MemoryPath, dict[str, Any]]:
    code = MemoryPath("/unit-protected/seed")
    record: dict[str, Any] = {
        "contract": dict(protected.PROTECTED_INSTALLATION_CONTRACT),
        "component": "example-app",
        "snapshot": "/unit-protected/input",
        "root": "/unit-protected/runtime",
        "uv": "/unit-protected/tools/uv",
        "health_module": "example_app",
        "expected_version": "1.0.profile.example",
        "expected_hash": "a" * 64,
        "home": "/unit-protected/home",
        "cache": "/unit-protected/cache",
        "temporary": "/unit-protected/temporary",
        "global_bin": None,
        "arguments": [],
        "repair": False,
    }
    memory.files[str(code / "protected-installation.json")] = json.dumps(
        record
    ).encode()
    memory.files[record["uv"]] = image()
    return code, record


@pytest.mark.parametrize("version", ["current", "old"])
def test_selected_contract_probe_does_not_execute_provider(
    memory: Memory, version: str
) -> None:
    code, _ = policy(memory)
    contract = dict(protected.PROTECTED_INSTALLATION_CONTRACT)
    if version == "old":
        contract["version"] = 0
    memory.files[str(code / "installer_protected_source.py")] = (
        "PROTECTED_INSTALLATION_CONTRACT = "
        + repr(contract)
        + "\nraise AssertionError('capability probe executed code')\n"
    ).encode()
    memory.files[str(code / "bootstrap-protected-install.sh")] = (
        b"#!/bin/bash -p\n# protected-installation-provider: linux-root/v1\nexit 23\n"
    )
    if version == "old":
        with pytest.raises(ValueError, match="lacks the protected"):
            protected.select_entry(cast(Path, code))
    else:
        assert str(protected.select_entry(cast(Path, code))) == str(
            code / "bootstrap-protected-install.sh"
        )


@pytest.mark.parametrize(
    ("origin", "mount", "filesystem", "refusal"),
    [
        ("net:[123]", "/unrelated/namespaces/example", "nsfs", None),
        ("mnt:[456]", "/unrelated/other", "nsfs", None),
        ("/", "/unrelated/native\\040mount", "ext4", None),
        ("net:[123]", "/unit-protected/input", "nsfs", "mount table is invalid"),
        ("relative", "/unit-protected/input", "ext4", "mount table is invalid"),
        ("/data/../input", "/unit-protected/input", "ext4", "mount table is invalid"),
        ("net:[123]", "relative", "nsfs", "mount table is invalid"),
        ("net:[123]", "/unrelated/../other", "nsfs", "mount table is invalid"),
        ("/", "/unit-protected/input", "nsfs", "supported native Linux filesystems"),
        ("/", "/unit-protected/input/nested", "ext4", "nested or ambiguous mounts"),
        ("/", "/", "ext4", "nested or ambiguous mounts"),
        (
            "/unit-protected/input",
            "/unit-protected/runtime",
            "ext4",
            "outputs overlap admitted code or inputs",
        ),
    ],
    ids=[
        "unrelated-network-namespace",
        "unrelated-mount-namespace",
        "unrelated-escaped-mountpoint",
        "selected-namespace-origin",
        "selected-relative-origin",
        "selected-parent-origin",
        "unrelated-relative-mountpoint",
        "unrelated-parent-mountpoint",
        "selected-unsupported-filesystem",
        "selected-nested-mount",
        "ambiguous-root-mount",
        "selected-bind-input-alias",
    ],
)
def test_mount_admission_only_interprets_addressed_origins(
    memory: Memory, origin: str, mount: str, filesystem: str, refusal: str | None
) -> None:
    code, record = policy(memory)
    memory.files["/unit-protected/mountinfo"] += (
        f"2 1 0:1 {origin} {mount} rw - {filesystem} fixture rw\n".encode()
    )
    path = cast(Path, code / "protected-installation.json")
    if refusal is not None:
        with pytest.raises(ValueError, match=refusal):
            protected.admit(path, code=cast(Path, code))
    else:
        selected = protected.admit(path, code=cast(Path, code))
        assert str(selected.snapshot) == record["snapshot"]
        assert str(selected.root) == record["root"]


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "unregistered-identity",
        "foreign-owner",
        "default-acl",
        "output-alias",
        "unknown-field",
    ],
)
def test_policy_admission_preserves_closed_native_roles(
    memory: Memory, case: str
) -> None:
    code, record = policy(memory)
    if case == "unregistered-identity":
        memory.identity = False
    elif case == "foreign-owner":
        memory.foreign.add(record["root"])
    elif case == "default-acl":
        memory.default_acl.add(record["root"])
    elif case == "output-alias":
        record["root"] = record["snapshot"]
    elif case == "unknown-field":
        record["approved"] = True
    path = code / "protected-installation.json"
    memory.files[str(path)] = json.dumps(record).encode()
    if case == "valid":
        selected = protected.admit(cast(Path, path), code=cast(Path, code))
        assert str(selected.root) == record["root"]
        assert str(selected.uv) == record["uv"]
        assert selected.expected_hash == record["expected_hash"]
    else:
        with pytest.raises(ValueError):
            protected.admit(cast(Path, path), code=cast(Path, code))


@pytest.mark.parametrize(
    "case",
    [
        "installed",
        "closed-wheels",
        "closed-escape",
        "old-launcher",
        "manager-context",
        "script-uv",
    ],
)
def test_protected_dispatch_uses_admitted_selection_without_fallback(
    memory: Memory,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    code, record = policy(memory)
    selected = protected.admit(
        cast(Path, code / "protected-installation.json"),
        code=cast(Path, code),
    )
    app = MemoryPath(record["snapshot"]) / "application"
    memory.files[str(app / "install.sh")] = (
        b"#!/bin/sh\n# installer-uv-selection: absolute/v1\n"
        if case != "old-launcher"
        else b"#!/bin/sh\nexec uv\n"
    )
    memory.files[str(app / "application.json")] = json.dumps(
        {
            "name": "agent-worktrees" if case == "manager-context" else "example-app",
        }
    ).encode()
    if case == "script-uv":
        memory.files[record["uv"]] = b"#!/controller/python\npass\n"
    monkeypatch.setattr(health, "_open", lambda path: path.open("rb"))
    source = ModuleType("install_source")
    payload = MemoryPath(record["snapshot"]) / "payload"
    closed = case in {"closed-wheels", "closed-escape"}

    def package_roots(_payload: Any, **options: Any) -> tuple[MemoryPath, ...]:
        if closed and not options.get("closed_wheels"):
            raise ValueError("development wheel lacks release-age proof")
        if not closed:
            assert options == {}, "ordinary source defaults changed"
        return (
            MemoryPath("/unit-protected/foreign")
            if case == "closed-escape"
            else payload,
        )

    vars(source).update(
        select_project=lambda *_: SimpleNamespace(
            payload=payload,
            project={"application": "application"},
            closed_wheel_source=closed,
        ),
        engine=SimpleNamespace(
            _package_roots=package_roots,
            application_uv_selection=health.application_uv_selection,
        ),
        _member=lambda root, name: root / name,
        _read=lambda path: json.loads(memory.files[str(path)]),
    )
    calls: list[dict[str, Any]] = []

    def install(
        snapshot: Path, component: str, chosen: Path, root: Path, **options: Any
    ) -> dict[str, str]:
        calls.append(
            {
                "snapshot": snapshot,
                "component": component,
                "payload": chosen,
                "root": root,
                **options,
            }
        )
        return {"state": "installed", "slot": str(root / "versions/example")}

    vars(source)["install_application"] = install
    monkeypatch.setitem(sys.modules, "install_source", source)
    if case not in {"installed", "closed-wheels"}:
        with pytest.raises(ValueError):
            protected.install(selected, code=cast(Path, code))
        assert calls == []
    else:
        assert (
            protected.install(selected, code=cast(Path, code))["state"] == "installed"
        )
        assert len(calls) == 1
        assert calls[0]["snapshot"] == selected.snapshot
        assert calls[0]["root"] == selected.root
        assert calls[0]["uv"] == selected.uv
        assert calls[0]["expected_hash"] == selected.expected_hash
        assert calls[0]["arguments"] == selected.arguments


@pytest.mark.parametrize(
    "case", ["native64", "native32", "dynamic-interpreter", "dynamic-section"]
)
def test_uv_requires_static_native_inputs(memory: Memory, case: str) -> None:
    path = MemoryPath("/unit-protected/uv")
    memory.files[str(path)] = image(
        wide=case != "native32",
        program=3
        if case == "dynamic-interpreter"
        else 2
        if case == "dynamic-section"
        else 1,
    )
    if case.startswith("dynamic"):
        with pytest.raises(ValueError, match="static ELF"):
            executable.require_native_executable(cast(Path, path))
    else:
        executable.require_native_executable(cast(Path, path))
