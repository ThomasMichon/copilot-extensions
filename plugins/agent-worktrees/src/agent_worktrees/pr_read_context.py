"""One-shot reads resolve target configuration without acquiring a worktree."""

from __future__ import annotations

from pathlib import Path

import yaml


def load_read_config(path=None):
    from . import config, related, repos
    if path is not None:
        if not path.is_file():
            raise ValueError("explicit PR configuration file is unavailable")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (UnicodeError, yaml.YAMLError):
            raise ValueError("explicit PR configuration must be a valid UTF-8 mapping") from None
        if not isinstance(raw, dict) or not raw:
            raise ValueError("explicit PR configuration must be a nonempty mapping")
        return config.load_config(path)
    if config.active_project():
        return config.load_config(path)
    anchor = related.find_control_plane_anchor()
    if not anchor:
        raise ValueError("PR read needs an explicit --project or --config with a resolvable target binding")
    matches = [entry.name for entry in repos.list_repos()
               if entry.local_path() and Path(entry.local_path()).resolve() == Path(anchor).resolve()]
    if len(matches) != 1:
        raise ValueError("PR read control-plane configuration is unavailable or ambiguous; pass --project")
    return config.load_project_config(matches[0])
