"""The first complete, conservative linked-checkout relocation flow."""

from __future__ import annotations

import hashlib
import fnmatch
import json
import os
import secrets
import shutil
import sqlite3
import stat
import sys
import time
from contextlib import nullcontext
from pathlib import Path

from . import checkout_permission_relocation as permission_paths
from . import checkout_relocation_state as state, checkout_transfer as transfer
from . import checkout_durability as durability, permissions
from . import config as cfg, execution_spaces, git_ops, launch_admission, launch_seed_state
from . import procs, retained_checkout, sessions, tracking, worktree_lifecycle
from .checkout_reference_paths import under as reference_under
from .finalize_lock import FinalizeLock

SCHEMA = "agent-worktrees.checkout-relocation.v1"


def _native_supported() -> bool:
    return os.name == "nt"


def _require_native_support() -> None:
    if not _native_supported():
        raise ValueError("relocation v1 requires native Windows; bounded POSIX copy qualification remains open")


def capabilities() -> dict:
    return {
        "schema": SCHEMA, "version": 1, "plan": _native_supported(), "apply": _native_supported(),
        "qualified_platforms": ["windows"], "platform_supported": _native_supported(),
        "recovery": "forward",
        "cancel_before_publication": _native_supported(),
        "scope": "completed-sessionless-unpaired-plain-linked-checkout",
        "live_admission": {
            "schema": "agent-worktrees.checkout-live-admission", "version": 1,
            "check_live": _native_supported(), "activity_refresh": True,
            "apply_rechecks": True,
        },
        "receiving_location": {
            "schema": state.RECEIVING_SCHEMA, "version": 1, "revisions": 1,
            "scope": "finalized-sessionless-unpaired-same-project-settled-origin",
            "native_owner_qualification": False,
        },
        "writer_generation": 1,
        "max_entries": transfer.MAX_ENTRIES, "max_bytes": transfer.MAX_BYTES,
    }


def _git(path: Path, *args: str) -> str:
    return git_ops.git(
        "--no-optional-locks", "-c", "core.fsmonitor=false",
        *args, cwd=path, timeout=15, kill_tree=True,
        isolated_repository=True,
    ).stdout.strip()


def _digest(root: Path) -> str:
    return hashlib.sha256(json.dumps(transfer.manifest(root), sort_keys=True).encode()).hexdigest()


def _require_retirement_copy(record, original: Path, target: Path, *, allow_partial: bool = False) -> None:
    _stopped(record, original)
    _sessionless(original)
    remaining, verified = transfer.manifest(original), transfer.manifest(target)
    changed = any(verified.get(name) != item for name, item in remaining.items()) if allow_partial else remaining != verified
    if changed:
        raise ValueError("original copy changed after publication; source retained")


def _identity(record) -> dict:
    return {
        "machine_identity": record.machine_identity.to_record_fields() if record.machine_identity else None,
        **{
        key: getattr(record, key)
        for key in (
            "worktree_id", "repo", "branch", "creation_nonce", "machine", "platform",
            "execution_machine", "owner_ref",
        )
        },
    }


def _stopped(record, checkout: Path) -> None:
    if (
        not getattr(record, "receiving_allocation_present", False)
        and getattr(record, "owner_ref", None) and getattr(record, "creation_nonce", None)
    ):
        from .receiving_allocation import has_retained_receiving_operation

        if has_retained_receiving_operation(record, record.yaml_path.parent):
            raise ValueError("missing original receiving proof during owner revalidation")
    verdict = sessions.verify_worktree_active(record, strict=True)
    if not verdict.probes_ok or verdict.active:
        raise ValueError("relocation requires confirmed inactive session/process owners")
    blocker = procs.checkout_retirement_blocker(str(checkout))
    if blocker:
        raise ValueError(blocker)


