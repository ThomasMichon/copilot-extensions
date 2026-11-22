"""Resolve harness and incoming release before handing off to the staged Manager."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

KIT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(KIT / "worktree-manager" / "src"), str(KIT / "tools"),
                str(KIT / "libs" / "agent-procutil" / "src")]

from worktree_manager import bootstrap_harness as harness
from worktree_manager import bootstrap_pairing
from worktree_manager import registered_suite, runtime_source_repository, source_config


def require_onboarding(python: Path, env: dict[str, str]) -> None:
    probe = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import importlib.util,sys; sys.exit(importlib.util.find_spec("
         "'worktree_manager.harness_onboarding') is None)"],
        env=env, timeout=30, check=False,
    )
    if probe.returncode:
        raise RuntimeError("Selected Manager does not provide harness onboarding; no app slots staged")


def main() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--srcroot", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--repo")
    parser.add_argument("--knowledge-repo")
    parser.add_argument("--stage", action="store_true")
    args = parser.parse_args()
    root = args.root.absolute()
    anchor = None
    if args.repo:
        anchor = harness.first_registration(root, args.srcroot.absolute(), args.repo)
    elif (root / "registry").exists():
        default, anchors = registered_suite.registrations(root)
        anchor = anchors[default]
    knowledge_url = ""
    if anchor is not None:
        default, _ = registered_suite.registrations(root)
        remote = (harness._git(anchor, "remote", "get-url", "origin")
                  if "origin" in harness._git(anchor, "remote").splitlines() else "")
        drive = os.environ.get("OneDrive")
        kind, value = bootstrap_pairing.select(
            root / "repos" / default / "config.yaml", remote, args.knowledge_repo,
            Path(drive) if drive else None,
        )
        if kind in {"explicit", "portable"} and value:
            knowledge_url = value
    if knowledge_url:
        if not knowledge_url.startswith("https://github.com/"):
            sys.path.insert(0, str(KIT / "plugins/harness-knowledge/skills/binding-knowledge/scripts"))
            from azure_project_visibility import require_private_project
            require_private_project(knowledge_url)
    if knowledge_url.startswith("https://github.com/"):
        locator = knowledge_url.removeprefix("https://github.com/")
        probe = subprocess.run(
            ["gh", "repo", "view", locator, "--json", "nameWithOwner,isPrivate"],
            env={**os.environ, "GH_PROMPT_DISABLED": "1"}, capture_output=True,
            text=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if probe.returncode:
            login = subprocess.run(
                ["gh", "api", "--hostname", "github.com", "user", "--jq", ".login"],
                env={**os.environ, "GH_PROMPT_DISABLED": "1"}, capture_output=True,
                timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if login.returncode or not login.stdout.strip():
                raise ValueError("GitHub CLI could not verify a signed-in identity on the target; "
                                 "check gh auth status --hostname github.com in the target terminal")
            raise ValueError("Target GitHub CLI identity is authenticated, but could not read "
                             "the selected private knowledge repository; verify repository access and SSO")
        observed = json.loads(probe.stdout)
        if observed["nameWithOwner"].casefold() != locator.casefold() or observed["isPrivate"] is not True:
            raise ValueError("Selected knowledge repository must resolve to the requested private repository")
    selection = runtime_source_repository.read(anchor, default_repository=source_config.DEFAULT_REPO)
    _, url = harness._github(selection["repository"])
    reference = selection["reference"]
    if reference.startswith("-") or ":" in reference or reference.startswith("+"):
        raise ValueError("Invalid source reference")
    checkout = args.workspace / "source"
    harness._git(args.workspace, "clone", "--no-checkout", "--no-recurse-submodules", "--", url, str(checkout))
    if reference == "main":
        reference = "refs/remotes/origin/main"
    commit = harness._git(checkout, "rev-parse", "--verify", "--end-of-options", reference + "^{commit}")
    print(f"Selected {url} {reference} at {commit}", flush=True)

    import bootstrap_manager_release
    from source_preparation_process import procutil_module

    procutil_module("owned_process")
    uv = shutil.which("uv")
    if not uv:
        raise ValueError("uv is required")
    footprint = bootstrap_manager_release.prepare(
        checkout, commit, url.removeprefix("https://github.com/"),
        args.workspace / "prepared", Path(uv), time.monotonic() + 1200,
    )
    # Keep the executor outside the copyable input. Its dependency source is
    # the same verified local wheel set the incoming application will use.
    executor = args.workspace / "executor"
    shutil.copytree(footprint / "apps" / "worktree-manager", executor)
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in {"GH_TOKEN", "GITHUB_TOKEN", "ARTIFACT_VERIFY_TOKEN"}}
    env.update(UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never")
    subprocess.run([uv, "sync", "--project", str(executor), "--locked", "--no-dev", "--no-build",
                    "--no-editable", "--python", sys.executable], env=env, check=True, timeout=180)
    python = executor / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if anchor is not None:
        # Inspect the incoming wheel, not the bootstrap checkout. Do not start a
        # partial installation if its Manager cannot perform the next phase.
        require_onboarding(python, env)
    command = [str(python), "-I", "-B", "-m", "worktree_manager.application_footprints",
               str(footprint), "--apps-root", str(root / "apps"), "--apply"]
    if args.stage or anchor is not None:
        command.append("--stage")
    subprocess.run(command, env=env, check=True, timeout=1800)
    if anchor is not None:
        (app,) = json.loads((footprint / "footprint.json").read_bytes())["apps"]
        installed = root / "apps" / "worktree-manager" / "versions" / app["version"] / ".venv"
        interpreter = installed / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        handoff = [str(interpreter), "-I", "-B", "-m", "worktree_manager.harness_onboarding",
                   "--root", str(root), "--srcroot", str(args.srcroot), "--source-checkout",
                   str(checkout), "--source-commit", commit]
        if knowledge_url:
            name, remote = harness.repository_remote(knowledge_url)
            knowledge = harness.acquire_checkout(args.srcroot.absolute(), args.srcroot.absolute() / name, remote)
            handoff.extend(["--knowledge-repo", remote, "--knowledge-checkout", str(knowledge)])
        if args.stage:
            handoff.append("--stage")
        subprocess.run(handoff, env=env, check=True, timeout=1800)


if __name__ == "__main__":
    main()
