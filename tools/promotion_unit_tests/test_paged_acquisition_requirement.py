"""Actor-free page requirement contracts; native indexing stays in the plugin lane."""

from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "plugins/agent-index/src/agent_index/indexing/acquisition_requirement.py"
)


@pytest.fixture
def api() -> ModuleType:
    module = ModuleType("promotion_paged_acquisition_requirement")
    exec(compile(SOURCE.read_bytes(), str(SOURCE), "exec"), module.__dict__)
    return module


def test_selected_callable_and_connector_require_explicit_v1_without_legacy_effects(api: ModuleType) -> None:
    calls: list[dict[str, object]] = []

    def selected(**kwargs: object) -> None:
        calls.append(kwargs)

    for versions in (None, (), (True,), (1.0,), (1, "2"), "1", (2,)):
        setattr(selected, "paged_acquisition_versions", versions)
        with pytest.raises(ValueError, match="Selected indexing callable"):
            api.require_execution_v1(selected)
        assert not calls
    setattr(selected, "paged_acquisition_versions", (1,))
    api.require_execution_v1(selected)
    selected(require_paged_acquisition=True)
    assert calls == [{"require_paged_acquisition": True}]
    with pytest.raises(ValueError):
        api.require_execution_v1(lambda **kwargs: selected(**kwargs))

    connector = SimpleNamespace(discover_delta_page=selected, discover_snapshot_page=selected)
    for full in (False, True):
        attribute = "snapshot_page_versions" if full else "acquisition_page_versions"
        for versions in (None, (), (True,), (1.0,), (1, "2"), "1", (2,)):
            setattr(selected, attribute, versions)
            with pytest.raises(ValueError, match="selected callable"):
                api.require_connector_pages(
                    connector, source="alias", full=full, change_hints=None, required=True,
                )
        setattr(selected, attribute, (1,))
        api.require_connector_pages(
            connector, source="alias", full=full, change_hints=None, required=True,
        )
    api.require_connector_pages(
        object(), source="alias", full=False, change_hints=None, required=False,
    )
    for malformed in (None, 1, "false"):
        with pytest.raises(ValueError, match="must be a bool"):
            api.require_connector_pages(
                connector, source="alias", full=False, change_hints=None, required=malformed,
            )
    assert len(calls) == 1


def test_retained_provenance_never_promotes_unknown_or_different_mode_work(api: ModuleType) -> None:
    claim = {"source": "alias", "item_ids": ["one"]}
    hints = SimpleNamespace(to_dict=lambda: claim)
    for mode in ("incremental", "full", "hinted"):
        full = mode == "full"
        current_hints = hints if mode == "hinted" else None
        qualified = (
            {"version": 6, "hints": claim} if mode == "hinted"
            else {"version": 7, "page_protocol": mode}
        )
        api.require_pending_pages(
            qualified, full=full, change_hints=current_hints,
        )
        for unknown in (
            None, {"version": 1}, {"version": 3},
            {"version": 7, "page_protocol": "unknown"},
            {"version": 6, "hints": {"item_ids": ["other"]}},
        ):
            with pytest.raises(ValueError, match="preserve it"):
                if unknown is None:
                    unknown = {}
                api.require_pending_pages(unknown, full=full, change_hints=current_hints)
    # Already-decoded document bodies remain opaque to policy, regardless of size.
    api.require_pending_pages(
        {"version": 7, "page_protocol": "incremental", "manifest": object()},
        full=False, change_hints=None,
    )
