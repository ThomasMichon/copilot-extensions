"""Refresh only the creation base and the committed reader's related inputs."""

from __future__ import annotations

import time
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dropin_registry import ScanAuthority

from . import config, config_acquisition, git_ops, inrepo_config_source as source
from .git_acquisition_budget import acquisition_budget
from .git_errors import GitError
from .git_repository_environment import repository_identity_scope


def prepare_worktree_base(
    anchor: Path, *, remote: str, default_branch: str, fast_forward_anchor: bool,
) -> git_ops.WorktreeBaseResult:
    """Prepare a fresh base without selecting an incompatible tracking ref."""
    with repository_identity_scope(preserve_credentials=True):
        fetched = False
        fetch_error: str | None = None
        usable_base = True
        try:
            prepared = _fetch_worktree_base(anchor, remote, default_branch)
            fetched, fetch_error = prepared.fetched, prepared.error
            usable_base = prepared.usable_base
            fast_forward_anchor = fast_forward_anchor and usable_base
        except Exception as exc:
            fetch_error = str(exc)
            fast_forward_anchor = False
            usable_base = False
        if usable_base:
            start_point = git_ops.resolve_start_point(remote, default_branch, cwd=anchor)
        else:
            start_point = f"refs/heads/{default_branch}"
            local = git_ops.git(
                "rev-parse", "--verify", f"{start_point}^{{commit}}",
                cwd=anchor, check=False, isolated_repository=True,
            )
            if local.returncode:
                raise ValueError(
                    f"creation base is unusable and local default branch '{default_branch}' "
                    "is unavailable",
                )
        branch = git_ops.current_branch(anchor)
        if not fast_forward_anchor:
            anchor_result = git_ops.FastForwardResult(False, "disabled")
        elif branch is None:
            anchor_result = git_ops.FastForwardResult(False, "detached")
        elif branch != default_branch:
            anchor_result = git_ops.FastForwardResult(False, "non-default-branch")
        else:
            anchor_result = git_ops.fast_forward_worktree(
                anchor, remote=remote, default_branch=default_branch, do_fetch=False,
            )
        return git_ops.WorktreeBaseResult(
            start_point=start_point, fetched=fetched, fetch_error=fetch_error,
            anchor=anchor_result,
        )


@dataclass(frozen=True)
class _FetchPreparation:
    fetched: bool
    error: str | None = None
    usable_base: bool = True


def _mapped_source(ref: str, pattern: str, replacement: str) -> str | None:
    if "*" not in pattern:
        return replacement if ref == pattern else None
    before, after = pattern.split("*", 1)
    if not ref.startswith(before) or not ref.endswith(after) or len(ref) < len(before) + len(after):
        return None
    end = len(ref) - len(after) if after else len(ref)
    return replacement.replace("*", ref[len(before):end])


