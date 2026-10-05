"""Versioned self-install for the Worktree Manager (matches the harness convention).

The Worktree Manager is delivered out-of-plugin, but its install flow follows the
**same versioning convention as the harness's other installers** (see
``plugins/agent-worktrees/scripts/versioned_runtime.py``):

* an immutable per-version slot at ``<root>/versions/<version>/``,
* a plain-text ``<root>/current-version`` **marker file** naming the active
  version (written atomically: temp + rename), and
* a **binstub** in ``~/.local/bin/`` (``worktree-manager`` + ``.cmd``/``.ps1`` on
  Windows) that resolves the marker and runs the active slot.

This is *convention* reuse, not code reuse: nothing here imports the plugin's
versioned-runtime helper, so the out-of-plugin, dependency-free boundary holds.
The install is idempotent and **version-gated** — re-running with the same
payload version is a no-op; a newer payload publishes a new slot + marker.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import worktree_manager

MARKER = "current-version"
VERSIONS_DIR = "versions"
STAGING_DIR = "staging"
CONTROL_PLANE_PROVIDERS_SUBDIR = "control-plane-providers.d"
CONTROL_PLANE_PROVIDERS_DIR_ENV = "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR"
_VERSION_RE = re.compile(r'__version__\s*=\s*"([^"]+)"')


def manager_repo(root: Path | None = None) -> str:
    """The git source :func:`self_update` fetches from.

    Resolves the **user-level source config** (``[source].repo`` in
    ``<root>/config.toml``), else the canonical GitHub repo. The override is a
    config file managed by ``worktree-manager source`` — deliberately **not** an
    env var — so it can point the updater at a **fork** or a **canary branch**
    for future updates, without weakening the out-of-plugin boundary.
    """
    from .source_config import resolved_repo

    return resolved_repo(root)


def manager_ref(root: Path | None = None) -> str:
    """The branch/ref :func:`self_update` fetches, from the source config or default."""
    from .source_config import resolved_ref

    return resolved_ref(root)


# Owner/name from a GitHub https or ssh remote (``.git`` + trailing slash optional).
_GITHUB_REPO_RE = re.compile(r"github\.com[/:]+(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$")


def manager_tarball_url(root: Path | None = None, ref: str | None = None) -> str | None:
    """The GitHub codeload tarball URL for the configured source, or ``None``.

    Derives ``https://codeload.github.com/<owner>/<name>/tar.gz/<ref>`` from the
    resolved source repo (:func:`manager_repo`) when it is a GitHub remote. Returns
    ``None`` for a non-GitHub source (a local path or an enterprise remote), where
    no codeload endpoint exists and git is genuinely required. This is what lets
    the bootstrap and :func:`self_update` fetch the payload **without git** on a
    bare machine — the git-optional path.
    """
    m = _GITHUB_REPO_RE.search(manager_repo(root).strip())
    if not m:
        return None
    ref = ref or manager_ref(root)
    return f"https://codeload.github.com/{m.group('owner')}/{m.group('name')}/tar.gz/{ref}"


def remote_init_url(root: Path | None = None, ref: str | None = None) -> str | None:
    """The raw GitHub URL for the configured source's ``__init__.py``, or
    ``None`` for a non-GitHub source (mirrors :func:`manager_tarball_url`'s
    GitHub-only support). This is the lightweight (single small file, no
    clone/tarball) endpoint :func:`fetch_remote_version` reads to check
    whether a newer Manager release exists without a full fetch."""
    m = _GITHUB_REPO_RE.search(manager_repo(root).strip())
    if not m:
        return None
    ref = ref or manager_ref(root)
    return (
        f"https://raw.githubusercontent.com/{m.group('owner')}/{m.group('name')}"
        f"/{ref}/worktree-manager/src/worktree_manager/__init__.py"
    )


def fetch_remote_version(
    root: Path | None = None, ref: str | None = None, timeout: float = 5.0,
) -> str | None:
    """The ``__version__`` currently on the configured source's ``ref``, via a
    single small HTTP GET -- no git, no clone/tarball. Best-effort: returns
    ``None`` on any failure (network, timeout, non-GitHub source, unparsable
    content), so a caller (a background update-availability poll) never has
    to guard this itself. This is deliberately separate from
    :func:`manager_tarball_url`/:func:`self_update`'s full fetch: it exists
    purely to answer "is a newer Manager version available", cheaply enough
    to run on a background thread on every picker launch."""
    url = remote_init_url(root, ref)
    if url is None:
        return None
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            text = resp.read().decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError, ValueError):
        return None
    m = _VERSION_RE.search(text)
    return m.group(1) if m else None


def default_root() -> Path:
    """Install root, mirroring ``~/.agent-worktrees`` for the core installer."""
    env = os.environ.get("WORKTREE_MANAGER_ROOT")
    if env:
        return Path(env)
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    return home / ".worktree-manager"


def local_bin() -> Path:
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    return home / ".local" / "bin"


def control_plane_providers_dir() -> Path:
    override = os.environ.get(CONTROL_PLANE_PROVIDERS_DIR_ENV)
    if override:
        return Path(override).expanduser()
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    return home / ".agent-worktrees" / CONTROL_PLANE_PROVIDERS_SUBDIR


def running_payload_dir() -> Path:
    """The project dir of the currently-running payload (has pyproject.toml)."""
    # .../worktree-manager/src/worktree_manager/self_install.py -> parents[2] == project.
    return Path(worktree_manager.__file__).resolve().parents[2]


def payload_version(payload_dir: Path | None = None) -> str | None:
    """Read ``__version__`` from a payload's src/worktree_manager/__init__.py."""
    pd = payload_dir or running_payload_dir()
    init = pd / "src" / "worktree_manager" / "__init__.py"
    try:
        m = _VERSION_RE.search(init.read_text("utf-8"))
    except OSError:
        return None
    return m.group(1) if m else None


def current_version(root: Path | None = None) -> str | None:
    """The active version from the marker file, or None if not installed."""
    marker = (root or default_root()) / MARKER
    try:
        return marker.read_text("utf-8").strip() or None
    except OSError:
        return None


def version_slot(version: str, root: Path | None = None) -> Path:
    return (root or default_root()) / VERSIONS_DIR / version


_SLOT_COMPLETE_MARKER = ".install-complete"
_SLOT_KEY_FILES = (
    "pyproject.toml",
    "src/worktree_manager/__init__.py",
    "src/worktree_manager/__main__.py",
)


