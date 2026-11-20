"""Passive file-version admission for new Windows PSMux sessions."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

MINIMUM_VERSION = (3, 4, 0, 0)


def file_version(path: str) -> tuple[int, int, int, int] | None:
    import ctypes
    from ctypes import wintypes

    try:
        api = getattr(ctypes, "WinDLL")("version", use_last_error=True)
        api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
        api.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        api.GetFileVersionInfoW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        ]
        api.GetFileVersionInfoW.restype = wintypes.BOOL
        api.VerQueryValueW.argtypes = [
            ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.UINT),
        ]
        api.VerQueryValueW.restype = wintypes.BOOL
        size = api.GetFileVersionInfoSizeW(path, None)
        if not 0 < size <= 1024 * 1024:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not api.GetFileVersionInfoW(path, 0, size, buffer):
            return None
        address = ctypes.c_void_p()
        length = wintypes.UINT()
        if (
            not api.VerQueryValueW(buffer, "\\", ctypes.byref(address), ctypes.byref(length))
            or not address.value or length.value < 13 * ctypes.sizeof(ctypes.c_uint32)
        ):
            return None
        fixed = ctypes.cast(address, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
        if fixed[0] != 0xFEEF04BD:
            return None
        major_minor, build_revision = int(fixed[2]), int(fixed[3])
        return (
            major_minor >> 16, major_minor & 0xFFFF,
            build_revision >> 16, build_revision & 0xFFFF,
        )
    except (AttributeError, OSError, ValueError):
        return None


def side_by_side_root() -> str:
    """Return the agent-worktrees-owned root for versioned PSMux copies."""
    import os

    return os.path.join(os.path.expanduser("~"), ".agent-worktrees", "psmux")


def default_search_roots() -> list[str]:
    """Directories that can hold an installed PSMux beside the PATH copy.

    Each root is searched for ``psmux.exe`` directly and one level below it:
    the agent-worktrees side-by-side root holds ``<version>\\psmux.exe``, and
    each WinGet portable package directory holds ``psmux.exe``.
    """
    import glob
    import os

    roots = [side_by_side_root()]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.extend(sorted(glob.glob(
            os.path.join(local, "Microsoft", "WinGet", "Packages", "marlocarlo.psmux_*")
        )))
    return roots


def _is_install_staging(name: str) -> bool:
    # The installer copies into ".staging-*" or "<version>.partial-*" and then
    # renames the directory; an incomplete copy must never be selected.
    return name.startswith(".") or ".partial-" in name


def compatible_candidates(
    search_roots: "Iterable[str]",
) -> list[tuple[tuple[int, int, int, int], str]]:
    """Return gate-compatible ``(version, path)`` candidates, newest first."""
    import os

    found: dict[str, tuple[int, int, int, int]] = {}
    for root in search_roots:
        if not root or not os.path.isdir(root):
            continue
        paths = [os.path.join(root, "psmux.exe")]
        try:
            paths.extend(
                os.path.join(entry.path, "psmux.exe")
                for entry in os.scandir(root)
                if entry.is_dir() and not _is_install_staging(entry.name)
            )
        except OSError:
            pass
        for path in paths:
            if not os.path.isfile(path):
                continue
            key = os.path.normcase(os.path.abspath(path))
            if key in found:
                continue
            version = file_version(path)
            if version is not None and version >= MINIMUM_VERSION:
                found[key] = version
    ordered = sorted(((version, path) for path, version in found.items()), reverse=True)
    return [(version, os.path.abspath(path)) for version, path in ordered]


def require_creation_binary(
    binary: str, *, search_roots: "Iterable[str] | None" = None,
) -> str:
    """Select a PSMux binary that may create a new session.

    The PATH resolution wins when it passes the version floor. Otherwise the
    newest compatible side-by-side or WinGet package copy is selected, so an
    older provider that still serves live sessions does not block creation.
    No provider process is started; older servers stay usable for observation.
    """
    import shutil

    selected = shutil.which(binary)
    version = file_version(selected) if selected else None
    if selected is not None and version is not None and version >= MINIMUM_VERSION:
        return selected
    roots = default_search_roots() if search_roots is None else search_roots
    candidates = compatible_candidates(roots)
    if candidates:
        return candidates[0][1]
    found = ".".join(map(str, version)) if version else "unknown"
    raise RuntimeError(
        "New PSMux sessions require version 3.4.0 or newer with readable file metadata "
        f"(PATH psmux: {selected or 'not found'}, version {found}); existing sessions "
        "were not changed. Run the agent-worktrees update to install PSMux 3.4.0 side by "
        "side under %USERPROFILE%\\.agent-worktrees\\psmux\\<version> without stopping "
        "live sessions."
    )
