"""Data-only admission and completeness rules for the promotion unit packet."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath

PROFILE = "copilot-extensions.promotion-units/v1"
_DIGEST = re.compile(r"[0-9a-f]{64}")
_SHA = re.compile(r"[0-9a-f]{40}")
_PACKET = "tools/promotion_unit_tests/"


class UnitEvidenceError(ValueError):
    """The declared unit packet or its observed evidence is incomplete."""


def decode_json(text: str | bytes) -> object:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise UnitEvidenceError(f"duplicate unit evidence field: {key}")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=unique)


def relative_file(value: object) -> str:
    if (
        not isinstance(value, str) or "\\" in value
        or PurePosixPath(value).is_absolute()
        or any(part.casefold() in ("", ".", "..", ".git") for part in value.split("/"))
        or not value.endswith(".py")
    ):
        raise UnitEvidenceError("unit source must be a canonical relative Python path")
    return value


def admit_manifest(data: object) -> dict:
    if not isinstance(data, dict) or set(data) != {
        "profile", "nodes", "sources", "coverage_targets", "max_seconds",
    }:
        raise UnitEvidenceError("invalid unit manifest fields")
    if data["profile"] != PROFILE or type(data["max_seconds"]) is not int:
        raise UnitEvidenceError("invalid unit profile or budget")
    if not 1 <= data["max_seconds"] <= 300:
        raise UnitEvidenceError("the entire unit packet must fit within five minutes")
    nodes, sources, targets = data["nodes"], data["sources"], data["coverage_targets"]
    if (
        not isinstance(nodes, list) or not nodes
        or any(not isinstance(node, str) for node in nodes)
        or len(nodes) != len(set(nodes))
        or not isinstance(sources, dict) or not sources
        or not isinstance(targets, list) or not targets
        or any(not isinstance(target, str) for target in targets)
        or len(targets) != len(set(targets))
    ):
        raise UnitEvidenceError("unit nodes, sources and coverage targets must be explicit")
    for path, digest in sources.items():
        relative_file(path)
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise UnitEvidenceError(f"invalid unit source digest: {path}")
    if len({path.casefold() for path in sources}) != len(sources):
        raise UnitEvidenceError("unit source paths contain case aliases")
    for node in nodes:
        path, separator, case = node.partition("::")
        relative_file(path)
        if (
            not separator or not case or not path.startswith(_PACKET)
            or not Path(path).name.startswith("test_") or path not in sources
        ):
            raise UnitEvidenceError(f"node is outside the admitted unit packet: {node}")
    for path in targets:
        relative_file(path)
        if path not in sources or path.startswith(_PACKET):
            raise UnitEvidenceError(f"coverage target is not admitted production source: {path}")
    return data


def verify_sources(root: Path, sources: dict[str, str]) -> None:
    for name, expected in sources.items():
        path = root.joinpath(relative_file(name))
        component = root
        indirect = False
        for part in PurePosixPath(name).parts:
            component /= part
            indirect = indirect or component.is_symlink()
        if indirect or not path.is_file() or not path.resolve().is_relative_to(root):
            raise UnitEvidenceError(f"unit source is missing or indirect: {name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise UnitEvidenceError(f"unit source changed without admission: {name}")


def verify_observations(
    expected: list[str], collected: list[str], reports: list[dict],
    effects: list[str], elapsed: float, maximum: int,
) -> None:
    if len(collected) != len(set(collected)) or set(collected) != set(expected):
        raise UnitEvidenceError("collected unit nodes differ from the exact admitted inventory")
    observed: dict[tuple[str, str], str] = {}
    for report in reports:
        if not isinstance(report, dict) or set(report) != {"node", "phase", "outcome"}:
            raise UnitEvidenceError("invalid unit phase report")
        if any(not isinstance(report[field], str) for field in ("node", "phase", "outcome")):
            raise UnitEvidenceError("unit phase fields must be strings")
        key = (report["node"], report["phase"])
        if key in observed or key[0] not in expected or key[1] not in ("setup", "call", "teardown"):
            raise UnitEvidenceError("duplicate or unexpected unit phase")
        observed[key] = report["outcome"]
    required = {(node, phase) for node in expected for phase in ("setup", "call", "teardown")}
    if set(observed) != required or any(outcome != "passed" for outcome in observed.values()):
        raise UnitEvidenceError("every admitted unit must pass setup, call and teardown without skips")
    if effects:
        raise UnitEvidenceError("unit packet attempted a prohibited effect: " + "; ".join(effects))
    if not 0 <= elapsed < maximum:
        raise UnitEvidenceError("unit packet exceeded its whole-execution budget")


def source_sha(value: str) -> str:
    if not _SHA.fullmatch(value):
        raise UnitEvidenceError("a full immutable source SHA is required")
    return value


def manifest_digest(data: dict) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def requires_units(changed_paths: list[str]) -> bool:
    """Unknown inputs run admitted units; known narrative-only changes need none."""
    if not changed_paths:
        return True
    for path in changed_paths:
        if (
            not isinstance(path, str) or "\\" in path
            or PurePosixPath(path).is_absolute()
            or any(part in ("", ".", "..") for part in path.split("/"))
        ):
            raise UnitEvidenceError("invalid changed-input path")
        narrative = (
            path.startswith(("docs/", "visions/", "efforts/"))
            and path.endswith(".md")
        ) or path in {"README.md", "CONTRIBUTING.md", "TESTING.md", "REVIEW.md"}
        if not narrative:
            return True
    return False


def changed_paths(base: str, head: str, read_diff: Callable[[list[str]], bytes]) -> list[str]:
    """Preserve deletions as well as additions, including source-to-doc renames."""
    arguments = [
        "git", "diff", "--no-renames", "--name-only", "-z",
        source_sha(base), source_sha(head),
    ]
    raw = read_diff(arguments)
    if not isinstance(raw, bytes):
        raise UnitEvidenceError("changed-input evidence must contain raw Git path bytes")
    return [path for path in raw.decode("utf-8").split("\0") if path]
