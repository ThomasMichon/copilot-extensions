"""One versioned, complete mux observation per admitted venue."""
from __future__ import annotations

import asyncio
import shlex
import subprocess
import time
from collections.abc import Awaitable, Callable
from typing import Any

from remote_login_shell import wrap_login_shell

from ._ssh_retry import exec_with_retry
from .owner_inventory import (
    OwnerInventory, capture_scope, collect_inventory, credential,
    credential_matches, joined_thread,
)


def _admitted_source(codespace, account, token, remaining):
    from agent_procutil import no_window_kwargs

    from .codespace_config import CodespaceSource

    class OwnerSource(CodespaceSource):
        def _fetch_gh_config(self):
            # The general source deliberately retries cold starts. An observer
            # has no such authority: one attempt, bounded by its Available proof.
            result = subprocess.run(
                ["gh", "codespace", "ssh", "--config", "-c", codespace],
                capture_output=True, text=True, env=self.gh_env,
                timeout=min(30.0, remaining()), **no_window_kwargs(),
            )
            if result.returncode != 0:
                raise RuntimeError("Owner config fetch failed")
            return result.stdout

    return OwnerSource(codespace, account=account, token=token)


def _admitted_manager(admit):
    from ssh_manager import ConnectionManager

    class OwnerManager(ConnectionManager):
        async def _start_control_master(self, *args, **kwargs):
            # The shared manager may retry: each dial must re-enter admission.
            budget = await admit()
            return await asyncio.wait_for(
                super()._start_control_master(*args, **kwargs), timeout=budget,
            )

    return OwnerManager()


class _AdmittedExec:
    def __init__(self, manager, admit):
        self.manager = manager
        self.admit = admit

    async def exec_command(self, *args, **kwargs):
        kwargs["timeout"] = min(kwargs.get("timeout", 30.0), await self.admit())
        return await self.manager.exec_command(*args, **kwargs)


def batch_command(targets: list[str]) -> str:
    targets = list(dict.fromkeys(targets))
    lines = ["command -v tmux >/dev/null 2>&1 || exit 127"]
    for index, target in enumerate(targets):
        lines.extend([
            f"tmux has-session -t {shlex.quote('=' + target)} >/dev/null 2>&1",
            "rc=$?",
            'case "$rc" in 0) state=present;; 1) state=gone;; *) state=unknown;; esac',
            f"""printf 'ACMUX1\\t{index}\\t%s\\n' "$state" """.rstrip(),
        ])
    lines.append(f"printf 'ACMUX1\\tDONE\\t{len(targets)}\\n'")
    return "\n".join(lines)


def parse_batch(targets: list[str], stdout: str, exit_code: int | None) -> dict[str, bool | None]:
    targets = list(dict.fromkeys(targets))
    unknown = dict.fromkeys(targets)
    if exit_code != 0 or not isinstance(stdout, str):
        return unknown
    lines = stdout.splitlines()
    if len(lines) != len(targets) + 1 or lines[-1] != f"ACMUX1\tDONE\t{len(targets)}":
        return unknown
    verdicts: dict[str, bool | None] = {}
    values = {"present": True, "gone": False, "unknown": None}
    for index, (target, line) in enumerate(zip(targets, lines)):
        parts = line.split("\t")
        if len(parts) != 3 or parts[:2] != ["ACMUX1", str(index)] or parts[2] not in values:
            return unknown
        verdicts[target] = values[parts[2]]
    return verdicts


class RemoteMuxProbe:
    """Callable legacy probe plus an explicit immutable per-wave adapter."""

    def __init__(
        self, *, inventory_reader: Callable[[int], OwnerInventory] = collect_inventory,
        scope_reader: Callable[..., Any] = capture_scope,
        open_manager: Callable[[str], Awaitable[Any]] | None = None,
        clock: Callable[[], float] = time.monotonic, max_age: float = 30.0,
    ) -> None:
        self.inventory_reader = inventory_reader
        self.scope_reader = scope_reader
        self.open_manager = open_manager
        self.clock = clock
        self.max_age = max_age

    async def begin_wave(self, generation: int) -> OwnerInventory:
        return await joined_thread(self.inventory_reader, generation)

    async def __call__(self, codespace: str, targets: list[str]) -> dict[str, bool | None]:
        try:
            inventory = await self.begin_wave(0)
            return await self.probe_wave(codespace, targets, inventory, 0, lambda: True)
        except Exception:
            return dict.fromkeys(targets)

    async def probe_wave(
        self, codespace: str, targets: list[str], inventory: OwnerInventory,
        generation: int, current: Callable[[], bool],
    ) -> dict[str, bool | None]:
        unknown = dict.fromkeys(targets)
        manager = None
        try:
            scope = await joined_thread(self.scope_reader)
            row = inventory.lookup(
                codespace, wave=generation, scope=scope,
                now=self.clock(), max_age=self.max_age,
            )
            if row is None or not current():
                return unknown
            if row.state.lower() in {"unknown", ""}:
                return unknown
            if row.state.lower() != "available":
                return {target: False for target in targets}

            def remaining() -> float:
                budget = min(item.observed_at for item in inventory.listings) + self.max_age - self.clock()
                if not current() or budget <= 0:
                    raise RuntimeError("Owner inventory admission expired")
                return budget

            async def admit() -> float:
                latest = await joined_thread(self.scope_reader)
                if latest != scope:
                    raise RuntimeError("Owner inventory authority changed")
                return remaining()

            if self.open_manager is not None:
                remaining()
                manager = await self.open_manager(codespace)
            else:
                token = await joined_thread(credential, row.account)
                if not credential_matches(scope, row.account, token):
                    return unknown
                source = _admitted_source(codespace, row.account, token, remaining)
                # Drain the synchronous config child ourselves. The manager's
                # ordinary to_thread call now only reads an already-filled cache.
                await joined_thread(source.get_ssh_config)
                manager = _admitted_manager(admit)
                budget = await admit()
                await asyncio.wait_for(
                    manager.ensure_connected(codespace, source, []), timeout=budget,
                )
            remaining()
            result = await exec_with_retry(
                _AdmittedExec(manager, admit), codespace, wrap_login_shell(batch_command(targets)),
                timeout=30.0, attempts=2,
            )
            remaining()
            return parse_batch(targets, getattr(result, "stdout", ""), getattr(result, "exit_code", None))
        except asyncio.CancelledError:
            raise
        except Exception:
            return unknown
        finally:
            if manager is not None:
                await manager.disconnect(codespace)
