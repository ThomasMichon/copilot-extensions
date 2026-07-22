"""Command-line interface for agent-vault."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import config, rendezvous
from .config import IS_WINDOWS, SOCKET_PATH, ResolvedVault
from .prompt import prompt_password as gui_prompt_password
from .winpipe import pipe_send


def _detect_wsl() -> bool:
    """Detect WSL via env var or /proc/version."""
    if IS_WINDOWS:
        return False
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


IS_WSL = _detect_wsl()


# ---------------------------------------------------------------------------
# IPC client
# ---------------------------------------------------------------------------


def _send_socket(
    request: dict, timeout: float | None = 5.0, path: str = SOCKET_PATH
) -> dict | None:
    """Try sending a command via Unix socket (``path`` defaults to ``SOCKET_PATH``)."""
    import socket as _sock

    connect_timeout = 5.0
    try:
        s = _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM)
        s.settimeout(connect_timeout)
        s.connect(path)
        s.settimeout(timeout)
        s.sendall((json.dumps(request) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        return json.loads(buf.decode().strip())
    except Exception:
        return None


def _send_tcp(request: dict, host: str, port: int, timeout: float | None) -> dict | None:
    """Try sending a command via TCP to a specific host:port."""
    import socket

    connect_timeout = 5.0
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(connect_timeout)
        s.connect((host, port))
        s.settimeout(timeout)
        s.sendall((json.dumps(request) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        return json.loads(buf.decode().strip())
    except Exception:
        return None


def _windows_run_dirs() -> list[Path]:
    """Candidate Windows-side agent-vault runtime dirs, seen from WSL via ``/mnt/c``.

    A WSL guest has no local daemon; it reaches the Windows-hosted vault. The
    Windows daemon advertises its endpoint under ``%USERPROFILE%\\.agent-vault\\run``,
    visible from WSL at ``/mnt/c/Users/<user>/.agent-vault/run``. Honors an
    explicit ``AGENT_VAULT_WINDOWS_RUN_DIR`` override; otherwise globs the mounted
    Windows profiles (skipping system profiles), newest ``endpoint.json`` first.
    """
    override = os.environ.get("AGENT_VAULT_WINDOWS_RUN_DIR")
    if override:
        return [Path(override)]
    mount = os.environ.get("AGENT_VAULT_WINDOWS_MOUNT", "/mnt/c")
    users = Path(mount) / "Users"
    skip = {"public", "default", "default user", "all users"}
    candidates: list[tuple[float, Path]] = []
    try:
        for profile in users.iterdir():
            if profile.name.lower() in skip:
                continue
            ep = profile / ".agent-vault" / "run" / "endpoint.json"
            try:
                mtime = ep.stat().st_mtime
            except OSError:
                continue
            candidates.append((mtime, ep.parent))
    except OSError:
        return []
    candidates.sort(reverse=True)  # newest advertised endpoint wins
    return [d for _, d in candidates]


def _read_windows_endpoint() -> "rendezvous.Endpoint | None":
    """Read the Windows-side rendezvous file from WSL; ``None`` if none found.

    The recorded ``pid`` is a *Windows* pid, meaningless to a Linux
    ``pid_alive`` check, so staleness here is left to the actual send (which
    fails fast and falls through to the legacy dial). The endpoint is re-tagged
    ``source="windows"`` so the caller keeps the resolved cross-boundary host.
    """
    from dataclasses import replace

    for run in _windows_run_dirs():
        ep = rendezvous.read_endpoint(run)
        if ep is not None:
            return replace(ep, source="windows")
    return None


def _discover_endpoint(context: ResolvedVault) -> "rendezvous.Endpoint | None":
    """Discover the daemon endpoint via the rendezvous ladder, or ``None``.

    Ladder: explicit ``AGENT_VAULT_ENDPOINT`` override -> the local rendezvous
    file (a daemon on this host) -> (WSL only) the Windows-side rendezvous file.
    Returns ``None`` when nothing is discovered so the caller falls back to
    exactly today's legacy dial (UDS->TCP / fixed port).
    """
    override = os.environ.get(config.ENDPOINT_ENV)
    try:
        return rendezvous.resolve(
            config.run_dir(),
            override=override,
            probe=rendezvous.connect_probe,
        )
    except rendezvous.EndpointUnavailable:
        pass
    if IS_WSL:
        return _read_windows_endpoint()
    return None


def send_command(request: dict, timeout: float | None = 5.0) -> dict | None:
    """Send a JSON command to the vault service."""
    context = config.resolve_context()
    request = dict(request)
    request.setdefault("kpdb", context.kpdb)
    request.setdefault("group", context.group)
    request.setdefault("vault", context.vault_name)

    def _tag(result: dict | None, transport: str) -> dict | None:
        if result is not None:
            result["_transport"] = transport
        return result

    from .extensions import TransportContext, get_registry

    host = os.environ.get("AGENT_VAULT_HOST", "127.0.0.1")
    port = context.port

    # Discovery-first (backwards-compatible): resolve the daemon's *actual*
    # endpoint from the rendezvous file, falling back to today's fixed
    # socket/port when nothing is advertised. A discovered TCP port also feeds
    # the extension transports (e.g. the WSL->Windows interop relay), so a
    # dynamic/discovered port is honored end to end.
    discovered = _discover_endpoint(context)
    discovered_unix: str | None = None
    discovered_pipe: str | None = None
    if discovered is not None:
        if discovered.transport == "unix":
            discovered_unix = discovered.address
        elif discovered.transport == "pipe":
            discovered_pipe = discovered.address
        elif discovered.transport == "tcp":
            try:
                d_host, d_port = discovered.tcp_host_port
                port = d_port
                # A locally-advertised file carries the real host; the WSL
                # Windows-side file advertises 127.0.0.1, so keep the resolved
                # host to cross the boundary.
                if discovered.source != "windows":
                    host = d_host
            except ValueError:
                discovered = None

    ext_ctx = TransportContext(
        kpdb=context.kpdb,
        group=context.group,
        vault_name=context.vault_name,
        port=port,
    )

    # Extension transports registered before_builtin take precedence over the
    # local daemon (e.g. an SSH-tunnel that should reach the caller's own vault).
    result = get_registry().try_transports(request, timeout, ext_ctx, before_builtin=True)
    if result is not None:
        return result

    # A discovered Windows named pipe (rung 2) is dialed ahead of TCP; any pipe
    # failure falls through to the legacy TCP path below.
    if discovered_pipe is not None and IS_WINDOWS:
        result = pipe_send(discovered_pipe, request, timeout)
        if result is not None:
            return _tag(result, "discovered-pipe")

    # A discovered Unix socket (native, possibly non-default path) is dialed
    # ahead of the legacy fixed socket.
    if discovered_unix is not None and not IS_WINDOWS:
        result = _send_socket(request, timeout, path=discovered_unix)
        if result is not None:
            return _tag(result, "discovered-unix")

    if discovered_unix is None and not IS_WINDOWS and context.sources.get("port") == "default":
        result = _send_socket(request, timeout)
        if result is not None:
            return _tag(result, "unix-socket")

    result = _send_tcp(request, host, port, timeout)
    if result is not None:
        is_disc_tcp = discovered is not None and discovered.transport == "tcp"
        return _tag(result, "discovered-tcp" if is_disc_tcp else "tcp")

    return get_registry().try_transports(request, timeout, ext_ctx)


# ---------------------------------------------------------------------------
# Service lifecycle
# ---------------------------------------------------------------------------


def _start_service_systemd() -> bool:
    """Start the vault service via systemd user unit if available."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-enabled", "agent-vault.service"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return False
        subprocess.run(
            ["systemctl", "--user", "start", "agent-vault.service"],
            capture_output=True, timeout=10,
        )
        for _ in range(20):
            time.sleep(0.25)
            resp = send_command({"action": "ping"})
            if resp and resp.get("ok"):
                return True
        return False
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def start_service(tcp_port: int | None = None) -> bool:
    """Start the vault service in the background."""
    if tcp_port is None:
        context = config.resolve_context()
        if not IS_WINDOWS and context.sources.get("port") == "default" and _start_service_systemd():
            return True
        tcp_port = context.port

    cmd = [sys.executable, "-m", "agent_vault.service"]
    if tcp_port:
        cmd.extend(["--tcp-port", str(tcp_port)])

    if IS_WINDOWS:
        create_no_window = 0x08000000
        detached_process = 0x00000008
        cmd.append("--foreground")
        subprocess.Popen(
            cmd,
            creationflags=create_no_window | detached_process,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    for _ in range(20):
        time.sleep(0.25)
        resp = send_command({"action": "ping"})
        if resp and resp.get("ok"):
            return True
    return False


def ensure_service(tcp_port: int | None = None) -> bool:
    """Ensure the service is running, starting it if needed."""
    resp = send_command({"action": "ping"})
    if resp and resp.get("ok"):
        return True
    return start_service(tcp_port)


# ---------------------------------------------------------------------------
# Password prompting (for unlock)
# ---------------------------------------------------------------------------


def prompt_password() -> str | None:
    """Prompt for master password via GUI or terminal."""
    pw = gui_prompt_password("KeePass master password:")
    if pw:
        return pw
    if os.environ.get("VAULT_NONINTERACTIVE"):
        print(
            "Error: vault is locked and no prompt is available.\n"
            "Run 'agent-vault unlock' in an interactive terminal first.",
            file=sys.stderr,
        )
        return None
    return None


def _read_password_from_tty(prompt: str) -> str | None:
    """Read a password from the controlling terminal with echo disabled."""
    if os.name == "nt":
        import getpass

        try:
            return getpass.getpass(prompt)
        except (EOFError, KeyboardInterrupt):
            return None

    try:
        import termios

        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return None

    try:
        old = termios.tcgetattr(fd)
        new = list(old)
        new[3] &= ~termios.ECHO
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, new)
            os.write(fd, prompt.encode("utf-8", "replace"))
            data = os.read(fd, 4096)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            os.write(fd, b"\n")
        return data.decode("utf-8", "replace").rstrip("\r\n")
    except (OSError, termios.error):
        return None
    finally:
        os.close(fd)


