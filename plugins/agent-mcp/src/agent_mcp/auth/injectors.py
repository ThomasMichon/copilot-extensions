"""Concrete auth injectors.

Token acquisition reuses the ``credential_relay`` host-credential sources
(``az_login``, ``gh_auth``, ``git_credential``) so this plugin does not
re-implement ``az`` / ``gh`` / GCM shell-outs, caching, or expiry handling. Each
source returns git-credential-protocol ``key=value`` text; :func:`parse_response`
pulls the token/password out of it.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from urllib.parse import urlsplit

from .._exec import resolve_argv
from ..config import AuthSpec, BridgeConfig
from .base import AuthInjector, CompositeInjector, NoneInjector, TokenInjector

log = logging.getLogger("agent-mcp.auth")


def parse_response(text: str | None) -> dict[str, str]:
    """Parse git-credential-protocol ``key=value`` lines into a dict."""
    out: dict[str, str] = {}
    if not text:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def _token_from(text: str | None) -> str | None:
    fields = parse_response(text)
    return fields.get("token") or fields.get("password")


class EnvInjector(TokenInjector):
    """Token from a host environment variable (``env``) or a literal (``static``)."""

    name = "env"

    async def _acquire(self) -> str | None:
        if self.spec.source_env:
            return os.environ.get(self.spec.source_env)
        return self.spec.value


class EntraInjector(TokenInjector):
    """Entra ID / Azure access token via ``credential_relay.sources.az_login``."""

    name = "entra"

    def __init__(self, spec: AuthSpec, *, timeout: float = 30.0) -> None:
        super().__init__(spec)
        self._timeout = timeout
        self._source = self._new_source()

    @staticmethod
    def _new_source():
        from credential_relay.sources.az_login import AzLoginSource

        # The bridge config is itself the allowlist boundary, so permit any scope
        # the operator configured (resource/scope is fixed per bridge file).
        return AzLoginSource(allowed_resources=["*"])

    async def invalidate(self) -> None:
        await super().invalidate()
        self._source = self._new_source()  # drop the source's internal token cache

    async def _acquire(self) -> str | None:
        fields: dict[str, str] = {}
        if self.spec.scope:
            fields["scope"] = self.spec.scope
        elif self.spec.resource:
            fields["resource"] = self.spec.resource
        if self.spec.tenant:
            fields["tenant"] = self.spec.tenant
        resp = await self._source.resolve("get-azure-token", fields, timeout=self._timeout)
        return _token_from(resp)


class GhInjector(TokenInjector):
    """GitHub token via ``credential_relay.sources.gh_auth``."""

    name = "gh"

    def __init__(self, spec: AuthSpec, *, timeout: float = 30.0) -> None:
        super().__init__(spec)
        self._timeout = timeout
        from credential_relay.sources.gh_auth import GhAuthSource

        self._source = GhAuthSource()

    async def _acquire(self) -> str | None:
        resp = await self._source.resolve("get-github-token", {}, timeout=self._timeout)
        return _token_from(resp)


class GitCredentialInjector(AuthInjector):
    """HTTP Basic from Git Credential Manager via ``credential_relay.sources.git_credential``.

    The host is derived from the upstream ``server.url``. Produces an
    ``Authorization: Basic base64(user:token)`` header for HTTP transports and a
    token-only env var for stdio transports.
    """

    name = "git-credential"

    def __init__(self, spec: AuthSpec, host: str, *, timeout: float = 30.0) -> None:
        self.spec = spec
        self._host = host
        self._timeout = timeout
        self._cached: dict[str, str] | None = None
        from credential_relay.sources.git_credential import GitCredentialSource

        self._source = GitCredentialSource()

    async def invalidate(self) -> None:
        self._cached = None

    async def _creds(self) -> dict[str, str]:
        if self._cached is None:
            resp = await self._source.resolve(
                "get", {"protocol": "https", "host": self._host}, timeout=self._timeout
            )
            self._cached = parse_response(resp)
        return self._cached

    async def headers(self) -> dict[str, str]:
        creds = await self._creds()
        user = creds.get("username", "")
        secret = creds.get("password") or creds.get("token")
        if not secret:
            return {}
        raw = f"{user}:{secret}".encode()
        return {self.spec.header: f"Basic {base64.b64encode(raw).decode()}"}

    async def child_env(self) -> dict[str, str]:
        creds = await self._creds()
        secret = creds.get("password") or creds.get("token")
        if not secret or not self.spec.target_env:
            return {}
        return {self.spec.target_env: secret}


class CommandInjector(TokenInjector):
    """Token from an external command that speaks the git-credential protocol.

    Generalizes :class:`GitCredentialInjector` from "always ``git credential``"
    to "any configured command." The command is run with the ``auth.request``
    fields written to its stdin as git-credential ``key=value`` text (a blank
    line terminates the request, exactly like ``git credential fill``), and its
    stdout is interpreted per ``auth.parse``:

    * ``keyvalue`` (default) -- parse ``key=value`` output and extract
      ``auth.field`` (default: ``token`` then ``password``). Wraps
      ``git credential fill``, a ``git-credential-vault``-style helper,
      ``op``/1Password CLI, etc.
    * ``raw`` -- the whole trimmed stdout is the secret verbatim. Wraps a plain
      secret-printer such as ``vault get "<entry>" password`` with no adapter.

    The resolved token is injected as a header (http) or env var (stdio) by the
    :class:`TokenInjector` base, and cached until :meth:`invalidate`.
    """

    name = "command"

    def __init__(self, spec: AuthSpec, *, timeout: float = 30.0) -> None:
        super().__init__(spec)
        self._timeout = timeout

    def _stdin(self) -> bytes:
        """git-credential request body: ``key=value`` lines + blank terminator."""
        lines = [f"{k}={v}" for k, v in self.spec.request.items()]
        return ("\n".join(lines) + "\n\n").encode()

    @staticmethod
    async def _terminate(proc: asyncio.subprocess.Process | None) -> None:
        """Kill and reap a child process (no-op if already gone)."""
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass

    async def _acquire(self) -> str | None:
        # Env-first fallback: if ``source_env`` is configured and that variable is
        # already set in the host environment (e.g. a push / no-vault machine's
        # static .env), use it instead of running the command. Lets one bridge
        # config work on both vault-enabled hosts (env unset -> run command) and
        # daemon-less hosts (static env present -> no vault needed).
        if self.spec.source_env:
            env_val = os.environ.get(self.spec.source_env, "").strip()
            if env_val:
                return env_val
        argv = self.spec.command
        if not argv:
            return None
        # Resolve argv[0] so a .cmd/.bat credential binstub (e.g. vault.cmd)
        # spawns on Windows -- create_subprocess_exec only auto-appends .exe.
        argv = resolve_argv(argv)
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=self._stdin()), timeout=self._timeout,
            )
        except (TimeoutError, asyncio.TimeoutError):
            log.error("auth command timed out (%.0fs): %s", self._timeout, argv[0])
            # wait_for cancelled communicate() but left the child running -- a
            # hung helper (e.g. an interactive credential prompt) would otherwise
            # leak a process, one per acquisition/401-retry. Reap it.
            await self._terminate(proc)
            return None
        except FileNotFoundError:
            log.error("auth command not found on PATH: %s", argv[0])
            return None

        if proc.returncode != 0:
            # Bound the logged stderr: a failing credential helper may emit
            # large and/or sensitive diagnostics, and this stream is inherited
            # by the MCP host's logs.
            err = stderr.decode(errors="replace").strip().replace("\n", " ")
            if len(err) > 200:
                err = err[:200] + "...(truncated)"
            log.error("auth command failed (exit %s): %s -- %s",
                      proc.returncode, argv[0], err)
            return None

        out = stdout.decode(errors="replace")
        if self.spec.parse == "raw":
            # Chomp only the CLI's line terminator; preserve any other
            # whitespace that may be part of the secret.
            return out.strip("\r\n") or None
        fields = parse_response(out)
        if self.spec.field_name:
            return fields.get(self.spec.field_name)
        return _token_from(out)


def _build_one(spec: AuthSpec, cfg: BridgeConfig) -> AuthInjector:
    """Construct a single auth injector from one :class:`AuthSpec`."""
    kind = spec.normalized_kind
    if kind == "none":
        return NoneInjector()
    if kind == "env":
        return EnvInjector(spec)
    if kind == "entra":
        return EntraInjector(spec, timeout=cfg.timeout)
    if kind == "gh":
        return GhInjector(spec, timeout=cfg.timeout)
    if kind == "command":
        return CommandInjector(spec, timeout=cfg.timeout)
    if kind == "git-credential":
        host = urlsplit(cfg.server.url or "").hostname or ""
        return GitCredentialInjector(spec, host=host, timeout=cfg.timeout)
    raise ValueError(f"unknown auth kind: {spec.kind}")


def build_injector(cfg: BridgeConfig) -> AuthInjector:
    """Construct the auth injector for a bridge config.

    A bridge with a single ``auth`` gets that one injector; a bridge whose
    ``auth`` is a list gets a :class:`CompositeInjector` that merges every
    injector's headers / child env (later entries win on key collisions).
    """
    injectors = [_build_one(spec, cfg) for spec in cfg.auths]
    if len(injectors) == 1:
        return injectors[0]
    return CompositeInjector(injectors)
