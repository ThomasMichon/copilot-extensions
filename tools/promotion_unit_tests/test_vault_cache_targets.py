"""Actor-free cache policy controls; native cache/service qualification is separate."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "promotion_vault_cache_policy"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT / "plugins/agent-vault/src/agent_vault")]
sys.modules[PACKAGE] = package
targets = importlib.import_module(f"{PACKAGE}.cache_targets")
sources = importlib.import_module(f"{PACKAGE}.cache_sources")


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


def test_vault_invalid_manifest_aborts_collection_without_substitute_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    def broken(_path: Path) -> Iterator[tuple[str, str]]:
        yield "/first", "password"
        raise OSError("private diagnostic must not become a target or log")

    def fallback(_machine: str | None) -> list[str]:
        calls.append(True)
        return ["substitute"]

    monkeypatch.setattr(targets, "iter_cache_manifest", broken)
    policy = {"cache_entries": [], "cache_manifests": ["logical.conf"]}
    with pytest.raises(targets.CacheTargetError, match="configured cache manifest could not be read"):
        sources.collect([lambda _machine: targets._declared(policy), fallback], None)
    assert calls == []
