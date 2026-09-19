"""Read-only mode admission over the existing CodeSpace host-authority catalog."""

from __future__ import annotations

import inspect
import shlex


def check_catalog(catalog, requested_mode, execution_id="", generation="", probe=None):
    import json
    import os
    from pathlib import Path

    root = Path(catalog) if catalog is not None else Path.home() / ".agent-bridge" / "session-hosts"
    if not root.exists():
        return 0, "EXECUTION_MODE_CLEAR"
    if root.is_symlink():
        return 78, "Execution authority catalog is not a regular directory"

    def alive(pid, ticks, boot):
        try:
            current_boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            if not current_boot or not boot or not ticks:
                return None
            if current_boot != boot:
                return False
        except OSError:
            return None
        try:
            fields = Path(f"/proc/{int(pid)}/stat").read_text().rpartition(")")[2].split()
            if fields[0] in {"Z", "X"}:
                return False
            current = fields[19]
            return current == ticks
        except FileNotFoundError:
            return False
        except (OSError, ValueError, IndexError):
            return None

    probe = probe or alive
    pattern = "host-native-*.json" if requested_mode == "acp" else "host-*.json"
    paths = list(root.glob(pattern))
    if len(paths) > 1024:
        return 78, "Execution authority catalog exceeds the bounded inspection limit"
    for path in paths:
        try:
            if path.is_symlink() or path.stat().st_size > 65536:
                raise ValueError("unsafe authority record")
            state = json.loads(path.read_text(encoding="utf-8"))
            mode = state.get("mode", "acp")
            if (
                mode == "native" and state.get("session_id") == execution_id
                and state.get("execution_generation") == generation
            ):
                continue
            host = probe(state["host_pid"], state.get("host_start_ticks", ""), state.get("boot_id", ""))
            child = probe(state["child_pid"], state.get("child_start_ticks", ""), state.get("boot_id", ""))
            if child is True or (mode == "native" and host is True):
                return 75, f"A {mode} execution still owns this venue"
            if child is None or (mode == "native" and host is None):
                return 78, "Execution liveness is uncertain; refusing a replacement"
            if mode == "native":
                try:
                    os.killpg(int(state["child_pid"]), 0)
                    return 78, "Native process-group retirement is unconfirmed"
                except ProcessLookupError:
                    pass
                except OSError:
                    return 78, "Native process-group liveness is uncertain"
        except (OSError, ValueError, KeyError, TypeError):
            return 78, "Execution authority is unreadable; refusing a replacement"
    return 0, "EXECUTION_MODE_CLEAR"


def command(requested_mode: str, execution_id: str = "", generation: str = "") -> str:
    program = inspect.getsource(check_catalog)
    program += (
        f"\ncode,detail=check_catalog(None,{requested_mode!r},{execution_id!r},{generation!r})\n"
        "print(detail)\nraise SystemExit(code)\n"
    )
    return "# execution-mode-guard\npython3 -c " + shlex.quote(program)


def admit_and_publish(path, state: dict, requested_mode: str, execution_id="", generation="") -> None:
    """Serialize remote mode inspection and provisional authority publication."""
    import fcntl
    import os
    from pathlib import Path
    from .launcher import _write_host_state

    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise RuntimeError("execution authority directory cannot be a symlink")
    fd = os.open(path.parent / ".execution-admission.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if requested_mode == "native" and path.exists():
            import json
            previous = json.loads(path.read_text(encoding="utf-8"))
            if previous.get("nonce") != state.get("nonce"):
                raise RuntimeError("native authority already belongs to another launch")
        code, detail = check_catalog(path.parent, "native", execution_id, generation)
        if code:
            raise RuntimeError(detail)
        _write_host_state(path, state)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