def _ordinary_shape(record, ledger: Path, *, allow_receiving: bool = False) -> None:
    if (
        record.kind != "session" or not record.checkout_managed
        or not isinstance(record.creation_nonce, str) or not record.creation_nonce
        or record.worktree_id == "@anchor" or record.status not in {"complete", "completed", "finalized"}
        or record.sessions or record.head_session or record.handoffs
        or any((record.pair_id, record.pair_role, record.pair_ref, record.pair_kind))
        or record.receiving_allocation_present and not allow_receiving
        or record.session_backend is not None or record.session_backend_opaque
        or record.execution_leg is not None or record.execution_leg_opaque
        or record.profile_assignments or record.pending_seed
        or record.follow_up or tracking.effective_open_follow_up_count(record)
        or record.active_effort is not None
    ):
        raise ValueError("relocation v1 requires a completed ordinary sessionless, unpaired linked checkout")
    if any(claim.is_unsettled or claim.metadata_opaque for claim in record.resources):
        raise ValueError("relocation v1 refuses unsettled resource obligations")
    if any(pr.state != "merged" for pr in record.prs):
        raise ValueError("relocation v1 refuses open, unknown or unmerged PR associations")
    if launch_seed_state.peek(ledger, read_only=True) is not None:
        raise ValueError("relocation refuses pending launch intent")
    _launchless(ledger)


def _launchless(ledger: Path) -> None:
    from . import launch_attempt, launch_request

    for index, path in enumerate(_strict_glob(ledger.parent / "launch-requests", "*.json")):
        if index >= transfer.MAX_ENTRIES or path.is_symlink() or path.stat().st_size > 64 * 1024:
            raise ValueError("launch request inventory is incomplete")
        receipt = launch_request._read_admission(ledger.parent, path.stem)
        if receipt["worktree_id"] == ledger.stem:
            raise ValueError("relocation v1 refuses retained launch request history")
    for index, path in enumerate(_strict_glob(ledger.parent / "launch-attempts", "*/*.json")):
        if index >= transfer.MAX_ENTRIES:
            raise ValueError("launch attempt inventory is incomplete")
        if len(path.stem) != 32:
            continue
        receipt = launch_attempt.read(ledger.parent, path.parent.name, path.stem)
        if any(event["details"].get("worktree_id") == ledger.stem for event in receipt["events"]):
            raise ValueError("relocation v1 refuses retained launch attempt history")


def _strict_glob(root: Path, pattern: str):
    parts, pending, seen = pattern.split("/"), [(root, 0)], 0
    while pending:
        directory, depth = pending.pop()
        try:
            info = directory.lstat()
        except FileNotFoundError:
            if depth == 0:
                continue
            raise
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("operational reference directory is redirected or unsupported")
        part = parts[depth]
        if part not in {"", ".", ".."} and Path(part).name == part and not any(char in part for char in "*?["):
            path = directory / part
            try:
                item = path.lstat()
            except FileNotFoundError:
                if not os.path.samestat(info, directory.lstat()):
                    raise ValueError("operational reference directory changed during inspection")
                continue
            if not os.path.samestat(info, directory.lstat()):
                raise ValueError("operational reference directory changed during inspection")
            if depth == len(parts) - 1:
                yield path
            else:
                if stat.S_ISLNK(item.st_mode) or getattr(item, "st_file_attributes", 0) & 0x400:
                    raise ValueError("operational reference directory is redirected")
                if stat.S_ISDIR(item.st_mode):
                    pending.append((path, depth + 1))
            continue
        with os.scandir(directory) as entries:
            for entry in entries:
                seen += 1
                if seen > transfer.MAX_ENTRIES:
                    raise ValueError("operational reference directory inventory exceeds its budget")
                if not fnmatch.fnmatch(entry.name, parts[depth]):
                    continue
                path = Path(entry.path)
                if depth == len(parts) - 1:
                    yield path
                else:
                    item = path.lstat()
                    if stat.S_ISLNK(item.st_mode) or getattr(item, "st_file_attributes", 0) & 0x400:
                        raise ValueError("operational reference directory is redirected")
                    if stat.S_ISDIR(item.st_mode):
                        pending.append((path, depth + 1))


