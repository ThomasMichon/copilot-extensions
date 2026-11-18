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


@dataclass
class Observation:
    agenda: bool = False
    empty: bool = False
    qualified: bool = True
    result: subprocess.CompletedProcess[str] | Exception = field(
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
        def __init__(self, name: str) -> None:
            self.name = name

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
        assert argv[:2] == ["fixture-git", "ls-files"]
        state.calls.append(kwargs)
        if isinstance(state.result, Exception):
            raise state.result
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


@pytest.mark.parametrize("kind", ["clean", "ignored", "failed", "oversized"])
def test_ignored_disposition_uses_bounded_selected_observation(
    api: tuple[ModuleType, Observation], kind: str,
) -> None:
    module, state = api
    text = "evidence/\0" if kind == "ignored" else "x" * 65537 if kind == "oversized" else ""
    state.result = subprocess.CompletedProcess(["git"], 128 if kind == "failed" else 0, text, "")
    reason = module.preservation_reason("checkout")
    assert (reason is None) is (kind == "clean")
    assert len(state.calls) == 1
    assert state.calls[0]["env"] == {"selected-repository": "fixture"}
    assert state.calls[0]["timeout"] == 5
    assert state.calls[0]["max_stdout_bytes"] == 65536
    assert state.calls[0]["max_stderr_bytes"] == 8192


@pytest.mark.parametrize("kind", ["timeout", "output-limit"])
def test_unavailable_observation_preserves_data(
    api: tuple[ModuleType, Observation], kind: str,
) -> None:
    module, state = api
    state.result = subprocess.TimeoutExpired("git", 5) if kind == "timeout" else subprocess.SubprocessError("output limit")
    assert "cannot classify" in module.preservation_reason("checkout")