class _RemoteSources:
    def __init__(self, anchor: Path, remote: str) -> None:
        self.anchor = anchor
        self.remote = remote
        self.cache: dict[str, str | None] = {}

    def _advertise(self, *patterns: str, symref: bool = False) -> subprocess.CompletedProcess[str]:
        options = ["--symref"] if symref else []
        try:
            return git_ops.git(
                *git_ops._auth_config_args(self.remote, cwd=self.anchor),
                "ls-remote", *options, self.remote, *patterns, cwd=self.anchor,
                timeout=git_ops.DEFAULT_FETCH_TIMEOUT, isolated_repository=True,
                auth_acquisition=True,
            )
        except subprocess.TimeoutExpired:
            raise GitError(
                ["git", "ls-remote", *options, self.remote, *patterns], 124,
                f"policy source lookup from '{self.remote}' timed out",
            ) from None

    def identity(self, ref: str) -> str:
        if ref != "HEAD":
            return ref
        if ref not in self.cache:
            self.cache[ref] = None
            result = self._advertise("HEAD", symref=True)
            symbolic: str | None = None
            object_id: str | None = None
            for line in result.stdout.splitlines():
                value, separator, name = line.partition("\t")
                if not separator or name != "HEAD":
                    continue
                if value.startswith("ref: refs/"):
                    symbolic = value.removeprefix("ref: ")
                elif re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", value):
                    object_id = value
            self.cache[ref] = symbolic or object_id
        selected = self.cache[ref]
        if selected is None:
            raise ValueError("configured policy HEAD source is unavailable")
        return selected

    def resolve(self, ref: str) -> str:
        if not ref:
            raise ValueError("committed policy fetch mapping has no source")
        if ref == "HEAD" or ref.startswith("refs/") or re.fullmatch(
            r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", ref,
        ):
            return ref
        if ref not in self.cache:
            self.cache[ref] = None
            candidates = (
                ref, f"refs/{ref}", f"refs/tags/{ref}", f"refs/heads/{ref}",
                f"refs/remotes/{ref}", f"refs/remotes/{ref}/HEAD",
            )
            result = self._advertise(*candidates)
            advertised = {
                name for line in result.stdout.splitlines()
                if "\t" in line for _, name in [line.split("\t", 1)]
                if name in candidates
            }
            selected = next((name for name in candidates if name in advertised), None)
            if selected is None:
                raise ValueError("configured policy fetch source is unavailable")
            self.cache[ref] = selected
        selected = self.cache[ref]
        if selected is None:
            raise ValueError("configured policy fetch source is unavailable")
        return selected


@dataclass(frozen=True)
class _FetchRules:
    mappings: tuple[tuple[str, str], ...]
    exclusions: tuple[str, ...]

    def sources(
        self, target: str, fallback: str, resolve: Callable[[str], str],
    ) -> tuple[set[str], set[str]]:
        candidates = {
            resolve(matched)
            for left, right in self.mappings
            if (matched := _mapped_source(target, right, left)) is not None
        } or {resolve(fallback)}
        eligible = {
            candidate for candidate in candidates
            if not any(
                _mapped_source(candidate, pattern, candidate) is not None
                for pattern in self.exclusions
            )
        }
        return candidates, eligible

    def source(
        self, target: str, fallback: str, resolve: Callable[[str], str], *,
        identity: Callable[[str], str],
        refreshed_source: str | None = None,
    ) -> str:
        candidates, eligible = self.sources(target, fallback, resolve)
        if (
            not eligible and refreshed_source is not None
            and {identity(candidate) for candidate in candidates} == {refreshed_source}
        ):
            eligible = candidates
        if not eligible:
            raise ValueError("committed policy fetch mapping excludes its source")
        if len(eligible) != 1:
            raise ValueError("committed policy fetch mapping is ambiguous")
        return next(iter(eligible))


def _fetch_rules(anchor: Path, remote: str) -> _FetchRules:
    result = git_ops.git(
        "config", "--get-all", f"remote.{remote}.fetch",
        cwd=anchor, check=False, isolated_repository=True,
    )
    if result.returncode not in (0, 1):
        raise ValueError("committed policy fetch mapping is unavailable")
    mappings: list[tuple[str, str]] = []
    exclusions: list[str] = []
    for line in result.stdout.splitlines():
        spec = line.removeprefix("+")
        if spec.startswith("^"):
            exclusions.append(spec[1:])
            continue
        left, separator, right = spec.partition(":")
        if not separator or not right:
            continue
        if left.count("*") != right.count("*") or left.count("*") > 1:
            raise ValueError("committed policy fetch mapping is invalid")
        mappings.append((left, right))
    return _FetchRules(tuple(mappings), tuple(exclusions))


def _fetch_worktree_base(anchor: Path, remote: str, default_branch: str) -> _FetchPreparation:
    from contextlib import nullcontext
    from . import tracking_write

    deadline = time.monotonic() + git_ops.DEFAULT_FETCH_TIMEOUT
    with nullcontext() if tracking_write.foreground_completion_active() else acquisition_budget(deadline):
        remotes = git_ops.git("remote", cwd=anchor, isolated_repository=True).stdout.splitlines()
        if remote not in remotes:
            return _FetchPreparation(False)
        policy_remote = "origin" if "origin" in remotes else remote
        return _refresh_sources(anchor, remote, default_branch, deadline, policy_remote)


