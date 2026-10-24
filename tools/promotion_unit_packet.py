"""Portable, data-only verification of one admitted promotion unit packet.

verify_packet returns exactly profile, source_sha, manifest_sha256,
manifest_file_sha256, proof_sha256, coverage_sha256, packet_sha256, manifest,
and proof. File digests bind raw bytes; manifest_sha256 binds the trusted
canonical manifest. packet_sha256 canonically hashes the three raw-file digests.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath

from promotion_unit_contract import (
    CANDIDATE_PROFILE, UnitEvidenceError, admit_manifest, decode_json, manifest_digest,
    relative_file, source_sha, verify_observations, verify_sources,
)

MAX_PACKET_FILE_BYTES = 16 * 1024 * 1024
_FILES = {"unit-manifest.json", "unit-proof.json", "coverage.json"}
_PROOF_FIELDS = {
    "profile", "source_sha", "manifest_sha256", "elapsed_seconds", "collected",
    "reports", "effects", "imported_sources", "coverage_file", "coverage_sha256",
}
_PHASES = ("setup", "call", "teardown")


def _canonical_path(value: object) -> str:
    path = relative_file(value)
    if (
        PureWindowsPath(path).drive or PurePosixPath(path).as_posix() != path
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or ":" in path
    ):
        raise UnitEvidenceError("unit packet paths must be canonical relative Python paths")
    return path


def _manifest(value: object) -> dict:
    data = admit_manifest(value)
    for path in (*data["sources"], *data["coverage_targets"]):
        _canonical_path(path)
    for node in data["nodes"]:
        _canonical_path(node.partition("::")[0])
        if any(ord(char) < 32 or ord(char) == 127 for char in node):
            raise UnitEvidenceError("unit node contains a control character")
    return data


def _read_file(directory: Path, name: str) -> tuple[bytes, dict]:
    path = directory / name
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_PACKET_FILE_BYTES:
            raise UnitEvidenceError(
                f"packet file must be regular, unlinked and bounded: {name} "
                f"(mode={stat.S_IFMT(before.st_mode)}, bytes={before.st_size}, "
                f"limit={MAX_PACKET_FILE_BYTES})",
            )
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                before.st_dev, before.st_ino,
            ):
                raise UnitEvidenceError(f"packet file changed during opening: {name}")
            raw = stream.read(MAX_PACKET_FILE_BYTES + 1)
        if len(raw) > MAX_PACKET_FILE_BYTES:
            raise UnitEvidenceError(f"packet file exceeds its size limit: {name}")
    except OSError as exc:
        raise UnitEvidenceError(f"cannot read packet file {name}: {exc}") from exc
    try:
        data = decode_json(raw)
        json.dumps(data, allow_nan=False)
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise UnitEvidenceError(f"invalid packet JSON in {name}: {exc}") from exc
    except ValueError as exc:
        raise UnitEvidenceError(f"invalid packet JSON in {name}: {exc}") from exc
    if not isinstance(data, dict):
        raise UnitEvidenceError(f"packet JSON must contain an object: {name}")
    return raw, data


def _string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise UnitEvidenceError(f"{label} must be a list of strings")
    if len(value) != len(set(value)):
        raise UnitEvidenceError(f"{label} must contain unique entries")
    return value


def _lines(value: object, label: str) -> set[int]:
    if (
        not isinstance(value, list)
        or any(type(line) is not int or line < 1 for line in value)
        or len(value) != len(set(value))
    ):
        raise UnitEvidenceError(f"{label} must contain unique positive integer lines")
    return set(value)


def _verify_coverage(coverage: dict, manifest: dict) -> None:
    if any(not isinstance(coverage.get(key), dict) for key in ("meta", "files", "totals")):
        raise UnitEvidenceError("coverage must contain meta, files and totals objects")
    files = coverage["files"]
    candidate = manifest["profile"] == CANDIDATE_PROFILE
    if (
        (not candidate and set(files) != set(manifest["coverage_targets"]))
        or (candidate and (
            not set(files) <= set(manifest["coverage_targets"])
            or {manifest["composition"]["origins"][name] for name in files}
            != set(manifest["composition"]["required_coverage_origins"])
        ))
    ):
        raise UnitEvidenceError("coverage files differ from the exact admitted production targets")
    valid_contexts = {""} | {f"{node}|{phase}" for node in manifest["nodes"] for phase in _PHASES}
    calls = {f"{node}|call": node for node in manifest["nodes"]}
    observed_calls = set()
    for name, record in files.items():
        _canonical_path(name)
        if not isinstance(record, dict) or any(
            not isinstance(record.get(key), dict) for key in ("summary", "contexts", "functions", "classes")
        ):
            raise UnitEvidenceError("invalid production coverage file record")
        executed = _lines(record.get("executed_lines"), "executed_lines")
        missing = _lines(record.get("missing_lines"), "missing_lines")
        _lines(record.get("excluded_lines"), "excluded_lines")
        if executed & missing:
            raise UnitEvidenceError("coverage line cannot be both executed and missing")
        contextual_lines = set()
        for line, contexts in record["contexts"].items():
            if not isinstance(line, str) or not line.isascii() or not line.isdigit() or line.startswith("0"):
                raise UnitEvidenceError("coverage context line is not a canonical positive integer")
            values = _string_list(contexts, "coverage contexts")
            if not values or not set(values) <= valid_contexts:
                raise UnitEvidenceError("coverage contains an unknown or empty context attribution")
            contextual_lines.add(line)
            observed_calls.update(calls[value] for value in values if value in calls)
        if contextual_lines != {str(line) for line in executed}:
            raise UnitEvidenceError("coverage contexts and executed lines disagree")
    if observed_calls != set(manifest["nodes"]):
        raise UnitEvidenceError("every admitted unit must contribute production call coverage")


def verify_packet(
    directory: Path, *, expected_source_sha: str, expected_manifest: dict,
    source_root: Path | None = None,
    development_root: Path | None = None,
) -> dict:
    """Verify a packet against caller-owned source and manifest expectations."""
    if not isinstance(directory, Path) or (source_root is not None and not isinstance(source_root, Path)):
        raise UnitEvidenceError("packet directory and optional source root must be Paths")
    if not isinstance(expected_source_sha, str):
        raise UnitEvidenceError("expected source SHA must be a string")
    expected_source_sha = source_sha(expected_source_sha)
    trusted = _manifest(expected_manifest)
    expected_digest = manifest_digest(trusted)
    try:
        if directory.is_symlink() or not directory.is_dir() or {p.name for p in directory.iterdir()} != _FILES:
            raise UnitEvidenceError("unit packet must contain exactly the three declared files")
    except OSError as exc:
        raise UnitEvidenceError(f"cannot inspect unit packet: {exc}") from exc
    manifest_raw, manifest = _read_file(directory, "unit-manifest.json")
    proof_raw, proof = _read_file(directory, "unit-proof.json")
    coverage_raw, coverage = _read_file(directory, "coverage.json")
    manifest = _manifest(manifest)
    if manifest_digest(manifest) != expected_digest:
        raise UnitEvidenceError("packet manifest differs from the trusted canonical manifest")
    coverage_digest = hashlib.sha256(coverage_raw).hexdigest()
    if (
        set(proof) != _PROOF_FIELDS or proof["profile"] != manifest["profile"]
        or not isinstance(proof["source_sha"], str)
        or source_sha(proof["source_sha"]) != expected_source_sha
        or proof["manifest_sha256"] != expected_digest
        or proof["coverage_file"] != "coverage.json" or proof["coverage_sha256"] != coverage_digest
    ):
        raise UnitEvidenceError("unit proof identity or byte binding differs from expectation")
    elapsed = proof["elapsed_seconds"]
    if type(elapsed) is not float or not math.isfinite(elapsed):
        raise UnitEvidenceError("elapsed_seconds must be a finite float")
    collected = _string_list(proof["collected"], "collected unit nodes")
    effects = _string_list(proof["effects"], "unit effects")
    imports = _string_list(proof["imported_sources"], "imported sources")
    if not isinstance(proof["reports"], list):
        raise UnitEvidenceError("unit phase reports must be a list")
    for path in imports:
        _canonical_path(path)
    if not set(imports) <= set(manifest["sources"]):
        raise UnitEvidenceError("unit packet imported an unadmitted source")
    test_modules = {node.partition("::")[0] for node in manifest["nodes"]}
    if not test_modules <= set(imports):
        raise UnitEvidenceError("unit packet did not observe every admitted test module")
    verify_observations(
        manifest["nodes"], collected, proof["reports"], effects, elapsed, manifest["max_seconds"],
    )
    _verify_coverage(coverage, manifest)
    if source_root is not None:
        verify_sources(source_root.resolve(), manifest["sources"])
    if manifest["profile"] == CANDIDATE_PROFILE:
        if source_root is None or development_root is None:
            raise UnitEvidenceError("candidate packet requires both authoritative source roots")
        from promotion_candidate_units import _bytes
        from promotion_development_map import project_coverage, verify_mapping

        composition = manifest["composition"]
        if composition["dev_sha"] != expected_source_sha:
            raise UnitEvidenceError("candidate packet has a different Dev anchor")
        verify_sources(development_root.resolve(), composition["development_sources"])
        origins = {name: composition["origins"][name] for name in coverage["files"]}
        mapping = verify_mapping(
            coverage.get("development_mapping"),
            dev_sha=expected_source_sha, candidate_tree=composition["candidate_tree"],
            sources=_bytes(development_root, set(origins.values())),
            candidate_sources=_bytes(source_root, origins), origins=origins,
        )
        raw = {key: value for key, value in coverage.items()
               if key not in ("development_mapping", "development_projection")}
        if coverage.get("development_projection") != project_coverage(raw, mapping):
            raise UnitEvidenceError("development coverage projection differs from verified raw hits")
    raw_digests = {
        "manifest_file_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "proof_sha256": hashlib.sha256(proof_raw).hexdigest(),
        "coverage_sha256": coverage_digest,
    }
    return {
        "profile": manifest["profile"], "source_sha": expected_source_sha, "manifest_sha256": expected_digest,
        **raw_digests, "packet_sha256": manifest_digest(raw_digests),
        "manifest": manifest, "proof": proof,
    }
