"""Claim-provider drop-in registry (claim-provider-pattern effort).

``agent-worktrees`` owns the claims ledger (``claims add|release|settle|
sweep|mirror-status|cleanup|orphans``), but several claimable resource kinds
-- a CodeSpace, a container, a dispatch task -- are actually owned by
higher-tier siblings in the suite's plugin stack (agent-codespaces,
agent-containers, agent-dispatch). Rather than agent-worktrees hardcoding a
call to each sibling's CLI (an **upward** call, forbidden by the plugin-stack
layering rule -- see ``docs/patterns/a-la-carte-independence.md``), a
claim-owning plugin registers as a **claim provider**: it ships a small
drop-in manifest declaring the claim **namespace** it serves (the ``<prefix>:``
of a namespaced claim ref, e.g. ``codespace:``) and one or both **callback**
argv templates agent-worktrees invokes on demand.

This mirrors the existing cross-plugin **pivot** registry
(:mod:`agent_worktrees.picker_support.pivot_manifest`) and **claim-kind**
registry (:mod:`agent_worktrees.claim_kinds_registry`) already used elsewhere
in this same plugin: a contributing plugin drops a static template at
``<plugin_root>/claim-providers/<namespace>.json`` in its own payload (no
sessionStart hook, no separate registration step -- the manifest ships with
the plugin's own installed version and is always current). agent-worktrees
scans the installed-plugins tree directly, verifies the contributing plugin's
identity via ``plugin_activation.resolve_active_plugins()``, and resolves each
declared command to the plugin's own **payload-local** binstub
(``<plugin_root>/bin/<command>[.cmd]``) -- never an ambient ``PATH`` lookup of
a higher-tier sibling, matching the marketplace-scoped-installations Phase 2
payload-local-invocation policy.

Manifest schema (``<plugin_root>/claim-providers/<namespace>.json``)::

    {
      "schema_version": 1,
      "namespace": "codespace",              # required: the claim-ref prefix
      "status_command": ["agent-codespaces"],  # optional: see below
      "reclaim_command": ["agent-codespaces"], # optional: see below
      "description": "GitHub Codespaces"       # optional: human label
    }

At least one of ``status_command``/``reclaim_command`` is required. Callback
contract (invoked with the claim ref's bare identifier, namespace prefix
already stripped):

* ``<status_command...> claim-status <ref>`` -> a JSON object on stdout, at
  minimum ``{"exists": bool}``; optional ``"state"``/``"detail"`` strings.
  Any parse failure, non-zero exit, or timeout degrades to
  ``{"available": False, "reason": "..."}"`` -- never raises.
* ``<reclaim_command...> claim-reclaim <ref> [--apply]`` -> a JSON object,
  at minimum ``{"reclaimed": bool}``; optional ``"detail"``. Without
  ``--apply`` the provider must not act (dry-run preview only), matching
  agent-worktrees' own ``claims cleanup`` dry-run-by-default convention.

A missing, absent, or malformed provider manifest degrades only that one
namespace's status/reclaim resolution -- never any of agent-worktrees' own
claims commands, exactly like the pivot and claim-kind registries.
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agent_procutil import no_window_kwargs
from dropin_registry import EntryDecision, EntryStatus, Finding, scan_directory
from plugin_activation import ActivationReport, resolve_active_plugins

from .claim_kinds_registry import installed_plugins_dir

log = logging.getLogger("agent-worktrees")

CLAIM_PROVIDERS_SUBDIR = "claim-providers"
REGISTRY_NAME = "claim-providers"
_CALLBACK_TIMEOUT_SECONDS = 15.0

#: cmd.exe's own structurally-significant characters, scanned for in a
#: resolved ``.cmd``/``.bat`` path before ever routing it through
#: ``cmd.exe /c`` (see ``_resolve_command``'s own doc comment for why
#: ordinary ``subprocess`` argv quoting cannot make this safe on its own).
#: ``!`` is included because this call site never disables delayed
#: variable expansion, under which cmd.exe treats ``!`` as significant too.
_CMD_METACHAR_RE = re.compile(r'[&|<>^%!"]')


#: Namespace and identifier characters this module ever passes through to a
#: callback command line. Deliberately conservative (matches typical
#: CodeSpace/container/task-id shapes: alnum plus a small, unambiguous
#: punctuation set) -- rejecting anything else, rather than trying to escape
#: it, is what actually closes cmd.exe metacharacter injection at the
#: source rather than merely narrowing it. The FIRST character may not be
#: `-`: a leading-dash identifier (e.g. a ref like "codespace:--apply")
#: could otherwise be parsed as an option/flag by a callback's own CLI
#: argument parser instead of a positional ref -- for
#: ``resolve_claim_reclaim`` specifically, a caller-controlled
#: ``"--apply"`` identifier would let a dry-run call
#: (``apply=False``) still append a literal ``--apply`` token the
#: callback's own parser could interpret as ITS ``--apply`` flag,
#: silently turning a preview into a real reclaim.
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._/][A-Za-z0-9._/-]*$")


class ManifestError(ValueError):
    """A claim-provider manifest was structurally invalid."""


class TargetUnusableError(ValueError):
    """A declared command exists but cannot satisfy its contract."""


@dataclass(frozen=True)
class ClaimProviderManifest:
    """A validated, identity-attributed claim-provider drop-in manifest."""

    namespace: str
    plugin: str
    plugin_root: str
    status_command: tuple[str, ...] | None = None
    reclaim_command: tuple[str, ...] | None = None
    description: str = ""
    source_path: str = ""


def parse_manifest(data: object, *, source_path: str = "") -> ClaimProviderManifest:
    """Build a :class:`ClaimProviderManifest` from parsed JSON.

    Raises :class:`ManifestError` on any structural problem so the caller can
    skip a single bad manifest without aborting discovery.
    """
    if not isinstance(data, dict):
        raise ManifestError("manifest root must be a JSON object")

    schema_version = data.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != 1:
        raise ManifestError("`schema_version` must be the integer 1")

    ns = data.get("namespace")
    if not isinstance(ns, str) or not ns.strip():
        raise ManifestError("`namespace` is required and must be a non-empty string")
    ns = ns.strip().rstrip(":")
    if not ns:
        raise ManifestError("`namespace` must not be empty after stripping ':'")
    if not _SAFE_TOKEN_RE.match(ns):
        raise ManifestError(
            "`namespace` must match the safe-token pattern (matches "
            "split_namespaced_ref's own validation, so a valid manifest "
            "namespace can always actually be selected by a ref)"
        )

    def _argv(field: str) -> tuple[str, ...] | None:
        value = data.get(field)
        if value is None:
            return None
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(x, str) and x and "\x00" not in x for x in value)
        ):
            raise ManifestError(
                f"`{field}` must be a non-empty array of non-empty strings "
                "with no embedded NUL when present"
            )
        return tuple(value)

    status_command = _argv("status_command")
    reclaim_command = _argv("reclaim_command")
    if status_command is None and reclaim_command is None:
        raise ManifestError(
            "at least one of `status_command`/`reclaim_command` is required"
        )

    desc = data.get("description", "")
    if not isinstance(desc, str):
        raise ManifestError("`description` must be a string when present")

    return ClaimProviderManifest(
        namespace=ns,
        plugin="",  # filled in by the caller once the plugin_root is known
        plugin_root="",
        status_command=status_command,
        reclaim_command=reclaim_command,
        description=desc,
        source_path=source_path,
    )


def _payload_command(root: Path, command: str) -> Path | None:
    """Resolve a bare command name to the plugin's own payload-local binstub.

    Mirrors ``picker_support.pivot_targets._payload_command``: only
    ``root/bin/<command>[.cmd]`` is ever considered -- never ambient ``PATH``
    -- so a claim-provider callback can only ever run the exact binstub the
    identity-verified plugin itself shipped.
    """
    if Path(command).name != command:
        return None
    candidates = [root / "bin" / command]
    if os.name == "nt":
        candidates = [root / "bin" / f"{command}.cmd", *candidates]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _is_reparse(info: os.stat_result) -> bool:
    """Whether a Windows stat result names a reparse point (mirrors
    ``picker_support.pivot_targets._is_reparse``)."""
    return bool(
        getattr(info, "st_file_attributes", 0) & 0x400 or getattr(info, "st_reparse_tag", 0)
    )


def _resolve_command(command: tuple[str, ...], *, root: Path) -> tuple[str, ...]:
    first = command[0]
    payload = _payload_command(root, first)
    if payload is None:
        raise FileNotFoundError(first)
    # lstat the candidate itself -- BEFORE any resolve() -- so a symlink or
    # reparse point planted at bin/<command> is rejected outright rather
    # than silently followed to whatever it points at (which could sit
    # outside the identity-verified plugin root entirely).
    pre_resolve_info = payload.lstat()
    if stat.S_ISLNK(pre_resolve_info.st_mode) or _is_reparse(pre_resolve_info):
        raise TargetUnusableError("command must not be a symlink or reparse point")
    canonical = payload.resolve(strict=True)
    info = canonical.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or _is_reparse(info):
        raise TargetUnusableError("command must be a regular, non-symlink file")
    if os.name != "nt" and not os.access(canonical, os.X_OK):
        raise TargetUnusableError("command is not executable")
    if os.name == "nt" and canonical.suffix.casefold() == ".ps1":
        raise TargetUnusableError(
            "PowerShell scripts must be invoked through an executable wrapper"
        )
    # `payload` itself was never a symlink (checked above), but an
    # ANCESTOR directory (e.g. `root/bin` or `root` itself) could still be
    # one, letting resolve() silently escape the identity-verified root
    # even though the leaf file is an ordinary regular file. Verify the
    # fully-resolved path is still contained in `root`'s own resolved
    # form -- mirrors `picker_support.pivot_targets._resolve_command`'s own
    # containment check.
    try:
        canonical.relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise TargetUnusableError(
            "command resolves outside the identity-verified plugin root"
        ) from exc
    # A `.cmd`/`.bat` command is routed through cmd.exe on Windows (see
    # `_windows_batch_argv`), whose own lexer treats `& | < > ^ %` and `"`
    # as structurally significant REGARDLESS of any quoting `subprocess`
    # itself applies when building the actual CreateProcess command line
    # (`subprocess`'s quoting assumes the target parses via
    # CommandLineToArgvW-style conventions, which cmd.exe's own `/C`
    # parsing does not use). A path containing one of these characters
    # could therefore split or reinterpret the command line even though
    # the claim ref's own namespace/identifier are already validated
    # elsewhere -- refuse outright (fail closed) rather than let it reach
    # cmd.exe at all.
    if (
        os.name == "nt"
        and canonical.suffix.casefold() in (".cmd", ".bat")
        and _CMD_METACHAR_RE.search(str(canonical))
    ):
        raise TargetUnusableError(
            "command path contains a cmd.exe metacharacter and cannot be safely invoked"
        )
    return (str(canonical), *command[1:])


def _inactive(path: Path, reason: str, *, detail: str | None = None) -> EntryDecision[ClaimProviderManifest]:
    return EntryDecision.inactive(
        Finding(
            registry=REGISTRY_NAME,
            entry=str(path),
            status="inactive",
            reason=reason,
            remedy="Reinstall/re-enable the contributing plugin.",
            detail=detail,
        )
    )


def _indeterminate(path: Path, *, detail: str | None = None) -> EntryDecision[ClaimProviderManifest]:
    return EntryDecision.indeterminate(
        Finding(
            registry=REGISTRY_NAME,
            entry=str(path),
            status="indeterminate",
            reason="entry-indeterminate",
            detail=detail,
        )
    )


def _classify(
    path: Path,
    plugin_root: Path,
    activation: ActivationReport,
) -> EntryDecision[ClaimProviderManifest]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        manifest = parse_manifest(data, source_path=str(path))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ManifestError) as exc:
        return _inactive(path, "invalid-entry", detail=str(exc))

    marketplace = plugin_root.parent.name
    plugin_name = plugin_root.name
    source = f"{plugin_name}@{marketplace}"

    if activation.authority.value == "indeterminate":
        return _indeterminate(path, detail="plugin activation evidence is indeterminate")
    decision = activation.decisions.get(source)
    if decision is None or decision.status is EntryStatus.INACTIVE:
        return _inactive(path, "not-enabled", detail=f"{source} is not enabled")
    if decision.status is EntryStatus.INDETERMINATE:
        return _indeterminate(path, detail=f"{source} activation is indeterminate")
    expected_roots = {selected.root for selected in decision.value.live_roots}
    try:
        canonical_root = plugin_root.resolve(strict=True)
    except OSError as exc:
        return _inactive(path, "missing-target", detail=str(exc))
    if canonical_root not in expected_roots:
        return _inactive(
            path,
            "identity-mismatch",
            detail=f"{plugin_root} is not among {source}'s live roots",
        )

    try:
        status_command = (
            _resolve_command(manifest.status_command, root=canonical_root)
            if manifest.status_command is not None
            else None
        )
        reclaim_command = (
            _resolve_command(manifest.reclaim_command, root=canonical_root)
            if manifest.reclaim_command is not None
            else None
        )
    except FileNotFoundError as exc:
        return _inactive(path, "missing-target", detail=str(exc))
    except TargetUnusableError as exc:
        return _inactive(path, "target-unusable", detail=str(exc))

    resolved = ClaimProviderManifest(
        namespace=manifest.namespace,
        plugin=source,
        plugin_root=str(canonical_root),
        status_command=status_command,
        reclaim_command=reclaim_command,
        description=manifest.description,
        source_path=manifest.source_path,
    )
    return EntryDecision.active(resolved)


def discover_claim_providers(
    plugins_root: str | os.PathLike[str] | None = None,
) -> tuple[dict[str, ClaimProviderManifest], tuple[Finding, ...]]:
    """Every valid ``claim-providers/*.json`` drop-in, keyed by namespace.

    Never raises: an absent/unreadable plugins root, an absent
    ``claim-providers`` subdirectory, or any single malformed manifest simply
    yields no entry for that namespace, with a :class:`Finding` recorded for
    diagnostics. The first plugin found (in deterministic sorted-path order)
    to declare a given namespace wins; a later duplicate is recorded as a
    finding rather than silently overriding the first.
    """
    root = installed_plugins_dir(plugins_root)
    providers: dict[str, ClaimProviderManifest] = {}
    findings: list[Finding] = []
    if not root.is_dir():
        return providers, tuple(findings)
    try:
        plugin_dirs = sorted(p for p in root.glob("*/*") if p.is_dir())
    except OSError:
        return providers, tuple(findings)

    activation_report: ActivationReport | None = None

    def current_activation() -> ActivationReport:
        nonlocal activation_report
        if activation_report is None:
            activation_report = resolve_active_plugins()
        return activation_report

    for plugin_dir in plugin_dirs:
        claim_providers_dir = plugin_dir / CLAIM_PROVIDERS_SUBDIR
        snapshot = scan_directory(
            claim_providers_dir,
            lambda path: _classify(path, plugin_dir, current_activation()),
            registry=REGISTRY_NAME,
            suffixes={".json"},
        )
        findings.extend(snapshot.findings)
        for entry, decision in sorted(snapshot.decisions.items()):
            if decision.value is None:
                continue
            manifest = decision.value
            prior = providers.get(manifest.namespace)
            if prior is not None:
                findings.append(
                    Finding(
                        registry=REGISTRY_NAME,
                        entry=entry,
                        status="inactive",
                        reason="duplicate",
                        target=manifest.namespace,
                        owner=manifest.plugin,
                        remedy=f"Remove {entry} or the conflicting {prior.source_path}.",
                        detail=f"namespace already claimed by {prior.source_path}",
                    )
                )
                continue
            providers[manifest.namespace] = manifest
    return providers, tuple(findings)


