"""Configured inventory through the selected Worktrees native process boundary."""

import json
import subprocess

from agent_procutil.owned_process import OwnedCleanupError

from .native_command import NativeCommandUnavailable, run_observation_command as run_command
from .watch_replay_store import ReplayCallerExpired, json_object


class RepositoryNativeUnsupported(ValueError):
    """Selected executable does not implement the required observation contract."""


class RepositoryNativeUnavailable(ValueError):
    """Settled read loss retains whether executing bounds were qualified."""

    def __init__(self, *, qualified):
        self.qualified = qualified
        super().__init__("native repository read unavailable")


def _invoke(argv, *, timeout, checkpoint, qualified=False):
    checkpoint()
    try:
        result = run_command(argv, timeout=timeout)
    except (OwnedCleanupError, ReplayCallerExpired):
        raise
    except (OSError, subprocess.SubprocessError, NativeCommandUnavailable):
        checkpoint()
        raise RepositoryNativeUnavailable(qualified=qualified) from None
    checkpoint()
    return result


def qualify(binding, *, checkpoint):
    """Confirm executing read bounds without provider or credential acquisition."""
    checkpoint()
    identity = binding.repository_identity
    if identity is None or binding.inventory_config is None:
        raise ValueError("repository observation not selected")
    prefix = list(binding.prefix or (binding.command,))
    declaration = _invoke(
        [*prefix, "pr-watch", "inventory", "--contract", "--json"],
        timeout=15, checkpoint=checkpoint,
    )
    try:
        support = json_object(declaration.stdout.encode())
    except (ValueError, UnicodeError, RecursionError):
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


def inventory(binding, *, checkpoint, _qualified=False):
    """Perform selected bounded inventory after its current-cycle qualification."""
    if not _qualified:
        qualify(binding, checkpoint=checkpoint)
    identity = binding.repository_identity
    prefix = list(binding.prefix or (binding.command,))
    result = _invoke([
        *prefix, "pr-watch", "inventory", identity.repo,
        "--config", binding.inventory_config,
        "--credential-command", json.dumps(binding.credential_command),
        "--max-pages", "4", "--max-pulls", "200", "--seconds", "60", "--json",
    ], timeout=60, checkpoint=checkpoint, qualified=True)
    if result.returncode:
        raise RepositoryNativeUnavailable(qualified=True)
    try:
        value = json_object(result.stdout.encode())
    except (ValueError, UnicodeError, RecursionError):
        raise RepositoryNativeUnavailable(qualified=True) from None
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
        raise RepositoryNativeUnavailable(qualified=True)
    checkpoint()
    return value
