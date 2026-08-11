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
from dataclasses import dataclass

from . import config as cfg
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


def _git_toplevel(cwd: str | None) -> str | None:
    """Return the git worktree root of ``cwd`` (or the process cwd), or None."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd or None,
            capture_output=True,
            text=True,
            timeout=10,
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


def state_repo_definition(res: StateRoot) -> str:
    """Return the sessionStart **"the user's state repo"** definition (Markdown).

    This is the single, authoritative binding of the term *"the user's state
    repo"* that agent-worktrees injects into session context (via the
    ``session-conduct`` sessionStart hook, ``state-root --conduct``). Downstream
    plugins/skills refer to "the user's state repo" in **plain prose** and never
    invoke agent-worktrees themselves; this injection binds the term to the
    concrete resolved checkout so those references resolve. When agent-worktrees
    is not installed nothing is injected, and the prose degrades to its plain
    meaning (the checkout where the user keeps their personal state).

    The returned string is a self-contained paragraph (no trailing newline);
    the hook merges it into ``additionalContext`` ahead of any static conduct
    fragments. When a knowledge repo is bound (``source == "knowledge_repo"``)
    the harness and the state repo are distinct checkouts, so the definition
    also carries a **write-routing** clause: shared-harness changes go in the
    harness repo, everything else in the user's state repo.
    """
    if res.path:
        if res.source == "knowledge_repo":
            where = "your machine's bound knowledge repo"
        elif res.source == "explicit":
            where = f"the '{res.repo}' repo"
        else:
            where = "your current repo (self-hosted)"
        text = (
            f"**The user's state repo** is `{res.path}` — {where}. It is the "
            "checkout where the user's personal state and reference data live: "
            "efforts, logs, visions, and skill reference data such as "
            "ownership.yml, weekly-updates/, icm/, on-call/, backlog/, and "
            "review-persona guidance. Whenever a skill refers to \"the user's "
            "state repo,\" read and write those paths under this checkout."
        )
        # When a knowledge repo is bound, the shared harness and the user's
        # state repo are two DISTINCT checkouts -- so tell the agent which
        # changes belong where. (For a self-hosted repo there is only one
        # checkout, so this routing guidance would be noise.)
        if res.source == "knowledge_repo":
            text += (
                " **Where changes go:** changes to the **shared harness** itself "
                "— its generic, name-free configuration, skills, agents, "
                "AGENTS.md, and docs (anything that benefits everyone using the "
                "harness) — belong in the harness repo (your current checkout). "
                "Everything else — the user's personal state and data (the "
                "efforts / logs / visions / preferences / personal skills / "
                "reference data above) — belongs in the user's state repo named "
                "above."
            )
        return text
    return (
        "**The user's state repo** — the checkout where the user's personal "
        "state and reference data (efforts, logs, visions, and skill reference "
        "data) belong — is not bound on this machine yet. A skill that needs to "
        "read or write \"the user's state repo\" should stop and ask the user "
        "to bind one (harness setup) before writing personal state."
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
