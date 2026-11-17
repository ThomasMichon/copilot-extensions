"""Actor-free cache policy controls; native cache/service qualification is separate."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, nullcontext
from io import StringIO
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "promotion_vault_cache_policy"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT / "plugins/agent-vault/src/agent_vault")]
sys.modules[PACKAGE] = package
targets = importlib.import_module(f"{PACKAGE}.cache_targets")
sources = importlib.import_module(f"{PACKAGE}.cache_sources")
manifests = importlib.import_module(f"{PACKAGE}.cache_manifests")
limits = importlib.import_module(f"{PACKAGE}.cache_limits")
read_manifest = cast(Callable[..., Iterator[tuple[str, str]]], manifests.iter_cache_manifest)


class TextRows(Protocol):
    def readline(self, size: int = -1) -> str: ...


def memory_manifest(stream: TextRows) -> Path:
    class Manifest:
        def open(self, *, encoding: str) -> AbstractContextManager[TextRows]:
            assert encoding == "utf-8"
            return nullcontext(stream)

    return cast(Path, Manifest())


def test_vault_cache_policy_refuses_malformed_allowlist_references() -> None:
    for data in (
        {"cache_entries": None},
        {"cache_entries": "entry"},
        {"cache_entries": [["entry", None]]},
        {"cache_entries": ["entry\nanother"]},
        {"cache_manifests": [False]},
    ):
        with pytest.raises(targets.CacheTargetError):
            targets.validate_configuration(data, "fixture")


def test_vault_cache_policy_preserves_anchors_and_manifest_owners() -> None:
    owner = Path(ROOT.anchor) / "declaring"
    absolute = owner / "native.conf"
    values = targets.preferences({
        "cache_entries": ["/Account", ["Account", "UserName"]],
        "cache_manifests": [str(Path("config") / "cache.conf"), str(absolute)],
    }, owner)
    assert values == {
        "cache_entries": ["/Account", ["Account", "UserName"]],
        "cache_manifests": [str(owner / "config" / "cache.conf"), str(absolute)],
    }
    assert list(targets._declared({"cache_entries": values["cache_entries"], "cache_manifests": []})) == [
        ("/Account", "password"), ("Account", "username"),
    ]


def test_vault_cache_policy_retains_and_validates_the_maintenance_owner() -> None:
    owner = Path(ROOT.anchor) / "declaring"
    assert targets.preferences({"cache_maintenance": "selected-service"}, owner) == {
        "cache_maintenance": "selected-service",
    }
    values: tuple[object, ...] = (True, None, [], "unknown")
    for value in values:
        with pytest.raises(RuntimeError, match="cache_maintenance"):
            targets.preferences({"cache_maintenance": value}, owner)


def test_vault_invalid_manifest_aborts_collection_without_substitute_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    path = memory_manifest(StringIO("/first | password\n | password\n"))

    def broken(_path: Path) -> Iterator[tuple[str, str]]:
        return read_manifest(path)

    def fallback(_machine: str | None) -> list[str]:
        calls.append(True)
        return ["substitute"]

    monkeypatch.setattr(targets, "iter_cache_manifest", broken)
    policy = {"cache_entries": [], "cache_manifests": ["logical.conf"]}
    with pytest.raises(targets.CacheTargetError, match="configured cache manifest could not be read"):
        sources.collect([lambda _machine: targets._declared(policy), fallback], None)
    assert calls == []


def test_vault_manifest_raw_lines_share_the_warming_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = StringIO("# one\n\n# two\n\nentry | password\n")
    path = memory_manifest(stream)

    def manifest(_path: Path, *, budget: object = None) -> Iterator[tuple[str, str]]:
        return read_manifest(path, budget=budget)

    monkeypatch.setattr(targets, "iter_cache_manifest", manifest)
    budget = limits.WorkBudget(4, 100.0, lambda: False, clock=lambda: 0.0)
    source = lambda _machine: targets._declared(
        {"cache_entries": [], "cache_manifests": ["logical.conf"]}, budget,
    )
    result = sources.collect([source], None, budget=budget, metered_sources=(source,))
    assert result.entries == [] and result.truncated
    assert stream.readline() == "entry | password\n"


@pytest.mark.parametrize("reason", ["deadline", "cancelled"])
def test_vault_manifest_scanning_observes_deadline_and_cancellation(reason: str) -> None:
    clock = [0.0]
    cancelled = [False]

    class Stream:
        def __init__(self) -> None:
            self.reads = 0
            self.content = StringIO("# first\n# second\nentry | password\n")

        def readline(self, size: int = -1) -> str:
            self.reads += 1
            if reason == "deadline":
                clock[0] += 1.0
            else:
                cancelled[0] = True
            return self.content.readline(size)

    stream = Stream()
    budget = limits.WorkBudget(1024, 2.0, lambda: cancelled[0], clock=lambda: clock[0])
    assert list(read_manifest(memory_manifest(stream), budget=budget)) == []
    assert budget.stopped
    assert stream.reads == (2 if reason == "deadline" else 1)