def _ordinary(record, ledger: Path, config) -> dict | None:
    _ordinary_shape(record, ledger, allow_receiving=True)
    receiving_proof = None
    if record.receiving_allocation_present:
        from .receiving_relocation import inspect as inspect_receiving

        receiving_proof = inspect_receiving(record, config)
        if record.worktree_path != receiving_proof["original_identity"]["path"]:
            raise ValueError("receiving relocation supports one current-location revision; inspect the original move")
    execution_spaces.require_record_mutation(record, config, recover_receiving=False)
    journal = ledger.parent.parent / "workspace-creation.sqlite3"
    if receiving_proof is None and journal.exists() and _references(journal, [
        ("SELECT 1 FROM creations WHERE json_extract(worktree, '$.worktree.id')=? "
         "OR json_extract(worktree, '$.id')=? LIMIT 1", (record.worktree_id, record.worktree_id)),
    ]):
        raise ValueError("relocation v1 refuses immutable creation receipts; location evolution remains unqualified")
    return receiving_proof


def _check_data(checkout: Path, config, branch: str) -> str:
    from .checkout_data import preservation_reason

    if preservation_reason(checkout) is not None or (Path(config.default_repo.anchor) / ".agent-index" / "config.yaml").exists():
        raise ValueError("relocation v1 requires qualification from the checkout-data or index owner")
    retained_checkout.require_git_checkout(checkout, Path(config.default_repo.anchor), branch)
    administrative = Path(_git(checkout, "rev-parse", "--absolute-git-dir"))
    try:
        (administrative / "locked").lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError("relocation refuses a Git-locked worktree")
    if _git(checkout, "status", "--porcelain=v1", "--untracked-files=normal"):
        raise ValueError("relocation v1 requires a clean checkout")
    upstream = f"refs/remotes/{config.default_repo.remote}/{config.default_repo.default_branch}"
    head = _git(checkout, "rev-parse", "HEAD")
    result = git_ops.git(
        "merge-base", "--is-ancestor", head, upstream, cwd=checkout, check=False,
        timeout=15, kill_tree=True, isolated_repository=True,
    )
    if result.returncode != 0:
        cherry = _git(checkout, "cherry", upstream, "HEAD").splitlines()
        if not (cherry and all(line.startswith("- ") for line in cherry)) and (
            _git(checkout, "rev-parse", f"{upstream}^{{tree}}") != _git(checkout, "rev-parse", "HEAD^{tree}")
        ):
            raise ValueError("relocation requires locally proven merged source content")
    return head


