"""Group a daemon process census into generations for ``zdd.diagnostics``.

On Windows a daemon started through a venv ``python.exe``/``pythonw.exe``
redirector appears twice in a process census: the redirector (the *launcher*)
and the base interpreter it starts (the *worker*), sharing one command line
and cwd. Both are one generation. Counting them as two daemons would let
duplicate repair terminate half of the only live generation.

Grouping is purely structural -- parent PID, executable image and start
order -- and opt-in per consumer (``DiagnosticContext.group_launchers``). A
census that cannot be grouped with confidence reports *uncertain*, and every
terminating repair is then refused (fail closed): diagnostics may report what
it sees, but never kills on a guess.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .diagnostics import DaemonCandidate, DiagnosticContext

__all__ = [
    "GROUPING_UNCERTAIN",
    "Generation",
    "generation_of",
    "group_generations",
    "is_venv_redirector",
    "same_generation",
]

GROUPING_UNCERTAIN = "generation grouping uncertain"
TERMINATING_KINDS = frozenset(
    {"abandoned_passive", "superseded_generation", "duplicate_resident"}
)
_VENV_LAUNCHER_NAMES = frozenset({"python.exe", "pythonw.exe"})


def is_venv_redirector(image: str | None) -> bool:
    """Whether ``image`` is a Windows venv launcher (``<venv>/Scripts/python*.exe``).

    Identified by layout, not by name alone: the executable sits in a
    ``Scripts`` directory whose parent carries the venv's ``pyvenv.cfg``. A
    base interpreter (the worker a redirector starts) never does.
    """
    if not image:
        return False
    scripts_dir, name = os.path.split(image.replace("\\", "/"))
    if name.lower() not in _VENV_LAUNCHER_NAMES:
        return False
    venv_root, scripts = os.path.split(scripts_dir)
    if scripts.lower() != "scripts" or not venv_root:
        return False
    return os.path.isfile(os.path.join(venv_root, "pyvenv.cfg"))


@dataclass(frozen=True)
class Generation:
    """One live daemon generation: its worker and, if any, its launcher."""

    worker: DaemonCandidate
    launcher: DaemonCandidate | None = None

    @property
    def pids(self) -> frozenset[int]:
        if self.launcher is None:
            return frozenset({self.worker.pid})
        return frozenset({self.worker.pid, self.launcher.pid})

    @property
    def key(self) -> str:
        return f"{self.worker.pid}:{self.worker.start_time}"

    def targets(self) -> list[dict[str, object]]:
        """Repair targets for this generation, worker first.

        Terminating the worker first lets the launcher exit on its own; a
        launcher that survives it is an unpaired launcher on the next census,
        which makes that census uncertain (no automatic repair).
        """
        if self.launcher is None:
            return [self.worker.to_dict()]
        worker = self.worker.to_dict()
        worker["role"] = "worker"
        launcher = self.launcher.to_dict()
        launcher["role"] = "launcher"
        return [worker, launcher]

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "worker_pid": self.worker.pid,
            "launcher_pid": self.launcher.pid if self.launcher is not None else None,
        }


def _started_after(parent: DaemonCandidate, child: DaemonCandidate) -> bool:
    try:
        return int(str(child.start_time)) >= int(str(parent.start_time))
    except ValueError:
        return False


def _unreadable_image(candidate: DaemonCandidate) -> str:
    return f"executable of candidate {candidate.pid} unreadable"


def _outside_parent_problem(
    candidate: DaemonCandidate, is_launcher_image: Callable[[str], bool]
) -> str | None:
    """Why a worker whose parent the census missed may be half a generation.

    A census can drop a live launcher (for example when its command line is
    unreadable) while still capturing its worker, which then carries only
    the launcher's PID. Unless that parent is proven not to be a launcher --
    no real parent (PID 0/1), provably gone, or a readable non-launcher
    image -- the worker is not a proven whole generation.
    """
    ppid = candidate.ppid
    if ppid is None or ppid in (0, 1, candidate.pid) or candidate.parent_gone:
        return None
    if not candidate.image or is_launcher_image(candidate.image):
        # Unreadable images are already uncertain; a launcher's own parent is
        # never the other half of its generation.
        return None
    if candidate.parent_image is None:
        return f"parent {ppid} of candidate {candidate.pid} not in census"
    if is_launcher_image(candidate.parent_image):
        return f"launcher parent {ppid} of candidate {candidate.pid} not in census"
    return None


def _pair_problem(
    parent: DaemonCandidate,
    child: DaemonCandidate,
    candidates: dict[int, DaemonCandidate],
    children: dict[int, list[DaemonCandidate]],
    is_launcher_image: Callable[[str], bool],
) -> str | None:
    if len(children[parent.pid]) != 1:
        return f"candidate {parent.pid} parents {len(children[parent.pid])} candidates"
    if parent.ppid in candidates or child.pid in children:
        return f"candidates {parent.pid} and {child.pid} are part of a longer chain"
    for member in (parent, child):
        if not member.image:
            return _unreadable_image(member)
    if not is_launcher_image(parent.image) or is_launcher_image(child.image):
        return (
            f"candidate {child.pid} is a child of candidate {parent.pid} but not "
            "a launcher/worker pair"
        )
    if not _started_after(parent, child):
        return f"start order of candidates {parent.pid} and {child.pid} unproven"
    return None


def group_generations(
    candidates: dict[int, DaemonCandidate],
    *,
    structural: bool,
    is_launcher_image: Callable[[str], bool] | None = None,
) -> tuple[list[Generation], str | None]:
    """Group ``candidates`` into generations; return ``(generations, uncertainty)``.

    Without ``structural`` grouping (the default for consumers that never
    launch through a redirector), each candidate is its own generation. With
    it, a candidate C and its parent P form one generation only when P is a
    launcher image, C is not, P parents no other candidate, neither belongs
    to a longer candidate chain, and C provably started after P. Any
    candidate whose parent or executable is unreadable, any launcher-image
    candidate with no captured worker (its worker may be the half the census
    missed), any worker whose live parent the census did not capture and
    which is not proven to be a non-launcher (the launcher may be the missed
    half), or any parent/child relation that fails those checks, makes the
    grouping uncertain (the returned reason).
    """
    ordered = [candidate for _, candidate in sorted(candidates.items())]
    # Only a disabled grouping or an empty census skips the structural checks:
    # a lone candidate whose parent or executable is unreadable is still
    # uncertain (it may be half of a launcher/worker pair whose other half the
    # census missed, and without its image it cannot be shown not to be).
    if not structural or not ordered:
        return [Generation(worker=candidate) for candidate in ordered], None
    classify = is_launcher_image or is_venv_redirector
    problems: list[str] = []
    children: dict[int, list[DaemonCandidate]] = {}
    for candidate in ordered:
        if not candidate.image:
            problems.append(_unreadable_image(candidate))
        if candidate.ppid is None:
            problems.append(f"parent of candidate {candidate.pid} unreadable")
        elif candidate.ppid in candidates and candidate.ppid != candidate.pid:
            children.setdefault(candidate.ppid, []).append(candidate)
        else:
            outside = _outside_parent_problem(candidate, classify)
            if outside is not None:
                problems.append(outside)
    worker_of: dict[int, DaemonCandidate] = {}
    for parent_pid, kids in children.items():
        parent = candidates[parent_pid]
        problem = _pair_problem(parent, kids[0], candidates, children, classify)
        if problem is not None:
            problems.append(problem)
            continue
        worker_of[parent_pid] = kids[0]
    paired_workers = {worker.pid for worker in worker_of.values()}
    generations = []
    for candidate in ordered:
        if candidate.pid in paired_workers:
            continue
        if candidate.pid in worker_of:
            generations.append(Generation(worker=worker_of[candidate.pid], launcher=candidate))
            continue
        if candidate.image and classify(candidate.image) and candidate.pid not in children:
            # A launcher whose worker the census did not capture: the
            # generation boundary is unproven, so never repair it as if it
            # were a whole single-process generation.
            problems.append(f"launcher candidate {candidate.pid} has no captured worker")
        generations.append(Generation(worker=candidate))
    return generations, "; ".join(dict.fromkeys(problems)) or None


def generation_of(generations: list[Generation], pid: object) -> Generation | None:
    if not isinstance(pid, int):
        return None
    for generation in generations:
        if pid in generation.pids:
            return generation
    return None


def same_generation(generations: list[Generation], a: object, b: object) -> bool:
    if a == b:
        return True
    generation = generation_of(generations, a)
    return generation is not None and b in generation.pids


def owner_pids(owner: dict[str, object] | None) -> set[int]:
    """Every PID of the validated owner's generation (never a repair target)."""
    if not isinstance(owner, dict):
        return set()
    pids = owner.get("generation_pids")
    if isinstance(pids, list):
        return {pid for pid in pids if isinstance(pid, int)}
    pid = owner.get("pid")
    return {pid} if isinstance(pid, int) else set()


