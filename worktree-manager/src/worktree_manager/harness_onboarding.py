"""Onboard registered harnesses using the executing Manager's released tools."""
from __future__ import annotations

import argparse
from importlib import import_module
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time
from types import ModuleType

from . import bootstrap_harness as boot
from . import bootstrap_pairing, harness_state, registered_suite, runtime_identity, runtime_source_repository
from .config_write_lock import config_write_lock, retained_sidecar_lock


def _asset(directory: Path, name: str) -> ModuleType:
    directory = directory.resolve(strict=True)
    if not directory.is_relative_to(Path(__file__).resolve().parent / "_runtime_assets"):
        raise ValueError("Onboarding tools must belong to the installed Manager")
    sys.path.insert(0, str(directory))
    module = import_module(name)
    if not module.__file__ or not Path(module.__file__).samefile(directory / (name + ".py")):
        raise ValueError("Foreign onboarding resource")
    return module


def register_companion(root: Path, name: str, remote: str, checkout: Path) -> None:
    path = root / "registry" / "repos.yaml"
    runtime_source_repository._ordinary(path)
    platform_key = harness_state._platform_key()
    with config_write_lock(path):
        state = harness_state.repos_registry(strict=True, input_roots=harness_state.ProfileInputRoots(path.parent, {}))
        existing = state["repos"].get(name)
        if existing is not None:
            if (existing.get("class") not in {"knowledge", "worktree"} or existing.get("remote") != remote
                    or not Path(existing.get(platform_key, "")).samefile(checkout)):
                raise ValueError("Existing companion registration differs from the selected checkout")
            return
        state["repos"][name] = {"class": "knowledge", "remote": remote, platform_key: str(checkout)}
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as out:
            temp = Path(out.name)
            json.dump(state, out, sort_keys=True)
        try:
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def bind_companion(root: Path, project: str, remote: str, checkout: Path, writer: ModuleType) -> Path:
    config = root / "repos" / project / "config.yaml"
    runtime_source_repository._ordinary(config)
    name, remote = boot.repository_remote(remote)
    checkout = boot._anchor(checkout)
    if boot.repository_remote(boot._git(checkout, "remote", "get-url", "origin"))[1] != remote:
        raise ValueError("Knowledge origin changed before binding")

    def registered_checkout() -> Path:
        inputs = harness_state.ProfileInputRoots(root / "registry", {})
        entry = harness_state.repos_registry(strict=True, input_roots=inputs)["repos"].get(name)
        if not entry or entry.get("class") not in {"worktree", "knowledge"} or entry.get("remote") != remote:
            raise ValueError("Selected knowledge registration changed")
        path = Path(entry[harness_state._platform_key()])
        if not path.is_absolute() or not path.samefile(checkout):
            raise ValueError("Selected knowledge checkout changed")
        return path

    def check_registration() -> None:
        registered_checkout()

    register_companion(root, name, remote, checkout)
    writer.write_binding(config, project, name, preserve=True, precondition=check_registration)
    if harness_state._read_yaml(config, strict=True).get("knowledge_repo") != name:
        raise ValueError("Manager could not read back the selected knowledge binding")
    return registered_checkout()


def compose_checkout(harness: Path, companion: Path) -> None:
    overlay = import_module("._runtime_assets.scripts.runtime-source-tools.knowledge_composition.knowledge_overlay", __package__)
    output = harness.resolve() / ".github" / "copilot" / "settings.local.json"
    runtime_source_repository._ordinary(output)
    lock = overlay._overlay_lock_target(output).with_suffix(".lock")
    with retained_sidecar_lock(lock, timeout=30):
        overlay._compose_locked(harness, companion)


