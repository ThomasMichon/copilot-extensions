"""Small additive proof for a managed, complete source-suite baseline."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

PROFILE = "contained-complete-baseline-v1"
TOOLS = (
    "tools/run-plugin-tests.py", "tools/pytest_portfolio_guard.py",
    "tools/plugin_test_containment.py", "tools/coverage_guided_selection/baseline.py",
    "tools/coverage_guided_selection/baseline_chunks.py",
    "tools/coverage_guided_selection/completion.py",
)
MAXIMA = dict(plugin_seconds=900, chunk_seconds=300, item_seconds=30,
              max_processes=128, max_memory_mb=4096, max_temp_mb=2048)


class GitReadError(subprocess.CalledProcessError):
    """Preserve the Git failure type and its literal diagnostic context."""

    def __init__(self, root: Path, error: subprocess.CalledProcessError):
        super().__init__(error.returncode, error.cmd, output=error.output, stderr=error.stderr)
        self.cwd = str(root.resolve())

    def __str__(self) -> str:
        stderr = self.stderr.decode("utf-8", errors="replace") if isinstance(self.stderr, bytes) else self.stderr
        return f"{super().__str__()} cwd={self.cwd!r}; stderr:\n{stderr or '(empty)'}"


def validate_bounds(bounds: dict) -> None:
    if not isinstance(bounds, dict) or set(bounds) != set(MAXIMA) or any(
        type(bounds[k]) not in (int, float) or not math.isfinite(bounds[k])
        or not 0 < bounds[k] <= v for k, v in MAXIMA.items()
    ):
        raise ValueError("invalid complete baseline bounds")


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _git(root: Path, *args: str, data: bytes | None = None) -> bytes:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    try:
        return subprocess.run(
            ["git", "-c", f"safe.directory={root.resolve()}", "--no-replace-objects", *args],
            cwd=root, env=env,
            input=data, check=True, capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise GitReadError(root, exc) from exc


def collector_identity(root: Path, source: str, *, check_working: bool = False) -> str:
    if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("complete baseline requires an exact source commit")
    blobs = {}
    for name in TOOLS:
        blob = _git(root, "show", f"{source}:{name}")
        if check_working and (root / name).read_bytes() != blob:
            raise ValueError("complete baseline collector differs from its source commit")
        blobs[name] = hashlib.sha256(blob).hexdigest()
    return digest(blobs)


def source_files(root: Path, source: str, plugin: str, *, check_working: bool = False) -> dict:
    prefix = f"plugins/{plugin}/tests/"
    files = {}
    for entry in _git(root, "ls-tree", "-rz", source, "--", prefix).split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        name = path.decode("utf-8")
        if Path(name).match("test_*.py") or Path(name).match("*_test.py"):
            mode, kind, blob = metadata.split()
            if mode not in {b"100644", b"100755"} or kind != b"blob" or "\n" in name:
                raise ValueError("complete baseline requires regular committed test files")
            files[name] = blob.decode()
    if not files:
        raise ValueError("empty complete baseline file corpus")
    if check_working:
        if _git(root, "rev-parse", "HEAD").decode().strip() != source:
            raise ValueError("complete baseline checkout is not the exact source commit")
        discovered = {
            p.relative_to(root).as_posix() for p in (root / prefix).rglob("*.py")
            if p.name.startswith("test_") or p.name.endswith("_test.py")
        }
        actual = _git(
            root, "hash-object", "--no-filters", "--stdin-paths",
            data=("\n".join(sorted(files)) + "\n").encode(),
        ).decode().splitlines()
        if discovered != set(files) or actual != [files[k] for k in sorted(files)]:
            raise ValueError("complete baseline test corpus differs from source commit")
        _git(root, "diff", "--exit-code", source, "--", ".")
        if _git(root, "ls-files", "--others", "--exclude-standard", "--", "plugins", "libs", "tools"):
            raise ValueError("complete baseline source has uncommitted code files")
    return files


def validate_chunk(chunk: dict) -> None:
    collected, outcomes = chunk.get("collected"), chunk.get("outcomes")
    tests, tiers = chunk.get("durations"), chunk.get("tiers")
    if (
        not isinstance(collected, list) or not collected
        or any(not isinstance(n, str) or not n for n in collected)
        or len(set(collected)) != len(collected) or chunk.get("deselected") != []
        or not isinstance(outcomes, dict) or set(outcomes) != set(collected)
        or not isinstance(tests, dict) or set(tests) != set(collected)
        or not isinstance(tiers, dict) or set(tiers) != set(collected)
        or any(v not in {"passed", "skipped", "xfailed", "xpassed"} for v in outcomes.values())
        or any(type(v) not in (float, int) or not math.isfinite(v) or v < 0 for v in tests.values())
        or any(v not in {None, "T0", "T1", "T2", "T3", "T4"} for v in tiers.values())
    ):
        raise ValueError("incomplete, duplicate, deselected or failed baseline corpus")
    toolchain = chunk.get("toolchain")
    if (
        not isinstance(toolchain, dict)
        or set(toolchain) != {"python", "platform", "pytest", "coverage", "pytest-json-report", "pytest-timeout"}
        or any(not isinstance(v, str) or not v for v in toolchain.values())
        or toolchain["platform"] != "linux" or not toolchain["python"].startswith("3.12.")
    ):
        raise ValueError("unsupported or missing complete baseline toolchain")


def seal(baseline: dict, chunks: list[dict], *, collector: str, workers: int, bounds: dict,
         files: dict[str, str], max_files: int = 5) -> None:
    all_ids: set[str] = set()
    outcomes = {}
    for chunk in chunks:
        validate_chunk(chunk)
        if all_ids.intersection(chunk["collected"]) or chunk["toolchain"] != chunks[0]["toolchain"]:
            raise ValueError("duplicate baseline chunks or mismatched toolchains")
        all_ids.update(chunk["collected"])
        outcomes.update(chunk["outcomes"])
    if not chunks or set(baseline["tests"]) != all_ids:
        raise ValueError("incomplete merged baseline corpus")
    baseline["complete_execution"] = {
        "version": 1, "profile": PROFILE, "collector": collector,
        "toolchain": chunks[0]["toolchain"], "workers": workers, "bounds": bounds,
        "files": files, "corpus": digest(sorted(all_ids)), "outcomes": outcomes,
        "max_files_per_chunk": max_files, "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
    }
    baseline["complete_execution"]["digest"] = digest(baseline)


def verify(
    baseline: dict, *, source: str, collector: str, plugin: str, files: dict | None = None,
    workers: int | None = None, run_id: str | None = None,
) -> None:
    proof = baseline.get("complete_execution")
    if not isinstance(proof, dict):
        raise ValueError("missing complete baseline proof")
    payload = json.loads(json.dumps(baseline))
    claimed = payload["complete_execution"].pop("digest", None)
    if (
        claimed != digest(payload) or type(proof.get("version")) is not int or proof["version"] != 1
        or proof.get("profile") != PROFILE or proof.get("collector") != collector
        or baseline.get("schema_version") != 3 or baseline.get("measured_commit") != source
        or baseline.get("plugin") != plugin
        or baseline.get("cov_source") != f"plugins/{plugin}/src/{plugin.replace('-', '_')}"
        or type(proof.get("workers")) is not int or proof["workers"] not in (1, 2)
        or (workers is not None and proof["workers"] != workers)
        or type(proof.get("max_files_per_chunk")) is not int
        or not 1 <= proof["max_files_per_chunk"] <= 5
        or (run_id is not None and proof.get("run_id") != run_id)
        or proof.get("corpus") != digest(sorted(baseline.get("tests", {})))
    ):
        raise ValueError("mismatched complete baseline provenance")
    if any(
        proof.get(k) is not None and (
            not isinstance(proof[k], str) or not proof[k].isdigit() or int(proof[k]) < 1
        )
        for k in ("run_id", "run_attempt")
    ):
        raise ValueError("invalid complete baseline run identity")
    validate_bounds(proof.get("bounds"))
    records = baseline.get("tests", {})
    if not isinstance(records, dict) or any(not isinstance(v, dict) for v in records.values()):
        raise ValueError("invalid complete baseline test records")
    validate_chunk({
        "collected": list(records), "deselected": [], "outcomes": proof.get("outcomes"),
        "durations": {k: v.get("duration_s") for k, v in records.items()},
        "tiers": {k: v.get("portfolio_tier") for k, v in records.items()},
        "toolchain": proof.get("toolchain"),
    })
    recorded_files = proof.get("files")
    prefix = f"plugins/{plugin}/tests/"
    if not isinstance(recorded_files, dict) or not recorded_files or any(
        not isinstance(k, str) or not k.startswith(prefix) or ".." in k.split("/")
        or not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{40}", v)
        for k, v in recorded_files.items()
    ) or (files is not None and files != recorded_files):
        raise ValueError("missing complete baseline file corpus")
    node_paths = set(recorded_files) | {name.removeprefix(f"plugins/{plugin}/") for name in recorded_files}
    if any(node.split("::", 1)[0] not in node_paths for node in records):
        raise ValueError("complete baseline node is outside the declared corpus")
    coverage = baseline.get("coverage")
    if not isinstance(coverage, dict) or not coverage:
        raise ValueError("empty complete baseline coverage")
    for name, per_line in coverage.items():
        if (
            not isinstance(name, str) or not name.startswith(baseline["cov_source"] + "/")
            or ".." in name.split("/") or not isinstance(per_line, dict) or not per_line
        ):
            raise ValueError("empty complete baseline attribution")
        for line, ids in per_line.items():
            if (
                not isinstance(line, str) or not line.isdigit() or int(line) < 1
                or not isinstance(ids, list) or not ids
                or any(not isinstance(n, str) or n not in records for n in ids)
            ):
                raise ValueError("unknown complete baseline attribution")


def verify_optional(
    baseline: dict, *, root: Path, source: str, plugin: str,
    error_type: type[Exception] = ValueError,
) -> None:
    """Preserve legacy ingestion, but strictly validate any complete-execution claim."""
    if "complete_execution" not in baseline:
        return
    try:
        verify(
            baseline, source=source, collector=collector_identity(root, source),
            plugin=plugin, files=source_files(root, source, plugin),
        )
    except (ValueError, subprocess.CalledProcessError, OSError) as exc:
        raise error_type(f"invalid complete baseline {plugin}.json: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("--source", required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--plugin", required=True)
    parser.add_argument("--workers", type=int, choices=(1, 2))
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    data = json.loads(args.baseline.read_text("utf-8"))
    verify(
        data, source=args.source, collector=collector_identity(args.repo, args.source),
        plugin=args.plugin, files=source_files(args.repo, args.source, args.plugin),
        workers=args.workers, run_id=args.run_id,
    )
    print(f"verified complete source baseline: {args.plugin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
