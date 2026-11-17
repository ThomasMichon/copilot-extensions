"""Explicit consuming-repository association in the existing Manager config."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import cast

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from . import source_config
from .config_write_lock import config_write_lock
from .runtime_source_repository import _ordinary

SCHEMA = "worktree-manager.repository-suite-association"
TABLE = "repository_suite"
MAX_CONFIG_BYTES = 65536


def _path(value: object, label: str) -> Path:
    text = os.fspath(value) if isinstance(value, Path) else value
    if (
        not isinstance(text, str) or not text or text != text.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in text)
    ):
        raise ValueError(f"{label} requires an absolute filesystem path")
    path = Path(text)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} requires an absolute path without parent components")
    return path


def _key(path: Path | None) -> str | None:
    if path is None:
        return None
    # Compare retained identities without probing an unavailable old repository.
    text = os.fspath(path)
    if os.name == "nt":
        if text[:8].casefold() == "\\\\?\\unc\\":
            text = "\\\\" + text[8:]
        elif text.startswith("\\\\?\\"):
            text = text[4:]
    return os.path.normcase(os.path.normpath(text))


def _config_path(root: Path) -> Path:
    selected = _path(root, "Manager configuration root")
    _ordinary(selected / source_config.CONFIG_NAME)
    return source_config.config_path(selected)


def _read(path: Path) -> tuple[str, dict[str, object], Path | None]:
    _ordinary(path)
    try:
        metadata = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Manager configuration must be an ordinary file")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise ValueError("Manager configuration changed during observation")
            content = stream.read(MAX_CONFIG_BYTES + 1)
    except FileNotFoundError:
        return "", {}, None
    if len(content) > MAX_CONFIG_BYTES:
        raise ValueError("Manager configuration exceeds its 64 KiB input budget")
    try:
        text = content.decode("utf-8")
        data = cast(dict[str, object], tomllib.loads(text))
    except (UnicodeError, tomllib.TOMLDecodeError, RecursionError):
        raise ValueError("Manager configuration must contain valid UTF-8 TOML") from None
    if TABLE not in data:
        return text, data, None
    table = data[TABLE]
    if (
        not isinstance(table, dict) or set(table) != {"version", "repository_root"}
        or type(table["version"]) is not int or table["version"] != 1
    ):
        raise ValueError("Unsupported repository-suite association table")
    return text, data, _path(table["repository_root"], "Associated repository root")


def read_association(root: Path) -> Path | None:
    """Observe only the explicitly addressed Manager config; never create it."""
    return _read(_config_path(root))[2]


def _equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, float) and isinstance(right, float) and math.isnan(left):
        return math.isnan(right)
    return left == right


def _replacement(content: str, original: dict[str, object], target: Path | None) -> bytes:
    kept: list[str] = []
    skipping = False
    if TABLE in original:
        for line in content.splitlines(keepends=True):
            stripped = line.strip()
            if stripped == f"[{TABLE}]":
                skipping = True
            elif stripped.startswith("["):
                skipping = False
            if not skipping:
                kept.append(line)
    else:
        kept.append(content)
    remainder = "".join(kept).rstrip("\r\n")
    replacement = remainder + ("\n" if remainder else "")
    expected = dict(original)
    expected.pop(TABLE, None)
    if target is not None:
        table: dict[str, object] = {"version": 1, "repository_root": str(target)}
        expected[TABLE] = table
        replacement += (
            ("\n" if replacement else "") + f"[{TABLE}]\nversion = 1\n"
            + "repository_root = " + json.dumps(str(target), ensure_ascii=False) + "\n"
        )
    try:
        actual = tomllib.loads(replacement)
    except tomllib.TOMLDecodeError:
        raise ValueError("Cannot replace a noncanonical repository-suite table") from None
    if not _equal(actual, expected):
        raise ValueError("Repository-suite change would alter unrelated configuration")
    encoded = replacement.encode("utf-8")
    if len(encoded) > MAX_CONFIG_BYTES:
        raise ValueError("Proposed Manager configuration exceeds its 64 KiB budget")
    return encoded


@dataclass(frozen=True)
class AssociationChange:
    before: Path | None
    proposed: Path | None
    applied: bool

    def document(self, root: Path, operation: str) -> dict[str, object]:
        current = self.proposed if self.applied else self.before
        return {
            "schema": SCHEMA, "version": 1, "operation": operation,
            "configuration_root": str(root),
            "repository_root": str(current) if current is not None else None,
            "proposed_repository_root": str(self.proposed) if self.proposed is not None else None,
            "changed": self.applied,
        }


def _check_expected(before: Path | None, target: Path | None, expected: Path | None) -> None:
    if _key(before) != _key(target) and _key(before) != _key(expected):
        raise ValueError("Current repository association differs from the expected root")


def _change(
    root: Path, target: Path | None, expected: Path | None, *, apply: bool,
) -> AssociationChange:
    if type(apply) is not bool:
        raise ValueError("Association apply must be a boolean")
    path = _config_path(root)
    expected = _path(expected, "Expected repository root") if expected is not None else None
    target = _path(target, "New repository root") if target is not None else None
    content, data, before = _read(path)
    if _key(before) == _key(target):
        return AssociationChange(before, target, False)
    if target is not None:
        _ordinary(target)
        if not target.is_dir():
            raise ValueError("New repository root must be an existing ordinary directory")
        target = target.resolve(strict=True)
        _ordinary(target)
    _check_expected(before, target, expected)
    if _key(before) == _key(target):
        return AssociationChange(before, target, False)
    _replacement(content, data, target)
    if not apply:
        return AssociationChange(before, target, False)
    with config_write_lock(path):
        content, data, before = _read(path)
        _check_expected(before, target, expected)
        if _key(before) == _key(target):
            return AssociationChange(before, target, False)
        if target is not None:
            _ordinary(target)
            if not target.is_dir():
                raise ValueError("New repository root became unavailable before publication")
        encoded = _replacement(content, data, target)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=".repository-suite-", suffix=".toml",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(encoded)
            _ordinary(path)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return AssociationChange(before, target, True)


def associate(
    root: Path, repository_root: Path, *, expect_current_root: Path | None = None,
    apply: bool = False,
) -> AssociationChange:
    return _change(root, repository_root, expect_current_root, apply=apply)


def clear(
    root: Path, *, expect_current_root: Path, apply: bool = False,
) -> AssociationChange:
    return _change(root, None, expect_current_root, apply=apply)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--describe"]:
        print(json.dumps({
            "schema": SCHEMA + ".capabilities", "version": 1,
            "operations": ["inspect", "associate", "clear"], "explicit_root": True,
            "read_only_default": True, "apply_required": True,
        }, sort_keys=True))
        return 0
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", type=Path, required=True)
    operations = parser.add_subparsers(dest="operation")
    operations.add_parser("inspect")
    setting = operations.add_parser("associate")
    setting.add_argument("--repository-root", type=Path, required=True)
    setting.add_argument("--expect-current-root", type=Path)
    setting.add_argument("--apply", action="store_true")
    removing = operations.add_parser("clear")
    removing.add_argument("--expect-current-root", type=Path, required=True)
    removing.add_argument("--apply", action="store_true")
    options = parser.parse_args(arguments)
    try:
        operation = options.operation or "inspect"
        if operation == "associate":
            result = associate(
                options.root, options.repository_root,
                expect_current_root=options.expect_current_root, apply=options.apply,
            )
        elif operation == "clear":
            result = clear(
                options.root, expect_current_root=options.expect_current_root, apply=options.apply,
            )
        else:
            current = read_association(options.root)
            result = AssociationChange(current, current, False)
        print(json.dumps(result.document(_config_path(options.root).parent, operation), sort_keys=True))
        return 0
    except (OSError, ValueError, RecursionError) as error:
        print(json.dumps({"schema": SCHEMA, "version": 1, "error": str(error)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
