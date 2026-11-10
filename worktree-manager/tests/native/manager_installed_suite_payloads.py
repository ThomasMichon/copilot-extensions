"""Complete payload acquisition for the existing installed lifecycle witness.

The qualification owner supplies the frozen materialized artifact. This module
does not select a release, admit an upgrade baseline, install runtimes or own
native cleanup. Its command executor must be the witness's bounded native owner.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import runpy
import tomllib

REPO = Path(__file__).resolve().parents[3]
_PROVENANCE = runpy.run_path(
    str(REPO / "tools" / "clean-room" / "lib" / "materialized_provenance.py"),
)
_PROFILE = runpy.run_path(
    str(Path(__file__).with_name("manager_installed_readiness_profile.py")),
)["required_readiness_profile"]


def selected_profile(artifact: Path, candidate_commit: str, repository_config: dict) -> dict:
    """Verify the complete frozen artifact before deriving its enabled roster."""
    if not artifact.is_absolute():
        raise ValueError("Select the absolute frozen artifact path")
    _PROVENANCE["verify"](artifact, candidate_commit)
    catalog = json.loads(
        (artifact / ".github" / "plugin" / "marketplace.json").read_text(encoding="utf-8"),
    )
    manifests = {}
    # Validate catalog paths before using them to read any manifest.
    for row in catalog["plugins"]:
        name, source = row["name"], row["source"]
        if (
            not isinstance(name, str) or not name
            or "/" in name or "\\" in name or ":" in name or name in {".", ".."}
            or source != "plugins/" + name
        ):
            raise ValueError("The frozen catalog contains an invalid plugin source")
        manifests[name] = json.loads(
            (artifact / "plugins" / name / "plugin.json").read_text(encoding="utf-8"),
        )
    manager = tomllib.loads(
        (artifact / "worktree-manager" / "pyproject.toml").read_text(encoding="utf-8"),
    )
    return _PROFILE(
        catalog, manifests, marketplace_root=str(artifact),
        manager_version=manager["project"]["version"], repository_config=repository_config,
    )


def install_payload_closure(
    artifact: Path, candidate_commit: str, *, repository_config: dict,
    copilot: Path, engine: Path, environment: dict, project: Path,
    diagnostics: Path, run,
) -> dict:
    """Acquire every declared payload with the actual CLI, then verify its bytes."""
    from manager_suite_fixture import require_venue

    require_venue()
    profile = selected_profile(artifact, candidate_commit, repository_config)
    if repository_config.get("require_application_readiness") is not True:
        raise ValueError("Apply the required-readiness profile before suite acquisition")
    if not copilot.is_absolute() or not copilot.is_file():
        raise ValueError("Select the actual native Copilot executable")
    home = Path(environment["COPILOT_HOME"])
    if not home.is_absolute() or not home.is_relative_to(Path(environment["USERPROFILE"])):
        raise ValueError("Suite acquisition requires the fixture's private Copilot home")
    settings = json.loads(
        (project / ".github" / "copilot" / "settings.json").read_text(encoding="utf-8"),
    )
    if settings != profile["settings"]:
        raise ValueError("The representative harness does not enable the complete frozen suite")
    executable_hash = hashlib.sha256(copilot.read_bytes()).hexdigest()
    marketplace = next(iter(profile["settings"]["extraKnownMarketplaces"]))
    names = sorted(profile["inventory"])
    for name in names:
        run(
            [copilot, "plugin", "install", name + "@" + marketplace],
            environment, project, diagnostics, "suite-payload-" + name, timeout=120,
        )
    observed = json.loads(run(
        [
            engine, "-I", "-B", "-c",
            "import json;from agent_worktrees.reconcile import core_installed_payload_dir;"
            "print(json.dumps({name:str(core_installed_payload_dir(name) or '') "
            "for name in json.loads(__import__('sys').argv[1])}))",
            json.dumps(names),
        ],
        environment, project, diagnostics, "suite-selected-payloads", timeout=30,
    ))
    if not isinstance(observed, dict) or set(observed) != set(names):
        raise ValueError("Installed discovery returned an incomplete suite")
    closure = {}
    expected_tree = _PROVENANCE["content_manifest"](artifact / "plugins")
    installed_trees = {}
    for name in names:
        payload = Path(observed[name])
        if (
            payload.name != name or not payload.is_absolute()
            or not payload.resolve().is_relative_to(home.resolve())
        ):
            raise ValueError(f"{name}: selected payload is not installed in the private home")
        if payload.parent not in installed_trees:
            installed_trees[payload.parent] = _PROVENANCE["content_manifest"](payload.parent)
        prefix = name + "/"
        expected = {
            key[len(prefix):]: value for key, value in expected_tree.items()
            if key.startswith(prefix)
        }
        actual = {
            key[len(prefix):]: value for key, value in installed_trees[payload.parent].items()
            if key.startswith(prefix)
        }
        if actual != expected:
            raise ValueError(f"{name}: installed payload differs from the frozen candidate")
        closure[name] = {
            "path": str(payload), "version": profile["inventory"][name]["version"],
            "manifest_sha256": hashlib.sha256(
                json.dumps(actual, sort_keys=True).encode("utf-8"),
            ).hexdigest(),
        }
    _PROVENANCE["verify"](artifact, candidate_commit)
    if hashlib.sha256(copilot.read_bytes()).hexdigest() != executable_hash:
        raise ValueError("The native Copilot executable changed during acquisition")
    return {
        "candidate_commit": candidate_commit, "profile": profile,
        "copilot_sha256": executable_hash, "payloads": closure,
        "runtime_installation": "not-observed",
    }
