"""External content-domain providers: ``providers.d`` discovery for agent-index.

Beyond the in-process :class:`~agent_index.sources.base.SourceConnector`
interface (a Python object registered inside the engine's own process), a
source domain may also join as an **external, cross-process provider**: a
self-contained CLI the provider ships, discovered by dropping a JSON manifest
into a standard ``providers.d/`` directory. This mirrors agent-bridge's
namespace-resolver ``providers.d`` pattern (``agent_bridge.provider_sources``)
-- the same discovery shape, reusing the same shared ``dropin_registry``
scan/classify primitive -- adapted to agent-index's four-method connector
protocol instead of agent-bridge's namespace-resolution verbs.

Manifest schema (``<providers-dir>/<name>.json``)::

    {
      "source_name": "gitea",              # required: registers as this source-name prefix
      "command": ["/abs/path/to/provider-cli"],  # required: absolute argv prefix
      "description": "Facility Gitea connector"    # optional: human label
    }

The engine drives ``<command...> content-<verb> --source <source>`` as a
subprocess for each connector operation -- it never imports or vendors the
provider's code. ``--source`` carries the **exact** source string the engine
resolved the connector for (the manifest's ``source_name``, or a hierarchical
sub-source such as ``"gitea:owner/repo"`` when the engine looked it up via
prefix match) -- a single provider manifest can serve a whole connector
*family*, distinguishing which member is being requested the same way a
built-in connector's own ``source`` constructor argument does. Each verb's
response is a small JSON object on stdout; a non-zero exit or malformed JSON
**fails closed** (raises :class:`ProviderError`) rather than being silently
treated as empty or trusted as-is:

- ``content-discover --source <source>`` -> ``{"entries": [{"path", "content",
  "language", "source", "metadata"}, ...]}``
- ``content-discover-changed --source <source> [--last-commit <sha>]`` -> same
  ``entries`` shape
- ``content-list-paths --source <source>`` -> ``{"paths": {"<source>":
  ["<path>", ...], ...}}``
- ``content-current-commit --source <source>`` -> ``{"commit": "<sha>" |
  null}``

A response's own ``"source"`` field(s) (in ``entries`` or ``content-list-
paths``' keys) are **verified against the requested ``--source``** -- an
entry must claim exactly that source or a strict descendant of it
(``source == requested`` or ``source.startswith(requested + ":")``); anything
else is rejected as :class:`ProviderError`. This bounds a provider's blast
radius to the subtree it was actually invoked for -- a misbehaving or
compromised provider cannot attribute content to an unrelated source.

A manifest whose ``source_name`` **overlaps** an already-registered prefix --
exactly, or hierarchically in either direction (``"git"`` vs. ``"git:foo"``)
-- is skipped with a warning finding, whether the collision is against a
built-in connector or another provider (including one discovered in the same
scan) -- it never silently shadows part of an existing registration.

Not yet implemented in this first slice (tracked as follow-up, not a silent
gap): mid-call cancellation of a running provider subprocess (``cancel_check``
is honored only *before* a call starts, not while one is in flight -- a
runaway provider is still bounded by the per-call timeout), and periodic
re-scanning on demand (this module's discovery runs once, at server startup).
"""

from __future__ import annotations

import json
import logging
import math
import os
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs
from dropin_registry import EntryDecision, Finding, scan_directory

from agent_index.sources.base import FileEntry

log = logging.getLogger(__name__)

REGISTRY_NAME = "providers.d"

#: Environment override for the provider-manifest directory (tests use it for
#: hermetic isolation; also an operator escape hatch).
PROVIDERS_DIR_ENV = "AGENT_INDEX_PROVIDERS_DIR"

#: Per-verb subprocess timeout. A provider CLI is expected to answer quickly
#: (agent-index's own connectors do); a slow provider should page its own work
#: rather than block indexing indefinitely.
DEFAULT_VERB_TIMEOUT = 60.0


