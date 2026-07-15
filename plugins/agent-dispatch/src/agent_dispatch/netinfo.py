"""Windows/WSL networking topology detection for the coordinator inversion.

On a machine that runs **both Windows and WSL**, the always-on **Windows host
owns the coordinator** and the WSL guest is a client (Phase 2 of the
agent-dispatch-coordinator-inversion effort -- reversing the #2777 WSL-owned
model). Where the coordinator binds, and how a WSL client reaches it, depend on
the WSL2 networking mode:

- **mirrored** (Win11): Windows and WSL share ``127.0.0.1``. The coordinator
  binds ``127.0.0.1`` -- reachable from WSL via ``127.0.0.1``, never LAN-exposed.
- **nat** (Win10/default): WSL reaches the Windows host only via its default
  gateway, which is the Windows-side ``vEthernet (WSL)`` IP (a host-only NAT
  subnet, *not* the LAN). The coordinator binds that dynamic IP -- WSL-reachable
  but not LAN-exposed. **Never** ``0.0.0.0``/LAN.

This module is import-light (stdlib + subprocess only) so ``config.client_url``
can pull it in on the CLI hot path without dragging server deps along.

The three public entry points:

- :func:`is_wsl` -- am I a WSL guest (client-only) vs standalone Linux (full)?
- :func:`get_wsl_networking_mode` / :func:`resolve_bind_host` -- Windows: which
  mode, and which host does the coordinator bind (at startup -- the NAT IP is
  per-boot).
- :func:`resolve_wsl_client_url` -- WSL guest: probe + resolve the Windows
  coordinator's base URL, cached best-effort.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Where a WSL guest caches the last-resolved Windows coordinator base URL. A
# per-boot NAT IP change invalidates it; a failed cached probe triggers re-probe.
_COORD_URL_CACHE = Path.home() / ".agent-dispatch" / "coordinator-url"


# -- Guest-vs-standalone (Linux) --------------------------------------------


# Files whose contents mention "microsoft" under WSL (overridable in tests).
_WSL_PROBE_FILES = ("/proc/sys/kernel/osrelease", "/proc/version")


def is_wsl() -> bool:
    """True on a WSL guest (a Linux env hosted by a Windows box).

    A WSL guest installs **client-only** and resolves the Windows coordinator; a
    standalone Linux host (e.g. Wheatley) installs the **full** coordinator.
    Detected via ``WSL_DISTRO_NAME`` or ``microsoft`` in the kernel osrelease /
    ``/proc/version`` (case-insensitive). Only meaningful on Linux -- Windows and
    macOS return False.
    """
    if not sys.platform.startswith("linux"):
        return False
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    for probe in _WSL_PROBE_FILES:
        try:
            text = Path(probe).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "microsoft" in text.lower():
            return True
    return False


def wsl_default_gateway() -> str | None:
    """The WSL default-route gateway IPv4 (``ip route show default``), or None.

    On NAT this is the Windows-side ``vEthernet (WSL)`` IP -- how a WSL client
    reaches the Windows coordinator. On mirrored it is the LAN router (unused;
    the client uses ``127.0.0.1`` there).
    """
    exe = shutil.which("ip")
    if exe is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe via shutil.which
            [exe, "route", "show", "default"],
            check=False, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "default" and parts[1] == "via":
            return parts[2]
    return None


# -- Networking mode (Windows) ----------------------------------------------


def _default_wslconfig_path() -> Path:
    """``%USERPROFILE%\\.wslconfig`` (the per-user WSL2 config)."""
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return Path(home) / ".wslconfig"


def _read_wslconfig_mode(path: str | os.PathLike[str]) -> str | None:
    """Parse ``[wsl2] networkingMode`` from a ``.wslconfig`` file (lowercased).

    Case-insensitive on both section and key; tolerates inline ``#``/``;``
    comments and quoted values. Returns the value (e.g. ``mirrored``/``nat``) or
    None when absent/unreadable.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    section: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in (";", "#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section == "wsl2" and "=" in line:
            key, _, val = line.partition("=")
            if key.strip().lower() == "networkingmode":
                val = val.split("#", 1)[0].split(";", 1)[0].strip()
                val = val.strip('"').strip("'").lower()
                return val or None
    return None


def _query_vethernet_wsl() -> tuple[str, str | None]:
    """Probe the Windows ``vEthernet (WSL)`` adapter's IPv4.

    Returns ``(status, ip)`` where ``status`` is:

    - ``"present"`` -- the adapter exists with an IPv4 (``ip`` is that address).
    - ``"absent"``  -- the query ran cleanly and found no such adapter (mirrored
      mode has no vEthernet adapter).
    - ``"unknown"`` -- the query could not run (not Windows, no PowerShell, or an
      error); the caller applies the safe NAT default.
    """
    if sys.platform != "win32":
        return ("unknown", None)
    ps = (
        shutil.which("powershell.exe")
        or shutil.which("powershell")
        or shutil.which("pwsh")
    )
    if ps is None:
        return ("unknown", None)
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$a = Get-NetIPAddress -AddressFamily IPv4 | "
        "Where-Object { $_.InterfaceAlias -like 'vEthernet (WSL*' } | "
        "Select-Object -ExpandProperty IPAddress -First 1;"
        "if ($a) { Write-Output $a }"
    )
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe via shutil.which
            [ps, "-NoProfile", "-NonInteractive", "-Command", script],
            check=False, capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return ("unknown", None)
    if proc.returncode != 0:
        return ("unknown", None)
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if lines:
        return ("present", lines[0])
    return ("absent", None)


