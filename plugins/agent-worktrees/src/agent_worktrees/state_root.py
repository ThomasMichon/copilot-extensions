"""Resolve the **state root** -- the repo checkout where an effort/vision/log
plugin should write personal state.

This is the resolver behind ``agent-worktrees state-root``. It exists for the
**stateless harness** split (the ``stateless-harness`` vision /
``citadel-harness-split`` effort): a shareable control-plane harness carries the
intelligence but *no* personal state, so plugins like ``efforts``, ``visions``,
and ``agent-logger`` must not assume the launch repo is where their writes land.

Resolution rules (highest precedence first):

1. **Explicit override** (``--repo NAME``): resolve that registered repo's local
   checkout. Lets a caller deliberately target the harness itself or a product
   repo, regardless of the binding.
2. **Requires an external state root**: when the launch repo declares
   ``requires_external_state_root: true`` (or ``stateless: true``, which implies
   it), route to the bound **knowledge repo** (top-level ``knowledge_repo`` in
   the machine-local config), resolved to a checkout via the repos registry. If
   no knowledge repo is bound -- or the bound name is not a registered checkout
   -- resolution **fails** (no fallback): the resolver refuses to silently write
   personal state into the launch repo (e.g. a shareable harness tree).
3. **Self-hosted state (backward-compatible default)**: when the repo does not
   require an external state root (the default), the launch repo *is* the state
   home. Prefer the current git worktree root (so state lands in the tree being
   edited); fall back to the repo's anchor.

The resolver never hardcodes a repo name or path -- everything comes from the
layered config + the repos registry.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import config as cfg
from . import git_ops
from . import repos as repos_mod


@dataclass(frozen=True)
class StateRoot:
    """The resolved (or unresolved) state root."""

    path: str | None
    """Absolute path to the checkout where state should be written, or ``None``
    when resolution failed (see :attr:`error`)."""
    source: str
    """Where the root came from: ``"knowledge_repo"``, ``"launch_repo"``, or
    ``"explicit"``."""
    repo: str
    """Name of the repo providing the root (knowledge repo name, launch repo
    name, or the explicit override)."""
    stateless: bool
    """Whether the launch repo declared itself a stateless harness."""
    requires_external: bool
    """Whether the launch repo requires an external state root -- the effective
    value of ``requires_external_state_root`` OR ``stateless`` (stateless
    implies it). This is the flag the ``efforts``/``visions`` plugins key on."""
    bound: bool
    """True when a usable path was resolved."""
    error: str | None = None
    """Human-readable reason resolution failed (``None`` on success)."""

    def as_dict(self) -> dict:
        return {
            "state_root": self.path,
            "source": self.source,
            "repo": self.repo,
            "stateless": self.stateless,
            "requires_external": self.requires_external,
            "bound": self.bound,
            "error": self.error,
        }


COORDINATION_READINESS_VERSION = 1
COORDINATION_READINESS_CODES = frozenset({
    "ready",
    "knowledge_binding_required",
    "state_root_resolution_failed",
})


@dataclass(frozen=True)
class CoordinationReadiness:
    """Versioned readiness for operations that create shared ownership."""

    ready: bool
    code: str
    state_root: StateRoot
    error: str | None = None
    version: int = field(
        default=COORDINATION_READINESS_VERSION,
        init=False,
    )

    def __post_init__(self) -> None:
        if self.code not in COORDINATION_READINESS_CODES:
            raise ValueError(f"unsupported coordination readiness code: {self.code}")
        if self.ready != (self.code == "ready"):
            raise ValueError(
                "coordination readiness must use code 'ready' exactly when ready"
            )
        if not self.ready and not self.error:
            raise ValueError("unready coordination readiness requires an error")

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "ready": self.ready,
            "code": self.code,
            "state_root": {
                "path": self.state_root.path,
                "source": self.state_root.source,
                "repo": self.state_root.repo,
                "requires_external": self.state_root.requires_external,
                "bound": self.state_root.bound,
            },
            "error": self.error,
        }


@dataclass(frozen=True)
class ConfigRoot:
    """A guarded machine-local configuration destination."""

    path: str | None
    """Absolute configuration root, or ``None`` when validation failed."""
    source: str
    """``"machine_local"`` for the default or ``"explicit"`` for a caller path."""
    repo: str
    """Name of the launch repo whose machine-local configuration is targeted."""
    stateless: bool
    """Whether the launch repo requires an external state root."""
    bound: bool
    """True when the destination is safe for a supported setup writer."""
    error: str | None = None
    """Actionable validation error, or ``None`` on success."""

    def as_dict(self) -> dict:
        return {
            "config_root": self.path,
            "source": self.source,
            "repo": self.repo,
            "stateless": self.stateless,
            "bound": self.bound,
            "error": self.error,
        }


def _normalized_path(path: str, *, cwd: str | None = None) -> Path:
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        expanded = os.path.join(cwd or os.getcwd(), expanded)
    return Path(expanded).resolve(strict=False)


def _path_within(path: Path, root: Path) -> bool:
    target = os.path.normcase(os.path.normpath(str(path)))
    parent = os.path.normcase(os.path.normpath(str(root.resolve(strict=False))))
    return target == parent or target.startswith(parent + os.sep)


def _declares_external_state_root(root: Path) -> bool:
    config_path = root / ".agent-worktrees" / "config.yaml"
    if not config_path.is_file():
        return False
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(
            f"cannot validate config destination because '{config_path}' "
            f"could not be read: {exc}"
        ) from exc
    if raw is None:
        return False
    if not isinstance(raw, dict):
        raise ValueError(
            f"cannot validate config destination because '{config_path}' "
            "must contain a YAML mapping"
        )
    return bool(raw.get("stateless") or raw.get("requires_external_state_root"))


def _containing_git_checkouts(path: Path) -> list[Path]:
    """Return every enclosing Git checkout, nearest first."""
    return [
        candidate
        for candidate in (path, *path.parents)
        if (candidate / ".git").exists()
    ]


def _git_common_dir(checkout: Path) -> Path:
    try:
        proc = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=10,
            env=git_ops.repository_identity_env(),
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(
            f"cannot validate config destination repository at '{checkout}': {exc}"
        ) from exc
    common = (proc.stdout or "").strip()
    if proc.returncode != 0 or not common:
        detail = (proc.stderr or "").strip() or "git common directory is unavailable"
        raise ValueError(
            f"cannot validate config destination repository at '{checkout}': "
            f"{detail}"
        )
    path = Path(common)
    if not path.is_absolute():
        path = checkout / path
    return path.resolve(strict=False)


def _enclosing_checkout_with_common_dir(
    path: Path,
    common_dir: Path,
) -> Path | None:
    """Return the nearest enclosing checkout with ``common_dir`` identity."""
    for checkout in _containing_git_checkouts(path):
        if _git_common_dir(checkout) == common_dir:
            return checkout
    return None


def _containing_stateless_checkout(path: Path) -> Path | None:
    """Return a stateless checkout containing ``path``, if any."""
    for candidate in _containing_git_checkouts(path):
        if _declares_external_state_root(candidate):
            return candidate
    return None


def validate_config_destination(
    destination: str,
    *,
    cwd: str | None = None,
    repo: str = "",
) -> ConfigRoot:
    """Validate an explicit setup configuration destination.

    This path-only guard deliberately does not require an adopted project, so a
    supported setup entry point can reject a stateless checkout during initial
    bootstrap.
    """
    launch_stateless = False
    try:
        launch_path = _normalized_path(cwd or os.getcwd())
        launch_checkout = _containing_stateless_checkout(launch_path)
        launch_stateless = launch_checkout is not None
        target = _normalized_path(destination, cwd=cwd)
        if target.exists() and not target.is_dir():
            raise ValueError(f"config root '{target}' is not a directory")
        unsafe_root = None
        if launch_checkout is not None:
            unsafe_root = _enclosing_checkout_with_common_dir(
                target,
                _git_common_dir(launch_checkout),
            )
        if unsafe_root is None:
            unsafe_root = _containing_stateless_checkout(target)
    except (OSError, ValueError) as exc:
        return ConfigRoot(
            None,
            "explicit",
            repo,
            launch_stateless,
            False,
            error=str(exc),
        )
    if unsafe_root is not None:
        return ConfigRoot(
            None,
            "explicit",
            repo,
            launch_stateless,
            False,
            error=(
                f"config destination '{target}' is inside stateless checkout "
                f"'{unsafe_root}'. Use `agent-worktrees config-root` without "
                "--destination to resolve the machine-local configuration root; "
                "the checkout is not a supported destination for concrete "
                "operator or product configuration."
            ),
        )
    return ConfigRoot(str(target), "explicit", repo, launch_stateless, True)


def resolve_config_root(
    config: cfg.Config,
    *,
    destination: str | None = None,
    cwd: str | None = None,
    project: str | None = None,
) -> ConfigRoot:
    """Resolve and guard the configuration root for supported setup writers.

    The default is the launch repo's per-project machine-local root
    (``~/.<project>/``). A caller may supply an explicit destination, but it is
    rejected when it targets the launch repo's stateless checkout or any other
    checkout that declares an external state root.
    """
    try:
        repo_cfg = config.default_repo
    except KeyError:
        repo_cfg = None
    repo = config.repo_name or "?"
    stateless = bool(
        getattr(repo_cfg, "stateless", False)
        or getattr(repo_cfg, "requires_external_state_root", False)
    )
    source = "explicit" if destination else "machine_local"
    project_name = project or cfg.active_project() or repo

    try:
        target = _normalized_path(
            destination or str(cfg.project_dir(project_name)),
            cwd=cwd,
        )
        if target.exists() and not target.is_dir():
            raise ValueError(f"config root '{target}' is not a directory")
        unsafe_root: Path | None = None
        if stateless and repo_cfg is not None:
            roots = [repo_cfg.anchor, _git_toplevel(cwd)]
            for raw_root in roots:
                if raw_root and _path_within(target, _normalized_path(raw_root)):
                    unsafe_root = _normalized_path(raw_root)
                    break
            if unsafe_root is None:
                anchor_checkouts = _containing_git_checkouts(
                    _normalized_path(repo_cfg.anchor)
                )
                if anchor_checkouts:
                    anchor_common_dir = _git_common_dir(anchor_checkouts[0])
                    unsafe_root = _enclosing_checkout_with_common_dir(
                        target,
                        anchor_common_dir,
                    )
        if unsafe_root is None:
            unsafe_root = _containing_stateless_checkout(target)
    except (OSError, ValueError) as exc:
        return ConfigRoot(
            None,
            source,
            repo,
            stateless,
            False,
            error=str(exc),
        )

    if unsafe_root is not None:
        return ConfigRoot(
            None,
            source,
            repo,
            stateless,
            False,
            error=(
                f"config destination '{target}' is inside stateless checkout "
                f"'{unsafe_root}'. Use `agent-worktrees config-root` without "
                "--destination to resolve the machine-local configuration root; "
                "the checkout is not a supported destination for concrete "
                "operator or product configuration."
            ),
        )

    return ConfigRoot(str(target), source, repo, stateless, True)


@dataclass(frozen=True)
class PairCheckout:
    """One role-bearing checkout in a tracked harness/knowledge pair."""

    role: str
    path: str
    repo: str
    worktree_id: str | None
    kind: str = "worktree"
    status: str | None = None

    def as_dict(self) -> dict:
        return {
            "worktree_id": self.worktree_id,
            "role": self.role,
            "path": self.path,
            "kind": self.kind,
            "status": self.status,
        }


@dataclass(frozen=True)
class StatePair:
    """Resolution of the current tracked harness/knowledge pair."""

    paired: bool
    pair_id: str | None = None
    pair_ref: str | None = None
    pair_kind: str | None = None
    current: PairCheckout | None = None
    sibling: PairCheckout | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        if not self.paired:
            result: dict = {"paired": False}
            if self.current:
                result["worktree_id"] = self.current.worktree_id
            if self.error:
                result["error"] = self.error
            return result
        result = {
            "paired": True,
            "pair_id": self.pair_id,
            "self": self.current.as_dict() if self.current else None,
            "sibling": self.sibling.as_dict() if self.sibling else None,
        }
        if self.error:
            result.update(
                {
                    "pair_ref": self.pair_ref,
                    "pair_kind": self.pair_kind,
                    "error": self.error,
                }
            )
        return result


def _git_toplevel(cwd: str | None) -> str | None:
    """Return the git worktree root of ``cwd`` (or the process cwd), or None."""
    try:
        checkout = cwd or os.getcwd()
        proc = subprocess.run(
            ["git", "-C", checkout, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
            env=git_ops.repository_identity_env(),
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    root = (proc.stdout or "").strip()
    return root or None


def _checkout_path(name: str) -> str | None:
    """Resolve a registered repo name to its local checkout path, or None.

    Uses :func:`repos.resolve_path`, matching ``agent-worktrees repos find``:
    the registry is consulted first, then a ``srcroot/name`` fallback -- so a
    knowledge repo that lives under the machine's source root resolves even
    without an explicit registry entry. The path must be an existing directory.
    """
    path = repos_mod.resolve_path(name)
    if not path or not os.path.isdir(path):
        return None
    return path


def resolve_state_root(
    config: cfg.Config,
    *,
    repo_override: str | None = None,
    cwd: str | None = None,
) -> StateRoot:
    """Resolve the state root for the given loaded config.

    Args:
        config: The layered project config (``cfg.load_config()``).
        repo_override: Explicit registered-repo name to target instead of the
            binding-driven default.
        cwd: Directory used for the self-hosted git-toplevel probe (defaults
            to the process cwd).

    Returns:
        A :class:`StateRoot`. On failure ``path`` is ``None`` and ``error`` is
        set; callers should treat that as "do not write" rather than falling
        back to the launch repo.
    """
    try:
        repo_cfg = config.default_repo
    except KeyError:
        repo_cfg = None
    launch_repo = config.repo_name or (repo_cfg and repo_cfg.anchor) or "?"
    stateless = bool(getattr(repo_cfg, "stateless", False))
    # A stateless harness always requires an external state root, so stateless
    # implies requires_external_state_root -- a harness never has to set both.
    requires_external = bool(
        getattr(repo_cfg, "requires_external_state_root", False) or stateless
    )

    # 1. Explicit override -- resolve any registered repo by name.
    if repo_override:
        path = _checkout_path(repo_override)
        if not path:
            return StateRoot(
                None, "explicit", repo_override, stateless, requires_external,
                False,
                error=(
                    f"repo '{repo_override}' is not a registered repo with a "
                    f"local checkout on this machine (agent-worktrees repos add …)"
                ),
            )
        return StateRoot(
            path, "explicit", repo_override, stateless, requires_external, True
        )

    # 2. Requires an external state root -> the bound knowledge repo (no fallback).
    if requires_external:
        kr = (config.knowledge_repo or "").strip()
        if not kr:
            return StateRoot(
                None, "knowledge_repo", "", stateless, True, False,
                error=(
                    f"launch repo '{launch_repo}' requires an external state "
                    f"root but no knowledge_repo is bound on this machine. Set "
                    f"'knowledge_repo: <name>' in ~/.{launch_repo}/config.yaml "
                    f"(or run the harness-knowledge setup) before writing "
                    f"efforts/logs/visions. Refusing to write state into the "
                    f"launch repo."
                ),
            )
        path = _checkout_path(kr)
        if not path:
            return StateRoot(
                None, "knowledge_repo", kr, stateless, True, False,
                error=(
                    f"knowledge_repo '{kr}' is not a registered repo with a "
                    f"local checkout on this machine. Register it "
                    f"(agent-worktrees repos add {kr} …) or fix the pointer in "
                    f"~/.{launch_repo}/config.yaml."
                ),
            )
        return StateRoot(path, "knowledge_repo", kr, stateless, True, True)

    # 3. Self-hosted -> the launch repo is the state home (backward-compatible).
    #    Prefer the current git worktree root so state lands in the tree being
    #    edited; fall back to the repo's anchor.
    root = _git_toplevel(cwd)
    if root:
        return StateRoot(root, "launch_repo", launch_repo, stateless, False, True)
    anchor = repo_cfg.anchor if repo_cfg else None
    if anchor and os.path.isdir(anchor):
        return StateRoot(
            anchor, "launch_repo", launch_repo, stateless, False, True
        )
    return StateRoot(
        None, "launch_repo", launch_repo, stateless, False, False,
        error=(
            f"could not resolve a state root for '{launch_repo}': no git "
            f"worktree at the current directory and no usable anchor."
        ),
    )


def coordination_readiness(
    config: cfg.Config,
    *,
    cwd: str | None = None,
) -> CoordinationReadiness:
    """Resolve whether claim-producing coordination may begin."""
    root = resolve_state_root(config, cwd=cwd)
    if root.path or not root.requires_external:
        return CoordinationReadiness(True, "ready", root)

    if root.requires_external and not (config.knowledge_repo or "").strip():
        error = (
            "This repository requires a bound knowledge repository before "
            "creating resource claims. Set `knowledge_repo: <name>` in the "
            f"machine-local project config for '{config.repo_name}', register "
            "that repository, and retry the same operation."
        )
        return CoordinationReadiness(
            False,
            "knowledge_binding_required",
            root,
            error=error,
        )

    error = (
        "The coordination state root could not be resolved. Repair the bound "
        "knowledge repository checkout or repository registration, then retry "
        f"the same operation. {root.error or ''}"
    ).strip()
    return CoordinationReadiness(
        False,
        "state_root_resolution_failed",
        root,
        error=error,
    )


def resolve_pair(config: cfg.Config | None, *, cwd: str | None = None) -> StatePair:
    """Resolve both roles of the tracked pair containing ``cwd``.

    This is the typed ownership seam shared by ``state-root --pair`` and
    knowledge-repo management commands. It never falls from a stale worktree
    pair back to the knowledge anchor.
    """
    from . import tracking

    current_dir = cwd or os.getcwd()
    worktree_id = tracking.find_worktree_id_by_cwd(current_dir)
    record = tracking.load_record_by_id(worktree_id) if worktree_id else None
    if record is None:
        return StatePair(
            paired=False, error="current directory is not a tracked worktree"
        )
    current = PairCheckout(
        role=record.pair_role or "",
        path=record.worktree_path,
        repo=record.repo,
        worktree_id=record.worktree_id,
        kind="worktree",
        status=record.status,
    )
    if not record.is_paired:
        return StatePair(
            paired=False,
            current=current,
            error=f"worktree '{record.worktree_id}' is not paired",
        )

    if record.pair_kind == "anchor":
        if config is None:
            return StatePair(
                paired=True,
                pair_id=record.pair_id,
                pair_ref=record.pair_ref,
                pair_kind="anchor",
                current=current,
                error="paired knowledge anchor requires an active project config",
            )
        pair_ref = tracking.parse_claim_ref(record.pair_ref or "")
        bound_repo = (config.knowledge_repo or "").strip()
        if not (pair_ref and pair_ref.is_qualified and pair_ref.project):
            return StatePair(
                paired=True,
                pair_id=record.pair_id,
                pair_ref=record.pair_ref,
                pair_kind="anchor",
                current=current,
                error=(
                    f"paired knowledge anchor reference "
                    f"'{record.pair_ref or '?'}' is not a qualified pair ref"
                ),
            )
        if pair_ref.project != bound_repo:
            return StatePair(
                paired=True,
                pair_id=record.pair_id,
                pair_ref=record.pair_ref,
                pair_kind="anchor",
                current=current,
                error=(
                    f"paired knowledge anchor repo '{pair_ref.project}' no longer "
                    f"matches the current binding '{bound_repo or '<unbound>'}'; "
                    "create or select a current pair"
                ),
            )
        resolved = resolve_state_root(config)
        if not (resolved.bound and resolved.path):
            return StatePair(
                paired=True,
                pair_id=record.pair_id,
                pair_ref=record.pair_ref,
                pair_kind="anchor",
                current=current,
                error=resolved.error or "paired knowledge anchor could not be resolved",
            )
        sibling = PairCheckout(
            role="knowledge",
            path=resolved.path,
            repo=resolved.repo,
            worktree_id=None,
            kind="anchor",
        )
    else:
        sibling_record = tracking.find_paired_record(record)
        if sibling_record is None:
            return StatePair(
                paired=True,
                pair_id=record.pair_id,
                pair_ref=record.pair_ref,
                pair_kind=record.pair_kind,
                current=current,
                error=(
                    f"paired sibling '{record.pair_ref or '?'}' has no local "
                    "record on this machine"
                ),
            )
        current_pair_id = (record.pair_id or "").strip()
        sibling_pair_id = (sibling_record.pair_id or "").strip()
        if not current_pair_id or not sibling_pair_id:
            return StatePair(
                paired=True,
                pair_id=record.pair_id,
                pair_ref=record.pair_ref,
                pair_kind=record.pair_kind,
                current=current,
                error=(
                    "paired worktree records require the same non-empty pair_id "
                    f"(current={current_pair_id or '<empty>'!r}, "
                    f"sibling={sibling_pair_id or '<empty>'!r})"
                ),
            )
        if current_pair_id != sibling_pair_id:
            return StatePair(
                paired=True,
                pair_id=record.pair_id,
                pair_ref=record.pair_ref,
                pair_kind=record.pair_kind,
                current=current,
                error=(
                    "paired worktree records disagree on pair_id "
                    f"(current={current_pair_id!r}, sibling={sibling_pair_id!r})"
                ),
            )

        current_identity = (
            record.machine,
            record.repo,
            record.worktree_id,
        )
        sibling_identity = (
            sibling_record.machine,
            sibling_record.repo,
            sibling_record.worktree_id,
        )
        if current_identity == sibling_identity:
            identity = tracking.format_claim_ref(*current_identity)
            return StatePair(
                paired=True,
                pair_id=record.pair_id,
                pair_ref=record.pair_ref,
                pair_kind=record.pair_kind,
                current=current,
                error=f"paired worktree cannot pair with itself ({identity!r})",
            )

        current_role = (record.pair_role or "").strip()
        sibling_role = (sibling_record.pair_role or "").strip()
        if {current_role, sibling_role} != {"harness", "knowledge"}:
            return StatePair(
                paired=True,
                pair_id=record.pair_id,
                pair_ref=record.pair_ref,
                pair_kind=record.pair_kind,
                current=current,
                error=(
                    "paired worktree records require complementary "
                    "harness/knowledge roles "
                    f"(current={current_role or '<empty>'!r}, "
                    f"sibling={sibling_role or '<empty>'!r})"
                ),
            )

        current_kind = (record.pair_kind or "").strip()
        sibling_kind = (sibling_record.pair_kind or "").strip()
        if (
            current_kind != sibling_kind
            or current_kind != "worktree"
        ):
            return StatePair(
                paired=True,
                pair_id=record.pair_id,
                pair_ref=record.pair_ref,
                pair_kind=record.pair_kind,
                current=current,
                error=(
                    "paired worktree records require matching 'worktree' "
                    "pair_kind values "
                    f"(current={current_kind or '<empty>'!r}, "
                    f"sibling={sibling_kind or '<empty>'!r})"
                ),
            )

        for owner, raw_ref, target, target_label in (
            ("current", record.pair_ref, sibling_record, "sibling"),
            ("sibling", sibling_record.pair_ref, record, "current"),
        ):
            parsed = tracking.parse_claim_ref(raw_ref or "")
            expected = tracking.format_claim_ref(
                target.machine,
                target.repo,
                target.worktree_id,
            )
            if parsed is None or not parsed.is_qualified:
                return StatePair(
                    paired=True,
                    pair_id=record.pair_id,
                    pair_ref=record.pair_ref,
                    pair_kind=record.pair_kind,
                    current=current,
                    error=(
                        f"{owner} pair_ref {raw_ref or '<empty>'!r} is not a "
                        f"qualified machine/repo/worktree reference to the "
                        f"{target_label} ({expected!r})"
                    ),
                )
            actual_identity = (
                parsed.machine,
                parsed.project,
                parsed.worktree_id,
            )
            expected_identity = (
                target.machine,
                target.repo,
                target.worktree_id,
            )
            if actual_identity != expected_identity:
                return StatePair(
                    paired=True,
                    pair_id=record.pair_id,
                    pair_ref=record.pair_ref,
                    pair_kind=record.pair_kind,
                    current=current,
                    error=(
                        f"{owner} pair_ref {raw_ref!r} does not reference the "
                        f"{target_label} {expected!r}"
                    ),
                )
        sibling = PairCheckout(
            role=sibling_record.pair_role or "",
            path=sibling_record.worktree_path,
            repo=sibling_record.repo,
            worktree_id=sibling_record.worktree_id,
            kind=record.pair_kind or "worktree",
            status=sibling_record.status,
        )
    return StatePair(
        paired=True,
        pair_id=record.pair_id,
        pair_ref=record.pair_ref,
        pair_kind=record.pair_kind,
        current=current,
        sibling=sibling,
    )


def _same_path(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def _code(value: str) -> str:
    safe = value.replace("`", "'")
    return f"`{safe}`"


def state_repo_definition(
    res: StateRoot,
    *,
    pair: StatePair | None = None,
    launch_path: str | None = None,
    launch_anchor: str | None = None,
) -> str:
    """Return the sessionStart **"the user's state repo"** definition (Markdown).

    This is the single, authoritative binding of the term *"the user's state
    repo"* that agent-worktrees injects into session context (via the
    ``session-conduct`` sessionStart hook, ``state-root --conduct``). It binds
    the configured repository identity to the exact paired worktree when one is
    available and otherwise makes the no-write degraded state explicit.

    The returned string is a self-contained paragraph (no trailing newline);
    the hook merges it into ``additionalContext`` ahead of any static conduct
    fragments. When a knowledge repo is bound (``source == "knowledge_repo"``),
    the definition also carries the harness/knowledge/product routing boundary.
    """
    if res.path:
        if res.source == "explicit":
            where = f"the '{res.repo}' repo"
        elif res.source != "knowledge_repo":
            where = "your current repo (self-hosted)"
            return (
                f"**The user's state repo** is {_code(res.path)} — {where}: the "
                "current writable checkout for personal state and reference data "
                "(efforts, logs, visions, and skill references)."
            )
        if res.source == "explicit":
            return (
                f"**The user's state repo** is {_code(res.path)} — {where}: the "
                "explicitly selected checkout for personal state and reference "
                "data."
            )

        sibling = pair.sibling if pair and pair.paired and not pair.error else None
        writable = (
            sibling
            if sibling and sibling.role == "knowledge" and sibling.kind == "worktree"
            else None
        )
        current = pair.current if pair else None
        if current and current.kind == "worktree":
            checkout = (
                f"Current: managed harness worktree "
                f"{_code(current.path)}."
            )
        elif launch_path and _same_path(launch_path, launch_anchor):
            checkout = (
                f"Current: harness anchor {_code(launch_path)} (read-only); "
                "create/select a harness worktree before editing."
            )
        elif launch_path:
            checkout = (
                f"Current: untracked checkout {_code(launch_path)}; resolve its "
                "management state before editing."
            )
        else:
            checkout = ""

        if writable:
            knowledge = (
                f"Knowledge: {_code(res.repo)}; registered anchor {_code(res.path)} "
                f"(read-only identity); writable paired worktree "
                f"{_code(writable.path)}. Use the pair, never the anchor."
            )
        else:
            diagnostic = (
                f" Pair resolution: {pair.error}."
                if pair and pair.error
                else ""
            )
            knowledge = (
                f"Knowledge: {_code(res.repo)}; registered anchor {_code(res.path)} "
                "(read-only identity); no writable paired knowledge worktree. Before "
                "writing, create/select one with "
                f"`agent-worktrees -p {res.repo} create --json` from the session "
                f"command catalog.{diagnostic}"
            )

        return (
            f"**State/worktree:** {checkout} {knowledge} **Routing:** harness "
            "instructions/configuration, skills, agents, plugins, and docs -> "
            "harness worktree; personal preferences, efforts, logs, notes, private "
            "data/plugins, and ambiguous writes -> knowledge worktree; product "
            "changes -> product repo. For another repo or machine, use "
            "`agent-worktrees related resolve <name>` and honor class, locus, and "
            "delegate. Never edit anchors; follow the injected worktree lifecycle."
        )

    if res.repo:
        binding = (
            f"is configured as {_code(res.repo)} but cannot be resolved on this "
            "machine"
        )
        action = (
            f"Repair/register {_code(res.repo)} as worktree-class before writing."
        )
    else:
        binding = "is not configured on this machine"
        action = (
            "Run `binding-knowledge` to attach an existing checkout, clone a "
            "remote, or create a private repo; declining leaves state writes blocked."
        )
    detail = f" Diagnostic: {res.error}" if res.error else ""
    return (
        f"**The user's state repo** {binding}. Stateful workflows are blocked: "
        "do not write personal state into the launch repo or another fallback. "
        f"{action}{detail}"
    )


# ---------------------------------------------------------------------------
# Config-source anchors (E1e) -- the KNOWLEDGE OVERLAY (config-graft) seam
# ---------------------------------------------------------------------------
#
# Terminology (two distinct axes; see the citadel-harness-split effort):
#   * state-root      -- the personal-state WRITE destination (efforts/logs/
#                        visions), resolved by ``resolve_state_root`` above.
#   * knowledge overlay -- the config-graft READ axis: the bound knowledge
#                        repo's ``.agent-*`` config (related.yaml / machines.yaml
#                        / .agent-codespaces/config.yaml) extending the harness base.
# The overlay REUSES the state-root resolver only to LOCATE the knowledge
# checkout; it is a separate concept from where personal state is written. A
# self-hosted repo has a state-root (itself) but grafts NO overlay.

@dataclass(frozen=True)
class ConfigSource:
    """One checkout that contributes ``.agent-*`` config for a launch context."""

    anchor: str
    """Absolute path to the checkout supplying config (``related.yaml``,
    ``machines.yaml``, ...)."""
    origin: str
    """``"harness"`` for the base/launch repo, ``"knowledge"`` for the bound
    knowledge repo's config overlay."""


