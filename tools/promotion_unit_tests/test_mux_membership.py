"""Actor-free membership composition; sockets, census and finite workers are boundaries."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace, TracebackType
from typing import TypeVar

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "libs/copilot-driver-core/src/agent_remote_driver"
_T = TypeVar("_T")
_U = TypeVar("_U")


class InlinePool:
    def __init__(self, *, max_workers: int) -> None:
        assert 1 <= max_workers <= 8

    def __enter__(self) -> InlinePool:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def map(self, function: Callable[[_T], _U], items: Iterable[_T]) -> Iterator[_U]:
        return map(function, items)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    name = "promotion_mux_membership"
    package = ModuleType(name)
    package.__path__ = []
    monkeypatch.setitem(sys.modules, name, package)
    monkeypatch.setitem(sys.modules, "agent_remote_driver", package)
    native = ModuleType(f"{name}.windows_mux_processes")
    native.__dict__["servers"] = lambda: {101: "100"}
    monkeypatch.setitem(sys.modules, native.__name__, native)
    setattr(package, "windows_mux_processes", native)

    def load(child: str) -> ModuleType:
        spec = importlib.util.spec_from_file_location(f"{name}.{child}", SOURCE / f"{child}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        monkeypatch.setitem(sys.modules, f"agent_remote_driver.{child}", module)
        setattr(package, child, module)
        spec.loader.exec_module(module)
        return module

    load("mux_observation_types")
    inventory = load("mux_inventory")
    observation = load("mux_observation")
    monkeypatch.setattr("concurrent.futures.ThreadPoolExecutor", InlinePool)
    monkeypatch.setattr(
        inventory, "_registrations",
        lambda *args: {"wt-one": (12345, "synthetic", (101, "100"))},
    )
    monkeypatch.setenv("PSMUX_DATA_DIR", str(ROOT / "synthetic-native"))
    monkeypatch.delenv("COPILOT_DRIVER_PSMUX_NAMESPACE", raising=False)
    return SimpleNamespace(inventory=inventory, observation=observation, native=native)


@pytest.mark.parametrize("count", [0, 1, 3])
def test_listing_counts_legacy_client_rows_and_never_queries_panes(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, count: int,
) -> None:
    queries: list[bytes] = []
    identity = f"wt-one:{int(count > 0)}:101:$7:100"
    clients = "\n".join("C\twt-one" for _ in range(count))

    def reply(port: int, key: str, deadline: float, query: bytes) -> str:
        assert port == 12345 and key == "synthetic"
        queries.append(query)
        return clients if query.startswith(b"list-clients ") else identity

    monkeypatch.setattr(api.inventory, "_reply", reply)
    monkeypatch.setattr(
        api.observation, "observe_psmux_many", lambda *args, **kwargs: pytest.fail("pane pass"),
    )
    result = api.observation.list_psmux()
    assert result is not None and set(result) == {"wt-one"}
    assert result["wt-one"].clients == count
    assert result["wt-one"].incarnation == "$7:100"
    assert len(queries) == 3 and queries[0] == queries[2]
    assert queries[1].startswith(b"list-clients ")
    assert not any(b"list-panes" in query for query in queries)


@pytest.mark.parametrize("failure", ["incarnation", "birth", "uncovered", "registration"])
def test_listing_refuses_identity_or_complete_coverage_changes(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    calls = [0]

    def reply(port: int, key: str, deadline: float, query: bytes) -> str:
        calls[0] += 1
        if failure == "birth" and calls[0] == 3:
            monkeypatch.setattr(api.native, "servers", lambda: {101: "200"})
        if failure == "uncovered" and calls[0] == 3:
            monkeypatch.setattr(api.native, "servers", lambda: {101: "100", 202: "100"})
        if failure == "registration" and calls[0] == 3:
            monkeypatch.setattr(
                api.inventory, "_registrations",
                lambda *args: {"wt-one": (54321, "synthetic", (101, "100"))},
            )
        if query.startswith(b"list-clients "):
            return "C\twt-one"
        return "wt-one:1:101:$8:200" if failure == "incarnation" and calls[0] == 3 else (
            "wt-one:1:101:$7:100"
        )

    monkeypatch.setattr(api.inventory, "_reply", reply)
    assert api.observation.list_psmux() is None


def test_listing_selects_qualified_names_without_reobserving_stale_hints(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COPILOT_DRIVER_PSMUX_NAMESPACE", "selected")
    monkeypatch.setattr(api.inventory, "observe_memberships", lambda **kwargs: {
        "selected__wt-one__literal": ("wt-one__literal", 2, "$7:100"),
        "foreign__wt-one__literal": ("wt-one__literal", 3, "$8:200"),
    })
    monkeypatch.setattr(
        api.observation, "observe_psmux_many", lambda *args, **kwargs: pytest.fail("pane pass"),
    )
    result = api.observation.list_psmux()
    assert result is not None and set(result) == {"wt-one__literal"}
    assert result["wt-one__literal"].clients == 2
    assert result["wt-one__literal"].incarnation == "$7:100"
