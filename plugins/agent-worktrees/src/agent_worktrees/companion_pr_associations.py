"""Bounded, on-demand canonical PR associations for an observing Companion."""

from __future__ import annotations

import re
import stat
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from . import config as cfg
from . import output, pr_config, providers, tracking
from .config_cache import ConfigCacheSession
from .inrepo_config_source import resolution_budget
from .strict_yaml import load_strict_yaml_document

CONTRACT = "companion-pr-observations-v1"
VERSION = 1
MAX_ASSOCIATIONS = 24
MAX_BYTES = 1024 * 1024
TEXT_LIMIT = 4096
_STATES = {"", "creating", "open", "active", "merged", "closed", "abandoned"}
_PROVIDERS = {"github", "gitea", "azure-devops"}


def declaration() -> dict:
    """Describe this executing CLI's optional fact and existing passive read."""
    return {
        "contract": CONTRACT,
        "version": VERSION,
        "option": "--pr-associations",
        "snapshot": {"schema": "agent-worktrees.pr-snapshot", "version": 1},
    }


def _text(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= TEXT_LIMIT
        and not any(ord(character) < 32 for character in value)
    )


def _selection(record: tracking.WorktreeRecord) -> tuple:
    return record.worktree_id, record.repo, record.worktree_path, record.creation_nonce


def _signature(path: Path) -> tuple:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("nonregular-record")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _read(path: Path, selected: tracking.WorktreeRecord) -> tuple[dict, tuple]:
    before = _signature(path)
    if before[2] > MAX_BYTES:
        raise ValueError("record-byte-budget")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("record-byte-budget")
    if _signature(path) != before:
        raise ValueError("record-changed")
    data = load_strict_yaml_document(raw.decode("utf-8"), label="PR association record")
    if not isinstance(data, dict):
        raise ValueError("invalid-record")
    if (
        data.get("worktree_id"),
        data.get("repo"),
        data.get("worktree_path"),
        data.get("creation_nonce", ""),
    ) != _selection(selected) or selected.worktree_id != path.stem:
        raise ValueError("record-selection-changed")
    return data, before


def _url_matches(url: str, provider: str, endpoint: str, repo: str, number: int) -> bool:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return False
    if provider == "github":
        authority = parsed.netloc.lower()
        return (
            authority == endpoint
            and parsed.path.strip("/").lower() == f"{repo}/pull/{number}".lower()
        )
    base = urlsplit(endpoint)
    prefix = base.path.rstrip("/")
    if provider == "gitea":
        return (
            parsed.scheme == base.scheme
            and parsed.netloc.lower() == base.netloc.lower()
            and parsed.path.rstrip("/").lower() == f"{prefix}/{repo}/pulls/{number}".lower()
        )
    project, repository = repo.split("/")
    return (
        parsed.scheme == base.scheme
        and parsed.netloc.lower() == base.netloc.lower()
        and parsed.path.rstrip("/").lower()
        == f"{prefix}/{project}/_git/{repository}/pullrequest/{number}".lower()
    )


def _binding(config, entry: dict) -> dict:
    if (
        not re.fullmatch(r"[0-9a-f]{32}", entry["pr_id"])
        or type(entry["pr_revision"]) is not int
        or not 1 <= entry["pr_revision"] <= tracking.MAX_PERSISTED_COUNTER
    ):
        return {"available": False, "reason": "legacy-association-identity"}
    if (
        entry["provider"] not in _PROVIDERS
        or not entry["repo"]
        or not entry["url"]
        or type(entry["number"]) is not int
        or entry["number"] <= 0
        or entry["recorded_state"] not in _STATES
    ):
        return {"available": False, "reason": "incomplete-association"}
    from .pr_related_binding import parse_repository

    parse_repository(entry["repo"])
    resolution = pr_config.resolve_repo_config_for_slug(
        config,
        entry["repo"],
        allow_git=False,
    )
    if not resolution.resolved:
        return {"available": False, "reason": "target-binding-unavailable"}
    pr = resolution.repo_config.pr
    if pr.provider != entry["provider"]:
        return {"available": False, "reason": "provider-mismatch"}
    if pr.repository and pr.repository != entry["repo"]:
        return {"available": False, "reason": "repository-mismatch"}
    if not isinstance(pr.api_base, str) or len(pr.api_base) > TEXT_LIMIT:
        return {"available": False, "reason": "endpoint-unavailable"}
    api_base = pr.api_base.strip()
    if not _text(api_base) or (pr.provider != "github" and not api_base):
        return {"available": False, "reason": "endpoint-unavailable"}
    configured = urlsplit(
        api_base if "://" in api_base else f"//{api_base}",
    )
    if configured.username or configured.password or configured.query or configured.fragment:
        return {"available": False, "reason": "endpoint-unavailable"}
    endpoint = providers.get_provider(pr.provider).authority_endpoint(api_base)
    if not _text(endpoint) or not endpoint:
        return {"available": False, "reason": "endpoint-unavailable"}
    if not _url_matches(entry["url"], pr.provider, endpoint, entry["repo"], entry["number"]):
        return {"available": False, "reason": "url-binding-mismatch"}
    return {
        "available": True,
        "provider": pr.provider,
        "repo": entry["repo"],
        "api_base": pr.api_base,
        "endpoint": endpoint,
    }