def _sessionless(checkout: Path) -> None:
    # Historical session migration is not qualified in this first flow.
    directory = sessions._session_state_dir()
    import yaml

    for index, workspace in enumerate(_strict_glob(directory, "*/workspace.yaml")):
        if index >= transfer.MAX_ENTRIES or workspace.stat().st_size > 64 * 1024 or workspace.is_symlink():
            raise ValueError("session workspace inventory is redirected")
        try:
            data = yaml.safe_load(workspace.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError("session workspace inventory has malformed YAML") from exc
        if not isinstance(data, dict):
            raise ValueError("session workspace inventory has an unsupported schema")
        for field in ("cwd", "git_root"):
            value = data.get(field)
            if isinstance(value, str) and reference_under(value, checkout):
                raise ValueError("relocation v1 refuses retained Copilot workspaces")
    database = directory.parent / "session-store.db"
    if database.exists():
        queries = [
            (f"SELECT 1 FROM {table} WHERE checkout_reference_under({column}, ?) LIMIT 1", (str(checkout),))
            for table, column in (("sessions", "cwd"), ("session_files", "file_path"))
        ]
        if _references(database, queries):
            raise ValueError("relocation v1 refuses retained Copilot database references")


def _references(path: Path, queries: list[tuple]) -> bool:
    if path.is_symlink():
        raise ValueError("operational reference database is redirected")
    with path.open("rb") as stream:
        header = stream.read(20)
    if header[18:20] == b"\x02\x02" and any(
        not Path(str(path) + suffix).is_file() or Path(str(path) + suffix).is_symlink()
        for suffix in ("-wal", "-shm")
    ):
        raise ValueError("read-only WAL inventory requires existing owner-managed sidecars")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    deadline = time.monotonic() + 3
    from .checkout_reference_paths import ReferenceSnapshot

    references = ReferenceSnapshot(deadline)
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    inspected = 0
    def bounded_reference(value, root):
        nonlocal inspected
        inspected += 1
        if inspected > transfer.MAX_ENTRIES or time.monotonic() > deadline:
            raise ValueError("operational reference inventory exceeds its budget")
        if value is None:
            return 0
        if not isinstance(value, str):
            raise ValueError("operational reference path has an unsupported schema")
        return int(references.under(value, root))
    connection.create_function("checkout_reference_under", 2, bounded_reference)
    try:
        matched = any(connection.execute(query, parameters).fetchone() for query, parameters in queries)
        if not matched:
            references.verify()
        return matched
    except sqlite3.DatabaseError as exc:
        raise ValueError("operational reference schema is unsupported or inventory is incomplete") from exc
    finally:
        connection.close()


def _target(record, destination: str, config) -> Path:
    path = Path(destination).expanduser()
    source = Path(record.worktree_path)
    if (
        not path.is_absolute() or path.resolve() != path.absolute()
        or path.name != record.worktree_id or not path.parent.is_dir()
        or path == source or path.is_relative_to(source) or source.is_relative_to(path)
    ):
        raise ValueError("destination must be a nonredirected absolute ID directory in an existing parent")
    protected = (Path(config.default_repo.anchor).resolve(), cfg.project_dir(config.repo_name).resolve())
    if any(path.is_relative_to(root) for root in protected):
        raise ValueError("relocation destination cannot be inside the anchor or project state")
    parent_repo = git_ops.git(
        "--no-optional-locks", "rev-parse", "--show-toplevel", cwd=path.parent,
        check=False, timeout=10, kill_tree=True, isolated_repository=True,
    )
    if parent_repo.returncode == 0:
        raise ValueError("relocation destination cannot be nested in another Git checkout")
    return path


def plan(worktree_id: str, destination: str, config) -> dict:
    """Inspect without action probes or recovery; unknown native owners refuse."""
    from .checkout_relocation_plan import inspect

    return inspect(worktree_id, destination, config)


def _action_plan(worktree_id: str, destination: str, config) -> dict:
    _require_native_support()
    state.require_writer_generation()
    if Path(worktree_id).name != worktree_id or "/" in worktree_id or "\\" in worktree_id:
        raise ValueError("move requires one exact managed worktree ID")
    ledger = cfg.tracking_dir(config.repo_name) / f"{worktree_id}.yaml"
    if ledger.is_symlink():
        raise ValueError("relocation ledger is redirected")
    original_record = ledger.read_bytes()
    record = tracking.load_record(ledger)
    if ledger.read_bytes() != original_record:
        raise ValueError("receiving record changed while reading its planning snapshot")
    if record.worktree_id != worktree_id or record.repo != config.repo_name:
        raise ValueError("relocation record does not match the receiving ledger identity/project")
    previous = state.read(ledger)
    if previous is not None:
        target = Path(destination).expanduser()
        if previous.get("identity") != _identity(record):
            raise ValueError("retained move identity or destination conflicts; inspect the original operation")
        _require_receiver(record, previous, config)
        if previous["phase"] not in {"complete", "cancelled"}:
            state.require_previous_owner(previous)
            if previous["destination"] != str(target):
                raise ValueError("unfinished move has a different destination; recover the original operation")
            if previous["phase"] == "cancelling":
                raise ValueError("relocation cancellation is unfinished; repeat --abort")
            _ordinary_shape(record, ledger, allow_receiving=True)
            return {**previous, "apply": False, "recovery": True}
        if (
            previous["phase"] == "complete" and previous["destination"] == str(target)
            and record.worktree_path == previous["destination"]
        ):
            return {**previous, "apply": False, "recovery": False, "replayed": True}
    target = _target(record, destination, config)
    source = Path(record.worktree_path)
    if record.worktree_path != str(source) or source.name != worktree_id or source.absolute() != source.resolve():
        raise ValueError("relocation source location is redirected or noncanonical")
    receiving_proof = _ordinary(record, ledger, config)
    if ledger.resolve().is_relative_to(source):
        raise ValueError("relocation requires checkout-independent tracking state")
    if target.exists():
        raise ValueError("relocation destination already exists")
    _stopped(record, source)
    _sessionless(source)
    _sessionless(target)
    head = _check_data(source, config, record.branch)
    fingerprint = _digest(source)
    transfer.require_file_mutation(ledger)
    transfer.require_source_access(source, target.parent)
    transfer.require_safe_destination(source, target.parent)
    total = sum(path.stat().st_size for path in source.rglob("*") if path.is_file())
    if shutil.disk_usage(target.parent).free < total + 1024 * 1024:
        raise ValueError("insufficient destination capacity")
    permissions = permission_paths.capture(str(source), str(target))
    report = {
        "schema": SCHEMA, "identity": _identity(record), "source": str(source),
        "destination": str(target), "head": head, "fingerprint": fingerprint,
        "anchor": str(Path(config.default_repo.anchor).resolve()),
        "git_common": _git(Path(config.default_repo.anchor), "rev-parse", "--path-format=absolute", "--git-common-dir"),
        "ledger_sha256": hashlib.sha256(original_record).hexdigest(),
        "permissions": permissions, "phase": "planned", "apply": False,
    }
    if receiving_proof is not None:
        report["schema"] = state.RECEIVING_SCHEMA
        report["receiving_location"] = receiving_proof
    if previous is not None:
        report["previous_journal"] = hashlib.sha256(state.journal_path(ledger).read_bytes()).hexdigest()
    _require_receiver(record, report, config)
    if len(json.dumps(report).encode("utf-8")) > 60 * 1024:
        raise ValueError("relocation pointer inventory exceeds its journal budget")
    return report


def _require_receiver(record, report: dict, config) -> None:
    execution_spaces.require_current_execution_space(config)
    local = execution_spaces.require_owner_identity(record.execution_machine, config, canonical_ref=False)
    if not local and record.machine_identity is None:
        from .creation_repair_identity import _observed_original_owner

        local = _observed_original_owner(record, config)
    if record.repo != config.repo_name or record.platform != cfg.detect_platform() or config.platform != cfg.detect_platform() or not local:
        raise ValueError("retained move belongs to another receiving execution space")
    context = execution_spaces.current_execution_context(config)
    if record.machine_identity is not None and (
        record.machine_identity.machine != context.machine
        or record.machine_identity.environment != context.environment
        or record.machine_identity.execution_machine != context.execution_machine
    ):
        raise ValueError("retained move physical/execution tuple changed")
    if (
        report.get("anchor") != str(Path(config.default_repo.anchor).resolve())
        or report.get("git_common") != _git(Path(config.default_repo.anchor), "rev-parse", "--path-format=absolute", "--git-common-dir")
    ):
        raise ValueError("relocation receiving Git family changed")


def _receiving_admission(ledger: Path, config):
    from .checkout_relocation_plan import read_record

    passive = read_record(ledger)
    from . import record_reporting
    record_reporting.apply_context(passive, record_reporting.current_context(config.repo_name))
    if passive.worktree_id != ledger.stem or passive.repo != config.repo_name:
        raise ValueError("relocation record does not match the receiving ledger identity/project")
    if passive.receiving_allocation_present:
        from .receiving_relocation import guard as receiving_owner, inspect as inspect_receiving

        proof = inspect_receiving(passive, config)
        return passive, proof, receiving_owner(ledger, proof)
    elif passive.owner_ref and passive.creation_nonce:
        from .receiving_allocation import has_retained_receiving_operation

        if has_retained_receiving_operation(passive, ledger.parent):
            raise ValueError("read-only proof cannot recover missing receiving allocation metadata; inspect through its owner")
    return passive, None, nullcontext()


def move(worktree_id: str, destination: str, config) -> dict:
    _require_native_support()
    state.require_writer_generation()
    ledger = cfg.tracking_dir(config.repo_name) / f"{worktree_id}.yaml"
    if Path(worktree_id).name != worktree_id or "/" in worktree_id or "\\" in worktree_id:
        raise ValueError("move requires one exact managed worktree ID")
    passive, proof, receiving_guard = _receiving_admission(ledger, config)
    if (
        proof is not None and passive.worktree_path != proof["original_identity"]["path"]
        and str(Path(destination).expanduser()) != passive.worktree_path
    ):
        raise ValueError("receiving relocation supports one current-location revision; inspect the original move")
    with FinalizeLock(
        Path(config.default_repo.worktree_root) / ".finalize.lock", timeout=3,
        on_stale=lambda message: print(message, file=sys.stderr),
    ), receiving_guard, (
        worktree_lifecycle.guard(ledger, relocation=True)
    ), launch_admission.guard(ledger), state.owner(ledger):
        report = _action_plan(worktree_id, destination, config)
        if report["phase"] == "complete":
            return {**report, "apply": True}
        record = tracking.load_record(ledger)
        source, target = Path(report["source"]), Path(report["destination"])
        if report["phase"] == "planned":
            report["stage"] = str(target.with_name(target.name + ".relocating-" + secrets.token_hex(8)))
            report["phase"] = "prepared"
            with tracking._RecordLock(ledger, require_sidecar=True):
                if (
                    _identity(tracking.load_record(ledger)) != report["identity"]
                    or hashlib.sha256(ledger.read_bytes()).hexdigest() != report["ledger_sha256"]
                ):
                    raise ValueError("relocation identity changed before admission")
                if report.get("previous_journal"):
                    state.archive_terminal(ledger, report["previous_journal"])
                report["caller"] = state.caller()
                state.write(ledger, report)
        state.require_previous_owner(report)
        report["caller"] = state.caller()
        state.write(ledger, report)
        stage = Path(report["stage"])
        retirement = source.with_name(source.name + ".retiring-" + stage.name.rsplit("-", 1)[1])
        if not source.exists() and report["phase"] != "retiring" and not (
            report["phase"] == "rebound" and retirement.exists()
        ):
            raise ValueError("original copy disappeared before owned retirement; retain original custody")
        if source.exists():
            _stopped(record, source)
            _sessionless(source)
            if report["phase"] != "rebound" and _digest(source) != report["fingerprint"]:
                raise ValueError("original checkout changed during relocation; copies retained")
        if report["phase"] == "prepared":
            if stage.exists():
                raise ValueError("stage creation outcome is uncertain; original retained for inspection")
            # Windows must inherit the admitted parent's ACL; Python 3.13 special-cases 0700.
            stage.mkdir(mode=0o777 if os.name == "nt" else 0o700)
            report["phase"] = "copying"
            state.write(ledger, report)
        if report["phase"] == "copying":
            transfer.require_safe_destination(source, target.parent)
            transfer.copy(source, stage)
            transfer.preserve_security(source, stage)
            if _digest(stage) != report["fingerprint"]:
                raise ValueError("checkout copy verification failed; original retained")
            durability.tree(stage)
            report["phase"] = "copied"
            state.write(ledger, report)
        if report["phase"] == "copied":
            if stage.exists() and not target.exists():
                _sessionless(target)
                if _digest(source) != report["fingerprint"]:
                    raise ValueError("original copy changed after copying; source retained")
                _stopped(record, source)
                durability.replace(stage, target, existing=False)
            if stage.exists() or not target.is_dir() or _digest(target) != report["fingerprint"]:
                raise ValueError("destination publication is ambiguous; retain original move custody")
            durability.directory(target.parent)
            report["phase"] = "published"
            state.write(ledger, report)
        if report["phase"] == "published":
            _git(Path(config.default_repo.anchor), "worktree", "repair", str(target))
            retained_checkout.require_git_checkout(target, Path(config.default_repo.anchor), record.branch)
            administrative = Path(_git(target, "rev-parse", "--absolute-git-dir"))
            durability.files([administrative / "gitdir", target / ".git"])
            report["phase"] = "git-repaired"
            state.write(ledger, report)
        if report["phase"] == "git-repaired":
            permission_paths.apply(str(source), str(target), report["permissions"])
            with tracking._RecordLock(ledger, require_sidecar=True):
                record = tracking.load_record(ledger)
                if _identity(record) != report["identity"] or record.worktree_path not in {str(source), str(target)}:
                    raise ValueError("relocation ledger changed; original operation remains unsettled")
                record.worktree_path = str(target)
                tracking.save_record(record)
                durability.files([
                    ledger,
                    *(path for kind, path in (
                        ("trust", permissions._config_path()), ("permissions", permissions._permissions_path()),
                    ) if path.exists() and report["permissions"][kind] is not None
                      and (kind != "trust" or report["permissions"][kind])),
                ])
            report["phase"] = "rebound"
            state.write(ledger, report)
        if report["phase"] in {"rebound", "retiring"}:
            record = tracking.load_record(ledger)
            if (
                record.worktree_path != str(target) or not retained_checkout.matches(record, config)
                or _check_data(target, config, record.branch) != report["head"]
                or _digest(target) != report["fingerprint"]
            ):
                raise ValueError("post-move consumer verification failed; source retained")
            _stopped(record, target)
            _sessionless(target)
            administrative = Path(_git(target, "rev-parse", "--absolute-git-dir"))
            durability.tree(target)
            durability.files([
                ledger, administrative / "gitdir", target / ".git",
                *(path for kind, path in (
                    ("trust", permissions._config_path()), ("permissions", permissions._permissions_path()),
                ) if path.exists() and report["permissions"][kind] is not None
                  and (kind != "trust" or report["permissions"][kind])),
            ])
            state.write(ledger, report)
            if report["phase"] == "rebound":
                if source.exists():
                    if retirement.exists():
                        raise ValueError("original and retirement copies both exist; retain original custody")
                    _require_retirement_copy(record, source, target)
                    durability.replace(source, retirement, existing=False)
                if retirement.exists():
                    _require_retirement_copy(record, retirement, target)
                durability.directory(source.parent)
                report["phase"] = "retiring"
                state.write(ledger, report)
            if source.exists():
                raise ValueError("original name reappeared during owned retirement; retain custody")
            if retirement.exists():
                _require_retirement_copy(record, retirement, target, allow_partial=True)
                shutil.rmtree(retirement)
            durability.directory(source.parent)
            report["phase"] = "complete"
            state.write(ledger, report)
        return {**report, "apply": True}


def abort(worktree_id: str, destination: str, config) -> dict:
    _require_native_support()
    state.require_writer_generation()
    if Path(worktree_id).name != worktree_id or "/" in worktree_id or "\\" in worktree_id:
        raise ValueError("abort requires one exact managed worktree ID")
    ledger = cfg.tracking_dir(config.repo_name) / f"{worktree_id}.yaml"
    _passive, _proof, receiving_guard = _receiving_admission(ledger, config)
    with FinalizeLock(
        Path(config.default_repo.worktree_root) / ".finalize.lock", timeout=3,
        on_stale=lambda message: print(message, file=sys.stderr),
    ), receiving_guard, (
        worktree_lifecycle.guard(ledger, relocation=True)
    ), launch_admission.guard(ledger), state.owner(ledger):
        report = state.read(ledger)
        record = tracking.load_record(ledger)
        if report is None or report["destination"] != str(Path(destination).expanduser()) or report["identity"] != _identity(record):
            raise ValueError("abort requires the original relocation identity and destination")
        _require_receiver(record, report, config)
        if report["phase"] == "cancelled":
            return {**report, "apply": True, "replayed": True}
        if report["phase"] not in {"prepared", "copying", "copied", "cancelling"}:
            raise ValueError("published relocation requires forward recovery; cancellation is unavailable")
        state.require_previous_owner(report)
        source, target, stage = (Path(report[key]) for key in ("source", "destination", "stage"))
        if target.exists() or record.worktree_path != str(source):
            raise ValueError("destination publication is uncertain; retain the original operation")
        actual_branch = _git(source, "symbolic-ref", "--short", "HEAD")
        retained_checkout.require_git_checkout(source, Path(config.default_repo.anchor), actual_branch)
        if report["phase"] == "prepared" and stage.exists():
            raise ValueError("stage creation custody is uncertain; inspection is required")
        if stage.exists():
            blocker = procs.checkout_retirement_blocker(str(stage))
            if blocker:
                raise ValueError(blocker)
            transfer.manifest(stage)
        report["caller"] = state.caller()
        report["phase"] = "cancelling"
        state.write(ledger, report)
        if stage.exists():
            shutil.rmtree(stage)
        durability.directory(stage.parent)
        report["phase"] = "cancelled"
        state.write(ledger, report)
        return {**report, "apply": True}
