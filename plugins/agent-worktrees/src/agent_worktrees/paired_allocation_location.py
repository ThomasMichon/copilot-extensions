"""Original companion allocation intent, independent of later allocation defaults."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from . import config as cfg, repos, state_root


def _path(project: str, worktree_id: str) -> Path:
    return cfg.tracking_dir(project) / f"{worktree_id}.paired-allocation.json"


def reserve(config, worktree_id: str, nonce: str, *, kind="session", no_pair=False, machine_identity=None, platform_name=None) -> None:
    if kind != "session" or no_pair or os.environ.get("AGENT_WORKTREES_NO_PAIR"):
        return
    root = state_root.resolve_state_root(config)
    if not root.requires_external or not root.bound:
        return
    entry = repos.find_repo(root.repo)
    if entry is None or repos.normalize_class(entry.repo_class) not in {"worktree", "knowledge"}:
        return
    receiving = cfg.load_project_config(root.repo, include_control_plane_related_pr=False)
    if Path(receiving.default_repo.anchor).resolve() != Path(root.path).resolve():
        raise ValueError("paired allocation receiving anchor changed")
    target = Path(receiving.default_repo.worktree_root) / f"{worktree_id}-k"
    if not target.is_absolute() or target.resolve() != target.absolute():
        raise ValueError("paired allocation root is relative or redirected")
    ledger = cfg.tracking_dir(config.repo_name) / f"{worktree_id}.yaml"
    if ledger.exists():
        from . import tracking

        record = tracking.load_record(ledger)
        if record.creation_nonce != nonce:
            raise ValueError("paired allocation parent incarnation changed")
    data = {
        "schema": "agent-worktrees.paired-allocation-location.v1",
        "project": config.repo_name, "worktree_id": worktree_id,
        "creation_nonce": nonce, "repo": root.repo, "anchor": str(Path(root.path).resolve()),
        "path": str(target),
    }
    path = _path(config.repo_name, worktree_id)
    from .checkout_durability import files, replace

    if path.exists():
        if path.is_symlink() or path.stat().st_size > 16 * 1024:
            raise ValueError("original paired allocation intent conflicts; retain owned recovery")
        existing = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(existing, dict):
            existing.pop("owner", None)
        if existing != data:
            raise ValueError("original paired allocation intent conflicts; retain owned recovery")
    else:
        staging = path.with_suffix(".reserving-" + secrets.token_hex(8))
        try:
            with staging.open("x", encoding="utf-8") as stream:
                json.dump(data, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            replace(staging, path, existing=False)
        finally:
            staging.unlink(missing_ok=True)
    files([path])


def original(record, root, *, legacy: bool = True) -> Path | None:
    path = _path(record.repo, record.worktree_id)
    if not path.exists():
        # Before this contract, companion creation always derived from its
        # anchor, even when that repository configured an independent root.
        return Path(cfg.derive_worktree_root(root.path)) / f"{record.worktree_id}-k" if legacy else None
    if path.is_symlink() or path.stat().st_size > 16 * 1024:
        raise ValueError("paired allocation intent is redirected or oversized")
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or set(data) - {"schema", "project", "worktree_id", "creation_nonce", "repo", "anchor", "path", "owner"}
        or data.get("schema") != "agent-worktrees.paired-allocation-location.v1"
        or any(data.get(key) != value for key, value in {
            "project": record.repo, "worktree_id": record.worktree_id,
            "creation_nonce": record.creation_nonce, "repo": root.repo,
            "anchor": str(Path(root.path).resolve()),
        }.items())
    ):
        raise ValueError("original paired allocation conflicts with the retained parent or receiver")
    target = Path(data["path"])
    if (
        not target.is_absolute() or target.resolve() != target.absolute()
        or target.name != f"{record.worktree_id}-k"
    ):
        raise ValueError("original paired allocation path is invalid")
    return target


def receiver_root(config, worktree_id: str, root, retained_path: Path | None) -> tuple[str, Path | None]:
    from . import tracking

    receiving = cfg.load_project_config(root.repo, include_control_plane_related_pr=False)
    if receiving.repo_name != root.repo or Path(receiving.default_repo.anchor).resolve() != Path(root.path).resolve():
        raise ValueError("paired allocation conflicts with its receiving repository")
    ledger = cfg.tracking_dir(config.repo_name) / f"{worktree_id}.yaml"
    retained = original(tracking.load_record(ledger), root) if ledger.exists() else None
    if retained_path is not None and retained != retained_path:
        raise ValueError("paired recovery does not match the original allocation intent")
    return (str(retained.parent) if retained else receiving.default_repo.worktree_root), retained


def require_receiver_root(root, worktree_root: str, *, retained: bool) -> None:
    receiving = cfg.load_project_config(root.repo, include_control_plane_related_pr=False)
    if (
        receiving.repo_name != root.repo
        or Path(receiving.default_repo.anchor).resolve() != Path(root.path).resolve()
        or not retained and Path(receiving.default_repo.worktree_root).resolve() != Path(worktree_root).resolve()
    ):
        raise ValueError("paired receiving allocation policy changed before creation")
