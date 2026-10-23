"""Write machine-local per-project configuration."""

from pathlib import Path

import yaml

from . import output, tracking


def write_project_config(
    path: Path,
    repo_dir: Path,
    machine: str,
    plat: str,
    project: str,
    default_branch: str = "master",
    *,
    headless: bool = False,
    no_terminal_profile: bool = False,
) -> None:
    """Write the machine-local per-project config YAML.

    Machine-wide fields live in the global config, while repository settings
    may live in the repository's own config. This file keeps the project marker
    and machine paths. ``default_branch`` remains accepted for compatibility
    but is not persisted.
    """
    del machine, plat, default_branch

    if path.exists():
        existing = yaml.safe_load(path.read_text(encoding="utf-8"))
        if existing is None:
            existing = {}
        if not isinstance(existing, dict):
            raise ValueError(f"Expected a YAML mapping in project config: {path}")

        existing = dict(existing)
        repos_value = existing.get("repos", {})
        if not isinstance(repos_value, dict):
            raise ValueError(f"Expected a repos mapping in project config: {path}")
        repos = dict(repos_value)
        existing["repos"] = repos

        repo_value = repos.get(project, {})
        if not isinstance(repo_value, dict):
            raise ValueError(f"Expected a repos.{project} mapping in project config: {path}")
        repo = dict(repo_value)
        repos[project] = repo

        existing["repo_name"] = project
        repo["anchor"] = str(repo_dir)
        repo.setdefault("remote", "origin")
        if headless:
            existing["headless"] = True
        if no_terminal_profile:
            existing["terminal_profiles"] = []
        tracking._atomic_write(path, yaml.safe_dump(existing, sort_keys=False))
        output.changed(f"Updated config (preserved existing settings): {path}")
        return

    wt_root = f"{repo_dir}.worktrees"
    headless_line = "headless: true\n" if headless else ""
    terminal_block = (
        "\n# No Windows Terminal profile for this project (--no-agent adoption):\n"
        "# an empty selection suppresses generation (absent applies the default).\n"
        "terminal_profiles: []\n"
        if no_terminal_profile
        else ""
    )
    content = f"""# ~/.{project}/config.yaml
# Machine-local config for {project} (overrides + machine paths only).
# Machine-wide defaults -> ~/.agent-worktrees/config.yaml.
# Repo settings may live in-repo -> <anchor>/.agent-worktrees/config.yaml.

repo_name: {project}
{headless_line}{terminal_block}
repos:
  {project}:
    anchor: {repo_dir}
    # worktree_root defaults to {wt_root} -- a sibling
    # <anchor>.worktrees dir, matching Copilot CLI's /worktree layout.
    # Uncomment and set an absolute path to override.
    remote: origin
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    output.changed(f"Written config: {path}")
