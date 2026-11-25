"""Prepare the first Manager using an independently selected promoted commit."""
from __future__ import annotations

import argparse
from functools import partial
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile
from typing import Any, Literal, TypedDict, overload

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "tools"), str(ROOT / "libs/installer-engine"),
    str(ROOT / "libs/versioned-runtime"), str(ROOT / "libs/installation-context"),
]

import artifact_release_discovery as discovery
from artifact_attestation import verify_attestation_subject
import local_install_profile as profiles
import local_install_source as source
from promotion_source_history import PIPELINE_STATE_PATH


class ReleaseInputsUnavailable(ValueError):
    """The selected source has no published first-party artifact."""


class _ReleaseAccess(TypedDict, total=False):
    runner: discovery.ReleaseRunner
    verify_subject: discovery.SubjectVerifier


def prepare_snapshot(checkout: Path, commit: str, repository: str, output: Path, uv: Path, deadline: float,
                     *, component: str, extras: tuple[str, ...] = (),
                     runner: discovery.ReleaseRunner | None = None) -> Path:
    provenance = source.preparation._git(
        checkout, "ls-tree", "--name-only", commit, "--", PIPELINE_STATE_PATH,
        deadline=deadline,
    )
    if not provenance:
        raise ReleaseInputsUnavailable("Selected source predates the published wheel channel")
    if provenance != PIPELINE_STATE_PATH:
        raise ValueError("Selected source returned ambiguous promotion provenance")
    output.mkdir(parents=True, exist_ok=False)
    release = output / "release"
    release.mkdir()
    access: _ReleaseAccess = {
        "runner": runner,
        "verify_subject": partial(verify_attestation_subject, runner=runner),
    } if runner is not None else {}
    discovered = discovery.discover_promoted_source(
        release, repository=repository, repo=checkout, main_commit=commit, deadline=deadline,
        **access,
    )
    if discovered is None:
        raise ReleaseInputsUnavailable("Selected commit has no published release index")
    index, assets = discovered
    entries = [e for e in index["artifacts"] if e.get("component", e.get("plugin")) == component]
    if not entries:
        raise ReleaseInputsUnavailable(f"Selected release has no {component} artifact")
    if len(entries) != 1:
        raise ValueError(f"Selected release has ambiguous {component} artifacts")
    entry, = entries
    standalone = "component" in entry
    key = ("standalone:" if standalone else "") + component
    inputs = output / "inputs"
    record = source.compose(checkout, commit, None, inputs, projects=(key,),
                            closed_wheel_source=True, deadline=deadline)
    inputs = Path(source.native_retained_path(inputs))
    tree = inputs / "source"
    binding = discovery.read_selected_source_artifact(
        release, index=index, assets=assets, repository=repository, component=("standalone-" if standalone else "") + component,
        version=entry["version"], candidate_root=tree,
        **access,
    )
    if binding is None:
        raise ValueError(f"Selected {component} artifact disappeared after release admission")
    manifest, _ = binding
    _, _, payloads, identities = source._source_projects(tree, record, (key,))
    if identities[key].package_version != manifest["version"]:
        raise ValueError(f"{component} version differs from its selected source")
    pool = inputs / "wheels" / component
    pool.mkdir(parents=True)
    with zipfile.ZipFile(release / entry["filename"]) as archive:
        for row in manifest["wheels"]:
            filename = row["filename"]
            if Path(filename).name != filename:
                raise ValueError("Invalid wheel filename")
            with archive.open(filename) as src, (pool / filename).open("xb") as dst:
                shutil.copyfileobj(src, dst)
    record["projects"][key] = {
        "payload": payloads[key].relative_to(inputs).as_posix(), "source": identities[key].document(),
        "wheels": pool.relative_to(inputs).as_posix(), "artifact": manifest,
    }
    (inputs / source.MANIFEST).write_text(json.dumps(record), encoding="utf-8")
    result = profiles.prepare_registry_lock(inputs, output / "profile", uv=str(uv), deadline=deadline,
                                           prepare_profile=True, component=component, extras=extras)
    return Path(result["snapshot"])


