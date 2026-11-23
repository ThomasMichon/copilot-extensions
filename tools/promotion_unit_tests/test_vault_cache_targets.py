"""Actor-free cache policy controls; native cache/service qualification is separate."""

from __future__ import annotations

import ast
import importlib
import sys
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, nullcontext
from io import StringIO
from pathlib import Path
from threading import local
from types import ModuleType, SimpleNamespace
from typing import Any, Protocol, cast

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
warm_selection = importlib.import_module(f"{PACKAGE}.cache_warm_selection")
read_manifest = cast(Callable[..., Iterator[tuple[str, str]]], manifests.iter_cache_manifest)


def test_read_source_receipts_preserve_outcome_and_never_report_resident_progress() -> None:
    root = str(Path(ROOT.anchor) / "selected" / "repository")
    context = local()
    context.kpdb, context.group = "fixture-database", "Example"
    selected = warm_selection.WarmSelection(root, False)
    calls: list[object] = []

    def status(_kpdb: str, _group: str, *, selection: object) -> dict[str, object]:
        calls.append(selection)
        assert selection == selected
        return {"state": "queued"}

    service = SimpleNamespace(_request_ctx=context, _cache_warm=SimpleNamespace(status=status))
    actions = warm_selection.capabilities()["cache_warm_selection_actions"]
    for action in actions:
        request = {"action": action, warm_selection.FIELD: selected.describe()}
        warm_selection.bind(context, request)
        response = {
            "ok": True, "value": "fixture-value", "generation": 7,
            "message": "fixture-created", "dest": "Example/target",
        }
        result = warm_selection.request_result(service, request, response)
        assert {key: result[key] for key in response} == response
        assert result["cache_warm"] == {"state": "queued", "request_selection_version": 1}
        assert "cache_warm" not in response
        failure = warm_selection.request_result(
            service, request, {"ok": False, "error": "locked", "needs_unlock": True},
        )
        assert failure["ok"] is False and failure["needs_unlock"] is True
        assert failure["error"] == "locked"
        invalid = {**request, warm_selection.FIELD: {}}
        warm_selection.bind(context, invalid)
        refusal = warm_selection.request_result(service, invalid, response)
        assert refusal["ok"] is True
        assert refusal["cache_warm"]["state"] == "not-queued"
        assert refusal["cache_warm"]["reason"] == "configuration-unavailable"
        legacy = {"action": action}
        warm_selection.bind(context, legacy)
        assert warm_selection.request_result(service, legacy, response) is response
    assert calls == [selected] * (2 * len(actions))


