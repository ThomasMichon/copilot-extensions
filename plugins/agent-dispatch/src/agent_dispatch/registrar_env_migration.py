"""Migration: legacy ``AGENT_DISPATCH_SUPERVISE_*`` env profile -> declaration.

Split out of :mod:`agent_dispatch.registrar` (module-size cap); the registrar
re-exports :func:`declaration_from_env`. Behavior is unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping

from .registrar import ProfileDeclaration, RegistrarError, _as_str_tuple, load_declaration


_ENV_PREFIX = "AGENT_DISPATCH_SUPERVISE_"

#: EXTRA_ARGS value flags (take an argument) parsed on migration so a legacy
#: profile converts losslessly. The bare flags (--all-repos, --no-heartbeat,
#: --no-reactive, --headless) are matched directly in :func:`_parse_extra_args`.
_EXTRA_VALUE_KEYS = ("--repo", "--pool", "--origin", "--headless-agent", "--evaluator")


def _env_int(raw: str | None, *, key: str, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RegistrarError(f"{key}: expected an integer, got {raw!r}") from exc


def _env_float(raw: str | None, *, key: str, default: float) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RegistrarError(f"{key}: expected a number, got {raw!r}") from exc


def declaration_from_env(name: str, env: Mapping[str, str]) -> ProfileDeclaration:
    """Convert a legacy ``AGENT_DISPATCH_SUPERVISE_*`` env profile into a declaration.

    The migration path (registrar effort Phase 4): existing ``supervisors/<name>.env``
    profiles become declarations without behavior change. ``name`` is the profile
    name (the env file's stem). Unrecognized ``EXTRA_ARGS`` are ignored here (they
    are surfaced by the migration tool, not silently dropped in production).
    """
    def g(key: str) -> str | None:
        v = env.get(_ENV_PREFIX + key)
        return v.strip() if isinstance(v, str) and v.strip() else None

    labels = _as_str_tuple(g("LABELS"), key="LABELS")
    headless_labels = _as_str_tuple(g("HEADLESS_LABELS"), key="HEADLESS_LABELS")
    cli_labels = _as_str_tuple(g("CLI_LABELS"), key="CLI_LABELS")
    headless_agent = g("HEADLESS_AGENT")
    backend = g("EMBODY_BACKEND")
    if backend is not None and backend not in ("headless", "cli"):
        raise RegistrarError(
            f"EMBODY_BACKEND: must be 'headless' or 'cli', got {backend!r}"
        )

    label_max: dict[str, int] = {}
    if (raw := g("LABEL_MAX_ATTEMPTS")) is not None:
        for pair in raw.replace(",", " ").split():
            if "=" not in pair:
                raise RegistrarError(f"LABEL_MAX_ATTEMPTS: expected LABEL=N, got {pair!r}")
            lbl, _, num = pair.partition("=")
            try:
                label_max[lbl] = int(num)
            except ValueError as exc:
                raise RegistrarError(f"LABEL_MAX_ATTEMPTS: N must be int, got {num!r}") from exc

    extra = _parse_extra_args((g("EXTRA_ARGS") or "").split())
    # The dedicated HEADLESS_AGENT var wins; an EXTRA_ARGS --headless-agent is a fallback.
    agent = headless_agent or extra["headless_agent"]
    # Embody backend defaults to HEADLESS. Only an explicit EMBODY_BACKEND=cli makes
    # the lane CLI-default; HEADLESS_LABELS alone no longer forces CLI (it is the
    # headless subset for a cli lane, redundant on a headless one). So a plain file
    # is headless, and an existing HEADLESS_LABELS=<all watched> file stays headless.
    body_type = "embody" if backend == "cli" else "headless"

    body: dict[str, object] = {"type": body_type}
    if agent:
        body["agent"] = agent
    if body_type == "embody" and headless_labels:
        body["headless_labels"] = list(headless_labels)
    if body_type == "headless" and cli_labels:
        body["cli_labels"] = list(cli_labels)

    data: dict[str, object] = {
        "name": name,
        "labels": list(labels),
        "repos": extra["repo"] or "all",
        "concurrency": _env_int(g("MAX_CONCURRENT"), key="MAX_CONCURRENT", default=1),
        "interval": _env_float(g("INTERVAL"), key="INTERVAL", default=30.0),
        "max_attempts": _env_int(g("MAX_ATTEMPTS"), key="MAX_ATTEMPTS", default=3),
        "label_max_attempts": label_max,
        "heartbeat": not extra["no_heartbeat"],
        "reactive": False,
        "body": body,
    }
    if extra["pool"]:
        data["fleet"] = {
            "pool": extra["pool"],
            **({"origin": extra["origin"]} if extra["origin"] else {}),
            "headless": extra["headless"],
        }
    if extra["evaluator"]:
        data["evaluator"] = extra["evaluator"]
    return load_declaration(data)


def _parse_extra_args(tokens: list[str]) -> dict[str, object]:
    """Light parse of the legacy EXTRA_ARGS flags relevant to a declaration."""
    out: dict[str, object] = {
        "all_repos": False,
        "no_heartbeat": False,
        "no_reactive": False,
        "headless": False,
        "repo": None,
        "pool": [],
        "origin": None,
        "headless_agent": None,
        "evaluator": None,
    }
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--all-repos":
            out["all_repos"] = True
        elif tok == "--no-heartbeat":
            out["no_heartbeat"] = True
        elif tok == "--no-reactive":
            out["no_reactive"] = True
        elif tok == "--headless":
            out["headless"] = True
        elif tok in _EXTRA_VALUE_KEYS and i + 1 < len(tokens):
            val = tokens[i + 1]
            if tok == "--repo":
                out["repo"] = val
            elif tok == "--pool":
                out["pool"] = [p for p in val.replace(",", " ").split() if p]
            elif tok == "--origin":
                out["origin"] = val
            elif tok == "--headless-agent":
                out["headless_agent"] = val
            elif tok == "--evaluator":
                out["evaluator"] = val
            i += 1
        i += 1
    return out
