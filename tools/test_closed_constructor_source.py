"""Callback Source preparation retains original metadata without duplicate copies."""
from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any

import pytest

from test_installer_closed_wheels import (
    candidate, engine as engine, inspected, plugin as plugin, snapshot,
)

if TYPE_CHECKING:
    from installer_engine import FrozenInputs


@pytest.mark.parametrize("closed_source", [False, True])
@pytest.mark.parametrize("mutation", [False, True])
def test_callback_uses_one_owned_original_source_for_closed_wheels(
    tmp_path: Path, engine: ModuleType, plugin: Path,
    monkeypatch: pytest.MonkeyPatch, closed_source: bool, mutation: bool,
) -> None:
    original_metadata = (plugin / "pyproject.toml").read_bytes()
    application = tmp_path / "application"
    application.mkdir()
    copies: list[Path] = []
    observed: list[Path] = []
    original_freeze = engine.freeze_inputs

    def freeze(*args: Any, **kwargs: Any) -> Any:
        frozen = original_freeze(*args, **kwargs)
        copies.append(frozen.plugin)
        return frozen

    class ReachedPreparation(Exception):
        pass

    def construct(inputs: FrozenInputs, slot: Path, *args: Any, **kwargs: Any) -> Path:
        assert inputs.plugin != plugin
        assert (inputs.plugin / "pyproject.toml").read_bytes() == original_metadata
        assert slot.is_dir()
        return Path(sys.executable)

    def prepare(
        inputs: FrozenInputs, slot: Path, python: Path, callback: str,
        data: dict[str, Any], descriptor: int | None, timeout: float,
        payload: FrozenInputs,
    ) -> None:
        assert payload.plugin != plugin
        assert (payload.plugin / "pyproject.toml").read_bytes() == original_metadata
        assert (payload.plugin == inputs.plugin) is closed_source
        observed.append(payload.plugin)
        if mutation:
            path = payload.plugin / "pyproject.toml"
            path.write_bytes(original_metadata + b"\n# changed during callback\n")
        else:
            raise ReachedPreparation

    monkeypatch.setattr(engine, "freeze_inputs", freeze)
    monkeypatch.setattr(engine, "_construct", construct)
    monkeypatch.setattr(engine, "_prepare_installed", prepare)
    root = tmp_path / "runtime"
    with snapshot(tmp_path) as retained:
        with pytest.raises(
            RuntimeError if mutation else ReachedPreparation,
            match="changed" if mutation else None,
        ):
            with candidate(
                engine, root, plugin, inspected(retained),
                application=application, closed_wheel_source=closed_source,
                construction_callable="demo_pkg:prepare_source",
            ):
                pytest.fail("changed or interrupted preparation must not complete")
    assert len(copies) == (1 if closed_source else 2)
    assert observed == [copies[-1] if closed_source else copies[0]]
    assert (plugin / "pyproject.toml").read_bytes() == original_metadata
    assert not engine.runtime.marker_path(root, "1.0.0").exists()
