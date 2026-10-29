"""Source guard for installed-footprint adopters, not an installer or updater."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
from types import ModuleType


ADOPTERS = ("agent-mcp",)


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "--no-pager", "-C", str(repo), *arguments],
        capture_output=True, text=True, encoding="utf-8", timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
    )
    if result.returncode:
        raise ValueError(f"Cannot verify footprint history baseline: {result.stderr.strip()}")
    return result.stdout


def _baseline(repo: Path, relative: str) -> str | None:
    branch = os.environ.get("GITHUB_BASE_REF")
    if branch:
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", branch) or ".." in branch:
            raise ValueError("Invalid footprint history base branch")
        ref = f"refs/remotes/origin/{branch}"
    elif os.environ.get("GITHUB_EVENT_NAME") == "push":
        ref = "HEAD^"
    else:
        ref = _git(repo, "merge-base", "HEAD", "refs/remotes/origin/dev").strip()
    _git(repo, "rev-parse", "--verify", ref)
    if not _git(repo, "ls-tree", "-z", ref, "--", relative):
        return None
    return _git(repo, "show", f"{ref}:{relative}")


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Footprint guard helper unavailable: {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def check(repo: Path) -> list[str]:
    adopters = tuple(plugin for plugin in ADOPTERS if (repo / "plugins" / plugin).is_dir())
    if not adopters:
        return []
    contract = _load(repo / "libs/installer-engine/installer_engine_footprint.py", "_footprint_guard")
    inventory = _load(repo / "libs/installer-engine/installer_engine_uninstall.py", "_footprint_profile_guard")
    problems = []
    for plugin in adopters:
        payload = repo / "plugins" / plugin
        path = payload / "scripts/uninstall-profile.json"
        try:
            inventory._plain(path, file=True)
            manifest = contract.parse(contract.decode(path.read_text(encoding="utf-8-sig")), plugin)
            if set(manifest.producers) != {"scripts", "src"}:
                raise ValueError("MCP producer coverage must include the complete scripts and src trees")
            inventory.profile_for(payload, plugin, path)
            previous = _baseline(repo, path.relative_to(repo).as_posix())
            if previous is not None:
                contract.require_append_only(
                    contract.decode(previous), contract.decode(path.read_text(encoding="utf-8-sig")), plugin,
                )
            problems.extend(f"{plugin}: {problem}" for problem in contract.producer_problems(manifest, payload))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            problems.append(f"{plugin}: installed footprint invalid ({error})")
    return problems
