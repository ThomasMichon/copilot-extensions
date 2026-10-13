"""Bounded CPU observations for one provider's owned namespace-list reads."""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import time
from concurrent.futures import Future

from agent_procutil.metrics import (
    CpuIssue,
    CpuObservation,
    OwnedProcessCpuSampler,
    ProcessIdentity,
)

from .reader_command import OwnedReaderCommand
from .telemetry import emit

log = logging.getLogger("agent-bridge")

_SAMPLE_JOIN_SECONDS = 0.1
_SAMPLE_INTERVAL_SECONDS = 1.0


class ReaderCpuObservation:
    """Keep the raw sampling holder even after its command or waiter ends."""

    def __init__(self, namespace: str) -> None:
        self._namespace = namespace if re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", namespace) else "other"
        self._sampler: OwnedProcessCpuSampler | None = None
        self._sample_holder: asyncio.Task[CpuObservation] | None = None
        self._next_sample_at = 0.0

    @property
    def pending_sample_future(self) -> Future[CpuObservation] | None:
        sampler = self._sampler
        return sampler.pending_sample_future if sampler is not None else None

    async def _sample(
        self,
        sampler: OwnedProcessCpuSampler,
        root: ProcessIdentity,
        previous: CpuObservation | None,
        *,
        final: bool = False,
    ) -> tuple[CpuObservation | None, CpuIssue | None]:
        holder = self._sample_holder
        if holder is None:
            if time.monotonic() < self._next_sample_at:
                return previous, (
                    CpuIssue(root.pid, "sampling_rate_limited") if previous is None else None
                )
            holder = asyncio.create_task(sampler.sample())
            self._sample_holder = holder
            self._next_sample_at = time.monotonic() + _SAMPLE_INTERVAL_SECONDS
        elif not holder.done() and not final:
            return previous, CpuIssue(root.pid, "sample_pending")
        try:
            observation = await asyncio.wait_for(
                asyncio.shield(holder),
                timeout=_SAMPLE_JOIN_SECONDS,
            )
        except TimeoutError:
            return previous, CpuIssue(root.pid, "sample_pending")
        except Exception:
            log.warning("Namespace reader CPU observation failed", exc_info=True)
            return previous, CpuIssue(root.pid, "sampling_failed")
        else:
            self._sample_holder = None
            return observation, None
        finally:
            self._next_sample_at = time.monotonic() + _SAMPLE_INTERVAL_SECONDS

    async def run(self, cmd: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        owned = OwnedReaderCommand()
        command = asyncio.create_task(asyncio.to_thread(owned.run, cmd, timeout=timeout))
        receipt = asyncio.wrap_future(owned.receipt)
        root: ProcessIdentity | None = None
        observation: CpuObservation | None = None
        issue: CpuIssue | None = None
        try:
            await asyncio.wait({command, receipt}, return_when=asyncio.FIRST_COMPLETED)
            if receipt.done():
                root = receipt.result()
            if root is not None:
                try:
                    if self.pending_sample_future is not None:
                        issue = CpuIssue(root.pid, "previous_sample_pending")
                    else:
                        delay = self._next_sample_at - time.monotonic()
                        if delay > 0 and not command.done():
                            await asyncio.wait({command}, timeout=delay)
                        sampler = OwnedProcessCpuSampler(root)
                        self._sampler = sampler
                        self._sample_holder = None
                        while not command.done():
                            observation, issue = await self._sample(sampler, root, observation)
                            if issue is not None and issue.reason != "sample_pending":
                                break
                            await asyncio.wait({command}, timeout=_SAMPLE_INTERVAL_SECONDS)
                        if issue is None or issue.reason == "sample_pending":
                            observation, issue = await self._sample(
                                sampler,
                                root,
                                observation,
                                final=True,
                            )
                except Exception:
                    log.warning("Namespace reader CPU observation setup failed", exc_info=True)
                    issue = CpuIssue(root.pid, "sampling_failed")
            return await asyncio.shield(command)
        finally:
            issues = list(observation.issues) if observation is not None else []
            if issue is not None:
                issues.append(issue)
            if root is None:
                issues.append(CpuIssue(0, "spawn_receipt_unavailable"))
            if not command.done():
                issues.append(CpuIssue(root.pid if root is not None else 0, "observer_cancelled"))
            if observation is None and not issues:
                issues.append(
                    CpuIssue(root.pid if root is not None else 0, "observation_unavailable")
                )
            emit(
                {
                    "kind": "event",
                    "name": "reader_cpu",
                    "event": "reader_cpu_observation",
                    "plugin": "agent-bridge",
                    "role": "namespace-list",
                    "namespace": self._namespace,
                    "pid": root.pid if root is not None else None,
                    "created_at": root.created_at if root is not None else None,
                    "wall_seconds": owned.wall_seconds if command.done() else None,
                    "observed_cpu_seconds": (
                        observation.observed_cpu_seconds if observation is not None else None
                    ),
                    "coverage": "observed_lower_bound",
                    "truncated": (observation.truncated if observation is not None else False)
                    or len(issues) > 128,
                    "issues": [{"pid": item.pid, "reason": item.reason} for item in issues[:128]],
                }
            )
