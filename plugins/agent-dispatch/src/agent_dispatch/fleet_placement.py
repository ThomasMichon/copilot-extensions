"""Pluggable placement ordering for a fleet lane's managed pool hosts.

A fleet lane may declare ``fleet.placement_provider = {name, hosts}``. ``hosts``
names the **managed** subset of ``fleet.pool`` whose order an external provider
decides each supervisor cycle; every other pool host is **unmanaged** and keeps
its configured position. The declaration carries only the provider *name*: the
argv and its whole-call timeout come from the supervisor host's trusted
``placement-providers.json`` (``AGENT_DISPATCH_PLACEMENT_PROVIDERS`` overrides
the path), never from the declaration, so a lane declaration cannot choose what
the supervisor executes. An unknown name, an invalid entry, or ``hosts`` that
are not a non-empty subset of the pool refuses the lane (fail closed).

Protocol (version 1): the provider reads ``{"version": 1, "candidates": [...]}``
(the full managed host set) on stdin and writes ``{"version": 1, "ordered":
[...]}`` -- a duplicate-free ordered subset of the candidates -- on stdout. A
managed host it omits takes no work this cycle. Any failure (spawn error,
nonzero exit, timeout, bad JSON/version/ids) disables only the managed hosts
for this cycle; unmanaged hosts stay eligible.

The provider runs at most once per lane per cycle, lazily, the first time a
task's candidates include a managed host; :meth:`PlacementOrdering.begin_cycle`
discards the result so nothing is cached across cycles. Each lane runs in its
own supervise process, so a slow provider only slows its own lane.
"""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("agent-dispatch.fleet")

PROTOCOL_VERSION = 1
PROVIDERS_ENV = "AGENT_DISPATCH_PLACEMENT_PROVIDERS"
#: Upper bound for a configured whole-call timeout, so one provider cannot stall
#: its lane for an unbounded part of a cycle.
MAX_TIMEOUT_SECONDS = 300.0
_DECLARATION_KEYS = frozenset({"name", "hosts"})
_PROVIDER_KEYS = frozenset({"argv", "timeout_seconds"})


class PlacementConfigError(ValueError):
    """A placement declaration or trusted provider entry is unusable."""


class PlacementProviderError(RuntimeError):
    """One provider call failed; its managed hosts sit out this cycle."""


@dataclass(frozen=True)
class PlacementDeclaration:
    """A lane's ``fleet.placement_provider`` -- a provider name + managed hosts."""

    name: str
    hosts: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {"name": self.name, "hosts": list(self.hosts)}

    def flag_argv(self) -> list[str]:
        """The `supervise --placement-provider` flag pair for this declaration."""
        return ["--placement-provider", json.dumps(self.to_json(), sort_keys=True)]


@dataclass(frozen=True)
class ProviderCommand:
    """A trusted provider entry from the supervisor host's own config."""

    argv: tuple[str, ...]
    timeout: float


def parse_declaration(value: object, pool: Sequence[str]) -> PlacementDeclaration:
    """Validate ``fleet.placement_provider`` against the lane's ``pool``.

    ``hosts`` must be a non-empty, duplicate-free subset of ``pool``; a host
    matches a pool entry case-insensitively and is stored in the pool's
    spelling (the spelling the fleet probes and passes as ``--machine``).
    """
    if not isinstance(value, Mapping):
        raise PlacementConfigError("fleet.placement_provider: expected a mapping")
    extra = sorted(str(key) for key in value if key not in _DECLARATION_KEYS)
    if extra:
        raise PlacementConfigError(f"fleet.placement_provider: unknown key(s) {extra}")
    name = value.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PlacementConfigError("fleet.placement_provider.name: expected a non-empty string")
    hosts = value.get("hosts")
    if not isinstance(hosts, list) or not hosts:
        raise PlacementConfigError("fleet.placement_provider.hosts: expected a non-empty list")
    by_key = {host.strip().lower(): host.strip() for host in pool if host.strip()}
    resolved: list[str] = []
    for host in hosts:
        if not isinstance(host, str) or host.strip().lower() not in by_key:
            raise PlacementConfigError(
                f"fleet.placement_provider.hosts: {host!r} is not in fleet.pool"
            )
        canonical = by_key[host.strip().lower()]
        if canonical in resolved:
            raise PlacementConfigError(
                f"fleet.placement_provider.hosts: duplicate host {host!r}"
            )
        resolved.append(canonical)
    return PlacementDeclaration(name=name.strip(), hosts=tuple(resolved))


def declaration_argv(value: object, pool: Sequence[str]) -> list[str]:
    """The ``supervise`` flag pair for a declaration, or ``[]`` when absent."""
    if value is None:
        return []
    return parse_declaration(value, pool).flag_argv()


