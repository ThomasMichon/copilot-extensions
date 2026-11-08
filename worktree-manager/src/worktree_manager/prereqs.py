"""Prerequisite detection (Phase 2 — provisioning + core install).

Programmatic, non-agentic detection of the baseline prerequisites the harness
needs before any plugin works (git, Python, uv, a terminal multiplexer). Pure
inspection — nothing here mutates the machine; provisioning lives in
:mod:`provision` and the core install in :mod:`core_install`.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from contextlib import nullcontext
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_procutil import no_window_kwargs

from .catalog import Prereq, load_catalog


def current_os() -> str:
    """Normalized OS token: "windows" | "macos" | "linux" | "unknown"."""
    sysname = platform.system().lower()
    if sysname.startswith("win"):
        return "windows"
    if sysname == "darwin":
        return "macos"
    if sysname == "linux":
        return "linux"
    return "unknown"


# Some tools are invoked under a different executable name than their catalog id.
_EXE_ALIASES = {
    "python3": ("python3", "python"),
    "psmux": ("psmux",),
}

_VERSION_RE = re.compile(r"(\d+(?:\.\d+){1,3})")


@dataclass(frozen=True)
class PrereqStatus:
    """Detected state of one prerequisite."""

    name: str
    present: bool
    path: str | None = None
    version: str | None = None
    #: The required minimum from the catalog, if any.
    min_required: str | None = None
    optional: bool = False
    notes: str | None = None

    @property
    def satisfied(self) -> bool:
        """Presence/version readiness; executable hosts must prove their floor."""
        if not self.present:
            return self.optional
        if self.name in {"python3", "pwsh"} and self.min_required and not self.version:
            return False
        if self.min_required and self.version:
            return _ge(self.version, self.min_required)
        return True


def _ge(have: str, want: str) -> bool:
    def parts(v: str) -> tuple[int, ...]:
        m = _VERSION_RE.search(v)
        return tuple(int(x) for x in m.group(1).split(".")) if m else ()
    h, w = parts(have), parts(want)
    if not h or not w:
        return True  # can't compare — don't block
    n = max(len(h), len(w))
    return h + (0,) * (n - len(h)) >= w + (0,) * (n - len(w))


def _which(name: str) -> tuple[str, str | None]:
    for exe in _EXE_ALIASES.get(name, (name,)):
        found = shutil.which(exe)
        if found:
            return exe, found
    return name, None


def _python_candidates(first: str | None) -> Iterator[str]:
    if first:
        yield first
    for exe in _EXE_ALIASES["python3"]:
        suffixes = ("",)
        if os.name == "nt":
            # Python 3.11 does not apply PATHEXT to directory-qualified commands.
            extensions = tuple(
                ext for ext in (os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD").split(os.pathsep)
                if ext
            )
            if extensions and not any(exe.lower().endswith(ext.lower()) for ext in extensions):
                suffixes = extensions
        for directory in os.get_exec_path():
            for suffix in suffixes:
                candidate = os.path.abspath(os.path.join(directory or os.curdir, exe + suffix))
                found = shutil.which(candidate, path=directory or os.curdir)
                if found:
                    yield found


def _probe_version(exe: str) -> str | None:
    powershell = Path(exe).stem.lower() == "pwsh"
    context = TemporaryDirectory(prefix="manager-pwsh-probe-") if powershell else nullcontext(None)
    with context as profile:
        environment = None
        prefix = []
        flags = ("--version", "-V", "version")
        if profile is not None:
            # Even --version initializes PowerShell's user configuration directory.
            environment = {
                **os.environ,
                **{name: profile for name in (
                    "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                    "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
                )},
                "POWERSHELL_TELEMETRY_OPTOUT": "1",
                "POWERSHELL_UPDATECHECK": "Off",
            }
            prefix = ["-NoLogo", "-NoProfile", "-NonInteractive"]
            flags = ("--version",)
        for flag in flags:
            try:
                r = subprocess.run(
                    [exe, *prefix, flag], capture_output=True, text=True, timeout=5,
                    env=environment, **no_window_kwargs(),
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if r.returncode != 0:
                continue
            blob = f"{r.stdout}\n{r.stderr}"
            m = _VERSION_RE.search(blob)
            if m:
                return m.group(1)
    return None


def detect_prereq(pr: Prereq) -> PrereqStatus:
    _, path = _which(pr.name)
    absent = PrereqStatus(
        name=pr.name,
        present=False,
        min_required=pr.min,
        optional=pr.optional,
        notes=pr.notes,
    )
    paths = _python_candidates(path) if pr.name == "python3" else (path,)
    first = None
    seen = set()
    for candidate in paths:
        if not candidate:
            continue
        key = os.path.normcase(os.path.abspath(candidate))
        if key in seen:
            continue
        seen.add(key)
        store_alias = (
            pr.name == "python3"
            and current_os() == "windows"
            and Path(candidate).parent.name.lower() == "windowsapps"
        )
        status = replace(
            absent, present=not store_alias, path=candidate,
            version=None if store_alias else _probe_version(candidate),
        )
        if first is None:
            first = status
        if not store_alias and status.satisfied:
            return status
    return first or absent


def detect_baseline(catalog=None) -> list[PrereqStatus]:
    """Detect the catalog's baseline prerequisites for this platform."""
    cat = catalog or load_catalog()
    os_ = current_os()
    return [
        detect_prereq(pr) for pr in cat.baseline_prereqs
        if not pr.platforms or os_ in pr.platforms
    ]


def missing(statuses: list[PrereqStatus]) -> list[PrereqStatus]:
    """The prerequisites that are absent or below their minimum (excluding
    optional ones that are simply absent)."""
    return [s for s in statuses if not s.satisfied]
