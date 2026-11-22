"""Verify knowledge-project visibility separately from Git transport access."""
from __future__ import annotations

import json
import os
import re
import subprocess
from urllib.parse import unquote, urlsplit
from uuid import UUID


def _organization(host: str, parts: list[str]) -> str:
    if host == "dev.azure.com" and parts:
        org = parts.pop(0)
    elif host.endswith(".visualstudio.com"):
        org = host.removesuffix(".visualstudio.com")
    else:
        raise ValueError("Unsupported Azure DevOps host")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]*", org):
        raise ValueError("Invalid Azure DevOps organization")
    return org.casefold()


def _location(url: str) -> tuple[str, list[str]]:
    u = urlsplit(url)
    if u.scheme != "https" or not u.hostname or u.hostname != u.netloc or u.query or u.fragment:
        raise ValueError("Expected a credential-free Azure DevOps HTTPS locator")
    parts = [unquote(p) for p in u.path.removeprefix("/").split("/")]
    org = _organization(u.hostname, parts)
    return org, parts


def require_private_project(remote: str) -> None:
    org, parts = _location(remote)
    if (len(parts) != 3 or parts[1] != "_git" or
            not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]*", p) for p in (parts[0], parts[2]))):
        raise ValueError("Expected an Azure Repos clone URL")
    project = parts[0]
    try:
        probe = subprocess.run(
            ["az", "devops", "project", "show", "--organization", f"https://dev.azure.com/{org}",
             "--project", project, "--detect", "false", "--only-show-errors", "--output", "json"],
            env={**os.environ, "AZURE_EXTENSION_USE_DYNAMIC_INSTALL": "no"},
            capture_output=True, timeout=60, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        observation = json.loads(probe.stdout)
        project_id = str(UUID(observation["id"]))
        observed_org, path = _location(observation["url"])
        if (observed_org == org and [x.casefold() for x in path] == ["_apis", "projects", project_id]
                and project.casefold() in (observation["name"].casefold(), project_id)
                and observation.get("visibility") == "private"):
            return
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        pass
    raise ValueError("Selected knowledge project could not be verified private by Azure DevOps")
