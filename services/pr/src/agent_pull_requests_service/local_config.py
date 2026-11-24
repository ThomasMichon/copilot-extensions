"""Explicit fresh local profile; no Gateway identity or credential enrollment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict
import inspect
import os
import stat

from .config import HostConfig, _fields, _path, _seconds, load_config

if TYPE_CHECKING:
    from .repository_config import RepositoryConfig
    from agent_pull_requests.watch_gateway import WatchScopeBinding
    from agent_pull_requests.watch_observer import NativeObservationBinding
    from agent_pull_requests.watch_replay_store import ReplayStore


class _ActivitySelection(TypedDict, total=False):
    repository_activity: bool
    github_snapshot_batches: bool


class _CoreSelection(TypedDict, total=False):
    native_read_concurrency: int
    github_snapshot_batches: bool


def core(
    *, filtered: bool = False, reviewer_actions: bool = False,
    native_read_concurrency: int | None = None,
    github_snapshot_batches: bool = False,
) -> None:
    """Qualify executing source APIs, without native/provider/credential calls."""
    if native_read_concurrency is not None and (
        type(native_read_concurrency) is not int or not 1 <= native_read_concurrency <= 4
    ):
        raise ValueError("native_read_concurrency must be an integer from 1 to 4")
    try:
        if type(github_snapshot_batches) is not bool or filtered and (reviewer_actions or github_snapshot_batches):
            raise ValueError
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
        if native_read_concurrency is not None:
            from agent_pull_requests.watch_observer import ReplayObserver
            for factory in (WatchService, ReplayObserver):
                parameter = inspect.signature(factory).parameters.get("native_read_concurrency")
                if parameter is None or parameter.kind not in {
                    inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY,
                }:
                    raise ValueError
        if reviewer_actions:
            from agent_pull_requests.repository_subscriptions import contract as reviewer_contract
            capability = reviewer_contract(reviewer_actions=True).get("reviewer_actions")
            if not isinstance(capability, dict) or (
                capability.get("protocol") != "agent-pull-requests.reviewer-actions/v1"
                or capability.get("page_cursor") != "projection-sha256"
            ):
                raise ValueError
        if github_snapshot_batches:
            from agent_pull_requests.watch_observer import NativeObservationBinding
            from agent_pull_requests.watch_replay_store import ReplayStore
            from agent_pull_requests.snapshot_batch_state import PROTOCOL, BatchAdmission, adopt
            if PROTOCOL != "agent-worktrees.pr-snapshot-batch/v1" or not callable(adopt) or (
                not callable(BatchAdmission.begin) or not callable(BatchAdmission.finish)
                or "snapshot_batch" not in inspect.signature(NativeObservationBinding).parameters
                or "github_snapshot_batches" not in inspect.signature(ReplayStore).parameters
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
    reviewer_actions: bool = False
    native_read_concurrency: int | None = None
    github_snapshot_batches: bool = False

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
        options: _CoreSelection = {}
        if self.native_read_concurrency is not None:
            options["native_read_concurrency"] = self.native_read_concurrency
        if self.github_snapshot_batches:
            options["github_snapshot_batches"] = True
        core(
            filtered=self.filtered, reviewer_actions=self.reviewer_actions,
            **options,
        )
        self._private_root()
        if self.filtered:
            return ReplayStore(
                self.root, self.namespace, repository_cache=True, repository_continuing=True,
                repository_filtered=True,
            )
        selected: _ActivitySelection = {"repository_activity": True} if self.reviewer_actions else {}
        if self.github_snapshot_batches:
            selected["github_snapshot_batches"] = True
        return ReplayStore(
            self.root, self.namespace, repository_cache=True, repository_continuing=True,
            **selected,
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
        }, {"interval", "operation_seconds", "drain_timeout", "reviewer_actions",
            "native_read_concurrency", "github_snapshot_batches"})
        filtered = (value["version"], value["profile"]) == (3, "repository-filtered/v1")
        if value["schema"] != "agent-pull-requests.service" or type(value["version"]) is not int or (
            not filtered and (value["version"], value["profile"]) != (2, "repository-local/v1")
        ):
            raise ValueError("unsupported local host profile")
        reviewer_actions = value.get("reviewer_actions", False)
        batches = value.get("github_snapshot_batches", False)
        if type(batches) is not bool:
            raise ValueError("explicit snapshot batch owner selection required")
        if type(reviewer_actions) is not bool:
            raise ValueError("explicit reviewer-action selection required")
        concurrency = value.get("native_read_concurrency")
        if "native_read_concurrency" in value and (
            type(concurrency) is not int or not 1 <= concurrency <= 4
        ):
            raise ValueError("native_read_concurrency must be an integer from 1 to 4")
        options: _CoreSelection = {}
        if concurrency is not None:
            options["native_read_concurrency"] = concurrency
        if batches:
            options["github_snapshot_batches"] = True
        core(
            filtered=filtered, reviewer_actions=reviewer_actions,
            **options,
        )
        from .repository_config import RepositoryConfig
        root = _path(value["root"])
        config = cls(
            root, value["namespace"], RepositoryConfig.parse(
                value["repository_cache"], value["namespace"], root, filtered=filtered,
            ),
            _seconds(value.get("interval", 5)), _seconds(value.get("operation_seconds", 60)),
            _seconds(value.get("drain_timeout", 60)),
            filtered=filtered, reviewer_actions=reviewer_actions,
            native_read_concurrency=concurrency,
            github_snapshot_batches=batches,
        )
        if not batches and any(getattr(binding, "snapshot_batch", None) is not None for binding in config.observations):
            raise ValueError("snapshot batch selection requires its durable owner")
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
