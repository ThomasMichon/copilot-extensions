"""Read-only owned delivery validation; filesystem capabilities are injected."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence

import instruction_delivery as delivery


@dataclass(frozen=True)
class DeliveryIO:
    safe: Callable[[Path, PurePosixPath], Path]
    read: Callable[[Path, int], bytes]
    indirect: Callable[[Path], bool]


def read_fallback(
    root: Path, entry: Mapping[str, object], io: DeliveryIO
) -> bytes | None:
    if entry.get("deliveryMode") != "selector":
        return None
    delivery.validate_lock_delivery(entry)
    fallback = entry["fallback"]
    path = io.safe(root, PurePosixPath(fallback["destination"]))
    raw = io.read(path, delivery.MAX_FALLBACK_BYTES)
    marker = delivery.parse_marker(raw)
    delivery.validate_local_template(raw, marker)
    expected = {
        key: value for key, value in entry.items() if key in delivery.BASE_KEYS
    } | {"renderedBytes": fallback["renderedBytes"]}
    if (
        any(marker.get(key) != value for key, value in expected.items())
        or hashlib.sha256(raw).hexdigest() != fallback["renderedSha256"]
        or not delivery.complete_body(raw, marker)
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


def resolve_source(
    root: Path, entry: Mapping[str, object], io: DeliveryIO,
    canonical: tuple[bytes, bytes] | None = None,
    canonical_error: str = "enabled canonical payload unavailable",
) -> dict[str, object]:
    fallback_raw = read_fallback(root, entry, io)
    reviewed_path = str(entry["destination"])
    if fallback_raw is None:
        fallback_raw = io.read(io.safe(root, PurePosixPath(reviewed_path)), 2 * 1024 * 1024)
    else:
        reviewed_path = str(entry["fallback"]["destination"])
    reviewed = delivery.parse_marker(fallback_raw)
    delivery.validate_candidate(reviewed, entry)
    selected, marker, raw = reviewed_path, reviewed, fallback_raw
    local_path = str(entry["destination"]).removesuffix(".instructions.md") + ".local.instructions.md"
    diagnostic: str | None = None
    try:
        candidate = io.safe(root, PurePosixPath(local_path))
        if candidate.exists():
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
    previous: Mapping[str, object] | None, io: DeliveryIO,
) -> list[tuple[Path, bytes | None, bytes | None]]:
    before = None
    if previous is not None and previous.get("deliveryMode") == "selector":
        before = read_fallback(root, previous, io)
    if content is None and before is None:
        return []
    path = io.safe(root, PurePosixPath(delivery.fallback_destination(destination)))
    if before is None and path.exists():
        raise ValueError("refusing to replace a fallback without matching lock ownership")
    return [] if before == content else [(path, content, before)]


def resolve_unpaired(
    root: Path, destination: str, expected: bytes, legacy: bytes, io: DeliveryIO
) -> dict[str, object]:
    local = destination.removesuffix(".instructions.md") + ".local.instructions.md"
    raw = io.read(io.safe(root, PurePosixPath(local)), 2 * 1024 * 1024)
    if raw not in (expected, legacy):
        raise ValueError("unpaired local source differs from its enabled canonical template")
    marker = delivery.parse_marker(raw)
    delivery.validate_local_template(raw, marker)
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


def inventory(
    root: Path, automatic: set[Path], entries: Mapping[str, Mapping[str, object]],
    io: DeliveryIO,
) -> dict[str, object]:
    groups: dict[str, list[dict[str, object]]] = {
        "selectors": [], "inline_kernels": [], "local_bodies": [], "reviewed_fallbacks": [],
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
                "automatically_discovered": name != "reviewed_fallbacks",
            } for name, rows in groups.items()
        },
        "validation_errors": errors,
        "actual_model_admission": "unknown",
        "selective_body_reads": "unknown",
    }
