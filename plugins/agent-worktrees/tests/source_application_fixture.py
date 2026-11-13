"""Committed, account-isolated inputs for opt-in complete-application tests."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from functools import wraps
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable, Iterator

from runtime_install_fixture import isolate_source_account_profile


@contextmanager
def trace_preparation(*, deadline: float) -> Iterator[None]:
    """Time existing preparation calls without replacing their work or budget."""
    from unittest.mock import patch

    import build_toolchain_lock as toolchain
    import local_install_source as provider
    import promote_release as promotion

    def timed(function: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(function)
        def invoke(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            label = function.__name__
            print(
                f"Application preparation: {label} "
                f"({max(0.0, deadline - started):.3f}s remaining)",
                file=sys.stderr, flush=True,
            )
            outcome = "failed"
            try:
                result = function(*args, **kwargs)
                outcome = "passed"
                return result
            finally:
                print(
                    f"Application preparation {outcome}: {label} "
                    f"({time.monotonic() - started:.3f}s)",
                    file=sys.stderr, flush=True,
                )
        return invoke

    with ExitStack() as stack:
        load_module = promotion._load_module

        def traced_module(path: Path, name: str) -> Any:
            module = load_module(path, name)
            if name == "promote_materialize_main":
                for function in (
                    "materialize_file_pointers", "materialize_uv_editable_refs",
                    "materialize_installer_engine_refs", "materialize_launch_wrapper_assets",
                ):
                    stack.enter_context(patch.object(module, function, timed(getattr(module, function))))
            elif name == "promote_accumulate_bumps":
                for function in ("read_changefiles", "_consume_changefiles"):
                    stack.enter_context(patch.object(module, function, timed(getattr(module, function))))
            return module

        stack.enter_context(patch.object(promotion, "_load_module", traced_module))
        for owner, name in (
            (provider, "compose"), (provider, "_source_projects"),
            (provider, "_build_input_roots"), (provider, "_build_input_fingerprint"),
            (toolchain, "resolve_toolchain_lock"), (provider, "_prepare_profiles"),
            (promotion, "add_scratch_worktree"), (promotion, "consume_pending_changes"),
            (promotion, "_seed_versions_from_main"), (promotion, "_sync_instruction_projections"),
            (promotion, "build_promotion_tree"), (promotion, "remove_scratch_worktree"),
        ):
            stack.enter_context(patch.object(owner, name, timed(getattr(owner, name))))
        yield


def application_source(
    repository: Path, workspace: Path, home: Path, *, deadline: float,
) -> tuple[Path, str]:
    import prepare_runtime_source as preparation

    @contextmanager
    def phase(label: str):
        started = time.monotonic()
        print(
            f"Application source phase: {label} "
            f"({max(0.0, deadline - started):.3f}s remaining)", flush=True,
        )
        outcome = "failed"
        try:
            yield
            outcome = "passed"
        finally:
            print(
                f"Application source phase {outcome}: {label} "
                f"({time.monotonic() - started:.3f}s)", flush=True,
            )

    workspace.mkdir()
    with phase("select committed source"):
        commit = preparation._git(repository, "rev-parse", "HEAD", deadline=deadline)
    checkout = workspace / "checkout"
    checkout.mkdir()
    hooks = workspace / "empty-hooks"
    hooks.mkdir()

    def git(*arguments: str) -> str:
        with phase("git " + " ".join(arguments)):
            return preparation._git(
                checkout, "-c", f"core.hooksPath={hooks}", "-c", "core.autocrlf=false",
                "-c", "user.name=Runtime test", "-c", "user.email=runtime-test@example.invalid",
                "-c", "commit.gpgsign=false", *arguments, deadline=deadline,
            )

    git("init", "--quiet", "--template", str(hooks))
    attributes = checkout / ".git" / "info" / "attributes"
    attributes.parent.mkdir(exist_ok=True)
    attributes.write_text("* -text -filter -ident -working-tree-encoding\n", encoding="utf-8")
    git("fetch", "--upload-pack=git -c pack.window=0 -c pack.compression=0 upload-pack",
        "--quiet", "--no-tags", "--no-recurse-submodules", "--depth=1",
        str(repository), commit)
    git(*(("-c", "checkout.workers=2") if os.name == "nt" else ()),
        "checkout", "--quiet", "--detach", commit)
    with phase("verify ordinary committed source"):
        if preparation.materializer.uer._find_symlink(checkout, skip_test_runs=False) is not None:
            raise AssertionError("application fixture source contains a symlink")
    with phase("isolate account profile"):
        isolate_source_account_profile(checkout, home)
    git("add", "--all", "--force", ".")
    if git("diff", "--cached", "--name-only"):
        try:
            git("-c", "trace2.eventTarget=2", "commit", "--quiet", "-m",
                "Account-isolated application fixture")
        except subprocess.TimeoutExpired as error:
            if error.stderr:
                detail = error.stderr
                if isinstance(detail, bytes):
                    detail = detail.decode("utf-8", errors="replace")
                print("Fixture Git commit timeout trace:\n" + detail[-16384:], flush=True)
            raise
    selected = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/dev", selected)
    with phase("admit isolated source"):
        preparation._admit(checkout, selected, "dev", deadline=deadline)
    return checkout, selected


def prepare_application(
    checkout: Path, commit: str, component: str, output: Path, *,
    uv: str, deadline: float, extras: tuple[str, ...] = (),
) -> Path:
    from local_install_source import prepare_applications

    snapshots = prepare_applications(
        checkout, commit, commit, output, components=(component,), branch="dev",
        uv=uv, deadline=deadline, extras={component: extras},
        source_descriptor={"source": "github", "repo": "Example/Plugins"},
    )
    return Path(snapshots[component])


def assert_application_reuse(
    checkout: Path, commit: str, component: str, output: Path, expected: Path, *,
    uv: str, deadline: float, extras: tuple[str, ...] = (),
) -> None:
    from unittest.mock import patch

    import local_install_source as provider

    completion = expected.parents[2] / "applications.json"
    before = completion.read_bytes(), completion.stat().st_mtime_ns
    with patch.object(provider, "_built_projects", side_effect=AssertionError(
        "unchanged complete application preparation attempted fresh construction",
    )):
        observed = prepare_application(
            checkout, commit, component, output, uv=uv, deadline=deadline, extras=extras,
        )
    assert observed == expected, "complete preparation did not reuse its original snapshot"
    assert (completion.read_bytes(), completion.stat().st_mtime_ns) == before
    assert not (output / "applications.json").exists(), "reuse published another completion"
