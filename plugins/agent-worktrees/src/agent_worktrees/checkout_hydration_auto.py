"""Automatic post-exit dehydration and hydrate-before-entry (Phase 4).

Two halves share the Phase 3 owner (:mod:`checkout_hydration`) and its gates:

* **Entry hydration.** Managed mux, Bridge, Picker and embody entry call
  :func:`entry_refusal` before any path-dependent policy, trust, projection or
  process-CWD use. It publishes a short *entry lease* and then rehydrates a
  dehydrated checkout (and a harness record's knowledge sibling) under the
  owner's lifecycle/admission locks. Previews and pure inspection never call it.
* **Automatic dehydration.** Post-exit only *queues* a candidate marker and
  wakes the resident status monitor; its bounded maintenance pass
  (:mod:`checkout_hydration_resident`) later re-confirms process retirement and
  calls the same :func:`checkout_hydration.dehydrate`, whose final pre-removal
  check re-confirms it again inside the lifecycle/admission locks. It is off
  unless the machine-local project config sets
  ``checkout_dehydration: {mode: automatic}``.

The lease closes the entry/dehydrate race without a lock on the hot entry
path: entry writes the lease, withdraws any queued candidate and reads the
catalog; dehydration publishes its catalog entry, then reads the lease and (for
automatic requests) re-checks its candidate generation. At least one side always observes
the other. Entry releases the lease as soon as it finishes or is refused, so a
later manual dehydrate does not wait; ``ENTRY_LEASE_TTL`` only bounds a lease a
crashed entry left behind. After entry, the launch's registered session and
live checkout processes are what the dehydrate gates observe.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, TypeVar

from . import checkout_hydration as hydration
from . import checkout_hydration_catalog as catalog
from . import config as cfg

_T = TypeVar("_T")

POLICY_KEY = "checkout_dehydration"
MODE_MANUAL = "manual"
MODE_AUTOMATIC = "automatic"
ENTRY_LEASE = "hydration-entry.json"
CANDIDATE = "dehydration-candidate.json"
PAIR_READINESS = hydration.PAIR_READINESS
# Long enough to cover entry -> session registration; dehydration retries later.
ENTRY_LEASE_TTL = 120.0
ENTRY_BUSY_WAIT = 30.0
MAX_ATTEMPTS = 24
PENDING_LIMIT = 3
RECONCILE_LIMIT = 3
RECONCILE_STATE = ".hydration-reconcile.json"
HOLD_BACKOFF = 3600.0
# Refusals that a later sweep can clear; others drop the candidate.
TRANSIENT = frozenset({
    "live-session", "live-process", "entry-pending", "busy", "pending-launch",
    "catalog-unavailable", "status-unavailable", "probes-unavailable", "record-unreadable",
})
CANDIDATE_LOCK_TIMEOUT = 10.0


# -- policy ---------------------------------------------------------------


def policy_mode(repo_name: str | None) -> str:
    """``automatic`` only when the machine-local project config says so exactly.

    Unreadable or malformed configuration means ``manual``: never remove a
    checkout on uncertain intent. Manual never disables entry hydration.
    """
    import yaml

    try:
        raw = (cfg.project_dir(repo_name) / "config.yaml").read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
    except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError):
        return MODE_MANUAL
    section = data.get(POLICY_KEY) if isinstance(data, dict) else None
    mode = section.get("mode") if isinstance(section, dict) else None
    return MODE_AUTOMATIC if mode == MODE_AUTOMATIC else MODE_MANUAL


# -- state files ----------------------------------------------------------


def _state_dir(ledger: Path) -> Path:
    """The per-worktree external state dir shared with launch-seed/admission."""
    return ledger.parent / ledger.stem


_SHARING_RETRY = 2.0


def _shared(operation: Callable[[], _T]) -> _T:
    """Run a state-file operation, retrying briefly on a Windows sharing violation.

    Windows refuses to replace, read or unlink a file another process or thread
    has open at that instant; the entry lease is read and replaced concurrently.
    """
    deadline = time.monotonic() + _SHARING_RETRY
    while True:
        try:
            return operation()
        except PermissionError:
            if os.name != "nt" or time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with open(staging, "w", encoding="utf-8") as stream:
            json.dump(data, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        _shared(lambda: os.replace(staging, path))
    finally:
        if staging.exists():
            staging.unlink()


def _read_json(path: Path) -> dict[str, Any] | None:
    """``None`` when absent; raises ``ValueError`` when unreadable or malformed."""
    try:
        data = json.loads(_shared(lambda: path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable state file {path.name}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError(f"unsupported state file {path.name}")
    return data


_OWNED_LEASES: dict[Path, str] = {}
_OWNED_LOCK = threading.Lock()


def write_entry_lease(ledger: Path) -> None:
    """Publish this process's entry lease until :func:`release_entry_lease`.

    Entry releases it as soon as hydration finishes or is refused. A dead or
    replaced owner also releases it; ``ENTRY_LEASE_TTL`` only bounds a lease
    left behind by an entry that crashed while its owner stays unidentifiable.
    """
    from . import locks

    pid = os.getpid()
    token = secrets.token_hex(8)
    path = _state_dir(ledger) / ENTRY_LEASE
    _write_json(path, {"version": 1, "pid": pid, "start": locks.process_start_time(pid) or "",
                       "token": token, "at": time.time()})
    with _OWNED_LOCK:
        _OWNED_LEASES[path] = token


def release_entry_lease(ledger: Path) -> None:
    """Remove the lease this process wrote for ``ledger``; never another entry's."""
    path = _state_dir(ledger) / ENTRY_LEASE
    with _OWNED_LOCK:
        token = _OWNED_LEASES.pop(path, None)
    if token is None:
        return
    try:
        lease = _read_json(path)
    except ValueError:
        return
    if lease is not None and lease.get("token") == token:
        _drop(path)


