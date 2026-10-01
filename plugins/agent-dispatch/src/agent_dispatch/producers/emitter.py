"""Lease-gated periodic command emitters.

An emitter spec names a command and cadence. The singleton supervisor owns the
emitter process; this module owns its repeated ticks and the cross-host job lease
that ensures only one eligible supervisor invokes the command.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs

from ..client import DispatchClient


class EmitterError(ValueError):
    """A malformed command-emitter spec."""


def validate_spec(spec: dict[str, Any]) -> None:
    """Validate a periodic command-emitter spec."""
    if not isinstance(spec, dict):
        raise EmitterError("emitter spec must be a JSON object")
    if not isinstance(spec.get("id"), str) or not spec["id"]:
        raise EmitterError("command emitter needs a non-empty 'id'")
    command = spec.get("command")
    builtin = spec.get("repository_issue_loop")
    if command is None and builtin is None:
        raise EmitterError(
            "emitter needs a 'command' or 'repository_issue_loop' configuration"
        )
    if command is not None and (
        not isinstance(command, list)
        or not command
        or any(not isinstance(part, str) or not part for part in command)
    ):
        raise EmitterError("'command' must be a non-empty list of non-empty strings")
    for key in ("cwd", "lease_scope", "holder_session"):
        value = spec.get(key)
        if value is not None and (not isinstance(value, str) or not value):
            raise EmitterError(f"'{key}' must be a non-empty string")
    if builtin is not None:
        if command is not None:
            raise EmitterError(
                "'command' and 'repository_issue_loop' are mutually exclusive"
            )
        if not isinstance(builtin, dict):
            raise EmitterError("'repository_issue_loop' must be an object")
        from ..repository_issue_loops import validate_config

        try:
            validate_config(builtin, cwd=spec.get("cwd"))
        except ValueError as exc:
            raise EmitterError(str(exc)) from exc
    try:
        interval = float(spec.get("interval_seconds"))
    except (TypeError, ValueError) as exc:
        raise EmitterError("'interval_seconds' must be a number > 0") from exc
    if interval <= 0:
        raise EmitterError("'interval_seconds' must be > 0")
    timeout = spec.get("timeout_seconds")
    if timeout is not None:
        try:
            if float(timeout) <= 0:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise EmitterError("'timeout_seconds' must be a number > 0") from exc
    env = spec.get("env")
    if env is not None and (
        not isinstance(env, dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in env.items()
        )
    ):
        raise EmitterError("'env' must be an object of string keys and values")
    evaluator_ref = spec.get("evaluator_ref")
    if evaluator_ref is not None and (
        not isinstance(evaluator_ref, str) or not evaluator_ref
    ):
        raise EmitterError("'evaluator_ref' must be a non-empty string")
    require_verification = spec.get("require_verification")
    if require_verification is not None and not isinstance(require_verification, bool):
        raise EmitterError("'require_verification' must be true/false")
    task_output = spec.get("task_output")
    if task_output not in (None, "json"):
        raise EmitterError("'task_output' must be 'json' when present")
    source = spec.get("source")
    if source is not None and (not isinstance(source, str) or not source):
        raise EmitterError("'source' must be a non-empty string")
    side_load = spec.get("side_load")
    if side_load is not None:
        if not isinstance(side_load, dict):
            raise EmitterError("'side_load' must be an object")
        command = side_load.get("command")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(part, str) or not part for part in command)
        ):
            raise EmitterError(
                "'side_load.command' must be a non-empty list of non-empty strings"
            )
        if not any("{change_ref}" in part for part in command):
            raise EmitterError(
                "'side_load.command' must include a {change_ref} placeholder"
            )


def load_spec(path: str | Path) -> dict[str, Any]:
    """Load and validate a command-emitter JSON spec."""
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    validate_spec(data)
    return data


def lease_scope(spec: dict[str, Any]) -> str:
    """Return the stable single-producer lease scope for ``spec``."""
    return str(spec.get("lease_scope") or f"emitter:{spec['id']}")


def _render_command(command: list[str], *, change_ref: str | None = None) -> list[str]:
    values = {
        "{change_ref}": change_ref or "",
        "{python}": sys.executable,
    }
    rendered = []
    for part in command:
        for token, value in values.items():
            part = part.replace(token, value)
        rendered.append(part)
    return rendered


def _task_specs(stdout: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(stdout)
    except ValueError as exc:
        raise EmitterError(f"emitter task output is not valid JSON: {exc}") from exc
    rows = value if isinstance(value, list) else [value]
    if any(not isinstance(row, dict) for row in rows):
        raise EmitterError("emitter task output must be an object or list")
    for row in rows:
        if not isinstance(row.get("title"), str) or not row["title"]:
            raise EmitterError("each emitted task needs a non-empty 'title'")
        if row.get("dedup_key") is not None and not isinstance(
            row["dedup_key"], str
        ):
            raise EmitterError("emitted task 'dedup_key' must be a string")
    return rows


def _author_tasks(
    client: DispatchClient,
    spec: dict[str, Any],
    stdout: str,
) -> list[dict[str, Any]]:
    created = []
    for row in _task_specs(stdout):
        fields = dict(row)
        title = fields.pop("title")
        fields["source"] = spec.get("source") or "emitter"
        fields["origin_ref"] = spec["id"]
        fields["evaluator_ref"] = spec.get("evaluator_ref")
        fields.setdefault(
            "require_verification", bool(spec.get("require_verification", False))
        )
        created.append(client.create(title, **fields))
    return created


# Receipts: a durable, per-emitter record of every task a command-emitter
# caused to be created (keyed by the same ``dedup_key`` it emitted), so the
# domain's own command can learn the resulting dispatch task id on a LATER
# invocation -- `_author_tasks`'s ``created`` list otherwise exists for
# exactly one tick and is then gone (see the `agent-dispatch-emitter-receipts`
# effort). Keyed by the emitter's declared ``id`` under the shared install
# root (``install_dir()``) rather than a path next to its spec FILE: a
# side-loaded emitter's spec lives only inside a coordinator registration,
# with no local file to sit beside, so an id-keyed location is the one sink
# both the ``tick``/``serve`` and ``side-load`` paths can share.


def _sanitized_emitter_id(emitter_id: str) -> str:
    """A filesystem-safe directory name for one emitter id."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in emitter_id)


