#!/usr/bin/env python3
"""Run the positively admitted unit packet once, with coverage and no child actors."""

from __future__ import annotations

import argparse
import _thread
import hashlib
import importlib
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

from promotion_unit_contract import (
    PROFILE, UnitEvidenceError, admit_manifest, decode_json, manifest_digest, source_sha,
    verify_observations, verify_sources,
)
from promotion_unit_effects import UnitRun
from promotion_unit_packet import verify_packet

ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "tools/promotion_unit_tests"


def prepare_framework_io() -> None:
    if sys.platform == "win32":
        importlib.import_module("colorama")


def write_line_coverage(cov, path: Path, version: str) -> None:
    data = cov.get_data()
    files = {}
    totals = dict(covered_lines=0, num_statements=0, missing_lines=0, excluded_lines=0)
    for filename in sorted(data.measured_files()):
        lines = data.lines(filename)
        if lines is None:
            raise UnitEvidenceError("measured coverage file has no line data")
        _, statements, excluded, missing, _ = cov.analysis2(filename)
        summary = {
            "covered_lines": len(statements) - len(missing),
            "num_statements": len(statements), "missing_lines": len(missing),
            "excluded_lines": len(excluded),
        }
        for field, value in summary.items():
            totals[field] += value
        files[Path(filename).resolve().relative_to(ROOT).as_posix()] = {
            "executed_lines": sorted(lines), "summary": summary,
            "missing_lines": sorted(missing), "excluded_lines": sorted(excluded),
            "contexts": {str(line): sorted(contexts) for line, contexts in data.contexts_by_lineno(filename).items()},
            "functions": {}, "classes": {},
        }
    report = {
        "meta": {
            "profile": PROFILE, "collector": "coverage.py", "version": version,
            "scope": "file-line-contexts; function/class expansions intentionally omitted",
            "show_contexts": True, "branch_coverage": False,
        },
        "files": files, "totals": totals,
    }
    path.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True, type=source_sha)
    parser.add_argument("--manifest", type=Path, default=ROOT / "tools/promotion-unit-manifest.json")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--development-root", type=Path)
    parser.add_argument("--candidate-tree", type=source_sha)
    args = parser.parse_args()
    started = time.monotonic()
    manifest = admit_manifest(decode_json(args.manifest.read_text(encoding="utf-8")))
    mapping = None
    if (args.development_root is None) != (args.candidate_tree is None):
        raise UnitEvidenceError("candidate execution requires both Dev bytes and the composed tree")
    if args.development_root is not None:
        from promotion_candidate_units import derive_manifest

        manifest, development, candidate = derive_manifest(
            manifest, development_root=args.development_root.resolve(), candidate_root=ROOT,
            dev_sha=args.source_sha, candidate_tree=args.candidate_tree,
        )
    verify_sources(ROOT, manifest["sources"])
    args.output_dir.mkdir(parents=True, exist_ok=False)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ.pop("PYTEST_ADDOPTS", None)
    os.environ.pop("PYTEST_PLUGINS", None)
    sys.dont_write_bytecode = True
    import coverage
    import pytest

    # Pytest's Windows capture adapter loads native console support before collection.
    prepare_framework_io()
    with tempfile.TemporaryDirectory(prefix="promotion-units-") as temporary:
        run = UnitRun(None, ROOT, manifest["sources"], Path(temporary), nodes=manifest["nodes"])
        cov = coverage.Coverage(
            data_file=None, config_file=False,
            include=[str(ROOT / name) for name in manifest["coverage_targets"]],
            plugins=[lambda registry: registry.add_dynamic_context(run)],
        )
        run.coverage = cov
        sys.addaudithook(run.audit)
        with (
            patch.object(tempfile, "tempdir", temporary),
            patch.object(sys, "pycache_prefix", temporary),
            patch.object(os, "open", run.guarded_open),
            patch.object(threading.Thread, "start", lambda *_a, **_k: run.refuse("thread start")),
            patch.object(_thread, "start_new_thread", lambda *_a, **_k: run.refuse("thread start")),
            patch.object(time, "sleep", lambda *_a, **_k: run.refuse("sleep")),
        ):
            cov.start()
            run.active = True
            try:
                result = pytest.main([
                    "-q", "-x", "--capture=sys", "-c", os.devnull, "-p", "no:cacheprovider",
                    "--noconftest", "--import-mode=importlib", "--assert=plain",
                    "--log-file", str(Path(temporary) / "pytest.log"),
                    "--rootdir", str(ROOT), "--confcutdir", str(ROOT),
                    "--basetemp", str(Path(temporary) / "pytest"),
                    *[str(ROOT / name) for name in dict.fromkeys(
                        node.partition("::")[0] for node in manifest["nodes"]
                    )],
                ], plugins=[run])
            finally:
                run.active = False
                cov.stop()
    elapsed = time.monotonic() - started
    if result != pytest.ExitCode.OK:
        raise UnitEvidenceError(f"unit execution failed with pytest exit code {result}")
    verify_observations(
        manifest["nodes"], run.collected, run.reports, run.effects,
        elapsed, manifest["max_seconds"],
    )
    imported = run.opened_sources | {
        Path(module.__file__).resolve().relative_to(ROOT).as_posix()
        for module in tuple(sys.modules.values())
        if getattr(module, "__file__", None)
        and Path(module.__file__).resolve().is_relative_to(ROOT)
        and str(module.__file__).endswith(".py")
    }
    unexpected = imported - set(manifest["sources"])
    if unexpected:
        raise UnitEvidenceError("unadmitted local import closure: " + ", ".join(sorted(unexpected)))
    verify_sources(ROOT, manifest["sources"])
    cov.save()
    coverage_path = args.output_dir / "coverage.json"
    write_line_coverage(cov, coverage_path, coverage.__version__)
    if args.development_root is not None:
        from promotion_development_map import build_mapping, project_coverage

        raw = decode_json(coverage_path.read_bytes())
        raw["meta"]["profile"] = manifest["profile"]
        origins = {name: manifest["composition"]["origins"][name] for name in raw["files"]}
        mapping = build_mapping(
            dev_sha=args.source_sha, candidate_tree=args.candidate_tree,
            sources={name: development[name] for name in set(origins.values())},
            candidate_sources={name: candidate[name] for name in origins}, origins=origins,
        )
        raw["development_projection"] = project_coverage(raw, mapping)
        raw["development_mapping"] = mapping
        coverage_path.write_text(
            json.dumps(raw, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8",
        )
    measured = {
        Path(name).resolve().relative_to(ROOT).as_posix() for name in cov.get_data().measured_files()
    }
    if mapping is None:
        complete = measured == set(manifest["coverage_targets"])
    else:
        origins = manifest["composition"]["origins"]
        complete = (
            measured <= set(manifest["coverage_targets"])
            and {origins[name] for name in measured}
            == set(manifest["composition"]["required_coverage_origins"])
        )
    if not complete:
        raise UnitEvidenceError("coverage did not observe every admitted production target")
    elapsed = time.monotonic() - started
    verify_observations(
        manifest["nodes"], run.collected, run.reports, run.effects,
        elapsed, manifest["max_seconds"],
    )
    proof = {
        "profile": manifest["profile"], "source_sha": args.source_sha,
        "manifest_sha256": manifest_digest(manifest), "elapsed_seconds": elapsed,
        "collected": run.collected, "reports": run.reports, "effects": run.effects,
        "imported_sources": sorted(imported), "coverage_file": coverage_path.name,
        "coverage_sha256": hashlib.sha256(coverage_path.read_bytes()).hexdigest(),
    }
    (args.output_dir / "unit-proof.json").write_text(
        json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    (args.output_dir / "unit-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    verify_packet(
        args.output_dir, expected_source_sha=args.source_sha,
        expected_manifest=manifest, source_root=ROOT, development_root=args.development_root,
    )
    if time.monotonic() - started >= manifest["max_seconds"]:
        raise UnitEvidenceError("unit packet verification exceeded the whole-execution budget")
    print(f"Unit packet complete: {len(run.collected)} passed in {elapsed:.3f}s; coverage recorded once.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (UnitEvidenceError, OSError, json.JSONDecodeError) as error:
        print(f"promotion unit evidence: {error}", file=sys.stderr)
        raise SystemExit(1)