def _lease_owner_gone(lease: dict[str, Any]) -> bool:
    from . import locks

    pid = lease.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if not locks.pid_alive(pid):
        return True
    start = lease.get("start")
    current = locks.process_start_time(pid)
    return bool(isinstance(start, str) and start and current and current != start)


def entry_lease_pending(ledger: Path, *, now: float | None = None) -> bool:
    """Whether a managed entry may still be on its way to a registered session.

    An unreadable lease counts as pending (fail closed toward retaining).
    """
    try:
        lease = _read_json(_state_dir(ledger) / ENTRY_LEASE)
    except ValueError:
        return True
    if lease is None:
        return False
    at = lease.get("at")
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return True
    if (time.time() if now is None else now) - float(at) >= ENTRY_LEASE_TTL:
        return False
    return not _lease_owner_gone(lease)


# -- entry hydration ------------------------------------------------------


def _catalog_claims(record: Any, config: Any) -> bool:
    """Whether any catalog entry (even an unreadable one) may hold this checkout."""
    if catalog.is_dehydrated(record):
        return True
    try:
        return catalog.entry_path(config.default_repo.worktree_root, record.worktree_id).exists()
    except (catalog.CatalogError, AttributeError, KeyError, TypeError):
        # The record's own recorded location was already checked above.
        return False


def _rehydrate(worktree_id: str, config: Any) -> dict[str, Any]:
    from .launch_admission import LaunchAdmissionBusy

    deadline = time.monotonic() + ENTRY_BUSY_WAIT
    while True:
        try:
            return hydration.rehydrate(worktree_id, config)
        except hydration.HydrationRefused as exc:
            if exc.code == "not-dehydrated":
                # A concurrent operation already settled the checkout.
                return {"schema": hydration.SCHEMA, "worktree_id": worktree_id,
                        "state": catalog.STATE_HYDRATED, "already": True}
            raise
        except (TimeoutError, LaunchAdmissionBusy) as exc:
            if time.monotonic() >= deadline:
                raise hydration.HydrationRefused(
                    "busy", f"another lifecycle operation holds this worktree: {exc}",
                ) from exc
            time.sleep(0.5)


def _record_ledger(record: Any, config: Any) -> Path:
    """The entered record's own ledger file, else the owner's resolved one."""
    loaded = getattr(record, "yaml_path", None)
    if isinstance(loaded, Path) and loaded.name == f"{record.worktree_id}.yaml":
        return loaded
    return hydration._ledger(record.worktree_id, config)


def ensure_hydrated(record: Any, config: Any) -> dict[str, Any]:
    """Make ``record``'s checkout present before entry; idempotent.

    The entry lease covers only this call: it is released when hydration
    finishes or is refused. Raises :class:`checkout_hydration.HydrationRefused`
    when the checkout cannot be restored (including ``hold`` for an unreadable
    or newer catalog).
    """
    ledger = _record_ledger(record, config)
    write_entry_lease(ledger)
    try:
        # A new entry supersedes the earlier exit; its own exit queues again.
        withdraw_candidate(ledger)
        if not _catalog_claims(record, config):
            return {"schema": hydration.SCHEMA, "worktree_id": record.worktree_id,
                    "state": catalog.STATE_HYDRATED, "already": True}
        return _rehydrate(record.worktree_id, config)
    finally:
        release_entry_lease(ledger)