def _project(entries: list) -> tuple[list[dict], int]:
    identities = Counter(
        entry["pr_id"]
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("pr_id"), str)
    )
    result = []
    for raw in entries[:MAX_ASSOCIATIONS]:
        if not isinstance(raw, dict):
            raise ValueError("invalid-association-ledger")
        entry = {
            "pr_id": raw.get("pr_id", ""),
            "pr_revision": raw.get("pr_revision", 0),
            "provider": raw.get("provider", ""),
            "repo": raw.get("repo", ""),
            "number": raw.get("number"),
            "url": raw.get("url", ""),
            "recorded_state": raw.get("state", ""),
        }
        if (
            any(
                not _text(entry[key])
                for key in (
                    "pr_id",
                    "provider",
                    "repo",
                    "url",
                    "recorded_state",
                )
            )
            or type(entry["pr_revision"]) is not int
            or (entry["number"] is not None and type(entry["number"]) is not int)
        ):
            raise ValueError("invalid-association-fields")
        if identities[entry["pr_id"]] > 1:
            entry["binding"] = {"available": False, "reason": "duplicate-association-id"}
        result.append(entry)
    return result, len(entries) - len(result)


def extend_bundle(
    bundle: dict, project: str, path: Path, selected: tracking.WorktreeRecord
) -> dict:
    """Add a current association fact without recomputing or modifying cached facts."""
    if any(not _text(field) for field in (project, selected.worktree_id, selected.creation_nonce)):
        return {
            "companion_pr_observations": declaration(),
            "availability": {"status": "unavailable", "reason": "association-metadata-overflow"},
        }
    value = {
        "schema": "agent-worktrees.pr-associations",
        "version": VERSION,
        "project": project,
        "worktree_id": selected.worktree_id,
        "creation_nonce": selected.creation_nonce,
        "available": False,
        "complete": False,
        "associations": [],
        "omitted": 0,
    }
    try:
        if bundle.get("project") != project or bundle.get("worktree_id") != selected.worktree_id:
            raise ValueError("bundle-selection-mismatch")
        data, signature = _read(path, selected)
        if not selected.creation_nonce:
            raise ValueError("legacy-worktree-incarnation")
        raw_entries = data.get("prs", [data["pr"]] if "pr" in data else [])
        if not isinstance(raw_entries, list):
            raise ValueError("invalid-association-ledger")
        entries, omitted = _project(raw_entries)
        if entries:
            with ConfigCacheSession().scope(), resolution_budget(reuse_snapshots=True):
                try:
                    config = cfg.load_config(
                        path=cfg.project_dir(project) / "config.yaml", project=project
                    )
                except Exception:
                    config = None
                for entry in entries:
                    if "binding" not in entry:
                        try:
                            entry["binding"] = _binding(config, entry)
                        except Exception:
                            entry["binding"] = {
                                "available": False,
                                "reason": "target-binding-unavailable",
                            }
        if _signature(path) != signature:
            raise ValueError("record-changed")
        value.update(available=True, complete=omitted == 0, associations=entries, omitted=omitted)
    except Exception as exc:
        value.update(
            available=False,
            complete=False,
            associations=[],
            omitted=0,
            reason=str(exc)
            if isinstance(exc, ValueError)
            and str(exc)
            in {
                "record-byte-budget",
                "record-changed",
                "invalid-record",
                "nonregular-record",
                "record-selection-changed",
                "legacy-worktree-incarnation",
                "invalid-association-ledger",
                "invalid-association-fields",
                "bundle-selection-mismatch",
                "association-byte-budget",
            }
            else "association-read-unavailable",
        )
    observed_at = time.time()

    def response():
        return {
            **bundle,
            "facts": {
                **bundle.get("facts", {}),
                "pr_associations": {
                    "value": value,
                    "confirmed": value["available"],
                    "observed_at": observed_at,
                },
            },
        }

    result = response()
    if len(output.json_output_text(result).encode("utf-8")) > MAX_BYTES:
        value.update(
            available=False,
            complete=False,
            associations=[],
            omitted=0,
            reason="association-byte-budget",
        )
        result = response()
    if len(output.json_output_text(result).encode("utf-8")) > MAX_BYTES:
        return {
            "project": project,
            "worktree_id": selected.worktree_id,
            "companion_pr_observations": declaration(),
            "facts": {"pr_associations": result["facts"]["pr_associations"]},
            "availability": {"status": "unavailable", "reason": "association-byte-budget"},
        }
    return result
