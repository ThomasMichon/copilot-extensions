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


def prepare(checkout: Path, commit: str, repository: str, output: Path, uv: Path, deadline: float) -> Path:
    output.mkdir(parents=True, exist_ok=False)
    release = output / "release"
    release.mkdir()
    discovered = discovery.discover_promoted_source(
        release, repository=repository, repo=checkout, main_commit=commit, deadline=deadline,
    )
    if discovered is None:
        raise ValueError("Selected commit has no published release index")
    index, assets = discovered
    component = "worktree-manager"
    key = "standalone:" + component
    inputs = output / "inputs"
    record = source.compose(checkout, commit, None, inputs, projects=(key,),
                            closed_wheel_source=True, deadline=deadline)
    inputs = Path(source.native_retained_path(inputs))
    tree = inputs / "source"
    entry, = (e for e in index["artifacts"] if e.get("component") == component)
    binding = discovery.read_selected_source_artifact(
        release, index=index, assets=assets, repository=repository, component="standalone-" + component,
        version=entry["version"], candidate_root=tree,
    )
    if binding is None:
        raise ValueError("Selected release has no standalone Manager artifact")
    manifest, _ = binding
    _, _, payloads, identities = source._source_projects(tree, record, (key,))
    if identities[key].package_version != manifest["version"]:
        raise ValueError("Manager version differs from its selected source")
    row, = manifest["wheels"]
    pool = inputs / "wheels" / component
    pool.mkdir(parents=True)
    with zipfile.ZipFile(release / entry["filename"]) as archive:
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
                                           prepare_profile=True)
    snapshot = Path(result["snapshot"])
    return application_footprint.export({component: snapshot}, output / "footprint", deadline=deadline)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    uv = shutil.which("uv")
    if not uv:
        parser.error("Manager preparation requires uv")
    print(prepare(args.checkout.resolve(strict=True), args.commit, args.repository,
                  args.output.absolute(), Path(uv), time.monotonic() + 600))


if __name__ == "__main__":
    main()