def block_on_uncertain_grouping(
    findings: list[dict[str, object]], reason: str | None
) -> None:
    """Fail closed: no terminating finding is repairable on an uncertain census."""
    if reason is None:
        return
    for finding in findings:
        if finding.get("kind") in TERMINATING_KINDS:
            finding["repairable"] = False
            finding["blocked_reason"] = GROUPING_UNCERTAIN
            finding["grouping_detail"] = reason


def _blocked_reason(ctx: DiagnosticContext, *checks: tuple[bool, str]) -> str | None:
    if not ctx.repair_supported:
        return "identity-bound repair unsupported on this platform"
    for failed, reason in checks:
        if failed:
            return reason
    return None


def inspect_superseded_generations(
    ctx: DiagnosticContext,
    table: dict | None,
    candidates: dict[int, DaemonCandidate],
    generations: list[Generation],
) -> tuple[dict[str, object] | None, set[int]]:
    if not isinstance(table, dict):
        return None, set()
    targets: list[dict[str, object]] = []
    matched: set[int] = set()
    for slot in ("previous", "active"):
        raw = table.get(slot)
        if not isinstance(raw, dict):
            continue
        pid = raw.get("pid")
        candidate = candidates.get(pid) if isinstance(pid, int) else None
        generation = generation_of(generations, pid)
        if candidate is None or generation is None or generation.pids & matched:
            continue
        try:
            routed_generation = int(raw.get("generation", 0))
        except (TypeError, ValueError):
            continue
        if routed_generation <= 0 or not ctx.is_superseded(candidate.pid, routed_generation):
            continue
        matched |= generation.pids
        for item in generation.targets():
            item["slot"] = slot
            item["generation"] = routed_generation
            targets.append(item)
    if not targets:
        return None, set()
    blocked = _blocked_reason(
        ctx, (not all(item.get("start_time") for item in targets), "candidate start_time unavailable")
    )
    return (
        {
            "kind": "superseded_generation",
            "summary": "a routed, superseded generation is still running past self-retire",
            "repairable": blocked is None,
            "blocked_reason": blocked,
            "targets": targets,
        },
        matched,
    )


def inspect_duplicates(
    ctx: DiagnosticContext,
    generations: list[Generation],
    owner: dict[str, object] | None,
    excluded_pids: set[int],
) -> dict[str, object] | None:
    """A duplicate is a second *generation*, never the other half of one."""
    if len(generations) <= 1:
        return None
    protected = owner_pids(owner)
    targets = [
        item
        for generation in generations
        if not protected or not (generation.pids & (protected | excluded_pids))
        for item in generation.targets()
    ]
    if not targets:
        return None
    blocked = _blocked_reason(
        ctx,
        (not protected, "no validated live owner"),
        (not all(item.get("start_time") for item in targets), "candidate start_time unavailable"),
    )
    return {
        "kind": "duplicate_resident",
        "summary": "more than one live daemon matches the resident active slot",
        "repairable": blocked is None,
        "blocked_reason": blocked,
        "owner": owner,
        "generation_count": len(generations),
        "targets": targets,
    }