def test_password_receipts_preserve_internal_custody_and_transaction_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ROOT / "plugins/agent-vault/src/agent_vault"
    service_tree = ast.parse((source / "service.py").read_bytes())
    constant = next(
        node.value for node in service_tree.body
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_DISPATCH_WITHOUT_CREDENTIAL_LOCK"
            for target in node.targets
        )
    )
    assert isinstance(constant, ast.Call)
    service_module = ModuleType(PACKAGE + ".service")
    service_module.__dict__["_DISPATCH_WITHOUT_CREDENTIAL_LOCK"] = frozenset(
        ast.literal_eval(constant.args[0]),
    )
    monkeypatch.setitem(sys.modules, service_module.__name__, service_module)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("password transaction acquired outer credential/cache custody")

    dispatch_source = source / "service_dispatch.py"
    tree = ast.parse(dispatch_source.read_bytes())
    namespace: dict[str, Any] = {
        "__name__": PACKAGE + ".service_dispatch", "__package__": PACKAGE,
        "contextlib": SimpleNamespace(ExitStack=forbidden),
        "cache_warm_selection": warm_selection,
        "service_cache": SimpleNamespace(
            custody=forbidden, ServiceCacheUnavailable=RuntimeError, UNAVAILABLE="fixture-unavailable",
        ),
    }
    body: list[ast.stmt] = [
        node for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
        or isinstance(node, ast.FunctionDef) and node.name == "handle_admitted"
    ]
    assert len(body) == 2
    exec(compile(ast.Module(body=body, type_ignores=[]), str(dispatch_source), "exec"), namespace)
    context = local()
    context.kpdb, context.group = "fixture-database", "Example"
    selected = warm_selection.WarmSelection(str(Path(ROOT.anchor) / "selected"), False)
    statuses: list[object] = []
    dispatches: list[object] = []

    def status(_kpdb: str, _group: str, *, selection: object) -> dict[str, object]:
        statuses.append(selection)
        assert selection == selected
        return {"state": "queued"}

    responses: tuple[dict[str, Any], ...] = (
        {"ok": True, "message": "fixture-updated", "generation": 7},
        {"ok": False, "committed": True, "cache_updated": False, "generation": 8,
         "error": "fixture-cache-failure"},
        {"ok": False, "cache_pending": True, "error": "fixture-unconfirmed"},
    )
    offers: tuple[object, ...] = (selected.describe(), {}, None)
    for response in responses:
        def dispatch(request: dict[str, Any], peer: str, transport: str) -> dict[str, Any]:
            dispatches.append((request, peer, transport))
            return response

        service = SimpleNamespace(
            _request_ctx=context, _credential_lock=None, _check_password_ttl=forbidden,
            _cache_warm=SimpleNamespace(foreground=nullcontext, status=status),
            _dispatch_request=dispatch,
        )
        for offered in offers:
            request: dict[str, Any] = {"action": "set-password"}
            if offered is not None:
                request[warm_selection.FIELD] = offered
            warm_selection.bind(context, request)
            result = namespace["handle_admitted"](
                service, request, "peer", "fixture", forbidden, forbidden,
            )
            assert {key: result[key] for key in response} == response
            if offered is None:
                assert result is response
            else:
                receipt = result["cache_warm"]
                assert receipt["request_selection_version"] == 1
                assert receipt["state"] == ("queued" if offered else "not-queued")
    assert statuses == [selected] * len(responses)
    assert len(dispatches) == 3 * len(responses)


def test_vault_warming_selection_preserves_native_source_provenance_without_io() -> None:
    root = str(Path(ROOT.anchor) / "selected" / "repository")
    for native_default in (False, True):
        selected = warm_selection.decode({
            "contract": warm_selection.CONTRACT, "version": 1,
            "root": root, "native_default": native_default,
        })
        assert selected.root == root and selected.native_default is native_default


def test_vault_warming_selection_refuses_malformed_or_unsupported_requests() -> None:
    valid = {
        "contract": warm_selection.CONTRACT, "version": 1,
        "root": str(Path(ROOT.anchor) / "selected"), "native_default": False,
    }
    invalid: tuple[object, ...] = (None, [], {}, *(
        {**valid, **changed} for changed in (
            {"contract": "unknown"}, {"version": True}, {"version": 2},
            {"root": ""}, {"root": "relative"}, {"root": False},
            {"root": "x" * 4097}, {"root": valid["root"] + "\n"},
            {"native_default": 1}, {"unrecognized": "value"},
        )
    ))
    for value in invalid:
        with pytest.raises(ValueError, match="invalid warming selection"):
            warm_selection.decode(value)


def test_vault_warming_selection_refuses_other_operating_system_path_syntax() -> None:
    foreign = "/guest/repository" if sys.platform == "win32" else r"C:\host\repository"
    with pytest.raises(ValueError, match="invalid warming selection"):
        warm_selection.decode({
            "contract": warm_selection.CONTRACT, "version": 1,
            "root": foreign, "native_default": False,
        })


def test_vault_warming_selection_does_not_leak_into_a_later_legacy_request() -> None:
    context = local()
    valid = {
        "contract": warm_selection.CONTRACT, "version": 1,
        "root": str(Path(ROOT.anchor) / "selected"), "native_default": True,
    }
    warm_selection.bind(context, {warm_selection.FIELD: valid})
    assert context.warm_selection.native_default is True
    warm_selection.bind(context, {warm_selection.FIELD: None})
    assert context.invalid_warm_selection is True and context.warm_selection is None
    warmed = warm_selection.WarmState("not-queued", "configuration-unavailable")
    explicit = warm_selection.unlocked({warm_selection.FIELD: None}, warmed)
    assert explicit["ok"] is True and explicit["cache_warm"]["state"] == "not-queued"
    warm_selection.bind(context, {"action": "unlock"})
    assert context.invalid_warm_selection is False and context.warm_selection is None
    assert warm_selection.unlocked({}, warmed) == {"ok": True}


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
