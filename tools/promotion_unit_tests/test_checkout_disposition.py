"""Actor-free checkout-data decisions with substituted filesystem/process owners."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
import stat
import subprocess
import sys
from types import CodeType, ModuleType, SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "plugins/agent-worktrees/src/agent_worktrees/checkout_data.py"
PRUNE = SOURCE.with_name("prune.py")


@dataclass
class Observation:
    agenda: bool = False
    empty: bool = False
    qualified: bool = True
    result: subprocess.CompletedProcess[str] | Exception = field(
        default_factory=lambda: subprocess.CompletedProcess(["git"], 0, "", ""),
    )
    index: subprocess.CompletedProcess[str] = field(
        default_factory=lambda: subprocess.CompletedProcess(["git"], 0, "", ""),
    )
    calls: list[dict[str, object]] = field(default_factory=list)


@pytest.fixture(scope="session")
def source_code() -> CodeType:
    return compile(SOURCE.read_bytes(), str(SOURCE), "exec")


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, source_code: CodeType) -> tuple[ModuleType, Observation]:
    state = Observation()

    class View:
        name: str

        def __init__(self, name: str | View) -> None:
            self.name = name.name if isinstance(name, View) else name

        def __truediv__(self, part: str) -> View:
            return View(self.name + "/" + part)

        def lstat(self) -> SimpleNamespace:
            if self.name.endswith("/.effort-agenda") and not state.agenda:
                raise FileNotFoundError
            if self.name.endswith("/.git") and not state.qualified:
                raise FileNotFoundError
            mode = stat.S_IFREG if self.name.endswith("/.git") else stat.S_IFDIR
            return SimpleNamespace(st_mode=mode, st_reparse_tag=0)

        def iterdir(self) -> Iterator[View]:
            return iter(()) if state.empty else iter((self / "payload",))

    def observe(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert argv[0] == "fixture-git" and "ls-files" in argv and "--ignored" not in argv
        state.calls.append(kwargs)
        if isinstance(state.result, Exception):
            raise state.result
        if any(value.startswith("--format=") for value in argv):
            return state.index
        return state.result

    package = ModuleType("promotion_checkout_disposition")
    package.__path__ = []
    identity = ModuleType(package.__name__ + ".git_repository_environment")
    vars(identity)["repository_identity_env"] = lambda: {"selected-repository": "fixture"}
    process_package = ModuleType("agent_procutil")
    process_package.__path__ = []
    process = ModuleType("agent_procutil.owned_process")
    vars(process)["run_caller_owned"] = observe
    resolver_package = ModuleType("plugin_resolve")
    resolver_package.__path__ = []
    resolver = ModuleType("plugin_resolve.git")
    vars(resolver)["resolve_git"] = lambda: "fixture-git"
    module = ModuleType(package.__name__ + ".checkout_data")
    module.__package__ = package.__name__
    module.__file__ = str(SOURCE)
    for imported in (package, identity, process_package, process, resolver_package, resolver, module):
        monkeypatch.setitem(sys.modules, imported.__name__, imported)
    exec(source_code, module.__dict__)
    monkeypatch.setattr(module, "Path", View)
    return module, state


@pytest.mark.parametrize("kind", ["agenda", "empty", "unqualified"])
def test_known_history_or_unqualified_source_never_queries(
    api: tuple[ModuleType, Observation], kind: str,
) -> None:
    module, state = api
    state.agenda = kind == "agenda"
    state.empty = kind == "empty"
    state.qualified = kind != "unqualified"
    reason = module.preservation_reason("checkout")
    assert (reason is None) is (kind == "empty")
    assert state.calls == []


@pytest.mark.parametrize("kind", ["clean", "failed", "submodule", "sparse"])
def test_index_disposition_uses_bounded_selected_observation(
    api: tuple[ModuleType, Observation], kind: str,
) -> None:
    module, state = api
    if kind == "failed":
        state.index = subprocess.CompletedProcess(["git"], 128, "", "")
    elif kind == "submodule":
        state.index = subprocess.CompletedProcess(["git"], 0, "160000\tnested\0", "")
    elif kind == "sparse":
        state.index = subprocess.CompletedProcess(["git"], 0, "040000\tsparse\0", "")
    reason = module.preservation_reason("checkout")
    assert (reason is None) is (kind == "clean")
    assert len(state.calls) == 1
    assert state.calls[0]["env"] == {
        "selected-repository": "fixture", "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1",
    }
    timeout = state.calls[0]["timeout"]
    assert isinstance(timeout, (int, float)) and 0 < timeout <= 5
    assert state.calls[0]["max_stdout_bytes"] == 32 * 1024 * 1024
    assert state.calls[0]["max_stderr_bytes"] == 8192


@pytest.mark.parametrize("kind", ["timeout", "output-limit"])
def test_unavailable_observation_preserves_data(
    api: tuple[ModuleType, Observation], kind: str,
) -> None:
    module, state = api
    state.result = subprocess.TimeoutExpired("git", 5) if kind == "timeout" else subprocess.SubprocessError("output limit")
    assert "cannot inspect the checkout index" in module.preservation_reason("checkout")


def test_cached_classification_uses_history_hint_without_fresh_process_work(
    api: tuple[ModuleType, Observation], monkeypatch: pytest.MonkeyPatch,
) -> None:
    data, state = api
    prefix = data.__package__
    assert prefix is not None
    git = ModuleType(prefix + ".git_ops")
    vars(git)["WorktreeState"] = SimpleNamespace(ACTIVE="active", COMPLETED="completed", WIP="wip", DIRTY="dirty")
    tracking = ModuleType(prefix + ".tracking")
    vars(tracking).update(WorktreeRecord=object, effective_open_follow_up_count=lambda rec: 0)
    claims = ModuleType(prefix + ".tracking_claims")
    vars(claims).update(
        ResourceClaim=object, effective_open_follow_up_count=lambda rec: 0, parse_claim_ref=lambda ref: None,
    )
    retirement = ModuleType(prefix + ".retirement_safety")
    vars(retirement)["retirement_blocker"] = lambda rec: None
    closure = ModuleType(prefix + ".closure_descriptor")
    for name in (
        "BLOCKER_CODES", "DESCRIPTOR_VERSION", "FACT_NAMES", "ClosureDescriptor",
        "assemble_closure_descriptor", "interpret_descriptor_payload",
    ):
        vars(closure)[name] = None
    lease = ModuleType(prefix + ".session_lease")
    vars(lease)["expired_finalized_self_claim"] = lambda rec, claim: False
    module = ModuleType(prefix + ".prune")
    module.__package__ = prefix
    module.__file__ = str(PRUNE)
    for imported in (git, tracking, claims, retirement, closure, lease, module):
        monkeypatch.setitem(sys.modules, imported.__name__, imported)
    exec(compile(PRUNE.read_bytes(), str(PRUNE), "exec"), module.__dict__)
    vars(module)["assess"] = lambda *a, **k: module.PruneVerdict(True, "completed-local", "cached Git facts")
    record = SimpleNamespace(
        resources=[], status="finalized", is_paired=False, checkout_managed=True, worktree_path="checkout",
    )
    result = module.cleanup_disposition(record, SimpleNamespace(state="completed", dirty=0))
    assert result.cleanable and result.bucket == "clean"
    assert state.calls == []