def providers_dir() -> Path:
    """Resolve the ``providers.d`` directory (does not create it)."""
    override = os.environ.get(PROVIDERS_DIR_ENV)
    if override:
        return Path(override).expanduser()
    from agent_index.config import install_dir  # local import: avoid an import cycle

    return install_dir() / REGISTRY_NAME


def _verb_timeout() -> float:
    """Resolve the per-verb subprocess timeout from the environment.

    Rejects any non-finite (``inf``/``nan``) or non-positive override --
    ``subprocess.run(timeout=...)`` treats ``inf``/a huge value as effectively
    "never", and ``0``/negative as "expire immediately" or undefined, either
    of which would defeat the whole point of a bounded per-call timeout.
    """
    raw = os.environ.get("AGENT_INDEX_PROVIDER_TIMEOUT")
    if raw is None:
        return DEFAULT_VERB_TIMEOUT
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_VERB_TIMEOUT
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_VERB_TIMEOUT
    return value


@dataclass(frozen=True)
class ProviderManifest:
    """A validated content-domain-provider drop-in manifest."""

    source_name: str
    command: tuple[str, ...]
    description: str = ""
    source_path: str = ""


class ManifestError(ValueError):
    """A provider manifest was structurally invalid."""


class TargetUnusableError(ValueError):
    """A provider command exists but cannot be executed."""


class ProviderError(RuntimeError):
    """A content-domain provider CLI failed, or returned malformed data.

    Raised instead of returning an empty/partial result -- a caller (the
    indexing pipeline) treats this exactly like any other connector exception:
    this source fails for the current cycle, nothing else silently degrades.
    """


def parse_manifest(data: object, *, source_path: str = "") -> ProviderManifest:
    """Build a :class:`ProviderManifest` from parsed JSON.

    Raises :class:`ManifestError` on any structural problem so the caller can
    skip a single bad manifest without aborting discovery.
    """
    if not isinstance(data, dict):
        raise ManifestError("manifest root must be a JSON object")

    name = data.get("source_name")
    if not isinstance(name, str) or not name.strip():
        raise ManifestError("`source_name` is required and must be a non-empty string")
    # Normalize a trailing ':' the same way agent-bridge's namespace manifests
    # do -- an unstripped trailing colon would register a prefix that never
    # matches get_connector()'s hierarchical "source.startswith(prefix + ':')"
    # lookup (every real hierarchical source would need a literal double
    # colon to match), silently making the provider unreachable.
    normalized_name = name.strip().rstrip(":")
    if not normalized_name:
        raise ManifestError("`source_name` must contain more than just ':' characters")

    cmd = data.get("command")
    if (
        not isinstance(cmd, list)
        or not cmd
        or not all(isinstance(x, str) and x for x in cmd)
    ):
        raise ManifestError("`command` must be a non-empty array of strings")

    desc = data.get("description", "")
    if not isinstance(desc, str):
        raise ManifestError("`description` must be a string when present")

    return ProviderManifest(
        source_name=normalized_name, command=tuple(cmd), description=desc, source_path=source_path
    )


