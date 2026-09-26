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

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import worktree_manager

MARKER = "current-version"
VERSIONS_DIR = "versions"
STAGING_DIR = "staging"
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


def _binstub_files() -> list[str]:
    if os.name == "nt":
        return ["worktree-manager.cmd", "worktree-manager.ps1", "worktree-manager"]
    return ["worktree-manager"]


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


def needs_install(version: str, root: Path | None = None) -> bool:
    r = root or default_root()
    return not (
        current_version(r) == version
        and version_slot(version, r).is_dir()
        and binstub_present() is not None
        and not _binstubs_are_stale()
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
        'if "%WORKTREE_MANAGER_ROOT%"=="" set "WORKTREE_MANAGER_ROOT=%USERPROFILE%\\.worktree-manager"\r\n'
        'set /p VER=<"%WORKTREE_MANAGER_ROOT%\\current-version"\r\n'
        'uv run --quiet --project "%WORKTREE_MANAGER_ROOT%\\versions\\%VER%" '
        "python -m worktree_manager %*\r\n"
    )


def _ps1_binstub() -> str:
    return (
        "# worktree-manager binstub (versioned) — resolves the current-version marker.\n"
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


def _load_materialize_main(monorepo_root: Path):
    """Dynamically load ``tools/materialize_main.py`` from a live monorepo
    checkout, without ever making it a real import-time dependency of this
    dependency-free out-of-plugin payload (see this module's own docstring)
    -- only reachable, and only ever called, when a monorepo ancestor is
    actually present (see ``_materialize_payload_pointers``)."""
    import importlib.util

    path = monorepo_root / "tools" / "materialize_main.py"
    spec = importlib.util.spec_from_file_location("materialize_main", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _materialize_payload_pointers(payload_dir: Path, slot: Path) -> None:
    """Expand any vendor-pointer lib copies under a freshly-copied
    ``slot/libs/*`` into real, self-contained content -- a standalone
    self-installed slot (or a self-updated one fetched via git/tarball) has
    no ``libs/``+``plugins/`` monorepo ancestor of its own, so the
    src-passthrough pointer stub's runtime ``_find_repo_root`` walk can
    never find canonical there; every pointer copy MUST be materialized
    into a real copy before (or as part of) publishing this slot, exactly
    the way a `main`-branch release already does.

    ``payload_dir`` is the SOURCE being copied (still-live at the moment
    this runs, whether a dev checkout or a freshly fetched self_update
    staging tree -- see ``self_update``'s git-clone/tarball paths, both of
    which now guarantee a ``libs/`` sibling next to the payload). Resolves
    canonical from ``payload_dir.parent`` -- if that ancestor lacks a real
    ``libs/`` (and thus can't provide canonical content), any pointer found
    in the copied slot is an unresolvable, permanently-broken import for
    whoever runs it next, so this raises rather than silently shipping it.
    """
    libs_dir = slot / "libs"
    if not libs_dir.is_dir():
        return
    monorepo_root = payload_dir.parent
    has_canonical = (monorepo_root / "libs").is_dir()
    has_tool = (monorepo_root / "tools" / "materialize_main.py").is_file()
    if not (has_canonical and has_tool):
        materialize_main = None
    else:
        materialize_main = _load_materialize_main(monorepo_root)
    unresolved = materialize_main.find_pointers_in_libs_dir(libs_dir) if materialize_main else \
        [p for p in libs_dir.glob("*/VENDOR_POINTER.json")]
    if not unresolved:
        return
    if materialize_main is None:
        names = ", ".join(sorted(p.parent.name for p in unresolved))
        raise RuntimeError(
            f"cannot install this payload: libs/{{{names}}} are unmaterialized "
            "vendor pointers, but no monorepo ancestor (libs/ + "
            "tools/materialize_main.py) is reachable from the fetched "
            "payload to resolve canonical content from -- self_update's "
            "fetch must provide the full monorepo shape, not just the "
            "worktree-manager/ subtree"
        )
    try:
        log = materialize_main.materialize_libs_dir(libs_dir, canonical_root=monorepo_root)
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


def _copy_payload(payload_dir: Path, slot: Path) -> None:
    if slot.exists():
        shutil.rmtree(slot)
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
    stubs = _deploy_binstubs()
    _write_marker(r, version)  # publish last, so the marker only names a ready slot
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

    Uses the stdlib's own ``filter="data"`` (available since Python 3.12,
    backported as a security fix to older supported versions too) rather
    than a hand-rolled path-only pre-check: a pre-check computed against
    ``getmembers()`` BEFORE any extraction happens cannot catch a classic
    tar symlink attack -- a symlink member (``evil -> /tmp/outside``)
    followed by a second member using it as a path prefix
    (``evil/payload.py``) -- because at pre-check time neither path exists
    on disk yet, so a lexical ``.resolve()`` sees no symlink to follow and
    both members pass; only DURING extractall's own sequential member-by-
    member write does the second member actually traverse through the
    just-created symlink and land outside ``dest`` entirely.
    ``filter="data"`` is stdlib's purpose-built, security-reviewed defense
    against exactly this: it validates each member (including a symlink's
    resolved destination) against ``dest`` live, as extraction proceeds,
    rather than trusting a snapshot taken before anything was written."""
    import tarfile

    dest = dest.resolve()
    try:
        tf.extractall(dest, filter="data")  # noqa: S202 - filter="data" validates live
    except tarfile.TarError as e:
        # filter="data" raises tarfile.FilterError (a TarError, not an
        # OSError) on a rejected member -- but this function's caller
        # contract (and _fetch_via_tarball's own documented "raises
        # OSError on any failure") is OSError-only, so normalize here
        # rather than letting a different exception type escape past
        # self_update's own OSError/SubprocessError catch.
        raise OSError(f"refused while extracting tarball: {e}") from e


def _fetch_via_tarball(staging: Path, url: str, *, timeout: int = 180) -> None:
    """Fetch + extract the worktree-manager payload from a GitHub tarball (no git).

    Replaces ``staging`` contents with the extracted ``worktree-manager/`` payload
    PLUS its sibling ``libs/`` directory and ``tools/materialize_main.py``, so
    ``staging/worktree-manager/pyproject.toml``, ``staging/libs/``, and
    ``staging/tools/materialize_main.py`` all exist -- the same monorepo-shaped
    layout the git clone produces. Both siblings are required for
    ``_materialize_payload_pointers`` to expand any vendor-pointer copy inside the
    payload (``libs/`` supplies canonical content, ``tools/materialize_main.py`` is
    dynamically loaded to do the expansion -- see that function's own docstring);
    a tarball-only fetch that skipped either would leave those pointers permanently
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
        # object to even go. Each root (payload, libs_source, tool_source)
        # is explicitly is_symlink()-checked before its own copy call.
        shutil.copytree(payload, staging / "worktree-manager", symlinks=True)
        libs_source = payload.parent / "libs"
        if libs_source.is_symlink():
            raise OSError(f"{libs_source} is a symlink -- refusing")
        if libs_source.is_dir():
            shutil.copytree(libs_source, staging / "libs", symlinks=True)
        # _materialize_payload_pointers() also needs tools/materialize_main.py
        # (a monorepo ancestor sibling, dynamically loaded -- see
        # _load_materialize_main()) to expand any pointer copy inside
        # libs/ before the standalone slot is published; a tarball fetch
        # that copied libs/ but not this file would still hit the
        # unresolved-pointer refusal.
        tool_source = payload.parent / "tools" / "materialize_main.py"
        tools_dir = payload.parent / "tools"
        if tools_dir.is_symlink():
            # tool_source.is_symlink() alone misses this: if `tools/` itself
            # is a symlink to another directory that happens to contain a
            # real (non-symlink) materialize_main.py, tool_source itself
            # would be a real file within that symlinked-to target -- the
            # parent must be checked too, the same class of gap as
            # payload/libs_source above.
            raise OSError(f"{tools_dir} is a symlink -- refusing")
        if tool_source.is_symlink():
            raise OSError(f"{tool_source} is a symlink -- refusing")
        if tool_source.is_file():
            tool_dest = staging / "tools"
            tool_dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(tool_source, tool_dest / "materialize_main.py")


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
    if res.action == "already-current":
        return SelfUpdateResult(action="already-current", version=res.version,
                                previous=previous)
    # installed | planned (dry-run)
    return SelfUpdateResult(action="updated", version=res.version, previous=previous)
