"""Coordinator-owned admission to the shared, optional command sink."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

from process_telemetry import SinkSlot
from process_telemetry.command import Binding, CommandRoutes
from process_telemetry.command_config import CAPABILITY, read_object
from process_telemetry.emission import LEGACY as LEGACY

from .install_paths import install_dir
from .identity import canonicalize_remote


def attach(bus: Any, policy: Any) -> ConsumerTelemetry:
    consumer = ConsumerTelemetry(policy)
    bus._consumer_telemetry = consumer
    return consumer


Lifespan = Callable[[Any], AbstractAsyncContextManager[None]]


def owning_lifespan(consumer: ConsumerTelemetry, serving: Lifespan) -> Lifespan:
    """Keep cleanup outside the existing coordinator's complete startup/stop scope."""
    if consumer.legacy:
        return serving

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        try:
            consumer.start()
            async with serving(app):
                yield
        finally:
            if consumer.owner is not None and consumer.owner.writer is not None:
                cleanup = asyncio.create_task(asyncio.to_thread(consumer.close))
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await cleanup
                    raise
            else:
                consumer.close()

    return lifespan


def capability() -> dict[str, Any]:
    """Describe this executing provider, not the shared library's other adapters."""
    return {
        **{key: list(value) if type(value) is list else value for key, value in CAPABILITY.items()},
        "source_schemas": ["dispatch.lifecycle.v1"],
    }


def load_policy() -> Any:
    """Read only the coordinator's exact local admission file; absence is legacy."""
    try:
        path = install_dir() / "consumer-telemetry.json"
        value = read_object(path, 16384)
        if value is not None:
            return value
        try:
            path.lstat()
        except FileNotFoundError:
            return LEGACY
        return {}
    except (OSError, ValueError, UnicodeError, TypeError, RecursionError):
        return {}


def validate_policy(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) - {"schema_version", "repositories", "service_root"}:
        raise ValueError("invalid admission policy")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("invalid admission version")
    repositories = value.get("repositories", {})
    if type(repositories) is not dict or len(repositories) > 4:
        raise ValueError("invalid repository bindings")
    roots = list(repositories.values())
    normalized = {}
    for repo, root in repositories.items():
        if type(repo) is not str or not repo or len(repo) > 512:
            raise ValueError("invalid repository key")
        lane = canonicalize_remote(repo)
        if not lane or lane in normalized:
            raise ValueError("ambiguous repository key")
        normalized[lane] = root
    if "service_root" in value:
        roots.append(value["service_root"])
    if len(roots) > 4 or any(
        type(root) is not str or not root or len(root) > 4096 or not Path(root).is_absolute()
        for root in roots
    ):
        raise ValueError("invalid admission roots")
    return {
        "schema_version": 1, "repositories": normalized,
        **({"service_root": value["service_root"]} if "service_root" in value else {}),
    }


class ConsumerTelemetry:
    """One coordinator lifetime; task routing follows its immutable stored repo."""

    def __init__(self, policy: Any = LEGACY) -> None:
        self.legacy = policy is LEGACY
        self.owner: CommandRoutes | None = None
        self.bindings: dict[str, Binding] = {}
        self.service: Binding | None = None
        self.unavailable: Binding | None = None
        self.refusal: str | None = None
        self.policy: dict[str, Any] = {}
        self.started = False
        if self.legacy:
            return
        try:
            self.policy = validate_policy(policy)
        except (ValueError, TypeError):
            self.refusal = "invalid_config"

    def start(self) -> None:
        if self.legacy or self.refusal is not None or self.started:
            return
        self.started = True
        try:
            self.owner = SinkSlot().configure_commands(source="agent-dispatch")
            self.unavailable = self.owner.admit(None)
            for repo, root in self.policy["repositories"].items():
                self.bindings[repo] = self.owner.admit(root)
            if "service_root" in self.policy:
                self.service = self.owner.admit(self.policy["service_root"])
        except Exception:
            self.refusal = "unsupported_contract"
            self.bindings.clear()
            self.service = None
            self.close()

    def emit(self, event: dict[str, Any]) -> None:
        if self.owner is None:
            return
        # Fence records are service observations, including failed unauthenticated
        # attempts. Their caller-supplied repo must never select an initiator route.
        repo = event.get("repo")
        binding = self.bindings.get(repo, self.unavailable) if isinstance(repo, str) else self.unavailable
        if event.get("name") == "producer_fence":
            binding = self.service or self.unavailable
        self.owner.slot.emit(event, binding=binding)

    def close(self) -> None:
        if self.owner is not None:
            try:
                self.owner.close()
            except Exception:
                self.refusal = "cleanup_unconfirmed"

    def status(self) -> dict[str, Any]:
        return {
            "legacy": self.legacy, "refusal": self.refusal,
            "states": [binding.state for binding in self.bindings.values()],
            "service_state": self.service.state if self.service is not None else None,
            "counters": self.owner.stats() if self.owner is not None else {},
        }
