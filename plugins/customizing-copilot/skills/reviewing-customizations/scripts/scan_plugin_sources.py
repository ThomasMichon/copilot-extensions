from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

AGENT_WORKTREES_REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
COPILOT_EXTENSIONS_SOURCE = "https://github.com/ThomasMichon/copilot-extensions"


@dataclass
class PluginSource:
    """A loaded plugin with ownership, source, and release identity.

    ``controlled`` is True when the repo under review OWNS the plugin (its own
    in-repo ``.ai`` directory-marketplace plugins, or its ``plugins/*`` suite):
    those get full checks and their findings are actionable in-repo. When False
    the plugin is **external** (installed from another marketplace) -- its skills
    are reference-only for collision detection, while agent safety findings are
    advisory and carry an upstream remediation pointer.

    ``commit`` is a best-effort immutable git commit SHA captured at
    *discovery* time -- when (and only when) the payload lives inside a
    trusted git working tree (this repo's own tree, or a checkout resolved
    via the ``agent-worktrees-repo`` source kind), empty otherwise (e.g. a
    plain installed-plugins payload copy with no local git history, or an
    arbitrary ``directory`` source with no verifiable provenance). It can
    go stale between discovery and use (e.g. across a `refresh` that
    advances the checkout) -- :func:`resolve_pinned_commits` re-probes
    fresh rather than trusting this field for exactly that reason.
    ``is_local_checkout`` marks which sources are even eligible for that
    re-probe: only ``controlled`` (this reviewing repo's own tree) or a
    footprint resolved via ``agent-worktrees-repo`` (a registered, named
    repo looked up through the trusted ``agent-worktrees`` CLI) qualify --
    a plain ``directory`` source is an arbitrary local path declared in a
    marketplace manifest with no provenance guarantee at all (it could
    point at a payload copied into a subdirectory of some entirely
    unrelated git repository), and an installed-plugins footprint is
    always a copied external payload; neither is ever re-probed or pinned.
    """

    skills_root: Path
    origin: str
    controlled: bool = False
    source: str = ""
    version: str = ""
    commit: str = ""
    is_local_checkout: bool = False

    @property
    def payload_root(self) -> Path:
        """Return the plugin directory containing skills, agents, and hooks."""
        return self.skills_root.parent

    @property
    def plugin_name(self) -> str:
        """Return the unqualified plugin name from the loaded identity."""
        return self.origin.rsplit("/", 1)[-1]

    @property
    def marketplace(self) -> str:
        """Return the marketplace qualifier from the loaded identity."""
        return self.origin.rsplit("/", 1)[0] if "/" in self.origin else ""


def _load_json_optional(path: Path) -> dict | None:
    """Load a JSON object, distinguishing an empty object from failure."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _load_json(path: Path) -> dict:
    """Best-effort JSON load (settings/manifests); returns {} on any problem."""
    data = _load_json_optional(path)
    if data is None:
        return {}
    return data


def _load_jsonc(path: Path) -> dict:
    """Best-effort load for Copilot's leading-comment config.json."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        body = "\n".join(
            line for line in text.splitlines()
            if not line.lstrip().startswith("//")
        )
        data = json.loads(body)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _repo_is_trusted(repo_root: Path, home: Path) -> bool:
    """Match Copilot's exact persisted-folder trust boundary."""
    data = _load_jsonc(home / ".copilot" / "config.json")
    folders = data.get("trustedFolders")
    if not isinstance(folders, list):
        return False
    resolved = repo_root.resolve()
    for value in folders:
        if not isinstance(value, str):
            continue
        try:
            if Path(value).expanduser().resolve(strict=True) == resolved:
                return True
        except OSError:
            continue
    return False


