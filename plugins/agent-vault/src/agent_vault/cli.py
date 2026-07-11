"""Command-line interface for agent-vault."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import config
from .config import IS_WINDOWS, SOCKET_PATH, ResolvedVault
from .prompt import prompt_password as gui_prompt_password


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


def _send_socket(request: dict, timeout: float | None = 5.0) -> dict | None:
    """Try sending a command via Unix socket."""
    import socket as _sock

    connect_timeout = 5.0
    try:
        s = _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM)
        s.settimeout(connect_timeout)
        s.connect(SOCKET_PATH)
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

    if not IS_WINDOWS and context.sources.get("port") == "default":
        result = _send_socket(request, timeout)
        if result is not None:
            return _tag(result, "unix-socket")

    host = os.environ.get("AGENT_VAULT_HOST", "127.0.0.1")
    port = context.port
    result = _send_tcp(request, host, port, timeout)
    if result is not None:
        return _tag(result, "tcp")

    from .extensions import TransportContext, get_registry

    ext_ctx = TransportContext(
        kpdb=context.kpdb,
        group=context.group,
        vault_name=context.vault_name,
        port=port,
    )
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
    """Prompt and send master password to unlock CLI backend."""
    if os.environ.get("VAULT_UNLOCK_TERMINAL"):
        return _terminal_unlock_local()
    if IS_WSL:
        print("[agent-vault] Requesting password via vault service...", file=sys.stderr)
        return _server_prompted_unlock()
    pw = prompt_password()
    if not pw:
        return False
    resp = send_command({"action": "unlock", "password": pw}, timeout=15.0)
    if resp and resp.get("ok"):
        return True
    if resp:
        print(f"Unlock failed: {resp.get('error', 'unknown')}", file=sys.stderr)
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

    if not ensure_service():
        print("Error: could not start vault service", file=sys.stderr)
        return 1

    resp = send_command({"action": "get", "entry": entry, "field": field}, timeout=None)
    if resp and resp.get("ok"):
        print(resp["value"])
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
