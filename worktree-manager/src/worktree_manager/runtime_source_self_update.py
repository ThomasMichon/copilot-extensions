"""Acquire and install Manager's independent source without changing its data root."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from pathlib import Path
import shutil
import subprocess
import time
from typing import TYPE_CHECKING
import uuid

from agent_procutil import no_window_kwargs

from .runtime_source import SourceAcquisitionError, acquire
from .runtime_source_config import RuntimeSource
from .runtime_source_prepare import prepare, prepare_applications, preparer
from .self_install_runtime import load_runtime_installer, runtime_scripts, running_payload_dir

if TYPE_CHECKING:
    from .self_install import SelfUpdateResult


def run_update(
    *, ref: str | None = None, root: Path | None = None, dry_run: bool = False,
) -> SelfUpdateResult:
    """Keep complete prepared inputs held through publication and cutover."""
    from . import self_install as owner

    previous = owner.current_version(root)
    result = None
    try:
        with ExitStack() as source_custody:
            result = _update(ref=ref, root=root, dry_run=dry_run, source_custody=source_custody)
        return result
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return owner.SelfUpdateResult(
            action="error", previous=previous, version=result.version if result else None,
            reason=f"prepared source handoff failed: {error}",
        )


def _update(
    *, ref: str | None, root: Path | None, dry_run: bool, source_custody: ExitStack,
) -> SelfUpdateResult:
    """Preserve existing Main/custom-ref acquisition and the canonical activation owner."""
    from . import engine_client, self_install as owner, source_config, update_auth

    installation = (root or owner.default_root()).resolve()
    ref = ref or owner.manager_ref(installation)
    repo = owner.manager_repo(installation)
    previous = owner.current_version(installation)
    staging = installation / owner.STAGING_DIR
    payload = staging / "worktree-manager"
    source_options = {}
    use_git = shutil.which("git") is not None
    try:
        repo = source_config.absolute_local_source(repo)
        if ref == "dev" and not use_git:
            return owner.SelfUpdateResult(
                action="skipped", previous=previous,
                reason="Manager merged-Dev acquisition requires git; no unqualified tarball fallback",
            )
        fetch_root = installation / "manager-source" if ref == "dev" else staging
        fetch_root.mkdir(parents=True, exist_ok=True)
        auth_env = update_auth.environment(repo, cwd=fetch_root) if use_git else None
        environment = {"env": auth_env} if auth_env is not None else {}
        if ref == "dev":
            payload, source_options = source_custody.enter_context(
                prepared(repo, installation, environment=auth_env),
            )
        elif use_git:
            git = ["git", "-c", "core.longpaths=true"]
            existing = (staging / ".git").is_dir()
            if existing:
                subprocess.run([*git, "-C", str(staging), "fetch", "--depth", "1",
                                repo, ref], check=True, capture_output=True,
                               text=True, timeout=180, **environment, **no_window_kwargs())
            else:
                owner._clear_dir(staging)
                subprocess.run([*git, "clone", "--no-checkout", "--depth", "1", "--branch", ref,
                                repo, str(staging)], check=True,
                               capture_output=True, text=True, timeout=300, cwd=staging,
                               **environment, **no_window_kwargs())
            sparse = staging / ".git" / "info" / "sparse-checkout"
            sparse.parent.mkdir(parents=True, exist_ok=True)
            sparse.write_text("/worktree-manager/\n/libs/\n/.gitattributes\n", encoding="utf-8")
            subprocess.run([*git, "-c", "core.sparseCheckout=true",
                            "-c", "core.sparseCheckoutCone=false", "-C", str(staging),
                            "checkout", "-q", "FETCH_HEAD" if existing else "HEAD"], check=True,
                            capture_output=True, text=True, timeout=60, **environment, **no_window_kwargs())
        else:
            url = owner.manager_tarball_url(installation, ref)
            if url is None:
                return owner.SelfUpdateResult(
                    action="skipped", previous=previous,
                    reason="git not found and source is not a GitHub repo "
                           "(cannot fetch a tarball) -- install git to update")
            owner._fetch_via_tarball(staging, url)
    except update_auth.AuthRequired as error:
        return owner.SelfUpdateResult(action="auth_required", previous=previous, reason=str(error))
    except (OSError, ValueError, subprocess.SubprocessError, engine_client.EngineError) as error:
        detail = f"\n{error.stderr.strip()}" if isinstance(error, subprocess.CalledProcessError) and error.stderr else ""
        return owner.SelfUpdateResult(
            action="error", previous=previous, reason=f"fetch failed: {error}{detail}",
        )

    if not (payload / "pyproject.toml").is_file():
        return owner.SelfUpdateResult(
            action="error", previous=previous, reason=f"fetched payload not found at {payload}",
        )
    cutover = None

    def after_activate(slot: Path) -> None:
        nonlocal cutover
        try:
            cutover = owner._runtime_installer()._activate_after_bootstrap_update(installation, slot)
        except owner._runtime_installer().ActivationStarted as committed:
            cutover = committed.outcome
            raise

    result = owner.self_install(
        payload_dir=payload, root=installation, dry_run=dry_run,
        _after_activate=after_activate if not dry_run else None,
        _transactional_activation=True, **source_options,
    )
    if result.action == "error":
        return owner.SelfUpdateResult(
            action="error", version=result.version, previous=previous, reason=result.reason,
        )
    return owner.SelfUpdateResult(
        action="already-current" if result.action == "already-current" else "updated",
        version=result.version, previous=previous, cutover=cutover,
    )


@contextmanager
def prepared(
    repo: str, root: Path, *, environment: dict[str, str] | None = None,
):
    """Hold Manager's independent selection and complete inputs through installation."""
    deadline = time.monotonic() + 600
    base = root / "manager-source"
    selection = RuntimeSource("dev", repo=repo, allow_dev=True)
    output = base / "runtime-sources" / "applications" / uuid.uuid4().hex
    tool = preparer()
    scripts = runtime_scripts(Path(__file__).resolve().parent, running_payload_dir)
    installer = load_runtime_installer(scripts)
    try:
        with acquire(selection, root=base, environment=environment, deadline=deadline) as resolved:
            snapshots = prepare_applications(
                tool, resolved, ("worktree-manager",), output,
                deadline=deadline, environment=environment,
            )
        with installer.prepared_manager_inputs(snapshots["worktree-manager"]) as (selected, options):
            composition = selected.composition
            if (
                selected.identity.source_commit != resolved.commit
                or selected.identity.source_tree != resolved.tree
                or not isinstance(composition, dict) or composition.get("main_seed") != resolved.main_head
                or "application" not in options
            ):
                raise ValueError("Manager complete inputs differ from its independent source selection")
            yield selected.payload, {"_" + name: value for name, value in options.items()}
    except SourceAcquisitionError as error:
        raise ValueError(f"Manager merged-Dev acquisition failed: {error}") from error


def fetch(
    repo: str, root: Path, *, environment: dict[str, str] | None = None,
) -> Path:
    base = root / "manager-source"
    selection = RuntimeSource("dev", repo=repo, allow_dev=True)
    tool = preparer()
    try:
        with acquire(selection, root=base, environment=environment) as resolved:
            record = prepare(tool, resolved, "worktree-manager", base / "runtime-sources/prepared")
    except SourceAcquisitionError as error:
        raise ValueError(f"Manager merged-Dev acquisition failed: {error}") from error
    return Path(record["payload"])