def _merged_settings(
    repo_root: Path,
    home: Path | None = None,
    *,
    require_trust: bool = False,
    include_user: bool = True,
    include_local: bool = True,
) -> tuple[dict[str, bool], dict[str, tuple[dict, Path]]]:
    """Merge the settings that decide a repo's *loaded* plugin set.

    Reads the repo's committed ``.github/copilot/settings.json`` (and the
    ``.claude/settings.json`` fallback) plus the user ``~/.copilot/settings.json``,
    and returns ``(enabled_plugins, marketplaces)``. Repo settings take
    precedence over user settings for a marketplace of the same name. Plugin
    booleans use last-layer-wins semantics, including explicit ``false``.
    """
    selected_home = home or Path.home()
    layers = (
        [(selected_home / ".copilot" / "settings.json", selected_home)]
        if include_user
        else []
    )
    if not require_trust or _repo_is_trusted(repo_root, selected_home):
        layers.append((repo_root / ".claude" / "settings.json", repo_root))
        if include_local:
            layers.append(
                (repo_root / ".claude" / "settings.local.json", repo_root)
            )
        layers.append(
            (repo_root / ".github" / "copilot" / "settings.json", repo_root)
        )
        if include_local:
            layers.append(
                (
                    repo_root / ".github" / "copilot" / "settings.local.json",
                    repo_root,
                )
            )
    enabled: dict[str, bool] = {}
    marketplaces: dict[str, tuple[dict, Path]] = {}
    for p, base in layers:
        data = _load_json(p)
        ep = data.get("enabledPlugins")
        if isinstance(ep, dict):
            for k, v in ep.items():
                if isinstance(v, bool):
                    enabled[str(k)] = v
        mk = data.get("extraKnownMarketplaces")
        if isinstance(mk, dict):
            for k, v in mk.items():
                if isinstance(v, dict):
                    marketplaces[str(k)] = (v, base)
    return enabled, marketplaces


def _plugin_repo_url(footprint: Path) -> str:
    """A plugin's upstream repo URL from its manifest (``repository`` /
    ``homepage``), read from either manifest spelling. Empty when unknown."""
    for manifest in (
        footprint / "plugin.json",
        footprint / ".claude-plugin" / "plugin.json",
    ):
        data = _load_json(manifest)
        for key in ("repository", "homepage"):
            val = data.get(key)
            if isinstance(val, dict):
                val = val.get("url", "")
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _plugin_version(footprint: Path) -> str:
    """Return the plugin's declared version from either manifest spelling."""
    for manifest in (
        footprint / "plugin.json",
        footprint / ".claude-plugin" / "plugin.json",
    ):
        data = _load_json(manifest)
        value = data.get("version")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


_GIT_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")

#: Git environment variables that let a subprocess override which
#: repository/worktree/object-store it operates against, independent of
#: the ``-C <path>`` argument -- an ambient `GIT_DIR`/`GIT_WORK_TREE`/object-
#: directory value inherited from the caller's environment must never be
#: allowed to redirect a provenance probe at an unrelated repository.
_GIT_ENV_PREFIX = "GIT_"


def _git_isolated_env() -> dict[str, str]:
    """A subprocess environment with every ``GIT_*`` variable stripped.

    Mirrors the repository-identity isolation pattern used elsewhere in the
    facility's own git-identity probes (an inherited `GIT_DIR`/
    `GIT_WORK_TREE`/object-directory override must never let a caller
    redirect this probe at a different repository than the one named by
    ``-C``).
    """
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(_GIT_ENV_PREFIX)
    }


