"""Tests for repository-declared task modes (ui_task_modes)."""

from __future__ import annotations

import pytest

from agent_bridge import ui_task_modes as modes


def _write(tmp_path, text):
    path = tmp_path / modes.MODES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    return tmp_path


def test_a_repo_without_the_file_offers_no_modes(tmp_path) -> None:
    assert modes.load_modes(tmp_path) == []
    assert modes.load_modes(None) == []
    assert modes.seed_for([], None, "Fix it") == "Fix it"
    with pytest.raises(KeyError):
        modes.seed_for([], "campaign", "x")


def test_modes_are_folded_validated_and_public_without_templates(tmp_path) -> None:
    root = _write(tmp_path, "modes:\n  - id: task\n    label: One task\n    prompt: |\n      Do this:\n      {prompt}\n")
    [mode] = modes.load_modes(root)
    assert mode["prompt"] == "Do this: {prompt}"  # the seed is typed, so it is one line
    assert modes.public([mode]) == [{"id": "task", "label": "One task", "description": ""}]
    assert modes.seed_for([mode], None, "go") == "Do this: go"
    assert modes.seed_for([{**mode, "prompt": "Prefix."}], "task", "go") == "Prefix. go"


@pytest.mark.parametrize("text", [
    "modes: nope",
    "- id: task",
    "modes:\n  - id: Bad Id\n    label: x\n    prompt: y\n",
    "modes:\n  - id: a\n    label: x\n",
    "modes:\n  - id: a\n    label: x\n    prompt: y\n  - id: a\n    label: z\n    prompt: w\n",
    "modes:\n  - id: a\n    label: " + "x" * 41 + "\n    prompt: y\n",
    "modes: [unclosed",
])
def test_an_invalid_declaration_offers_nothing(tmp_path, text) -> None:
    assert modes.load_modes(_write(tmp_path, text)) == []


def test_the_checkout_comes_from_an_existing_registry_path(tmp_path) -> None:
    assert modes.checkout_path({"paths": {"windows": str(tmp_path), "linux": str(tmp_path)}}) == tmp_path
    assert modes.checkout_path({"paths": {"windows": str(tmp_path / "missing")}}) is None
    assert modes.checkout_path({"paths": None}) is None


def test_the_file_is_reread_when_it_changes(tmp_path) -> None:
    import os
    root = _write(tmp_path, "modes:\n  - id: a\n    label: A\n    prompt: '{prompt}'\n")
    assert [m["id"] for m in modes.load_modes(root)] == ["a"]
    path = root / modes.MODES_FILE
    path.write_text("modes:\n  - id: b\n    label: B\n    prompt: '{prompt}'\n", "utf-8")
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 5))
    assert [m["id"] for m in modes.load_modes(root)] == ["b"]
