"""Provider source-repository/ref observations used by recovery publication."""

from __future__ import annotations

import json
import re
from urllib.parse import quote, unquote, urlsplit

from .base import ProviderError


def same_repository(source_identity: str, destination_identity: str, provider: str) -> bool:
    """Match Azure's secure SSH/HTTPS layouts without weakening other identities."""
    def canonical(identity: str) -> str:
        if provider == "azure-devops":
            identity = "/".join(
                quote(unquote(component, errors="strict").lower(), safe="")
                for component in identity.split("/")
            )
            ssh = re.fullmatch(r"ssh\.dev\.azure\.com/v3/([^/]+)/([^/]+)/([^/]+)", identity)
            if ssh:
                organization, project, repository = ssh.groups()
                return f"dev.azure.com/{organization}/{project}/_git/{repository}"
            legacy = re.fullmatch(
                r"([^./:]+)\.visualstudio\.com/(?:DefaultCollection/)?([^/]+)/_git/([^/]+)",
                identity, re.IGNORECASE,
            )
            if legacy:
                organization, project, repository = legacy.groups()
                return f"dev.azure.com/{organization}/{project}/_git/{repository}"
        return identity

    return bool(source_identity and destination_identity) and (
        canonical(source_identity) == canonical(destination_identity)
    )


def pull_payload(body: str) -> dict:
    """Decode a provider response without accepting a non-object payload."""
    try:
        data = json.loads(body)
    except (ValueError, TypeError) as exc:
        raise ProviderError("Malformed pull-request response: expected JSON object.") from exc
    if not isinstance(data, dict):
        raise ProviderError("Malformed pull-request response: expected JSON object.")
    return data


def source_text(data: dict, field: str) -> str:
    """Return a nullable source field, rejecting non-string identity values."""
    value = data.get(field)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ProviderError(f"Malformed pull-request source field: {field} must be a string.")
    return value


def source_object(data: dict, field: str) -> dict:
    value = data.get(field)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProviderError(f"Malformed pull-request source field: {field} must be an object.")
    return value


def github_source(data: dict) -> dict[str, str]:
    repository = source_object(data, "headRepository")
    name = source_text(repository, "nameWithOwner")
    try:
        url = urlsplit(source_text(data, "url"))
        host = url.netloc if url.hostname and not url.username and not url.password else ""
    except ValueError as exc:
        raise ProviderError("Malformed pull-request source URL.") from exc
    # Enterprise Managed User logins contain an underscore enterprise shortcode.
    if name and (
        not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?/[A-Za-z0-9_.-]+", name)
        or name.rsplit("/", 1)[-1] in {".", ".."}
    ):
        raise ProviderError("Malformed pull-request source repository name.")
    return {
        "head_ref": source_text(data, "headRefName"),
        "head_url": f"{url.scheme}://{host}/{name}.git"
        if url.scheme in {"http", "https"} and host and name else "",
    }


def gitea_source(data: dict) -> dict[str, str]:
    head = source_object(data, "head")
    return {
        "head_sha": source_text(head, "sha"),
        "head_ref": source_text(head, "ref"),
        "head_url": source_text(source_object(head, "repo"), "clone_url"),
    }


def azure_source(data: dict) -> dict[str, str]:
    fork = data.get("forkSource")
    repository = (
        source_object(source_object(data, "forkSource"), "repository")
        if fork is not None else source_object(data, "repository")
    )
    return {
        "head_ref": source_text(data, "sourceRefName").removeprefix("refs/heads/"),
        "head_url": source_text(repository, "remoteUrl"),
    }
