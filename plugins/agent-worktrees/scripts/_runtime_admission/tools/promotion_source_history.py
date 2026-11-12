"""Read frozen promotion facts from an independently acquired Git history."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

PIPELINE_STATE_PATH = ".github/release-pipeline-state.json"
COVERAGE_BASELINES_DIR = ".github/coverage-baselines"
_SHA = re.compile(r"[0-9a-f]{40}")


class SourceHistoryError(RuntimeError):
    pass


def _git(repo: Path, arguments: list[str], *, deadline: float | None = None, index=None) -> bytes:
    timeout = 30
    if deadline is not None:
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
            raise SourceHistoryError("source history needs a finite deadline")
        timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            raise SourceHistoryError("source history deadline exceeded")
    environment = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
    }
    environment.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    if index is not None:
        environment["GIT_INDEX_FILE"] = str(index)
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-c", "core.fsmonitor=false",
             "-c", "core.longpaths=true", *arguments],
            cwd=repo, env=environment, capture_output=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SourceHistoryError("cannot read acquired source history") from error
    if result.returncode:
        raise SourceHistoryError("cannot read acquired source history")
    if deadline is not None and time.monotonic() >= deadline:
        raise SourceHistoryError("source history deadline exceeded")
    return result.stdout


def content_tree(tree_sha: str, *, repo: Path, deadline: float | None = None) -> str:
    """Exclude the publisher's bookkeeping using one disposable Git index."""
    if not isinstance(tree_sha, str) or not _SHA.fullmatch(tree_sha):
        raise SourceHistoryError("source content needs an exact Git tree")
    with tempfile.TemporaryDirectory() as temporary:
        index = Path(temporary) / "index"
        def git(arguments):
            return _git(repo, arguments, deadline=deadline, index=index)

        git(["read-tree", tree_sha])
        git(["rm", "--cached", "-f", "-q", "--ignore-unmatch", PIPELINE_STATE_PATH])
        git(["rm", "--cached", "-f", "-q", "-r", "--ignore-unmatch", COVERAGE_BASELINES_DIR])
        return git(["write-tree"]).decode("ascii").strip()


def is_ancestor(repo: Path, ancestor: str, descendant: str, *, deadline: float | None = None) -> bool:
    if any(not isinstance(value, str) or not _SHA.fullmatch(value) for value in (ancestor, descendant)):
        raise SourceHistoryError("source ancestry needs exact Git commits")
    # Unlike a missing-object/error result, a different merge base is ordinary
    # negative ancestry. The acquisition owner requires complete Git history.
    return _git(
        repo, ["merge-base", ancestor, descendant], deadline=deadline,
    ).decode("ascii").strip() == ancestor


@dataclass(frozen=True)
class PromotedSource:
    main_commit: str
    dev_head: str
    main_before: str
    content_tree: str


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SourceHistoryError("duplicate source promotion state field")
        result[key] = value
    return result


def read_promoted_source(
    repo: Path, main_commit: str, *, deadline: float | None = None,
) -> PromotedSource:
    """Bind the channel's Dev key to the caller's retained Main selection.

    This reads data only, never imports or executes tooling from that source.
    The release reader still authenticates the resulting index and attestation.
    """
    if not isinstance(main_commit, str) or not _SHA.fullmatch(main_commit):
        raise SourceHistoryError("source promotion needs an exact Main commit")
    def git(arguments):
        return _git(repo, arguments, deadline=deadline)

    if git(["cat-file", "-t", main_commit]).strip() != b"commit":
        raise SourceHistoryError("selected Main is not a Git commit")
    parents = git(["show", "-s", "--format=%P", main_commit]).decode("ascii").split()
    entry = git(["ls-tree", "-z", main_commit, "--", PIPELINE_STATE_PATH]).split(b"\0")
    if len(parents) != 1 or len(entry) != 2 or not entry[0] or entry[1]:
        raise SourceHistoryError("selected Main has no single-parent promotion provenance")
    metadata, path = entry[0].split(b"\t", 1)
    mode, kind, blob = metadata.decode("ascii").split()
    if (
        mode != "100644" or kind != "blob" or path.decode("ascii") != PIPELINE_STATE_PATH
        or int(git(["cat-file", "-s", blob])) > 1 << 20
    ):
        raise SourceHistoryError("selected Main has unsupported promotion state")
    try:
        state = json.loads(git(["cat-file", "blob", blob]), object_pairs_hook=_unique)
    except (UnicodeError, ValueError) as error:
        raise SourceHistoryError("selected Main has invalid promotion state") from error
    promotion = state.get("last_promotion") if isinstance(state, dict) else None
    if (
        not isinstance(state, dict) or state.get("schema") != "copilot-extensions.release-pipeline-state"
        or type(state.get("version")) is not int or state["version"] != 1
        or not isinstance(promotion, dict)
        or not isinstance(promotion.get("dev_head"), str) or not _SHA.fullmatch(promotion["dev_head"])
        or promotion.get("main_before") != parents[0]
        or promotion.get("release_id") != promotion["dev_head"]
        or promotion.get("tag") != "promote-dev-" + promotion["dev_head"]
        or git(["cat-file", "-t", promotion["dev_head"]]).strip() != b"commit"
    ):
        raise SourceHistoryError("selected Main differs from its recorded frozen source")
    tree = git(["rev-parse", main_commit + "^{tree}"]).decode("ascii").strip()
    return PromotedSource(
        main_commit, promotion["dev_head"], parents[0],
        content_tree(tree, repo=repo, deadline=deadline),
    )
