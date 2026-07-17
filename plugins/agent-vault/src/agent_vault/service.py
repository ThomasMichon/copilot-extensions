"""Machine-local agent-vault service."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import (
    DEFAULT_TCP_PORT,
    IS_WINDOWS,
    LOG_FILE,
    PID_FILE,
    SOCKET_PATH,
    load_config,
    normalize_entry,
    resolve_kpdb,
    run_dir,
)
from .config import (
    tcp_port as configured_tcp_port,
)
from .extensions import ActionContext, UnlockContext, get_registry
from .gcm import git_credential_action
from .keepassxc import KeePassXCBackend
from .prompt import prompt_password

try:
    from endpoint_rendezvous import clear_endpoint, write_endpoint
except ImportError:  # pragma: no cover - lib is vendored at install time
    clear_endpoint = write_endpoint = None  # type: ignore[assignment]

# Service inactivity timeout (0 = never, set via --persistent)
TIMEOUT_SECONDS = int(os.environ.get("VAULT_TIMEOUT", "600"))

# Password cache TTL: master password is forgotten after this many seconds of inactivity.
PASSWORD_TTL = int(os.environ.get("VAULT_PASSWORD_TTL", "3600"))

# Maximum password attempts before giving up (per unlock cycle)
MAX_UNLOCK_ATTEMPTS = 3

# Cooldown after a failed unlock (seconds).
UNLOCK_COOLDOWN = 5

# Extended cooldown after a prompt cycle ends with dismissal/timeout.
PROMPT_DISMISS_COOLDOWN = int(os.environ.get("VAULT_PROMPT_DISMISS_COOLDOWN", "120"))

UNLOCK_REQUIRED_ACTIONS = frozenset({
    "get", "has", "search", "add", "set-password", "set-username",
    "import-key", "export-key", "list", "ls", "show", "remove", "rm",
    "move", "mv",
})

log = logging.getLogger("agent-vault.service")


def _within_group(entry: str, group: str | None) -> bool:
    """Whether a normalized entry falls within the resolved vault group.

    When no group is configured there is nothing to scope to, so every entry is
    considered in-scope. Used to gate destructive operations (remove/move) so a
    caller cannot delete or move an entry outside the active vault's group
    without an explicit ``force``.
    """
    if not group:
        return True
    root = group.rstrip("/")
    return entry == root or entry.startswith(root + "/")


class VaultService:
    def __init__(self) -> None:
        self.cli = KeePassXCBackend()
        self.cache: dict[tuple[str, str, str], str] = {}
        self.last_activity = time.time()
        self._password_set_at: dict[str, float] = {}
        self._shutdown = False
        self.ttl_override: int | None = None  # 0 = persistent (never expire)
        self._unlock_lock = threading.Lock()  # prevents concurrent GUI prompts
        self._unlock_failed_at: dict[str, float] = {}
        self._last_unlock_error: dict[str, str] = {}
        self._last_dismiss: dict[str, bool] = {}
        self._request_ctx = threading.local()  # per-thread request context

    def keepalive(self) -> None:
        self.last_activity = time.time()

    @property
    def ttl(self) -> int:
        if self.ttl_override == 0:
            return 999999
        return max(0, int(TIMEOUT_SECONDS - (time.time() - self.last_activity)))

    @property
    def is_expired(self) -> bool:
        if self.ttl_override == 0:
            return False
        return self.ttl <= 0

    def _check_password_ttl(self) -> None:
        """Clear cached password if it has exceeded PASSWORD_TTL."""
        if PASSWORD_TTL <= 0:
            return
        now = time.time()
        for kpdb, set_at in list(self._password_set_at.items()):
            if self.cli.has_password(kpdb) and (now - set_at) > PASSWORD_TTL:
                log.info("Password TTL expired (%ds) - clearing %s", PASSWORD_TTL, kpdb)
                self.cli.clear_password(kpdb)
                self.invalidate_cache(kpdb)
                self._password_set_at.pop(kpdb, None)

    def initialize(self) -> None:
        kpdb = resolve_kpdb(required=False)
        log.info("Initializing - KPDB=%s, CLI=%s", kpdb or "<unset>", self.cli._cli_path)
        if not kpdb:
            log.error("KeePass database path is not configured; set KPDB to your .kdbx path")
        elif not os.path.isfile(kpdb):
            log.error("KeePass database not found at %s - set KPDB to your .kdbx path", kpdb)

    def invalidate_cache(self, kpdb: str | None = None) -> None:
        if kpdb is None:
            self.cache.clear()
            return
        for key in [key for key in self.cache if key[0] == kpdb]:
            del self.cache[key]

    def _effective_cooldown(self, kpdb: str) -> float:
        """Return cooldown duration based on last failure mode."""
        if self._last_dismiss.get(kpdb, False) and PROMPT_DISMISS_COOLDOWN > 0:
            return PROMPT_DISMISS_COOLDOWN
        return UNLOCK_COOLDOWN

    def _record_unlock_error(self, kpdb: str, message: str | None) -> None:
        if message:
            self._last_unlock_error[kpdb] = message
        else:
            self._last_unlock_error.pop(kpdb, None)

    def _last_error(self, kpdb: str) -> str | None:
        return self._last_unlock_error.get(kpdb)

    def ensure_unlocked(
        self, kpdb: str, vault_name: str = "", reason: str = "",
        allow_prompt: bool | None = None,
    ) -> bool:
        """Ensure the CLI backend has the master password.

        Unlock-source providers always run (inline resolution). The interactive
        prompt is opt-in: when ``allow_prompt`` is False (the default -- fail-fast),
        a still-locked vault returns an actionable error instead of popping a
        blocking prompt. Pass ``allow_prompt=True`` (or set it on the request
        context) for the explicit, at-console unlock path.
        """
        if allow_prompt is None:
            allow_prompt = getattr(self._request_ctx, "allow_prompt", False)
        if not reason:
            reason = getattr(self._request_ctx, "reason", "")
        if not vault_name:
            vault_name = getattr(self._request_ctx, "vault_name", "")
        if self.cli.has_password(kpdb):
            return True

        if not kpdb:
            self._record_unlock_error("", "KeePass database path is not configured; set KPDB")
            log.error("Cannot unlock -- %s", self._last_error(""))
            return False
        if not os.path.isfile(kpdb):
            self._record_unlock_error(kpdb, f"KeePass database not found: {kpdb}")
            log.error("Cannot unlock -- %s", self._last_error(kpdb))
            return False

        cooldown = self._effective_cooldown(kpdb)
        failed_at = self._unlock_failed_at.get(kpdb)
        if failed_at is not None and (time.time() - failed_at) < cooldown:
            remaining = int(cooldown - (time.time() - failed_at))
            log.debug("Cooldown active (%ds remaining) -- suppressing prompt%s",
                      remaining, f" [{reason}]" if reason else "")
            return False

        acquired = self._unlock_lock.acquire(timeout=10)
        if not acquired:
            log.warning("Another unlock prompt is already active%s",
                        f" [{reason}]" if reason else "")
            self._record_unlock_error(kpdb, "Another unlock prompt is already active")
            return False

        try:
            if self.cli.has_password(kpdb):
                return True

            cooldown = self._effective_cooldown(kpdb)
            failed_at = self._unlock_failed_at.get(kpdb)
            if failed_at is not None and (time.time() - failed_at) < cooldown:
                remaining = int(cooldown - (time.time() - failed_at))
                log.debug("Cooldown active after lock (%ds remaining) -- suppressing prompt%s",
                          remaining, f" [{reason}]" if reason else "")
                return False

            cancel_streak = 0
            wrong_streak = 0
            base_prompt = (
                f"Master password for the '{vault_name}' vault:"
                if vault_name
                else f"Master password for {os.path.basename(kpdb)}:"
            )

            provided = get_registry().provide_unlock(
                UnlockContext(kpdb=kpdb, vault_name=vault_name, reason=reason),
                lambda candidate: self.cli.verify_password(kpdb, candidate),
            )
            if provided is not None:
                pw, provider_name = provided
                self.cli.set_password(kpdb, pw)
                self._password_set_at[kpdb] = time.time()
                self._unlock_failed_at.pop(kpdb, None)
                self._record_unlock_error(kpdb, None)
                self._last_dismiss[kpdb] = False
                log.info("CLI backend unlocked via provider %r (TTL %ds)%s",
                         provider_name, PASSWORD_TTL, f" [{reason}]" if reason else "")
                return True

            # Fail-fast: inline resolution (providers) did not unlock and the
            # caller has not opted into an interactive prompt. Return an
            # actionable error instead of popping a blocking dialog.
            if not allow_prompt:
                self._record_unlock_error(
                    kpdb,
                    "Vault locked -- run 'agent-vault unlock' to unlock, then retry",
                )
                log.info("Vault locked; prompting disabled -- fail-fast%s",
                         f" [{reason}]" if reason else "")
                return False

            while cancel_streak < MAX_UNLOCK_ATTEMPTS and wrong_streak < MAX_UNLOCK_ATTEMPTS:
                if wrong_streak > 0:
                    message = (
                        f"Invalid password -- try again ({wrong_streak + 1} of "
                        f"{MAX_UNLOCK_ATTEMPTS}):\n{base_prompt}"
                    )
                else:
                    message = base_prompt

                log.info("Prompting for master password (cancel_streak=%d, wrong_streak=%d)%s",
                         cancel_streak, wrong_streak, f" [{reason}]" if reason else "")
                pw = prompt_password(message)

                if not pw:
                    cancel_streak += 1
                    wrong_streak = 0
                    log.warning("Empty/cancelled prompt (%d/%d)%s",
                                cancel_streak, MAX_UNLOCK_ATTEMPTS,
                                f" [{reason}]" if reason else "")
                    continue

                cancel_streak = 0

                if self.cli.verify_password(kpdb, pw):
                    self.cli.set_password(kpdb, pw)
                    self._password_set_at[kpdb] = time.time()
                    self._unlock_failed_at.pop(kpdb, None)
                    self._record_unlock_error(kpdb, None)
                    self._last_dismiss[kpdb] = False
                    log.info("CLI backend unlocked (TTL %ds)%s",
                             PASSWORD_TTL, f" [{reason}]" if reason else "")
                    return True

                wrong_streak += 1
                log.warning("Invalid password (%d/%d)%s",
                            wrong_streak, MAX_UNLOCK_ATTEMPTS,
                            f" [{reason}]" if reason else "")

            if cancel_streak >= MAX_UNLOCK_ATTEMPTS:
                error = f"Unlock aborted (dismissed {MAX_UNLOCK_ATTEMPTS} times)"
                self._last_dismiss[kpdb] = True
            else:
                error = f"Password verification failed ({MAX_UNLOCK_ATTEMPTS} consecutive attempts)"
                self._last_dismiss[kpdb] = False
            self._record_unlock_error(kpdb, error)
            self._unlock_failed_at[kpdb] = time.time()
            log.error("Unlock failed: %s%s", error,
                      f" [{reason}]" if reason else "")
            return False
        finally:
            self._unlock_lock.release()

    # -- core operations -----------------------------------------------------

    def get(
        self,
        kpdb: str,
        entry: str,
        field: str = "password",
        group: str | None = None,
    ) -> str | None:
        entry = normalize_entry(entry, group)
        cache_key = (kpdb, entry, field)
        if cache_key in self.cache:
            return self.cache[cache_key]

        if not self.ensure_unlocked(kpdb):
            return None

        value = self.cli.get_entry(kpdb, entry, field)
        if value is not None:
            self.cache[cache_key] = value
        return value

    def has(self, kpdb: str, entry: str, group: str | None = None) -> bool | None:
        """Returns True/False, or None if unlock was cancelled."""
        entry = normalize_entry(entry, group)
        if not self.ensure_unlocked(kpdb):
            return None
        return self.cli.has_entry(kpdb, entry)

    # -- request handler -----------------------------------------------------

    def handle_request(self, request: dict, peer: str = "?") -> dict:
        self.keepalive()
        self._check_password_ttl()
        action = request.get("action")
        client = request.get("_client", "")
        kpdb = request.get("kpdb") or resolve_kpdb(required=False)
        group = request.get("group")
        vault_name = request.get("vault", "") or ""

        reason_parts = [f"action={action}"]
        if client:
            reason_parts.append(f"client={client}")
        if vault_name:
            reason_parts.append(f"vault={vault_name}")
        reason_parts.append(f"peer={peer}")
        reason = " ".join(reason_parts)

        vault_locked = not self.cli.has_password(kpdb)
        if vault_locked and action in UNLOCK_REQUIRED_ACTIONS:
            log.info("AUDIT unlock-required: %s", reason)
        elif vault_locked and action == "unlock" and request.get("prompt"):
            log.info("AUDIT unlock-prompt-requested: %s", reason)

        self._request_ctx.reason = reason
        self._request_ctx.kpdb = kpdb
        self._request_ctx.group = group
        self._request_ctx.vault_name = vault_name
        # Fail-fast by default: credential ops do not pop an interactive prompt
        # unless the caller explicitly opts in (allow_prompt=True). Unlock-source
        # providers still run inside ensure_unlocked, so inline resolution is not
        # skipped -- only the blocking prompt is gated. A still-locked op then
        # returns an actionable needs_unlock rather than stalling on a dialog.
        self._request_ctx.allow_prompt = bool(request.get("allow_prompt", False))

        if action == "ping":
            return {
                "ok": True,
                "pid": os.getpid(),
                "ttl": self.ttl,
                "cached": len(self.cache),
                "cli": self.cli.status(),
                "unlocked_vaults": self.cli.unlocked_vaults(),
            }

        if action == "get":
            entry = request.get("entry", "")
            field = request.get("field", "password")
            value = self.get(kpdb, entry, field, group)
            if value is not None:
                return {"ok": True, "value": value}
            if not self.cli.has_password(kpdb):
                error_msg = self._last_error(kpdb) or self._last_error("") or "Vault locked"
                return {"ok": False, "error": error_msg, "needs_unlock": True}
            return {"ok": False, "error": f"Entry not found: {entry}"}

        if action == "has":
            entry = request.get("entry", "")
            result = self.has(kpdb, entry, group)
            if result is None:
                error_msg = self._last_error(kpdb) or self._last_error("") or "Vault locked"
                return {"ok": False, "error": error_msg, "needs_unlock": True}
            return {"ok": True, "exists": result}

        if action == "lock":
            lock_kpdb = request.get("kpdb")
            if lock_kpdb:
                was_unlocked = self.cli.has_password(lock_kpdb)
                self.cli.clear_password(lock_kpdb)
                self.invalidate_cache(lock_kpdb)
                self._password_set_at.pop(lock_kpdb, None)
                self._unlock_failed_at.pop(lock_kpdb, None)
                self._record_unlock_error(lock_kpdb, None)
                self._last_dismiss.pop(lock_kpdb, None)
            else:
                was_unlocked = bool(self.cli.unlocked_vaults())
                self.cli.clear_password()
                self.invalidate_cache()
                self._password_set_at.clear()
                self._unlock_failed_at.clear()
                self._last_unlock_error.clear()
                self._last_dismiss.clear()
            log.info("CLI backend locked by client request [%s]", reason)
            return {"ok": True, "was_unlocked": was_unlocked}

        if action == "unlock":
            password = request.get("password", "")
            if not password and request.get("prompt"):
                # Explicit unlock-with-prompt opts into the interactive prompt.
                if self.ensure_unlocked(kpdb, vault_name, reason, allow_prompt=True):
                    return {"ok": True}
                error_msg = self._last_error(kpdb) or self._last_error("") or "Unlock failed"
                return {"ok": False, "error": error_msg, "needs_unlock": True}
            if not password:
                return {"ok": False, "error": "No password provided"}
            if not kpdb:
                return {"ok": False, "error": "KeePass database path is not configured; set KPDB"}
            if self.cli.verify_password(kpdb, password):
                self.cli.set_password(kpdb, password)
                self._password_set_at[kpdb] = time.time()
                self._unlock_failed_at.pop(kpdb, None)
                self._record_unlock_error(kpdb, None)
                self._last_dismiss[kpdb] = False
                log.info("CLI backend unlocked (TTL %ds) [%s]", PASSWORD_TTL, reason)
                return {"ok": True}
            return {"ok": False, "error": "Invalid password"}

        if action == "search":
            query = request.get("query", "")
            if not self.ensure_unlocked(kpdb, vault_name, reason):
                error_msg = self._last_error(kpdb) or self._last_error("") or "Vault locked"
                return {"ok": False, "error": error_msg, "needs_unlock": True}
            return {"ok": True, "results": self.cli.search(kpdb, query)}

        if action == "add":
            entry = normalize_entry(request.get("entry", ""), group)
            if not entry:
                return {"ok": False, "error": "No entry path provided"}
            if not self.ensure_unlocked(kpdb, vault_name, reason):
                error_msg = self._last_error(kpdb) or self._last_error("") or "Vault locked"
                return {"ok": False, "error": error_msg, "needs_unlock": True}
            ok, msg = self.cli.add_entry(
                kpdb,
                entry,
                username=request.get("username"),
                url=request.get("url"),
                password=request.get("password"),
                generate=request.get("generate", False),
            )
            return {"ok": ok, "message": msg} if ok else {"ok": False, "error": msg}

        if action == "set-password":
            entry = normalize_entry(request.get("entry", ""), group)
            password = request.get("password", "")
            if not entry:
                return {"ok": False, "error": "No entry path provided"}
            if not password:
                return {"ok": False, "error": "No password provided"}
            if not self.ensure_unlocked(kpdb, vault_name, reason):
                error_msg = self._last_error(kpdb) or self._last_error("") or "Vault locked"
                return {"ok": False, "error": error_msg, "needs_unlock": True}
            ok, msg = self.cli.edit_password(kpdb, entry, password)
            if ok:
                keys_to_remove = [k for k in self.cache if k[0] == kpdb and k[1] == entry]
                for key in keys_to_remove:
                    del self.cache[key]
                log.info("Password updated for %s, %d cache entries invalidated",
                         entry, len(keys_to_remove))
            return {"ok": ok, "message": msg} if ok else {"ok": False, "error": msg}

        if action == "import-key":
            import base64

            entry = normalize_entry(request.get("entry", ""), group)
            key_name = request.get("key_name", "")
            key_data_b64 = request.get("key_data", "")
            pub_data_b64 = request.get("pub_data", "")
            if not entry or not key_name:
                return {"ok": False, "error": "entry and key_name required"}
            if not self.ensure_unlocked(kpdb, vault_name, reason):
                error_msg = self._last_error(kpdb) or self._last_error("") or "Vault locked"
                return {"ok": False, "error": error_msg, "needs_unlock": True}
            if not self.cli.has_entry(kpdb, entry):
                ok, msg = self.cli.add_entry(kpdb, entry)
                if not ok:
                    return {"ok": False, "error": f"Failed to create entry: {msg}"}
            if key_data_b64:
                key_data = base64.b64decode(key_data_b64)
                ok, msg = self.cli.import_attachment(kpdb, entry, key_name, key_data)
                if not ok:
                    return {"ok": False, "error": f"Private key import failed: {msg}"}
            pub_name = key_name + ".pub"
            if pub_data_b64:
                pub_data = base64.b64decode(pub_data_b64)
                ok, msg = self.cli.import_attachment(kpdb, entry, pub_name, pub_data)
                if not ok:
                    return {"ok": False, "error": f"Public key import failed: {msg}"}
            return {"ok": True, "message": f"Imported {key_name} into {entry}"}

        if action == "export-key":
            import base64

            entry = normalize_entry(request.get("entry", ""), group)
            key_name = request.get("key_name", "")
            if not entry or not key_name:
                return {"ok": False, "error": "entry and key_name required"}
            if not self.ensure_unlocked(kpdb, vault_name, reason):
                error_msg = self._last_error(kpdb) or self._last_error("") or "Vault locked"
                return {"ok": False, "error": error_msg, "needs_unlock": True}
            key_data, msg = self.cli.export_attachment(kpdb, entry, key_name)
            if key_data is None:
                return {"ok": False, "error": f"Private key export failed: {msg}"}
            pub_name = key_name + ".pub"
            pub_data, msg = self.cli.export_attachment(kpdb, entry, pub_name)
            if pub_data is None:
                return {"ok": False, "error": f"Public key export failed: {msg}"}
            return {
                "ok": True,
                "key_data": base64.b64encode(key_data).decode(),
                "pub_data": base64.b64encode(pub_data).decode(),
            }

        if action in ("list", "ls"):
            list_path = request.get("path") or request.get("group") or "/"
            recursive = bool(request.get("recursive", False))
            flatten = bool(request.get("flatten", False))
            if not self.ensure_unlocked(kpdb, vault_name, reason):
                error_msg = self._last_error(kpdb) or self._last_error("") or "Vault locked"
                return {"ok": False, "error": error_msg, "needs_unlock": True}
            entries = self.cli.list_entries(
                kpdb, list_path, recursive=recursive, flatten=flatten
            )
            if entries is None:
                error_msg = self._last_error(kpdb) or self._last_error("") or "Vault locked"
                return {"ok": False, "error": error_msg, "needs_unlock": True}
            return {"ok": True, "entries": entries}

        if action == "show":
            entry = normalize_entry(request.get("entry", ""), group)
            if not entry:
                return {"ok": False, "error": "No entry path provided"}
            show_protected = bool(request.get("show_protected", False))
            if not self.ensure_unlocked(kpdb, vault_name, reason):
                error_msg = self._last_error(kpdb) or self._last_error("") or "Vault locked"
                return {"ok": False, "error": error_msg, "needs_unlock": True}
            output = self.cli.show_entry(kpdb, entry, show_protected=show_protected)
            if output is None:
                return {"ok": False, "error": f"Entry not found: {entry}"}
            return {"ok": True, "output": output}

        if action == "set-username":
            entry = normalize_entry(request.get("entry", ""), group)
            username = request.get("username", "")
            if not entry:
                return {"ok": False, "error": "No entry path provided"}
            if not username:
                return {"ok": False, "error": "No username provided"}
            if not self.ensure_unlocked(kpdb, vault_name, reason):
                error_msg = self._last_error(kpdb) or self._last_error("") or "Vault locked"
                return {"ok": False, "error": error_msg, "needs_unlock": True}
            ok, msg = self.cli.edit_username(kpdb, entry, username)
            if ok:
                keys_to_remove = [k for k in self.cache if k[0] == kpdb and k[1] == entry]
                for key in keys_to_remove:
                    del self.cache[key]
                log.info("Username updated for %s, %d cache entries invalidated",
                         entry, len(keys_to_remove))
            return {"ok": ok, "message": msg} if ok else {"ok": False, "error": msg}

        if action in ("remove", "rm"):
            entry = normalize_entry(request.get("entry", ""), group)
            if not entry:
                return {"ok": False, "error": "No entry path provided"}
            force = bool(request.get("force", False))
            effective_group = group or load_config().group
            if not force and not _within_group(entry, effective_group):
                return {"ok": False, "error": (
                    f"Entry '{entry}' is outside the '{effective_group}' group "
                    "scope. Send force=true to override.")}
            if not self.ensure_unlocked(kpdb, vault_name, reason):
                error_msg = self._last_error(kpdb) or self._last_error("") or "Vault locked"
                return {"ok": False, "error": error_msg, "needs_unlock": True}
            ok, msg = self.cli.remove_entry(kpdb, entry)
            if ok:
                keys_to_remove = [k for k in self.cache if k[0] == kpdb and k[1] == entry]
                for key in keys_to_remove:
                    del self.cache[key]
                log.info("Removed entry %s, %d cache entries invalidated",
                         entry, len(keys_to_remove))
            return {"ok": ok, "message": msg} if ok else {"ok": False, "error": msg}

        if action in ("move", "mv"):
            entry = normalize_entry(request.get("entry", ""), group)
            dest_group = request.get("dest") or request.get("dest_group") or ""
            if not entry:
                return {"ok": False, "error": "No entry path provided"}
            if not dest_group:
                return {"ok": False, "error": "No destination group provided"}
            force = bool(request.get("force", False))
            effective_group = group or load_config().group
            if not force and not _within_group(entry, effective_group):
                return {"ok": False, "error": (
                    f"Entry '{entry}' is outside the '{effective_group}' group "
                    "scope. Send force=true to override.")}
            if not self.ensure_unlocked(kpdb, vault_name, reason):
                error_msg = self._last_error(kpdb) or self._last_error("") or "Vault locked"
                return {"ok": False, "error": error_msg, "needs_unlock": True}
            ok, msg = self.cli.move_entry(kpdb, entry, dest_group)
            if ok:
                # Invalidate cache for the old path and any stale cache at the new path.
                entry_name = entry.rsplit("/", 1)[-1]
                new_path = dest_group.rstrip("/") + "/" + entry_name
                keys_to_remove = [
                    k for k in self.cache
                    if k[0] == kpdb and k[1] in (entry, new_path)
                ]
                for key in keys_to_remove:
                    del self.cache[key]
                log.info("Moved entry %s -> %s", entry, dest_group)
            return {"ok": ok, "message": msg} if ok else {"ok": False, "error": msg}

        if action == "stop":
            self._shutdown = True
            return {"ok": True}

        if action == "git-credential":
            # Delegate an HTTPS git credential to the local GCM for allowlisted
            # hosts. Independent of KeePassXC -- no vault unlock required.
            return git_credential_action({**request, "allow_prompt": self._request_ctx.allow_prompt})

        handler = get_registry().action(action)
        if handler is not None:
            ext_ctx = ActionContext(
                kpdb=kpdb, group=group, vault_name=vault_name, reason=reason
            )
            try:
                return handler(self, request, ext_ctx)
            except Exception as exc:
                log.warning("Extension action %r raised: %s", action, exc)
                return {"ok": False, "error": f"Extension action failed: {action}"}

        return {"ok": False, "error": f"Unknown action: {action}"}


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    service: VaultService,
) -> None:
    peer = writer.get_extra_info("peername", "?")
    try:
        data = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if not data:
            return
        try:
            request = json.loads(data.decode().strip())
        except json.JSONDecodeError:
            response = {"ok": False, "error": "Invalid JSON"}
            writer.write((json.dumps(response) + "\n").encode())
            await writer.drain()
            return

        action = request.get("action", "?")
        client = request.get("_client", "")
        log.debug("< %s from %s%s", action, peer, f" client={client}" if client else "")

        response = await asyncio.get_event_loop().run_in_executor(
            None, service.handle_request, request, str(peer)
        )

        ok = response.get("ok", False)
        log.debug(
            "> %s ok=%s%s",
            action,
            ok,
            f" error={response['error']}" if not ok and "error" in response else "",
        )

        writer.write((json.dumps(response) + "\n").encode())
        await writer.drain()
    except TimeoutError:
        log.debug("Client %s timed out waiting for request", peer)
    except Exception as exc:
        log.debug("Client %s error: %s", peer, exc)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


def advertised_endpoint(
    *,
    is_windows: bool,
    unix_bound: bool,
    socket_path: str,
    tcp_bound: bool,
    tcp_address: str | None,
) -> tuple[str, str] | None:
    """Pick the endpoint to advertise for discovery, preferring the OS-native one.

    On a POSIX host the Unix domain socket is the preferred local endpoint; a
    Windows host advertises its loopback TCP endpoint. Returns ``(transport,
    address)`` or ``None`` if nothing bound.
    """
    if unix_bound and not is_windows:
        return ("unix", socket_path)
    if tcp_bound and tcp_address:
        return ("tcp", tcp_address)
    return None


async def run_server(service: VaultService, tcp_port: int | None = None) -> None:
    servers = []

    unix_bound = False
    if not IS_WINDOWS:
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        unix_srv = await asyncio.start_unix_server(
            lambda r, w: handle_client(r, w, service),
            path=SOCKET_PATH,
        )
        os.chmod(SOCKET_PATH, 0o600)
        servers.append(unix_srv)
        unix_bound = True
        log.info("Listening on Unix socket %s", SOCKET_PATH)

    port = tcp_port or configured_tcp_port()
    tcp_address: str | None = None
    try:
        tcp_srv = await asyncio.start_server(
            lambda r, w: handle_client(r, w, service),
            host="127.0.0.1",
            port=port,
        )
        servers.append(tcp_srv)
        # Read the *actual* bound port so this stays correct if ``port`` is ever 0.
        bound_port = tcp_srv.sockets[0].getsockname()[1]
        tcp_address = f"127.0.0.1:{bound_port}"
        log.info("Listening on TCP %s", tcp_address)
    except OSError as e:
        if IS_WINDOWS or not servers:
            log.error("Could not bind TCP 127.0.0.1:%d (%s); no listeners, exiting", port, e)
            sys.exit(1)
        log.warning("Could not bind TCP 127.0.0.1:%d (%s); using Unix socket only", port, e)

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    # Advertise the endpoint so clients can discover it instead of hardcoding a
    # constant -- see the endpoint-rendezvous lib and
    # docs/patterns/local-endpoint-discovery.md. Additive: clients that still use
    # the fixed socket/port are unaffected.
    advertised = advertised_endpoint(
        is_windows=IS_WINDOWS,
        unix_bound=unix_bound,
        socket_path=SOCKET_PATH,
        tcp_bound=tcp_address is not None,
        tcp_address=tcp_address,
    )
    if advertised is not None and write_endpoint is not None:
        try:
            path = write_endpoint(run_dir(), advertised[0], advertised[1])
            log.info("Advertised endpoint %s:%s at %s", advertised[0], advertised[1], path)
        except OSError as e:
            log.warning("Could not write rendezvous file (%s); discovery degraded", e)

    try:
        while not service._shutdown and not service.is_expired:
            await asyncio.sleep(0.5)
    finally:
        reason = "shutdown requested" if service._shutdown else "inactivity timeout"
        log.info("Stopping: %s", reason)
        for srv in servers:
            srv.close()
            await srv.wait_closed()
        cleanup()


def cleanup() -> None:
    if clear_endpoint is not None:
        with contextlib.suppress(OSError):
            clear_endpoint(run_dir())
    for path in (SOCKET_PATH, PID_FILE):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)


def send_command(request: dict) -> dict | None:
    """Send a command to a running service (client helper)."""
    if not IS_WINDOWS:
        import socket as sock_mod

        try:
            s = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
            s.connect(SOCKET_PATH)
            s.settimeout(5.0)
            s.sendall((json.dumps(request) + "\n").encode())
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
            s.close()
            return json.loads(buf.decode().strip())
        except Exception:
            pass

    import socket as sock_mod

    try:
        s = sock_mod.socket(sock_mod.AF_INET, sock_mod.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect(("127.0.0.1", configured_tcp_port()))
        s.sendall((json.dumps(request) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        return json.loads(buf.decode().strip())
    except Exception:
        return None


def is_process_alive(pid: int) -> bool:
    if IS_WINDOWS:
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def read_pid() -> int | None:
    try:
        return int(Path(PID_FILE).read_text().strip())
    except Exception:
        return None


def daemonize_unix() -> bool:
    """Double-fork into a background daemon (Linux/WSL)."""
    pid = os.fork()
    if pid > 0:
        time.sleep(0.5)
        return False
    os.setsid()
    pid2 = os.fork()
    if pid2 > 0:
        os._exit(0)
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, sys.stdin.fileno())
    os.dup2(devnull, sys.stdout.fileno())
    os.dup2(devnull, sys.stderr.fileno())
    os.close(devnull)
    return True


def daemonize_windows(argv: list[str]) -> None:
    """Start a detached background process (Windows)."""
    create_no_window = 0x08000000
    detached_process = 0x00000008
    cmd = [sys.executable, *argv, "--foreground"]
    subprocess.Popen(
        cmd,
        creationflags=create_no_window | detached_process,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Agent Vault Service")
    parser.add_argument("--foreground", action="store_true", help="Run in foreground")
    parser.add_argument("--persistent", action="store_true", help="Disable inactivity timeout")
    parser.add_argument("--tcp-port", type=int, default=None,
                        help=f"TCP port (default: {DEFAULT_TCP_PORT})")
    parser.add_argument("--ping", action="store_true", help="Check if service is running")
    parser.add_argument("--stop", action="store_true", help="Stop running service")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log.setLevel(logging.DEBUG)

    try:
        fh = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(name)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"))
        logging.getLogger().addHandler(fh)
        log.debug("Log file: %s", LOG_FILE)
    except Exception as exc:
        log.warning("Could not open log file %s: %s", LOG_FILE, exc)

    if args.ping:
        resp = send_command({"action": "ping"})
        if resp and resp.get("ok"):
            print(
                f"Vault service running - PID {resp['pid']}, "
                f"TTL {resp['ttl']}s, {resp['cached']} cached entries, "
                f"cli={resp['cli']}"
            )
            sys.exit(0)
        print("Vault service not running")
        sys.exit(1)

    if args.stop:
        resp = send_command({"action": "stop"})
        if resp and resp.get("ok"):
            print("Vault service stopped")
            sys.exit(0)
        print("Vault service not running")
        sys.exit(1)

    existing = send_command({"action": "ping"})
    if existing and existing.get("ok"):
        pid = existing["pid"]
        if is_process_alive(pid):
            print(f"Vault service already running - PID {pid}, cli={existing['cli']}")
            sys.exit(0)
        cleanup()

    service = VaultService()
    if args.persistent:
        service.ttl_override = 0
        log.info("Persistent mode - inactivity timeout disabled")
    service.initialize()

    tcp_port = args.tcp_port

    if args.foreground:
        print(
            f"Vault service starting (cli={service.cli.status(resolve_kpdb(required=False))}, "
            f"TCP 127.0.0.1:{tcp_port or configured_tcp_port()})"
        )

        def sig_handler(signum, frame):
            service._shutdown = True

        signal.signal(signal.SIGTERM, sig_handler)
        signal.signal(signal.SIGINT, sig_handler)

        asyncio.run(run_server(service, tcp_port=tcp_port))
    else:
        if IS_WINDOWS:
            daemonize_windows(["-m", "agent_vault.service", *sys.argv[1:]])
            time.sleep(1.0)
            resp = send_command({"action": "ping"})
            if resp and resp.get("ok"):
                print(f"Vault service started - PID {resp['pid']}, cli={resp['cli']}")
            else:
                print("Vault service started (waiting for readiness...)")
        else:
            is_daemon = daemonize_unix()
            if is_daemon:
                signal.signal(signal.SIGTERM, lambda s, f: setattr(service, "_shutdown", True))
                signal.signal(signal.SIGINT, lambda s, f: setattr(service, "_shutdown", True))
                asyncio.run(run_server(service, tcp_port=tcp_port))
            else:
                time.sleep(0.3)
                resp = send_command({"action": "ping"})
                if resp and resp.get("ok"):
                    print(f"Vault service started - PID {resp['pid']}, cli={resp['cli']}")
                else:
                    print("Vault service started")


if __name__ == "__main__":
    main()