def _payload_is_clean(git: str, footprint: Path) -> bool:
    """Whether ``footprint`` has no local modifications, untracked, or
    ignored files.

    ``git rev-parse HEAD`` alone only proves the *containing repository's*
    current commit -- it says nothing about whether the payload on disk at
    ``footprint`` actually matches that commit. A dirty or untracked file
    within the payload would otherwise still receive a valid-looking pin,
    letting the bypass path publish content that was never present at the
    pinned commit. ``git status --porcelain --ignored --untracked-files=all``
    scoped to ``footprint`` (via ``-C`` + a ``.`` pathspec) reports
    staged/unstaged modifications, untracked files, **and** ignored files --
    plain ``--porcelain`` alone omits ignored entries, and
    ``--untracked-files=all`` is required too: a caller's (or the ambient
    environment's) ``status.showUntrackedFiles=no`` config would otherwise
    silently hide a genuinely new, uncommitted payload file from even the
    default untracked reporting. An ignored-but-still-read file (the
    projection scanner does not consult ``.gitignore``) is exactly as
    unproven against ``HEAD`` as an untracked one. Any output at all means
    the payload is not a faithful checkout of ``HEAD`` -- this deliberately
    over-rejects (e.g. a stray `__pycache__`/build-cache directory inside
    the payload also blocks pinning) rather than trying to distinguish
    "harmless" ignored content from real payload drift.
    """
    try:
        result = subprocess.run(
            [
                git, "-C", str(footprint),
                "status", "--porcelain", "--ignored", "--untracked-files=all",
                "--", ".",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=_git_isolated_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and not result.stdout.strip()


def _git_head(git: str, footprint: Path) -> str:
    """Return ``footprint``'s current ``HEAD`` commit SHA, or ``""``."""
    try:
        result = subprocess.run(
            [git, "-C", str(footprint), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=_git_isolated_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    sha = result.stdout.strip()
    return sha if _GIT_COMMIT_SHA.fullmatch(sha) else ""


def _plugin_commit(footprint: Path) -> str:
    """Return the immutable git commit SHA a payload was checked out at.

    Only resolvable when ``footprint`` lives inside a real git working tree
    -- this repo's own directory-marketplace plugins, or an
    ``agent-worktrees-repo`` checkout, are the two cases that qualify today
    -- **and** that payload has no local modifications, untracked, or
    ignored files (see :func:`_payload_is_clean`): a dirty checkout cannot
    be pinned, since the commit it would report no longer describes what is
    actually on disk. Returns ``""`` -- never raises -- for every other
    case: git is unavailable, the footprint is not inside a git working
    tree, the payload is dirty, or any command fails for any reason. Every
    git subprocess runs with `GIT_*` environment variables stripped (see
    :func:`_git_isolated_env`) so an inherited `GIT_DIR`/`GIT_WORK_TREE`
    override can never redirect this probe at an unrelated repository.
    Deliberately never attempts to resolve a commit for a plain
    installed-plugins payload copy (no local git history to read there) --
    that remains a genuinely unsolved case (see
    ``efforts/active/ambient-guidance-navigability``'s Journal), not
    something to guess at. **Callers must only invoke this against a
    trusted-checkout footprint** (``controlled`` -- this reviewing repo's
    own tree -- or one resolved via the ``agent-worktrees-repo`` source
    kind) -- a plain ``directory`` source is an arbitrary local path with
    no provenance guarantee, and an installed-plugins footprint is always a
    copied external payload with no source-commit provenance of its own,
    even when either happens to live under some unrelated enclosing git
    checkout; `assemble_enabled_plugins` and `_sources_from_raw_dir`
    enforce this by construction (see their own call sites, and
    `PluginSource.is_local_checkout`) rather than this function guessing at
    the caller's intent.

    **HEAD is read before *and* after the cleanliness check**, and both
    reads must agree: the clean check and a single ``rev-parse`` are two
    separate subprocesses with no shared lock, so a payload modified in the
    gap between them could otherwise still report a stale, no-longer-
    accurate ``HEAD`` as clean. Reading ``HEAD`` again immediately after
    and requiring it to be unchanged catches any concurrent commit/checkout
    that moved it during the probe -- it does not eliminate every possible
    race against a payload change (no purely read-only, unlocked probe
    can), but it closes the specific, easily-observable window between this
    function's own two subprocess calls.
    """
    git = shutil.which("git")
    if git is None:
        return ""
    head_before = _git_head(git, footprint)
    if not head_before:
        return ""
    if not _payload_is_clean(git, footprint):
        return ""
    head_after = _git_head(git, footprint)
    return head_after if head_after and head_after == head_before else ""


def resolve_pinned_commits(sources: list[PluginSource]) -> dict[str, str]:
    """Build a ``"<plugin>@<marketplace>" -> commit SHA`` map from ``sources``.

    **Re-probes each eligible source's commit fresh at call time** -- it
    does NOT trust :attr:`PluginSource.commit` (captured at discovery time,
    which can precede a `refresh` that advances a directory-marketplace
    checkout; using the stale field would let a payload that changed during
    refresh still satisfy the pin conjunct with its old SHA). Only sources
    with :attr:`PluginSource.is_local_checkout` are even eligible for
    re-probing -- an installed-plugins payload copy is never re-probed or
    pinned, no matter what its (informational-only) ``commit`` field says.
    A source this resolver could not pin (today: any plain
    installed-plugins payload copy, or a local checkout that is currently
    dirty) is simply absent from the map, so
    ``projection_reflect.bypass_decision``'s ``pinned_commits`` conjunct
    correctly treats it as unpinned (fail-closed, review-only) rather than
    silently vacuously trusted. This is an honestly **partial** resolver: it
    closes the immutable-pin gap for self-hosted/directory-marketplace
    sources today, and does not invent an answer for externally-installed
    ones (see ``efforts/active/ambient-guidance-navigability``'s tracked
    follow-up, issue #3132, for that remaining half).
    """
    pinned: dict[str, str] = {}
    for source in sources:
        if not source.is_local_checkout:
            continue
        commit = _plugin_commit(source.payload_root)
        if commit:
            pinned[f"{source.plugin_name}@{source.marketplace}"] = commit
    return pinned


def _plugin_manifest_path(footprint: Path) -> Path:
    """Return whichever manifest spelling exists, preferring the root one."""
    root_manifest = footprint / "plugin.json"
    if root_manifest.is_file():
        return root_manifest
    return footprint / ".claude-plugin" / "plugin.json"


def _plugin_declares_agents(footprint: Path) -> bool:
    """Whether the plugin manifest declares a truthy top-level `agents` field.

    The runtime's documented behavior falls back to `plugin_root/agents` when
    this field is absent, but every shipped example (e.g.
    `copilot-extensions-harness`) declares it explicitly, and explicit is more
    robust than implicit: it survives a future change to the default-path
    fallback and gives a reviewer an unambiguous manifest to read. Mirrors
    `_plugin_version`'s two-manifest-spelling lookup, but does not merge across
    spellings: the first manifest found (root `plugin.json`, else
    `.claude-plugin/plugin.json`) is authoritative, matching the runtime's own
    precedence.
    """
    data = _load_json(_plugin_manifest_path(footprint))
    return bool(data.get("agents"))


def _plugin_hook_files(footprint: Path) -> set[Path]:
    """Return conventional and manifest-declared hook files for a plugin."""
    manifest_data: dict = {}
    for manifest in (
        footprint / "plugin.json",
        footprint / ".claude-plugin" / "plugin.json",
    ):
        data = _load_json(manifest)
        if data:
            manifest_data = data
            break
    configured = manifest_data.get("hooks")
    values = [configured] if isinstance(configured, str) else configured
    if isinstance(values, list):
        payload_root = footprint.resolve()
        paths = set()
        for value in values:
            if not isinstance(value, str) or not value.strip():
                continue
            candidate = (footprint / value).resolve()
            if candidate.is_relative_to(payload_root):
                paths.add(candidate)
    else:
        paths = {footprint / "hooks.json", footprint / "hooks" / "hooks.json"}
    return {path for path in paths if path.is_file()}


def _has_reviewable_payload(footprint: Path) -> bool:
    """Return whether a plugin contains skills, agents, or hook declarations."""
    skills_root = footprint / "skills"
    agents_root = footprint / "agents"
    return (
        (skills_root.is_dir() and any(skills_root.glob("*/SKILL.md")))
        or (agents_root.is_dir() and any(agents_root.glob("*.agent.md")))
        or bool(_plugin_hook_files(footprint))
    )


def _marketplace_manifest(root: Path) -> tuple[dict, Path] | None:
    """Load a supported marketplace manifest and its plugin-root base."""
    for relative in (
        Path(".github/plugin/marketplace.json"),
        Path(".claude-plugin/marketplace.json"),
    ):
        path = root / relative
        data = _load_json_optional(path)
        if data is not None:
            manifest_root = (
                path.parent.parent.parent
                if relative.parts[0] == ".github"
                else path.parent.parent
            )
            return data, manifest_root
    return None


def _agent_worktrees_repo_root(repo_name: str) -> Path | None:
    """Resolve a registered repo's local checkout path via the trusted CLI."""
    if not AGENT_WORKTREES_REPO_NAME.fullmatch(repo_name):
        return None
    agent_worktrees = shutil.which("agent-worktrees")
    if not agent_worktrees:
        return None
    try:
        completed = subprocess.run(
            [agent_worktrees, "repos", "find", repo_name],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    try:
        path = Path(lines[-1]).resolve(strict=True)
    except OSError:
        return None
    return path if path.is_dir() else None


def _directory_marketplace_plugin(
    marketplace: str,
    name: str,
    declaration: dict,
    base: Path,
) -> Path | None:
    """Resolve one directory-marketplace entry with runtime-equivalent checks."""
    source = declaration.get("source")
    if not isinstance(source, dict):
        return None
    source_kind = str(source.get("source", "")).strip().lower()
    if source_kind == "directory":
        raw_path = source.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None
        configured = Path(raw_path).expanduser()
        root = configured if configured.is_absolute() else base / configured
        try:
            root = root.resolve(strict=True)
        except OSError:
            return None
    elif source_kind == "agent-worktrees-repo":
        repo_name = source.get("repo")
        if not isinstance(repo_name, str) or not repo_name.strip():
            return None
        repo_root = _agent_worktrees_repo_root(repo_name.strip())
        if repo_root is None:
            return None
        raw_path = source.get("path", ".ai")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None
        configured = Path(raw_path).expanduser()
        root = configured if configured.is_absolute() else repo_root / configured
        try:
            root = root.resolve(strict=True)
        except OSError:
            return None
    else:
        return None
    loaded = _marketplace_manifest(root)
    if loaded is None:
        return None
    manifest, manifest_root = loaded
    if manifest.get("name") != marketplace:
        return None
    plugin_root = manifest_root
    metadata = manifest.get("metadata")
    if isinstance(metadata, dict):
        configured_root = metadata.get("pluginRoot")
        if isinstance(configured_root, str) and configured_root.strip():
            plugin_root = manifest_root / configured_root
    try:
        plugin_root = plugin_root.resolve(strict=True)
        plugin_root.relative_to(root)
    except (OSError, ValueError):
        return None
    entries = manifest.get("plugins")
    if not isinstance(entries, list):
        return None
    matches = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("name") == name
    ]
    if len(matches) != 1:
        return None
    plugin_source = matches[0].get("source")
    if not isinstance(plugin_source, str) or not plugin_source.strip():
        return None
    try:
        footprint = (plugin_root / plugin_source).resolve(strict=True)
        footprint.relative_to(plugin_root)
    except (OSError, ValueError):
        return None
    manifest_data = _load_json_optional(footprint / "plugin.json")
    if manifest_data is None:
        manifest_data = _load_json_optional(
            footprint / ".claude-plugin" / "plugin.json"
        )
    return (
        footprint
        if manifest_data is not None and manifest_data.get("name") == name
        else None
    )


def assemble_enabled_plugins(
    repo_root: Path,
    installed_root: Path | None = None,
    *,
    home: Path | None = None,
    require_trust: bool = False,
    include_user: bool = True,
    include_local: bool = True,
) -> list[PluginSource]:
    """Assemble the plugin set *actually loaded for this repo* into review scope."""
    selected_home = home or Path.home()
    if installed_root is None:
        installed_root = selected_home / ".copilot" / "installed-plugins"
    enabled, marketplaces = _merged_settings(
        repo_root,
        home,
        require_trust=require_trust,
        include_user=include_user,
        include_local=include_local,
    )
    out: list[PluginSource] = []
    for key in sorted(enabled):
        if not enabled[key]:
            continue
        name, _, mkt = key.partition("@")
        name = name.strip()
        mkt = mkt.strip()
        if not name:
            continue
        origin = f"{mkt}/{name}" if mkt else name
        declaration, base = marketplaces.get(mkt, ({}, repo_root))
        src = declaration.get("source") or {}
        src_kind = (
            str(src.get("source", "")).strip().lower()
            if isinstance(src, dict)
            else ""
        )

        footprint = _directory_marketplace_plugin(mkt, name, declaration, base)
        # A directory-marketplace footprint is only trusted checkout
        # provenance when it is EITHER this reviewing repo's own tree
        # (`controlled`) OR resolved via the `agent-worktrees-repo` source
        # kind (a registered, named repo looked up through the trusted
        # `agent-worktrees` CLI). A plain `directory` source is an
        # arbitrary local path declared in the marketplace manifest -- it
        # could point at a payload copied into a subdirectory of some
        # entirely unrelated git repository, so `footprint is not None`
        # alone is not proof of provenance. An installed_root footprint
        # (the `else` branch below) is always a copied external payload
        # with no source-commit provenance of its own. `_plugin_commit()`
        # must never run against anything that fails this check: doing so
        # could return an unrelated enclosing repository's HEAD and let
        # `requireImmutablePin` accept the wrong identity.
        if footprint is not None:
            try:
                controlled = (
                    repo_root.resolve() in footprint.parents
                    or footprint == repo_root.resolve()
                )
            except Exception:
                controlled = False
            is_local_checkout = controlled or src_kind == "agent-worktrees-repo"
            skills_root = footprint / "skills"
            source_url = "" if controlled else _plugin_repo_url(footprint)
        else:
            footprint = installed_root / mkt / name if mkt else installed_root / name
            skills_root = footprint / "skills"
            controlled = False
            is_local_checkout = False
            source_url = ""
            if isinstance(src, dict) and src_kind == "github" and src.get("repo"):
                source_url = f"https://github.com/{str(src['repo']).strip()}"
            if not source_url:
                source_url = _plugin_repo_url(footprint)

        out.append(
            PluginSource(
                skills_root=skills_root,
                origin=origin,
                controlled=controlled,
                source=source_url,
                version=_plugin_version(footprint),
                commit=_plugin_commit(footprint) if is_local_checkout else "",
                is_local_checkout=is_local_checkout,
            )
        )
    return out


def _sources_from_raw_dir(root: Path) -> list[PluginSource]:
    """Convert a raw installed-plugins tree into external PluginSource entries."""
    out: list[PluginSource] = []
    for plugin_dir in sorted(root.glob("*/*")):
        if not plugin_dir.is_dir() or not _has_reviewable_payload(plugin_dir):
            continue
        origin = f"{plugin_dir.parent.name}/{plugin_dir.name}"
        out.append(
            PluginSource(
                skills_root=plugin_dir / "skills",
                origin=origin,
                controlled=False,
                source=_plugin_repo_url(plugin_dir),
                version=_plugin_version(plugin_dir),
                # This converts a raw *installed-plugins* tree -- a copied
                # external payload with no source-commit provenance of its
                # own -- into external sources; never call _plugin_commit()
                # here (it could accidentally return an enclosing checkout's
                # HEAD and mark a copied payload as pinned).
                commit="",
            )
        )
    return out
