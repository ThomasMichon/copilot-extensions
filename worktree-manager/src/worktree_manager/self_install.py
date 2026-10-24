"""Versioned self-install for the Worktree Manager (matches the harness convention).

The Worktree Manager is delivered out-of-plugin, but its install flow follows the
**same versioning convention as the harness's other installers** (see
``plugins/agent-worktrees/scripts/versioned_runtime.py``):

* an immutable per-version slot at ``<root>/versions/<version>/``,
* a plain-text ``<root>/current-version`` **marker file** naming the active
  version (written atomically: temp + rename), and
* a **binstub** in ``~/.local/bin/`` (``worktree-manager`` + ``.cmd``/``.ps1`` on
  Windows) that resolves the marker and runs the active slot.

The standalone payload ships canonical installer-engine/versioned-runtime
helpers independently of any plugin. Its adapter constructs and health-gates
an installed candidate before publication; legacy or damaged selected slots
require distinct repair generations rather than in-place rebuilding.
"""

from __future__ import annotations

import json
import importlib.util
import os
import re
import shutil
import subprocess
import sys
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
    package = Path(worktree_manager.__file__).resolve()
    retained = Path(sys.prefix) / "payload"
    if package.is_relative_to(Path(sys.prefix).resolve()) and (retained / "pyproject.toml").is_file():
        return retained
    return package.parents[2]


