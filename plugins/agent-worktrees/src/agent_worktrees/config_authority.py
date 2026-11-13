"""Registration-specific receiving-side authority configuration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config

def _load_state_root_authority(
    name: str, anchor: str, platform_name: str, remote_name: str | None,
) -> tuple[dict | None, str]:
    """Read only state-root gates from the registered committed config."""
    from . import config as cfg, git_ops, repos

    root = Path(anchor)

    def _git(*args: str):
        return git_ops.git(
            *args, cwd=root, check=False, capture=True,
            isolated_repository=True,
        )

    candidates = (
        cfg.inrepo_config_path(Path()),
        cfg.legacy_inrepo_config_path(Path()),
        Path(cfg.INREPO_CONFIG_FILENAME),
    )
    if remote_name is None:
        remotes = _git("remote")
        if remotes.returncode != 0:
            raise ValueError(f"cannot resolve local remote for project {name!r}")
        names = [line.strip() for line in remotes.stdout.splitlines() if line.strip()]
        if "origin" in names:
            remote_name = "origin"
        elif len(names) == 1:
            remote_name = names[0]
        elif not names:
            remote_name = ""
        else:
            raise ValueError(f"local remote is ambiguous for project {name!r}")

    def _refs(branch: str) -> list[str]:
        return (
            [
                f"refs/remotes/{remote_name}/{branch}",
                f"refs/heads/{branch}",
            ]
            if remote_name else [f"refs/heads/{branch}"]
        )

    def _read_branch(branch: str, *, required: bool) -> dict | None:
        ref = ""
        listing = None
        for candidate_ref in _refs(branch):
            exists = _git(
                "show-ref", "--verify", "--quiet", candidate_ref,
            )
            if exists.returncode == 1:
                continue
            if exists.returncode != 0:
                raise ValueError(
                    f"cannot verify committed authority ref {candidate_ref!r}"
                )
            candidate_listing = _git(
                "ls-tree", "-r", "--name-only", candidate_ref, "--",
                *(candidate.as_posix() for candidate in candidates),
            )
            if candidate_listing.returncode != 0:
                raise ValueError(
                    f"cannot list committed authority config at {candidate_ref!r}"
                )
            ref, listing = candidate_ref, candidate_listing
            break
        if listing is None:
            if required:
                raise ValueError(
                    f"cannot read committed authority config for {name!r}"
                )
            return None
        available = set(listing.stdout.splitlines())
        selected = next(
            (
                candidate for candidate in candidates
                if candidate.as_posix() in available
            ),
            None,
        )
        if selected is None:
            if required and remote_name and not ref.startswith(f"refs/remotes/{remote_name}/"):
                raise ValueError("committed authority absence requires the remote tracking ref")
            return None
        content = _git(
            "show", f"{ref}:{selected.as_posix()}",
        )
        if content.returncode != 0:
            raise ValueError(
                f"cannot read committed authority config for {name!r}"
            )
        raw = cfg.yaml.safe_load(content.stdout)
        if not isinstance(raw, dict):
            raise ValueError(
                f"committed authority config for {name!r} is malformed"
            )
        return raw

    head_ref = (
        f"refs/remotes/{remote_name}/HEAD"
        if remote_name else "HEAD"
    )
    head = _git(
        "symbolic-ref", "-q", head_ref,
    )
    prefix = (
        f"refs/remotes/{remote_name}/"
        if remote_name else "refs/heads/"
    )
    if head.returncode == 0:
        ref = head.stdout.strip()
        if not ref.startswith(prefix) or not ref[len(prefix):]:
            raise ValueError(
                f"committed authority HEAD is malformed for project {name!r}"
            )
        branch = ref[len(prefix):]
    elif head.returncode == 1:
        branch = ""
    else:
        raise ValueError(
            f"cannot resolve committed authority HEAD for project {name!r}"
        )
    if not branch:
        entry = repos.read_registry().repos.get(name)
        branch = entry.default_branch if entry is not None else ""
    if not branch:
        branch = str(
            cfg._resolve_adoption_defaults_from_registry(
                name, platform_name,
            ).get("default_branch", "")
        )
    if not branch:
        raise ValueError(f"cannot resolve committed branch for project {name!r}")

    data = _read_branch(branch, required=True)
    if data is None and head.returncode != 0:
        raise ValueError("committed authority absence requires a resolved default HEAD")
    declared = data.get("default_branch") if data is not None else None
    if isinstance(declared, str) and declared and declared != branch:
        hopped = _read_branch(declared, required=False)
        if hopped is not None:
            data = hopped
    return data, remote_name


def _require_absent_repository_policy(anchor: str) -> None:
    """Prove that the ordinary config loader has no disk policy fallback."""
    from . import config as cfg

    root = Path(anchor)
    candidates = [
        cfg.inrepo_config_path(root),
        cfg.legacy_inrepo_config_path(root),
        root / cfg.INREPO_CONFIG_FILENAME,
    ]
    context = cfg.registry_paths.installation_context_call(
        cfg.registry_paths.installation_context,
    )
    marketplace = str(context.get("marketplaceId", "")).strip() if isinstance(context, dict) else ""
    if marketplace:
        candidates.append(
            root / cfg.MARKETPLACE_OVERLAYS_DIR / marketplace / cfg.GLOBAL_CONFIG_FILENAME
        )
    for candidate in candidates:
        if cfg.config_acquisition.source_exists(candidate, "registration-policy", strict=True):
            raise ValueError("repository policy fallback is present; authority is unavailable")


def _require_absent_policy_record(record, config: Config) -> None:
    """Admit only an independently proven local checkout."""
    from . import retained_checkout

    if not retained_checkout.is_local(record, config):
        raise ValueError("config-free registration requires a proven local checkout")


def load_project_authority_config(
    name: str, *, path: Path | None = None, record=None,
) -> Config:
    """Resolve only identity, registry, and state-root facts used by fences."""
    from . import config as cfg

    path = path or cfg.project_dir(name) / "config.yaml"
    global_raw = cfg.config_migrations.migrate_loaded(
        cfg._load_yaml_safe(cfg.global_config_path()),
        cfg.config_migrations.SCHEMA_CONFIG,
    )
    machine_raw = cfg._load_yaml_safe(path)
    dropins = cfg._load_config_d(
        path.parent / "config.d",
        project_name=name,
        warn=True,
    )
    if dropins:
        machine_raw = cfg._deep_merge(dropins, machine_raw)

    platform_name = (
        machine_raw.get("platform")
        or global_raw.get("platform")
        or cfg.detect_platform()
    )
    from .machine_identity import configured_machine_binding

    configured_machine = configured_machine_binding(machine_raw, global_raw)
    machine = configured_machine or cfg.detect_machine()
    machine_repos = machine_raw.get("repos") or {}
    machine_repo = (
        machine_repos.get(name) or {}
        if isinstance(machine_repos, dict)
        else {}
    )
    if not isinstance(machine_repo, dict):
        machine_repo = {}
    remote_name = (
        str(machine_repo.get("remote") or "")
        if "remote" in machine_repo else None
    )
    anchor = machine_repo.get("anchor") or cfg._resolve_anchor_from_registry(
        name, platform_name
    )
    if not anchor:
        raise ValueError(
            f"No repo could be resolved for project {name!r} from "
            f"{path} or the repos registry"
        )

    inrepo_authority, resolved_remote = _load_state_root_authority(
        name, str(anchor), platform_name, remote_name,
    )
    absent_policy = inrepo_authority is None
    if absent_policy:
        from dropin_registry import ScanAuthority
        from . import config_dropins, inrepo_config_source

        _require_absent_repository_policy(str(anchor))
        strict_global = cfg.config_migrations.migrate_loaded(
            inrepo_config_source.load_config_file(cfg.global_config_path(), strict=True),
            cfg.config_migrations.SCHEMA_CONFIG,
        )
        strict_machine = inrepo_config_source.load_config_file(path, strict=True)
        report = config_dropins.scan_config_dropin_registry(
            path.parent / "config.d", project_name=name, strict=True,
        )
        if report.authority not in (ScanAuthority.COMPLETE, ScanAuthority.ABSENT) or report.findings:
            raise ValueError("machine configuration fragments are unavailable")
        strict_dropins = {}
        for contribution in report.active_configs:
            strict_dropins = cfg._deep_merge(strict_dropins, contribution.raw_config)
        strict_machine = cfg._deep_merge(strict_dropins, strict_machine)
        if strict_global != global_raw or strict_machine != machine_raw:
            raise ValueError("machine configuration authority changed during acquisition")
        if ("repos" in machine_raw and not isinstance(machine_raw["repos"], dict)) or (
            not isinstance(machine_repos, dict)
        ) or (
            name in machine_repos and not isinstance(machine_repos[name], dict)
        ):
            raise ValueError("machine repository configuration is malformed")
        flags = ("stateless", "requires_external_state_root")
        if any(key in machine_repo and type(machine_repo[key]) is not bool for key in flags):
            raise ValueError("machine state-root policy is malformed")
        if record is None or any(machine_repo.get(key, False) for key in flags):
            raise ValueError("committed authority config is absent")
        inrepo_authority = {}
    stateless = bool(
        machine_repo.get("stateless", inrepo_authority.get("stateless", False))
    )
    requires_external_state_root = bool(
        machine_repo.get(
            "requires_external_state_root",
            inrepo_authority.get("requires_external_state_root", False),
        )
        or stateless
    )
    worktree_root = machine_repo.get("worktree_root") or cfg.derive_worktree_root(
        anchor
    )
    config = cfg.Config(
        srcroot=machine_raw.get("srcroot") or global_raw.get("srcroot") or "",
        machine=machine,
        platform=platform_name,
        configured_machine=configured_machine or None,
        repo_name=name,
        knowledge_repo=(
            machine_raw.get("knowledge_repo")
            or global_raw.get("knowledge_repo")
            or ""
        ),
        repos={
            name: cfg.RepoConfig(
                anchor=str(anchor),
                worktree_root=str(worktree_root),
                remote=str(inrepo_authority.get("remote", resolved_remote) or ""),
                stateless=stateless,
                requires_external_state_root=requires_external_state_root,
            ),
        },
    )
    from .machine_identity import resolve_execution_machine

    try:
        config = replace(config, machine=resolve_execution_machine(
            configured_machine or cfg.socket.gethostname(),
            guest=cfg.detect_platform() == "wsl", configured=bool(configured_machine),
            load_entries=lambda: cfg.load_machines_yaml(str(anchor), config=config),
            preserve_native_hostname=True,
        ))
    except (OSError, ValueError, TypeError, cfg.yaml.YAMLError):
        pass
    if absent_policy:
        _require_absent_policy_record(record, config)
    return config


def registration_record_config(record) -> Config:
    """Return receiving-side configuration after fencing registration."""
    from . import config as cfg
    from .execution_spaces import ExecutionSpaceError, require_record_mutation

    try:
        config = load_project_authority_config(record.repo, record=record)
        if record.repo not in config.repos:
            raise ValueError(
                "requested project is not registered in receiving-side configuration"
            )
        require_record_mutation(record, config)
        return config
    except (
        OSError, ValueError, KeyError, TypeError,
        cfg.yaml.YAMLError,
    ) as exc:
        raise ExecutionSpaceError(
            f"cannot establish receiving-side authority for project {record.repo!r}: {exc}"
        ) from exc


def require_registration_record_mutation(record) -> None:
    """Fence one session-registration record without full project policy."""
    registration_record_config(record)
