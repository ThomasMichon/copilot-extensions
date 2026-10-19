"""HTTP source registration never silently selects an ambiguous provider."""

import pytest

from credential_relay import RelayBuilder
from credential_relay.http_sources import HttpSourceRegistry


def test_registry_keeps_provider_identity_and_scope_capability():
    registry = HttpSourceRegistry()
    source = object()
    registry.register("example@example/grant", source, scoped=True)
    assert registry.get("example@example/grant").source is source
    assert registry.get("example@example/grant").scoped
    with pytest.raises(PermissionError):
        registry.get("missing@example/grant")


def test_duplicate_reference_refuses_without_disabling_other_sources():
    builder = RelayBuilder()
    builder.add_http_source("example@example/grant", object())
    builder.add_http_source("example@example/grant", object())
    builder.add_http_source("other@example/grant", object())
    with pytest.raises(PermissionError, match="ambiguous"):
        builder.http_sources.get("example@example/grant")
    assert builder.http_sources.get("other@example/grant")
    merged = HttpSourceRegistry()
    merged.register("example@example/grant", object())
    merged.include(builder.http_sources)
    with pytest.raises(PermissionError, match="ambiguous"):
        merged.get("example@example/grant")
    assert merged.get("other@example/grant")


@pytest.mark.parametrize("reference", [
    "unqualified", "", "provider/grant", "../source", "name@example/source/path",
    "name@example/a\nb", "name@example/", "name @example/grant",
])
def test_source_reference_must_be_provider_qualified(reference):
    with pytest.raises(ValueError):
        HttpSourceRegistry().register(reference, object())