def providers_path() -> Path:
    """The supervisor host's trusted placement-provider config file."""
    from .install_paths import install_dir

    return Path(os.environ.get(PROVIDERS_ENV) or (install_dir() / "placement-providers.json"))


def load_provider(name: str, *, path: Path | None = None) -> ProviderCommand:
    """Look up ``name`` in the trusted config; refuse anything unusable."""
    source = path or providers_path()
    try:
        data: object = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PlacementConfigError(
            f"placement provider {name!r}: no trusted provider config at {source}"
        ) from None
    except (OSError, ValueError) as exc:
        raise PlacementConfigError(f"placement providers {source}: unreadable ({exc})") from None
    providers = data.get("providers") if isinstance(data, Mapping) else None
    entry = providers.get(name) if isinstance(providers, Mapping) else None
    if entry is None:
        raise PlacementConfigError(f"placement provider {name!r} is not configured in {source}")
    if not isinstance(entry, Mapping) or set(entry) != _PROVIDER_KEYS:
        raise PlacementConfigError(
            f"placement provider {name!r}: expected exactly {sorted(_PROVIDER_KEYS)}"
        )
    argv = entry["argv"]
    if (
        not isinstance(argv, list) or not argv
        or not all(isinstance(arg, str) and arg for arg in argv)
    ):
        raise PlacementConfigError(f"placement provider {name!r}: argv must be non-empty strings")
    timeout = entry["timeout_seconds"]
    if (
        isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        or not 0 < timeout <= MAX_TIMEOUT_SECONDS
    ):
        raise PlacementConfigError(
            f"placement provider {name!r}: timeout_seconds must be in (0, {MAX_TIMEOUT_SECONDS:g}]"
        )
    return ProviderCommand(argv=tuple(str(arg) for arg in argv), timeout=float(timeout))


def _capture(command: ProviderCommand, payload: str) -> tuple[int, str]:
    """Run ``command`` with ``payload`` on stdin inside its whole-call budget.

    The budget covers process-tree cleanup: Windows uses the owned Job
    execution, POSIX reserves part of the budget for the group kill.
    """
    if sys.platform == "win32":
        from agent_procutil.owned_process import OwnedCleanupError

        from .procutil import run_background_capture

        try:
            result = run_background_capture(
                command.argv, timeout=command.timeout, input_text=payload, encoding="utf-8",
            )
        except OwnedCleanupError as exc:
            raise PlacementProviderError(f"cleanup unconfirmed: {exc}") from None
        except UnicodeError:
            # The owned path settles the Job before decoding, so no child remains.
            raise PlacementProviderError("stdout is not UTF-8") from None
        if result is None:
            raise PlacementProviderError(f"failed or timed out after {command.timeout:g}s")
        return result.returncode, result.stdout
    return _capture_posix(command, payload)


