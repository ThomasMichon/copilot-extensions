"""Finite declarations share held-command custody without assuming JSONL output."""

from __future__ import annotations

import json
import sys
from threading import Event, enumerate as threads

import pytest

from worktree_manager import agenda_transport as transport
from worktree_manager.agenda_contract import AgendaUnavailable

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]


def command(output: str, *, exit_code: int = 0) -> list[str]:
    return [
        sys.executable, "-I", "-c",
        f"import sys; sys.stdout.write({output!r}); sys.stdout.flush(); sys.exit({exit_code})",
    ]


def test_pretty_declaration_is_delivered_once_only_after_successful_completion(tmp_path) -> None:
    declaration = {"schema": "example.contracts", "version": 1, "contracts": {"sample": {"version": 1}}}
    frames = list(transport.command_frames(
        command(json.dumps(declaration, indent=2) + "\n"), cancelled=Event(),
        cwd=str(tmp_path), json_document=True,
    ))
    assert frames == [declaration]
    with pytest.raises(AgendaUnavailable, match="exited 3"):
        list(transport.command_frames(
            command(json.dumps(declaration) + "\n", exit_code=3), cancelled=Event(),
            cwd=str(tmp_path), json_document=True,
        ))
    assert not any(thread.name.startswith("agenda-feed-") for thread in threads())


@pytest.mark.parametrize("output", [
    '{"version": 1,\n"version": 2}\n',
    "[]\n",
    '{"incomplete": true\n',
    '{"nonfinite": NaN}\n',
    '{"nested": ' + "[" * 20 + "0" + "]" * 20 + "}\n",
    '{"nested": ' + "[" * 1100 + "0" + "]" * 1100 + "}\n",
    json.dumps({"large": "x" * (transport.MAX_FRAME_BYTES + 1)}, indent=2) + "\n",
    '{\n' + "".join(f'"field{index}": "{"x" * 1000}",\n' for index in range(40)) + '"end": true\n}\n',
])
def test_malformed_duplicate_and_total_document_overflow_refuse(tmp_path, output: str) -> None:
    with pytest.raises(ValueError):
        list(transport.command_frames(
            command(output), cancelled=Event(), cwd=str(tmp_path), json_document=True,
        ))
    assert not any(thread.name.startswith("agenda-feed-") for thread in threads())