def _has_controlling_tty() -> bool:
    """Whether an interactive controlling terminal is available for a prompt.

    On POSIX this checks that ``/dev/tty`` can be opened -- it works even when
    stdin is piped, as long as the process has a controlling terminal (e.g. an
    interactive SSH session). On Windows it falls back to ``sys.stdin.isatty()``.
    Used to decide whether the inline-terminal unlock fallback can reach the
    operator, so a headless/agent caller fails fast instead of printing a
    "no terminal" error.
    """
    if os.name == "nt":
        return sys.stdin.isatty()
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return False
    os.close(fd)
    return True


def _server_prompted_unlock() -> bool:
    """Ask the vault service to prompt for the password on its own GUI."""
    resp = send_command({"action": "unlock", "prompt": True}, timeout=None)
    if resp is not None:
        if resp.get("ok"):
            return True
        print(f"Unlock failed: {resp.get('error', 'unknown')}", file=sys.stderr)
        return False
    print("Error: vault service not reachable", file=sys.stderr)
    return False


def _terminal_unlock_local() -> bool:
    """Read the master password on this terminal and unlock the local service."""
    pw = _read_password_from_tty("KeePass master password: ")
    if pw is None:
        print(
            "Error: terminal unlock needs an interactive controlling terminal, "
            "but none is available.",
            file=sys.stderr,
        )
        return False
    if not pw:
        return False
    resp = send_command({"action": "unlock", "password": pw}, timeout=None)
    if resp is not None:
        if resp.get("ok"):
            return True
        print(f"Unlock failed: {resp.get('error', 'unknown')}", file=sys.stderr)
        return False
    print("Error: vault service not reachable", file=sys.stderr)
    return False


