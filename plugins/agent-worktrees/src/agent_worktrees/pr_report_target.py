"""Exact native report selection; called inside the enclosing owned worker."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


def select_report_target(config_path: Path, repository: str) -> dict:
    """Resolve the actual explicit configuration and strict configured account."""
    from . import config, repos
    from .config_acquisition import capture_config_observations
    from dropin_registry import ScanAuthority
    from .pr_read_context import read_explicit_config
    from .pr_related_binding import resolve_related_binding
    from .repos_configuration_sources import identity

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("invalid native report repository")
    read_explicit_config(config_path)
    with capture_config_observations() as observations:
        selected = config.load_config(
            config_path, include_control_plane_related_pr=False, include_knowledge_preferences=False,
            strict_inrepo_config=True,
        )
    if any(
        item.authority is ScanAuthority.INDETERMINATE
        and item.stage in ("config-yaml", "config-dropins", "config-dropin-entry", "inrepo-file", "inrepo-yaml")
        for item in observations
    ):
        raise ValueError("native report configuration acquisition is unavailable")
    target, name, error = resolve_related_binding(
        selected, repository, include_control_plane=False,
    )
    if error or target is None:
        raise ValueError("native report target is unavailable or ambiguous")
    pr = target.pr
    if pr.provider != "github" or pr.token_command or pr.token_env:
        raise ValueError("native report credential selection is unsupported")
    endpoint = pr.api_base or "https://api.github.com"
    parsed = urlsplit(endpoint if "://" in endpoint else "https://" + endpoint)
    if (
        parsed.scheme != "https" or parsed.hostname not in ("github.com", "api.github.com")
        or parsed.port not in (None, 443) or parsed.path not in ("", "/")
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or any(character.isspace() for character in endpoint)
    ):
        raise ValueError("native report endpoint is unsupported")
    registry = repos.read_registry(strict=True)
    matches = [
        entry for entry in registry.repos.values()
        if (repos.github_slug(entry.remote) or "").casefold() == repository.casefold()
    ]
    owners = [owner.casefold() for owner in registry.account_map]
    if len(matches) > 1 or len(owners) != len(set(owners)):
        raise ValueError("native report account selection is ambiguous")
    account = (
        repos.resolve_account(matches[0], registry=registry, configured_only=True)
        if matches else repos.account_for_github_owner(
            repository.split("/", 1)[0], registry=registry, configured_only=True,
        )
    )
    if not account or identity(account) != account:
        raise ValueError("native report needs an explicitly configured account")
    return {
        "target": name, "provider": "github", "endpoint": "github.com",
        "account": account, "repo": repository,
    }


def worker_main():
    """Run only native report selection, never an externally chosen operation."""
    try:
        if len(sys.argv) != 3 or not Path(sys.argv[1]).is_absolute():
            raise ValueError
        result = select_report_target(Path(sys.argv[1]), sys.argv[2])
    except Exception:
        raise SystemExit(1) from None
    print(json.dumps(result, allow_nan=False))