def _requires_sibling(record: Any) -> bool:
    """A harness worktree's session composes its knowledge sibling worktree."""
    return getattr(record, "pair_role", None) == "harness" and getattr(record, "pair_kind", None) == "worktree"


def _sibling_unavailable(record: Any, config: Any, restored: bool, detail: str) -> None:
    """Refuse an unresolved declared sibling only when hydration is involved.

    Involved means this entry restored the primary checkout, or the sibling
    project's catalog (configured or registered roots, by worktree id) holds
    an entry for it or cannot be read. Otherwise an unresolved pair is the pair
    owner's concern and entry proceeds as it would without dehydration.
    """
    ref = getattr(record, "pair_claim_ref", None)
    project = ref.project if ref is not None and ref.is_qualified else None
    evidence = (
        catalog.project_entry_state(project, ref.worktree_id, config)
        if project and ref is not None else catalog.ENTRY_ABSENT
    )
    if restored or evidence != catalog.ENTRY_ABSENT:
        why = "this entry restored its checkout" if restored else f"the sibling's catalog entry is {evidence}"
        raise hydration.HydrationRefused("sibling-unavailable", f"{detail} ({why})")


def _required_sibling(record: Any, config: Any, restored: bool) -> Any:
    """The required knowledge sibling, or ``None`` when there is none to hydrate.

    A declared pair whose sibling cannot be resolved is refused as
    ``sibling-unavailable`` when hydration is involved (see
    :func:`_sibling_unavailable`); it is never silently treated as unpaired then.
    """
    if not _requires_sibling(record):
        return None
    from . import tracking_lookup

    ref = getattr(record, "pair_claim_ref", None)
    if ref is None or not ref.is_qualified or not ref.project:
        _sibling_unavailable(record, config, restored, "the declared knowledge pair has no qualified sibling reference")
        return None
    try:
        sibling = tracking_lookup.find_paired_record(record)
    except Exception as exc:
        _sibling_unavailable(record, config, restored, f"cannot resolve the knowledge sibling: {exc}")
        return None
    if sibling is not None:
        return sibling
    ledger = cfg.project_dir(ref.project) / "worktrees" / f"{ref.worktree_id}.yaml"
    detail = (
        f"the knowledge sibling record {ref.worktree_id} is unreadable or not a local reciprocal pair"
        if ledger.exists() else f"the knowledge sibling record {ref.worktree_id} is missing"
    )
    _sibling_unavailable(record, config, restored, detail)
    return None


def _restored(report: dict[str, Any]) -> bool:
    return not report.get("already")


def _prepare_pair(record: Any, config: Any) -> None:
    """Recompose pair settings and companion instructions a restore removed.

    Uses the validated pair-preparation owner (also used at creation and
    launch); a repository's composition opt-out is honoured by that owner.
    """
    from . import knowledge_plugins

    try:
        result = knowledge_plugins.compose_from_pair(cwd=record.worktree_path, config=config)
    except (knowledge_plugins.KnowledgePluginError, OSError, ValueError) as exc:
        raise hydration.HydrationRefused("pair-preparation-failed", str(exc)) from exc
    error = result.get("pair_error")
    if error is not None or result.get("action") == "error":
        raise hydration.HydrationRefused(
            "pair-preparation-failed", str(error or result.get("error") or "knowledge composition failed"),
        )


