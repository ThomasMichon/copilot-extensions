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

    response = read("record", "--cwd", str(directory), "--json", structured=True)
    if (not isinstance(response, dict) or response.get("ok") is not True
            or type(response.get("version")) is not int or response["version"] != 1):
        _refuse("workspace_response_invalid")
    context = response.get("context")
    if (not isinstance(context, dict)
            or context.get("schema") != "agent-worktrees.workspace-context"
            or type(context.get("version")) is not int or context["version"] != 1
            or not _key(context.get("project"))):
        _refuse("workspace_response_invalid")
    anchor = _directory(context.get("anchor"), "workspace_unresolved")
    root = _directory(context.get("root"), "workspace_unresolved")
    if not directory.is_relative_to(root):
        _refuse()
    label = None
    head = None
    owner_ref = None
    if root == anchor:
        identity = context.get("identity")
        if (not isinstance(identity, dict)
                or context.get("worktree") is not None
                or identity.get("schema") != "agent-worktrees.execution-identity"
                or type(identity.get("version")) is not int or identity["version"] != 1
                or identity.get("status") != "ready" or identity.get("configured") is not True
                or not isinstance(identity.get("source"), str)
                or identity["source"] not in {"project-machine-config", "global-machine-config"}
                or not all(_key(identity.get(key)) for key in ("machine", "project"))
                or identity["project"] != context["project"]
                or _platform(identity.get("platform")) is None):
            _refuse()
        participant = {
            "machine": identity["machine"], "repo": identity["project"],
            "kind": "anchor", "id": session_id,
        }
    else:
        row = context.get("worktree")
        if not isinstance(row, dict):
            _refuse("workspace_response_invalid")
        worktree_id = row.get("id")
        row_owner = row.get("machine")
        tuple_fields = {"machine_identity_version", "environment", "execution_machine"}
        if tuple_fields & row.keys():
            if (type(row.get("machine_identity_version")) is not int
                    or row["machine_identity_version"] != 1
                    or not _key(row.get("machine")) or not _key(row.get("execution_machine"))
                    or _platform(row.get("environment")) != _platform(row.get("platform"))):
                _refuse()
            row_owner = row["execution_machine"]
        if (not _key(worktree_id) or not _key(row_owner)
                or row.get("repo") != context["project"]
                or _platform(row.get("platform")) is None
                or _directory(row.get("path"), "workspace_unresolved") != root):
            _refuse()
        participant = {
            "machine": row_owner, "repo": row["repo"], "kind": "worktree", "id": worktree_id,
        }
        owner_ref = "/".join((row_owner, row["repo"], worktree_id))
        label = row.get("codename")
        if label is not None and (not isinstance(label, str) or _LABEL.fullmatch(label) is None):
            _refuse("workspace_response_invalid")
        observed = row.get("head")
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