def _windows_batch_argv(command: tuple[str, ...]) -> list[str]:
    """Route a ``.cmd``/``.bat`` command through ``cmd.exe`` on Windows.

    ``CreateProcess`` (what ``subprocess.run(..., shell=False)`` ultimately
    calls) cannot execute a batch file directly -- only ``cmd.exe``'s own
    interpreter understands it -- so every real claim-provider callback
    (whose resolved binstub is a `.cmd` shim on Windows, per
    ``_payload_command``) would otherwise fail to launch at all and degrade
    to ``available: False`` before its logic ever ran. ``/d`` skips
    ``AutoRun`` registry commands (never let an ambient AutoRun script
    inject itself into this call).

    The batch path and each callback argument are passed as **separate**
    argv elements -- `[comspec, "/d", "/s", "/c", batch_path, *args]` --
    never pre-joined into one already-quoted string. `subprocess.run`
    itself quotes each argv element when it builds the actual
    `CreateProcess` command line; pre-quoting the whole thing first (an
    earlier revision of this function did, via `list2cmdline`) means that
    quoting happens a SECOND time on top of the first, breaking cmd.exe's
    own parsing (especially once any path or argument contains a space).
    Mirrors `agent_bridge.transport._wrap_batch_for_windows`, an existing,
    separately-tested precedent for this exact problem.
    """
    if os.name != "nt" or Path(command[0]).suffix.casefold() not in (".cmd", ".bat"):
        return list(command)
    comspec = os.environ.get("ComSpec", "cmd.exe")
    return [comspec, "/d", "/s", "/c", *command]