def ensure_entry(record: Any, config: Any) -> list[dict[str, Any]]:
    """Hydrate the entered worktree and any required pair sibling.

    After a restore on either side, pair preparation runs before entry is
    ready. Never concludes, unpairs or finalizes either side.
    """
    # Worktree and anchor harness pairs both need preparation after a restore; only
    # a worktree pair has a sibling checkout to hydrate. The marker is persisted
    # before any restore (primary or sibling; the shared restore path also writes
    # it) and cleared only once the sibling resolved and preparation succeeded.
    prepared = hydration.pair_preparation_required(record)
    pending = hydration.pair_readiness_marker(_record_ledger(record, config)) if prepared else None

    def mark_pending() -> None:
        if pending is not None:
            _write_json(pending, {"version": 1, "worktree_id": record.worktree_id, "at": time.time()})

    if _catalog_claims(record, config):
        mark_pending()
    reports = [ensure_hydrated(record, config)]
    restored = _restored(reports[0]) or (pending is not None and pending.exists())
    sibling = _required_sibling(record, config, restored)
    if sibling is not None:
        from . import execution_spaces

        try:
            sibling_config = execution_spaces.project_record_config(sibling)
        except execution_spaces.ExecutionSpaceError as exc:
            if restored or catalog.is_dehydrated(sibling):
                raise hydration.HydrationRefused("sibling-unavailable", str(exc)) from exc
            sibling_config = None  # not dehydrated: the pair owner reports it
        if sibling_config is not None:
            if _catalog_claims(sibling, sibling_config):
                mark_pending()
            reports.append(ensure_hydrated(sibling, sibling_config))
    if pending is not None and (pending.exists() or any(_restored(report) for report in reports)):
        mark_pending()
        _prepare_pair(record, config)
        _drop(pending)
    return reports


DEHYDRATED_REFUSAL = "checkout-dehydrated"


class EntryRefused(RuntimeError):
    """A dehydrated checkout that entry could not restore; never launch in it."""

    def __init__(self, worktree_id: str, code: str, detail: str) -> None:
        self.worktree_id = worktree_id
        self.code = code
        self.remedy = f"agent-worktrees rehydrate {worktree_id}"
        super().__init__(
            f"{DEHYDRATED_REFUSAL}: cannot hydrate {worktree_id} for entry ({code}): {detail}. "
            f"Run `{self.remedy}` and retry."
        )

    def to_dict(self) -> dict[str, Any]:
        return {"reason": DEHYDRATED_REFUSAL, "worktree_id": self.worktree_id,
                "code": self.code, "remedy": self.remedy, "message": str(self)}


def require_entry(record: Any, config: Any) -> None:
    """Hydrate ``record`` (and its required sibling) or raise :class:`EntryRefused`."""
    worktree_id = getattr(record, "worktree_id", None)
    if not isinstance(worktree_id, str) or not worktree_id:
        return  # not a tracked worktree record: nothing is catalogued for it
    try:
        ensure_entry(record, config)
    except hydration.HydrationRefused as exc:
        raise EntryRefused(worktree_id, exc.code, str(exc)) from exc
    except (OSError, ValueError, RuntimeError) as exc:
        raise EntryRefused(worktree_id, "error", str(exc)) from exc


def entry_error(record: Any, config: Any, *, preview: bool = False) -> EntryRefused | None:
    """Common-entry hook: ``None`` when ready to launch, else the structured refusal."""
    if preview:
        return None
    try:
        require_entry(record, config)
    except EntryRefused as exc:
        return exc
    return None


def entry_refusal(record: Any, config: Any, *, preview: bool = False) -> str | None:
    """:func:`entry_error` as a message for plain-text entry surfaces."""
    refused = entry_error(record, config, preview=preview)
    return None if refused is None else str(refused)


def json_refusal(refused: EntryRefused, exit_code: int = 3) -> int:
    """Emit the structured ``checkout-dehydrated`` JSON error envelope."""
    from . import output

    output._json_output({"error": str(refused), **refused.to_dict()})
    return exit_code


def _preview(args: Any) -> bool:
    return bool(getattr(args, "dry_run", False))


def json_entry_refusal(record: Any, config: Any, args: Any) -> int | None:
    """JSON entry hook: emit a structured refusal and return its exit code, else ``None``."""
    refused = entry_error(record, config, preview=_preview(args))
    return None if refused is None else json_refusal(refused)


def entered_path(record: Any, config: Any, args: Any) -> str:
    """Plain entry hook: the hydrated checkout path, else ``ValueError`` with the refusal."""
    refusal = entry_refusal(record, config, preview=_preview(args))
    if refusal:
        raise ValueError(refusal)
    return str(record.worktree_path)


def embody_refusal(args: Any) -> str | None:
    """:func:`embody_error` as a message."""
    refused = embody_error(args)
    return None if refused is None else str(refused)


