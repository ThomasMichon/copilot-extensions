#!/usr/bin/env python3
"""Rehearse source tests with pinned preserved control in a disposable Git fixture.

Run under the normal bounded test supervisor. This checks composition contracts;
it is not generated-consumer, native-platform or live release qualification.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile


STATE = ".github/release-pipeline-state.json"
MANIFEST = ".github/promotion-control.json"


def fixture_environment() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_PAGER="cat")
    return env


def git_bytes(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "--no-pager", "--no-replace-objects", "-C", str(root), *args],
        check=True, capture_output=True, timeout=60,
        env=fixture_environment(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout


def git(root: Path, *args: str) -> str:
    return git_bytes(root, *args).decode("utf-8").strip()


def blob(root: Path, sha: str, name: str) -> bytes:
    entry = git(root, "ls-tree", sha, "--", name)
    if not entry.startswith("100644 blob "):
        raise ValueError(f"preserved control must be a regular nonexecutable file: {name}")
    return git_bytes(root, "show", f"{sha}:{name}")


def write_fixture(root: Path, name: str, data: bytes) -> None:
    target = root / name
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"fixture destination escapes its checkout: {name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def revision(root: Path, ref: str) -> str:
    value = git(root, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError(f"control rehearsal requires an immutable commit: {ref!r}")
    return value


def control_paths(raw: str) -> list[str]:
    manifest = json.loads(raw)
    files = manifest["files"]
    if not isinstance(files, list) or not files:
        raise ValueError("preservation manifest must contain a nonempty workflow list")
    seen: set[str] = set()
    for name in files:
        if not isinstance(name, str):
            raise ValueError("preserved workflow names must be strings")
        path = PurePosixPath(name)
        if (
            len(path.parts) != 3 or path.parts[:2] != (".github", "workflows")
            or not re.fullmatch(r"[A-Za-z0-9_.-]+\.(?:yml|yaml)", path.name)
            or path.name in {".", ".."} or name != path.as_posix()
            or name.casefold() in seen
        ):
            raise ValueError(f"unsafe or duplicate preserved workflow: {name!r}")
        seen.add(name.casefold())
    if not {".github/workflows/ci.yml", ".github/workflows/validate-and-promote.yml"}.issubset(files):
        raise ValueError("control rehearsal requires preserved CI and promotion workflows")
    return [MANIFEST, *files]


def selected_tests(text: str, explicit: list[str] | None) -> list[str]:
    names = explicit if explicit is not None else sorted(set(re.findall(
        r"\btools/test_[A-Za-z0-9_]+\.py\b", text,
    )))
    if not names:
        raise ValueError("control rehearsal requires at least one test module")
    for name in names:
        if not re.fullmatch(r"tools/test_[A-Za-z0-9_]+\.py", name):
            raise ValueError(f"invalid rehearsal test module: {name!r}")
    return list(dict.fromkeys(names))


def commit_fixture(root: Path, message: str) -> str:
    git(root, "add", ".github")
    git(
        root, "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "--quiet", "-m", message,
    )
    return revision(root, "HEAD")


def rehearse(
    source: Path, control_ref: str, tests: list[str] | None = None,
    overlay: Path | None = None, scratch: Path | None = None,
) -> int:
    source = source.resolve(strict=True)
    if git(source, "status", "--porcelain"):
        raise ValueError("commit the source snapshot before control rehearsal")
    source_sha, control_sha = revision(source, "HEAD"), revision(source, control_ref)
    paths = control_paths(blob(source, control_sha, MANIFEST).decode("utf-8"))
    original_data = {name: blob(source, control_sha, name) for name in [*paths, STATE]}
    if overlay is not None:
        overlay = overlay.resolve(strict=True)
        for name in ("ci.yml", "validate-and-promote.yml"):
            if f".github/workflows/{name}" not in paths or not (overlay / name).is_file():
                raise ValueError(f"control overlay requires a preserved {name}")
            if (overlay / name).is_symlink():
                raise ValueError(f"control overlay cannot use a symlink: {name}")

    with tempfile.TemporaryDirectory(prefix="promotion-control-rehearsal-", dir=scratch) as directory:
        fixture = Path(directory) / "repo"
        subprocess.run(
            ["git", "clone", "--quiet", "--shared", "--no-checkout", str(source), str(fixture)],
            check=True, timeout=60, env=fixture_environment(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if overlay is not None:
            git(fixture, "checkout", "--quiet", "--detach", control_sha)
            changed = False
            for name in ("ci.yml", "validate-and-promote.yml"):
                target = fixture / ".github/workflows" / name
                data = (overlay / name).read_bytes()
                if target.read_bytes() != data:
                    write_fixture(fixture, f".github/workflows/{name}", data)
                    changed = True
            if changed:
                control_sha = commit_fixture(fixture, "proposed control fixture; not a release")
        data = (
            original_data if overlay is None
            else {name: blob(fixture, control_sha, name) for name in [*paths, STATE]}
        )
        state = json.loads(data[STATE])
        if not isinstance(state.get("last_promotion"), dict):
            raise ValueError("control rehearsal requires recorded promotion lineage")
        state["last_promotion"]["dev_head"] = source_sha
        git(fixture, "checkout", "--quiet", "--detach", source_sha)
        for name in paths:
            write_fixture(fixture, name, data[name])
        write_fixture(fixture, STATE, (json.dumps(state, sort_keys=True) + "\n").encode("utf-8"))
        commit_fixture(fixture, "preserved control compatibility fixture; not a release")
        git(fixture, "update-ref", "refs/remotes/origin/main", control_sha)
        git(fixture, "update-ref", "refs/remotes/origin/dev", source_sha)
        declarations = data[".github/workflows/ci.yml"] + b"\n" + blob(
            fixture, source_sha, ".github/workflows/ci.yml",
        )
        names = selected_tests(declarations.decode("utf-8"), tests)
        for name in names:
            target = fixture / name
            if target.is_symlink() or not target.resolve().is_relative_to(fixture.resolve()):
                raise ValueError(f"test module escapes the fixture: {name}")
            if not target.is_file():
                raise ValueError(f"test module is unavailable in the source snapshot: {name}")
        print(json.dumps({
            "source": source_sha, "control": control_sha, "tests": names,
            "evidence": "preserved-control compatibility, not release qualification",
        }, sort_keys=True), flush=True)
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=short", *names],
            cwd=fixture, env=fixture_environment(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--control-ref", default="origin/main")
    parser.add_argument("--workflow-overlay", type=Path)
    parser.add_argument("--scratch", type=Path)
    parser.add_argument("--tests", nargs="+")
    args = parser.parse_args(argv)
    return rehearse(args.source, args.control_ref, args.tests, args.workflow_overlay, args.scratch)


if __name__ == "__main__":
    raise SystemExit(main())
