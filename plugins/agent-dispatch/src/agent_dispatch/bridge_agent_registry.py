"""agent-bridge agent-registry lookups: parse, resolve, and preflight-check
registered agents.

Extracted out of ``bridge.py`` (over its 1000-line module-size cap) into its
own cohesive module, mirroring this session's own precedent for landing new
capability (or, here, relocating a self-contained existing cluster) in a
fresh same-package module rather than growing an over-cap file further.
Re-exported from ``bridge.py`` so ``bridge.X`` call sites throughout the
plugin are unaffected.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

from .procutil import agent_bridge_launch_prefix as _agent_bridge_launch_prefix
from .procutil import no_window_kwargs

def parse_agents(out: str | None) -> list[dict] | None:
    """Extract agent records from ``agent-bridge --json agents`` stdout.

    ``agent-bridge`` may print a human preamble line before the JSON array, so we
    locate the first ``[`` and ``raw_decode`` from there. Returns ``None`` --
    meaning *indeterminate*, not *empty* -- when the payload is missing or
    unparseable.
    """
    text = (out or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        # Tolerate a stray human preamble line before the JSON array: decode from
        # the first '[' (best-effort; a preamble that itself contains '[' simply
        # reads as indeterminate rather than crashing).
        start = text.find("[")
        if start == -1:
            return None
        try:
            data, _end = json.JSONDecoder().raw_decode(text[start:])
        except (ValueError, TypeError):
            return None
    if not isinstance(data, list):
        return None
    return [entry for entry in data if isinstance(entry, dict)]


def parse_agent_names(out: str | None) -> set[str] | None:
    """Extract agent ``name`` values from ``agent-bridge --json agents`` stdout."""
    rows = parse_agents(out)
    if rows is None:
        return None
    return {
        name
        for row in rows
        if isinstance((name := row.get("name")), str) and name
    }


def registered_agents(*, timeout: float = 20.0) -> list[dict] | None:
    """Best-effort agent records from the **local** agent-bridge.

    Returns ``None`` (indeterminate) whenever the registry can't be read -- the
    bridge CLI is absent, the command exits non-zero, times out, or emits
    unparseable output. Never raises.

    ``timeout`` defaults generously (20s, was 8s): ``agent-bridge agents``
    enumerates every registered namespace provider (e.g. CodeSpaces across
    mapped GitHub accounts, or Docker containers), and even with agent-bridge's
    own short-TTL namespace-list cache warm, a cold cache or a provider having
    a genuinely slow moment can still take several seconds. A too-tight
    timeout here silently degrades a real registry read to "indeterminate",
    which upstream callers may then dead-letter a spawn reservation on -- this
    is defense-in-depth headroom, not a substitute for the bridge-side caching
    fix.

    Prefer :func:`registered_agent` when only a single, known agent name needs
    checking (the common case: spawn preflight, headless-lane resolution) --
    it never enumerates namespace resolvers at all, so it is not subject to
    CodeSpace/container latency in the first place. Use this full listing only
    when the actual set of *all* registered agents is needed.
    """
    exe = _agent_bridge_launch_prefix()
    if exe is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe resolved above
            [*exe, "--json", "agents"],
            check=False, capture_output=True, text=True, timeout=timeout,
            **no_window_kwargs(),
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return parse_agents(proc.stdout)


class _NotFound:
    """Sentinel: ``agent-show`` positively confirmed no such agent -- distinct
    from ``None`` (indeterminate: bridge absent, timeout, crash, unparseable).
    A caller that raises on indeterminate must not also raise on a
    legitimately-absent agent; keeping these two outcomes distinguishable is
    the whole point of this sentinel."""


_AGENT_NOT_FOUND = _NotFound()


class _Unsupported:
    """Sentinel: the installed agent-bridge CLI does not recognize
    ``agent-show`` (argparse rejects it, exit 2) -- version skew, since
    agent-bridge and agent-dispatch are independently-updated plugins and an
    agent-dispatch update can land before its paired agent-bridge one.
    Distinct from a genuine indeterminate failure: callers must fall back to
    the full listing here, not treat every local allocation as unreadable."""


_AGENT_SHOW_UNSUPPORTED = _Unsupported()


def registered_agent(
    name: str,
    *,
    timeout: float = 8.0,
    include_unaddressable: bool = False,
) -> dict | _NotFound | _Unsupported | None:
    """Best-effort single-agent record via agent-bridge's fast ``agent-show``
    lookup -- a static/topology-only lookup that never enumerates namespace
    resolvers (CodeSpaces, containers), unlike :func:`registered_agents`.

    Returns:
    - a ``dict`` record when ``name`` is a registered (non-namespace-prefixed)
      agent;
    - :data:`_AGENT_NOT_FOUND` when the registry was read successfully and
      confirmed no such agent (``agent-show`` exits 1) -- a legitimate,
      non-error outcome;
    - :data:`_AGENT_SHOW_UNSUPPORTED` when the installed agent-bridge CLI
      doesn't recognize ``agent-show`` yet (argparse exit 2) -- version skew;
      callers should fall back to :func:`registered_agents`, not treat this
      as indeterminate;
    - ``None`` (indeterminate) when the registry could not be read at all
      (bridge CLI absent, spawn error, timeout, crash, unparseable output).

    Cannot resolve a namespace-prefixed name (``codespace:foo``,
    ``container:bar``) -- ``agent-show`` deliberately does not enumerate
    namespace resolvers, which is exactly the latency this fast path exists
    to avoid. Callers needing a namespace-resolved agent, or one this
    returned :data:`_AGENT_SHOW_UNSUPPORTED` for, should use
    :func:`registered_agents` (or the ``_resolve_agent_record`` wrapper below,
    which does this automatically).
    """
    exe = _agent_bridge_launch_prefix()
    if exe is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe resolved above
            [
                *exe,
                "--json",
                "agent-show",
                name,
                *(["--include-unaddressable"] if include_unaddressable else []),
            ],
            check=False, capture_output=True, text=True, timeout=timeout,
            **no_window_kwargs(),
        )
    except (subprocess.SubprocessError, OSError):
        return None
    stderr = proc.stderr or ""
    if proc.returncode == 2 and (
        "agent-show" in stderr or "--include-unaddressable" in stderr
    ):
        return _AGENT_SHOW_UNSUPPORTED
    if proc.returncode == 1:
        return _AGENT_NOT_FOUND
    if proc.returncode != 0:
        return None
    text = (proc.stdout or "").strip()
    if not text or text == "null":
        return _AGENT_NOT_FOUND
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _resolve_agent_record(
    name: str,
    *,
    timeout: float,
    fallback_timeout: float = 20.0,
    include_unaddressable: bool = False,
) -> dict | _NotFound | None:
    """Resolve one agent's record, preferring the fast single-agent lookup
    and transparently falling back to the full listing when the fast path
    can't answer for this ``name``:

    - a namespace-prefixed name (``codespace:foo``) -- ``agent-show`` never
      enumerates namespace resolvers, so it can never resolve one;
    - :data:`_AGENT_SHOW_UNSUPPORTED` -- the installed agent-bridge CLI
      predates ``agent-show`` (version skew across independently-updated
      plugins).

    ``fallback_timeout`` is deliberately more generous than ``timeout``: the
    full listing legitimately waits on namespace-resolver enumeration, which
    the fast path exists specifically to avoid.
    """
    if ":" not in name:
        row = registered_agent(
            name,
            timeout=timeout,
            include_unaddressable=include_unaddressable,
        )
        if row is not _AGENT_SHOW_UNSUPPORTED:
            return row
    rows = registered_agents(timeout=max(timeout, fallback_timeout))
    if rows is None:
        return None
    for row in rows:
        if row.get("name") == name:
            return row
    return _AGENT_NOT_FOUND


def agent_is_registered(
    name: str, *, timeout: float = 8.0, include_unaddressable: bool = False
) -> bool | None:
    """Best-effort "is ``name`` a registered agent?" -- prefers the fast
    single-agent path, falling back to the full listing for namespace-
    prefixed names or an agent-bridge CLI that predates ``agent-show`` (see
    :func:`_resolve_agent_record`). Returns ``None`` (indeterminate) when the
    registry could not be read at all."""
    row = _resolve_agent_record(
        name, timeout=timeout, include_unaddressable=include_unaddressable
    )
    if row is None:
        return None
    return row is not _AGENT_NOT_FOUND


def registered_agent_names(*, timeout: float = 20.0) -> set[str] | None:
    """Best-effort set of names registered with the local agent-bridge."""
    rows = registered_agents(timeout=timeout)
    if rows is None:
        return None
    return {
        name
        for row in rows
        if isinstance((name := row.get("name")), str) and name
    }


def registered_agent_project(
    agent: str,
    *,
    timeout: float = 8.0,
    strict: bool = False,
) -> str | None:
    """Return a registered agent's explicit project, when available.

    Prefers the fast single-agent lookup, which resolves purely from static/
    topology config and never enumerates namespace resolvers -- but
    transparently falls back to the full listing for a namespace-prefixed
    name or an agent-bridge CLI predating ``agent-show`` (see
    :func:`_resolve_agent_record`), so this remains correct for every caller,
    not just the common plain-local-agent case. ``timeout`` defaults to 8s
    (was 20s): the fast path has no namespace-enumeration latency to size
    for; a fallback to the full listing uses a more generous timeout of its
    own regardless.
    """
    row = _resolve_agent_record(
        agent, timeout=timeout, include_unaddressable=True
    )
    if row is None:
        if strict:
            from .bridge import BridgeUnavailable

            raise BridgeUnavailable(
                f"could not read the local agent registry while resolving {agent!r}"
            )
        return None
    if row is _AGENT_NOT_FOUND:
        return None
    project = row.get("project")
    return project if isinstance(project, str) and project else None


def preflight_headless_agent(
    agent: str,
    *,
    pool: Sequence[str] | None = None,
    local_timeout: float = 8.0,
    remote_timeout: float = 15.0,
) -> list[str]:
    """Best-effort check that ``agent`` is a registered agent-bridge agent on the
    host(s) where a headless embody body will actually spawn.

    A headless supervise lane hands ``agent`` to ``agent-bridge create <agent>``;
    if no such agent is registered the spawn fails ("'<agent>' is not a known
    agent name"), retries, and dead-letters -- silently, from the operator's seat.
    This preflight turns that latent misconfiguration (classically the bogus
    ``task-worker`` code default naming an agent nobody registered) into a loud,
    diagnosable startup WARNING, returning one human-readable line per host where
    ``agent`` is *provably* absent.

    It is deliberately **advisory and best-effort** (``degrade-gracefully`` +
    ``fail-loud-on-endpoint-error``): it warns only when the registry is readable
    AND the agent is confirmed missing. If the registry can't be read (bridge
    absent, host unreachable, timeout, unparseable) that host is INDETERMINATE and
    yields no warning -- the preflight never blocks a lane and never cries wolf on
    ignorance. For a fleet lane (``pool`` set) the body spawns on each remote pool
    host, so each is probed over SSH; otherwise the local registry is probed via
    the fast single-agent path (:func:`agent_is_registered`), which skips
    namespace/CodeSpace/container enumeration for the common case (a plain
    local/SSH-topology name) and transparently falls back to the full listing
    for a namespace-prefixed name or version-skewed agent-bridge CLI -- see
    :func:`_resolve_agent_record`. ``local_timeout`` defaults to 8s
    accordingly (was 20s, sized for the full listing this no longer calls in
    the common case).
    """
    checks: list[tuple[str, dict | _NotFound | None]] = []
    if pool:
        from . import embody

        for host in pool:
            h = host.strip()
            if not h:
                continue
            checks.append(
                (
                    h,
                    embody.remote_registered_agent_record(
                        h, agent, timeout=remote_timeout
                    ),
                )
            )
    else:
        row = _resolve_agent_record(
            agent,
            timeout=local_timeout,
            include_unaddressable=True,
        )
        checks.append(("this host", row))
    warnings: list[str] = []
    for where, record in checks:
        if record is _AGENT_NOT_FOUND:
            warnings.append(
                f"agent-dispatch supervise: WARNING -- headless embody agent "
                f"{agent!r} is not registered with agent-bridge on {where}; "
                f"headless spawns for this lane will fail ({agent!r} is not a known "
                f"agent name) and dead-letter. Register it (e.g. add a body to "
                f"acp-agents.json) or set --headless-agent to a registered agent."
            )
        elif isinstance(record, dict) and bool(record.get("managed")):
            warnings.append(
                f"agent-dispatch supervise: WARNING -- headless embody agent "
                f"{agent!r} resolves on {where} but is managed (non-spawnable as a "
                "bound charter); headless spawns for this lane will fail. Use a "
                "non-managed charter profile or a venue target instead."
            )
    return warnings