def _embody_rejected(args: Any) -> bool:
    """Mirror embody's side-effect-free argument validation, which runs before it resolves a target.

    A rejected invocation must leave the checkout, catalog and candidate untouched.
    """
    from . import bridge_acquisition_launch, launch_passthrough

    raw_id = getattr(args, "worktree_id", None)
    if bridge_acquisition_launch.intent_error(args, raw_id):
        return True
    if getattr(args, "deliver_pending_seed", False) and (
        getattr(args, "seed", None) or getattr(args, "fresh", False)
        or getattr(args, "bridge_seed_operation", None) or getattr(args, "bridge_acquisition", None)
    ):
        return True
    return launch_passthrough.passthrough_error(args) is not None


def embody_error(args: Any) -> EntryRefused | None:
    """Hydrate an existing ``embody --worktree-id``/``--codename`` target before its policy/CWD use.

    ``--new`` creates a checkout and ``--anchor`` enters the anchor, which is
    never dehydratable. A codename is resolved in this machine's project only;
    a remote or unresolvable target, and invalid selector combinations, are
    reported by the embody operation itself.
    """
    raw_id = getattr(args, "worktree_id", None)
    codename = getattr(args, "codename", None)
    if (
        getattr(args, "new", False) or getattr(args, "anchor", False)
        or getattr(args, "dry_run", False)
    ):
        return None
    has_id = isinstance(raw_id, str) and bool(raw_id)
    has_codename = isinstance(codename, str) and bool(codename)
    if has_id == has_codename or _embody_rejected(args):
        return None
    from . import tracking, worktree_identity

    try:
        config = cfg.load_config()
        worktree_id: str | None
        if isinstance(raw_id, str) and raw_id:
            worktree_id = worktree_identity._resolve_worktree_id(raw_id)
        elif isinstance(codename, str):
            worktree_id = worktree_identity.resolve_worktree_id_by_codename(codename)
        else:
            return None
        if not worktree_id:
            return None
        ledger = cfg.tracking_dir() / f"{worktree_id}.yaml"
        if not ledger.exists():
            return None
        record = tracking.load_record(ledger)
    except (SystemExit, Exception):
        return None  # the embody operation reports selection/config failures itself
    return entry_error(record, config)


_REQUEST_PROTOCOL_OPTIONS = frozenset({
    "command", "json", "launch_request_b64", "launch_request_status",
    "require_native_seed", "require_launch_attempt",
})


def _admissible_request(args: Any) -> dict[str, Any] | None:
    """The decoded envelope when the request command would admit it, else ``None``.

    Mirrors the request command's side-effect-free invocation validation
    (``launch_request.cmd_request``): rejected invocations never hydrate.
    """
    from . import launch_request

    if not getattr(args, "json", False) or any(
        value is not None and value is not False and value != []
        for key, value in vars(args).items() if key not in _REQUEST_PROTOCOL_OPTIONS
    ):
        return None
    encoded = getattr(args, "launch_request_b64", None)
    if not isinstance(encoded, str) or not encoded or getattr(args, "launch_request_status", None) is not None:
        return None
    request = launch_request.decode(encoded)
    version = request.get("version")
    if bool(getattr(args, "require_native_seed", False)) != (version == 2):
        return None
    if getattr(args, "require_launch_attempt", False):
        from .launch_attempt import identity

        identity(request.get("attempt_id"))
        if version != 2:
            return None
    return request


def run_launch_request(args: Any) -> int:
    """Remote seed admission entry: validate, hydrate a resume target, then admit."""
    from .launch_request import cmd_request

    if (refused := launch_request_error(args)) is not None:
        return json_refusal(refused)
    return cmd_request(args)


def launch_request_error(args: Any) -> EntryRefused | None:
    """Hydrate a remote seed admission's resume target before its policy check.

    Only an envelope the request command would admit, of kind *resume*,
    hydrates; status reads, New requests and rejected or malformed invocations
    are left to the request command, which reports them without launching.
    """
    from . import tracking

    try:
        request = _admissible_request(args)
        if request is None or request.get("kind") != "resume":
            return None
        worktree_id = request.get("worktree_id")
        if not isinstance(worktree_id, str) or not worktree_id:
            return None
        config = cfg.load_config()
        ledger = cfg.tracking_dir() / f"{worktree_id}.yaml"
        if not ledger.exists():
            return None
        record = tracking.load_record(ledger)
    except (SystemExit, Exception):
        return None  # the request command reports envelope and config failures itself
    return entry_error(record, config)


# -- automatic post-exit dehydration --------------------------------------