def _invalidate_slot_completion(slot: Path) -> None:
    """Remove ``slot``'s completion marker, if any, before any mutation of
    ``slot`` begins. A slot is proven complete only by this marker having
    been published as the LAST step of a fully successful
    ``_copy_payload_unsafe()`` -- invalidating it first (rather than
    relying on the mutation that follows to remove it, which can itself
    fail partway through) guarantees no stale marker from a slot's
    previous occupant can ever survive an interrupted rebuild of the same
    path and be mistaken for proof that the NEW content is complete.

    Only a missing marker (the ordinary case: no prior install, or one
    already invalidated) is swallowed. Any OTHER failure -- a
    ``PermissionError`` from the marker still being held open, most
    notably -- must propagate rather than let mutation proceed with a
    stale marker still in place: ``_copy_payload``'s own boundary
    normalizes it to the one exception type ``self_install()`` catches,
    aborting the install instead of silently risking exactly the
    stale-proof-of-completeness state this marker exists to prevent.
    """
    try:
        (slot / _SLOT_COMPLETE_MARKER).unlink()
    except FileNotFoundError:
        pass


def _mark_slot_complete(slot: Path) -> None:
    (slot / _SLOT_COMPLETE_MARKER).write_text("", encoding="utf-8")


def _slot_is_complete(slot: Path) -> bool:
    """``True`` only when ``slot`` is proven complete by its own marker --
    published solely as the last step of a fully successful payload copy
    and pointer materialization -- AND still carries the key files the
    shipped binstubs need to launch (``uv run --project <slot> python -m
    worktree_manager``: ``pyproject.toml``, ``src/worktree_manager/
    __init__.py``, and ``src/worktree_manager/__main__.py``).

    The marker alone proves the install completed; it does not prove the
    slot hasn't been damaged since (a file removed by something outside
    this module's control after a genuinely successful install). The key
    files remain a cheap, independent second check against that later
    damage. Directory existence, or any subset of files present without
    the marker, proves nothing: a slot can exist, and even partially
    survive a failed ``_copy_payload()`` recopy (one that imports or
    materialized libraries it depends on at runtime may still be missing),
    while still being unlaunchable (``No module named worktree_manager``
    or an equivalent failure at run time).
    """
    return (slot / _SLOT_COMPLETE_MARKER).is_file() and all(
        (slot / rel).is_file() for rel in _SLOT_KEY_FILES
    )


def _binstub_files() -> list[str]:
    if os.name == "nt":
        return ["worktree-manager.cmd", "worktree-manager.ps1", "worktree-manager"]
    return ["worktree-manager"]


def _primary_binstub_name() -> str:
    return "worktree-manager.cmd" if os.name == "nt" else "worktree-manager"


def _provider_manifest_template_path(payload_dir: Path | None = None) -> Path:
    candidate = (payload_dir or running_payload_dir()) / "references" / "control-plane-provider.json"
    if candidate.is_file():
        return candidate
    return running_payload_dir() / "references" / "control-plane-provider.json"


def _expected_control_plane_provider_manifest(
    payload_dir: Path | None = None, *, root: Path | None = None
) -> dict[str, object]:
    template = json.loads(
        _provider_manifest_template_path(payload_dir).read_text(encoding="utf-8")
    )
    template["command"] = [str((local_bin() / _primary_binstub_name()).resolve())]
    template["provider_root"] = str((root or default_root()).resolve())
    return template


def _control_plane_provider_manifest_path(
    payload_dir: Path | None = None, *, root: Path | None = None
) -> Path:
    provider = str(
        _expected_control_plane_provider_manifest(payload_dir, root=root).get("provider") or ""
    ).strip()
    return control_plane_providers_dir() / f"{provider}.json"


def _control_plane_provider_manifest_is_stale(
    payload_dir: Path | None = None, *, root: Path | None = None
) -> bool:
    path = _control_plane_provider_manifest_path(payload_dir, root=root)
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return current != _expected_control_plane_provider_manifest(payload_dir, root=root)