def _refresh_sources(
    anchor: Path, remote: str, default_branch: str, deadline: float, policy_remote: str,
) -> _FetchPreparation:
    from . import tracking_write

    attempted: dict[tuple[str, str, str], bool] = {}
    errors: list[Exception] = []
    base_source = f"refs/heads/{default_branch}"
    base_target = f"refs/remotes/{remote}/{default_branch}"

    def _refresh(fetch_remote: str, fetch_source: str, target: str) -> bool:
        key = (fetch_remote, fetch_source, target)
        if key not in attempted:
            attempted[key] = False
            try:
                git_ops._fetch_refspecs(
                    fetch_remote, cwd=anchor, refspec=(f"+{fetch_source}:{target}",),
                    timeout=None if tracking_write.foreground_completion_active()
                    else git_ops.DEFAULT_FETCH_TIMEOUT, ignore_refmaps=True,
                )
                attempted[key] = True
            except Exception as exc:
                errors.append(exc)
        return attempted[key]

    fetched = False
    parent_observations = config_acquisition.current_observations()
    try:
        with config_acquisition.capture_config_observations() as observations:
            try:
                rules = _fetch_rules(anchor, policy_remote)
                resolver = _RemoteSources(anchor, policy_remote)
                if base_target.startswith("refs/remotes/origin/"):
                    matches_base = False
                    if policy_remote == remote:
                        branch = base_target.removeprefix("refs/remotes/origin/")
                        candidates, eligible = rules.sources(
                            base_target, f"refs/heads/{branch}", resolver.resolve,
                        )
                        matches_base = {
                            resolver.identity(candidate) for candidate in eligible or candidates
                        } == {base_source}
                    if not matches_base:
                        return _FetchPreparation(
                            False, "creation base conflicts with its configured policy source", False,
                        )
            except Exception as exc:
                return _FetchPreparation(False, str(exc), False)

            fetched = _refresh(remote, base_source, base_target)

            def _refresh_policy(branch: str) -> None:
                target = f"refs/remotes/origin/{branch}"
                matching_base = (
                    base_source if fetched and policy_remote == remote and target == base_target else None
                )
                try:
                    selected = rules.source(
                        target, f"refs/heads/{branch}", resolver.resolve,
                        identity=resolver.identity,
                        refreshed_source=matching_base,
                    )
                    _refresh(policy_remote, resolver.identity(selected), target)
                except Exception as exc:
                    errors.append(exc)

            try:
                if tracking_write.foreground_completion_active():
                    deadline = time.monotonic() + git_ops.DEFAULT_FETCH_TIMEOUT
                policy_branch = source._offline_remote_head_branch(anchor, "origin", deadline)
                if policy_branch:
                    _refresh_policy(policy_branch)
                    candidates_paths = (
                        config.inrepo_config_path(Path()),
                        config.legacy_inrepo_config_path(Path()),
                        Path(config.INREPO_CONFIG_FILENAME),
                    )
                    bootstrap = source._read_committed_batch(
                        anchor, "origin", policy_branch, candidates_paths, deadline,
                    )
                    if bootstrap is not None:
                        data = next((
                            source.parse_yaml_text_safe(bootstrap[path])
                            for path in candidates_paths if path in bootstrap
                        ), {})
                        declared = data.get("default_branch")
                        if isinstance(declared, str) and declared:
                            _refresh_policy(declared)
                if any(o.authority is ScanAuthority.INDETERMINATE for o in observations):
                    errors.append(ValueError("committed policy refresh could not verify its source"))
            except Exception as exc:
                errors.append(exc)
    finally:
        if parent_observations is not None:
            parent_observations.extend(observations)
    return _FetchPreparation(fetched, str(errors[0]) if errors else None)
