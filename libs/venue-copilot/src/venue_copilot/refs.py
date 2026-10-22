"""Reference files for a detached CLI-mode session (``copilot --detach --ref-file``).

An orchestrator hands the *worker* a file it should consult -- a HAR trace, a
session transcript, a log -- without reading it into its own context: it passes
only the host path. The file is copied into the venue outside the product
checkout (so it is never committed) and the worker is told where it is, in its
seed (new session) or in a message (running session).

The transfer is the same egress-free lane plugin staging uses
(agent-codespaces' plugin staging): tar+gzip in memory, base64 over
the SSH exec channel's **stdin**, so the command line stays tiny.
"""
from __future__ import annotations

import base64
import hashlib
import io
import shutil
import subprocess
import tarfile
import time
import uuid
import json
from dataclasses import dataclass
from typing import Literal
from collections.abc import Callable
from pathlib import Path

REFS_ROOT = "$HOME/.agent-bridge/refs"  # marketplace-isolation: allow agent-bridge-management
#: The payload travels in memory (tar.gz -> base64); larger inputs belong in a
#: repository or storage account the venue can reach itself.
MAX_REF_BYTES = 256 * 1024 * 1024


class RefFileError(ValueError):
    """A reference file cannot be sent (missing, too large)."""


def _size(path: Path) -> int:
    if path.is_dir():
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return path.stat().st_size


def _unique_names(paths: list[Path]) -> list[str]:
    names: list[str] = []
    for path in paths:
        stem, suffix, n = path.stem, path.suffix, 1
        name = path.name
        while name in names:
            n += 1
            name = f"{stem}-{n}{suffix}"
        names.append(name)
    return names


def build_refs_upload(raw_paths: list[str], batch: str) -> tuple[str, bytes, list[tuple[str, int]]]:
    """``(remote_command, stdin_bytes, [(name, size), ...])`` for one batch.

    The command extracts the files into ``<REFS_ROOT>/<batch>`` and prints that
    directory's absolute path (so the caller can name exact venue paths).
    """
    paths = [Path(p).expanduser() for p in raw_paths]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise RefFileError(f"reference file not found: {', '.join(missing)}")
    sizes = [_size(p) for p in paths]
    if sum(sizes) > MAX_REF_BYTES:
        raise RefFileError(
            f"reference files total {sum(sizes) // (1024 * 1024)} MiB; the limit is "
            f"{MAX_REF_BYTES // (1024 * 1024)} MiB -- share larger inputs through a "
            "location the venue can fetch itself"
        )
    names = _unique_names(paths)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, name in zip(paths, names):
            tf.add(str(path), arcname=name)
    dest = f"{REFS_ROOT}/{batch}"
    command = f'mkdir -p "{dest}" && base64 -d | tar -xzf - -C "{dest}" && cd "{dest}" && pwd'
    return command, base64.b64encode(buf.getvalue()), list(zip(names, sizes))


