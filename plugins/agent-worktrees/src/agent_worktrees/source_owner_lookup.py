"""Complete source-path routing without hydrating unrelated retained ledgers."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TypedDict, cast

import yaml

from . import config as cfg, record_io, tracking


class Route(TypedDict):
    stamp: list[int]
    source: str


def _identity(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _snapshot(ledger: Path) -> dict[str, list[int]]:
    entries = {}
    try:
        with os.scandir(ledger) as listing:
            for entry in listing:
                if not os.path.normcase(entry.name).endswith(".yaml"):
                    continue
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    raise ValueError("Source ownership contains an unsafe ledger path.")
                stat = entry.stat(follow_symlinks=False)
                entries[entry.name] = [
                    stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size,
                ]
    except FileNotFoundError:
        if ledger.exists():
            raise ValueError("Source owner routing changed during enumeration.") from None
    return entries


def _cached(path: Path, ledger: Path) -> dict[str, Route]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != 2 or data.get("ledger") != str(ledger):
            return {}
        entries = data["entries"]
        if not isinstance(entries, dict) or any(
            not isinstance(name, str) or not isinstance(row, dict)
            or not isinstance(row.get("stamp"), list) or len(row["stamp"]) != 5
            or any(type(value) is not int for value in row["stamp"])
            or not isinstance(row.get("source"), str)
            for name, row in entries.items()
        ):
            return {}
        return cast(dict[str, Route], entries)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def _matching(ledger: Path, workspace: Path) -> set[Path]:
    ledger = ledger.resolve()
    key = hashlib.sha256(os.fsencode(str(ledger))).hexdigest()
    cache = cfg.install_dir() / "source-owner-routing" / (key + ".json")
    previous = _cached(cache, ledger)
    for _ in range(2):
        snapshot = _snapshot(ledger)
        indexed: dict[str, Route] = {}
        for name, stamp in snapshot.items():
            row = previous.get(name)
            if row is None or row["stamp"] != stamp:
                data = record_io.read_record_mapping(ledger / name)
                source = data.get("worktree_path", "")
                if (
                    not isinstance(source, str)
                    or "worktree_id" not in data or "branch" not in data
                ):
                    raise ValueError("Declared source ownership is malformed.")
                row = {"stamp": stamp, "source": source}
            indexed[name] = row
        if _snapshot(ledger) == snapshot:
            if indexed != previous:
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    tracking._atomic_write(cache, json.dumps({
                        "schema": 2, "ledger": str(ledger), "entries": indexed,
                    }, separators=(",", ":")))
                except OSError:
                    pass
            identity = _identity(workspace)
            return {
                ledger / name for name, row in indexed.items()
                if row["source"] and _identity(row["source"]) == identity
            }
        previous = indexed
    raise ValueError("Source owner routing changed during lookup; requalify before publication.")


def matching(ledgers: tuple[Path, ...], workspace: Path) -> set[Path]:
    """Enumerate every ledger entry; cached routes never replace current metadata."""
    found: set[Path] = set()
    for ledger in ledgers:
        try:
            found.update(_matching(ledger, workspace))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise ValueError(f"Declared source ownership is unavailable: {exc}") from exc
    return found
