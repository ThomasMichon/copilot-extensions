"""Startup bodies and periodically maintained, non-discovered fallbacks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_instruction_delivery import delivery, fixture, projections


@pytest.mark.parametrize(
    "name",
    [
        "rules.instructions.md.instructions.md",
        "rules.INSTRUCTIONS.MD.instructions.md",
        "rules.instructions.md.backup.instructions.md",
        "AGENTS.instructions.md",
        "claude.instructions.md",
        "GEMINI.instructions.md",
        "copilot-instructions.instructions.md",
        "nested.instructions.md/rules.instructions.md",
        "nested.INSTRUCTIONS.MD/rules.instructions.md",
    ],
)
def test_fallback_names_never_match_native_instruction_discovery(name: str) -> None:
    with pytest.raises(ValueError, match="automatic instruction discovery"):
        delivery.fallback_destination(f".github/instructions/policy/{name}")


def test_nested_fallback_path_remains_non_discovered() -> None:
    assert delivery.fallback_destination(
        ".github/instructions/policy/nested/rules.instructions.md"
    ) == ".github/copilot/context-fallbacks/policy/nested/rules.md"


@pytest.mark.parametrize("mode", [None, "inline", "selector"])
@pytest.mark.parametrize("skip_local_cache", [False, True])
def test_periodic_sync_keeps_every_full_body_out_of_automatic_discovery(
    tmp_path: Path, mode: str | None, skip_local_cache: bool,
) -> None:
    repo, source, spec = fixture(tmp_path)
    declaration = source.payload_root / "instruction-projections.json"
    data = json.loads(declaration.read_bytes())
    entry = data["projections"][0]
    if mode is None:
        entry.pop("deliveryMode")
    else:
        entry["deliveryMode"] = mode
    entry["skipLocalCache"] = skip_local_cache
    declaration.write_text(json.dumps(data))

    synced = projections.sync_repository(repo, [source])
    assert not synced.blocking, synced.findings
    destination = entry["destination"]
    selector = (repo / destination).read_bytes()
    fallback_path = repo / delivery.fallback_destination(destination)
    fallback = fallback_path.read_bytes()
    assert delivery.parse_marker(selector)["deliveryKind"] == "selector"
    assert b"Never assume authorization." not in selector
    assert b"Never assume authorization." in fallback
    assert ".instructions.md" not in fallback_path.name.casefold()

    refreshed = projections.render_local_cache(repo, lambda: [source])
    assert not refreshed.blocking, refreshed.findings
    current_path = repo / delivery.managed_destination(destination, spec.plugin)
    discovered = [
        path for path in repo.rglob("*.instructions.md")
        if b"Never assume authorization." in path.read_bytes()
    ]
    assert discovered == [current_path]
    selected = projections.resolve_instruction_source(repo, destination, [source])
    assert selected["selectedPath"] == current_path.relative_to(repo).as_posix()

    (source.payload_root / entry["template"]).write_text(
        '---\napplyTo: "**"\n---\n\nUse the updated policy.\n'
    )
    periodic = projections.sync_repository(repo, [source])
    assert not periodic.blocking, periodic.findings
    assert b"Use the updated policy." in fallback_path.read_bytes()
    assert b"Use the updated policy." not in (repo / destination).read_bytes()
    assert b"Never assume authorization." in current_path.read_bytes()