@contextmanager
def _candidate_lock(marker: Path) -> Iterator[None]:
    """Serialize every candidate write, withdrawal and generation check (cross-process)."""
    from . import tracking

    marker.parent.mkdir(parents=True, exist_ok=True)
    with tracking._RecordLock(marker, timeout=CANDIDATE_LOCK_TIMEOUT, require_sidecar=True):
        yield


def note_exit(record: Any, config: Any) -> bool:
    """Queue a candidate after an agent process exit; never dehydrates inline.

    A post-exit callback is not proof of retirement: the resident pass
    re-confirms it. Each queued exit gets a fresh ``generation``; an entry's
    withdrawal invalidates it, so a retry can never revive a withdrawn exit.
    """
    if policy_mode(config.repo_name) != MODE_AUTOMATIC:
        return False
    if record.kind != "session" or not record.checkout_managed or record.status in ("finalized", "finalizing"):
        return False
    if catalog.is_dehydrated(record):
        return False
    marker = _state_dir(hydration._ledger(record.worktree_id, config)) / CANDIDATE
    with _candidate_lock(marker):
        _write_json(marker, {"version": 1, "exited_at": time.time(), "attempts": 0,
                             "generation": secrets.token_hex(8)})
    return True


def withdraw_candidate(ledger: Path) -> None:
    """Entry supersedes a queued exit (under the candidate lock)."""
    marker = _state_dir(ledger) / CANDIDATE
    try:
        with _candidate_lock(marker):
            _drop(marker)
    except TimeoutError as exc:
        raise hydration.HydrationRefused("busy", f"the dehydration candidate is locked: {exc}") from exc


def _generation(marker: Path) -> str | None:
    """The queued candidate's generation; ``None`` when absent or unusable."""
    try:
        data = _read_json(marker)
    except ValueError:
        return None
    generation = data.get("generation") if data else None
    return generation if isinstance(generation, str) and generation else None


def _candidates(config: Any) -> list[tuple[float, Path]]:
    found: list[tuple[float, Path]] = []
    try:
        markers = list(cfg.tracking_dir(config.repo_name).glob(f"*/{CANDIDATE}"))
    except OSError:
        return found
    for marker in markers:
        try:
            data = _read_json(marker)
            at = float(data.get("exited_at", 0.0)) if data else 0.0
        except (ValueError, TypeError):
            at = 0.0
        found.append((at, marker))
    return sorted(found)


def _drop(marker: Path) -> None:
    try:
        _shared(marker.unlink)
    except FileNotFoundError:
        pass


def _drop_generation(marker: Path, generation: str) -> None:
    """Drop the candidate only while it is still ``generation``."""
    try:
        with _candidate_lock(marker):
            if _generation(marker) == generation:
                _drop(marker)
    except TimeoutError:
        pass  # left for the next pass, which re-gates it


def _retain(marker: Path, generation: str, reason: str) -> str:
    """Count a transient refusal for ``generation``; never recreates a withdrawn candidate."""
    try:
        with _candidate_lock(marker):
            try:
                data = _read_json(marker)
            except ValueError:
                data = None
            if data is None or data.get("generation") != generation:
                return "superseded"
            attempts = int(data.get("attempts", 0) or 0) + 1
            if attempts >= MAX_ATTEMPTS:
                _drop(marker)
                return "expired"
            _write_json(marker, {**data, "attempts": attempts, "last_reason": reason})
            return "retained"
    except TimeoutError:
        return "retained"  # untouched; the next pass re-gates it


def _confirm_retired(record: Any, ledger: Path) -> str | None:
    """A reason the agent is not confirmed retired, or ``None``."""
    from . import launch_seed_state, sessions

    verdict = sessions.verify_worktree_active(record, strict=True)
    if not getattr(verdict, "probes_ok", False):
        return "probes-unavailable"
    if getattr(verdict, "active", False) or getattr(verdict, "mux_live", False):
        return "live-session"
    if getattr(record, "pending_seed", None):
        return "pending-launch"
    try:
        if launch_seed_state.peek(ledger, read_only=True) is not None:
            return "pending-launch"
    except (OSError, ValueError):
        return "pending-launch"
    if entry_lease_pending(ledger):
        return "entry-pending"
    return None


def _ineligible(record: Any) -> bool:
    return record.status in ("finalized", "finalizing") or record.kind != "session" or not record.checkout_managed