def _github_runner(command: list[str]) -> discovery.ReleaseRunner:
    @overload
    def run(
        argv: list[str], *, timeout: float = 120, binary: Literal[False] = False,
        verification: bool = False,
    ) -> subprocess.CompletedProcess[str]: ...

    @overload
    def run(
        argv: list[str], *, timeout: float = 120, binary: Literal[True],
        verification: bool = False,
    ) -> subprocess.CompletedProcess[bytes]: ...

    def run(
        argv: list[str], *, timeout: float = 120, binary: bool = False,
        verification: bool = False,
    ) -> subprocess.CompletedProcess[Any]:
        environment = os.environ.copy()
        environment.pop("ARTIFACT_VERIFY_TOKEN", None)
        result = subprocess.run(
            [*command, *argv], capture_output=True, text=not binary,
            env=environment, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        diagnostics = [result.stderr]
        if result.returncode:
            diagnostics.append(result.stdout[:8192] if binary else result.stdout)
        for stream in diagnostics:
            messages = ("could not mint", "using ambient auth")
            refused = (
                any(message.encode("ascii") in stream.lower() for message in messages)
                if isinstance(stream, bytes)
                else any(message in stream.lower() for message in messages)
            )
            if refused:
                raise ValueError("Repository-scoped GitHub authentication is unavailable")
        return result
    return run


def prepare(checkout: Path, commit: str, repository: str, output: Path, uv: Path, deadline: float) -> Path:
    import application_footprint

    snapshot = prepare_snapshot(checkout, commit, repository, output, uv, deadline, component="worktree-manager")
    return application_footprint.export({"worktree-manager": snapshot}, output / "footprint", deadline=deadline)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--marketplace", action="store_true")
    parser.add_argument("--component", action="append")
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--uv", type=Path)
    parser.add_argument("--deadline", type=float)
    parser.add_argument("--github-command", type=json.loads)
    parser.add_argument("--index-host", action="store_true")
    parser.add_argument("--frozen-seeds", nargs=2, metavar=("MAIN", "DEV"))
    args = parser.parse_args(argv)
    if args.snapshot and (
        len(args.component or []) != 1 or args.marketplace or args.frozen_seeds
    ):
        parser.error("--snapshot requires one component and no footprint or marketplace export")
    if args.github_command is not None and (
        not args.snapshot or not isinstance(args.github_command, list)
        or not 1 <= len(args.github_command) <= 64
        or any(not isinstance(value, str) or not value or any(char in value for char in "\r\n\0")
               for value in args.github_command)
    ):
        parser.error("--github-command requires a bounded command vector and --snapshot")
    if args.index_host and "agent-index" not in (args.component or []):
        parser.error("--index-host requires agent-index")
    extras: dict[str, tuple[str, ...]] = {"agent-index": ("server", "store")} if args.index_host else {}
    uv = str(args.uv) if args.uv is not None else shutil.which("uv")
    if not uv:
        parser.error("Manager preparation requires uv")
    checkout, output = args.checkout.resolve(strict=True), args.output.absolute()
    deadline = args.deadline if args.deadline is not None else time.monotonic() + 1500
    source.preparation._remaining(deadline)
    if args.snapshot:
        try:
            snapshot = prepare_snapshot(
                checkout, args.commit, args.repository, output, Path(uv), deadline,
                component=args.component[0], extras=extras.get(args.component[0], ()),
                **({"runner": _github_runner(args.github_command)}
                   if args.github_command is not None else {}),
            )
        except ReleaseInputsUnavailable as error:
            print(f"Released wheel inputs unavailable: {error}", file=sys.stderr)
            return 3
        print(json.dumps({"snapshot": str(snapshot)}, sort_keys=True))
    elif args.marketplace:
        if args.component or args.frozen_seeds:
            parser.error("marketplace export takes no component selection")
        archive = output.with_suffix(".tar")
        source.preparation._archive(checkout, args.commit, archive, deadline=deadline)
        output.mkdir()
        source.preparation._extract_archive(archive, output, deadline=deadline)
    elif args.component:
        import application_footprint

        components = tuple(args.component)
        if args.frozen_seeds:
            snapshots = application_footprint.prepare_frozen(
                checkout, args.commit, args.frozen_seeds[0], args.frozen_seeds[1], output,
                components=components, uv=uv, deadline=deadline, extras=extras,
            )
        else:
            output.mkdir()
            snapshots = {name: str(prepare_snapshot(checkout, args.commit, args.repository,
                             output / name, Path(uv), deadline, component=name,
                             extras=extras.get(name, ()))) for name in components}
        print(application_footprint.export({k: Path(v) for k, v in snapshots.items()},
                                          output / "footprint", deadline=deadline))
    elif args.frozen_seeds:
        parser.error("frozen preparation requires an explicit component roster")
    else:
        print(prepare(checkout, args.commit, args.repository, output, Path(uv), deadline))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
