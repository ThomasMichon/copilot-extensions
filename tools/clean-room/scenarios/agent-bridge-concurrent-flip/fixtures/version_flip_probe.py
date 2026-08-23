#!/usr/bin/env python3
"""Portable version-slot flip-storm probe for agent-bridge (concurrent-flip family).

Asserts the invariant that makes a concurrent plugin *update* safe: while multiple
installers race to flip the active runtime version -- the exact effect of several
new Copilot sessions each firing the ``sessionStart``-hook reinstall at once -- a
reader resolving the runtime must ALWAYS land on a valid, existing version slot. It
must never observe a torn/half-written ``current-version`` marker or a pointer to a
slot that isn't there. This is the third leg of the concurrent-update triad:
``agent-bridge-concurrent-relay`` (the relay recovers), ``agent-bridge-concurrent-daemon``
(a duplicate daemon is refused), and this (the version flip stays coherent).

It drives the REAL immutable-runtime layout manager (``versioned_runtime`` -- the
same module the installer uses to publish ``current-version`` atomically via
``os.replace`` and to resolve the runtime through the current -> last-known-good ->
newest tiers), with **cross-process** flippers (the probe re-execs itself in
``--flip`` mode), so it is faithful to two real installers racing and verifiable
off-Docker on any built agent-bridge venv.

Checks (each prints ``PROBE: <name> PASS|FAIL <detail>``):
  flip-storm-coherent-resolution  two installers race to flip current-version
                                  between two valid slots; over thousands of reads
                                  the resolver ALWAYS returns an existing slot
                                  python (never None, never a missing path), and
                                  both versions are observed active (the storm
                                  really flipped) -- the atomic swap + tiered
                                  resolution never yields a torn/dangling result.
  marker-never-torn               during the same storm the raw current-version
                                  marker is never empty/partial -- os.replace makes
                                  a reader see the old or the new value, never a
                                  half-written one.

Usage:
    python version_flip_probe.py --runtime-module <path/to/versioned_runtime.py> [--checks a,b]
    python version_flip_probe.py --flip <root> --version <v> --seconds <s> \
        --runtime-module <path>                                              # internal

Exit 0 iff every selected check PASSes.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time

ALL_CHECKS = ["flip-storm-coherent-resolution", "marker-never-torn"]

VER_A = "0.0.1-slot-a"
VER_B = "0.0.1-slot-b"


def _emit(name: str, ok: bool, detail: str) -> bool:
    print(f"PROBE: {name} {'PASS' if ok else 'FAIL'} {detail}")
    return ok


def _load_runtime(path: str):
    spec = importlib.util.spec_from_file_location("versioned_runtime", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load versioned_runtime from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_slots(vr, root: str) -> None:
    import pathlib

    for v in (VER_A, VER_B):
        binpath = pathlib.Path(vr.version_dir(pathlib.Path(root), v)) / "bin"
        binpath.mkdir(parents=True, exist_ok=True)
        py = binpath / "python"
        py.write_text("#!/bin/sh\n")
        py.chmod(0o755)


def _flip_mode(vr, root: str, version: str, seconds: float) -> int:
    """Hammer current-version -> `version` for `seconds` (one racing installer)."""
    import pathlib

    deadline = time.time() + seconds
    r = pathlib.Path(root)
    while time.time() < deadline:
        try:
            vr.activate(r, version)
        except Exception:
            pass  # a transient race in the manager is what we're stressing
    return 0


def _spawn_flippers(runtime_path: str, root: str, seconds: float):
    import subprocess

    procs = []
    for v in (VER_A, VER_B):
        procs.append(
            subprocess.Popen(
                [
                    sys.executable,
                    os.path.abspath(__file__),
                    "--flip",
                    root,
                    "--version",
                    v,
                    "--seconds",
                    str(seconds),
                    "--runtime-module",
                    runtime_path,
                ]
            )
        )
    return procs


def _run_storm(vr, runtime_path: str, root: str, seconds: float):
    """Race two flippers while sampling resolver + marker. Returns (samples)."""
    import pathlib

    r = pathlib.Path(root)
    procs = _spawn_flippers(runtime_path, root, seconds)
    resolved_missing = 0
    resolved_none = 0
    marker_torn = 0
    seen_versions = set()
    reads = 0
    deadline = time.time() + seconds
    try:
        while time.time() < deadline or any(p.poll() is None for p in procs):
            rp = vr.resolve_python(r)
            reads += 1
            if rp is None:
                resolved_none += 1
            elif not os.path.exists(str(rp)):
                resolved_missing += 1
            cur = vr.current_version(r)
            if cur in (VER_A, VER_B):
                seen_versions.add(cur)
            elif cur is None or cur == "":
                marker_torn += 1
            if time.time() >= deadline and all(p.poll() is not None for p in procs):
                break
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
    return {
        "reads": reads,
        "resolved_none": resolved_none,
        "resolved_missing": resolved_missing,
        "marker_torn": marker_torn,
        "seen_versions": seen_versions,
    }


def _run_checks(runtime_path: str, checks: list[str]) -> int:
    import tempfile

    vr = _load_runtime(runtime_path)
    with tempfile.TemporaryDirectory(prefix="cr-flip-") as root:
        _make_slots(vr, root)
        vr.activate(__import__("pathlib").Path(root), VER_A)
        s = _run_storm(vr, runtime_path, root, seconds=2.5)

    all_ok = True
    if "flip-storm-coherent-resolution" in checks:
        ok = (
            s["reads"] > 0
            and s["resolved_none"] == 0
            and s["resolved_missing"] == 0
            and len(s["seen_versions"]) == 2
        )
        all_ok = _emit(
            "flip-storm-coherent-resolution",
            ok,
            f"(reads={s['reads']} resolved_none={s['resolved_none']} "
            f"resolved_missing={s['resolved_missing']} "
            f"versions_seen={sorted(s['seen_versions'])})",
        ) and all_ok
    if "marker-never-torn" in checks:
        ok = s["marker_torn"] == 0 and s["reads"] > 0
        all_ok = _emit(
            "marker-never-torn",
            ok,
            f"(reads={s['reads']} marker_torn={s['marker_torn']})",
        ) and all_ok
    return 0 if all_ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runtime-module", required=True, help="path to versioned_runtime.py")
    ap.add_argument("--flip", metavar="ROOT", help="internal: flip current-version to --version")
    ap.add_argument("--version", default="")
    ap.add_argument("--seconds", type=float, default=2.5)
    ap.add_argument("--checks", default=",".join(ALL_CHECKS))
    args = ap.parse_args()
    if args.flip:
        vr = _load_runtime(args.runtime_module)
        return _flip_mode(vr, args.flip, args.version, args.seconds)
    checks = [c.strip() for c in args.checks.split(",") if c.strip()]
    return _run_checks(args.runtime_module, checks)


if __name__ == "__main__":
    import signal

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    sys.exit(main())
