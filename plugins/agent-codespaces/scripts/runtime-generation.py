"""Choose an immutable CodeSpaces install candidate without repairing an incumbent."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path


def _runtime():
    spec = importlib.util.spec_from_file_location(
        "_codespaces_versioned_runtime", Path(__file__).with_name("versioned_runtime.py"),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def select_generation(
    root: Path, version: str, payload_hash: str, source_identity: dict | None = None,
    *, require_retained_helpers: bool = False,
) -> str:
    runtime = _runtime()
    suffix = ".repair." if "+" in version else "+repair."
    repair = re.compile(re.escape(version + suffix) + r"[0-9a-f]{32}")

    def reusable(candidate: str) -> bool:
        if source_identity is not None:
            try:
                deployment = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
                if (deployment.get("runtime_version") != candidate
                        or deployment.get("runtime_source") != source_identity["runtime_source"]):
                    return False
            except (OSError, ValueError, AttributeError):
                return False
        if runtime.check_admission(root, candidate, payload_hash=payload_hash) != runtime.ADMIT_REUSE:
            return False
        if require_retained_helpers:
            try:
                runtime.retain_runtime_helpers(runtime.version_dir(root, candidate), complete=True)
            except (OSError, ValueError, RuntimeError):
                return False
        python = runtime.slot_python(root, candidate)
        if python is None:
            return False
        try:
            result = subprocess.run(
                [str(python), "-I", "-B", "-m", "agent_codespaces", "--help"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=30, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    candidates = []
    for read in (runtime.current_version, runtime.read_last_known_good):
        try:
            candidates.append(read(root))
        except UnicodeError:
            continue
    for candidate in dict.fromkeys(candidates):
        if candidate and repair.fullmatch(candidate) and reusable(candidate):
            return candidate
    slot = runtime.version_dir(root, version)
    if not os.path.lexists(slot):
        return version
    if reusable(version):
        return version
    return version + suffix + uuid.uuid4().hex


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--payload-hash", required=True)
    parser.add_argument("--source-identity", type=json.loads)
    parser.add_argument("--require-retained-helpers", action="store_true")
    args = parser.parse_args()
    print(select_generation(
        args.root, args.version, args.payload_hash, args.source_identity,
        require_retained_helpers=args.require_retained_helpers,
    ))


if __name__ == "__main__":
    main()
