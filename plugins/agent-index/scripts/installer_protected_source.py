"""Optional Linux installation-owner handoff, before importing construction code."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from typing import Any

PROTECTED_INSTALLATION_CONTRACT = {
    "schema": "copilot-extensions.protected-installation",
    "version": 1,
    "provider": "linux-root/v1",
    "entrypoint": "bootstrap-protected-install.sh",
    "operation": "install-prepared-application",
}
_POLICY = "protected-installation.json"
_MAX_ENTRIES = 100_000
_MOUNTINFO = Path("/proc/self/mountinfo")
_FILESYSTEMS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "tmpfs"}
_metadata = Path.lstat


def _native_mounts(paths: tuple[Path, ...]) -> dict[Path, tuple[bytes, Path]]:
    """Refuse mode-bit emulation and foreign filesystems, including nested mounts."""
    with _MOUNTINFO.open("rb") as stream:
        content = stream.read((4 << 20) + 1)
    if len(content) > 4 << 20:
        raise ValueError("protected installation mount table exceeds its bound")
    root_seen = False
    mounts: dict[Path, tuple[bytes, Path]] = {}
    for line in content.splitlines():
        before, separator, after = line.partition(b" - ")
        fields, filesystem = before.split(), after.split()
        if not separator or len(fields) < 6 or len(filesystem) < 3:
            raise ValueError("protected installation mount table is unavailable")
        name = re.sub(
            rb"\\(040|011|012|134)",
            lambda match: bytes([int(match[1], 8)]),
            fields[4],
        )
        mount = Path(os.fsdecode(name))
        if not mount.is_absolute() or ".." in mount.parts:
            raise ValueError("protected installation mount table is invalid")
        root_seen |= mount == Path("/")
        if any(
            mount.is_relative_to(path) or path.is_relative_to(mount) for path in paths
        ):
            origin = Path(
                os.fsdecode(
                    re.sub(
                        rb"\\(040|011|012|134)",
                        lambda match: bytes([int(match[1], 8)]),
                        fields[3],
                    )
                )
            )
            if not origin.is_absolute() or ".." in origin.parts:
                raise ValueError("protected installation mount table is invalid")
            if mount in mounts or any(
                mount != path and mount.is_relative_to(path) for path in paths
            ):
                raise ValueError(
                    "protected installation has nested or ambiguous mounts"
                )
            mounts[mount] = (fields[2], origin)
            if os.fsdecode(filesystem[0]) not in _FILESYSTEMS:
                raise ValueError(
                    "protected installation requires supported native Linux filesystems"
                )
    if not root_seen:
        raise ValueError("protected installation has no native root mount")
    result: dict[Path, tuple[bytes, Path]] = {}
    for path in paths:
        mount = max(
            (mount for mount in mounts if path.is_relative_to(mount)),
            key=lambda mount: len(mount.parts),
        )
        device, origin = mounts[mount]
        result[path] = (device, origin / path.relative_to(mount))
    return result


def _overlap(first: tuple[bytes, Path], second: tuple[bytes, Path]) -> bool:
    return first[0] == second[0] and (
        first[1].is_relative_to(second[1]) or second[1].is_relative_to(first[1])
    )


def select_entry(code: Path) -> Path:
    """Require the selected provider's literal contract without executing it.

    Discovery is not privilege or source approval. The existing native owner
    still selects and protects the entry before invoking the requested operation.
    """
    _path(str(code))
    for path in (code, *code.parents):
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise ValueError("protected provider has indirect directory ancestry")
    provider = code / "installer_protected_source.py"
    if not stat.S_ISREG(provider.lstat().st_mode) or provider.stat().st_size > 1 << 20:
        raise ValueError("selected runtime has no bounded protected provider")
    with provider.open("rb") as stream:
        content = stream.read((1 << 20) + 1)
    if len(content) > 1 << 20:
        raise ValueError("selected runtime has no bounded protected provider")
    try:
        declarations = [
            node.value
            for node in ast.parse(content).body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "PROTECTED_INSTALLATION_CONTRACT"
                for target in node.targets
            )
        ]
        contract = ast.literal_eval(declarations[0]) if len(declarations) == 1 else None
    except (SyntaxError, ValueError, TypeError) as error:
        raise ValueError(
            "selected runtime lacks the protected installation contract"
        ) from error
    if (
        not isinstance(contract, dict)
        or contract != PROTECTED_INSTALLATION_CONTRACT
        or type(contract.get("version")) is not int
    ):
        raise ValueError("selected runtime lacks the protected installation contract")
    entry = code / str(PROTECTED_INSTALLATION_CONTRACT["entrypoint"])
    if not stat.S_ISREG(entry.lstat().st_mode) or entry.stat().st_size > 65_536:
        raise ValueError("selected protected entry is not an ordinary file")
    with entry.open("rb") as stream:
        entry_content = stream.read(65_537)
    if len(entry_content) > 65_536 or not entry_content.startswith(
        b"#!/bin/bash -p\n# protected-installation-provider: linux-root/v1\n"
    ):
        raise ValueError(
            "selected native entry lacks the protected installation contract"
        )
    return entry


@dataclass(frozen=True)
class ProtectedApplication:
    """Selections from an independently installed administrative policy."""

    snapshot: Path
    component: str
    root: Path
    uv: Path
    health_module: str
    expected_version: str
    expected_hash: str
    home: Path
    cache: Path
    temporary: Path
    global_bin: Path | None
    arguments: tuple[str, ...]
    repair: bool


def _path(value: object) -> Path:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or value.startswith("//")
        or any(char in value for char in "\r\n\0")
    ):
        raise ValueError("protected installation requires an absolute ordinary path")
    path = Path(value)
    if str(path) != value or ".." in path.parts or path == Path("/"):
        raise ValueError("protected installation requires a normalized non-root path")
    return path


def _protected(path: Path, *, directory: bool) -> None:
    """Require native root custody, including every replaceable ancestor."""
    for candidate in (*reversed(path.parents), path):
        info = _metadata(candidate)
        is_directory = candidate != path or directory
        if (
            info.st_uid != 0
            or info.st_mode & 0o022
            or not (
                stat.S_ISDIR(info.st_mode)
                if is_directory
                else stat.S_ISREG(info.st_mode)
            )
        ):
            raise ValueError(
                "protected installation path is not under native root custody"
            )


def _output_directory(path: Path) -> None:
    if sys.platform != "linux":
        raise ValueError("protected output ACL inspection requires Linux")
    try:
        acl = os.getxattr(path, "system.posix_acl_default", follow_symlinks=False)
    except OSError as error:
        if error.errno == errno.ENODATA:
            return
        raise ValueError("protected output default ACL cannot be inspected") from error
    if acl:
        raise ValueError("protected output directory has a default ACL")


def _os_file(path: Path) -> Path:
    """Follow only a completely protected, bounded OS-prefix link chain."""
    prefix = Path(sys.base_prefix)
    _protected(prefix, directory=True)
    for _ in range(40):
        if path == prefix or not path.is_relative_to(prefix):
            raise ValueError("protected interpreter link leaves its OS prefix")
        current = prefix
        for part in path.relative_to(prefix).parts:
            current /= part
            info = _metadata(current)
            if (
                info.st_uid != 0
                or info.st_mode & 0o022
                and not stat.S_ISLNK(info.st_mode)
            ):
                raise ValueError("protected interpreter link is replaceable")
            if stat.S_ISLNK(info.st_mode):
                path = Path(
                    os.path.abspath(
                        current.parent
                        / os.readlink(current)
                        / path.relative_to(current)
                    )
                )
                break
            if not (
                stat.S_ISDIR(info.st_mode)
                if current != path
                else stat.S_ISREG(info.st_mode)
            ):
                raise ValueError(
                    "protected interpreter link is not an ordinary OS file"
                )
        else:
            return path
    raise ValueError("protected interpreter link chain exceeds its bound")


def _tree(root: Path, *, deadline: float, internal_links: bool = False) -> None:
    """Inspect protection, not incumbent provenance or installation receipts."""
    _protected(root, directory=True)
    pending = [root]
    count = 0
    while pending:
        directory = pending.pop()
        if internal_links:
            _output_directory(directory)
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                if count > _MAX_ENTRIES or time.monotonic() >= deadline:
                    raise ValueError(
                        "protected installation admission exceeds its inspection bound"
                    )
                path = Path(entry.path)
                info = _metadata(path)
                if internal_links and stat.S_ISSOCK(info.st_mode) and info.st_uid == 0:
                    continue
                if stat.S_ISLNK(info.st_mode) and internal_links and info.st_uid == 0:
                    # Existing selectors may be stale. Do not follow them or use
                    # their provenance as an incoming-replacement prerequisite.
                    link = Path(os.readlink(path))
                    if ".." in link.parts:
                        raise ValueError(
                            "protected installation link has indirect parent traversal"
                        )
                    target = Path(os.path.abspath(path.parent / link))
                    if not target.is_relative_to(root):
                        if not target.is_relative_to(Path(sys.base_prefix)):
                            raise ValueError(
                                "protected installation link leaves its addressed root"
                            )
                        _os_file(target)
                    continue
                if (
                    info.st_uid != 0
                    or info.st_mode & 0o022
                    or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                ):
                    raise ValueError(
                        "protected installation tree contains replaceable content"
                    )
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)


def _document(path: Path) -> dict[str, Any]:
    _protected(path, directory=False)
    with path.open("rb") as stream:
        raw = stream.read(65_537)
    if len(raw) > 65_536:
        raise ValueError("protected installation policy exceeds its document bound")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        record: dict[str, Any] = {}
        for key, value in pairs:
            if key in record:
                raise ValueError("protected installation policy has duplicate fields")
            record[key] = value
        return record

    record = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(record, dict):
        raise ValueError("protected installation policy must be an object")
    return record


def _root_identity() -> bool:
    if sys.platform != "linux":
        return False
    return all(
        identity() == 0
        for identity in (
            os.getuid,
            os.geteuid,
            os.getgid,
            os.getegid,
        )
    )


def admit(policy: Path, *, code: Path) -> ProtectedApplication:
    """Admit an already protected seed and addressed operation without effects."""
    if sys.platform != "linux" or not _root_identity():
        raise ValueError(
            "protected installation provider supports Linux root identity only"
        )
    _path(str(code))
    _path(str(policy))
    if policy != code / _POLICY:
        raise ValueError(
            "protected installation policy must belong to its selected provider"
        )
    if code.name == "installer-engine" and code.parent.name == "libs":
        raise ValueError(
            "protected installation requires a packaged seed, not developer fallbacks"
        )
    deadline = time.monotonic() + 30
    _native_mounts((code, Path(sys.base_prefix)))
    _tree(code, deadline=deadline)
    record = _document(policy)
    paths = ("snapshot", "root", "uv", "home", "cache", "temporary")
    fields = {
        "contract",
        *paths,
        "component",
        "health_module",
        "expected_version",
        "expected_hash",
        "global_bin",
        "arguments",
        "repair",
    }
    if (
        set(record) != fields
        or record.get("contract") != PROTECTED_INSTALLATION_CONTRACT
        or type(record["contract"].get("version")) is not int
        or type(record.get("repair")) is not bool
        or any(
            not isinstance(record.get(key), str) or not record[key]
            for key in (
                "component",
                "health_module",
                "expected_version",
                "expected_hash",
            )
        )
        or not isinstance(record.get("arguments"), list)
        or len(record["arguments"]) > 64
        or any(
            not isinstance(value, str) or "\0" in value for value in record["arguments"]
        )
        or sum(len(value.encode("utf-8")) for value in record["arguments"]) > 65_536
    ):
        raise ValueError("unsupported protected installation policy")
    selected = {key: _path(record[key]) for key in paths}
    global_bin = None if record["global_bin"] is None else _path(record["global_bin"])
    mounts = _native_mounts(
        (
            code,
            *selected.values(),
            *((global_bin,) if global_bin is not None else ()),
        )
    )
    for key in ("snapshot", "root", "home", "cache", "temporary"):
        _tree(selected[key], deadline=deadline, internal_links=key != "snapshot")
    _protected(selected["uv"], directory=False)
    if not selected["uv"].stat().st_mode & 0o111:
        raise ValueError("protected installation UV selection is not executable")
    if global_bin is not None:
        _tree(global_bin, deadline=deadline, internal_links=True)
    immutable = (code, selected["snapshot"], selected["uv"])
    writable = tuple(
        selected[key] for key in ("root", "home", "cache", "temporary")
    ) + ((global_bin,) if global_bin is not None else ())
    for output in writable:
        if any(_overlap(mounts[output], mounts[source]) for source in immutable):
            raise ValueError(
                "protected installation outputs overlap admitted code or inputs"
            )
    if any(
        _overlap(mounts[first], mounts[second])
        for index, first in enumerate(writable)
        for second in writable[index + 1 :]
    ):
        raise ValueError("protected installation output roles must be disjoint")
    return ProtectedApplication(
        snapshot=selected["snapshot"],
        root=selected["root"],
        uv=selected["uv"],
        home=selected["home"],
        cache=selected["cache"],
        temporary=selected["temporary"],
        component=record["component"],
        health_module=record["health_module"],
        expected_version=record["expected_version"],
        expected_hash=record["expected_hash"],
        global_bin=global_bin,
        arguments=tuple(record["arguments"]),
        repair=record["repair"],
    )


def install(selected: ProtectedApplication, *, code: Path) -> dict[str, str]:
    """Compose the existing Source and application owners; never retry their effects."""
    # Called only by the isolated native entry after admission. In particular,
    # importing this module in an elevated controller is not a trusted bootstrap.
    sys.path.insert(0, str(code))
    import install_source
    from installer_native_executable import require_native_executable

    require_native_executable(selected.uv)

    source = install_source.select_project(selected.snapshot, selected.component)
    if not source.payload.is_relative_to(selected.snapshot) or any(
        not package.is_relative_to(selected.snapshot)
        for package in install_source.engine._package_roots(
            source.payload,
            **({"closed_wheels": True} if source.closed_wheel_source else {}),
        )
    ):
        raise ValueError(
            "protected incoming package closure leaves its admitted snapshot"
        )
    if (
        "application" not in source.project
        or not install_source.engine.application_uv_selection(
            install_source._member(selected.snapshot, source.project["application"])
        )
    ):
        raise ValueError("protected application lacks absolute UV executable selection")
    application = install_source._member(
        selected.snapshot, source.project["application"]
    )
    if (
        install_source._read(application / "application.json").get("name")
        == "agent-worktrees"
    ):
        raise ValueError("protected application requires unsupported callback context")
    return install_source.install_application(
        selected.snapshot,
        selected.component,
        source.payload,
        selected.root,
        uv=selected.uv,
        base_python=Path(sys.executable),
        health_module=selected.health_module,
        expected_version=selected.expected_version,
        expected_hash=selected.expected_hash,
        arguments=selected.arguments,
        repair=selected.repair,
        global_bin=selected.global_bin,
    )


def main() -> int:
    """Consume only the installed policy; command-line overrides are unsupported."""
    if len(sys.argv) != 1 or not sys.flags.isolated or not sys.flags.no_site:
        raise ValueError(
            "protected installation requires its isolated native bootstrap"
        )
    if sys.version_info < (3, 11) or sys.prefix != sys.base_prefix:
        raise ValueError(
            "protected installation requires the OS CPython 3.11+ interpreter"
        )
    code = Path(__file__).absolute().parent
    selected = admit(code / _POLICY, code=code)
    if not (code / "versioned_runtime.py").is_file():
        raise ValueError(
            "protected installation requires its complete packaged installer family"
        )
    os.environ.clear()
    os.environ.update(
        PATH="/usr/bin:/bin",
        LANG="C.UTF-8",
        LC_ALL="C.UTF-8",
        HOME=str(selected.home),
        XDG_CACHE_HOME=str(selected.cache),
        UV_CACHE_DIR=str(selected.cache / "uv"),
        TMPDIR=str(selected.temporary),
        PYTHONDONTWRITEBYTECODE="1",
    )
    os.chdir(selected.snapshot)
    print(json.dumps(install(selected, code=code)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
