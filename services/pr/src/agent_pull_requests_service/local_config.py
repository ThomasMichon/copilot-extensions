"""Explicit fresh local profile; no Gateway identity or credential enrollment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
import inspect
import os
import stat

from .config import HostConfig, _fields, _path, _seconds, load_config

if TYPE_CHECKING:
    from .repository_config import RepositoryConfig
    from agent_pull_requests.watch_gateway import WatchScopeBinding
    from agent_pull_requests.watch_observer import NativeObservationBinding
    from agent_pull_requests.watch_replay_store import ReplayStore


def core(*, filtered: bool = False) -> None:
    """Qualify executing source APIs, without native/provider/credential calls."""
    try:
        if filtered:
            from agent_pull_requests.filtered_subscriptions import contract
        else:
            from agent_pull_requests.repository_subscriptions import contract
        from agent_pull_requests.watch_service import WatchService
        from fleet_manager.local_service_handler import LocalServiceRequest
        if contract().get("storage") != f"repository_cache_version:{3 if filtered else 2}" or (
            "local_socket" not in inspect.signature(WatchService).parameters
            or "response_limit" not in inspect.signature(LocalServiceRequest).parameters
        ):
            raise ValueError
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ValueError("selected local repository core/SDK unsupported") from None


@dataclass(frozen=True)
class LocalHostConfig:
    root: Path
    namespace: str
    repositories: RepositoryConfig
    interval: int = 5
    operation_seconds: int = 60
    drain_timeout: int = 60
    workspaces: None = None
    reports: None = None
    filtered: bool = False

    @property
    def bindings(self) -> tuple[WatchScopeBinding, ...]:
        return self.repositories.bindings

    @property
    def observations(self) -> tuple[NativeObservationBinding, ...]:
        return self.repositories.observations

    @property
    def status_path(self) -> Path:
        return self.root / "service-status.json"

    def store(self) -> ReplayStore:
        from agent_pull_requests.watch_replay_store import ReplayStore
        core(filtered=self.filtered)
        self._private_root()
        if self.filtered:
            return ReplayStore(
                self.root, self.namespace, repository_cache=True, repository_continuing=True,
                repository_filtered=True,
            )
        return ReplayStore(
            self.root, self.namespace, repository_cache=True, repository_continuing=True,
        )

    def _private_root(self) -> None:
        try:
            info = self.root.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(info.st_mode) or (
            info.st_uid != int(getattr(os, "getuid", lambda: -1)())
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ValueError("local repository root must be private and owned by the serving user")

    def provision(self) -> None:
        """Create only fresh selected state; no adoption/migration or provider probing."""
        store = self.store()
        if self.root.is_symlink() or self.root.exists() and any(self.root.iterdir()):
            raise ValueError("local repository profile requires a fresh dedicated state root")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._private_root()
        store.provision()

    @classmethod
    def parse(cls, raw: object) -> LocalHostConfig:
        value = _fields(raw, {
            "schema", "version", "profile", "root", "namespace", "repository_cache",
        }, {"interval", "operation_seconds", "drain_timeout"})
        filtered = (value["version"], value["profile"]) == (3, "repository-filtered/v1")
        if value["schema"] != "agent-pull-requests.service" or type(value["version"]) is not int or (
            not filtered and (value["version"], value["profile"]) != (2, "repository-local/v1")
        ):
            raise ValueError("unsupported local host profile")
        core(filtered=filtered)
        from .repository_config import RepositoryConfig
        root = _path(value["root"])
        config = cls(
            root, value["namespace"], RepositoryConfig.parse(
                value["repository_cache"], value["namespace"], root, filtered=filtered,
            ),
            _seconds(value.get("interval", 5)), _seconds(value.get("operation_seconds", 60)),
            _seconds(value.get("drain_timeout", 60)),
            filtered=filtered,
        )
        store = config.store()
        from single_instance_lease import SingleInstance
        writer = SingleInstance(config.root, service="agent-pull-requests-watch")
        if config.repositories.socket_path in {
            config.status_path, store.path, store._lock().path,
            config.root / "watch-subscriptions.json", writer.lock_path,
        }:
            raise ValueError("local repository socket collides with readiness or storage ownership")
        return config


def load_service_config(path: Path) -> HostConfig | LocalHostConfig:
    """Preserve the legacy loader while selecting the explicit optional local profile."""
    from agent_pull_requests.watch_replay_store import json_object
    with _path(str(path)).open("rb") as stream:
        raw = stream.read(1_048_577)
    if len(raw) > 1_048_576:
        raise ValueError("host configuration exceeds its byte budget")
    value: dict[str, Any] = json_object(raw)
    if "profile" in value:
        return LocalHostConfig.parse(value)
    return load_config(path)
