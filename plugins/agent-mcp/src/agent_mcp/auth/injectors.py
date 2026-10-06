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
import contextlib
import logging
import os
import shutil
from urllib.parse import urlsplit

from agent_procutil import windowless_daemon_kwargs

from .._exec import no_window_creationflags, resolve_argv
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


async def _terminate_proc(proc: asyncio.subprocess.Process | None) -> None:
    """Kill and reap a child process (no-op if already gone)."""
    if proc is None or proc.returncode is not None:
        return
    try:
        proc.kill()
        await proc.wait()
    except ProcessLookupError:
        pass


async def _terminate_tree(proc: asyncio.subprocess.Process | None) -> None:
    """Kill and reap a child **and its descendants** (no-op if already gone).

    For a wrapper that is not a single-process executable -- e.g. a Node shim
    that ``spawnSync``s further shell/Python helpers, as the Codespace
    credential-relay client does -- killing only the direct child can leave a
    nested process running until its own timeout. Best-effort: a failed or
    slow tree-kill attempt still falls through to :func:`_terminate_proc` for
    the direct child.
    """
    if proc is None or proc.returncode is not None:
        return
    if os.name == "nt":
        with contextlib.suppress(Exception):
            tree_kill = await asyncio.create_subprocess_exec(
                "taskkill", "/T", "/F", "/PID", str(proc.pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=no_window_creationflags(),
            )
            # taskkill /T /F is observed to take 100+ seconds under endpoint-
            # protection scanning (CONTRIBUTING.md's agent-codespaces-ssh
            # postmortem). Bound our wait so a slow tree-kill can't stall this
            # acquisition past its own timeout; shield the kill itself so it
            # keeps running to completion in the background rather than being
            # cancelled outright, then fall through to the direct-process reap.
            with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(tree_kill.wait()), timeout=10)
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            import signal

            child_pgid = os.getpgid(proc.pid)
            # Only kill the *group* when the child actually owns one of its own
            # (windowless_daemon_kwargs skips start_new_session in contained test
            # mode, so a timed-out child can otherwise share -- and killpg would
            # tear down -- the calling process's/test runner's own group).
            if child_pgid != os.getpgid(0):
                os.killpg(child_pgid, signal.SIGKILL)
    await _terminate_proc(proc)


class EnvInjector(TokenInjector):
    """Token from a host environment variable (``env``) or a literal (``static``)."""

    name = "env"

    async def _acquire(self) -> str | None:
        if self.spec.source_env:
            return os.environ.get(self.spec.source_env)
        return self.spec.value