def _final_check(marker: Path, generation: str, ledger: Path) -> Callable[[], str | None]:
    """Re-gate under dehydrate's lifecycle/admission locks, just before removal.

    Seed staging takes the same admission guard, so a seed staged after the
    first check is observed here; entry withdrawal changes the generation.
    """
    def check() -> str | None:
        from . import tracking

        try:
            with _candidate_lock(marker):
                if _generation(marker) != generation:
                    return "superseded"
        except TimeoutError:
            return "busy"
        try:
            record = tracking.load_record(ledger)
        except Exception:
            return "record-unreadable"
        if _ineligible(record):
            return "ineligible"
        return _confirm_retired(record, ledger)

    return check


def _process_candidate(marker: Path, config: Any) -> dict[str, Any]:
    from . import tracking
    from .launch_admission import LaunchAdmissionBusy

    worktree_id = marker.parent.name
    ledger = marker.parent.parent / f"{worktree_id}.yaml"
    result: dict[str, Any] = {"worktree_id": worktree_id}
    generation = _generation(marker)
    if generation is None:
        try:
            with _candidate_lock(marker):
                if _generation(marker) is None:
                    _drop(marker)  # unreadable or from an unsupported writer
        except TimeoutError:
            pass
        return {**result, "outcome": "dropped", "reason": "candidate-unreadable"}
    if policy_mode(config.repo_name) != MODE_AUTOMATIC:
        _drop_generation(marker, generation)
        return {**result, "outcome": "dropped", "reason": "policy-manual"}
    try:
        record = tracking.load_record(ledger)
    except FileNotFoundError:
        _drop_generation(marker, generation)
        return {**result, "outcome": "dropped", "reason": "no-record"}
    except Exception as exc:
        return {**result, "outcome": _retain(marker, generation, "record-unreadable"), "reason": str(exc)[:200]}
    if _ineligible(record):
        _drop_generation(marker, generation)
        return {**result, "outcome": "dropped", "reason": "ineligible"}
    reason = _confirm_retired(record, ledger)
    if reason is not None:
        return {**result, "outcome": _retain(marker, generation, reason), "reason": reason}
    try:
        report = hydration.dehydrate(worktree_id, config, final_check=_final_check(marker, generation, ledger))
    except hydration.HydrationRefused as exc:
        if exc.code in TRANSIENT:
            return {**result, "outcome": _retain(marker, generation, exc.code), "reason": exc.code}
        _drop_generation(marker, generation)
        return {**result, "outcome": "dropped", "reason": exc.code}
    except (TimeoutError, LaunchAdmissionBusy) as exc:
        return {**result, "outcome": _retain(marker, generation, "busy"), "reason": str(exc)[:200]}
    _drop_generation(marker, generation)
    return {**result, "outcome": "dehydrated", "already": bool(report.get("already"))}


def _evidence(path: Path, entry: dict[str, Any]) -> list[Any]:
    """What an inspection-only hold depends on: its catalog entry, checkout and Git registrations."""
    identity = entry.get("identity") or {}
    watched = [path, Path(str(identity.get("checkout_path", ""))), Path(str(identity.get("anchor", ""))) / ".git" / "worktrees"]
    evidence: list[Any] = []
    for watched_path in watched:
        try:
            stat = watched_path.stat()
            evidence.append([stat.st_mtime_ns, stat.st_size])
        except OSError:
            evidence.append(None)
    return evidence


def _reconcile_state(project: str) -> tuple[Path, dict[str, Any]]:
    path = cfg.tracking_dir(project) / RECONCILE_STATE
    try:
        data = _read_json(path) or {}
    except ValueError:
        data = {}  # attempt history is advisory: reconcile itself is the gate
    entries = data.get("entries")
    return path, entries if isinstance(entries, dict) else {}


def _interrupted_paths(roots: list[Path]) -> list[tuple[str, Path, dict[str, Any]]]:
    found: list[tuple[str, Path, dict[str, Any]]] = []
    seen: set[str] = set()
    for root in roots:
        try:
            entries = catalog.iter_entries(root)
        except OSError:
            continue
        for path in entries:
            if path.stem in seen:
                continue
            seen.add(path.stem)
            try:
                entry = catalog.read_entry(path)
            except catalog.CatalogError:
                continue  # inspection surfaces it as a hold
            if entry and entry["stage"] in (catalog.STAGE_REMOVING, catalog.STAGE_REHYDRATING):
                found.append((path.stem, path, entry))
    return found