def auto_unlock() -> bool:
    """Acquire the master password and unlock the CLI backend.

    Prefers the richest prompt available and always falls back to an inline
    terminal prompt when a controlling terminal is present -- so a bare ``unlock``
    reaches the operator even on a headless/SSH host where no GUI dialog can be
    displayed. (Unlock-source providers -- e.g. an operator-held value -- are
    consulted daemon-side before any prompt.) When there is no GUI *and* no
    controlling terminal, it returns ``False`` rather than stalling.
    """
    if os.environ.get("VAULT_UNLOCK_TERMINAL"):
        return _terminal_unlock_local()
    if IS_WSL:
        # Let the service resolve providers and prompt on a GUI if one is
        # reachable; on a headless/SSH host it returns without unlocking, so fall
        # back to an inline terminal prompt when we have a controlling TTY.
        print("[agent-vault] Requesting password via vault service...", file=sys.stderr)
        if _server_prompted_unlock():
            return True
        if _has_controlling_tty():
            return _terminal_unlock_local()
        return False
    pw = prompt_password()
    if pw:
        resp = send_command({"action": "unlock", "password": pw}, timeout=15.0)
        if resp and resp.get("ok"):
            return True
        if resp:
            print(f"Unlock failed: {resp.get('error', 'unknown')}", file=sys.stderr)
    # No GUI prompt available (or it failed/cancelled) -> inline terminal fallback.
    if _has_controlling_tty():
        return _terminal_unlock_local()
    return False


def prompt_password_with_confirm(title: str = "New Password") -> str | None:
    """Prompt for a new password with confirmation via GUI or terminal."""
    try:
        ps_script = r'''
Add-Type -AssemblyName System.Windows.Forms
$form = New-Object System.Windows.Forms.Form
$form.Text = "TITLE_PLACEHOLDER"
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.TopMost = $true

$lbl1 = New-Object System.Windows.Forms.Label
$lbl1.Text = "Password:"
$lbl1.Location = New-Object System.Drawing.Point(15, 20)
$lbl1.AutoSize = $true
$form.Controls.Add($lbl1)

$box1 = New-Object System.Windows.Forms.TextBox
$box1.Location = New-Object System.Drawing.Point(15, 45)
$box1.Size = New-Object System.Drawing.Size(320, 20)
$box1.UseSystemPasswordChar = $true
$form.Controls.Add($box1)

$lbl2 = New-Object System.Windows.Forms.Label
$lbl2.Text = "Confirm password:"
$lbl2.Location = New-Object System.Drawing.Point(15, 80)
$lbl2.AutoSize = $true
$form.Controls.Add($lbl2)

$box2 = New-Object System.Windows.Forms.TextBox
$box2.Location = New-Object System.Drawing.Point(15, 105)
$box2.Size = New-Object System.Drawing.Size(320, 20)
$box2.UseSystemPasswordChar = $true
$form.Controls.Add($box2)

$ok = New-Object System.Windows.Forms.Button
$ok.Text = "OK"
$ok.Location = New-Object System.Drawing.Point(255, 140)
$ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
$form.AcceptButton = $ok
$form.Controls.Add($ok)

$form.Size = New-Object System.Drawing.Size(370, 210)
if ($form.ShowDialog() -ne "OK" -or -not $box1.Text) { Write-Output "CANCELLED"; return }
if ($box1.Text -ne $box2.Text) { Write-Output "MISMATCH"; return }
Write-Output $box1.Text
'''.replace("TITLE_PLACEHOLDER", title.replace('"', "'"))
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=120,
        )
        pw = r.stdout.strip().replace("\r", "")
        if pw == "CANCELLED" or not pw:
            return None
        if pw == "MISMATCH":
            print("Error: passwords do not match", file=sys.stderr)
            return None
        return pw
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if os.environ.get("VAULT_NONINTERACTIVE"):
        return None
    import getpass

    pw1 = getpass.getpass("Password: ")
    if not pw1:
        return None
    pw2 = getpass.getpass("Confirm password: ")
    if pw1 != pw2:
        print("Error: passwords do not match", file=sys.stderr)
        return None
    return pw1


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_get(args):
    entry = args.entry
    field = args.field or "password"
    refresh = getattr(args, "refresh", False)
    cache_only = getattr(args, "cache_only", False)

    from .cache import get_cache

    cache = get_cache()

    # Tier 0: the persistent on-disk cache. Consulted first unless --refresh,
    # and authoritatively (no daemon contact) when --cache-only.
    if cache.enabled and not refresh:
        cached = cache.get(entry, field)
        if cached is not None:
            print(cached)
            return 0
    if cache_only:
        print(f"Error: {entry} [{field}] not in cache", file=sys.stderr)
        return 1

    if not ensure_service():
        print("Error: could not start vault service", file=sys.stderr)
        return 1

    request = {"action": "get", "entry": entry, "field": field}
    if getattr(args, "prompt", False):
        request["allow_prompt"] = True
    resp = send_command(request, timeout=None)
    if resp and resp.get("ok"):
        value = resp["value"]
        if cache.enabled:
            cache.put(entry, field, value)
        print(value)
        return 0

    error = resp.get("error", "unknown") if resp else "service unreachable"
    print(f"Error: {error}", file=sys.stderr)
    return 1