def receipts_path(emitter_id: str) -> Path:
    """Return the durable receipts sidecar path for one emitter id."""
    from ..install_paths import install_dir

    return (
        install_dir() / "emitters" / _sanitized_emitter_id(emitter_id) / "receipts.jsonl"
    )


DEFAULT_MAX_RECEIPTS = 2000


def _read_receipt_lines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return [line for line in text.splitlines() if line.strip()]


def _append_receipts(
    emitter_id: str,
    created: list[dict[str, Any]],
    *,
    tick_id: str | None = None,
    clock: Callable[[], float] = time.time,
    max_receipts: int = DEFAULT_MAX_RECEIPTS,
) -> None:
    """Append one durable receipt per created task, then bound the log.

    Each receipt carries a strictly-increasing ``seq`` so a reader's cursor
    stays valid across a later trim (a trim only drops already-old records;
    it never renumbers the ones that remain) -- unlike a line-position
    cursor, which a trim would silently invalidate.
    """
    if not created:
        return
    path = receipts_path(emitter_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_receipt_lines(path)
    next_seq = json.loads(existing[-1])["seq"] + 1 if existing else 1
    now = clock()
    new_lines = []
    for task in created:
        record = {
            "seq": next_seq,
            "ts": now,
            "tick_id": tick_id,
            "dedup_key": task.get("dedup_key"),
            "task_id": task.get("id"),
            "status": task.get("status"),
        }
        new_lines.append(json.dumps(record, separators=(",", ":"), sort_keys=True))
        next_seq += 1
    all_lines = existing + new_lines
    if len(all_lines) > max_receipts:
        all_lines = all_lines[-max_receipts:]
    path.write_text("\n".join(all_lines) + "\n", encoding="utf-8")


def read_receipts(
    emitter_id: str,
    *,
    since: int = 0,
) -> dict[str, Any]:
    """Return receipts with ``seq > since`` and the cursor for the next read.

    ``since`` defaults to 0 (read everything recorded so far). The returned
    ``cursor`` is the highest ``seq`` seen -- pass it back as ``since`` on the
    next call to read only new receipts. A cursor is stable even if the log
    has since been trimmed: trimming only drops already-consumed low-``seq``
    records, it never reuses or shifts a ``seq`` value.
    """
    path = receipts_path(emitter_id)
    lines = _read_receipt_lines(path)
    records = [json.loads(line) for line in lines]
    new_records = [r for r in records if int(r.get("seq") or 0) > since]
    cursor = max((int(r.get("seq") or 0) for r in records), default=since)
    return {"receipts": new_records, "cursor": cursor}



RECEIPTS_PATH_ENV = "AGENT_DISPATCH_EMITTER_RECEIPTS_PATH"


def run_side_load(
    client: DispatchClient,
    registration: dict[str, Any],
    change_ref: str,
    *,
    current_machine: str | None = None,
    current_env: str = "default",
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Run one registered emitter's on-demand path and author its tasks."""
    if registration.get("kind") != "emitter":
        raise EmitterError("side-load requires an emitter registration")
    owner_machine = registration.get("machine")
    owner_env = str(registration.get("env") or "default")
    if owner_machine and current_machine != owner_machine:
        raise EmitterError(
            f"emitter registration belongs to machine {owner_machine!r}; "
            f"run side-load on that host"
        )
    if owner_env != current_env:
        raise EmitterError(
            f"emitter registration belongs to environment {owner_env!r}; "
            f"current environment is {current_env!r}"
        )
    spec = dict(registration.get("spec") or {})
    validate_spec(spec)
    side_load = spec.get("side_load")
    if not side_load:
        raise EmitterError(f"emitter {spec['id']!r} does not declare side_load")
    completed = runner(
        _render_command(side_load["command"], change_ref=change_ref),
        cwd=spec.get("cwd"),
        env={
            **os.environ,
            **(spec.get("env") or {}),
            RECEIPTS_PATH_ENV: str(receipts_path(spec["id"])),
        },
        timeout=spec.get("timeout_seconds"),
        check=False,
        capture_output=True,
        text=True,
        **no_window_kwargs(),
    )
    if int(completed.returncode) != 0:
        raise EmitterError(
            f"side-load command exited {completed.returncode}: "
            f"{str(completed.stderr or '').strip()}"
        )
    created = _author_tasks(client, spec, str(completed.stdout or ""))
    _append_receipts(spec["id"], created, tick_id=f"side-load:{change_ref}")
    return {
        "registration_id": registration.get("id"),
        "emitter_id": spec["id"],
        "change_ref": change_ref,
        "created": created,
    }


def run_tick(
    client: DispatchClient,
    spec: dict[str, Any],
    *,
    holder: str,
    runner: Callable[..., Any] = subprocess.run,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Acquire/renew the emitter lease and invoke one command tick when held."""
    validate_spec(spec)
    scope = lease_scope(spec)
    lease = client.acquire_schedule_lease(
        scope,
        holder,
        holder_session=spec.get("holder_session"),
        ttl=spec.get("lease_ttl"),
    )
    if not lease.get("granted"):
        return {
            "held": False,
            "lease": lease.get("lease"),
            "scope": scope,
            "created": [],
        }

    env = {
        **os.environ,
        **(spec.get("env") or {}),
        RECEIPTS_PATH_ENV: str(receipts_path(spec["id"])),
    }
    started_at = clock()
    try:
        if spec.get("repository_issue_loop") is not None:
            from ..repository_issue_loops import run_tick as run_issue_tick

            result = run_issue_tick(
                client,
                spec["repository_issue_loop"],
                clock=clock,
                cwd=spec.get("cwd"),
            )
            return {
                "held": True,
                "lease": lease.get("lease"),
                "scope": scope,
                "returncode": 0,
                "error": None,
                "created": result.get("created", []),
                "result": result,
                "duration_seconds": max(0.0, clock() - started_at),
            }
        task_output_json = spec.get("task_output") == "json"
        completed = runner(
            _render_command(spec["command"]),
            cwd=spec.get("cwd"),
            env=env,
            timeout=spec.get("timeout_seconds"),
            check=False,
            # For the JSON task-output protocol, capture cleanly through a
            # pipe -- ``_author_tasks`` needs the exact bytes. Otherwise
            # route the command's own stdout/stderr DIRECTLY to THIS
            # process's own stderr at the OS level (real streaming, no
            # Python-side buffering, nothing lost on a timeout kill) --
            # never let it inherit our stdout, which is reserved
            # exclusively for ``_emit()``'s later JSON result: a
            # subprocess-based caller (a serve loop shelling out to
            # ``emitter tick``) treats the whole captured stdout stream as
            # one payload, and a command's own output landing there would
            # interleave with and corrupt it.
            stdout=subprocess.PIPE if task_output_json else sys.stderr,
            stderr=subprocess.PIPE if task_output_json else sys.stderr,
            text=task_output_json,
            **no_window_kwargs(),
        )
        returncode = int(completed.returncode)
        error = None
        created = (
            _author_tasks(client, spec, str(completed.stdout or ""))
            if returncode == 0 and task_output_json
            else []
        )
        _append_receipts(spec["id"], created, tick_id=f"{holder}:{started_at}", clock=clock)
    except subprocess.TimeoutExpired as exc:
        returncode = None
        error = f"timed out after {exc.timeout}s"
        created = []
    except OSError as exc:
        returncode = None
        error = str(exc)
        created = []
    return {
        "held": True,
        "lease": lease.get("lease"),
        "scope": scope,
        "returncode": returncode,
        "error": error,
        "created": created,
        "duration_seconds": max(0.0, clock() - started_at),
    }


def _parse_tick_process_output(completed: Any) -> dict[str, Any]:
    """Turn a completed ``agent-dispatch emitter tick`` subprocess into the
    same result shape :func:`run_tick` returns in-process.

    ``emitter tick`` always prints a well-formed JSON result (a dict with at
    least a ``held`` bool) to stdout on a clean run. Anything else on a
    zero exit -- empty/malformed/truncated stdout -- is itself a protocol
    violation, not a vacuous success; treat it as an error rather than
    silently normalizing it to an idle/empty tick, which would otherwise
    lose the failure entirely and report ``ok: true``. A hard failure (the
    coordinator connection itself refused, a misconfigured ``--shared``,
    ...) exits non-zero with no JSON on stdout -- its own diagnostic
    already streamed live to our own inherited stderr (see ``serve``'s own
    subprocess call), so only a generic, exit-code-keyed message is
    synthesized here rather than trying to re-capture and re-embed it.
    """
    stdout = getattr(completed, "stdout", None)
    if stdout:
        try:
            parsed = json.loads(stdout)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("held"), bool):
            return parsed
    returncode = getattr(completed, "returncode", None)
    return {
        "error": (
            f"emitter tick produced no valid JSON result (exit {returncode})"
        )
    }


def serve(
    spec_path: str | Path,
    *,
    holder: str,
    cli_argv: list[str] | None = None,
    on_tick: Callable[[dict[str, Any]], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    """Reload and tick a command emitter on its declared cadence.

    Each tick runs this emitter's OWN ``agent-dispatch emitter tick`` CLI
    command in a **fresh subprocess** -- it never builds or holds a
    :class:`DispatchClient` in-process across the sleep boundary. This is
    deliberate, not an efficiency accident: the coordinator's address is an
    internal, invocation-time detail of ``agent-dispatch`` itself
    (``_resolve_client_target``/``client_url()`` already re-derive it fresh
    on every CLI invocation, proven correct for every one-shot command) --
    a long-lived Python loop should never be the thing responsible for
    remembering it, any more than any other caller of the CLI would be. A
    prior version of this loop resolved the coordinator's URL/token once at
    its own process startup (or later, once per tick via an in-process
    resolver callback) and reused that connection knowledge across an
    otherwise coordinator-agnostic loop; either way, a coordinator restart
    onto a new ephemeral OS-assigned port between ticks was one more thing
    this module had to know how to recover from. Shelling out per tick
    removes that responsibility entirely: every tick gets the exact
    discovery a fresh, one-shot ``agent-dispatch emitter tick`` invocation
    would get, because it *is* one -- there is no coordinator-shaped state
    inside this loop at all for a restart to strand.

    ``cli_argv`` is the ``agent-dispatch`` argv prefix to reuse for every
    tick (e.g. ``[sys.executable, "-m", "agent_dispatch", "--shared"]`` when
    the parent invocation targeted ``--shared``/``--url``/``--token``) --
    defaults to ``[sys.executable, "-m", "agent_dispatch"]`` (plain local
    discovery, identical to running the CLI with no override flags).
    """
    argv = list(cli_argv) if cli_argv is not None else [sys.executable, "-m", "agent_dispatch"]
    tick_argv = [*argv, "emitter", "tick", str(spec_path), "--holder", holder]

    def _default_on_tick(result: dict[str, Any]) -> None:
        if result.get("error"):
            print(
                f"agent-dispatch emitter: tick failed: {result['error']}",
                file=sys.stderr,
            )
        elif not result.get("held"):
            print(
                f"agent-dispatch emitter: lease {result.get('scope')!r} held by "
                f"{(result.get('lease') or {}).get('holder')!r} -- idling",
                file=sys.stderr,
            )
        else:
            print(
                f"agent-dispatch emitter: tick returncode={result.get('returncode')} "
                f"duration={result.get('duration_seconds', 0.0):.3f}s",
                file=sys.stderr,
            )

    report = on_tick or _default_on_tick
    health_path = Path(spec_path).with_suffix(".health.json")
    while True:
        interval = 60.0
        try:
            spec = load_spec(spec_path)
            interval = float(spec["interval_seconds"])
            # No outer timeout here: the forked ``emitter tick`` process
            # itself needs time beyond the spec's own command timeout for
            # its own startup (coordinator discovery, lease acquire) before
            # its configured command even starts running -- `run_tick`
            # inside that process already applies `timeout_seconds` to just
            # the command it launches and returns a clean timed-out result
            # either way. Racing an identical timeout at this outer layer
            # would fire before that inner handling gets a chance to,
            # forcibly killing the wrapper process and potentially leaving
            # its own grandchild command running/overlapping the next tick.
            #
            # Only stdout is piped (captured for JSON parsing) -- stderr is
            # deliberately left to inherit ours directly (real-time OS-level
            # streaming, no Python-side buffering at either this layer or
            # inside the forked process itself, and nothing lost if a
            # timeout ever kills the process mid-output). The forked
            # process's own launched command output (when not itself
            # emitting the JSON task protocol) is routed straight to ITS
            # stderr the same way -- see run_tick's own stdout/stderr
            # handling -- so it flows straight through here too.
            completed = runner(
                tick_argv,
                stdout=subprocess.PIPE,
                text=True,
                check=False,
                **no_window_kwargs(),
            )
            result = _parse_tick_process_output(completed)
            report(result)
            health_path.write_text(
                json.dumps(
                    {"updated_at": time.time(), "ok": not result.get("error"), **result},
                    default=str,
                ),
                encoding="utf-8",
            )
        except KeyboardInterrupt:
            return
        except Exception as exc:
            print(f"agent-dispatch emitter: tick failed: {exc}", file=sys.stderr)
            try:
                health_path.write_text(
                    json.dumps(
                        {"updated_at": time.time(), "ok": False, "error": str(exc)}
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass
        try:
            sleep(interval)
        except KeyboardInterrupt:
            return