def interrupted_entries(project: str, roots: list[Path], *, now: float | None = None) -> list[tuple[str, Path]]:
    """Runnable interrupted transitions, least recently attempted first.

    A hold whose evidence is unchanged since its last reconciliation is not
    runnable work (and so not resident demand) until ``HOLD_BACKOFF`` elapses.
    """
    _path, attempts = _reconcile_state(project)
    now = time.time() if now is None else now
    runnable: list[tuple[float, str, Path]] = []
    for worktree_id, path, entry in _interrupted_paths(roots):
        last = attempts.get(worktree_id)
        last = last if isinstance(last, dict) else {}
        at = last.get("at")
        at = float(at) if isinstance(at, (int, float)) else 0.0
        if last.get("held") and last.get("evidence") == _evidence(path, entry) and 0 <= now - at < HOLD_BACKOFF:
            continue
        runnable.append((at, worktree_id, path))
    return [(worktree_id, path) for _at, worktree_id, path in sorted(runnable)]


def _note_reconcile(project: str, attempted: dict[str, tuple[Path, bool]], live: set[str]) -> None:
    """Record attempt times (for rotation) and hold evidence (for backoff); never raises."""
    try:
        path, attempts = _reconcile_state(project)
        attempts = {k: v for k, v in attempts.items() if k in live}
        for worktree_id, (entry_path, held) in attempted.items():
            try:
                entry = catalog.read_entry(entry_path)
            except catalog.CatalogError:
                entry = None
            if entry is None or entry["stage"] not in (catalog.STAGE_REMOVING, catalog.STAGE_REHYDRATING):
                attempts.pop(worktree_id, None)
                continue
            attempts[worktree_id] = {"at": time.time(), "held": held, "evidence": _evidence(entry_path, entry)}
        _write_json(path, {"version": 1, "entries": attempts})
    except (OSError, ValueError, KeyError, TypeError):
        pass


def _roots(config: Any) -> list[Path]:
    try:
        return [*catalog.registered_roots(config.repo_name), Path(config.default_repo.worktree_root)]
    except (OSError, ValueError, KeyError, AttributeError, catalog.CatalogError):
        return []


def run_pending(config: Any, *, limit: int = PENDING_LIMIT) -> dict[str, Any]:
    """One bounded resident-maintenance pass; never raises.

    Restart reconciliation settles interrupted catalog transitions first (under
    the owner's locks, never removing a valid checkout); then up to ``limit``
    queued candidates are re-gated and dehydrated.
    """
    results: dict[str, Any] = {"reconciled": [], "candidates": []}
    roots = _roots(config)
    runnable = interrupted_entries(config.repo_name, roots) if roots else []
    attempted: dict[str, tuple[Path, bool]] = {}
    # Fair rotation: least recently attempted first, so persistent holds cannot starve later entries.
    for worktree_id, entry_path in runnable[:RECONCILE_LIMIT]:
        held = False
        try:
            report = hydration.reconcile(worktree_id, config)
            held = report.get("state") == "hold"
            results["reconciled"].append({"worktree_id": worktree_id, "state": report.get("state")})
        except Exception as exc:
            results["reconciled"].append({"worktree_id": worktree_id, "error": str(exc)[:200]})
        attempted[worktree_id] = (entry_path, held)
    if roots:
        _note_reconcile(config.repo_name, attempted, {wid for wid, _p, _e in _interrupted_paths(roots)})
    for _at, marker in _candidates(config)[:limit]:
        try:
            results["candidates"].append(_process_candidate(marker, config))
        except Exception as exc:
            results["candidates"].append({"worktree_id": marker.parent.name, "error": str(exc)[:200]})
    return results


def _wake_resident() -> None:
    """Ask the existing resident monitor to run (cheap no-op when it is live)."""
    from . import status_monitor_runtime

    try:
        if status_monitor_runtime._status_monitor_enabled():
            status_monitor_runtime._ensure_status_monitor()
    except Exception:
        pass


def after_post_exit(worktree_id: str, config: Any) -> None:
    """``post-exit`` hook: queue this worktree and wake the resident pass; never dehydrates inline."""
    from . import tracking

    try:
        record = tracking.load_record(hydration._ledger(worktree_id, config))
        if note_exit(record, config):
            _wake_resident()
    except Exception:
        pass


def run_housekeeping() -> None:
    """Session-exit cadence: wake the resident pass when the project has queued work; never raises."""
    from .checkout_hydration_resident import _has_work

    try:
        if _has_work(cfg.load_config().repo_name):
            _wake_resident()
    except Exception:
        pass
