"""Pure predecessor-ownership controls for the native shortcut cache binding."""
from __future__ import annotations

from collections.abc import Callable
import importlib.util
from pathlib import Path
import sys
from typing import cast

import pytest

_path = Path(__file__).resolve().parents[1] / "test_installer_engine_commands.py"
_spec = importlib.util.spec_from_file_location("cache_owner_command_fixtures", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
_fixtures = cast(Callable[[str], list[tuple[str, str]]], _original.fixtures)
_owned = cast(Callable[[str, dict[str, str]], tuple[str, ...]], _original.owned)


@pytest.mark.parametrize("product", ("agent-vault", "agent-ssh", "agent-pull-requests"))
def test_pre_cache_owner_commands_remain_owned(product: str) -> None:
    contents = dict(_fixtures(product))
    lines = (
        'if [ "$_name" = agent-vault ]; then export AGENT_VAULT_HOME="$_root"; fi\n',
        f"if ('{product}' -ceq 'agent-vault') {{ $env:AGENT_VAULT_HOME = $_root }}\n",
    )
    for name in (product, product + ".ps1"):
        for line in lines:
            contents[name] = contents[name].replace(line, "")
    assert set(_owned(product, contents)) == set(contents)
    contents[product] += "\n# operator customization\n"
    contents[product + ".ps1"] += "\n# operator customization\n"
    assert product not in _owned(product, contents)
    assert product + ".ps1" not in _owned(product, contents)
    assert product + ".cmd" not in _owned(product, contents)