def install_prepared(command: list[str]) -> int:
    setup_env = {k: v for k, v in os.environ.items()
                 if k.upper() not in {"GH_TOKEN", "GITHUB_TOKEN", "ARTIFACT_VERIFY_TOKEN"}}
    setup_env.update(UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never")
    return subprocess.run(
        [sys.executable, "-I", "-B", "-m", "worktree_manager.application_footprints", *command],
        executable=sys.executable, env=setup_env, timeout=1800, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for argument in ("root", "srcroot", "source-checkout"):
        parser.add_argument("--" + argument, type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--knowledge-repo")
    parser.add_argument("--knowledge-checkout", type=Path)
    parser.add_argument("--stage", action="store_true")
    parser.add_argument("--frozen-seeds", nargs=2, metavar=("MAIN", "DEV"))
    args = parser.parse_args(argv)
    if args.frozen_seeds and not args.stage:
        parser.error("explicit frozen preparation requires --stage")
    root, commit = args.root, args.source_commit
    slot = runtime_identity.running_application_slot()
    if (slot is None or not root.is_absolute() or not re.fullmatch(r"[a-f0-9]{40}", commit)
            or not slot.parent.parent.samefile(root / "apps/worktree-manager")
            or not Path(__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
            or json.loads((slot / "runtime-source.json").read_bytes())["source_commit"] != commit):
        raise ValueError("Onboarding requires the incoming Manager at the registered suite commit")
    checkout = boot._anchor(args.source_checkout)
    origin = boot._github(boot._git(checkout, "remote", "get-url", "origin"))[1]
    if boot._git(checkout, "rev-parse", "--verify", commit + "^{commit}") != commit:
        raise ValueError("Incoming source commit unavailable")
    default, anchors = registered_suite.registrations(root)
    kit = Path(__file__).resolve().parent / "_runtime_assets/scripts/runtime-source-tools"
    writer = _asset(kit / "plugins/harness-knowledge/skills/binding-knowledge/scripts", "binding_yaml")
    deadline = time.monotonic() + 1500
    def produce(output: Path, *options: str) -> None:
        script = (kit / "tools/bootstrap_manager_release.py").resolve(strict=True)
        if not script.is_relative_to(Path(__file__).resolve().parent / "_runtime_assets"):
            raise ValueError("Producer must belong to this Manager")
        subprocess.run(
            [sys.executable, "-I", "-B", "-c",
             "import runpy,sys;from pathlib import Path;p=Path(sys.argv.pop(1));"
             "sys.path.insert(0,str(p.parent));runpy.run_path(str(p),run_name='__main__')",
             str(script), "--checkout", str(checkout), "--commit", commit, "--repository",
             origin.removeprefix("https://github.com/"), "--output", str(output), *options],
            executable=sys.executable, check=True, timeout=max(0.0, deadline - time.monotonic()),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    with tempfile.TemporaryDirectory(prefix="ce-suite-") as work:
        workspace = Path(work)
        companions: dict[str, Path] = {}
        for project, anchor in anchors.items():
            config = root / "repos" / project / "config.yaml"
            runtime_source_repository._ordinary(config)
            local = harness_state._read_yaml(config, strict=True).get("knowledge_repo")
            if local:
                rows = harness_state.repos_registry(strict=True, input_roots=harness_state.ProfileInputRoots(
                    root / "registry", {}))["repos"]
                entry = rows[local]
                remote = entry["remote"]
                knowledge = Path(entry[harness_state._platform_key()])
            elif project == default and args.knowledge_repo and args.knowledge_checkout:
                remote, knowledge = args.knowledge_repo, args.knowledge_checkout
            else:
                continue
            name, remote = boot.repository_remote(remote)
            if local and name != local:
                raise ValueError("Local binding conflicts with repository identity")
            companions[project] = bind_companion(root, project, remote, knowledge, writer)
        if companions:
            for project, companion in companions.items():
                compose_checkout(anchors[project], companion)
                if os.environ.get("OneDrive"):
                    bootstrap_pairing.remember_bound(
                        root / "repos" / project / "config.yaml",
                        boot._git(anchors[project], "remote", "get-url", "origin"),
                        boot._git(companion, "remote", "get-url", "origin"), Path(os.environ["OneDrive"]),
                        registered_knowledge=companion,
                    )

        def observe(project: str, anchor: Path) -> registered_suite.HarnessPolicy:
            companion = companions.get(project)
            dirs = (anchor, companion) if companion else (anchor,)
            gate = tuple(d / filename for d in dirs if d for filename in ("services.yaml", "external-repos.yaml"))
            target = platform.node().lower()
            fleet = companion / ".copilot-extensions/fleet-manager/config.json" if companion else None
            return registered_suite.HarnessPolicy(gate, (target,), fleet_config=fleet if fleet and fleet.exists() else None,
                                                  fleet_target=target if fleet and fleet.exists() else None)

        market = workspace / "marketplace"
        produce(market, "--marketplace")
        required = registered_suite.required_applications(anchors, market, commit, observe)
        options = [arg for component in sorted(required) for arg in ("--component", component)]
        if args.frozen_seeds:
            options.extend(["--frozen-seeds", *args.frozen_seeds])
        produce(workspace / "suite", *options)
        footprint = workspace / "suite/footprint"
        plan = registered_suite.registered_plan(
            root, resolve=lambda *_: registered_suite.PreparedSuite(commit, market, footprint), observe=observe)
        command = [str(footprint), "--apps-root", str(root / "apps"), "--apply"]
        for app in plan:
            command.extend(["--app", app.name])
        if args.stage:
            command.append("--stage")
        return install_prepared(command)


if __name__ == "__main__":
    raise SystemExit(main())