def _write_control_plane_provider_manifest(
    payload_dir: Path | None = None, *, root: Path | None = None
) -> Path:
    path = _control_plane_provider_manifest_path(payload_dir, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique per-writer temp name -- a concurrent repair (two
    # worktree-manager instances independently detecting and fixing the
    # same stale manifest) must not race on one shared ``.tmp`` file, where
    # the loser's os.replace() would raise FileNotFoundError after the
    # winner already moved it.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(
        json.dumps(
            _expected_control_plane_provider_manifest(payload_dir, root=root),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _replace_with_retry(tmp, path)
    return path


def _replace_with_retry(src: Path, dst: Path, *, attempts: int = 10, delay_s: float = 0.01) -> None:
    """``os.replace(src, dst)``, retrying a transient Windows
    ``PermissionError`` (``ERROR_ACCESS_DENIED``, errno 13).

    POSIX ``rename()`` is atomic even when two processes/threads target the
    same ``dst`` concurrently -- exactly one call wins and the other simply
    sees its own replace succeed with no error either way. Windows'
    underlying ``MoveFileEx`` can instead briefly deny one of two truly
    concurrent replacements of the same destination (observed in practice:
    two callers each committing their own uniquely-named temp file onto the
    same manifest path at once). The content is identical in that case --
    both writers computed the same expected manifest -- so retrying after a
    short backoff until this call's own replace succeeds (another
    concurrent winner having already published equivalent content is not a
    failure) is correct, not merely a cosmetic swallow.
    """
    last: PermissionError | None = None
    for _ in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            last = e
            time.sleep(delay_s)
    assert last is not None
    raise last


def binstub_present() -> Path | None:
    lb = local_bin()
    for name in _binstub_files():
        p = lb / name
        if p.exists():
            return p
    return None


def _expected_binstub_contents() -> dict[str, str]:
    """The exact binstub file contents this version would (re)deploy.

    Factored out of :func:`_deploy_binstubs` so :func:`_binstubs_are_stale`
    can compare against the same expectation without writing anything.
    """
    contents = {"worktree-manager": _sh_binstub()}
    if os.name == "nt":
        contents = {
            "worktree-manager.cmd": _cmd_binstub(),
            "worktree-manager.ps1": _ps1_binstub(),
            "worktree-manager": _sh_binstub(),  # for git-bash on Windows
        }
    return contents


def _binstubs_are_stale() -> bool:
    """True when a deployed binstub is missing or its content doesn't match.

    ``binstub_present()`` only proves *some* file with an expected name
    exists -- it says nothing about what's actually in it. A machine can
    carry a **legacy, pre-versioned, or otherwise incompatible**
    ``worktree-manager`` (e.g. left over from an earlier install attempt or
    prototype) that happens to occupy the exact binstub filename. Content-less
    presence-checking then makes :func:`needs_install` report "already
    installed" and skip re-deploying, silently leaving that stale/broken file
    in place -- which then fails the consuming ``agent-worktrees`` seam's
    ``--version`` health probe and falls back to the bundled picker instead of
    handing off to this Manager. Comparing content closes that gap: any
    mismatch (or absence) means the binstub needs (re)deploying.
    """
    lb = local_bin()
    for name, expected in _expected_binstub_contents().items():
        p = lb / name
        try:
            # newline="" preserves exact line endings on read (matches how
            # _deploy_binstubs writes them) -- otherwise universal-newline
            # translation would normalize a freshly-written \r\n binstub back
            # to \n on read, making an up-to-date file look "stale".
            with p.open(encoding="utf-8", newline="") as f:
                actual = f.read()
        except OSError:
            return True
        if actual != expected:
            return True
    return False


@dataclass(frozen=True)
class SelfInstallStatus:
    installed_version: str | None
    binstub: str | None
    root: str

    @property
    def installed(self) -> bool:
        return self.installed_version is not None and self.binstub is not None


def status(root: Path | None = None) -> SelfInstallStatus:
    r = root or default_root()
    stub = binstub_present()
    return SelfInstallStatus(
        installed_version=current_version(r),
        binstub=str(stub) if stub else None,
        root=str(r),
    )


def _core_install_satisfied(version: str, root: Path | None = None) -> bool:
    """Marker/slot/binstubs match ``version`` -- independent of the manifest."""
    r = root or default_root()
    return (
        current_version(r) == version
        and _slot_is_complete(version_slot(version, r))
        and binstub_present() is not None
        and not _binstubs_are_stale()
    )


def needs_install(version: str, root: Path | None = None) -> bool:
    r = root or default_root()
    return not (
        _core_install_satisfied(version, r)
        and not _control_plane_provider_manifest_is_stale(root=r)
    )


# ── binstub content (resolves the marker each run; matches ~/.local/bin) ─────

def _sh_binstub() -> str:
    return (
        "#!/usr/bin/env bash\n"
        "# worktree-manager binstub (versioned) — resolves the current-version marker.\n"
        'set -euo pipefail\n'
        'root="${WORKTREE_MANAGER_ROOT:-$HOME/.worktree-manager}"\n'
        'ver="$(cat "$root/current-version")"\n'
        'exec uv run --quiet --project "$root/versions/$ver" python -m worktree_manager "$@"\n'
    )


def _cmd_binstub() -> str:
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        'set "PYTHONUTF8=1"\r\n'
        'if "%WORKTREE_MANAGER_ROOT%"=="" set "WORKTREE_MANAGER_ROOT=%USERPROFILE%\\.worktree-manager"\r\n'
        'set /p VER=<"%WORKTREE_MANAGER_ROOT%\\current-version"\r\n'
        'uv run --quiet --project "%WORKTREE_MANAGER_ROOT%\\versions\\%VER%" '
        "python -m worktree_manager %*\r\n"
    )


def _ps1_binstub() -> str:
    return (
        "# worktree-manager binstub (versioned) — resolves the current-version marker.\n"
        "$env:PYTHONUTF8 = '1'\n"
        '$root = if ($env:WORKTREE_MANAGER_ROOT) { $env:WORKTREE_MANAGER_ROOT } '
        'else { Join-Path $env:USERPROFILE ".worktree-manager" }\n'
        '$ver = (Get-Content (Join-Path $root "current-version") -Raw).Trim()\n'
        '$slot = Join-Path (Join-Path $root "versions") $ver\n'
        "uv run --quiet --project $slot python -m worktree_manager @args\n"
    )


def _deploy_binstubs() -> list[Path]:
    lb = local_bin()
    lb.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, body in _expected_binstub_contents().items():
        p = lb / name
        p.write_text(body, encoding="utf-8", newline="")
        if os.name != "nt":
            p.chmod(0o755)
        written.append(p)
    return written


def _write_marker(root: Path, version: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / (MARKER + ".tmp")
    tmp.write_text(version, encoding="utf-8")
    tmp.replace(root / MARKER)  # atomic publish


def _materialize_payload_pointers(payload_dir: Path, slot: Path) -> None:
    """Expand any vendor-pointer lib copies under a freshly-copied
    ``slot/libs/*``, AND any `uv`-editable canonical-reference entry in
    ``slot/pyproject.toml`` (the mechanism that superseded the
    src-passthrough directory-pointer form -- vendor-pointer-generalization
    effort, Phase 1), into real, self-contained content -- a standalone
    self-installed slot (or a self-updated one fetched via git/tarball) has
    no ``libs/``+``plugins/`` monorepo ancestor of its own, so neither the
    src-passthrough pointer stub's runtime ``_find_repo_root`` walk NOR a
    `uv`-editable entry's escaping relative ``path`` can ever resolve
    there; every pointer/reference MUST be materialized into a real copy
    before (or as part of) publishing this slot, exactly the way a
    `main`-branch release already does.

    Uses ``_trusted_pointer_materializer`` -- a real, statically-shipped
    copy of ``tools/materialize_main.py``'s pointer-expansion core, NOT a
    dynamic load of the fetched ``tools/materialize_main.py`` itself.
    ``self_update`` supports user-configured forks/canary refs (see
    ``source_config.py``), so the fetched tree is untrusted input;
    dynamically loading and executing a file FROM it would hand a
    compromised or merely untrusted update source arbitrary code
    execution with the updater's own privileges. This module's code
    always comes from the already-installed, already-trusted running
    process -- only canonical file BYTES are ever read from the fetch.

    ``payload_dir`` is the SOURCE being copied (still-live at the moment
    this runs, whether a dev checkout or a freshly fetched self_update
    staging tree -- see ``self_update``'s git-clone/tarball paths, both of
    which now guarantee a ``libs/`` sibling next to the payload) -- also
    the correct base a `uv`-editable entry's relative ``path`` was
    authored against, since ``payload_dir`` and ``slot`` share the same
    nesting depth (a plain ``copytree``, not a re-rooted layout). Resolves
    canonical from ``payload_dir.parent`` -- if that ancestor lacks a real
    ``libs/`` (and thus can't provide canonical content), any pointer/
    reference found in the copied slot is an unresolvable, permanently-
    broken import for whoever runs it next, so this raises rather than
    silently shipping it.
    """
    from . import _trusted_pointer_materializer as materializer

    libs_dir = slot / "libs"
    # Discover pointer markers and uv-editable references FIRST, before
    # deciding whether canonical is even reachable -- a normal payload with
    # only real (non-pointer, non-escaping) dependencies has nothing to
    # materialize at all, so there's no reason to fail (or even inspect)
    # canonical reachability for it.
    unresolved = materializer.find_pointers_in_libs_dir(libs_dir) if libs_dir.is_dir() else []
    try:
        uv_refs = materializer.find_uv_editable_refs(payload_dir)
    except materializer.ManifestUnreadable as e:
        raise RuntimeError(f"cannot install this payload: {e}") from e
    if not unresolved and not uv_refs:
        return
    monorepo_root = payload_dir.parent
    has_canonical = (monorepo_root / "libs").is_dir()
    if not has_canonical:
        names = sorted(p.parent.name for p in unresolved) + sorted(
            lib for _name, _raw_path, lib, _editable in uv_refs
        )
        raise RuntimeError(
            f"cannot install this payload: libs/{{{', '.join(names)}}} are "
            "unmaterialized vendor pointers or uv-editable canonical "
            "references, but no monorepo ancestor (libs/) is reachable "
            "from the fetched payload to resolve canonical content from -- "
            "self_update's fetch must provide the full monorepo shape, not "
            "just the worktree-manager/ subtree"
        )
    try:
        log = materializer.materialize_libs_dir(libs_dir, canonical_root=monorepo_root) if unresolved else []
        log.extend(materializer.materialize_uv_editable_ref_into(
            source_consumer_dir=payload_dir, dest_consumer_dir=slot,
            canonical_root=monorepo_root, dest_root=slot,
        ) if uv_refs else [])
    except Exception as e:  # noqa: BLE001 -- normalize ANY materialization
        # failure (a malformed pointer's json.JSONDecodeError/KeyError, an
        # OSError from a copy/remove failure, ...) into the one exception
        # type self_install() knows to catch and translate to its
        # documented action="error" result -- letting an unexpected
        # exception type escape here would violate self_update's own
        # best-effort/non-fatal contract just as readily as a raw
        # RuntimeError would.
        raise RuntimeError(
            f"pointer materialization under {libs_dir} failed: {e}"
        ) from e
    failures = [line for line in log if line.startswith("SKIP ")]
    if failures:
        raise RuntimeError(
            "refusing to install this payload: pointer materialization "
            "was rejected for " + "; ".join(failures)
        )


def _find_any_symlink(tree: Path) -> Path | None:
    """The first path under (and including) ``tree`` that is a symlink, or
    ``None`` if none is found. Unlike the pointer-specific checks in
    ``_materialize_payload_pointers`` (which only ever examine canonical's
    ``src``/``tests`` and the pointer marker itself), this scans the WHOLE
    copied payload: a symlink anywhere else in it (unrelated to any vendor
    pointer) would still survive into the published slot untouched and
    could resolve outside it at runtime -- ``symlinks=True`` on the
    copytree calls preserves such a symlink faithfully rather than
    dereferencing it, but preservation alone doesn't make it SAFE to
    publish; this is the final blanket check before a slot goes live."""
    if tree.is_symlink():
        return tree
    if not tree.is_dir():
        return None
    for entry in sorted(tree.rglob("*")):
        if entry.is_symlink():
            return entry
    return None


class _CutoverLockBusy(Exception):
    """A genuinely concurrent cutover holds the mux-daemon lock right now."""


def _acquire_install_cutover_lease(root: Path):
    """The shared mux-daemon cutover lease, held for the WHOLE slot mutation
    below (reap + :func:`_copy_payload`).

    Raises :class:`_CutoverLockBusy` both when the lock is genuinely held by
    a live concurrent cutover elsewhere, AND when the cutover machinery
    itself fails to import: an import failure does not prove no older mux
    daemon or concurrent cutover exists, so this fails CLOSED (defers the
    install) rather than silently proceeding unprotected. Per
    ``docs/patterns/graceful-daemon-cutover.md``'s "serialize cutover
    attempts under one lease" rule, a caller that cannot establish the
    lease must defer rather than mutate the slot unprotected -- releasing
    the lease before ``_copy_payload`` runs (or skipping it on a busy
    timeout or missing dependency) would reopen the exact race this
    closes: a concurrent cutover's ``spawn_passive`` could stand a new
    passive up inside the very slot ``_copy_payload`` is about to
    ``rmtree``.
    """
    try:
        from . import mux_daemon_cutover
    except Exception as exc:  # surfaced to the caller as a defer, not swallowed
        raise _CutoverLockBusy(
            f"mux-daemon cutover machinery is unavailable for {root}: {exc}"
        ) from exc
    try:
        lease = mux_daemon_cutover._acquire_cutover_lock(root, timeout_s=5.0)
    except Exception as exc:  # surfaced to the caller as a defer, not swallowed
        raise _CutoverLockBusy(
            f"a concurrent mux-daemon cutover holds the lock for {root}: {exc}"
        ) from exc
    return lease, mux_daemon_cutover


def _reap_stranded_cutover_passive(root: Path, mux_daemon_cutover) -> None:
    """Best-effort: clear a passive mux-daemon stranded by an aborted cutover.

    Must be called ONLY while the caller already holds the cutover lease
    from :func:`_acquire_install_cutover_lease` for the same ``root`` --
    this performs no locking of its own.

    ``mux_daemon_cutover.spawn_passive`` spawns a new version's mux-daemon
    with its ``cwd`` pinned INSIDE the version slot being cut over to, so it
    can be health-checked before promotion. If the orchestrator driving that
    cutover (``self_update()`` -> ``activate_after_update()``) dies before
    the passive is ever promoted or terminated -- a crash, a killed
    terminal, an interrupted upgrade -- the passive lingers indefinitely,
    its open ``cwd`` handle preventing that slot from ever being deleted on
    Windows (``WinError 32``).

    ``activate_after_update()`` already reaps exactly this (via the durable
    cutover breadcrumb + :func:`mux_daemon_cutover._reap_abandoned_passive`)
    -- but only when IT runs. A bare ``self_install(dry_run=False)`` (the
    ``self-install`` CLI command, or any retry after a crashed self-update)
    calls :func:`_copy_payload` directly and never goes through that
    recovery, so a stranded passive pinned inside the slot about to be
    ``rmtree``'d is never cleared first, and the delete fails.

    This reuses the SAME breadcrumb-driven reap (never a new cwd-hunting
    mechanism -- that would need a new OS-specific dependency this
    deliberately dependency-free installer does not carry). Deliberately
    does NOT touch the breadcrumb file itself (no clearing, no rewriting):
    the SAME breadcrumb may still name an ``old`` endpoint that a later,
    full ``activate_after_update()`` -> ``recover_stale_cutover()`` needs to
    undrain -- clearing it here, even after a successful reap, would
    silently discard that other half of recovery. Leaving the file
    untouched is always safe: a later real cutover re-reads it and finds
    the reaped pid simply no longer alive (a clean no-op on its side).
    """
    try:
        from zdd import breadcrumb

        record = breadcrumb.read_breadcrumb(mux_daemon_cutover.routing_dir(root))
        mux_daemon_cutover._reap_abandoned_passive(root, record)
    except Exception:  # noqa: BLE001, S110 -- reap is best-effort, never fatal to install
        pass


def _copy_payload(payload_dir: Path, slot: Path) -> None:
    try:
        _copy_payload_unsafe(payload_dir, slot)
    except RuntimeError:
        # Already our one normalized, self_install()-caught boundary type --
        # re-raise as-is (don't re-wrap).
        raise
    except OSError as e:
        # shutil.rmtree(slot)/shutil.copytree() can themselves raise a bare
        # OSError -- most notably a Windows PermissionError (WinError 32)
        # when the slot directory is still held open by another process's
        # cwd (a stranded OR still-legitimately-running mux-daemon pinned
        # there -- see copilot-extensions#4999). self_install() only
        # catches RuntimeError at its boundary (matching
        # _materialize_payload_pointers' own boundary below), so any
        # filesystem failure here must be normalized to that type for
        # self_install()'s cleanup-and-report ("best-effort slot removal,
        # report action='error'") to run instead of this exception
        # escaping uncaught (``_slot_is_complete`` is what then lets a
        # later run detect and repair a slot that cleanup could not fully
        # remove).
        raise RuntimeError(f"copying payload into {slot} failed: {e}") from e


def _copy_payload_unsafe(payload_dir: Path, slot: Path) -> None:
    # Invalidate completion FIRST, before any mutation -- see
    # _invalidate_slot_completion's own docstring for why this must not be
    # left to the rmtree below (which can itself fail partway through).
    _invalidate_slot_completion(slot)
    if slot.exists():
        shutil.rmtree(slot)
    if payload_dir.is_symlink():
        # symlinks=True on the copytree below only protects symlinks
        # encountered DURING the walk of payload_dir's own tree -- it
        # cannot protect payload_dir being a symlink ITSELF (shutil.
        # copytree always creates dst as a real directory, so there's
        # nowhere for a preserved-root-symlink object to go). In the
        # git-backed self_update path, a checked-out worktree-manager/
        # dir could itself be a symlink; the earlier
        # (payload/"pyproject.toml").is_file() check would still pass
        # (it follows the link), copytree would dereference it, and
        # _find_any_symlink(slot) afterward would see no link at all
        # (the slot's own root is never included in its own scan).
        raise RuntimeError(
            f"refusing to install this payload: {payload_dir} is a "
            "symlink -- a payload root must be a real directory, not a "
            "link to an external tree"
        )
    ignore = shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.pyc")
    # symlinks=True: a payload staged by self_update's tarball fetch may
    # carry a preserved (not dereferenced -- see _fetch_via_tarball's own
    # symlinks=True) symlink; copying it here with the default
    # symlinks=False would dereference it at this second hop, still
    # smuggling external content into the published slot one step later
    # and defeating _materialize_payload_pointers' downstream symlink
    # rejection.
    shutil.copytree(payload_dir, slot, ignore=ignore, symlinks=True)
    bad = _find_any_symlink(slot)
    if bad is not None:
        shutil.rmtree(slot, ignore_errors=True)
        raise RuntimeError(
            f"refusing to install this payload: {bad} is a symlink -- a "
            "self-installed slot must contain only real files (a symlink "
            "anywhere in it could resolve outside the slot at runtime)"
        )
    _materialize_payload_pointers(payload_dir, slot)
    missing = [rel for rel in _SLOT_KEY_FILES if not (slot / rel).is_file()]
    if missing:
        raise RuntimeError(
            f"refusing to mark {slot} complete: payload is missing "
            + ", ".join(missing)
        )
    # Published only here, as the LAST step of a fully successful copy +
    # materialization that has itself verified every _SLOT_KEY_FILES entry
    # is present -- this is what proves the slot complete.
    _mark_slot_complete(slot)


# ── legacy artifact recognition + cleanup ────────────────────────────────
#
# Before this out-of-plugin, versioned Manager existed, an EARLIER and
# entirely unrelated "worktree-manager" plugin lived at
# ``plugins/worktree-manager/`` (2026-05-26) and shipped its own
# ``~/.local/bin/worktree-manager`` (+ ``.cmd``) binstub, plus a
# non-versioned ``~/.worktree-manager/.venv`` + ``~/.worktree-manager/lib``
# runtime layout. That plugin was renamed to ``agent-worktrees`` days later
# (commit 6512114be) -- but a machine that installed it *before* the rename
# can still carry those exact files, and the rename never cleaned them up.
# They collide on both the binstub name and the root directory this Manager
# now uses for its own versioned layout, and agent-worktrees' own
# ``--version`` health probe against that ancient stub is exactly what
# fails and falls back to the bundled picker.
#
# These are the byte-exact contents git history shows for that prototype
# (unchanged from its initial scaffold through the rename commit). Matching
# them exactly -- not just "the binstub looks wrong" -- lets cleanup
# positively attribute what it removes to this one known, historical
# artifact, rather than assuming any unrecognized content at that path is
# safe to delete.
_LEGACY_PRERENAME_SH = (
    "#!/usr/bin/env bash\n"
    "set -euo pipefail\n"
    "\n"
    'if [[ -z "${WORKTREE_PROJECT:-}" ]]; then\n'
    '    echo "ERROR: WORKTREE_PROJECT is not set. Use the project-specific '
    'binstub or export WORKTREE_PROJECT." >&2\n'
    "    exit 1\n"
    "fi\n"
    "\n"
    "# Dual-layout: prefer new runtime, fall back to legacy\n"
    'if [[ -x "$HOME/.worktree-manager/.venv/bin/python" ]]; then\n'
    '    PYTHON="$HOME/.worktree-manager/.venv/bin/python"\n'
    '    export PYTHONPATH="$HOME/.worktree-manager/lib"\n'
    "else\n"
    '    echo "ERROR: Venv not found. Run the installer first." >&2\n'
    "    exit 1\n"
    "fi\n"
    "\n"
    "unset PYTHONHOME\n"
    'exec "$PYTHON" -m worktree_manager "$@"\n'
)

_LEGACY_PRERENAME_CMD = (
    "@echo off\n"
    "setlocal\n"
    "\n"
    "if not defined WORKTREE_PROJECT (\n"
    "    echo ERROR: WORKTREE_PROJECT is not set. Use the project-specific "
    "binstub or set WORKTREE_PROJECT. >&2\n"
    "    exit /b 1\n"
    ")\n"
    "\n"
    "rem Resolve runtime\n"
    'set "NEW_RUNTIME=%USERPROFILE%\\.worktree-manager"\n'
    "\n"
    'if exist "%NEW_RUNTIME%\\.venv\\Scripts\\python.exe" (\n'
    '    set "PYTHON=%NEW_RUNTIME%\\.venv\\Scripts\\python.exe"\n'
    '    set "PYTHONPATH=%NEW_RUNTIME%\\lib"\n'
    ") else (\n"
    "    echo ERROR: Venv not found. Run the installer first. >&2\n"
    "    exit /b 1\n"
    ")\n"
    "\n"
    '"%PYTHON%" -m worktree_manager %*\n'
    "exit /b %ERRORLEVEL%\n"
)

_LEGACY_LABEL = (
    "pre-rename 'worktree-manager' plugin prototype, May 2026 -- "
    "before it became agent-worktrees"
)

_KNOWN_LEGACY_BINSTUB_SIGNATURES: dict[str, str] = {
    "worktree-manager": _LEGACY_PRERENAME_SH,
    "worktree-manager.cmd": _LEGACY_PRERENAME_CMD,
}

# The non-versioned runtime layout that same prototype kept directly under
# the shared ``~/.worktree-manager`` root -- orphaned relative to this
# Manager's own ``versions/`` + ``current-version`` layout, which never
# creates either of these.
_LEGACY_ROOT_DIRS = (".venv", "lib")


def _read_exact(p: Path) -> str | None:
    try:
        with p.open(encoding="utf-8", newline="") as f:
            return f.read()
    except OSError:
        return None


def _identify_legacy_binstub(name: str, text: str) -> str | None:
    """A human label when ``text`` exactly matches ``name``'s known legacy
    signature, else ``None`` -- never a fuzzy/generic "looks different" match."""
    expected = _KNOWN_LEGACY_BINSTUB_SIGNATURES.get(name)
    return _LEGACY_LABEL if expected is not None and text == expected else None


def plan_legacy_cleanup(root: Path | None = None) -> list[str]:
    """Report (never act on) recognized legacy artifacts a real run would remove."""
    r = root or default_root()
    lb = local_bin()
    findings: list[str] = []
    for name in _KNOWN_LEGACY_BINSTUB_SIGNATURES:
        text = _read_exact(lb / name)
        if text is not None and (label := _identify_legacy_binstub(name, text)):
            findings.append(f"legacy binstub {lb / name} ({label})")
    for name in _LEGACY_ROOT_DIRS:
        d = r / name
        if d.is_dir():
            findings.append(f"legacy root artifact {d} ({_LEGACY_LABEL})")
    return findings


def clean_legacy_artifacts(root: Path | None = None) -> list[str]:
    """Remove recognized legacy worktree-manager artifacts; return what was removed.

    Only acts on content positively identified via
    :data:`_KNOWN_LEGACY_BINSTUB_SIGNATURES` or the prototype's known root
    layout (:data:`_LEGACY_ROOT_DIRS`) -- never a generic "this looks wrong"
    guess, so an operator's own unrelated file at the same path is never
    touched.
    """
    r = root or default_root()
    lb = local_bin()
    cleaned: list[str] = []
    for name in _KNOWN_LEGACY_BINSTUB_SIGNATURES:
        p = lb / name
        text = _read_exact(p)
        if text is None:
            continue
        label = _identify_legacy_binstub(name, text)
        if not label:
            continue
        try:
            p.unlink()
        except OSError:
            continue
        cleaned.append(f"removed legacy binstub {p} ({label})")
    for name in _LEGACY_ROOT_DIRS:
        d = r / name
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            cleaned.append(f"removed legacy root artifact {d} ({_LEGACY_LABEL})")
    return cleaned


@dataclass(frozen=True)
class SelfInstallResult:
    version: str | None
    action: str          # "installed" | "already-current" | "planned" | "error"
    root: str
    slot: str | None = None
    marker: str | None = None
    binstubs: tuple[str, ...] = ()
    reason: str | None = None
    cleaned: tuple[str, ...] = ()


def self_install(
    payload_dir: Path | None = None,
    *,
    root: Path | None = None,
    dry_run: bool = True,
) -> SelfInstallResult:
    """Install the running (or given) payload into the versioned layout.

    Idempotent + version-gated: a no-op when the marker already names this
    version and its slot + binstub exist. Dry-run by default.
    """
    r = root or default_root()
    pd = payload_dir or running_payload_dir()
    version = payload_version(pd)
    if version is None:
        return SelfInstallResult(version=None, action="error", root=str(r),
                                 reason="could not read payload __version__")
    slot = version_slot(version, r)
    if dry_run:
        would_clean = tuple(plan_legacy_cleanup(r))
        if not needs_install(version, r):
            return SelfInstallResult(version=version, action="already-current", root=str(r),
                                     slot=str(slot), marker=version, cleaned=would_clean)
        return SelfInstallResult(version=version, action="planned", root=str(r),
                                 slot=str(slot), reason="dry-run", cleaned=would_clean)
    cleaned = tuple(clean_legacy_artifacts(r))
    if not needs_install(version, r):
        return SelfInstallResult(version=version, action="already-current", root=str(r),
                                 slot=str(slot), marker=version, cleaned=cleaned)
    if _core_install_satisfied(version, r):
        # Only the manifest is stale -- repair it directly rather than
        # falling through to the lease-guarded _copy_payload below, which
        # would pointlessly rmtree + recopy an already-good (possibly
        # in-use) version slot.
        _write_control_plane_provider_manifest(pd, root=r)
        return SelfInstallResult(
            version=version, action="installed", root=str(r), slot=str(slot),
            marker=version, cleaned=cleaned,
        )
    # Hold the cutover lease across BOTH the stale-passive reap AND
    # _copy_payload itself -- releasing it in between (or skipping it on a
    # busy timeout or missing dependency) would reopen the exact race this
    # closes: a concurrent cutover's spawn_passive could stand a new
    # passive up inside the very slot _copy_payload is about to rmtree. A
    # genuinely busy lock, or an unavailable cutover machinery, means this
    # cannot be proven safe; defer rather than mutate the slot unprotected
    # (docs/patterns/graceful-daemon-cutover.md).
    try:
        lease, mux_daemon_cutover = _acquire_install_cutover_lease(r)
    except _CutoverLockBusy as exc:
        return SelfInstallResult(version=version, action="error", root=str(r),
                                 slot=str(slot), reason=str(exc), cleaned=cleaned)
    try:
        # Re-check under the lease: a concurrent self_install()/self_update()
        # could have finished installing (and even activated) this EXACT
        # version while this call was waiting to acquire it -- without this
        # recheck, we would blindly rmtree + recopy a slot that is now the
        # live, already-active install, racing whatever is currently running
        # out of it. The lock-free check above only proves the version was
        # needed at that point in time, not that it still is now.
        if not needs_install(version, r):
            return SelfInstallResult(version=version, action="already-current", root=str(r),
                                     slot=str(slot), marker=version, cleaned=cleaned)
        _reap_stranded_cutover_passive(r, mux_daemon_cutover)
        try:
            _copy_payload(pd, slot)
        except RuntimeError as e:
            # _materialize_payload_pointers() raises when a vendor-pointer copy
            # inside the payload can't be resolved/expanded -- _copy_payload
            # has already copytree'd the payload into slot by that point, so a
            # bare re-raise would leave a partially-populated, broken slot on
            # disk that a later needs_install() version-existence check could
            # mistake for a valid install and skip retrying. Remove it so a
            # retry starts clean, and report the failure rather than crashing
            # the caller (self_update's own contract is best-effort/non-fatal).
            if slot.exists():
                shutil.rmtree(slot, ignore_errors=True)
            return SelfInstallResult(version=version, action="error", root=str(r),
                                     slot=str(slot), reason=str(e), cleaned=cleaned)
    finally:
        lease.release()
    stubs = _deploy_binstubs()
    _write_marker(r, version)  # publish last, so the marker only names a ready slot
    _write_control_plane_provider_manifest(pd, root=r)
    return SelfInstallResult(
        version=version, action="installed", root=str(r), slot=str(slot),
        marker=version, binstubs=tuple(str(s) for s in stubs), cleaned=cleaned,
    )


@dataclass(frozen=True)
class SelfUpdateResult:
    action: str          # "updated" | "already-current" | "skipped" | "error"
    version: str | None = None
    previous: str | None = None
    reason: str | None = None
    cutover: dict[str, object] | None = None


def _clear_dir(d: Path) -> None:
    """Empty a directory in place (keep the directory itself)."""
    if not d.exists():
        return
    for child in d.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except OSError:
                pass


def _safe_extract(tf, dest: Path) -> None:
    """Extract a tar into ``dest``, refusing anything that would escape it.

    A hand-rolled path-only pre-check computed against ``getmembers()``
    BEFORE any extraction happens cannot catch a classic tar symlink
    attack -- a symlink member (``evil -> /tmp/outside``) followed by a
    second member using it as a path prefix (``evil/payload.py``) --
    because at pre-check time neither path exists on disk yet, so a
    lexical ``.resolve()`` sees no symlink to follow and both members
    pass; only DURING a batch extractall's own sequential write does the
    second member actually traverse through the just-created symlink and
    land outside ``dest`` entirely.

    Fixed by extracting ONE member at a time, validating each immediately
    before extracting it (not the whole batch up front): by the time a
    later member (``evil/payload.py``) is checked, an earlier symlink
    member (``evil``) has ALREADY been written to disk in a prior
    iteration of this same loop, so ``.resolve()`` follows the REAL
    symlink now sitting there and correctly detects the escape --
    reproducing the safety property of stdlib's ``filter="data"``
    (available only since Python 3.12, backported to some but not all
    supported-version patch releases) without depending on it, since this
    project declares ``requires-python = \">=3.10\"`` and a raw
    ``filter=\"data\"`` call would raise ``TypeError`` on an
    unpatched 3.10/3.11 host, breaking the documented git-optional
    tarball fallback instead of updating."""
    import warnings

    dest = dest.resolve()
    for member in tf.getmembers():
        target = (dest / member.name).resolve()
        if target != dest and dest not in target.parents:
            raise OSError(f"unsafe path in tarball: {member.name}")
        # Hardlinks, device files, fifos, etc. are refused outright by the
        # member-type check below -- only a symlink's own target needs
        # validating here.
        if member.issym():
            if os.path.isabs(member.linkname):
                raise OSError(f"{member.name} is a link to an absolute path")
            # Resolve the link's OWN target relative to where it will
            # live (target.parent), the same way the filesystem would --
            # this is what actually catches the sequential-traversal
            # attack: a later member's own target path (checked above)
            # only escapes visibly once THIS check has forced the
            # symlink's resolved destination to be validated too.
            link_dest = (target.parent / member.linkname).resolve()
            if link_dest != dest and dest not in link_dest.parents:
                raise OSError(f"{member.name} is a link escaping the destination")
        if not (member.isreg() or member.isdir() or member.issym()):
            raise OSError(
                f"refusing to extract {member.name}: unsupported member type "
                "(only regular files, directories, and symlinks are allowed)"
            )
        with warnings.catch_warnings():
            # Every member here is already validated by hand above,
            # independent of Python's own filter= mechanism (unavailable
            # on this project's older supported Python versions) --
            # silence the resulting "no filter given" DeprecationWarning
            # rather than leaving noise in every real self-update run.
            warnings.simplefilter("ignore", DeprecationWarning)
            tf.extract(member, dest)  # noqa: S202 - each member validated immediately above


def _fetch_via_tarball(staging: Path, url: str, *, timeout: int = 180) -> None:
    """Fetch + extract the worktree-manager payload from a GitHub tarball (no git).

    Replaces ``staging`` contents with the extracted ``worktree-manager/`` payload
    PLUS its sibling ``libs/`` directory, so ``staging/worktree-manager/pyproject.toml``
    and ``staging/libs/`` both exist -- the same monorepo-shaped layout the git
    clone produces. ``libs/`` supplies canonical content for
    ``_materialize_payload_pointers`` to expand any vendor-pointer copy inside the
    payload -- expansion itself uses the LOCAL, already-trusted
    ``_trusted_pointer_materializer`` module (never the fetched ``tools/``, which
    this function deliberately does NOT fetch: ``self_update`` supports
    user-configured forks/canary refs, so executing anything from that untrusted
    tree would be a real supply-chain risk -- see
    ``_materialize_payload_pointers``'s own docstring). A tarball-only fetch that
    skipped the ``libs/`` sibling would leave those pointers permanently
    unresolvable in the installed slot.
    Raises ``OSError`` on any failure so the caller can degrade to an ``error`` result.
    """
    import tarfile
    import tempfile
    import urllib.request

    staging.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        archive = tdp / "payload.tar.gz"
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - https codeload
            archive.write_bytes(resp.read())
        extract = tdp / "x"
        extract.mkdir()
        with tarfile.open(archive, "r:gz") as tf:
            _safe_extract(tf, extract)
        # codeload extracts to a single <name>-<ref>/ top dir; find the payload.
        payload = None
        for rdir in (p for p in extract.iterdir() if p.is_dir()):
            if rdir.is_symlink():
                # rdir.is_dir() above already follows a symlink -- checking
                # only payload (rdir / "worktree-manager") afterward misses
                # THIS case: a symlinked top-level extraction dir whose own
                # "worktree-manager" subpath is a real (non-symlink) file
                # within the symlinked target, so payload.is_symlink() alone
                # would be False even though the whole tree was reached via
                # a symlinked parent.
                raise OSError(f"extracted tarball entry {rdir} is a symlink -- refusing")
            cand = rdir / "worktree-manager"
            if (cand / "pyproject.toml").is_file():
                payload = cand
                break
        if payload is None:
            raise OSError(f"worktree-manager payload not found in tarball from {url}")
        if payload.is_symlink():
            raise OSError(f"worktree-manager payload at {payload} is a symlink -- refusing")
        _clear_dir(staging)
        # symlinks=True on every copytree below: shutil.copytree's default
        # (symlinks=False) DEREFERENCES a symlink anywhere in the source
        # tree, silently copying whatever file it points to -- a malicious
        # or corrupted tarball could include a symlink entry pointing
        # outside the extracted archive, and a naive copytree would smuggle
        # that external content straight into staging before
        # materialize_main ever gets a chance to reject it. Preserving
        # symlinks as symlinks instead lets the existing canonical-symlink
        # validation (_find_symlink()) correctly detect and refuse them
        # downstream, the same as it already does for a real dev checkout.
        # NOTE: symlinks=True does NOT protect the copytree's own SOURCE
        # ROOT -- shutil.copytree always creates dst as a real directory,
        # so if the root argument itself were a symlink, os.scandir would
        # transparently follow it with nowhere for a preserved-symlink
        # object to even go. Each root (payload, libs_source) is
        # explicitly is_symlink()-checked before its own copy call.
        shutil.copytree(payload, staging / "worktree-manager", symlinks=True)
        libs_source = payload.parent / "libs"
        if libs_source.is_symlink():
            raise OSError(f"{libs_source} is a symlink -- refusing")
        if libs_source.is_dir():
            shutil.copytree(libs_source, staging / "libs", symlinks=True)


def self_update(
    *,
    ref: str | None = None,
    root: Path | None = None,
    dry_run: bool = False,
) -> SelfUpdateResult:
    """Fetch the latest Worktree Manager payload and version-install it.

    This is the "updater updates itself" step: it fetches the out-of-band
    ``worktree-manager`` payload (the same source as the bootstrap one-liners) and
    :func:`self_install`\\s it -- publishing a new ``versions/<ver>`` slot +
    ``current-version`` marker when the fetched payload is newer, a no-op when
    already current (version-gated).

    Fetch is **git-optional**: with git on PATH it ``git clone``/``fetch``es (which
    also enables a lightweight in-place refetch next run); **without git** it falls
    back to a GitHub codeload **tarball** download (:func:`manager_tarball_url` /
    :func:`_fetch_via_tarball`), so a machine that never installed git can still
    update. The tarball fallback needs a GitHub source; a non-GitHub remote (local
    path / enterprise) still requires git and returns ``skipped``.

    Deliberately **best-effort and non-fatal**: git/uv missing, offline, or a
    fetch error return an ``error``/``skipped`` result rather than raising, so a
    transient network problem never blocks the harness ``update`` this feeds. The
    currently-running process keeps running its existing code; the freshly
    installed slot takes effect on the *next* ``worktree-manager`` invocation
    (normal versioned-install semantics -- no in-process hot-swap).
    """
    r = root or default_root()
    ref = ref or manager_ref(r)
    repo = manager_repo(r)
    previous = current_version(r)

    staging = r / STAGING_DIR
    use_git = shutil.which("git") is not None
    try:
        staging.mkdir(parents=True, exist_ok=True)
        if use_git:
            if (staging / ".git").is_dir():
                subprocess.run(["git", "-C", str(staging), "fetch", "--depth", "1",
                                repo, ref], check=True, capture_output=True,
                               text=True, timeout=180)
                subprocess.run(["git", "-C", str(staging), "checkout", "-q",
                                "FETCH_HEAD"], check=True, capture_output=True,
                               text=True, timeout=60)
            else:
                # staging may hold a prior tarball payload (no .git); clear so the
                # clone lands in an empty dir.
                _clear_dir(staging)
                subprocess.run(["git", "clone", "--depth", "1", "--branch", ref,
                                repo, str(staging)], check=True,
                               capture_output=True, text=True, timeout=300)
        else:
            url = manager_tarball_url(r, ref)
            if url is None:
                return SelfUpdateResult(
                    action="skipped", previous=previous,
                    reason="git not found and source is not a GitHub repo "
                           "(cannot fetch a tarball) -- install git to update")
            _fetch_via_tarball(staging, url)
    except (OSError, subprocess.SubprocessError) as e:
        return SelfUpdateResult(action="error", previous=previous,
                                reason=f"fetch failed: {e}")

    payload = staging / "worktree-manager"
    if not (payload / "pyproject.toml").is_file():
        return SelfUpdateResult(action="error", previous=previous,
                                reason=f"fetched payload not found at {payload}")

    res = self_install(payload_dir=payload, root=r, dry_run=dry_run)
    if res.action == "error":
        return SelfUpdateResult(action="error", version=res.version,
                                previous=previous, reason=res.reason)
    cutover = None
    if not dry_run and res.version is not None:
        try:
            from . import mux_daemon_cutover

            cutover = mux_daemon_cutover.activate_after_update(
                root=r,
                slot=version_slot(res.version, r),
                version=res.version,
            )
        except Exception as error:  # noqa: BLE001 -- best-effort like fetch/install
            cutover = {"action": "error", "reason": f"mux cutover failed: {error}"}
    if res.action == "already-current":
        return SelfUpdateResult(
            action="already-current",
            version=res.version,
            previous=previous,
            cutover=cutover,
        )
    # installed | planned (dry-run)
    return SelfUpdateResult(
        action="updated",
        version=res.version,
        previous=previous,
        cutover=cutover,
    )