def _capture_posix(command: ProviderCommand, payload: str) -> tuple[int, str]:
    from .procutil import terminate_process_tree

    reserve = min(2.0, command.timeout / 2)
    try:
        proc = subprocess.Popen(  # noqa: S603 -- trusted, operator-configured argv
            list(command.argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError as exc:
        raise PlacementProviderError(f"could not start: {exc}") from None
    try:
        raw, _stderr = proc.communicate(
            payload.encode("utf-8"), timeout=command.timeout - reserve,
        )
    except subprocess.TimeoutExpired:
        terminate_process_tree(proc, grace=reserve / 2)
        raise PlacementProviderError(f"timed out after {command.timeout:g}s") from None
    except (OSError, subprocess.SubprocessError) as exc:
        terminate_process_tree(proc, grace=reserve / 2)
        raise PlacementProviderError(f"failed: {exc}") from None
    try:
        stdout = raw.decode("utf-8")
    except UnicodeError:
        # Reap any descendant still in the provider's session before failing.
        terminate_process_tree(proc, grace=reserve / 2)
        raise PlacementProviderError("stdout is not UTF-8") from None
    return proc.returncode, stdout


class ProviderOrder(tuple[str, ...]):
    """A validated provider ordering, plus any optional per-host ``scores``."""

    scores: dict[str, float]

    def __new__(cls, ordered: Sequence[str], scores: Mapping[str, float] | None = None) -> ProviderOrder:
        self = super().__new__(cls, ordered)
        self.scores = dict(scores or {})
        return self


def _scores(data: Mapping[str, object], known: set[str]) -> dict[str, float]:
    """Optional ``scores`` map (host -> number); recorded for audit, never ordering."""
    raw = data.get("scores")
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise PlacementProviderError("'scores' must be an object")
    scores: dict[str, float] = {}
    for host, value in raw.items():
        if host not in known:
            raise PlacementProviderError(f"unknown scored host {host!r}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise PlacementProviderError(f"score for {host!r} must be a finite number")
        scores[host] = float(value)
    return scores


def run_provider(command: ProviderCommand, candidates: Sequence[str]) -> ProviderOrder:
    """Call the provider once; return its validated ordering or raise."""
    payload = json.dumps({"version": PROTOCOL_VERSION, "candidates": list(candidates)})
    returncode, stdout = _capture(command, payload)
    if returncode != 0:
        raise PlacementProviderError(f"exited {returncode}")
    try:
        data: object = json.loads(stdout)
    except ValueError:
        raise PlacementProviderError("stdout is not JSON") from None
    if not isinstance(data, Mapping) or data.get("version") != PROTOCOL_VERSION:
        raise PlacementProviderError(f"expected a version {PROTOCOL_VERSION} object")
    ordered = data.get("ordered")
    if not isinstance(ordered, list):
        raise PlacementProviderError("'ordered' must be a list")
    known = set(candidates)
    result: list[str] = []
    for host in ordered:
        if not isinstance(host, str) or host not in known:
            raise PlacementProviderError(f"unknown host {host!r}")
        if host in result:
            raise PlacementProviderError(f"duplicate host {host!r}")
        result.append(host)
    return ProviderOrder(result, _scores(data, known))


ProviderRunner = Callable[[ProviderCommand, Sequence[str]], tuple[str, ...]]


class PlacementOrdering:
    """Per-lane provider state: one call per cycle, merged into each task's order."""

    def __init__(
        self,
        declaration: PlacementDeclaration,
        command: ProviderCommand,
        *,
        runner: ProviderRunner = run_provider,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.declaration = declaration
        self.command = command
        self.managed = frozenset(declaration.hosts)
        self._runner = runner
        self._clock = clock
        self._called = False
        self._ordered: tuple[str, ...] | None = None
        #: The last call's failure text (``None`` after a successful call).
        self.last_error: str | None = None
        #: Provider calls made (all cycles) and the last call's wall time, in seconds.
        self.calls = 0
        self.last_duration: float | None = None

    def begin_cycle(self) -> None:
        """Forget the previous cycle's result; the next need calls again."""
        self._called = False
        self._ordered = None
        self.last_error = None

    def audit(self) -> dict[str, object]:
        """This cycle's provider exchange, for the placement decision record."""
        ordered = self._ordered
        record: dict[str, object] = {
            "provider": self.declaration.name,
            "candidates_sent": list(self.declaration.hosts),
            "provider_called": self._called,
            "provider_ordered": None if ordered is None else list(ordered),
        }
        scores = getattr(ordered, "scores", None)
        if scores:
            record["provider_scores"] = dict(scores)
        if self.last_error is not None:
            record["provider_error"] = self.last_error
        return record

    def _provider_order(self) -> tuple[str, ...] | None:
        if not self._called:
            self._called = True
            self.calls += 1
            started = self._clock()
            try:
                self._ordered = self._runner(self.command, self.declaration.hosts)
                self.last_error = None
            except PlacementProviderError as exc:
                self._ordered = None
                self.last_error = str(exc)
                log.warning(
                    "placement provider %r %s; managed hosts %s skip this cycle",
                    self.declaration.name, exc, ", ".join(self.declaration.hosts),
                )
            self.last_duration = self._clock() - started
        return self._ordered

    def order(self, candidates: Sequence[str]) -> list[str]:
        """Merge the provider order into ``candidates`` (already in pool order).

        Unmanaged hosts keep their positions; the managed positions are
        refilled in provider order. Managed hosts the provider omitted, or all
        managed hosts after a failed call, are dropped.
        """
        if not any(host in self.managed for host in candidates):
            return list(candidates)
        ordered = self._provider_order() or ()
        present = set(candidates)
        refill = iter([host for host in ordered if host in present])
        merged: list[str] = []
        for host in candidates:
            if host not in self.managed:
                merged.append(host)
            elif (replacement := next(refill, None)) is not None:
                merged.append(replacement)
        return merged


def placement_from_flag(flag: str | None, pool: Sequence[str]) -> PlacementOrdering | None:
    """Build the lane's ordering from ``--placement-provider``; ``None`` when unset."""
    if not flag:
        return None
    if not pool:
        raise PlacementConfigError("--placement-provider requires --pool")
    try:
        value: object = json.loads(flag)
    except ValueError:
        raise PlacementConfigError("--placement-provider: expected a JSON object") from None
    declaration = parse_declaration(value, pool)
    return PlacementOrdering(declaration, load_provider(declaration.name))