def _run_callback(
    command: tuple[str, ...], *, timeout: float, required_bool_field: str
) -> dict | None:
    try:
        proc = subprocess.run(
            _windows_batch_argv(command), capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", **no_window_kwargs(),
        )
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get(required_bool_field), bool):
        return None
    # The callback contract documents "state"/"detail" as optional STRING
    # fields -- validate them too, not just the required boolean, so a
    # malformed provider (e.g. {"exists": true, "state": {}}) cannot be
    # reported as a successful, well-shaped result.
    for optional_field in ("state", "detail"):
        value = data.get(optional_field)
        if value is not None and not isinstance(value, str):
            return None
    return data


def split_namespaced_ref(ref: str) -> tuple[str, str] | None:
    """Split ``"<namespace>:<identifier>"`` into its two parts, or ``None``
    if ``ref`` carries no recognizable namespace prefix (a legacy,
    pre-namespacing claim ref) or either part contains a character outside
    :data:`_SAFE_TOKEN_RE`.

    The identifier ultimately reaches a callback command line -- on
    Windows, `.cmd`/`.bat` callbacks are routed through ``cmd.exe``, whose
    metacharacter parsing (``&``, ``|``, ``<``, ``>``, ``^``, ``%``, ``!``)
    happens independently of, and before, any C-runtime argv quoting
    (`subprocess.list2cmdline`) -- so a crafted ref such as
    ``"codespace:foo&whoami"`` could otherwise terminate the intended
    command and inject another. Rejecting any ref whose namespace or
    identifier contains such a character (rather than attempting to escape
    it perfectly for cmd.exe's own parser) closes this off at the source,
    for every platform, not just Windows.
    """
    if ":" not in ref:
        return None
    namespace, _, identifier = ref.partition(":")
    namespace = namespace.strip()
    identifier = identifier.strip()
    if not namespace or not identifier:
        return None
    if not _SAFE_TOKEN_RE.match(namespace) or not _SAFE_TOKEN_RE.match(identifier):
        return None
    return namespace, identifier


