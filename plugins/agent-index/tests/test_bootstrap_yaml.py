"""Base-interpreter configuration reads do not need ambient PyYAML."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "resolve_effective_config.py"
    spec = importlib.util.spec_from_file_location("bootstrap_yaml", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("style", "expected"), [
    (">-", "The owner's \"quoted\" description continues.\nSecond paragraph."),
    (">", "The owner's \"quoted\" description continues.\nSecond paragraph.\n"),
    ("|-", "The owner's \"quoted\" description\ncontinues.\n\nSecond paragraph."),
    ("|+", "The owner's \"quoted\" description\ncontinues.\n\nSecond paragraph.\n\n"),
])
def test_fallback_preserves_block_scalars_and_following_state_flags(
    style: str, expected: str,
) -> None:
    parsed = _module()._parse_simple_yaml(
        f"description: {style}\n"
        "  The owner's \"quoted\" description\n"
        "  continues.\n"
        "\n"
        "  Second paragraph.\n"
        "\n"
        "stateless: true\n"
        "requires_external_state_root: true\n",
    )
    assert parsed == {
        "description": expected, "stateless": True, "requires_external_state_root": True,
    }


def test_fallback_keeps_more_indented_block_content_literal() -> None:
    parsed = _module()._parse_simple_yaml(
        "settings:\n"
        "  description: >-\n"
        "    example:\n"
        "      - name: literal\n"
        "    end\n"
        "  enabled: true\n",
    )
    assert parsed == {
        "settings": {"description": "example:\n  - name: literal\nend", "enabled": True},
    }


def test_fallback_rejects_partially_dedented_block_content() -> None:
    module = _module()
    with pytest.raises(module.ConfigError, match="invalid block scalar indentation"):
        module._parse_simple_yaml("description: |\n    first\n  invalid\n")
