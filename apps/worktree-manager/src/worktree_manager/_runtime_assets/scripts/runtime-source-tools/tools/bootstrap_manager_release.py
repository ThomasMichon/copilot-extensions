"""Prepare the first Manager using an independently selected promoted commit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import time
import zipfile

import application_footprint
import artifact_release_discovery as discovery
import local_install_profile as profiles
import local_install_source as source


def prepare_snapshot(checkout: Path, commit: str, repository: str, output: Path, uv: Path, deadline: float,
                     *, component: str, extras: tuple[str, ...] = ()) -> Path:
    output.mkdir(parents=True, exist_ok=False)
    release = output / "release"
    release.mkdir()
    discovered = discovery.discover_promoted_source(
        release, repository=repository, repo=checkout, main_commit=commit, deadline=deadline,
    )
    if discovered is None:
        raise ValueError("Selected commit has no published release index")
    index, assets = discovered
    entry, = (e for e in index["artifacts"] if e.get("component", e.get("plugin")) == component)
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
    )
    if binding is None:
        raise ValueError("Selected release has no standalone Manager artifact")
    manifest, _ = binding
    _, _, payloads, identities = source._source_projects(tree, record, (key,))
    if identities[key].package_version != manifest["version"]:
        raise ValueError("Manager version differs from its selected source")
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


def prepare(checkout: Path, commit: str, repository: str, output: Path, uv: Path, deadline: float) -> Path:
    snapshot = prepare_snapshot(checkout, commit, repository, output, uv, deadline, component="worktree-manager")
    return application_footprint.export({"worktree-manager": snapshot}, output / "footprint", deadline=deadline)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--marketplace", action="store_true")
    parser.add_argument("--component", action="append")
    parser.add_argument("--index-host", action="store_true")
    parser.add_argument("--frozen-seeds", nargs=2, metavar=("MAIN", "DEV"))
    args = parser.parse_args()
    if args.index_host and "agent-index" not in (args.component or []):
        parser.error("--index-host requires agent-index")
    extras: dict[str, tuple[str, ...]] = {"agent-index": ("server", "store")} if args.index_host else {}
    uv = shutil.which("uv")
    if not uv:
        parser.error("Manager preparation requires uv")
    checkout, output = args.checkout.resolve(strict=True), args.output.absolute()
    deadline = time.monotonic() + 1500
    if args.marketplace:
        if args.component or args.frozen_seeds:
            parser.error("marketplace export takes no component selection")
        archive = output.with_suffix(".tar")
        source.preparation._archive(checkout, args.commit, archive, deadline=deadline)
        output.mkdir()
        source.preparation._extract_archive(archive, output, deadline=deadline)
    elif args.component:
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


if __name__ == "__main__":
    main()