def cmd_has(args):
    if not ensure_service():
        print("Error: could not start vault service", file=sys.stderr)
        return 1

    resp = send_command({"action": "has", "entry": args.entry}, timeout=None)
    if resp and resp.get("ok"):
        if resp["exists"]:
            print("true")
            return 0
        print("false")
        return 1

    error = resp.get("error", "unknown") if resp else "service unreachable"
    print(f"Error: {error}", file=sys.stderr)
    return 1


def cmd_search(args):
    if not ensure_service():
        print("Error: could not start vault service", file=sys.stderr)
        return 1

    resp = send_command({"action": "search", "query": args.query}, timeout=None)
    if resp and resp.get("ok"):
        for path in resp["results"]:
            print(path)
        return 0

    error = resp.get("error", "unknown") if resp else "service unreachable"
    print(f"Error: {error}", file=sys.stderr)
    return 1


def _vault_status_label(cli_state: str) -> str:
    """Translate internal cli state to user-facing status."""
    if cli_state == "unlocked":
        return "available"
    return "locked"


_TRANSPORT_LABELS = {
    "unix-socket": "unix socket",
    "tcp": "local TCP",
    "discovered-pipe": "named pipe (discovered)",
    "discovered-unix": "unix socket (discovered)",
    "discovered-tcp": "local TCP (discovered)",
}


def cmd_ping(args):
    resp = send_command({"action": "ping"})
    if resp and resp.get("ok"):
        status = _vault_status_label(resp["cli"])
        transport = _TRANSPORT_LABELS.get(
            resp.get("_transport", ""), resp.get("_transport", "unknown")
        )
        print(
            f"Vault service running - PID {resp['pid']}, "
            f"TTL {resp['ttl']}s, {resp['cached']} cached, "
            f"status={status}, via {transport}"
        )
        return 0
    print("Vault service not running")
    return 1


def cmd_start(args):
    resp = send_command({"action": "ping"})
    if resp and resp.get("ok"):
        status = _vault_status_label(resp["cli"])
        print(f"Already running - PID {resp['pid']}, status={status}")
        return 0

    if ensure_service():
        resp = send_command({"action": "ping"})
        if resp and resp.get("ok"):
            status = _vault_status_label(resp["cli"])
            print(f"Vault service started - PID {resp['pid']}, status={status}")
            return 0
    print("Error: failed to start vault service", file=sys.stderr)
    return 1


def cmd_stop(args):
    resp = send_command({"action": "stop"})
    if resp and resp.get("ok"):
        print("Vault service stopped")
        return 0
    print("Vault service not running")
    return 1


def cmd_lock(args):
    resp = send_command({"action": "lock"})
    if resp and resp.get("ok"):
        if resp.get("was_unlocked"):
            print("Vault locked")
        else:
            print("Vault already locked")
        return 0
    print("Vault service not running")
    return 1


def _current_vault_unlocked(resp: dict, context: ResolvedVault | None = None) -> bool:
    """Return whether the service has the current vault unlocked."""
    if not resp or not resp.get("ok"):
        return False
    context = context or config.resolve_context()
    if context.kpdb:
        return context.kpdb in resp.get("unlocked_vaults", [])
    return resp.get("cli") == "unlocked"


def cmd_unlock(args):
    if getattr(args, "terminal", False) or os.environ.get("VAULT_UNLOCK_TERMINAL"):
        if _terminal_unlock_local():
            print("Vault unlocked (local service, via terminal)")
            return 0
        return 1

    if not ensure_service():
        print("Error: could not start vault service", file=sys.stderr)
        return 1

    resp = send_command({"action": "ping"})
    if _current_vault_unlocked(resp):
        print("Vault available")
        return 0

    if auto_unlock():
        print("Vault available")
        return 0
    return 1


def _ensure_unlocked_service() -> bool:
    """Ensure service is running and unlocked. Returns True on success."""
    if not ensure_service():
        print("Error: could not start vault service", file=sys.stderr)
        return False
    resp = send_command({"action": "ping"})
    if _current_vault_unlocked(resp):
        return True
    if auto_unlock():
        return True
    print("Error: vault unavailable", file=sys.stderr)
    return False


