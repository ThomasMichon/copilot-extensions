"""Read-only, JSON-first accounting feature/calibration/estimate commands."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from agent_logger.sessions import SessionRef, archive_stem
from agent_logger.sync.publication_directory import publication_directory
from agent_logger.usage_calibration import CalibrationPolicy, calibrate
from agent_logger.usage_estimation import estimate
from agent_logger.usage_feature_contract import MAX_SESSIONS, admit_sessions, validate_feature_session
from agent_logger.usage_feature_evidence import enrich_features
from agent_logger.usage_features import read_transcript_features
from agent_logger.usage_ledger import MAX_BYTES, MAX_RECORDS, UsageEvidenceError
from agent_logger.usage_sqlite import UsageReadError, read_sqlite_usage

FEATURE_SET_SCHEMA = "agent-logger.transcript-feature-set"


def _load(path: str) -> object:
    target = Path(path).absolute()
    with publication_directory(target.parent, create=False) as directory:
        with directory.open(target.name) as stream:
            before = os.fstat(stream.fileno())
            if before.st_size > MAX_BYTES:
                raise UsageEvidenceError("usage JSON input exceeds byte limit")
            data = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
            if len(data) > MAX_BYTES or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise UsageEvidenceError("usage JSON input changed or exceeds byte limit")
    return json.loads(data)


def _documents(paths: list[str]) -> list[dict]:
    if len(paths) > MAX_SESSIONS:
        raise UsageEvidenceError("usage input file limit exceeded")
    documents = []
    calls = 0
    for path in paths:
        value = _load(path)
        if isinstance(value, dict) and value.get("schema") == FEATURE_SET_SCHEMA:
            if (
                type(value.get("version")) is not int or value["version"] != 1
                or not isinstance(value.get("sessions"), list)
            ):
                raise UsageEvidenceError("incompatible usage feature collection")
            batch = value["sessions"]
        else:
            batch = [value]
        if len(documents) + len(batch) > MAX_SESSIONS:
            raise UsageEvidenceError("usage input session limit exceeded")
        for item in batch:
            item = validate_feature_session(item)
            calls += len(item["calls"])
            if calls > MAX_RECORDS:
                raise UsageEvidenceError("usage input total call limit exceeded")
            documents.append(item)
    return documents


def _ref(value: str) -> SessionRef:
    path = Path(value).absolute()
    stem = archive_stem(path)
    return SessionRef(
        id=stem if stem else path.name, kind="archive" if stem else "live", path=path,
        store=path.parent if stem else None,
    )


def _execute(args: argparse.Namespace) -> dict:
    if args.usage_command == "features":
        if len(args.session) > MAX_SESSIONS:
            raise UsageEvidenceError("usage session limit exceeded")
        refs = [_ref(value) for value in args.session]
        documents = []
        calls = 0
        for ref in refs:
            document = read_transcript_features(ref)
            calls += len(document["calls"])
            if calls > MAX_RECORDS:
                raise UsageEvidenceError("usage extraction total call limit exceeded")
            documents.append(document)
        rows: dict[str, list[dict]] = {}
        if args.usage_store:
            ids = {ref.id for ref in refs} | {document["session_id"] for document in documents}
            rows = read_sqlite_usage(Path(args.usage_store), ids)
        for ref, document in zip(refs, documents):
            selected = {ref.id, document["session_id"]}
            records = [record for identity in selected for record in rows.get(identity, [])]
            enrich_features(document, ref, additional_records=records)
        return {"schema": FEATURE_SET_SCHEMA, "version": 1, "sessions": admit_sessions(documents)}
    if args.usage_command == "calibrate":
        return calibrate(
            _documents(args.features), cohort=args.cohort,
            policy=CalibrationPolicy(
                min_train_sessions=args.min_train_sessions,
                min_validation_sessions=args.min_validation_sessions,
                holdout_modulus=args.holdout_modulus,
                max_p90_relative_error=args.max_p90_relative_error,
            ),
        )
    return estimate(_documents(args.features), _load(args.calibration))


def _run(args: argparse.Namespace) -> int:
    try:
        result = _execute(args)
        output = bytearray()
        for chunk in json.JSONEncoder(ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2).iterencode(result):
            output.extend(chunk.encode("utf-8"))
            if len(output) > MAX_BYTES:
                raise UsageEvidenceError("usage JSON output exceeds byte limit")
        print(output.decode("utf-8"))
        return 0
    except (OSError, ValueError, UsageReadError, RecursionError) as exc:
        print(json.dumps({"schema": "agent-logger.usage-error", "version": 1, "error": str(exc)}), file=sys.stderr)
        return 2


def register_usage(subparsers: argparse._SubParsersAction) -> None:
    usage = subparsers.add_parser("usage", help="count-only transcript features and calibrated historical estimates")
    commands = usage.add_subparsers(dest="usage_command", required=True)
    features = commands.add_parser("features", help="read explicitly selected live/session archive paths")
    features.add_argument("--session", action="append", required=True, help="session directory or archive; repeatable")
    features.add_argument("--usage-store", help="optional read-only producing-machine session-store.db")
    calibration = commands.add_parser("calibrate", help="fit session-disjoint empirical relationships")
    calibration.add_argument("--features", action="append", required=True, help="feature JSON; repeatable")
    calibration.add_argument("--cohort", required=True, help="operator-assigned source cohort label")
    calibration.add_argument("--min-train-sessions", type=int, default=3)
    calibration.add_argument("--min-validation-sessions", type=int, default=2)
    calibration.add_argument("--holdout-modulus", type=int, default=5)
    calibration.add_argument("--max-p90-relative-error", type=float, help="optional admission ceiling; absent means best effort")
    estimation = commands.add_parser("estimate", help="report measured tokens separately from estimates/unknowns")
    estimation.add_argument("--features", action="append", required=True)
    estimation.add_argument("--calibration", required=True)
    for parser in (features, calibration, estimation):
        parser.set_defaults(func=_run)
