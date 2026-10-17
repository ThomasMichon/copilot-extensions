"""Local setup and cache-only readiness for the optional PR identity layer."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys

from . import identity_marker, identity_vault
from . import identity_vault_settings as settings

_VAULT_ACTIONS = {
    "database_unconfigured": "Configure the shared KeePass database through agent-vault first.",
    "database_unavailable": "Make the selected synchronized database available; do not initialize another key.",
    "context_invalid": "Check the selected vault context and explicitly replace invalid selection pins.",
    "confirmation_required": "First-owner initialization requires separate confirmation and both initialization flags.",
    "invalid_entry": "Select a complete valid common group/entry path.",
    "needs_unlock": "Unlock the selected vault explicitly or allow --prompt.",
    "entry_missing": "Synchronize the existing common entry; only a confirmed new owner may initialize it.",
    "malformed_key": "Preserve the malformed existing entry and investigate; do not replace the owner key.",
    "ambiguous_entry": "Resolve ambiguous vault entries before setup; no replacement key was requested.",
    "vault_read_failed": "Check the selected vault's authoritative read; a failed read is not absence.",
    "group_create_failed": "Check the selected vault's group creation and write permissions.",
    "operation_expired": "The vault operation expired; retry setup to reconcile authoritative state.",
    "creation_unconfirmed": "Reconcile the authoritative entry before retrying; never rotate or overwrite it.",
    "service_busy": "Retry after the current vault operation finishes.",
    "service_unavailable": "Start the selected vault independently through its owning lifecycle command, then retry setup.",
    "cache_unavailable": "Enable usable protected-cache support in the selected vault runtime.",
    "cache_read_failed": "Repair the selected protected cache without replacing the authoritative owner key.",
    "cache_update_failed": "Check protected-cache population and permissions; source configuration was not replaced.",
    "invalid_response": "Check the selected vault's compatible identity-key CLI/service interface.",
}


class VaultActionRequired(ValueError):
    def __init__(self, code: str):
        self.code = code if isinstance(code, str) and code in _VAULT_ACTIONS else "invalid_response"
        super().__init__(_VAULT_ACTIONS[self.code])


def add_parsers(sub) -> None:
    parser = sub.add_parser("identity-vault", help="Set up or verify the shared vault-backed PR identity key")
    verbs = parser.add_subparsers(dest="identity_vault_command", required=True)
    for verb in ("status", "setup", "verify"):
        child = verbs.add_parser(verb)
        child.add_argument("--json", action="store_true")
        if verb != "verify":
            child.add_argument("--vault-argv", help="Exact agent-vault session-catalog argv as JSON")
            child.add_argument("--vault-context", help="Optional owning vault receipt from its source-qualified catalog")
            child.add_argument("--entry", default=None, help="Complete shared group/entry path")
        if verb == "setup":
            child.add_argument("--prompt", action="store_true", help="Explicitly allow a local vault unlock prompt")
            child.add_argument("--initialize-owner", action="store_true", help="First owner only: create an absent shared key")
            child.add_argument("--confirm-first-owner", action="store_true", help="Confirm this is the authoritative first copy, not an additional machine")


def _vault_json(
    argv: list[str], args: list[str], *, env: dict | None = None,
    prompt: bool = False, timeout: float = 45, input_data: bytes | None = None,
) -> dict:
    status, raw = identity_vault._read_command_output(
        [*argv, *args], env=env, limit=32768, timeout=180 if prompt else timeout,
        input_data=input_data,
    )
    result = json.loads(raw)
    if not isinstance(result, dict) or type(result.get("version")) is not int or result["version"] != 1:
        raise ValueError("vault identity-key interface unavailable or incompatible")
    if status:
        raise VaultActionRequired(result.get("error", "invalid_response"))
    return result


def _candidate(args) -> dict | None:
    if args.vault_argv is None:
        selected = settings.load_settings()
        if args.entry is not None or args.vault_context is not None:
            raise ValueError("--entry/--vault-context requires --vault-argv; source changes must be explicit")
        return selected
    argv = settings.validate_argv(json.loads(args.vault_argv))
    entry = settings.validate_entry(args.entry or identity_vault.DEFAULT_ENTRY)
    context = _vault_json(
        argv, ["identity-key", "context", "--json"],
        env=settings.discovery_environment(args.vault_context),
    )
    return settings.validate_settings({
        "version": 1, "argv": argv, "entry": entry, "env": context.get("env"),
    })


def _overrides(selected: dict | None) -> list[str]:
    conflicts = []
    if os.environ.get(identity_marker.KEY_ENV_VAR):
        conflicts.append(identity_marker.KEY_ENV_VAR)
    if identity_vault.ARGV_ENV in os.environ:
        try:
            matches = selected is not None and json.loads(os.environ[identity_vault.ARGV_ENV]) == selected["argv"]
        except (ValueError, TypeError):
            matches = False
        if not matches:
            conflicts.append(identity_vault.ARGV_ENV)
        # Legacy argv overrides intentionally retain their ambient selection.
        # They cannot silently supersede a newly pinned source.
        conflicts.append("legacy-vault-environment-override")
    elif identity_vault.ENTRY_ENV in os.environ:
        conflicts.append(identity_vault.ENTRY_ENV)
    return conflicts


def _cache_ready(selected: dict) -> bool:
    try:
        raw = identity_vault._read_key_output(
            [*selected["argv"], "get", selected["entry"], "password", "--cache-only"],
            env=settings.source_environment(selected),
            timeout=identity_vault._PAYLOAD_READ_TIMEOUT,
        )
        return len(base64.b64decode(raw.strip(), validate=True)) == identity_marker.KEY_BYTES
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        return False


def _fresh_probe(selected: dict | None = None) -> dict:
    result = _vault_json(
        [sys.executable, "-m", "agent_worktrees.identity_vault_cli"],
        ["--probe"], input_data=json.dumps(selected).encode() if selected is not None else None,
        timeout=60,
    )
    if result != {"version": 1, "ready": True, "cache_only": True, "roundtrip": True}:
        raise ValueError("fresh-process shared-key verification failed")
    return result


def _probe() -> int:
    try:
        raw = sys.stdin.buffer.read(32769)
        if len(raw) > 32768:
            raise ValueError("oversized probe source")
        source = settings.validate_settings(json.loads(raw)) if raw else None
        key = identity_vault.load_cached_vault_key(source) if source is not None else identity_marker.load_identity_key()
        payload = identity_marker.build_identity_payload(worktree_id="identity-readiness", machine="fixture")
        token = identity_marker.encrypt_identity_payload(payload, key=key) if key is not None else None
        ready = token is not None and identity_marker.decrypt_identity_payload(token, key=key) == payload
    except (OSError, ValueError, RuntimeError, identity_marker.IdentityMarkerError, subprocess.TimeoutExpired):
        ready = False
    print(json.dumps({"version": 1, "ready": ready, "cache_only": True, "roundtrip": ready}))
    return 0


def cmd_identity_vault(args: argparse.Namespace) -> int:
    try:
        verb = args.identity_vault_command
        selected = settings.load_settings() if verb == "verify" else _candidate(args)
        conflicts = _overrides(selected)
        if verb == "status":
            cached = bool(selected and not conflicts and _cache_ready(selected))
            persisted = settings.load_settings()
            result = {
                "version": 1, "configured": persisted is not None,
                "invocation": bool(selected),
                "database": bool(selected and selected["env"].get("AGENT_VAULT_PINNED_CONTEXT")),
                "cache": cached, "overrides": conflicts,
                "ready": cached and persisted == selected,
            }
        elif verb == "verify":
            if selected is None:
                raise ValueError("run identity-vault setup with the vault session-catalog argv first")
            if conflicts:
                raise ValueError("conflicting identity overrides: " + ", ".join(conflicts))
            result = _fresh_probe()
        else:
            if selected is None:
                raise ValueError("setup requires --vault-argv from the agent-vault session command catalog")
            if conflicts:
                raise ValueError("conflicting identity overrides: " + ", ".join(conflicts))
            if args.initialize_owner != args.confirm_first_owner:
                raise ValueError("first-owner initialization requires both --initialize-owner and --confirm-first-owner")
            command = ["identity-key", "setup", "--entry", selected["entry"], "--require-running", "--json"]
            if args.prompt:
                command.append("--prompt")
            if args.initialize_owner:
                command.extend(["--initialize-owner", "--confirm-first-owner"])
            provisioned = _vault_json(
                selected["argv"], command, env=settings.source_environment(selected),
                prompt=args.prompt, timeout=125,
            )
            if provisioned.get("status") not in ("reused", "created") or provisioned.get("cached") is not True:
                raise ValueError("vault did not confirm protected-cache population; run the vault identity-key setup directly")
            _fresh_probe(selected)
            settings.save_settings(selected)
            result = {"version": 1, "configured": True, "ready": True, "key": provisioned["status"]}
        print(json.dumps(result) if args.json else
              "Shared PR identity ready." if result["ready"] else
              "Shared PR identity not ready; inspect identity-vault status --json and run setup.")
        return 0 if result["ready"] else 2
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        # Backend stdout/stderr and arbitrary exception payloads are never
        # reflected: an adapter may have emitted credential-bearing output.
        known = (
            "conflicting identity overrides:", "first-owner initialization requires",
            "setup requires --vault-argv", "run identity-vault setup",
            "--entry/--vault-context requires --vault-argv",
        )
        message = str(exc) if isinstance(exc, VaultActionRequired) or str(exc).startswith(known) else (
            "Vault identity setup/read failed. Check the selected vault's identity-key "
            "context/setup commands; unlock explicitly or opt into --prompt. "
            "Existing source configuration was not replaced."
        )
        failure = {
            "version": 1, "ready": False,
            "error": exc.code if isinstance(exc, VaultActionRequired) else "configuration_or_adapter_error",
            "message": message,
        }
        print(json.dumps(failure) if args.json else message,
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    if sys.argv[1:] != ["--probe"]:
        raise SystemExit("internal readiness probe requires --probe")
    raise SystemExit(_probe())