def resolve_claim_status(
    ref: str,
    *,
    plugins_root: str | os.PathLike[str] | None = None,
    timeout: float = _CALLBACK_TIMEOUT_SECONDS,
) -> dict:
    """Best-effort claim status via the registered claim provider for ``ref``'s
    namespace. Never raises -- degrades to ``{"available": False, "reason":
    "..."}"`` on any absence, malformed manifest, or callback failure."""
    split = split_namespaced_ref(ref)
    if split is None:
        return {"available": False, "reason": "ref has no namespace prefix, or contains unsafe characters"}
    namespace, identifier = split
    providers, _findings = discover_claim_providers(plugins_root)
    provider = providers.get(namespace)
    if provider is None:
        return {
            "available": False,
            "reason": f"no claim provider registered for namespace '{namespace}:'",
        }
    if provider.status_command is None:
        return {
            "available": False,
            "reason": f"{provider.plugin} is a claim provider for '{namespace}:' but declares no status_command",
        }
    result = _run_callback(
        (*provider.status_command, "claim-status", identifier),
        timeout=timeout, required_bool_field="exists",
    )
    if result is None:
        return {"available": False, "reason": f"{provider.plugin} claim-status callback failed"}
    # "available" is the envelope's own key, set here (never sourced from
    # the callback) -- a provider that happens to emit its own "available"
    # key in ``result`` must never be able to shadow it.
    return {**{k: v for k, v in result.items() if k != "available"}, "available": True}


