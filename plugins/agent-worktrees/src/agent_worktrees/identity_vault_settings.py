"""Installation-scoped, non-secret source selection for PR identity."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from . import config as cfg

ENV_KEYS = frozenset({
    "COPILOT_EXTENSIONS_CONTEXT",
    "AGENT_VAULT_PINNED_CONTEXT", "AGENT_VAULT_CACHE", "AGENT_VAULT_CACHE_DIR",
    "AGENT_VAULT_HOME", "AGENT_VAULT_INSTALLATION_ID", "AGENT_VAULT_CONFIG",
    "AGENT_VAULT_RUN_DIR", "AGENT_VAULT_ENDPOINT", "AGENT_VAULT_HOST",
    "AGENT_VAULT_PORT", "AGENT_VAULT_PIPE", "AGENT_VAULT_SOCKET",
    "AGENT_VAULT_PID", "AGENT_VAULT_LOG", "AGENT_VAULT_SYSTEMD_UNIT",
    "AGENT_VAULT_TASK_NAME", "AGENT_VAULT_WINDOWS_RUN_DIR",
    "KPDB", "VAULT_GROUP", "AGENT_VAULT",
})


def settings_path() -> Path:
    return cfg.install_dir() / "identity-vault.json"


def validate_argv(value: object) -> list[str]:
    if (
        not isinstance(value, list) or not value or len(value) > 16
        or any(not isinstance(arg, str) or not arg or len(arg) > 4096
               or any(char in arg for char in "\x00\r\n") for arg in value)
        or not os.path.isabs(value[0])
    ):
        raise ValueError("vault argv must be an absolute session-catalog JSON argv array")
    return value


def validate_entry(value: object) -> str:
    if (
        not isinstance(value, str) or not value or len(value) > 1024
        or any(char in value for char in "\x00\r\n\\")
        or (os.name == "nt" and any(char in value for char in '&|<>^%!"'))
        or any(part in ("", ".", "..") or part.startswith("-") for part in value.split("/"))
        or "/" not in value
    ):
        raise ValueError("vault entry must be a complete group/entry path safe for the owning launcher")
    return value


def validate_settings(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"version", "argv", "entry", "env"}:
        raise ValueError("invalid identity vault configuration")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("unsupported identity vault configuration version")
    validate_argv(value["argv"])
    validate_entry(value["entry"])
    env = value["env"]
    if (
        not isinstance(env, dict) or not set(env) <= ENV_KEYS
        or any(not isinstance(item, str) or len(item) > 16384 or "\x00" in item
               for item in env.values())
        or env.get("AGENT_VAULT_CACHE") != "1"
        or not os.path.isabs(env.get("AGENT_VAULT_CACHE_DIR", ""))
        or not env.get("AGENT_VAULT_PINNED_CONTEXT")
        or "COPILOT_EXTENSIONS_CONTEXT" not in env
        or (env["COPILOT_EXTENSIONS_CONTEXT"] and not os.path.isabs(env["COPILOT_EXTENSIONS_CONTEXT"]))
    ):
        raise ValueError("invalid pinned vault/cache context")
    try:
        pin = json.loads(env["AGENT_VAULT_PINNED_CONTEXT"])
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid pinned vault selection") from exc
    if (
        not isinstance(pin, dict)
        or set(pin) != {"version", "kpdb", "group", "vault_name", "port"}
        or type(pin["version"]) is not int or pin["version"] != 1
        or not isinstance(pin["kpdb"], str) or not os.path.isabs(pin["kpdb"])
        or any(not isinstance(pin[key], str) for key in ("group", "vault_name"))
        or type(pin["port"]) is not int or not 0 <= pin["port"] <= 65535
    ):
        raise ValueError("invalid pinned vault selection")
    if len(json.dumps(value, sort_keys=True, indent=2).encode("utf-8")) + 1 > 32768:
        raise ValueError("identity vault configuration is oversized")
    return value


def load_settings() -> dict | None:
    path = settings_path()
    try:
        with path.open("rb") as stream:
            encoded = stream.read(32769)
    except FileNotFoundError:
        return None
    if len(encoded) > 32768:
        raise ValueError("identity vault configuration is oversized")
    try:
        return validate_settings(json.loads(encoded))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("identity vault configuration is unreadable") from exc


def configured() -> bool:
    # An invalid/unreadable configured source is still selected, never a signal
    # to fall back to a different owner's legacy file.
    try:
        settings_path().stat()
        return True
    except FileNotFoundError:
        return False
    except (OSError, ValueError, RuntimeError):
        return True


def source_environment(settings: dict) -> dict[str, str]:
    env = os.environ.copy()
    for key in ENV_KEYS | {"COPILOT_PLUGIN_ROOT", "PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"}:
        env.pop(key, None)
    env.update(settings["env"])
    env["COPILOT_EXTENSIONS_BOOT_TRACE_LOG_PATH"] = os.devnull
    return env


def discovery_environment(context: str | None = None) -> dict[str, str]:
    """Let the selected payload resolve its owner, never inherit a peer receipt."""
    env = os.environ.copy()
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"):
        env.pop(key, None)
    env["AGENT_VAULT_NO_SELFPROVISION"] = "1"
    env["COPILOT_EXTENSIONS_BOOT_TRACE_LOG_PATH"] = os.devnull
    if context:
        if not os.path.isabs(context):
            raise ValueError("vault installation context must be an absolute receipt path")
        env["COPILOT_EXTENSIONS_CONTEXT"] = context
    return env


def save_settings(settings: dict) -> None:
    validate_settings(settings)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".identity-vault-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(settings, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