def _default_anchor(config: cfg.Config) -> str | None:
    try:
        repo_cfg = config.default_repo
    except KeyError:
        return None
    return repo_cfg.anchor if repo_cfg else None


def config_source_anchors(
    config: cfg.Config,
    *,
    base_anchor: str | None = None,
    cwd: str | None = None,
) -> list[ConfigSource]:
    """Ordered ``.agent-*`` config sources for the current launch context.

    This is the **knowledge overlay** (config-graft) seam (E1e): agent-* tools
    that read harness config (``related.yaml``, ``machines.yaml``,
    ``.agent-codespaces/config.yaml``, ...) should union across these anchors
    instead of assuming the launch repo is the sole config source. The list is in
    **overlay order** -- the base (harness / launch) anchor first, then the bound
    **knowledge repo** when the launch repo requires an external state root -- so
    later sources win on conflict.

    This is the config-READ axis, distinct from the **state-root** (the personal-
    state WRITE destination): it only reuses the state-root resolver to LOCATE the
    knowledge checkout. A normal (self-hosted) repo yields just its own anchor
    (no overlay), so grafted readers behave identically to the pre-overlay
    single-anchor path.

    Args:
        config: The layered project config (``cfg.load_config()``).
        base_anchor: Explicit base anchor (e.g. a ``--repo`` target or the
            control-plane anchor). Defaults to the git worktree root of ``cwd``,
            then the launch repo's anchor.
        cwd: Directory for the git-toplevel probe (defaults to the process cwd).

    Returns:
        A list of :class:`ConfigSource`, base first. Empty only when no base
        anchor can be resolved at all.
    """
    base = base_anchor or _git_toplevel(cwd) or _default_anchor(config)
    sources: list[ConfigSource] = []
    if base:
        sources.append(ConfigSource(anchor=base, origin="harness"))
    res = resolve_state_root(config, cwd=cwd)
    if res.requires_external and res.bound and res.path:
        if not base or os.path.abspath(res.path) != os.path.abspath(base):
            sources.append(ConfigSource(anchor=res.path, origin="knowledge"))
    return sources