def _runtime_installer():
    script = running_payload_dir() / "scripts" / "runtime_install.py"
    name = "_manager_runtime_install"
    existing = sys.modules.get(name)
    if existing is not None and Path(existing.__file__).resolve() == script.resolve():
        return existing
    spec = importlib.util.spec_from_file_location(name, script)
    if spec is None or spec.loader is None:
        raise RuntimeError("Manager canonical installer adapter is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


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


def _mark_slot_complete(slot: Path) -> None:
    (slot / _SLOT_COMPLETE_MARKER).write_text("", encoding="utf-8")


def _slot_is_complete(slot: Path) -> bool:
    """Check canonical completion, interpreter and retained payload availability.

    Payload assembly alone never establishes runtime readiness. Apply/reuse
    additionally runs the canonical installed-origin and CLI health probe under
    candidate custody; this read-only status check does not reconstruct a slot.
    """
    payload = slot / "payload"
    try:
        runtime = _runtime_installer().runtime
        return (
            runtime.is_complete(slot.parent.parent, slot.name)
            and runtime.slot_python(slot.parent.parent, slot.name) is not None
            and all((payload / rel).is_file() for rel in _SLOT_KEY_FILES)
        )
    except (OSError, ValueError, ImportError, RuntimeError):
        return False


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
    template["resident_picker"] = {
        "version": 1, "command": [*template["command"], "--resident-picker"],
        "session": {"version": 1, "root": str((root or default_root()).resolve())},
    }
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
        if self.installed_version is None:
            return False
        root = Path(self.root)
        version = payload_version(version_slot(self.installed_version, root) / "payload")
        return version is not None and _core_install_satisfied(version, root)


def status(root: Path | None = None) -> SelfInstallStatus:
    r = root or default_root()
    stub = binstub_present()
    return SelfInstallStatus(
        installed_version=current_version(r),
        binstub=str(stub) if stub else None,
        root=str(r),
    )


def _selected_runtime_slot(root: Path) -> Path | None:
    selected = current_version(root)
    if not selected:
        return None
    try:
        _runtime_installer().runtime._validate_dev_previous_version(selected)
    except (OSError, ImportError, RuntimeError, ValueError):
        return None
    return version_slot(selected, root)


def _core_install_satisfied(version: str, root: Path | None = None) -> bool:
    """A valid selection, slot and binstubs match the requested package."""
    r = root or default_root()
    slot = _selected_runtime_slot(r)
    return (
        slot is not None
        and payload_version(slot / "payload") == version
        and _slot_is_complete(slot)
        and binstub_present() is not None
        and not _binstubs_are_stale()
    )


def _core_install_satisfied_gaps(version: str, root: Path | None = None) -> list[str]:
    """Human-readable reasons :func:`_core_install_satisfied` returned
    ``False`` for ``version`` -- one entry per failing sub-check.

    A caller falling through to a full reinstall on a ``False`` result must
    never do so silently (#5219's requested fix 2): this is what let a
    manifest-only gap silently escalate to "full reinstall" in practice,
    even while ``doctor`` was reporting the very same install as fully
    healthy (``doctor`` only compares the marker to the running version --
    it does not check slot completeness or binstub staleness at all).
    """
    r = root or default_root()
    gaps: list[str] = []
    cur = current_version(r)
    valid_selection = _selected_runtime_slot(r)
    selected = valid_selection or version_slot(version, r)
    matching_payload = payload_version(selected / "payload") == version
    if valid_selection is None:
        gaps.append(f"current-version marker is missing, empty or invalid: {cur!r}")
    elif cur != version and not matching_payload:
        gaps.append(f"current-version marker is {cur!r}, not {version!r}")
    target = selected if matching_payload else version_slot(version, r)
    if not _slot_is_complete(target):
        gaps.append(
            f"slot {target} is missing its completion marker "
            "or a key file"
        )
    stub = binstub_present()
    if stub is None:
        gaps.append("no binstub found in ~/.local/bin")
    elif _binstubs_are_stale():
        gaps.append(f"binstub {stub} content does not match this version's template")
    return gaps


def _currently_running_from(slot: Path) -> bool:
    """``True`` when *this process's own interpreter* lives inside ``slot``.

    Checked by path containment against the running interpreter
    (``sys.executable``), never by a version-string comparison -- the whole
    point is to catch this even when :func:`_core_install_satisfied`
    incorrectly disagrees that the version matches (#5219). Overwriting
    such a slot is a **self-overwrite**: on Windows, ``shutil.rmtree``
    cannot delete the currently-open venv's ``python.exe`` (``WinError 5:
    Access is denied``), and even where deletion nominally succeeds on
    another platform, destroying your own on-disk payload mid-execution is
    never something a reinstall needs to do -- the process already proves
    this exact version runs.
    """
    try:
        exe = Path(sys.executable).resolve()
    except OSError:
        return False
    try:
        slot_resolved = slot.resolve()
    except OSError:
        slot_resolved = slot
    return slot_resolved == exe or slot_resolved in exe.parents


def needs_install(version: str, root: Path | None = None) -> bool:
    r = root or default_root()
    return not (
        _core_install_satisfied(version, r)
        and not _control_plane_provider_manifest_is_stale(root=r)
    )


# Preserve existing test/caller seams while native rendering has its own owner.
from .runtime_launchers import _cmd_binstub as _cmd_binstub
from .runtime_launchers import _ps1_binstub as _ps1_binstub
from .runtime_launchers import _sh_binstub as _sh_binstub


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


# Payload build/validate/atomic-publish -- see self_install_payload.py's own
# module docstring for why this is a separate module (purely a line-count
# split; no behavior change). _copy_payload is imported here so the one
# existing call site below (and tests that
# `monkeypatch.setattr(si, "_copy_payload", ...)`) keep working unchanged
# against this module's own namespace. Its own internal helpers
# (_copy_payload_unsafe, _materialize_payload_pointers, _find_any_symlink,
# _swap_in_staged_slot) have no other caller in this module and are not
# re-exported here -- reach them via `self_install_payload` directly if ever
# needed.
from .self_install_payload import _copy_payload


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


from .self_install_recovery import reap_stranded_cutover_passive as _reap_stranded_cutover_passive  # noqa: F401 -- compatibility test seam


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


def _complete_candidate(
    ready, payload: Path, root: Path, *, previous: str | None,
    was_current: bool, before_publish=None, after_activate=None,
    transactional_activation: bool = False,
) -> SelfInstallResult:
    slot = ready.slot
    version = payload_version(payload)
    if before_publish is not None:
        before_publish()
    cleaned = tuple(clean_legacy_artifacts(root))
    stubs = _deploy_binstubs()
    _write_control_plane_provider_manifest(payload, root=root)
    installer = _runtime_installer()
    if transactional_activation:
        installer.activate(
            root, slot.name, expected=previous,
            after_publish=(lambda: after_activate(slot)) if after_activate is not None else None,
        )
    else:
        installer.activate(root, slot.name, expected=previous)
    if after_activate is not None and not transactional_activation:
        after_activate(slot)
    return SelfInstallResult(
        version=version, action="already-current"
        if was_current and ready.reused and previous == slot.name else "installed",
        root=str(root), slot=str(slot), marker=slot.name,
        binstubs=tuple(str(stub) for stub in stubs), cleaned=cleaned,
    )


def self_install(
    payload_dir: Path | None = None,
    *,
    root: Path | None = None,
    dry_run: bool = True,
    _after_activate=None,
    _before_publish=None,
    _transactional_activation: bool = False,
) -> SelfInstallResult:
    """Construct and health-gate with the canonical engine before selection."""
    r = (root or default_root()).resolve()
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
    previous = current_version(r)
    was_current = not needs_install(version, r)
    try:
        installer = _runtime_installer()
        with installer.candidate(pd, r, assemble=_copy_payload) as ready:
            slot = ready.slot
            return _complete_candidate(
                ready, pd, r, previous=previous, was_current=was_current,
                before_publish=_before_publish, after_activate=_after_activate,
                transactional_activation=_transactional_activation,
            )
    except (OSError, ImportError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        return SelfInstallResult(
            version=version, action="error", root=str(r), slot=str(slot), reason=str(error),
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
    """Fetch and version-install the Manager through its existing source authority.

    A newer payload publishes a new slot and marker; a matching version is a no-op.
    Fetch uses Git when available, enabling a lightweight in-place refetch; otherwise it falls
    back to a GitHub codeload tarball (:func:`manager_tarball_url`), so a bare machine can still
    update. The tarball fallback needs a GitHub source; a non-GitHub remote (local
    path / enterprise) still requires git and returns ``skipped``.

    Deliberately **best-effort and non-fatal**: git/uv missing, offline, or a
    fetch error return an ``error``/``skipped`` result rather than raising, so a
    transient network problem never blocks the harness ``update`` this feeds. The
    currently-running process keeps running its existing code; the freshly
    installed slot takes effect on the *next* ``worktree-manager`` invocation
    (normal versioned-install semantics -- no in-process hot-swap).
    """
    r = (root or default_root()).resolve()
    ref = ref or manager_ref(r)
    repo = manager_repo(r)
    previous = current_version(r)

    staging = r / STAGING_DIR
    use_git = shutil.which("git") is not None
    try:
        from . import engine_client, source_config, update_auth

        repo = source_config.absolute_local_source(repo)
        staging.mkdir(parents=True, exist_ok=True)
        auth_env = update_auth.environment(repo, cwd=staging) if use_git else None
        environment = {"env": auth_env} if auth_env is not None else {}
        if use_git:
            if (staging / ".git").is_dir():
                subprocess.run(["git", "-C", str(staging), "fetch", "--depth", "1",
                                repo, ref], check=True, capture_output=True,
                               text=True, timeout=180, **environment)
                subprocess.run(["git", "-C", str(staging), "checkout", "-q",
                                "FETCH_HEAD"], check=True, capture_output=True,
                               text=True, timeout=60, **environment)
            else:
                # staging may hold a prior tarball payload (no .git); clear so the
                # clone lands in an empty dir.
                _clear_dir(staging)
                subprocess.run(["git", "clone", "--depth", "1", "--branch", ref,
                                repo, str(staging)], check=True,
                               capture_output=True, text=True, timeout=300, cwd=staging, **environment)
        else:
            url = manager_tarball_url(r, ref)
            if url is None:
                return SelfUpdateResult(
                    action="skipped", previous=previous,
                    reason="git not found and source is not a GitHub repo "
                           "(cannot fetch a tarball) -- install git to update")
            _fetch_via_tarball(staging, url)
    except update_auth.AuthRequired as e:
        return SelfUpdateResult(action="auth_required", previous=previous, reason=str(e))
    except (OSError, subprocess.SubprocessError, engine_client.EngineError) as e:
        return SelfUpdateResult(action="error", previous=previous,
                                reason=f"fetch failed: {e}")

    payload = staging / "worktree-manager"
    if not (payload / "pyproject.toml").is_file():
        return SelfUpdateResult(action="error", previous=previous,
                                reason=f"fetched payload not found at {payload}")

    cutover = None

    def after_activate(slot: Path) -> None:
        nonlocal cutover
        try:
            from . import mux_daemon_cutover

            cutover = mux_daemon_cutover.activate_after_update(
                root=r, slot=slot, version=slot.name,
            )
        except Exception as error:  # noqa: BLE001 -- existing best-effort cutover boundary
            cutover = {"action": "error", "reason": f"mux cutover failed: {error}"}

    res = self_install(
        payload_dir=payload, root=r, dry_run=dry_run,
        _after_activate=after_activate if not dry_run else None,
    )
    if res.action == "error":
        return SelfUpdateResult(action="error", version=res.version,
                                previous=previous, reason=res.reason)
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
