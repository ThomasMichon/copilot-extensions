"""Pure, byte-bound candidate coverage correspondence, not Dev execution proof."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import unicodedata

SCHEMA = "copilot-extensions.promotion-development-map"
PROJECTION_SCHEMA = "copilot-extensions.promotion-development-coverage"
VERSION = 1
_VERSION_NAMES = {"__version__", "VERSION", "_VERSION"}


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _digest(value: dict) -> str:
    return _hash(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("utf-8"))


def _path(value: object) -> str:
    if not isinstance(value, str) or not value or not value.endswith(".py"):
        raise ValueError("expected a canonical relative Python path")
    if "\\" in value or ":" in value or any(ord(c) < 32 or ord(c) == 127 for c in value) or unicodedata.normalize("NFC", value) != value:
        raise ValueError("noncanonical path")
    parts = value.split("/")
    if any(p in ("", ".", "..") or p.lower() == ".git" or p.rstrip(" .") != p for p in parts):
        raise ValueError("escaping or noncanonical path")
    if any(re.fullmatch(r"con|prn|aux|nul|com[1-9]|lpt[1-9]", p.split(".")[0], re.I) for p in parts):
        raise ValueError("device path alias")
    return value


def _identity(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
        raise ValueError("expected a full lowercase git object identity")
    return value


def _inventory(value: object) -> dict[str, bytes]:
    if not isinstance(value, dict):
        raise ValueError("source inventory must be a dictionary")
    for path, content in value.items():
        _path(path)
        if not isinstance(content, bytes):
            raise ValueError("source contents must be bytes")
    return value


def _aliases(paths) -> None:
    seen = {}
    for path in paths:
        key = path.casefold()
        if key in seen and seen[key] != path:
            raise ValueError("case aliases are not canonical paths")
        seen[key] = path


def _version_lines(original: bytes, candidate: bytes) -> set[int] | None:
    """Recognize only same-line literal edits in standalone version assignments."""
    old_lines, new_lines = original.splitlines(keepends=True), candidate.splitlines(keepends=True)
    if len(old_lines) != len(new_lines):
        return None
    changed = {i for i, (a, b) in enumerate(zip(old_lines, new_lines), 1) if a != b}
    try:
        old_tree = ast.parse(original.decode("utf-8"))
        new_tree = ast.parse(candidate.decode("utf-8"))
    except (SyntaxError, UnicodeError, ValueError):
        return None

    def normalize(tree, lines):
        recognized = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
            elif isinstance(node, ast.AnnAssign):
                target = node.target
            else:
                continue
            value = node.value
            if (
                not isinstance(target, ast.Name) or target.id not in _VERSION_NAMES
                or not isinstance(value, ast.Constant) or not isinstance(value.value, str)
                or node.lineno != node.end_lineno or value.lineno != value.end_lineno
                or node.lineno not in changed
            ):
                continue
            line = lines[node.lineno - 1]
            # Byte columns are valid here because the decoded source is UTF-8.
            if line[:node.col_offset].strip() or line[node.end_col_offset:].strip():
                continue
            if not re.fullmatch(r"\d+(?:\.\d+)+(?:[A-Za-z0-9.+-]*)", value.value):
                continue
            recognized[node.lineno] = (
                value.value, line[:value.col_offset], line[value.end_col_offset:],
            )
            value.value = "<version-stamp>"
        return recognized

    old_versions = normalize(old_tree, old_lines)
    new_versions = normalize(new_tree, new_lines)
    if set(old_versions) != changed or set(new_versions) != changed:
        return None
    if any(old_versions[n][0] == new_versions[n][0] or old_versions[n][1:] != new_versions[n][1:]
           for n in changed):
        return None
    if ast.dump(old_tree) != ast.dump(new_tree):
        return None
    return changed


def build_mapping(
    *, dev_sha: str, candidate_tree: str, sources: dict[str, bytes],
    candidate_sources: dict[str, bytes], origins: dict[str, str],
) -> dict:
    """Build canonical, per-copy correspondence from explicitly admitted bytes."""
    _identity(dev_sha)
    _identity(candidate_tree)
    _inventory(sources)
    _inventory(candidate_sources)
    if not isinstance(origins, dict):
        raise ValueError("origins must be a dictionary")
    for path, origin in origins.items():
        _path(path)
        _path(origin)
        if path not in candidate_sources or origin not in sources:
            raise ValueError("untrusted origin outside admitted inventories")
    _aliases([*sources, *candidate_sources, *origins.values()])
    files = {}
    for path, content in sorted(candidate_sources.items()):
        origin = origins.get(path, path if path in sources else None)
        original = sources.get(origin) if origin is not None else None
        count = len(content.splitlines(keepends=True))
        changed = None
        kind = "unmapped"
        reason = "missing-origin" if original is None else "unsupported-transform"
        if original == content:
            kind, reason = "exact-copy", None
        elif origin == path:
            changed = _version_lines(original, content)
            if changed is not None:
                kind, reason = "known-version-stamp", None
        lines = {}
        unmapped = {}
        for number in range(1, count + 1):
            if kind == "exact-copy" or (kind == "known-version-stamp" and number not in changed):
                lines[str(number)] = number
            else:
                unmapped[str(number)] = "changed-version-statement" if kind == "known-version-stamp" else reason
        files[path] = {
            "origin": origin, "candidate_sha256": _hash(content),
            "source_sha256": _hash(original) if original is not None else None,
            "transform": {"kind": kind, "version": VERSION},
            "lines": lines, "unmapped": unmapped, "unmapped_reason": reason,
        }
    result = {
        "schema": SCHEMA, "version": VERSION, "dev_sha": dev_sha,
        "candidate_tree": candidate_tree,
        "sources": {p: _hash(b) for p, b in sorted(sources.items())},
        "candidate_sources": {p: _hash(b) for p, b in sorted(candidate_sources.items())},
        "origins": dict(sorted(origins.items())), "files": files,
    }
    result["digest"] = _digest(result)
    return result


def verify_mapping(
    mapping, *, dev_sha, candidate_tree, sources, candidate_sources, origins,
) -> dict:
    """Recompute every correspondence; a supplied digest alone is not provenance."""
    expected = build_mapping(
        dev_sha=dev_sha, candidate_tree=candidate_tree, sources=sources,
        candidate_sources=candidate_sources, origins=origins,
    )
    try:
        if type(mapping) is not dict or json.dumps(mapping, sort_keys=True, allow_nan=False) != json.dumps(
            expected, sort_keys=True, allow_nan=False,
        ):
            raise ValueError("mapping differs from canonical byte-bound correspondence")
    except (TypeError, OverflowError) as error:
        raise ValueError("invalid mapping") from error
    return expected


def _admit_mapping(mapping: dict) -> None:
    """Check portable structure; callers still verify against authoritative bytes."""
    if type(mapping) is not dict:
        raise ValueError("invalid mapping")
    required = {"schema", "version", "dev_sha", "candidate_tree", "sources",
                "candidate_sources", "origins", "files", "digest"}
    if set(mapping) != required or mapping["schema"] != SCHEMA or type(mapping["version"]) is not int or mapping["version"] != VERSION:
        raise ValueError("unsupported mapping schema")
    _identity(mapping["dev_sha"])
    _identity(mapping["candidate_tree"])
    for name in ("sources", "candidate_sources"):
        inventory = mapping[name]
        if type(inventory) is not dict:
            raise ValueError("invalid hash inventory")
        for path, digest in inventory.items():
            _path(path)
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("invalid content digest")
    _aliases([*mapping["sources"], *mapping["candidate_sources"]])
    if type(mapping["origins"]) is not dict or type(mapping["files"]) is not dict:
        raise ValueError("invalid mapping inventories")
    for path, origin in mapping["origins"].items():
        if _path(path) not in mapping["candidate_sources"] or _path(origin) not in mapping["sources"]:
            raise ValueError("untrusted origin")
    if set(mapping["files"]) != set(mapping["candidate_sources"]):
        raise ValueError("mapping file inventory mismatch")
    for path, record in mapping["files"].items():
        if type(record) is not dict or set(record) != {
            "origin", "candidate_sha256", "source_sha256", "transform", "lines", "unmapped", "unmapped_reason",
        }:
            raise ValueError("invalid file mapping")
        origin = mapping["origins"].get(path, path if path in mapping["sources"] else None)
        if record["origin"] != origin or record["candidate_sha256"] != mapping["candidate_sources"][path] or record["source_sha256"] != mapping["sources"].get(origin):
            raise ValueError("file mapping identity mismatch")
        kind = record["transform"]
        if type(kind) is not dict or set(kind) != {"kind", "version"} or type(kind["version"]) is not int or kind["version"] != VERSION or not isinstance(kind["kind"], str) or kind["kind"] not in {"exact-copy", "known-version-stamp", "unmapped"}:
            raise ValueError("unsupported transform")
        lines, unmapped = record["lines"], record["unmapped"]
        if type(lines) is not dict or type(unmapped) is not dict or set(lines) & set(unmapped):
            raise ValueError("invalid line coordinates")
        for line in [*lines, *unmapped]:
            if not isinstance(line, str) or not re.fullmatch(r"[1-9][0-9]*", line):
                raise ValueError("invalid line coordinate")
        if any(type(v) is not int or int(k) != v for k, v in lines.items()):
            raise ValueError("unsupported shifted coordinate")
        if sorted(map(int, [*lines, *unmapped])) != list(range(1, len(lines) + len(unmapped) + 1)):
            raise ValueError("incomplete line coordinates")
        if kind["kind"] == "exact-copy":
            if origin is None or record["source_sha256"] != record["candidate_sha256"] or unmapped or record["unmapped_reason"] is not None:
                raise ValueError("invalid exact copy")
        elif kind["kind"] == "known-version-stamp":
            if origin != path or not unmapped or record["unmapped_reason"] is not None or any(v != "changed-version-statement" for v in unmapped.values()):
                raise ValueError("invalid version transform")
        elif lines or not isinstance(record["unmapped_reason"], str) or record["unmapped_reason"] not in {"missing-origin", "unsupported-transform"} or any(v != record["unmapped_reason"] for v in unmapped.values()):
            raise ValueError("invalid unmapped region")
    unsigned = {k: v for k, v in mapping.items() if k != "digest"}
    if mapping["digest"] != _digest(unsigned):
        raise ValueError("mapping digest mismatch")


def project_coverage(raw: dict, mapping: dict) -> dict:
    """Project promotion ``files`` or legacy ``coverage`` without changing raw evidence.

    ``verify_mapping`` must first bind the map to authoritative inventories.
    Structural/digest checks here cannot authenticate a self-rehashed forgery.
    Context strings, including phase suffixes, are opaque and preserved.
    """
    _admit_mapping(mapping)
    if type(raw) is not dict or ("files" in raw) == ("coverage" in raw):
        raise ValueError("raw coverage must contain exactly one supported coverage shape")
    coverage = raw.get("coverage")
    if "files" in raw:
        if type(raw["files"]) is not dict:
            raise ValueError("raw files must be a dictionary")
        coverage = {}
        for path, record in raw["files"].items():
            _path(path)
            if type(record) is not dict:
                raise ValueError("invalid promotion file coverage")
            executed = record.get("executed_lines")
            contexts = record.get("contexts", {})
            if type(executed) is not list or any(type(n) is not int or n < 1 for n in executed):
                raise ValueError("invalid executed lines")
            if len(executed) != len(set(executed)) or type(contexts) is not dict:
                raise ValueError("invalid promotion line contexts")
            lines = {str(n): [] for n in executed}
            if any(not isinstance(n, str) or n not in lines for n in contexts):
                raise ValueError("context outside executed lines")
            lines.update(contexts)
            coverage[path] = lines
    if type(coverage) is not dict:
        raise ValueError("raw coverage must contain a line coverage dictionary")
    for path in coverage:
        _path(path)
    copies, development = {}, {}
    for path, hits in sorted(coverage.items()):
        _path(path)
        if path not in mapping["files"] or type(hits) is not dict:
            raise ValueError("raw coverage outside admitted candidate")
        record = mapping["files"][path]
        mapped, unmapped = {}, {}
        for line, contexts in hits.items():
            if not isinstance(line, str) or not re.fullmatch(r"[1-9][0-9]*", line) or type(contexts) is not list or any(not isinstance(c, str) for c in contexts):
                raise ValueError("invalid raw line attribution")
            if line in record["lines"]:
                target = str(record["lines"][line])
                mapped[target] = sorted(set(contexts))
                bucket = development.setdefault(record["origin"], {}).setdefault(target, set())
                bucket.update(contexts)
            else:
                if line not in record["unmapped"]:
                    raise ValueError("raw line outside candidate contents")
                unmapped[line] = {"contexts": copy.deepcopy(contexts), "reason": record["unmapped"][line]}
        copies[path] = {
            "origin": record["origin"], "candidate_sha256": record["candidate_sha256"],
            "source_sha256": record["source_sha256"], "mapped": mapped, "unmapped": unmapped,
        }
    return {
        "schema": PROJECTION_SCHEMA, "version": VERSION, "dev_sha": mapping["dev_sha"],
        "candidate_tree": mapping["candidate_tree"], "mapping_digest": mapping["digest"],
        "attribution": "derived-dev-coordinates-not-dev-byte-execution",
        "evidence": {"lines": True, "branches": False, "functions": False},
        "raw_candidate": copy.deepcopy(raw), "copies": copies,
        "coverage": {p: {n: sorted(c) for n, c in sorted(lines.items())}
                     for p, lines in sorted(development.items())},
    }