def _human(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024 or unit == "GB":
            break
    return f"{value:.1f} {unit}"


def refs_note(remote_dir: str, files: list[tuple[str, int]]) -> str:
    """The text that tells the worker where its reference files are."""
    lines = [
        "Reference files for this task, provided by the operator. They are outside "
        "the repository -- read them with your tools as the task needs, and never "
        "commit or paste them wholesale. If one is missing or unreadable, say so "
        "(BLOCKED: ...) instead of guessing its contents:",
    ]
    lines += [f"- {remote_dir}/{name} ({_human(size)})" for name, size in files]
    return "\n".join(lines)


def batch_id(scope: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-._" else "-" for c in scope)
    return f"{safe}-{time.strftime('%Y%m%d-%H%M%S')}"


def upload_for(raw_paths: list[str], scope: str) -> tuple[str, bytes, list[tuple[str, int]]] | None:
    """The batch upload for a session's ``--ref-file`` paths, or ``None`` when none."""
    return build_refs_upload(list(raw_paths), batch_id(scope)) if raw_paths else None


def send_refs(
    run_input: Callable[[str, bytes], tuple[int, str, str] | None],
    upload: tuple[str, bytes, list[tuple[str, int]]],
    progress: Callable[[str, str], None],
) -> str | None:
    """Copy one batch into the venue with ``run_input(command, stdin)``.

    Returns the worker-facing note, or ``None`` when the copy failed.
    """
    command, payload, files = upload
    progress("refs", f"copying {len(files)} reference file(s) to the venue")
    result = run_input(command, payload)
    if result is None or result[0] != 0 or not result[1].strip():
        progress("refs-failed", ((result[2] or f"exit {result[0]}") if result else "transport failure").strip()[-500:])
        return None
    return refs_note(result[1].strip().splitlines()[-1], files)


@dataclass(frozen=True)
class OwnedDelivery:
    status: Literal["admitted", "refused", "unknown"]
    operation: str
    session_id: str
    idempotency_key: str
    input_digest: str
    message_id: int | None = None

    def __bool__(self):
        return self.status == "admitted"

    def recovery(self) -> dict:
        return {**self.__dict__, "version": 1}


def _owned_input(note: str, context: dict) -> str:
    return json.dumps({"prompt": note, "plugin_context": context}, sort_keys=True, separators=(",", ":"))


def owned_result(value, *, session_id: str, operation: str, note: str, context: dict) -> OwnedDelivery:
    if isinstance(value, OwnedDelivery):
        return value
    # A legacy false/exception is not evidence that the server admitted nothing.
    return OwnedDelivery(
        "admitted" if value is True else "unknown", operation, session_id,
        "venue-startup-" + hashlib.sha256(operation.encode()).hexdigest()[:32],
        hashlib.sha256(_owned_input(note, context).encode()).hexdigest(),
    )


def deliver_note(
    session_id: str, note: str, *, run=subprocess.run, min_daemon_protocol: int | None = None,
    operation: str | None = None,
    prepared_context: dict | None = None,
    admission_unknown: bool = False,
) -> bool | OwnedDelivery:
    """Tell a running session about references; prepared sends return admission evidence.

    Steered into the running turn (not queued behind it, where a worker that is
    about to report DONE would never see it) and bounded, so a wedged bridge
    cannot block the rejoin that delivers it. ``min_daemon_protocol`` makes the
    send fail (deliver nothing) unless the host daemon speaks at least that
    protocol; an agent-bridge CLI too old to know the flag fails too.

    ``operation`` names this logical delivery (a launch passes its reservation
    id): a retry of it reuses the key -- whichever handle it names (a
    placeholder or the id it was renamed to: both reach the same session) --
    so the bridge answers an ambiguous earlier attempt with the original
    message instead of enqueueing the note -- or a seed -- twice. A later,
    separate delivery of the same text is a new operation with its own key, so
    it is really sent. Omitted, every call is its own operation.

    The raw path retains its Boolean API. The prepared path returns an
    ``OwnedDelivery`` and retries ambiguous transport once with identical input;
    a bare exit status cannot authorize destructive launch cleanup.
    """
    _bin = "agent-bridge"  # marketplace-isolation: allow legacy-compatibility
    bridge = shutil.which(_bin) or _bin
    op = operation or uuid.uuid4().hex
    key = ("venue-startup-" + hashlib.sha256(op.encode()).hexdigest()[:32]
           if prepared_context is not None else
           "venue-note-" + hashlib.sha256(f"{op}\0{note}".encode("utf-8")).hexdigest()[:32])
    argv = [bridge, "send", session_id, "--prompt-file", "-", "--no-wait",
            "--idempotency-key", key]
    stdin = note
    options = {}
    if prepared_context is not None:
        require_cli_context(prepared_context)
        argv += ["--plugin-context-stdin", "--delivery", "queue"]
        min_daemon_protocol = max(min_daemon_protocol or 0, 41)
        stdin = _owned_input(note, prepared_context)
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        argv.insert(6, "--steer")
    if min_daemon_protocol:
        argv += ["--min-daemon-protocol", str(min_daemon_protocol)]
    if prepared_context is not None:
        argv.insert(1, "--json")
        argv += ["--expected-session-id", session_id, "--sender", "venue-startup"]
        fingerprint = hashlib.sha256(stdin.encode()).hexdigest()
        unknown = admission_unknown
        for _attempt in range(2):
            try:
                result = run(argv, input=stdin, capture_output=True, text=True, timeout=150, **options)
                payload = json.loads(getattr(result, "stdout", "") or "{}")
            except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
                unknown = True
                continue
            if not isinstance(payload, dict):
                unknown = True
                continue
            receipt = payload.get("receipt") or {}
            if not isinstance(receipt, dict):
                unknown = True
                continue
            if (
                getattr(result, "returncode", 1) == 0 and payload.get("accepted") is True
                and type(payload.get("message_id")) is int and payload["message_id"] > 0
                and receipt.get("stage") in {"admitted", "sdk_accepted", "consumed"}
                and receipt.get("session_id") == payload.get("session_id")
            ):
                return OwnedDelivery("admitted", op, payload["session_id"], key, fingerprint, payload["message_id"])
            if (
                type(payload.get("owned_delivery_version")) is int and payload["owned_delivery_version"] == 1
                and payload.get("admission_status") == "not_admitted" and not unknown
            ):
                return OwnedDelivery("refused", op, session_id, key, fingerprint)
            unknown = True
        return OwnedDelivery("unknown", op, session_id, key, fingerprint)
    try:
        result = run(argv, input=stdin, capture_output=True, text=True,
                     timeout=60, **options)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return getattr(result, "returncode", 1) == 0


def owned_delivery_journal(
    session_id: str, note: str, context: dict, *, operation: str, plan: dict,
    reservation_id: str | None, creation_receipt: dict | None, daemon_port: int,
) -> dict:
    return {
        **owned_result(False, session_id=session_id, operation=operation, note=note, context=context).recovery(),
        "envelope": {"prompt": note, "plugin_context": context},
        "creation_receipt": creation_receipt, "reservation_id": reservation_id,
        "scope_id": plan["scope_id"], "mux_session": plan["mux_session"], "daemon_port": daemon_port,
    }


def reconcile_owned_delivery(pending: dict, *, run=subprocess.run) -> OwnedDelivery:
    envelope = pending["envelope"]
    expected = owned_result(
        False, session_id=pending["session_id"], operation=pending["operation"],
        note=envelope["prompt"], context=envelope["plugin_context"],
    )
    if expected.input_digest != pending["input_digest"] or expected.idempotency_key != pending["idempotency_key"]:
        raise ValueError("Pending delivery inputs changed; refusing to replay")
    if pending.get("status") == "admitted":
        message_id = pending.get("message_id")
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("Confirmed pending delivery has no valid message ID")
        return OwnedDelivery(
            "admitted", expected.operation, expected.session_id, expected.idempotency_key,
            expected.input_digest, message_id,
        )
    return deliver_note(
        pending["session_id"], envelope["prompt"], run=run, operation=pending["operation"],
        prepared_context=envelope["plugin_context"], admission_unknown=True,
    )


def require_cli_context(context: dict) -> dict:
    if (
        not isinstance(context, dict) or type(context.get("version")) is not int or context["version"] != 1
        or not isinstance(context.get("cwd"), str) or not context["cwd"]
        or not isinstance(context.get("plugins"), list)
    ):
        raise ValueError("Detached Bridge-owned launch requires compiled plugin metadata")
    if context.get("acp_only_sources"):
        raise ValueError("Native CLI registration has no marketplace origin: " + ", ".join(context["acp_only_sources"]))
    return context


def deferred_seed(embodied: dict, seed: str | None, operation: str) -> tuple[str | None, str, dict | None]:
    from . import SEED_NEVER_TYPED_REASONS
    if (
        embodied.get("bridge_seed_deferred") is not True or embodied.get("seed_submitted")
        or embodied.get("seeded") or embodied.get("seed_unconfirmed")
        or (embodied.get("seed_reason") and embodied["seed_reason"] not in SEED_NEVER_TYPED_REASONS)
    ):
        raise ValueError("Venue did not prove the Bridge-owned seed was never typed")
    receipt = embodied.get("deferred_creation_seed") or {}
    creation = receipt.get("seed")
    if creation:
        text = creation.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("Invalid deferred creation-seed receipt")
        seed = f"{seed.rstrip()}\n\n{text}" if seed else text
        operation = embodied["bridge_seed_operation"]
    return seed, operation, receipt if creation else None


def settle_deferred_seed(run_input, identity: str, workspace: str, receipt: dict | None) -> bool:
    if receipt is None:
        return True
    import shlex

    command = (
        f"cd {shlex.quote(workspace)} && agent-worktrees bridge-seed-settle "
        f"--worktree-id {shlex.quote(identity)}"
    )
    result = run_input(command, json.dumps({"receipt": receipt}).encode())
    return bool(result is not None and result[0] == 0)
