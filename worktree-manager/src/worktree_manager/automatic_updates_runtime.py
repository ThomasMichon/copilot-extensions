"""Single scheduling owner; bounded cycles through the installed update command."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import secrets
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from agent_procutil import DaemonSpawnError
from agent_procutil.daemon_broker import WindowsDaemonProcess
from agent_procutil.daemon_instances import daemon_entrypoint
from zdd.cutover_lock import CutoverLock, CutoverLockedError

from . import automatic_updates_process as process
from .automatic_updates_policy import PolicyError, atomic_json, load_policy, state_dir

log = logging.getLogger(__name__)
POLL_SECONDS = 5.0
CYCLE_BUDGET_SECONDS = 3600.0


class Worker:
    def __init__(self, root: Path, *, standby: str | None = None) -> None:
        self.root = root
        self.standby = standby
        self.lease = CutoverLock(state_dir(root) / "worker-lease")
        self_cycle = os.environ.get("WORKTREE_MANAGER_AUTOMATIC_UPDATES_SELF_CYCLE", "1")
        if self_cycle not in ("0", "1"):
            raise PolicyError("WORKTREE_MANAGER_AUTOMATIC_UPDATES_SELF_CYCLE must be 0 or 1")
        self.snapshot: dict[str, Any] = {
            "worker_id": secrets.token_hex(16), "cookie": secrets.token_hex(32),
            "pid": os.getpid(), "runtime": process.runtime_identity(),
            "state": "starting", "next_due": time.time(), "failures": 0,
            "self_cycle_enabled": self_cycle == "1",
        }
        self.restart_after = 0.0
        self.stop_requested = asyncio.Event()
        self._native_candidate: subprocess.Popen | WindowsDaemonProcess | None = None

    def request_stop(self) -> None:
        self.snapshot["stop_requested"] = True
        if self.snapshot["state"] in ("updating", "draining-overdue-update"):
            self.snapshot.update(state="draining-update", reason="native stop requested; preserving active update")
        self.stop_requested.set()

    def publish(self) -> None:
        atomic_json(state_dir(self.root) / process.STATUS_NAME, self.snapshot)

    def resume_schedule(self, previous: dict[str, Any]) -> None:
        for key in ("next_due", "cycle_finished"):
            if key not in previous:
                continue
            value = previous[key]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise PolicyError(f"invalid saved maintenance schedule field: {key}")
            if key == "next_due" and value > time.time() + 86400:
                raise PolicyError("saved next maintenance cycle exceeds the maximum cadence")
            self.snapshot[key] = value
        for key in ("failures", "last_exit_code"):
            if key not in previous:
                continue
            value = previous[key]
            if type(value) is not int or (key == "failures" and value < 0):
                raise PolicyError(f"invalid saved maintenance schedule field: {key}")
            self.snapshot[key] = value

    async def health(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=2)
            request = json.loads(raw)
            if (
                isinstance(request, dict)
                and secrets.compare_digest(str(request.get("cookie", "")), self.snapshot["cookie"])
                and isinstance(request.get("challenge"), str)
            ):
                response = {k: v for k, v in self.snapshot.items() if k != "cookie"}
                response["challenge"] = request["challenge"]
                writer.write(json.dumps(response).encode() + b"\n")
                await asyncio.wait_for(writer.drain(), timeout=2)
        except (OSError, ValueError, TimeoutError, asyncio.LimitOverrunError):
            log.debug("invalid maintenance health request", exc_info=True)
        finally:
            writer.close()
            await writer.wait_closed()

    async def acquire(self) -> bool:
        deadline = time.monotonic() + (20 if self.standby else 0)
        while True:
            try:
                self.lease.acquire()
                return True
            except CutoverLockedError:
                if time.monotonic() >= deadline:
                    return False
                await asyncio.sleep(0.1)

    async def cycle(self) -> None:
        """Own no installer authority: invoke the selected canonical CLI once.

        A slow installation drains rather than being killed mid-publication.
        Its CLI owns the execution lock even if this scheduler is lost.
        """
        policy = load_policy(self.root)
        if policy.owner != "manager":
            return
        argv = process.selected_command(
            self.root, ["update", "--automatic-trigger", "manager",
                        *(["--project", policy.project] if policy.project else [])],
        )
        self.snapshot.update(state="updating", cycle_started=time.time())
        self.publish()
        child = await asyncio.create_subprocess_exec(
            *argv, cwd=self.root, env=process.update_environment(self.root),
            stdin=asyncio.subprocess.DEVNULL, **process.child_kwargs(),
        )
        try:
            rc = await asyncio.wait_for(
                asyncio.shield(child.wait()), timeout=CYCLE_BUDGET_SECONDS,
            )
        except TimeoutError:
            self.snapshot.update(
                state="draining-overdue-update",
                reason="update exceeded its cycle budget; preserving active installation",
            )
            self.publish()
            rc = await child.wait()
        failures = 0 if rc == 0 else int(self.snapshot["failures"]) + 1
        delay = policy.interval_seconds if rc == 0 else min(
            86400, max(policy.interval_seconds, 300 * 2 ** min(failures - 1, 8)),
        )
        self.snapshot.update(
            state="waiting" if rc == 0 else "deferred",
            last_exit_code=rc, cycle_finished=time.time(), failures=failures,
            next_due=time.time() + delay,
            reason=None if rc == 0 else f"canonical update returned {rc}; see update telemetry/log",
        )
        self.publish()

    async def replace_if_stale(self) -> bool:
        if not self.snapshot["self_cycle_enabled"]:
            return False
        if time.time() < self.restart_after:
            return False
        if str(process.selected_slot(self.root).resolve()) == self.snapshot["runtime"]:
            return False
        from .automatic_updates_autostart import native_supervisor_state

        supervisor = await asyncio.to_thread(native_supervisor_state, self.root)
        if supervisor == "ambiguous":
            raise PolicyError("native supervisor identity is ambiguous; worker replacement deferred")
        if supervisor == "systemd":
            return await self.handover(native_restart=True)
        return await self.handover()

    def _defer_replacement(self, reason: str, *, state: str = "replacement-deferred") -> bool:
        self.snapshot.update(state=state, reason=reason)
        self.restart_after = time.time() + 300
        self.publish()
        return False

    async def _prepare_standby(self) -> None:
        from .automatic_updates_autostart import UNIT_ENV, inspect_autostart_refresh

        registration = await asyncio.to_thread(inspect_autostart_refresh, self.root)
        if registration.get("registered") or os.environ.get(UNIT_ENV):
            if not registration.get("registered") or registration.get("activation") == "blocked":
                raise PolicyError(f"successor startup refresh unavailable: {registration.get('blockers')}")
        previous = process.read_snapshot(state_dir(self.root) / process.STATUS_NAME)
        if previous is not None:
            self.resume_schedule(previous)

    async def handover(self, *, native_restart: bool = False) -> bool:
        """Qualify a standby before releasing scheduling custody; recover on failure."""
        if native_restart and self._native_candidate is not None and self._native_candidate.poll() is None:
            return self._defer_replacement("previous native successor qualification remains unsettled")
        selected = process.selected_slot(self.root).resolve() if native_restart else None
        if selected is not None and str(selected) == self.snapshot["runtime"]:
            return False
        if native_restart:
            self.snapshot.update(state="qualifying-native-replacement")
            self.publish()
        nonce = secrets.token_hex(16)
        candidate_path = state_dir(self.root) / f"candidate-{nonce}.json"
        try:
            child = await asyncio.to_thread(process.spawn_worker, self.root, standby=nonce)
        except (DaemonSpawnError, OSError, PolicyError, ValueError) as exc:
            log.exception("maintenance successor launch deferred; retaining current controller")
            return self._defer_replacement(f"successor launch failed: {exc}")
        if native_restart:
            self._native_candidate = child
        deadline = time.monotonic() + 15
        ready = None
        while time.monotonic() < deadline:
            ready = await asyncio.to_thread(process.probe, process.read_snapshot(candidate_path))
            if ready is not None:
                break
            if child.poll() is not None:
                break
            await asyncio.sleep(0.1)
        if ready is None or ready.get("runtime") != str(
            selected if native_restart else process.selected_slot(self.root).resolve()
        ):
            return self._defer_replacement("successor readiness failed")
        if native_restart:
            # The old lease stays held: the standby can qualify startup, but
            # must finish its bounded non-owning attempt before systemd restarts.
            try:
                code = await asyncio.to_thread(child.wait, timeout=25)
            except (OSError, subprocess.TimeoutExpired, TimeoutError) as exc:
                return self._defer_replacement(f"native successor qualification did not settle: {exc}")
            if code != 0:
                return self._defer_replacement(f"native successor qualification exited {code}")
            if self.stop_requested.is_set() or load_policy(self.root).owner != "manager":
                self.snapshot["state"] = "drained" if self.stop_requested.is_set() else "disabled"
                self.publish()
                return True
            if process.selected_slot(self.root).resolve() != selected:
                return self._defer_replacement("Manager selection changed during native qualification")
            self.snapshot.update(state="native-restart-requested")
            self.publish()
            self.lease.release()
            return True
        self.lease.release()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = await asyncio.to_thread(process.worker_status, self.root)
            if (
                status["running"] and status.get("worker_id") != self.snapshot["worker_id"]
                and status.get("runtime") == str(process.selected_slot(self.root).resolve())
            ):
                return True
            if child.poll() is not None:
                break
            await asyncio.sleep(0.1)
        try:
            self.lease.acquire()
        except CutoverLockedError:
            raise PolicyError("successor owns the lease but failed its active readiness probe")
        return self._defer_replacement(
            "successor failed activation; old worker retained", state="replacement-recovered",
        )

    @daemon_entrypoint(
        "worktree-manager-automatic-updates", source=__file__,
        runtime_root=lambda self: self.root, purpose="production",
    )
    async def run(self) -> int:
        if load_policy(self.root).owner != "manager":
            raise PolicyError("Manager automatic maintenance is not enabled")
        server = await asyncio.start_server(self.health, "127.0.0.1", 0, limit=16384)
        self.snapshot["port"] = server.sockets[0].getsockname()[1]
        candidate_path = None
        loop = asyncio.get_running_loop()
        previous_signals = {}
        try:
            if os.name != "nt":
                for sig in (signal.SIGTERM, signal.SIGINT):
                    previous_signals[sig] = signal.getsignal(sig)
                    loop.add_signal_handler(sig, self.request_stop)
            if self.standby:
                if not all(c in "0123456789abcdef" for c in self.standby) or len(self.standby) != 32:
                    raise PolicyError("invalid standby identity")
                await self._prepare_standby()
                candidate_path = state_dir(self.root) / f"candidate-{self.standby}.json"
                self.snapshot["state"] = "standby"
                atomic_json(candidate_path, self.snapshot)
            if not await self.acquire():
                return 0
            from .automatic_updates_autostart import inspect_autostart, register_autostart

            registration = await asyncio.to_thread(inspect_autostart, self.root)
            if registration.get("registered") is True:
                refreshed = await asyncio.to_thread(register_autostart, self.root)
                if refreshed.get("activation") == "blocked":
                    raise PolicyError(f"owned startup refresh failed: {refreshed.get('blockers')}")
            previous = process.read_snapshot(state_dir(self.root) / process.STATUS_NAME)
            if previous is not None:
                self.resume_schedule(previous)
            self.snapshot["state"] = "waiting"
            self.publish()
            while True:
                try:
                    if self.stop_requested.is_set():
                        self.snapshot["state"] = "drained"
                        self.publish()
                        return 0
                    if load_policy(self.root).owner != "manager":
                        self.snapshot["state"] = "disabled"
                        self.publish()
                        return 0
                    if await self.replace_if_stale():
                        return process.RESTART_EXIT if self.snapshot["state"] == "native-restart-requested" else 0
                    if time.time() >= self.snapshot["next_due"]:
                        await self.cycle()
                except (OSError, PolicyError, ValueError) as exc:
                    log.exception("maintenance deferred")
                    if not self.lease.held:
                        # A successor may own scheduling even if its endpoint failed.
                        # Never resume work after losing exclusive custody.
                        return 1
                    self.snapshot.update(
                        state="blocked", reason=str(exc), next_due=time.time() + 300,
                    )
                    self.restart_after = time.time() + 300
                    self.publish()
                try:
                    await asyncio.wait_for(self.stop_requested.wait(), timeout=POLL_SECONDS)
                except TimeoutError:
                    pass
        finally:
            self.lease.release()
            server.close()
            await server.wait_closed()
            if candidate_path is not None:
                candidate_path.unlink(missing_ok=True)
            for sig, previous in previous_signals.items():
                loop.remove_signal_handler(sig)
                signal.signal(sig, previous)


def run_worker(root: Path, *, standby: str | None = None) -> int:
    return asyncio.run(Worker(root, standby=standby).run())