def _resolve_command(command: tuple[str, ...]) -> tuple[str, ...]:
    """Validate the provider's argv prefix.

    Deliberately requires an **absolute** ``command[0]`` -- no ``PATH``
    resolution (``shutil.which``) is attempted. The manifest contract is an
    absolute path by design (mirroring agent-bridge's resolved-argv
    manifests): a bare command name resolved via the *engine's* ``PATH`` could
    silently execute a different binary than the manifest author intended if
    ``PATH`` ever changes underneath it.
    """
    first = command[0]
    candidate = Path(first).expanduser()
    if not candidate.is_absolute():
        raise TargetUnusableError(
            f"provider command must be an absolute path, got {first!r}"
        )
    try:
        info = candidate.stat()
    except FileNotFoundError:
        raise
    except OSError as exc:
        # A missing file is the distinct "missing-target" finding (handled by
        # the caller); anything else (permission denied, a broken reparse
        # point, ...) is a clean "target-unusable" rather than bubbling up as
        # an indeterminate scan result that could keep a stale manifest alive.
        raise TargetUnusableError(f"provider command is not accessible: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise TargetUnusableError("provider command is not a regular file")
    if os.name != "nt" and not os.access(candidate, os.X_OK):
        raise TargetUnusableError("provider command is not executable")
    return (str(candidate), *command[1:])


def _classify_manifest(path: Path) -> EntryDecision[ProviderManifest]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        manifest = parse_manifest(data, source_path=str(path))
    except (json.JSONDecodeError, UnicodeDecodeError, ManifestError) as exc:
        return EntryDecision.inactive(
            Finding(
                registry=REGISTRY_NAME,
                entry=str(path),
                status="inactive",
                reason="invalid-entry",
                detail=str(exc),
                remedy=f"Fix or remove {path}.",
            )
        )

    try:
        command = _resolve_command(manifest.command)
    except FileNotFoundError:
        return EntryDecision.inactive(
            Finding(
                registry=REGISTRY_NAME,
                entry=str(path),
                status="inactive",
                reason="missing-target",
                target=manifest.command[0],
                remedy=f"Install the provider CLI or remove {path}.",
            )
        )
    except TargetUnusableError as exc:
        return EntryDecision.inactive(
            Finding(
                registry=REGISTRY_NAME,
                entry=str(path),
                status="inactive",
                reason="target-unusable",
                target=manifest.command[0],
                detail=str(exc),
                remedy=f"Fix the provider CLI's permissions or remove {path}.",
            )
        )

    manifest = ProviderManifest(
        source_name=manifest.source_name,
        command=command,
        description=manifest.description,
        source_path=manifest.source_path,
    )
    return EntryDecision.active(manifest)


@dataclass(frozen=True)
class ProviderRegistryReport:
    """A providers.d scan's reconciled, de-duplicated active manifests."""

    manifests: dict[str, ProviderManifest]
    findings: tuple[Finding, ...]


def scan_provider_registry(
    directory: str | os.PathLike[str] | None = None,
) -> ProviderRegistryReport:
    """Scan, reconcile, and de-duplicate provider manifests by ``source_name``."""
    root = Path(directory) if directory is not None else providers_dir()
    snapshot = scan_directory(
        root, _classify_manifest, registry=REGISTRY_NAME, suffixes=(".json",)
    )
    entries = snapshot.reconcile()
    manifests: dict[str, ProviderManifest] = {}
    findings = list(snapshot.findings)
    for entry_path, manifest in sorted(entries.items()):
        prior = manifests.get(manifest.source_name)
        if prior is None:
            manifests[manifest.source_name] = manifest
            continue
        findings.append(
            Finding(
                registry=REGISTRY_NAME,
                entry=entry_path,
                status="inactive",
                reason="duplicate",
                target=manifest.source_name,
                remedy=f"Remove {entry_path} or the conflicting {prior.source_path}.",
                detail=f"source_name already claimed by {prior.source_path}",
            )
        )
    return ProviderRegistryReport(manifests=manifests, findings=tuple(findings))


class CliSourceConnector:
    """Drive an external content-domain provider over a process boundary.

    Implements the :class:`~agent_index.sources.base.SourceConnector` protocol
    by shelling out to the provider's own CLI (``<command...>
    content-<verb>``) instead of importing the provider's code -- so a
    provider fix reaches indexing from the provider's OWN process/venv with no
    agent-index redeploy, and the engine never links or imports anything
    provider-specific.

    Fails **closed**: a non-zero exit or a malformed/incomplete JSON response
    raises :class:`ProviderError` rather than being treated as empty or
    trusted as-is.
    """

    def __init__(self, source: str, manifest: ProviderManifest) -> None:
        self._source = source
        self._manifest = manifest

    @property
    def source_name(self) -> str:
        return self._source

    def _run(self, verb: str, *args: str) -> Any:
        cmd = [*self._manifest.command, verb, *args]
        try:
            result = subprocess.run(  # noqa: S603 -- provider path is operator-configured
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_verb_timeout(),
                check=False,
                **no_window_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProviderError(
                f"content-domain provider {self._manifest.source_name!r} "
                f"failed to run {verb!r}: {exc}"
            ) from exc
        if result.returncode != 0:
            raise ProviderError(
                f"content-domain provider {self._manifest.source_name!r} "
                f"{verb!r} exited {result.returncode}: {result.stderr.strip()[:500]}"
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"content-domain provider {self._manifest.source_name!r} "
                f"{verb!r} returned malformed JSON: {exc}"
            ) from exc

    def _entries(self, data: Any, verb: str) -> list[FileEntry]:
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            raise ProviderError(
                f"content-domain provider {self._manifest.source_name!r} "
                f"{verb!r} response missing an 'entries' array"
            )
        out: list[FileEntry] = []
        for i, raw in enumerate(data["entries"]):
            if not isinstance(raw, dict):
                raise ProviderError(
                    f"content-domain provider {self._manifest.source_name!r} "
                    f"{verb!r} entry {i} is not an object"
                )
            try:
                path, content, language, source = (
                    raw["path"],
                    raw["content"],
                    raw["language"],
                    raw["source"],
                )
            except KeyError as exc:
                raise ProviderError(
                    f"content-domain provider {self._manifest.source_name!r} "
                    f"{verb!r} entry {i} missing required field {exc}"
                ) from exc
            if not all(isinstance(v, str) for v in (path, content, language, source)):
                raise ProviderError(
                    f"content-domain provider {self._manifest.source_name!r} "
                    f"{verb!r} entry {i} has a non-string required field"
                )
            metadata = raw.get("metadata", {})
            if not isinstance(metadata, dict):
                raise ProviderError(
                    f"content-domain provider {self._manifest.source_name!r} "
                    f"{verb!r} entry {i} 'metadata' must be an object"
                )
            if not self._owns_source(source):
                raise ProviderError(
                    f"content-domain provider {self._manifest.source_name!r} "
                    f"{verb!r} entry {i} claims source {source!r}, outside the "
                    f"requested {self._source!r} subtree -- rejected"
                )
            out.append(
                FileEntry(
                    path=path, content=content, language=language, source=source, metadata=metadata
                )
            )
        return out

    def _owns_source(self, source: str) -> bool:
        """Whether ``source`` is the requested source itself or a strict
        descendant of it (``self._source`` or ``self._source + ":..."``).

        A provider is invoked for exactly one source (``--source
        <self._source>``); trusting an arbitrary ``source`` value in its
        response verbatim would let a misbehaving or compromised provider
        inject content that agent-index attributes to a *different* source --
        outside the subtree it was asked about, and potentially interfering
        with another connector's data. Rejecting out-of-subtree entries keeps
        a provider's blast radius limited to what it was actually invoked for.
        """
        return source == self._source or source.startswith(f"{self._source}:")

    def discover(self, cancel_check: Callable[[], None] | None = None) -> list[FileEntry]:
        if cancel_check is not None:
            cancel_check()
        data = self._run("content-discover", "--source", self._source)
        return self._entries(data, "content-discover")

    def discover_changed(
        self,
        last_commit: str | None,
        cancel_check: Callable[[], None] | None = None,
    ) -> list[FileEntry]:
        if cancel_check is not None:
            cancel_check()
        args = ("--last-commit", last_commit) if last_commit else ()
        data = self._run("content-discover-changed", "--source", self._source, *args)
        return self._entries(data, "content-discover-changed")

    def list_paths(
        self, cancel_check: Callable[[], None] | None = None
    ) -> dict[str, set[str]]:
        if cancel_check is not None:
            cancel_check()
        data = self._run("content-list-paths", "--source", self._source)
        if not isinstance(data, dict) or not isinstance(data.get("paths"), dict):
            raise ProviderError(
                f"content-domain provider {self._manifest.source_name!r} "
                "'content-list-paths' response missing a 'paths' object"
            )
        result: dict[str, set[str]] = {}
        for key, values in data["paths"].items():
            if not isinstance(key, str) or not isinstance(values, list) or not all(
                isinstance(v, str) for v in values
            ):
                raise ProviderError(
                    f"content-domain provider {self._manifest.source_name!r} "
                    f"'content-list-paths' entry {key!r} is malformed"
                )
            if not self._owns_source(key):
                raise ProviderError(
                    f"content-domain provider {self._manifest.source_name!r} "
                    f"'content-list-paths' claims source {key!r}, outside the "
                    f"requested {self._source!r} subtree -- rejected"
                )
            result[key] = set(values)
        return result

    def current_commit(self) -> str | None:
        data = self._run("content-current-commit", "--source", self._source)
        if not isinstance(data, dict) or "commit" not in data:
            raise ProviderError(
                f"content-domain provider {self._manifest.source_name!r} "
                "'content-current-commit' response missing 'commit'"
            )
        commit = data["commit"]
        if commit is not None and not isinstance(commit, str):
            raise ProviderError(
                f"content-domain provider {self._manifest.source_name!r} "
                "'content-current-commit' 'commit' must be a string or null"
            )
        return commit


def _prefixes_overlap(a: str, b: str) -> bool:
    """Whether source-name prefixes ``a`` and ``b`` would shadow each other in
    :func:`agent_index.sources.get_connector`'s longest-prefix-match lookup --
    an exact match, or either being a hierarchical ancestor of the other
    (``"git"`` vs. ``"git:foo"``, in either direction)."""
    return a == b or a.startswith(f"{b}:") or b.startswith(f"{a}:")


def discover_and_register_providers(
    directory: str | os.PathLike[str] | None = None,
) -> ProviderRegistryReport:
    """Scan ``providers.d`` and register a :class:`CliSourceConnector` factory
    per active manifest into the connector registry (additive; never touches
    built-in in-process connectors -- a manifest whose ``source_name``
    **overlaps** an already-registered prefix -- exactly, or hierarchically in
    either direction (``"git"`` vs. ``"git:foo"``) -- is skipped with a
    warning finding rather than silently shadowing part of it). The returned
    report's ``manifests`` reflects only what was actually registered --
    a collision-skipped manifest appears in ``findings``, never in
    ``manifests``, so the two stay consistent with the live registry. Findings
    for skipped/malformed/colliding entries are logged as warnings, never
    raised -- one bad manifest never blocks startup or the other, valid
    providers.
    """
    from agent_index.sources import register_connector, registered_source_prefixes

    report = scan_provider_registry(directory)
    findings = list(report.findings)
    # Seeded from the registry as it stands before this scan (built-ins plus
    # anything already registered by an earlier scan), then grown as THIS
    # scan's providers are accepted -- so two providers in the same pass that
    # hierarchically overlap each other are caught too, not just overlaps
    # against pre-existing registrations.
    claimed = set(registered_source_prefixes())
    registered: dict[str, ProviderManifest] = {}
    for name, manifest in sorted(report.manifests.items()):
        colliding = next(
            (existing for existing in claimed if _prefixes_overlap(name, existing)), None
        )
        if colliding is not None:
            findings.append(
                Finding(
                    registry=REGISTRY_NAME,
                    entry=manifest.source_path,
                    status="inactive",
                    reason="prefix-collision",
                    target=name,
                    remedy=(
                        f"Choose a source_name that doesn't overlap "
                        f"{colliding!r}, or remove {manifest.source_path}."
                    ),
                    detail=(
                        f"{name!r} overlaps the already-registered prefix "
                        f"{colliding!r} (a built-in or another provider)"
                    ),
                )
            )
            continue
        register_connector(
            name,
            lambda *, source, _manifest=manifest, **_kwargs: CliSourceConnector(source, _manifest),
        )
        claimed.add(name)
        registered[name] = manifest
    for finding in findings:
        log.warning("content-domain provider issue: %s", finding.to_dict())
    return ProviderRegistryReport(manifests=registered, findings=tuple(findings))