def get_wsl_networking_mode(wslconfig_path: str | os.PathLike[str] | None = None) -> str:
    """Resolve the WSL2 networking mode on Windows: ``"mirrored"`` or ``"nat"``.

    1. ``.wslconfig`` ``[wsl2] networkingMode`` -- an explicit ``mirrored``/``nat``
       wins.
    2. Else corroborate via the ``vEthernet (WSL)`` adapter: present -> ``nat``,
       cleanly absent -> ``mirrored`` (mirrored has no vEthernet adapter).
    3. Ambiguous (detection failed) -> ``nat`` (the safe assumption: bind the
       specific vEthernet IP rather than presume a shared loopback).
    """
    if wslconfig_path is None:
        wslconfig_path = _default_wslconfig_path()
    mode = _read_wslconfig_mode(wslconfig_path)
    if mode == "mirrored":
        return "mirrored"
    if mode == "nat":
        return "nat"
    status, _ = _query_vethernet_wsl()
    if status == "present":
        return "nat"
    if status == "absent":
        return "mirrored"
    return "nat"


def resolve_bind_host(wslconfig_path: str | os.PathLike[str] | None = None) -> str:
    """The host the Windows coordinator binds -- evaluated **at startup**.

    - **mirrored** -> ``127.0.0.1`` (shared loopback; WSL reaches it there).
    - **nat** -> the ``vEthernet (WSL)`` adapter IPv4 (the WSL default gateway),
      resolved dynamically because it is re-assigned per WSL/HNS restart.

    **Never** returns ``0.0.0.0`` or a LAN address. On NAT with no resolvable
    vEthernet IP it **fails loud** (raises :class:`RuntimeError`) so the launcher
    retries rather than silently binding the LAN.
    """
    mode = get_wsl_networking_mode(wslconfig_path)
    if mode == "mirrored":
        return "127.0.0.1"
    status, ip = _query_vethernet_wsl()
    if ip:
        return ip
    raise RuntimeError(
        "agent-dispatch: NAT WSL networking detected but the 'vEthernet (WSL)' "
        "adapter has no resolvable IPv4 address yet -- refusing to bind "
        "0.0.0.0/LAN. Retry once WSL networking is up "
        f"(vEthernet query status: {status})."
    )


# -- WSL client URL resolution ----------------------------------------------


def _probe_health(base_url: str, timeout: float) -> bool:
    """True if ``<base_url>/health`` answers 2xx within ``timeout`` seconds."""
    try:
        import httpx
    except ImportError:  # pragma: no cover -- httpx is a hard dependency
        return False
    url = base_url.rstrip("/") + "/health"
    try:
        resp = httpx.get(url, timeout=timeout)
    except Exception:
        return False
    return 200 <= resp.status_code < 300


def _read_url_cache() -> str | None:
    try:
        val = _COORD_URL_CACHE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return val or None


def _write_url_cache(url: str) -> None:
    try:
        _COORD_URL_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _COORD_URL_CACHE.write_text(url + "\n", encoding="utf-8")
    except OSError:
        pass


def resolve_wsl_client_url(port: int, timeout: float = 1.0) -> str:
    """Resolve the Windows coordinator base URL from a WSL guest, best-effort.

    Resolution order (``AGENT_DISPATCH_URL`` is handled by the caller):

    1. A cached URL that still answers ``/health`` (tolerates a stable topology).
    2. ``http://127.0.0.1:<port>`` -- the **mirrored** path.
    3. ``http://<default-gateway>:<port>`` -- the **nat** path (the gateway is the
       Windows ``vEthernet (WSL)`` IP).
    4. Fall back to ``http://127.0.0.1:<port>`` (documented default) and let the
       call fail loud.

    The winning URL is cached to ``~/.agent-dispatch/coordinator-url``; a failed
    cached probe triggers a fresh probe (handles a per-boot NAT IP change).
    """
    cached = _read_url_cache()
    if cached and _probe_health(cached, timeout):
        return cached
    candidates = [f"http://127.0.0.1:{port}"]
    gateway = wsl_default_gateway()
    if gateway and gateway != "127.0.0.1":
        candidates.append(f"http://{gateway}:{port}")
    for candidate in candidates:
        if _probe_health(candidate, timeout):
            _write_url_cache(candidate)
            return candidate
    return f"http://127.0.0.1:{port}"
