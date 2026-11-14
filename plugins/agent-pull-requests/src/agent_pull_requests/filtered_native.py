"""Selected population reads through the existing finite native-process owner."""

import json
import os
from pathlib import Path
import tempfile

from agent_procutil.owned_process import OwnedCleanupError
from pr_filters import Expression, PROTOCOL as FILTER_PROTOCOL
from pr_filters.acquisition import plan

from .filtered_state import facts as validate_facts
from .repository_native import (
    RepositoryNativeUnavailable, RepositoryNativeUnsupported, _invoke,
)
from .watch_replay_store import json_object

PROTOCOL = "agent-worktrees.pr-population/v1"


def qualify(binding, *, checkpoint):
    identity = binding.repository_identity
    result = _invoke(
        [*(binding.prefix or (binding.command,)), "pr-watch", "population", "--contract", "--json"],
        timeout=15, checkpoint=checkpoint,
    )
    try:
        value = json_object(result.stdout.encode())
        support = value["providers"][identity.provider]
        if result.returncode or value["protocol"] != PROTOCOL or value["filter_protocol"] != FILTER_PROTOCOL or (
            value["credential"] != "observe/per-request" or value["effects"] != "read-only"
            or value.get("query_rotation") != "offset/v1"
        ) or any(type(value[name]) is not int or not lower <= value[name] <= upper for name, lower, upper in (
            ("max_pages", 4, 20), ("max_pulls", 200, 1000), ("max_requests", 1, 64),
            ("max_seconds", 60, 60), ("max_capture_bytes", 1, 1_048_584),
            ("max_json_bytes", 1, 1_048_576),
        )):
            raise ValueError
        selected = Expression.parse({"protocol": FILTER_PROTOCOL, "where": {"repo": [identity.repo]}})
        selected.require(support, {identity.repo})
        plan(selected, support.get("acquisition", {}))
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
        raise RepositoryNativeUnsupported("selected native population contract unsupported") from None
    checkpoint()
    return support


def population(binding, expression, *, root, checkpoint, start_checkpoint=None, query_offset=0):
    """Keep immutable input until the actual native reader and cleanup settle."""
    start_checkpoint = checkpoint if start_checkpoint is None else start_checkpoint
    support = qualify(binding, checkpoint=start_checkpoint)
    identity = binding.repository_identity
    expression.require(support, {identity.repo})
    plan(expression, support["acquisition"])
    descriptor, filename = tempfile.mkstemp(prefix=".population-", suffix=".json", dir=root)
    retained = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(expression.canonical.encode("utf-8"))
        start_checkpoint()
        result = _invoke([
            *(binding.prefix or (binding.command,)), "pr-watch", "population", identity.repo,
            "--config", binding.inventory_config,
            "--credential-command", json.dumps(binding.credential_command),
            "--filter-file", filename,
            "--query-offset", str(query_offset),
            "--max-pages", "4", "--max-pulls", "200", "--seconds", "60", "--json",
        ], timeout=60, checkpoint=checkpoint, qualified=True)
    except OwnedCleanupError:
        retained = True
        raise
    finally:
        if not retained:
            Path(filename).unlink()
    try:
        value = json_object(result.stdout.encode())
        if result.returncode or set(value) != {
            "schema", "version", "provider", "api_base", "repo", "filter_revision",
            "outcome", "pages", "max_pages", "max_pulls", "error", "pulls",
        } or value["schema"] != "agent-worktrees.pr-population" or (
            type(value["version"]) is not int or value["version"] != 1
        ) or (value["provider"], value["api_base"], value["repo"], value["filter_revision"]) != (
            identity.provider, identity.endpoint, identity.repo, expression.revision,
        ) or value["outcome"] not in ("complete", "partial", "unavailable") or (
            type(value["pages"]) is not int or not 0 <= value["pages"] <= 4
            or type(value["max_pages"]) is not int or value["max_pages"] != 4
            or type(value["max_pulls"]) is not int or value["max_pulls"] != 200
            or not isinstance(value["error"], str) or len(value["error"]) > 128
            or not isinstance(value["pulls"], list) or len(value["pulls"]) > 200
            or value["outcome"] == "unavailable" and value["pulls"]
            or value["outcome"] == "complete" and (value["pages"] < 1 or value["error"])
            or value["outcome"] != "complete" and not value["error"]
        ):
            raise ValueError
        numbers = set()
        for pull in value["pulls"]:
            if not isinstance(pull, dict) or set(pull) != {"number", "facts", "match"} or (
                type(pull["number"]) is not int or pull["number"] < 1 or pull["number"] in numbers
            ) or pull["match"] not in ("match", "nonmatch", "unknown"):
                raise ValueError
            numbers.add(pull["number"])
            validate_facts(pull["facts"], identity)
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
        raise RepositoryNativeUnavailable(qualified=True) from None
    checkpoint()
    return value
