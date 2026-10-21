"""Optional read-only workspace context from explicitly selected Worktrees."""

from __future__ import annotations

import re
from pathlib import Path
import sys
import time

from .client import ClientError
from .store import ChatError, strict_json
from .transport import command, run_command

_KEY = re.compile(r"[A-Za-z0-9_.-]{1,256}")
_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_LABEL = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_CWD_RUNNER = (
    "import os,subprocess,sys; os.chdir(sys.argv[1]); "
    "os.environ.pop('PYTHONPATH',None); "
    "sys.exit(subprocess.call(sys.argv[2:],stdin=subprocess.DEVNULL,"
    "stdout=sys.stdout.buffer,stderr=sys.stderr.buffer,"
    "creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0))"
)


def _refuse(code="workspace_unresolved"):
    raise ClientError(code, "Trusted workspace context could not be established; no adoption performed.")


def _key(value):
    return isinstance(value, str) and _KEY.fullmatch(value) is not None


def _directory(value, code):
    if (not isinstance(value, str) or not value or len(value) > 4096
            or any(c in value for c in "\0\r\n") or not Path(value).is_absolute()):
        _refuse(code)
    try:
        path = Path(value).resolve(strict=True)
        if not path.is_dir():
            _refuse(code)
        return path
    except (OSError, ValueError, RuntimeError):
        _refuse(code)


def _platform(value):
    return {"win": "windows", "windows": "windows", "linux": "linux", "wsl": "wsl"}.get(
        value if isinstance(value, str) else None,
    )


def resolve_workspace(*, worktrees_command, session_id, cwd):
    """Consume host initialization once; return no Chat actor or inbox authority."""
    if not isinstance(session_id, str) or _SESSION.fullmatch(session_id) is None:
        _refuse("invalid_initialization")
    directory = _directory(cwd, "invalid_initialization")
    owner = command(worktrees_command)
    deadline = time.monotonic() + 60

    def read(*args, structured=False):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _refuse("workspace_timeout")
        raw = run_command(
            [sys.executable, "-I", "-c", _CWD_RUNNER, str(directory), *owner, *args],
            timeout=remaining, max_capture=65536,
        )
        if time.monotonic() >= deadline:
            _refuse("workspace_timeout")
        try:
            return strict_json(raw) if structured else raw.decode("utf-8").strip()
        except (ChatError, ValueError, UnicodeError):
            _refuse("workspace_response_invalid")

    anchor = _directory(read("get", "repo-dir"), "workspace_unresolved")
    worktree_text = read("get", "worktree-dir")
    root = _directory(worktree_text, "workspace_unresolved") if worktree_text else anchor
    if not directory.is_relative_to(root):
        _refuse()
    label = None
    head = None
    owner_ref = None
    if root == anchor:
        identity = read("get", "execution-identity", structured=True)
        if (not isinstance(identity, dict)
                or identity.get("schema") != "agent-worktrees.execution-identity"
                or type(identity.get("version")) is not int or identity["version"] != 1
                or identity.get("status") != "ready" or identity.get("configured") is not True
                or not isinstance(identity.get("source"), str)
                or identity["source"] not in {"project-machine-config", "global-machine-config"}
                or not all(_key(identity.get(key)) for key in ("machine", "project"))
                or _platform(identity.get("platform")) is None):
            _refuse()
        participant = {
            "machine": identity["machine"], "repo": identity["project"],
            "kind": "anchor", "id": session_id,
        }
    else:
        owner_ref = read("get", "owner-ref")
        parts = owner_ref.split("/")
        if len(parts) != 3 or not all(_key(part) for part in parts):
            _refuse()
        worktree_id = parts[2]
        participant = {"machine": parts[0], "repo": parts[1], "kind": "worktree", "id": worktree_id}
        listing = read("list", "--json", "--cache-only", "--worktree-id", worktree_id,
                       structured=True)
        if (not isinstance(listing, dict) or type(listing.get("version")) is not int
                or listing["version"] != 1 or not isinstance(listing.get("worktrees"), list)
                or len(listing["worktrees"]) != 1):
            _refuse("workspace_response_invalid")
        row = listing["worktrees"][0]
        if not isinstance(row, dict):
            _refuse("workspace_response_invalid")
        row_owner = row.get("machine")
        tuple_fields = {"machine_identity_version", "environment", "execution_machine"}
        if tuple_fields & row.keys():
            if (type(row.get("machine_identity_version")) is not int
                    or row["machine_identity_version"] != 1
                    or not _key(row.get("machine")) or not _key(row.get("execution_machine"))
                    or _platform(row.get("environment")) != _platform(row.get("platform"))):
                _refuse()
            row_owner = row["execution_machine"]
        if (row.get("id") != worktree_id or row_owner != participant["machine"]
                or row.get("repo") != participant["repo"]
                or _platform(row.get("platform")) is None
                or _directory(row.get("path"), "workspace_unresolved") != root):
            _refuse()
        label = row.get("codename")
        if label is not None and (not isinstance(label, str) or _LABEL.fullmatch(label) is None):
            _refuse("workspace_response_invalid")
        observed = read("head-session", "--worktree-id", worktree_id, "--json", structured=True)
        if (not isinstance(observed, dict) or type(observed.get("version")) is not int
                or not {"head_session", "state"} <= observed.keys()
                or observed["version"] != 1 or observed.get("worktree_id") != worktree_id
                or observed.get("tracked") is not True
                or type(observed.get("active")) is not bool
                or type(observed.get("head_revision")) is not int or observed["head_revision"] < 0
                or not isinstance(observed.get("pending_handoffs"), list)):
            _refuse("workspace_response_invalid")
        head_session = observed.get("head_session")
        if head_session is None:
            if observed["active"] or observed.get("state") is not None:
                _refuse("workspace_response_invalid")
        elif (not isinstance(head_session, str) or _SESSION.fullmatch(head_session) is None
              or not observed["active"] or observed.get("state") != "active"):
            _refuse("workspace_response_invalid")
        head = {
            "session_id": head_session, "revision": observed["head_revision"],
            "is_initializing_session": head_session == session_id,
            "pending_handoff": bool(observed["pending_handoffs"]),
        }
    return {
        "schema": "agent-chat.workspace-context", "version": 1,
        "session_id": session_id, "cwd": str(directory),
        "participant": participant, "display_name": label,
        "owner_ref": owner_ref, "head": head,
        "assignment": {"status": "unavailable", "reason": "chat-assignment-binding-required"},
        "mutations_performed": False, "native_listening_verified": False,
    }