def cmd_add(args):
    if not _ensure_unlocked_service():
        return 1

    request = {
        "action": "add",
        "entry": args.entry,
    }
    if args.username:
        request["username"] = args.username
    if args.url:
        request["url"] = args.url
    if args.generate:
        request["generate"] = True
    elif args.password:
        request["password"] = args.password
    else:
        pw = prompt_password_with_confirm("New Entry: " + args.entry)
        if pw is None:
            print("Error: no password provided", file=sys.stderr)
            return 1
        request["password"] = pw

    resp = send_command(request, timeout=10.0)
    if resp and resp.get("ok"):
        print(resp.get("message", "Entry created"))
        return 0
    error = resp.get("error", "unknown") if resp else "service unreachable"
    print(f"Error: {error}", file=sys.stderr)
    return 1


def cmd_set_password(args):
    if not _ensure_unlocked_service():
        return 1

    password = args.password
    if not password:
        import getpass

        password = getpass.getpass("New password: ")
        if not password:
            print("Error: no password provided", file=sys.stderr)
            return 1

    resp = send_command({
        "action": "set-password",
        "entry": args.entry,
        "password": password,
    }, timeout=10.0)
    if resp and resp.get("ok"):
        print(resp.get("message", "Password updated"))
        return 0
    error = resp.get("error", "unknown") if resp else "service unreachable"
    print(f"Error: {error}", file=sys.stderr)
    return 1


def cmd_set_username(args):
    if not _ensure_unlocked_service():
        return 1

    resp = send_command({
        "action": "set-username",
        "entry": args.entry,
        "username": args.username,
    }, timeout=10.0)
    if resp and resp.get("ok"):
        print(resp.get("message", "Username updated"))
        return 0
    error = resp.get("error", "unknown") if resp else "service unreachable"
    print(f"Error: {error}", file=sys.stderr)
    return 1


def cmd_list(args):
    if not ensure_service():
        print("Error: could not start vault service", file=sys.stderr)
        return 1

    request = {"action": "list", "path": args.path or "/"}
    if args.recursive:
        request["recursive"] = True
    if args.flatten:
        request["flatten"] = True
    resp = send_command(request, timeout=None)
    if resp and resp.get("ok"):
        for entry in resp["entries"]:
            print(entry)
        return 0
    error = resp.get("error", "unknown") if resp else "service unreachable"
    print(f"Error: {error}", file=sys.stderr)
    return 1


def cmd_show(args):
    if not ensure_service():
        print("Error: could not start vault service", file=sys.stderr)
        return 1

    request = {"action": "show", "entry": args.entry}
    if args.show_protected:
        request["show_protected"] = True
    resp = send_command(request, timeout=None)
    if resp and resp.get("ok"):
        print(resp["output"], end="")
        return 0
    error = resp.get("error", "unknown") if resp else "service unreachable"
    print(f"Error: {error}", file=sys.stderr)
    return 1


def cmd_remove(args):
    if not _ensure_unlocked_service():
        return 1

    request = {"action": "remove", "entry": args.entry}
    if args.force:
        request["force"] = True
    resp = send_command(request, timeout=10.0)
    if resp and resp.get("ok"):
        print(resp.get("message", "Entry removed"))
        return 0
    error = resp.get("error", "unknown") if resp else "service unreachable"
    print(f"Error: {error}", file=sys.stderr)
    return 1


def cmd_move(args):
    if not _ensure_unlocked_service():
        return 1

    request = {"action": "move", "entry": args.entry, "dest": args.dest}
    if args.force:
        request["force"] = True
    resp = send_command(request, timeout=10.0)
    if resp and resp.get("ok"):
        print(resp.get("message", "Entry moved"))
        return 0
    error = resp.get("error", "unknown") if resp else "service unreachable"
    print(f"Error: {error}", file=sys.stderr)
    return 1


def cmd_git_credential(args):
    """git credential helper: delegate an allowlisted host's credential to GCM.

    Reads the git-credential protocol (key=value lines, blank-line terminated)
    on stdin. Only ``get`` resolves a credential (via the daemon's
    ``git-credential`` action); ``store``/``erase`` are no-ops because the vault
    is the source of truth and GCM manages its own cache.
    """
    if args.op != "get":
        return 0

    fields: dict[str, str] = {}
    for line in sys.stdin:
        line = line.rstrip("\n")
        if not line:
            break
        key, _, value = line.partition("=")
        if key:
            fields[key.strip()] = value.strip()

    host = fields.get("host", "")
    if not host:
        return 0

    if not ensure_service():
        return 0  # helper: stay silent so git falls through to other helpers

    request = {
        "action": "git-credential",
        "protocol": fields.get("protocol", "https"),
        "host": host,
    }
    if fields.get("path"):
        request["path"] = fields["path"]
    if fields.get("username"):
        request["username"] = fields["username"]
    resp = send_command(request, timeout=None)
    if resp and resp.get("ok"):
        print(f"protocol={resp.get('protocol', request['protocol'])}")
        print(f"host={resp.get('host', host)}")
        print(f"username={resp.get('username', '')}")
        print(f"password={resp.get('password', '')}")
    return 0