class EntraInjector(TokenInjector):
    """Entra ID / Azure access token via ``credential_relay.sources.az_login``.

    Prefers an on-``PATH`` ``ado-auth-helper`` -- the credential-relay client
    shim ``agent-codespaces`` installs on a Codespace guest (see its
    ``codespace_assets``) -- over shelling to a local ``az`` CLI directly. A
    guest has no logged-in Azure CLI session of its own, so the direct
    ``AzLoginSource`` path always fails there even when the relay tunnel is
    live and already serving every other Azure/ADO consumer in that guest.
    Calling ``ado-auth-helper`` instead needs no environment detection here --
    it's the same PATH-resolution trick ``rush``'s cloud build-cache login and
    the ADO npm-token flow already rely on to work headlessly in a Codespace:
    whichever binary answers on PATH determines local-vs-relayed behavior.
    Falls back to the existing local-``az`` behavior unchanged when the helper
    isn't present, or when an explicit ``tenant`` is configured (the relay's
    ``get-azure-token`` action has no tenant parameter to forward).
    """

    name = "entra"

    _RELAY_HELPER = "ado-auth-helper"

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

    def _scope(self) -> str:
        """The AAD scope to request, canonicalized the same way ``az_login`` does."""
        from credential_relay.sources.az_login import _to_scope

        return _to_scope(self.spec.scope or self.spec.resource or "")

    async def _acquire_via_helper(self, helper: str) -> str | None:
        """Mint a token via the on-PATH relay-client shim (Codespace guest path)."""
        scope = self._scope()
        if not scope:
            return None
        argv = resolve_argv([helper, "get-access-token", "--scope", scope])
        # The wrapper is not a single-process executable -- its Node shim
        # spawnSync's further shell/Python helpers of its own. windowless_daemon_kwargs
        # keeps it windowless on Windows and puts it in its own POSIX session, so
        # the whole tree stays killable as one unit (see _terminate_tree).
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **windowless_daemon_kwargs(),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout,
            )
        except (TimeoutError, asyncio.TimeoutError):
            log.error("%s get-access-token timed out (%.0fs)", helper, self._timeout)
            # wait_for cancelled communicate() but left the child (and any of its
            # own nested helpers) running -- kill the whole tree so a hung relay
            # client doesn't leak processes per acquisition/401 retry.
            await _terminate_tree(proc)
            return None
        except OSError as exc:
            log.error("%s get-access-token failed to launch: %s", helper, exc)
            return None
        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip().replace("\n", " ")[:200]
            log.error(
                "%s get-access-token failed (exit %d): %s", helper, proc.returncode, err,
            )
            return None
        token = stdout.decode(errors="replace").strip()
        if not token and stderr:
            # A clean exit with an empty token is the shape of a *silent* relay
            # denial (e.g. the resource/scope isn't in this Codespace's az-login
            # allowlist) -- log whatever diagnostic the helper did emit so the
            # next caller isn't left guessing why acquisition produced nothing.
            err = stderr.decode(errors="replace").strip().replace("\n", " ")[:200]
            log.warning("%s get-access-token returned no token: %s", helper, err)
        return token or None

    async def _acquire(self) -> str | None:
        helper = None if self.spec.tenant else shutil.which(self._RELAY_HELPER)
        if helper:
            token = await self._acquire_via_helper(helper)
            if token:
                return token
            log.warning(
                "%s present but returned no token; falling back to local az CLI",
                self._RELAY_HELPER,
            )
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

    def __init__(self, spec: AuthSpec, *, timeout: float = 30.0,
                 repair_timeout: float = 300.0) -> None:
        super().__init__(spec)
        self._timeout = timeout
        # A repair (e.g. reinstalling mint tooling) is inherently slower than a
        # mint, so it gets its own, more generous timeout.
        self._repair_timeout = repair_timeout

    def _stdin(self) -> bytes:
        """git-credential request body: ``key=value`` lines + blank terminator."""
        lines = [f"{k}={v}" for k, v in self.spec.request.items()]
        return ("\n".join(lines) + "\n\n").encode()

    @staticmethod
    def _bound_err(stderr: bytes) -> str:
        """Bound possibly-large/sensitive helper stderr for a single log line."""
        err = stderr.decode(errors="replace").strip().replace("\n", " ")
        if len(err) > 200:
            err = err[:200] + "...(truncated)"
        return err

    def _parse_token(self, out: str) -> str | None:
        """Extract the secret from a successful command's stdout per ``parse``."""
        if self.spec.parse == "raw":
            # Chomp only the CLI's line terminator; preserve any other
            # whitespace that may be part of the secret.
            return out.strip("\r\n") or None
        fields = parse_response(out)
        if self.spec.field_name:
            return fields.get(self.spec.field_name)
        return _token_from(out)

    async def _run_command(self, argv: list[str]) -> tuple[str | None, bool]:
        """Run the mint command once. Returns ``(token_or_None, hard_failed)``.

        ``hard_failed`` is True only for a *tooling* failure -- timeout, missing
        binary, or non-zero exit -- i.e. the cases a ``repair`` step might fix. A
        clean exit whose output merely lacks a token is **not** a hard failure
        (returns ``(None, False)``), so repair never fires on a well-behaved-but-
        empty response.
        """
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **windowless_daemon_kwargs(),  # Windows: no console window;
                                                # POSIX: own process group so a
                                                # multi-process mint command can
                                                # be fully reaped on timeout
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=self._stdin()), timeout=self._timeout,
            )
        except (TimeoutError, asyncio.TimeoutError):
            log.error("auth command timed out (%.0fs): %s", self._timeout, argv[0])
            # wait_for cancelled communicate() but left the child running -- a
            # hung helper (e.g. an interactive credential prompt) would otherwise
            # leak a process, one per acquisition/401-retry. Reap the whole
            # tree: `auth.command` is caller-configured and may itself be a
            # multi-process wrapper, not a guaranteed single process.
            await _terminate_tree(proc)
            return None, True
        except FileNotFoundError:
            log.error("auth command not found on PATH: %s", argv[0])
            return None, True

        if proc.returncode != 0:
            # Bound the logged stderr: a failing credential helper may emit large
            # and/or sensitive diagnostics inherited by the MCP host's logs.
            log.error("auth command failed (exit %s): %s -- %s",
                      proc.returncode, argv[0], self._bound_err(stderr))
            return None, True

        return self._parse_token(stdout.decode(errors="replace")), False

    async def _run_repair(self) -> bool:
        """Run the configured ``auth.repair`` command once; True on a clean exit.

        A repair (e.g. reinstalling/refreshing the mint tooling) takes no
        git-credential stdin and can be slow, so it runs with ``DEVNULL`` stdin
        under its own generous timeout. Its stdout is discarded; only bounded
        stderr is logged, and only when the repair itself fails.
        """
        argv = resolve_argv(self.spec.repair)
        log.warning("auth command failed; running repair once: %s", argv[0])
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **windowless_daemon_kwargs(),  # same tree-reapable launch as the
                                                # mint command above
            )
            _, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._repair_timeout,
            )
        except (TimeoutError, asyncio.TimeoutError):
            log.error("auth repair timed out (%.0fs): %s", self._repair_timeout, argv[0])
            await _terminate_tree(proc)
            return False
        except FileNotFoundError:
            log.error("auth repair not found on PATH: %s", argv[0])
            return False
        if proc.returncode != 0:
            log.error("auth repair failed (exit %s): %s -- %s",
                      proc.returncode, argv[0], self._bound_err(stderr))
            return False
        log.warning("auth repair succeeded: %s -- retrying mint", argv[0])
        return True

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
        token, hard_failed = await self._run_command(argv)
        if not hard_failed:
            return token
        # Self-heal (opt-in): when the mint command *hard-fails* and a ``repair``
        # command is configured, run it ONCE and retry the mint ONCE. Lets a
        # browser-minting bridge recover from broken tooling (e.g. an out-of-date
        # Playwright that can't drive an updated Edge) instead of just yielding no
        # token. Strictly bounded -- a single repair + single retry, never a loop.
        if self.spec.repair and await self._run_repair():
            token, hard_failed = await self._run_command(argv)
            if not hard_failed:
                return token
        return None


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
