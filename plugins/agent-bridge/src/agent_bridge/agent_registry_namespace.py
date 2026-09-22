"""Namespace-resolver contracts and CLI-backed implementations."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import shutil
import subprocess
import time
from abc import ABC, abstractmethod

from agent_procutil import no_window_flags

from .agent_registry_common import (
    NamespaceAgentInfo,
    _namespace_list_ttl,
)
from .transport import PluginRef, SpawnTarget

log = logging.getLogger("agent-bridge")

_NS_NOT_FOUND_EXIT = 3
_NS_BAD_STATE_EXIT = 4


class NamespaceResolver(ABC):
    """Pluggable resolver for a namespace of agents."""

    @property
    @abstractmethod
    def prefix(self) -> str:
        """The namespace prefix this resolver handles (e.g. ``codespace``)."""
        ...

    @abstractmethod
    async def resolve(
        self,
        name: str,
        *,
        extra_plugins: list[PluginRef] = (),
        repo: str | None = None,
        repo_remote: str | None = None,
    ) -> SpawnTarget:
        """Resolve a bare name (without prefix) to a SpawnTarget."""
        ...

    @abstractmethod
    async def list(self) -> list[NamespaceAgentInfo]:
        """Enumerate available agents in this namespace."""
        ...

    @property
    def bare_addressable(self) -> bool:
        """Whether this namespace participates in bare-name resolution."""
        return True

    async def ensure_ready(self, name: str) -> None:
        """Optional hook called before ``resolve()``."""

    async def target_repo(self, name: str) -> str | None:
        """The workspace repo this target hosts, or ``None``."""
        return None


class CliNamespaceResolver(NamespaceResolver):
    """Drive a namespace provider over a process boundary."""

    def __init__(
        self,
        prefix: str,
        binstub: str,
        fallback: NamespaceResolver | None = None,
        *,
        command: list[str] | None = None,
    ) -> None:
        self._prefix = prefix
        self._binstub = binstub
        self._fallback = fallback
        self._command = list(command) if command else None
        self._list_cache: tuple[float, list[NamespaceAgentInfo]] | None = None

    def invalidate_list_cache(self) -> None:
        """Drop the cached ``list()`` result so the next call re-queries live."""
        self._list_cache = None

    @property
    def prefix(self) -> str:
        return self._prefix

    @property
    def bare_addressable(self) -> bool:
        if self._fallback is not None:
            return self._fallback.bare_addressable
        return True

    async def _run(
        self, argv: list[str], *, timeout: float = 90.0,
    ) -> tuple[int, str, str] | None:
        """Run ``<binstub> <argv...>`` or return ``None`` if launch failed."""
        if self._command:
            cmd = [*self._command, *argv]
        else:
            exe = shutil.which(self._binstub)
            if not exe:
                return None
            cmd = [exe, *argv]

        def _call() -> subprocess.CompletedProcess[str]:
            creationflags = no_window_flags()
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=creationflags,
            )

        try:
            result = await asyncio.to_thread(_call)
            return result.returncode, result.stdout, result.stderr
        except Exception:
            log.debug(
                "CLI namespace call failed: %s %s",
                self._binstub,
                argv,
                exc_info=True,
            )
            return None

    async def _fallback_or_raise(self, method: str, *args, **kwargs):
        if self._fallback is None:
            raise RuntimeError(
                f"{self._binstub} {method} unavailable and no in-process "
                f"fallback resolver for namespace '{self._prefix}:'"
            )
        fn = getattr(self._fallback, method)
        if kwargs:
            try:
                params = inspect.signature(fn).parameters
                kwargs = {k: v for k, v in kwargs.items() if k in params}
            except (TypeError, ValueError):
                kwargs = {}
        return await fn(*args, **kwargs)

    async def list(self, *, timeout: float | None = None) -> list[NamespaceAgentInfo]:
        """Enumerate this namespace's agents."""
        ttl = _namespace_list_ttl()
        if ttl > 0 and self._list_cache is not None:
            stamped_at, cached = self._list_cache
            if (time.monotonic() - stamped_at) <= ttl:
                return cached

        run_kwargs = {} if timeout is None else {"timeout": timeout}
        res = await self._run(["namespace-list"], **run_kwargs)
        if res is not None and res[0] == 0:
            try:
                agents = [NamespaceAgentInfo(**doc) for doc in json.loads(res[1])]
            except Exception:
                log.warning(
                    "namespace-list output unparseable (%s) -- falling back",
                    self._binstub,
                    exc_info=True,
                )
            else:
                if ttl > 0:
                    self._list_cache = (time.monotonic(), agents)
                return agents
        if self._fallback is None:
            log.debug(
                "namespace '%s:': provider '%s' unavailable and no in-process "
                "fallback -- contributing no dynamic agents",
                self._prefix,
                self._binstub,
            )
            return []
        return await self._fallback_or_raise("list")

    async def resolve(
        self,
        name: str,
        *,
        extra_plugins: list[PluginRef] = (),
        repo: str | None = None,
        repo_remote: str | None = None,
    ) -> SpawnTarget:
        """Resolve via the CLI seam (full-capability signature)."""
        return await self._resolve_impl(
            name,
            extra_plugins=extra_plugins,
            repo=repo,
            repo_remote=repo_remote,
        )

    async def _resolve_impl(
        self,
        name: str,
        *,
        extra_plugins: list[PluginRef] = (),
        repo: str | None = None,
        repo_remote: str | None = None,
    ) -> SpawnTarget:
        argv = ["namespace-resolve", name]
        if repo:
            argv += ["--repo", repo]
        if repo_remote:
            argv += ["--repo-remote", repo_remote]
        for plugin in extra_plugins or ():
            source = getattr(plugin, "source", None)
            if source:
                argv += ["--stage-plugin", source]
        res = await self._run(argv)
        if res is not None:
            rc, out, err = res
            if rc == _NS_NOT_FOUND_EXIT:
                raise KeyError(err.strip() or name)
            if rc == _NS_BAD_STATE_EXIT:
                raise ValueError(err.strip() or f"{name} is not spawnable")
            if rc == 0:
                try:
                    spec = json.loads(out)
                except Exception:
                    log.warning(
                        "namespace-resolve output unparseable (%s) -- falling "
                        "back",
                        self._binstub,
                        exc_info=True,
                    )
                else:
                    raw_venue = spec.get("venue")
                    if raw_venue is not None and not isinstance(raw_venue, dict):
                        raise RuntimeError(
                            f"{self._binstub} namespace-resolve returned a "
                            "non-object venue contract"
                        )
                    venue = dict(raw_venue or {})
                    workspace = spec.get("workspace_folder")
                    security_profile = spec.get("security_profile")
                    if workspace is not None and (
                        not isinstance(workspace, str) or not workspace.strip()
                    ):
                        raise RuntimeError(
                            f"{self._binstub} namespace-resolve returned an "
                            "invalid workspace_folder"
                        )
                    if security_profile is not None and security_profile not in {
                        "trusted",
                        "restricted",
                    }:
                        raise RuntimeError(
                            f"{self._binstub} namespace-resolve returned an "
                            "invalid security_profile"
                        )
                    for key in (
                        "provider",
                        "kind",
                        "target_id",
                        "scope",
                        "fleet",
                        "workspace_folder",
                        "security_profile",
                        "configured_security_profile",
                        "observed_security_profile",
                        "effective_security_profile",
                        "state",
                        "transport",
                    ):
                        value = venue.get(key)
                        if value is not None and (
                            not isinstance(value, str) or not value.strip()
                        ):
                            raise RuntimeError(
                                f"{self._binstub} namespace-resolve venue.{key} "
                                "must be a non-empty string"
                            )
                    instance_id = venue.get("instance_id")
                    if instance_id is not None and (
                        not isinstance(instance_id, str) or not instance_id.strip()
                    ):
                        raise RuntimeError(
                            f"{self._binstub} namespace-resolve venue.instance_id "
                            "must be null or a non-empty string"
                        )
                    for key in ("ready", "posture_verified"):
                        if key in venue and not isinstance(venue[key], bool):
                            raise RuntimeError(
                                f"{self._binstub} namespace-resolve venue.{key} "
                                "must be boolean"
                            )
                    if "schema_version" in venue and (
                        not isinstance(venue["schema_version"], int)
                        or isinstance(venue["schema_version"], bool)
                        or venue["schema_version"] < 1
                    ):
                        raise RuntimeError(
                            f"{self._binstub} namespace-resolve "
                            "venue.schema_version must be a positive integer"
                        )
                    capabilities = venue.get("capabilities")
                    if capabilities is not None and (
                        not isinstance(capabilities, dict)
                        or not all(
                            isinstance(capability, str)
                            and capability
                            and isinstance(enabled, bool)
                            for capability, enabled in capabilities.items()
                        )
                    ):
                        raise RuntimeError(
                            f"{self._binstub} namespace-resolve "
                            "venue.capabilities must be boolean flags"
                        )
                    if workspace:
                        venue_workspace = venue.get("workspace_folder")
                        if venue_workspace and venue_workspace != workspace:
                            raise RuntimeError(
                                f"{self._binstub} namespace-resolve returned "
                                "conflicting workspace_folder values"
                            )
                        venue["workspace_folder"] = workspace
                    if security_profile:
                        venue_profile = venue.get("security_profile")
                        if venue_profile and venue_profile != security_profile:
                            if "restricted" not in {venue_profile, security_profile}:
                                raise RuntimeError(
                                    f"{self._binstub} namespace-resolve returned "
                                    "conflicting security_profile values"
                                )
                            venue["security_profile"] = "restricted"
                            venue["ready"] = False
                        else:
                            venue["security_profile"] = security_profile
                    target_type = spec.get("type", "command")
                    spawn_command = spec.get("spawn_command")
                    if target_type != "command":
                        raise RuntimeError(
                            f"{self._binstub} namespace-resolve returned "
                            f"unsupported target type {target_type!r}"
                        )
                    if (
                        not isinstance(spawn_command, list)
                        or not spawn_command
                        or not all(
                            isinstance(part, str) and part and "\x00" not in part
                            for part in spawn_command
                        )
                    ):
                        raise RuntimeError(
                            f"{self._binstub} namespace-resolve returned an "
                            "invalid spawn_command"
                        )
                    self.invalidate_list_cache()
                    return SpawnTarget(
                        type=target_type,
                        spawn_command=spawn_command,
                        user=spec.get("user"),
                        codespace=spec.get("codespace"),
                        container=spec.get("container"),
                        venue=venue or None,
                    )
        return await self._fallback_or_raise(
            "resolve",
            name,
            extra_plugins=extra_plugins,
            repo=repo,
            repo_remote=repo_remote,
        )

    async def ensure_ready(self, name: str) -> None:
        res = await self._run(["namespace-ensure-ready", name])
        if res is None:
            await self._fallback_or_raise("ensure_ready", name)
            return
        rc, _out, err = res
        if rc == 0:
            self.invalidate_list_cache()
            return
        raise RuntimeError(err.strip() or f"{self._prefix}:{name} is not ready")

    async def target_repo(self, name: str) -> str | None:
        res = await self._run(["namespace-target-repo", name])
        if res is not None and res[0] == 0:
            return res[1].strip() or None
        if self._fallback is not None:
            return await self._fallback.target_repo(name)
        return None


class RestrictedCliNamespaceResolver(CliNamespaceResolver):
    """CLI-backed namespace resolver without cross-repo / plugin kwargs."""

    async def resolve(self, name: str) -> SpawnTarget:  # type: ignore[override]
        return await self._resolve_impl(name)