def cmd_import_key(args):
    import base64

    key_file = Path(args.key_file)
    pub_file = key_file.with_suffix(key_file.suffix + ".pub") if key_file.suffix else Path(
        str(key_file) + ".pub"
    )
    if not key_file.exists():
        print(f"Error: key file not found: {key_file}", file=sys.stderr)
        return 1
    if not pub_file.exists():
        print(f"Error: public key not found: {pub_file}", file=sys.stderr)
        return 1

    if not _ensure_unlocked_service():
        return 1

    key_data = base64.b64encode(key_file.read_bytes()).decode()
    pub_data = base64.b64encode(pub_file.read_bytes()).decode()
    key_name = key_file.name

    resp = send_command({
        "action": "import-key",
        "entry": args.entry,
        "key_name": key_name,
        "key_data": key_data,
        "pub_data": pub_data,
    }, timeout=15.0)
    if resp and resp.get("ok"):
        print(resp.get("message", f"Imported {key_name}"))
        return 0
    error = resp.get("error", "unknown") if resp else "service unreachable"
    print(f"Error: {error}", file=sys.stderr)
    return 1


def cmd_export_key(args):
    import base64

    dest_dir = Path(args.dest_dir)
    if not dest_dir.is_dir():
        print(f"Error: destination directory not found: {dest_dir}", file=sys.stderr)
        return 1

    if not _ensure_unlocked_service():
        return 1

    resp = send_command({
        "action": "export-key",
        "entry": args.entry,
        "key_name": args.key_name,
    }, timeout=15.0)
    if not resp or not resp.get("ok"):
        error = resp.get("error", "unknown") if resp else "service unreachable"
        print(f"Error: {error}", file=sys.stderr)
        return 1

    key_file = dest_dir / args.key_name
    pub_file = dest_dir / (args.key_name + ".pub")

    key_file.write_bytes(base64.b64decode(resp["key_data"]))
    pub_file.write_bytes(base64.b64decode(resp["pub_data"]))

    if not IS_WINDOWS:
        key_file.chmod(0o600)
        pub_file.chmod(0o644)

    print(f"Exported {args.key_name} to {dest_dir}")
    return 0


def _read_cache_manifest(path: Path) -> list[tuple[str, str]]:
    """Parse ``entry | field`` lines (``#`` comments) from a manifest file."""
    pairs: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entry, sep, field = line.partition("|")
        entry = entry.strip()
        field = field.strip() if sep else "password"
        if entry:
            pairs.append((entry, field or "password"))
    return pairs


def cmd_cache_populate(args):
    """Pre-warm the vault cache for a set of entries.

    Entries come from (deduped, in order): explicit ``--entry`` values, a
    ``--manifest`` file, and every registered cache-source extension. Each entry
    is fetched so the daemon caches it (and missing entries surface early).
    """
    from .extensions import get_registry

    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(entry: str, field: str) -> None:
        pair = (entry, field or "password")
        if pair[0] and pair not in seen:
            seen.add(pair)
            pairs.append(pair)

    for entry in (args.entry or []):
        e, sep, f = entry.partition(":")
        _add(e.strip(), (f.strip() if sep else "password"))

    if args.manifest:
        manifest = Path(args.manifest)
        if not manifest.exists():
            print(f"Error: manifest not found: {manifest}", file=sys.stderr)
            return 1
        for e, f in _read_cache_manifest(manifest):
            _add(e, f)

    for e, f in get_registry().collect_cache_entries(args.machine):
        _add(e, f)

    if not pairs:
        print("No entries to cache (no --entry, --manifest, or cache-source extension).",
              file=sys.stderr)
        return 1

    if not ensure_service():
        print("Error: could not start vault service", file=sys.stderr)
        return 1

    verb = "Verifying" if args.verify else "Populating"
    print(f"{verb} cache for {len(pairs)} entr{'y' if len(pairs) == 1 else 'ies'}...")

    from .cache import get_cache

    cache = get_cache()
    ok = 0
    missing: list[str] = []
    for entry, field in pairs:
        action = "has" if args.verify else "get"
        request = {"action": action, "entry": entry, "field": field}
        if args.prompt and not args.verify:
            request["allow_prompt"] = True
        resp = send_command(request, timeout=None if args.prompt else 15.0)
        present = bool(resp and resp.get("ok") and (resp.get("exists", True)))
        if present:
            ok += 1
            # Warm the persistent cache too, so the value survives daemon
            # restarts and answers later --cache-only reads.
            if not args.verify and cache.enabled and resp.get("value") is not None:
                cache.put(entry, field, resp["value"])
        else:
            missing.append(f"{entry} [{field}]")

    fail = len(pairs) - ok
    if args.verify:
        print(f"Present: {ok}/{len(pairs)}")
    else:
        print(f"Cached {ok} credential(s), {fail} failed")
    if missing:
        for m in missing:
            print(f"  missing: {m}", file=sys.stderr)
    return 1 if fail > 0 else 0


def cmd_cache_clear(args):
    """Wipe the persistent on-disk credential cache."""
    from .cache import get_cache

    cache = get_cache()
    if cache.clear():
        print("Cache cleared.")
        return 0
    print("Error: could not clear cache", file=sys.stderr)
    return 1


def cmd_cache_status(args):
    """Show persistent-cache status (enabled, location, counts, staleness)."""
    from .cache import get_cache

    status = get_cache().status()
    if getattr(args, "json", False):
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0
    state = "enabled" if status["enabled"] else "disabled"
    if not status["available"]:
        state += " (cryptography unavailable)"
    print(f"cache: {state}")
    print(f"dir: {status['cache_dir']}")
    print(f"entries: {status['entry_count']} ({status['field_count']} field(s))")
    if status["oldest"]:
        print(f"oldest: {status['oldest']}")
    if status["newest"]:
        print(f"newest: {status['newest']}")
    return 0


