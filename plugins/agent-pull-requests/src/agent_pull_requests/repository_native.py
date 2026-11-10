"""Configured inventory through the selected Worktrees native process boundary."""

import json

from .native_command import run_command
from .watch_replay_store import json_object


class RepositoryNativeUnsupported(ValueError):
    """Selected executable does not implement the required observation contract."""


def inventory(binding, *, checkpoint):
    checkpoint()
    identity = binding.repository_identity
    if identity is None or binding.inventory_config is None:
        raise ValueError("repository observation not selected")
    prefix = list(binding.prefix or (binding.command,))
    declaration = run_command(
        [*prefix, "pr-watch", "inventory", "--contract", "--json"], timeout=15,
    )
    try:
        support = json_object(declaration.stdout.encode())
    except (ValueError, UnicodeError):
        raise RepositoryNativeUnsupported("selected native repository observation unsupported") from None
    if (
        declaration.returncode or support.get("protocol") != "agent-worktrees.pr-inventory/v1"
        or not isinstance(support.get("providers"), list)
        or identity.provider not in support.get("providers", [])
        or support.get("credential") != "observe/per-request" or support.get("effects") != "none"
        or type(support.get("snapshot_request_budget")) is not int
        or not 1 <= support["snapshot_request_budget"] <= 64
        or type(support.get("snapshot_capture_bytes")) is not int
        or not 1 <= support["snapshot_capture_bytes"] <= 1_048_584
    ):
        raise RepositoryNativeUnsupported("selected native repository observation unsupported")
    checkpoint()
    result = run_command([
        *prefix, "pr-watch", "inventory", identity.repo,
        "--config", binding.inventory_config,
        "--credential-command", json.dumps(binding.credential_command),
        "--max-pages", "4", "--max-pulls", "200", "--seconds", "60", "--json",
    ], timeout=60)
    if result.returncode:
        raise ValueError("native repository inventory unavailable")
    value = json_object(result.stdout.encode())
    if (
        set(value) != {"schema", "version", "provider", "api_base", "repo", "outcome",
                       "numbers", "pages", "error", "max_pages", "max_pulls"}
        or value["schema"] != "agent-worktrees.pr-inventory"
        or type(value["version"]) is not int or value["version"] != 1
        or (value["provider"], value["api_base"], value["repo"]) != (
            identity.provider, identity.endpoint, identity.repo,
        )
        or value["outcome"] not in ("complete", "partial", "unavailable")
        or type(value["pages"]) is not int or not 0 <= value["pages"] <= 4
        or value["max_pages"] != 4 or value["max_pulls"] != 200
        or not isinstance(value["error"], str) or len(value["error"]) > 128
        or not isinstance(value["numbers"], list) or len(value["numbers"]) > 200
        or any(type(number) is not int or number < 1 for number in value["numbers"])
        or len(set(value["numbers"])) != len(value["numbers"])
        or value["outcome"] == "unavailable" and value["numbers"]
        or value["outcome"] == "complete" and (value["pages"] < 1 or value["error"])
        or value["outcome"] != "complete" and not value["error"]
    ):
        raise ValueError("invalid native repository inventory")
    checkpoint()
    return value
