"""Read-only owned delivery validation; filesystem capabilities are injected."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence

import instruction_delivery as delivery


@dataclass(frozen=True)
class DeliveryIO:
    safe: Callable[[Path, PurePosixPath], Path]
    read: Callable[[Path, int], bytes]
    indirect: Callable[[Path], bool]


def read_fallback(
    root: Path, entry: Mapping[str, object], io: DeliveryIO, *,
    allow_content_edits: bool = False,
) -> bytes | None:
    if entry.get("deliveryMode") != "selector":
        return None
    delivery.validate_lock_delivery(entry)
    fallback = entry["fallback"]
    path = io.safe(root, PurePosixPath(fallback["destination"]))
    raw = io.read(path, delivery.MAX_FALLBACK_BYTES)
    marker = delivery.parse_marker(raw)
    if allow_content_edits:
        delivery.validate_local_frontmatter(raw, marker)
    else:
        delivery.validate_local_template(raw, marker)
    expected = {
        key: value for key, value in entry.items() if key in delivery.BASE_KEYS
    } | {"renderedBytes": fallback["renderedBytes"]}
    if (
        any(marker.get(key) != value for key, value in expected.items())
        or marker.get("deliveryKind") != "body"
        or (not allow_content_edits and (
            hashlib.sha256(raw).hexdigest() != fallback["renderedSha256"]
            or not delivery.complete_body(raw, marker)
        ))
    ):
        raise ValueError("reviewed fallback integrity, ownership or body coverage mismatch")
    return raw


def orphan_fallbacks(
    root: Path, entries: Mapping[str, Mapping[str, object]], io: DeliveryIO
) -> list[str]:
    directory = io.safe(root, PurePosixPath(".github/copilot/context-fallbacks"))
    if not directory.exists():
        return []
    owned = {
        str(entry["fallback"]["destination"])
        for entry in entries.values() if entry.get("deliveryMode") == "selector"
    }
    found: list[str] = []
    for current, directories, names in os.walk(directory):
        directories[:] = [
            name for name in directories if not io.indirect(Path(current) / name)
        ]
        for name in names:
            if not name.endswith(".md"):
                continue
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            if relative not in owned:
                path = io.safe(root, PurePosixPath(relative))
                if delivery.MARKER_PREFIX.encode() in io.read(path, delivery.MAX_FALLBACK_BYTES):
                    found.append(relative)
    return found


def iter_local_files(root: Path, io: DeliveryIO) -> list[Path]:
    try:
        directory = io.safe(root, PurePosixPath(".github/instructions"))
    except ValueError:
        return []
    found: list[Path] = []
    for current, directories, names in os.walk(directory):
        directories[:] = [
            name for name in directories if not io.indirect(Path(current) / name)
        ]
        found.extend(Path(current) / name for name in names if name.endswith(".local.instructions.md"))
    return found


def ignored_local_paths(root: Path, relatives: list[str]) -> set[str]:
    if not relatives:
        return set()
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(LC_ALL="C", LANG="C")
    flags = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    try:
        probe = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, timeout=5, env=env, **flags,
        )
        if probe.returncode != 0:
            if b"not a git repository" not in probe.stderr.lower():
                return set()
            return set(relatives)
        if probe.stdout.strip() != b"true":
            return set()
        proc = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--no-index", "-z", "--stdin"],
            input=b"\0".join(path.encode() for path in relatives) + b"\0",
            capture_output=True, timeout=5, env=env, **flags,
        )
        if proc.returncode not in (0, 1):
            return set()
        return {p for p in proc.stdout.decode("utf-8").split("\0") if p}
    except FileNotFoundError:
        return set() if any((p / ".git").exists() for p in [root, *root.parents]) else set(relatives)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return set()


def resolve_source(
    root: Path, entry: Mapping[str, object], io: DeliveryIO,
    canonical: tuple[bytes, bytes] | None = None,
    canonical_error: str = "enabled canonical payload unavailable",
) -> dict[str, object]:
    fallback_raw = read_fallback(root, entry, io)
    reviewed_path = str(entry["destination"])
    if fallback_raw is None:
        fallback_raw = io.read(io.safe(root, PurePosixPath(reviewed_path)), 2 * 1024 * 1024)
        if (
            len(fallback_raw) != entry["renderedBytes"]
            or hashlib.sha256(fallback_raw).hexdigest() != entry["renderedSha256"]
        ):
            raise ValueError("reviewed inline bytes differ from the locked digest")
    else:
        reviewed_path = str(entry["fallback"]["destination"])
    reviewed = delivery.parse_marker(fallback_raw)
    delivery.validate_candidate(reviewed, entry)
    selected, marker, raw = reviewed_path, reviewed, fallback_raw
    local_paths = [
        delivery.local_destination(str(entry["destination"])),
        delivery.legacy_local_destination(str(entry["destination"])),
    ]
    diagnostic: str | None = None
    for local_path in local_paths:
        try:
            candidate = io.safe(root, PurePosixPath(local_path))
            if not candidate.exists():
                continue
            candidate_raw = io.read(candidate, 2 * 1024 * 1024)
            candidate_marker = delivery.parse_marker(candidate_raw)
            delivery.validate_candidate(candidate_marker, entry)
            if candidate_marker["renderedBytes"] != len(candidate_raw):
                raise ValueError("local byte count mismatch")
            if candidate_marker.get("deliveryKind") == "selector":
                raise ValueError("local candidate is not a body")
            delivery.validate_local_template(candidate_raw, candidate_marker)
            if canonical is None:
                raise ValueError(canonical_error)
            if candidate_raw not in canonical:
                raise ValueError("local candidate differs from enabled canonical render")
            if delivery.local_wins(candidate_marker, reviewed):
                selected, marker, raw = local_path, candidate_marker, candidate_raw
                break
        except (OSError, ValueError, KeyError, TypeError) as exc:
            diagnostic = f"local candidate rejected: {exc}"
    return {
        "schema": "copilot-extensions.instruction-source-selection",
        "version": 1,
        "selectedPath": selected,
        "identity": delivery.identity(marker),
        "bodySha256": marker.get("bodySha256"),
        "receiptBindingSha256": (
            delivery.receipt_binding(marker) if marker.get("deliveryKind") == "body" else None
        ),
        "receiptCapable": delivery.complete_body(raw, marker),
        "modelAdmission": "unknown",
        "diagnostic": diagnostic,
    }


def resolve_authenticated(
    root: Path, destination: str, entries: Mapping[str, Mapping[str, object]],
    specs: Sequence[delivery.Projection], io: DeliveryIO,
) -> dict[str, object]:
    matches = [spec for spec in specs if spec.destination == destination]
    if destination not in entries:
        if io.safe(root, PurePosixPath(destination)).exists():
            raise ValueError("unpaired source requires verified enabled declarations")
        if len(matches) != 1:
            raise ValueError("unpaired source requires verified enabled declarations (unambiguous)")
        current, legacy = delivery.canonical_renders(matches[0])
        return resolve_unpaired(root, destination, current, legacy, io)
    canonical = None
    error = "enabled canonical payload unavailable or ambiguous"
    if len(matches) == 1:
        try:
            canonical = delivery.canonical_renders(matches[0])
            delivery.validate_candidate(delivery.parse_marker(canonical[0]), entries[destination])
        except ValueError as exc:
            canonical = None
            error = f"enabled canonical payload rejected: {exc}"
    return resolve_source(root, entries[destination], io, canonical, error)


def plan_fallback_change(
    root: Path, destination: str, content: bytes | None,
    previous: Mapping[str, object] | None, io: DeliveryIO, *,
    overwrite_existing_managed: bool = False,
) -> list[tuple[Path, bytes | None, bytes | None]]:
    before = None
    if previous is not None and previous.get("deliveryMode") == "selector":
        before = read_fallback(
            root, previous, io,
            allow_content_edits=overwrite_existing_managed and content is not None,
        )
    if content is None and before is None:
        return []
    path = io.safe(root, PurePosixPath(delivery.fallback_destination(destination)))
    if before is None and path.exists():
        raise ValueError("refusing to replace a fallback without matching lock ownership")
    return [] if before == content else [(path, content, before)]


def resolve_unpaired(
    root: Path, destination: str, expected: bytes, legacy: bytes, io: DeliveryIO
) -> dict[str, object]:
    for local in [delivery.local_destination(destination), delivery.legacy_local_destination(destination)]:
        try:
            raw = io.read(io.safe(root, PurePosixPath(local)), 2 * 1024 * 1024)
            if raw not in (expected, legacy):
                continue
            marker = delivery.parse_marker(raw)
            delivery.validate_local_template(raw, marker)
            break
        except (OSError, ValueError):
            continue
    else:
        raise ValueError("unpaired local source differs from its enabled canonical template")
    return {
        "schema": "copilot-extensions.instruction-source-selection",
        "version": 1, "selectedPath": local, "identity": delivery.identity(marker),
        "bodySha256": marker.get("bodySha256"),
        "receiptBindingSha256": (
            delivery.receipt_binding(marker) if marker.get("deliveryKind") == "body" else None
        ),
        "receiptCapable": delivery.complete_body(raw, marker),
        "modelAdmission": "unknown", "diagnostic": "verified enabled, unpaired source",
    }


def require_untracked(
    relative: str,
    probe: Callable[[list[str]], tuple[set[str], bool]],
) -> None:
    tracked, inconclusive = probe([relative])
    if relative in tracked or inconclusive:
        raise ValueError("local body became tracked or tracking status is unknown")


def retire_legacy_local(
    root: Path, spec: delivery.Projection, io: DeliveryIO, result: object,
    tracked_paths: set[str], inconclusive: bool,
    recheck_tracking: Callable[[list[str]], tuple[set[str], bool]],
) -> None:
    legacy = delivery.legacy_local_destination(spec.destination)
    try:
        path = io.safe(root, PurePosixPath(legacy))
        if not path.exists():
            return
        raw = io.read(path, 2 * 1024 * 1024)
        marker = delivery.parse_marker(raw)
        delivery.validate_local_template(raw, marker)
        expected = delivery.canonical_renders(spec)
        delivery.version_key(marker.get("pluginVersion"))
        prior = delivery.canonical_renders(replace(spec, plugin_version=marker["pluginVersion"]))
        if raw not in prior:
            raise ValueError("legacy local is not the current enabled canonical body")
        if legacy in tracked_paths or inconclusive:
            raise ValueError("legacy local is tracked or tracking status is unknown")
        new = io.safe(root, PurePosixPath(delivery.local_destination(spec.destination)))
        if io.read(new, 2 * 1024 * 1024) != expected[0]:
            raise ValueError("new canonical local body is not published")
        path = io.safe(root, PurePosixPath(legacy))
        if io.read(path, 2 * 1024 * 1024) != raw:
            raise ValueError("legacy local changed after validation")
        latest_tracked, latest_inconclusive = recheck_tracking([legacy])
        if legacy in latest_tracked or latest_inconclusive:
            raise ValueError("legacy local became tracked or tracking status is unknown")
        path = io.safe(root, PurePosixPath(legacy))
        if io.read(path, 2 * 1024 * 1024) != raw:
            raise ValueError("legacy local changed during tracking validation")
        path.unlink()
        result.changed.append(legacy)
        result.removed.append(legacy)
    except (OSError, ValueError) as exc:
        result.add("warning", "projection-local-cache-legacy", legacy, str(exc))


def report_unmanaged_legacy_locals(
    root: Path, destinations: set[str], io: DeliveryIO, result: object
) -> None:
    try:
        directory = io.safe(root, PurePosixPath(".github/copilot/context-locals"))
        expected = {delivery.legacy_local_destination(path) for path in destinations}
        for current, directories, names in os.walk(directory):
            directories[:] = [
                name for name in directories if not io.indirect(Path(current) / name)
            ]
            for name in names:
                if not name.endswith(".local.md"):
                    continue
                relative = (Path(current) / name).relative_to(root).as_posix()
                if relative not in expected:
                    result.add(
                        "warning", "projection-local-cache-legacy", relative,
                        "compatibility body has no enabled canonical migration owner; retained",
                    )
    except (OSError, ValueError) as exc:
        result.add("warning", "projection-local-cache-legacy", ".github/copilot/context-locals", str(exc))


def new_source_admission(
    root: Path, spec: delivery.Projection, io: DeliveryIO
) -> tuple[bool, str]:
    try:
        if getattr(spec, "skip_local_cache", False):
            raise ValueError("required inline control kernel has no static delivery floor")
        current, legacy = delivery.canonical_renders(spec)
        resolve_unpaired(root, spec.destination, current, legacy, io)
        return True, "complete enabled canonical local body verified; reviewed freshness pending"
    except (OSError, ValueError) as exc:
        return False, f"missing verified guidance delivery: {exc}"


def inventory(
    root: Path, automatic: set[Path], entries: Mapping[str, Mapping[str, object]],
    io: DeliveryIO,
) -> dict[str, object]:
    groups: dict[str, list[dict[str, object]]] = {
        "selectors": [], "inline_kernels": [], "local_bodies": [], "reviewed_fallbacks": [],
        "legacy_local_bodies": [],
    }
    errors: list[dict[str, str]] = []

    def metrics(path: str, raw: bytes) -> dict[str, object]:
        return {
            "path": path, "bytes": len(raw), "characters": len(raw.decode("utf-8")),
        }

    for path in sorted(automatic):
        relative = path.relative_to(root).as_posix()
        try:
            raw = io.read(io.safe(root, PurePosixPath(relative)), 2 * 1024 * 1024)
            if delivery.MARKER_PREFIX.encode() not in raw:
                continue
            marker = delivery.parse_marker(raw)
        except (OSError, ValueError):
            continue
        kind = (
            "local_bodies" if path.name.endswith(".local.instructions.md")
            else "selectors" if marker.get("deliveryKind") == "selector"
            else "inline_kernels"
        )
        groups[kind].append(metrics(relative, raw))
    directory = io.safe(root, PurePosixPath(".github/copilot/context-locals"))
    if directory.exists():
        for current, directories, names in os.walk(directory):
            directories[:] = [
                name for name in directories if not io.indirect(Path(current) / name)
            ]
            for name in names:
                if not name.endswith(".local.md"):
                    continue
                relative = (Path(current) / name).relative_to(root).as_posix()
                try:
                    raw = io.read(io.safe(root, PurePosixPath(relative)), 2 * 1024 * 1024)
                    marker = delivery.parse_marker(raw)
                    delivery.validate_local_template(raw, marker)
                    if delivery.legacy_local_destination(str(marker["destination"])) != relative:
                        raise ValueError("local body path does not match provenance")
                    groups["legacy_local_bodies"].append(metrics(relative, raw))
                except (OSError, ValueError, KeyError) as exc:
                    errors.append({"path": relative, "error": str(exc)})
    for entry in entries.values():
        if entry.get("deliveryMode") != "selector":
            continue
        path = str(entry["fallback"]["destination"])
        try:
            raw = read_fallback(root, entry, io)
            groups["reviewed_fallbacks"].append(metrics(path, raw))
        except (OSError, ValueError) as exc:
            errors.append({"path": path, "error": str(exc)})
    return {
        "categories": {
            name: {
                "files": rows, "count": len(rows),
                "bytes": sum(row["bytes"] for row in rows),
                "characters": sum(row["characters"] for row in rows),
                "automatically_discovered": name not in {"reviewed_fallbacks", "legacy_local_bodies"},
            } for name, rows in groups.items()
        },
        "validation_errors": errors,
        "actual_model_admission": "unknown",
        "selective_body_reads": "unknown",
    }