def cmd_cache_verify(args):
    """Check the persistent cache holds a set of entries without unlocking.

    Entries come from ``--entry`` / ``--manifest`` / cache-source extensions,
    exactly like ``cache-populate``. Exits non-zero (2) if any are missing, so a
    launch-time gate can confirm an unattended box is primed while still locked.
    """
    from .cache import get_cache

    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(entry: str, field: str) -> None:
        pair = (entry, field or "password")
        if pair[0] and pair not in seen:
            seen.add(pair)
            pairs.append(pair)

    for entry in (getattr(args, "entry", None) or []):
        e, sep, f = entry.partition(":")
        _add(e.strip(), (f.strip() if sep else "password"))
    if getattr(args, "manifest", None):
        manifest = Path(args.manifest)
        if not manifest.exists():
            print(f"Error: manifest not found: {manifest}", file=sys.stderr)
            return 1
        for e, f in _read_cache_manifest(manifest):
            _add(e, f)
    from .extensions import get_registry

    for e, f in get_registry().collect_cache_entries(getattr(args, "machine", None)):
        _add(e, f)

    if not pairs:
        print("No entries to verify (no --entry, --manifest, or cache-source extension).",
              file=sys.stderr)
        return 1

    cache = get_cache()
    present = 0
    missing: list[str] = []
    for entry, field in pairs:
        if cache.get(entry, field) is not None:
            present += 1
        else:
            missing.append(f"{entry} [{field}]")

    if getattr(args, "json", False):
        print(json.dumps(
            {"present": present, "total": len(pairs), "missing": missing},
            indent=2,
        ))
    else:
        print(f"Cached: {present}/{len(pairs)}")
        for m in missing:
            print(f"  missing: {m}", file=sys.stderr)
    return 2 if missing else 0


def cmd_which(args):
    context = config.resolve_context()
    payload = {
        "vault_name": context.vault_name,
        "kpdb": context.kpdb,
        "group": context.group,
        "port": context.port,
        "sources": context.sources,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"vault: {context.vault_name or '(none)'} ({context.sources.get('vault')})")
    print(f"kpdb: {context.kpdb or '(none)'} ({context.sources.get('kpdb')})")
    print(f"group: {context.group or '(none)'} ({context.sources.get('group')})")
    print(f"port: {context.port} ({context.sources.get('port')})")
    return 0


def cmd_vault_list(args):
    data = config.load_global_config()
    vaults = config.list_vaults()
    active = config.resolve_context().vault_name
    default = data.get("default_vault")
    if args.json:
        print(json.dumps({"vaults": vaults, "default_vault": default, "active": active}, indent=2))
        return 0
    if not vaults:
        print("No named vaults configured.")
        return 0
    for name in sorted(vaults):
        item = vaults[name]
        markers = []
        if name == default:
            markers.append("default")
        if name == active:
            markers.append("active")
        marker = f" [{' '.join(markers)}]" if markers else ""
        print(f"{name}{marker}")
        print(f"  kpdb: {item.get('kpdb', '(none)')}")
        if item.get("group"):
            print(f"  group: {item['group']}")
        if item.get("port"):
            print(f"  port: {item['port']}")
    return 0


def cmd_vault_add(args):
    config.add_vault(args.name, args.kpdb, group=args.group, port=args.port)
    print(f"Vault added: {args.name}")
    return 0


def cmd_vault_set_default(args):
    try:
        config.set_default_vault(args.name)
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Default vault: {args.name}")
    return 0


def cmd_vault_remove(args):
    try:
        config.remove_vault(args.name)
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Vault removed: {args.name}")
    return 0


