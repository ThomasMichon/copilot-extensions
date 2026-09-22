"""AgentResolver implementation extracted from the registry composition root."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import replace
from typing import Any

from dropin_registry import Finding, WarningTracker

from .agent_registry_common import (
    AgentConfig,
    AmbiguousAgentError,
)
from .agent_registry_namespace import (
    CliNamespaceResolver,
    NamespaceResolver,
    RestrictedCliNamespaceResolver,
)
from .cold_store_sources import ColdStoreProviderRegistry
from .provider_sources import ProviderManifest, scan_provider_registry
from .topology import MachineConfig, SshEnvironment
from .transport import PluginRef, SpawnTarget

log = logging.getLogger("agent-bridge")


class AgentResolver:
    """Resolves agent names to SpawnTargets using topology + registry."""

    def __init__(
        self,
        agents: dict[str, AgentConfig],
        machines: dict[str, MachineConfig],
        *,
        topology_errors: list[str] | None = None,
        topology_warnings: list[str] | None = None,
    ) -> None:
        from . import agent_registry as compat

        self._agents = agents
        self._machines = machines
        self._topology_errors = list(topology_errors or [])
        self._topology_warnings = list(topology_warnings or [])
        self._namespace_resolvers: dict[str, NamespaceResolver] = {}
        self._provider_scan_ts: float = 0.0
        self._provider_scan_ttl: float = 10.0
        self._provider_entries: dict[str, ProviderManifest] = {}
        self._provider_manifests: dict[str, ProviderManifest] = {}
        self._provider_namespaces: set[str] = set()
        self._provider_warning_tracker = WarningTracker()
        self.cold_store = ColdStoreProviderRegistry()
        self._alias_index: dict[str, tuple[MachineConfig, SshEnvironment]] = {}
        for machine in machines.values():
            for env in machine.ssh_environments:
                if env.alias in self._alias_index:
                    log.warning(
                        "Duplicate SSH alias '%s' (machines '%s' and '%s')",
                        env.alias,
                        self._alias_index[env.alias][0].key,
                        machine.key,
                    )
                else:
                    self._alias_index[env.alias] = (machine, env)

        self._agent_alias_index: dict[str, str | None] = {}
        for canonical, config in agents.items():
            for alias in [canonical, *config.aliases]:
                key = alias.casefold()
                existing = self._agent_alias_index.get(key)
                if existing is None and key in self._agent_alias_index:
                    continue
                if existing is not None and existing != canonical:
                    self._agent_alias_index[key] = None
                    warning = (
                        f"agent alias {alias!r} is ambiguous between "
                        f"{existing!r} and {canonical!r}"
                    )
                    self._topology_warnings.append(warning)
                    log.warning(warning)
                else:
                    self._agent_alias_index[key] = canonical

        self._local_machine, self._local_platform = compat._detect_local_machine(
            machines,
        )

    @property
    def agents(self) -> dict[str, AgentConfig]:
        return self._agents

    @property
    def machines(self) -> dict[str, MachineConfig]:
        return self._machines

    @property
    def topology_errors(self) -> list[str]:
        return list(self._topology_errors)

    @property
    def topology_warnings(self) -> list[str]:
        return list(self._topology_warnings)

    def canonical_agent_name(self, name: str) -> str | None:
        """Resolve an exact, case-insensitive, or declared static alias."""
        if name in self._agents:
            return name
        return self._agent_alias_index.get(name.casefold())

    def get_agent_config(self, name: str) -> AgentConfig | None:
        canonical = self.canonical_agent_name(name)
        return self._agents.get(canonical) if canonical else None

    def machine_key_for_agent(self, config: AgentConfig) -> str | None:
        """Return the normalized topology key hosting ``config``."""
        if config.host:
            try:
                machine, _env = self._resolve_machine(config.host, config.ssh_environment)
                return machine.key
            except ValueError:
                return None
        if config.auto_discovered and self._local_machine:
            return self._local_machine.key
        return None

    def register_namespace_resolver(self, resolver: NamespaceResolver) -> None:
        """Register a namespace resolver for prefixed agent names."""
        prefix = resolver.prefix
        if prefix in self._namespace_resolvers:
            raise ValueError(f"Namespace resolver for '{prefix}:' already registered")
        self._namespace_resolvers[prefix] = resolver
        log.info("Registered namespace resolver: %s:", prefix)

    def unregister_namespace_resolver(self, prefix: str) -> bool:
        """Remove a namespace resolver. Returns True if it existed."""
        removed = self._namespace_resolvers.pop(prefix, None)
        if removed:
            log.info("Unregistered namespace resolver: %s:", prefix)
        return removed is not None

    @property
    def namespace_resolvers(self) -> dict[str, NamespaceResolver]:
        """Read-only view of registered namespace resolvers."""
        return dict(self._namespace_resolvers)

    def refresh_provider_resolvers(self, *, force: bool = False) -> None:
        """Register namespace resolvers from the ``providers.d`` manifest registry."""
        now = time.monotonic()
        if not force and (now - self._provider_scan_ts) < self._provider_scan_ttl:
            return
        self._provider_scan_ts = now

        try:
            report = scan_provider_registry(previous=self._provider_entries)
        except Exception:
            log.warning("Provider manifest discovery failed", exc_info=True)
            return

        findings = list(report.findings)
        for manifest in report.manifests.values():
            if (
                manifest.namespace in self._namespace_resolvers
                and manifest.namespace not in self._provider_namespaces
            ):
                findings.append(
                    Finding(
                        registry="providers.d",
                        entry=manifest.source_path,
                        status="inactive",
                        reason="duplicate",
                        target=manifest.namespace,
                        owner=manifest.plugin,
                        remedy="Remove the provider entry or rename its namespace.",
                        detail="namespace conflicts with a built-in resolver",
                    )
                )
        warning_batch = self._provider_warning_tracker.select(findings)
        for finding in warning_batch.emitted:
            target = f" target={finding.target}" if finding.target else ""
            log.warning(
                "%s: %s (%s)%s; run `agent-bridge doctor`",
                finding.registry,
                finding.entry,
                finding.reason,
                target,
            )
        if warning_batch.suppressed:
            log.warning(
                "providers.d: %d additional finding(s) suppressed; "
                "run `agent-bridge doctor`",
                warning_batch.suppressed,
            )
        if warning_batch.recovered:
            log.info(
                "providers.d: %d prior finding(s) recovered",
                warning_batch.recovered,
            )

        desired = dict(report.manifests)
        desired_namespaces = set(desired)
        for namespace in sorted(self._provider_namespaces - desired_namespaces):
            self.unregister_namespace_resolver(namespace)
            self._provider_manifests.pop(namespace, None)

        for manifest in desired.values():
            namespace = manifest.namespace
            current = self._provider_manifests.get(namespace)
            if current == manifest and namespace in self._provider_namespaces:
                continue
            if namespace in self._provider_namespaces:
                self.unregister_namespace_resolver(namespace)
                self._provider_namespaces.discard(namespace)
                self._provider_manifests.pop(namespace, None)
            elif namespace in self._namespace_resolvers:
                continue
            cls = RestrictedCliNamespaceResolver if manifest.restricted else CliNamespaceResolver
            try:
                self.register_namespace_resolver(
                    cls(
                        manifest.namespace,
                        manifest.command[0],
                        command=list(manifest.command),
                    )
                )
                self._provider_namespaces.add(namespace)
                self._provider_manifests[namespace] = manifest
                log.info(
                    "Registered %s: namespace resolver from %s",
                    namespace,
                    manifest.source_path,
                )
            except Exception:
                log.warning(
                    "Failed to register '%s:' from %s",
                    manifest.namespace,
                    manifest.source_path,
                    exc_info=True,
                )
        self._provider_entries = dict(report.entries)

    def _parse_namespaced_agent(
        self, agent_name: str,
    ) -> tuple[str, str] | None:
        """Split ``prefix:name`` into ``(prefix, name)``."""
        if ":" not in agent_name:
            return None
        prefix, _, name = agent_name.partition(":")
        if prefix in self._namespace_resolvers and name:
            return prefix, name
        return None

    def _resolve_machine(
        self, host: str, ssh_environment: str | None = None,
    ) -> tuple[MachineConfig, SshEnvironment | None]:
        """Resolve a host to a machine, checking keys then SSH aliases."""
        machine = self._machines.get(host)
        if machine:
            return machine, None

        entry = self._alias_index.get(host)
        if entry:
            machine, matched_env = entry
            if ssh_environment and ssh_environment != matched_env.name:
                raise ValueError(
                    f"Host '{host}' resolved via SSH alias to machine "
                    f"'{machine.key}' environment '{matched_env.name}', "
                    f"but agent config specifies ssh_environment="
                    f"'{ssh_environment}' (conflict)"
                )
            return machine, matched_env

        raise ValueError(f"Machine '{host}' not found by key or SSH alias in topology")

    def resolve_ssh_environment(
        self, host: str, ssh_environment: str | None = None,
    ) -> tuple[MachineConfig, SshEnvironment]:
        """Resolve a topology machine key or SSH alias to one exact environment."""
        machine, forced_env = self._resolve_machine(host, ssh_environment)
        environment = forced_env or machine.get_ssh_env(ssh_environment)
        if environment is None:
            raise ValueError(f"Machine '{machine.key}' has no SSH environment")
        return machine, environment

    def resolve(self, agent_name: str) -> SpawnTarget:
        """Resolve an agent name to a SpawnTarget (sync path)."""
        ns = self._parse_namespaced_agent(agent_name)
        if ns:
            raise ValueError(
                f"Agent '{agent_name}' uses namespace '{ns[0]}:' -- "
                "use resolve_async() for namespaced agents"
            )
        return self._resolve_static(agent_name)

    async def resolve_async(
        self, agent_name: str, sender_repo: str | None = None,
    ) -> SpawnTarget:
        """Resolve an agent name to a SpawnTarget (async path)."""
        from . import agent_registry as compat

        self.refresh_provider_resolvers()
        ns = self._parse_namespaced_agent(agent_name)
        if ns:
            prefix, name = ns
            resolver = self._namespace_resolvers[prefix]
            log.info(
                "Resolving namespaced agent %s:%s via %s resolver",
                prefix,
                name,
                prefix,
            )
            await resolver.ensure_ready(name)
            return await self._resolve_with_plugins(resolver, name)

        repo, venue = compat._split_repo_venue(agent_name)
        if repo is not None:
            static_name = self.canonical_agent_name(agent_name)
            if static_name:
                return await self._resolve_bare(static_name)
            return await self._resolve_venue_bound(repo, venue)

        candidates = await self._gather_bare_candidates(agent_name)
        if len(candidates) > 1:
            raise AmbiguousAgentError(
                agent_name,
                [qualified for qualified, _, _ in candidates],
            )
        if len(candidates) == 1:
            _, resolver, resolve_name = candidates[0]
            if resolver is None:
                cfg = self._agents.get(resolve_name)
                if (
                    sender_repo
                    and cfg is not None
                    and cfg.derived
                    and cfg.host
                    and sender_repo != cfg.project
                ):
                    log.info(
                        "Bare machine venue '%s' -> sender repo '%s' "
                        "(venue-default-else-sender)",
                        resolve_name,
                        sender_repo,
                    )
                    return await self._resolve_venue_bound(sender_repo, resolve_name)
                return await self._resolve_bare(agent_name)
            await resolver.ensure_ready(resolve_name)
            return await self._resolve_with_plugins(resolver, resolve_name)

        return self._resolve_static(agent_name)

    async def _resolve_venue_bound(
        self, repo: str, venue: str,
    ) -> SpawnTarget:
        """Resolve ``<repo>@<venue>``: the venue, bound to run ``<repo>``."""
        from . import agent_registry as compat

        repo_remote = compat.resolve_repo_remote(repo)
        ns = self._parse_namespaced_agent(venue)
        if ns:
            prefix, name = ns
            resolver = self._namespace_resolvers[prefix]
            await resolver.ensure_ready(name)
            return await self._resolve_with_plugins(
                resolver,
                name,
                repo=repo,
                repo_remote=repo_remote,
            )

        candidates = await self._gather_bare_candidates(venue)
        if len(candidates) > 1:
            raise AmbiguousAgentError(venue, [qualified for qualified, _, _ in candidates])
        if len(candidates) == 1:
            _, resolver, resolve_name = candidates[0]
            if resolver is None:
                target = await self._resolve_bare(venue)
                return self._bind_repo(target, repo, venue)
            await resolver.ensure_ready(resolve_name)
            return await self._resolve_with_plugins(
                resolver,
                resolve_name,
                repo=repo,
                repo_remote=repo_remote,
            )

        target = self._resolve_static(venue)
        return self._bind_repo(target, repo, venue)

    def _bind_repo(self, target: SpawnTarget, repo: str, venue: str) -> SpawnTarget:
        """Rebind a machine/local venue target to run ``<repo>``'s binstub."""
        if target.type in ("local", "ssh"):
            import dataclasses

            return dataclasses.replace(target, project=repo)
        raise ValueError(
            f"Cross-repo dispatch '{repo}@{venue}' is not supported for this "
            "venue (it hosts its own repo/checkout)."
        )

    async def _resolve_with_plugins(
        self,
        resolver: NamespaceResolver,
        name: str,
        repo: str | None = None,
        repo_remote: str | None = None,
    ) -> SpawnTarget:
        """Resolve via a namespace resolver, injecting related-repo plugins."""
        extra = await self._related_plugins_for(resolver, name)
        return await self._call_resolver(
            resolver,
            name,
            extra_plugins=extra,
            repo=repo,
            repo_remote=repo_remote,
        )

    async def _call_resolver(
        self,
        resolver: NamespaceResolver,
        name: str,
        *,
        extra_plugins: list[PluginRef],
        repo: str | None,
        repo_remote: str | None = None,
    ) -> SpawnTarget:
        """Invoke ``resolver.resolve`` passing only the kwargs it accepts."""
        signature = inspect.signature(resolver.resolve)
        kwargs: dict[str, Any] = {}
        if extra_plugins and "extra_plugins" in signature.parameters:
            kwargs["extra_plugins"] = extra_plugins
        if repo is not None:
            if "repo" not in signature.parameters:
                raise ValueError(
                    f"Cross-repo dispatch (repo='{repo}') is not supported by "
                    f"the '{getattr(resolver, 'prefix', '?')}:' resolver."
                )
            kwargs["repo"] = repo
        if repo_remote is not None and "repo_remote" in signature.parameters:
            kwargs["repo_remote"] = repo_remote
        return await resolver.resolve(name, **kwargs)

    async def _related_plugins_for(
        self, resolver: NamespaceResolver, name: str,
    ) -> list[PluginRef]:
        """Related-repo plugins to inject for a dispatch target, or ``[]``."""
        try:
            repo = await self._resolver_target_repo(resolver, name)
            if not repo:
                return []
            from .related_plugins import related_plugins_for_repo

            refs = related_plugins_for_repo(repo)
            if refs:
                log.info(
                    "Injecting %d related-repo plugin(s) for %s (repo=%s): %s",
                    len(refs),
                    name,
                    repo,
                    [ref.source for ref in refs],
                )
            return refs
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("related-repo plugin sourcing failed for %s: %s", name, exc)
            return []

    async def _resolver_target_repo(
        self, resolver: NamespaceResolver, name: str,
    ) -> str | None:
        """Best-effort workspace repo for a resolved target."""
        fn = getattr(resolver, "target_repo", None)
        if fn is None:
            return None
        result = fn(name)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, str) and result.strip() else None

    async def _resolve_bare(self, agent_name: str) -> SpawnTarget:
        """Resolve a bare static/provider agent, routing elevated ones."""
        canonical = self.canonical_agent_name(agent_name) or agent_name
        relay = await self._maybe_elevated_relay(canonical)
        if relay is not None:
            return relay
        target = self._resolve_static(canonical)
        config = self._agents.get(canonical)
        if config is not None and config.requires_admin:
            from . import elevated

            if elevated.is_process_elevated():
                return replace(target, elevated=True)
        return target

    async def _maybe_elevated_relay(
        self, agent_name: str,
    ) -> SpawnTarget | None:
        """Return a sub-daemon relay target for an elevated agent, else None."""
        from . import elevated

        config = self._agents.get(agent_name)
        if config is None or not config.requires_admin:
            return None
        if not elevated.relay_applicable(config.requires_admin):
            return None

        loop = asyncio.get_running_loop()
        token = await loop.run_in_executor(None, elevated.ensure_running)
        cmd = elevated.relay_spawn_command(config.name, token=token)
        log.info(
            "Routing elevated agent '%s' via sub-daemon relay (port %d)",
            config.name,
            elevated.discovered_port(),
        )
        return SpawnTarget(
            type="command",
            spawn_command=cmd,
            project=config.project,
            elevated=True,
        )

    async def _gather_bare_candidates(
        self, name: str,
    ) -> list[tuple[str, NamespaceResolver | None, str]]:
        """Find every agent a bare name matches, across static + namespaces."""
        candidates: list[tuple[str, NamespaceResolver | None, str]] = []
        static_name = self.canonical_agent_name(name)
        if static_name:
            candidates.append((static_name, None, static_name))

        lower_name = name.lower()
        for prefix, resolver in self._namespace_resolvers.items():
            if not getattr(resolver, "bare_addressable", True):
                continue
            try:
                infos = await resolver.list()
            except Exception:
                log.warning(
                    "Namespace resolver '%s' failed to list during bare-name "
                    "resolution of '%s'",
                    prefix,
                    name,
                    exc_info=True,
                )
                continue
            for info in infos:
                names = [info.name, *getattr(info, "aliases", [])]
                if any(candidate and candidate.lower() == lower_name for candidate in names):
                    candidates.append((f"{prefix}:{info.name}", resolver, info.name))

        return candidates

    def _own_plugin_args(self, config) -> list[str]:
        """``--plugin-dir`` args for the launching repo's own enabled plugins."""
        try:
            from pathlib import Path as _Path

            from .related_plugins import _registry_anchor
            from .repo_own_plugins import repo_plugin_dir_args

            anchor = None
            project = getattr(config, "project", None)
            if project:
                anchor = _registry_anchor(project)
            if anchor is None and getattr(config, "cwd", None):
                anchor = _Path(config.cwd)
            return repo_plugin_dir_args(anchor)
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("own-plugin arg staging failed: %s", exc)
            return []

    def _resolve_static(self, agent_name: str) -> SpawnTarget:
        """Resolve via the static / auto-discovered registry."""
        canonical = self.canonical_agent_name(agent_name)
        config = self._agents.get(canonical) if canonical else None
        if not config:
            raise KeyError(f"Agent '{agent_name}' not found in registry")

        if config.managed:
            raise ValueError(
                f"Agent '{agent_name}' is managed (non-spawnable) -- "
                "it cannot be started via agent-bridge transport"
            )

        if config.spawn_command:
            return SpawnTarget(
                type="command",
                spawn_command=config.spawn_command,
                codespace=config.codespace,
                env=config.env,
                mcp_servers=config.mcp_servers,
            )

        if not config.host:
            return SpawnTarget(
                type="local",
                cwd=config.cwd,
                copilot_path=config.copilot_path,
                copilot_args=config.copilot_args + self._own_plugin_args(config),
                env=config.env,
                project=config.project,
                mcp_servers=config.mcp_servers,
            )

        machine, alias_env = self._resolve_machine(config.host, config.ssh_environment)
        if alias_env:
            ssh_env = alias_env
            if not config.project:
                posix_shells = {"bash", "sh", "zsh", "dash", "fish"}
                if ssh_env.shell not in posix_shells:
                    raise ValueError(
                        f"Agent '{agent_name}' resolved via SSH alias "
                        f"'{config.host}' to environment '{ssh_env.name}' "
                        f"(shell={ssh_env.shell}), but non-binstub SSH "
                        "targets require a POSIX-compatible shell"
                    )
        elif config.project:
            ssh_env = machine.get_ssh_env(config.ssh_environment)
        else:
            ssh_env = machine.get_spawnable_ssh_env(config.ssh_environment)

        if (
            ssh_env
            and self._local_machine
            and machine.key == self._local_machine.key
            and ssh_env.name == self._local_platform
        ):
            log.info(
                "Loopback detected for agent '%s' (machine '%s', env '%s') "
                "-- spawning locally instead of SSH",
                agent_name,
                machine.key,
                ssh_env.name,
            )
            return SpawnTarget(
                type="local",
                cwd=config.cwd,
                copilot_path=config.copilot_path,
                copilot_args=config.copilot_args + self._own_plugin_args(config),
                env=config.env,
                project=config.project,
                mcp_servers=config.mcp_servers,
            )

        if not machine.ssh_ready:
            raise ValueError(
                f"Machine '{machine.key}' is not marked as SSH-ready "
                "in the topology (inter-machine SSH is unavailable; only "
                "local loopback dispatch works)"
            )

        if not ssh_env:
            available = [env.name for env in machine.ssh_environments]
            if config.project:
                raise ValueError(
                    f"No SSH environment "
                    f"{repr(config.ssh_environment) + ' ' if config.ssh_environment else ''}"
                    f"for agent '{agent_name}' on '{machine.key}'. "
                    f"Available: {available}"
                )
            posix = [
                env.name
                for env in machine.ssh_environments
                if env.shell in {"bash", "sh", "zsh", "dash", "fish"}
            ]
            raise ValueError(
                f"No suitable SSH environment for agent '{agent_name}' on "
                f"'{machine.key}'. Available: {available}, "
                f"POSIX-compatible: {posix}. "
                "Non-binstub SSH targets require a POSIX-compatible shell."
            )

        auth_hook_dicts = [
            {
                "name": hook.name,
                "local_port": hook.local_port,
                "remote_port": hook.remote_port,
                "env": hook.env,
            }
            for hook in machine.auth_hooks
        ]
        return SpawnTarget(
            type="ssh",
            cwd=config.cwd,
            host=ssh_env.alias,
            user=ssh_env.user or config.ssh_user,
            copilot_path=config.copilot_path,
            copilot_args=config.copilot_args,
            env=config.env,
            project=config.project,
            ssh_shell=ssh_env.shell,
            auth_hooks=auth_hook_dicts,
            mcp_servers=config.mcp_servers,
        )

    def _is_local_loopback_agent(self, config: AgentConfig) -> bool:
        """True when this agent dispatches via local loopback rather than SSH."""
        return bool(
            config.host
            and self._local_machine
            and config.host == self._local_machine.key
            and config.ssh_environment == self._local_platform
        )

    def _agent_to_dict(self, config: AgentConfig) -> dict[str, Any]:
        """Convert an AgentConfig to API-ready dict."""
        spawnable = not config.managed and config.spawnable_as_target
        if config.spawn_command:
            target_type = "command"
        elif config.host and not self._is_local_loopback_agent(config):
            target_type = "ssh"
        else:
            target_type = "local"
        return {
            "name": config.name,
            "display_name": config.display_name or config.name,
            "aliases": list(config.aliases),
            "description": config.description or "",
            "icon": config.icon,
            "managed": config.managed,
            "spawnable_as_target": config.spawnable_as_target,
            "spawnable": spawnable,
            "target_type": target_type,
            "host": config.host or "",
            "machine_key": self.machine_key_for_agent(config),
            "ssh_user": config.ssh_user,
            "ssh_environment": config.ssh_environment,
            "cwd": config.cwd,
            "copilot_path": config.copilot_path,
            "copilot_args": config.copilot_args,
            "worktree_root": config.worktree_root,
            "env": config.env or {},
            "project": config.project,
            "auto_discovered": config.auto_discovered,
            "derived": config.derived,
            "provider": config.provider,
        }

    def list_agents(self) -> list[dict[str, Any]]:
        """List all agents with metadata for the API."""
        result = []
        for config in self._agents.values():
            if not config.spawnable_as_target:
                continue
            result.append(self._agent_to_dict(config))
        return result

    async def list_agents_async(self) -> list[dict[str, Any]]:
        """List all agents including namespace-resolved agents."""
        from . import agent_registry as compat

        self.refresh_provider_resolvers()
        result = self.list_agents()
        prefixes = list(self._namespace_resolvers.keys())
        resolver_timeout = compat._namespace_list_resolver_timeout()

        async def _bounded_list(prefix: str) -> Any:
            resolver = self._namespace_resolvers[prefix]
            list_kwargs: dict[str, Any] = {}
            if resolver_timeout > 0:
                try:
                    params = inspect.signature(resolver.list).parameters
                except (TypeError, ValueError):
                    params = {}
                if "timeout" in params:
                    list_kwargs["timeout"] = resolver_timeout
            coro = resolver.list(**list_kwargs)
            if resolver_timeout <= 0:
                return await coro
            return await asyncio.wait_for(coro, timeout=resolver_timeout)

        listings = await asyncio.gather(
            *(_bounded_list(prefix) for prefix in prefixes),
            return_exceptions=True,
        )
        for prefix, outcome in zip(prefixes, listings):
            if isinstance(outcome, asyncio.TimeoutError):
                log.warning(
                    "Namespace resolver '%s' timed out listing agents after %ss "
                    "(AGENT_BRIDGE_NAMESPACE_LIST_RESOLVER_TIMEOUT) -- dropped "
                    "from this listing, not blocking the rest",
                    prefix,
                    resolver_timeout,
                )
                continue
            if isinstance(outcome, BaseException):
                log.warning(
                    "Namespace resolver '%s' failed to list agents",
                    prefix,
                    exc_info=outcome,
                )
                continue
            resolver = self._namespace_resolvers[prefix]
            for agent in outcome:
                result.append({
                    "name": f"{prefix}:{agent.name}",
                    "display_name": agent.display_name or agent.name,
                    "description": agent.description,
                    "icon": agent.icon,
                    "aliases": [f"{prefix}:{alias}" for alias in getattr(agent, "aliases", [])],
                    "managed": False,
                    "spawnable_as_target": True,
                    "spawnable": True,
                    "target_type": "command",
                    "host": "",
                    "ssh_user": None,
                    "ssh_environment": None,
                    "cwd": None,
                    "copilot_path": None,
                    "copilot_args": [],
                    "worktree_root": None,
                    "env": {},
                    "project": None,
                    "auto_discovered": False,
                    "provider": prefix,
                    "bare_addressable": getattr(resolver, "bare_addressable", True),
                    "state": agent.state,
                })
        return result