def resolve_claim_reclaim(
    ref: str,
    *,
    apply: bool,
    plugins_root: str | os.PathLike[str] | None = None,
    timeout: float = _CALLBACK_TIMEOUT_SECONDS,
) -> dict:
    """Best-effort claim reclaim via the registered claim provider for
    ``ref``'s namespace. Never raises -- degrades to ``{"available": False,
    "reason": "..."}"`` on any absence, malformed manifest, or callback
    failure. Without ``apply``, the provider must not act (dry-run)."""
    split = split_namespaced_ref(ref)
    if split is None:
        return {"available": False, "reason": "ref has no namespace prefix, or contains unsafe characters"}
    namespace, identifier = split
    providers, _findings = discover_claim_providers(plugins_root)
    provider = providers.get(namespace)
    if provider is None:
        return {
            "available": False,
            "reason": f"no claim provider registered for namespace '{namespace}:'",
        }
    if provider.reclaim_command is None:
        return {
            "available": False,
            "reason": f"{provider.plugin} is a claim provider for '{namespace}:' but declares no reclaim_command",
        }
    argv = [*provider.reclaim_command, "claim-reclaim", identifier]
    if apply:
        argv.append("--apply")
    result = _run_callback(tuple(argv), timeout=timeout, required_bool_field="reclaimed")
    if result is None:
        return {"available": False, "reason": f"{provider.plugin} claim-reclaim callback failed"}
    return {**{k: v for k, v in result.items() if k != "available"}, "available": True}