def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog="agent-vault",
        description="Local KeePassXC-backed credential CLI",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("get", help="Read a credential")
    p.add_argument("entry", help="Entry path")
    p.add_argument("field", nargs="?", default="password",
                   help="Field to read (default: password)")
    p.add_argument("-p", "--prompt", action="store_true",
                   help="Prompt for the master password if locked "
                        "(default: fail fast and ask you to run 'agent-vault unlock')")
    p.add_argument("--refresh", action="store_true",
                   help="Bypass the persistent cache and fetch from the live vault")
    p.add_argument("--cache-only", action="store_true", dest="cache_only",
                   help="Read from the persistent cache only; never contact the service")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("has", help="Check if an entry exists")
    p.add_argument("entry", help="Entry path")
    p.set_defaults(func=cmd_has)

    p = sub.add_parser("search", help="Search entries")
    p.add_argument("query", help="Search query")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("ping", help="Check service status")
    p.set_defaults(func=cmd_ping)

    p = sub.add_parser("start", help="Start the vault service")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("stop", help="Stop the vault service")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("lock", help="Lock vault and keep service running")
    p.set_defaults(func=cmd_lock)

    p = sub.add_parser("unlock", help="Unlock CLI backend")
    p.add_argument(
        "--terminal", "--here", action="store_true", dest="terminal",
        help="Prompt for the master password on this terminal and unlock the local service.",
    )
    p.set_defaults(func=cmd_unlock)

    p = sub.add_parser("add", help="Create a new vault entry")
    p.add_argument("entry", help="Entry path")
    p.add_argument("-u", "--username", help="Username")
    p.add_argument("--url", help="URL")
    p.add_argument("--password", help="Password (prompted if not given and -g not set)")
    p.add_argument("-g", "--generate", action="store_true", help="Generate a random password")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("set-password", help="Update entry password")
    p.add_argument("entry", help="Entry path")
    p.add_argument("--password", help="New password (prompted if not given)")
    p.set_defaults(func=cmd_set_password)

    p = sub.add_parser("set-username", help="Update entry username")
    p.add_argument("entry", help="Entry path")
    p.add_argument("username", help="New username")
    p.set_defaults(func=cmd_set_username)

    p = sub.add_parser("list", aliases=["ls"], help="List entries under a group")
    p.add_argument("path", nargs="?", default="/", help="Group path (default: /)")
    p.add_argument("-R", "--recursive", action="store_true", help="Recurse into subgroups")
    p.add_argument("-f", "--flatten", action="store_true", help="Flatten to full paths")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="Show all fields of an entry")
    p.add_argument("entry", help="Entry path")
    p.add_argument("-s", "--show-protected", action="store_true",
                   help="Reveal protected fields (e.g. password)")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("remove", aliases=["rm"], help="Remove an entry")
    p.add_argument("entry", help="Entry path")
    p.add_argument("-f", "--force", action="store_true",
                   help="Allow removing an entry outside the vault group")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("move", aliases=["mv"], help="Move an entry to another group")
    p.add_argument("entry", help="Entry path")
    p.add_argument("dest", help="Destination group")
    p.add_argument("-f", "--force", action="store_true",
                   help="Allow moving an entry outside the vault group")
    p.set_defaults(func=cmd_move)

    p = sub.add_parser("git-credential",
                       help="git credential helper (delegates allowlisted hosts to GCM)")
    p.add_argument("op", choices=["get", "store", "erase"], help="git credential operation")
    p.set_defaults(func=cmd_git_credential)

    p = sub.add_parser("import-key", help="Import key pair into entry")
    p.add_argument("entry", help="Entry path")
    p.add_argument("key_file", help="Path to private key file (.pub must exist alongside)")
    p.set_defaults(func=cmd_import_key)

    p = sub.add_parser("export-key", help="Export key pair from entry")
    p.add_argument("entry", help="Entry path")
    p.add_argument("dest_dir", help="Destination directory")
    p.add_argument("key_name", help="Key filename (e.g. id_ed25519)")
    p.set_defaults(func=cmd_export_key)

    p = sub.add_parser("which", help="Show the resolved vault context")
    p.add_argument("--json", action="store_true", help="Print JSON")
    p.set_defaults(func=cmd_which)

    p = sub.add_parser("cache-populate", help="Pre-warm the cache for a set of entries")
    p.add_argument("--entry", action="append", metavar="PATH[:FIELD]",
                   help="An entry to cache (repeatable; FIELD defaults to password)")
    p.add_argument("--manifest", help="File of 'entry | field' lines to cache")
    p.add_argument("--machine", help="Machine hint passed to cache-source extensions")
    p.add_argument("--prompt", action="store_true",
                   help="Allow an interactive unlock prompt for the fetches")
    p.add_argument("--verify", action="store_true",
                   help="Only check the entries exist (no fetch/cache)")
    p.set_defaults(func=cmd_cache_populate)

    p = sub.add_parser("cache-clear", help="Wipe the persistent on-disk credential cache")
    p.set_defaults(func=cmd_cache_clear)

    p = sub.add_parser("cache-status", help="Show persistent cache status")
    p.add_argument("--json", action="store_true", help="Print JSON")
    p.set_defaults(func=cmd_cache_status)

    p = sub.add_parser("cache-verify",
                       help="Check the persistent cache holds a set of entries "
                            "(no unlock); exit 2 if any missing")
    p.add_argument("--entry", action="append", metavar="PATH[:FIELD]",
                   help="An entry to check (repeatable; FIELD defaults to password)")
    p.add_argument("--manifest", help="File of 'entry | field' lines to check")
    p.add_argument("--machine", help="Machine hint passed to cache-source extensions")
    p.add_argument("--json", action="store_true", help="Print JSON")
    p.set_defaults(func=cmd_cache_verify)

    p = sub.add_parser("vault", help="Manage named vaults")
    vault_sub = p.add_subparsers(dest="vault_command")

    vp = vault_sub.add_parser("list", help="List named vaults")
    vp.add_argument("--json", action="store_true", help="Print JSON")
    vp.set_defaults(func=cmd_vault_list)

    vp = vault_sub.add_parser("add", help="Add or update a named vault")
    vp.add_argument("name", help="Vault name")
    vp.add_argument("--kpdb", required=True, help="KeePass database path")
    vp.add_argument("--group", help="Default group")
    vp.add_argument("--port", type=int, help="Service TCP port")
    vp.set_defaults(func=cmd_vault_add)

    vp = vault_sub.add_parser("set-default", help="Set the default vault")
    vp.add_argument("name", help="Vault name")
    vp.set_defaults(func=cmd_vault_set_default)

    vp = vault_sub.add_parser("remove", help="Remove a named vault")
    vp.add_argument("name", help="Vault name")
    vp.set_defaults(func=cmd_vault_remove)

    from .extensions import get_registry
    get_registry().apply_cli_commands(sub)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1
    if args.command == "vault" and not getattr(args, "vault_command", None):
        p.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
