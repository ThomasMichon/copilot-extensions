"""Tests for contributed pivot process-boundary loading."""

from __future__ import annotations

import sys

import pytest

from worktree_manager.pivot_runtime import (
    PivotLoadError,
    format_argv,
    load_pivot,
    parse_list_payload,
)
from worktree_manager.plugin_contracts import parse_manifest


def _pivot(list_cmd: list[str]):
    contribution = parse_manifest(
        {
            "schema_version": 1,
            "label": "Tasks",
            "list": list_cmd,
            "entry": {"id": "id", "title": "title"},
        },
        name="tasks",
        marketplace="example",
        plugin="agent-example",
        source_path="/payload/tasks.json",
    )
    assert contribution.pivot is not None
    return contribution.pivot


def test_format_argv_substitutes_context_and_empties_unknown_tokens():
    assert format_argv(
        ["agent-example", "--machine", "{machine}", "{unknown}"],
        {"machine": "host"},
    ) == ["agent-example", "--machine", "host", ""]


def test_parse_list_payload_accepts_array_and_summary_object():
    assert len(parse_list_payload([{"id": "1"}, "ignored"]).rows) == 1
    payload = parse_list_payload({
        "entries": [{"id": "2"}],
        "summary": {"ready": 1},
    })
    assert payload.rows[0]["id"] == "2"
    assert payload.summary == {"ready": 1}


def test_parse_list_payload_accepts_declared_items_field():
    payload = parse_list_payload(
        {"worktrees": [{"id": "1"}]},
        items_field="worktrees",
    )
    assert payload.rows == ({"id": "1"},)


def test_load_pivot_runs_command_and_parses_json():
    script = (
        "import json,sys;"
        "print(json.dumps({'entries':[{'id':'1','machine':sys.argv[1]}],"
        "'summary':{'ready':1}}))"
    )
    payload = load_pivot(
        _pivot([sys.executable, "-c", script, "{machine}"]),
        {"machine": "host"},
    )
    assert payload.rows[0]["machine"] == "host"
    assert payload.summary == {"ready": 1}


def test_load_pivot_surfaces_invalid_json():
    with pytest.raises(PivotLoadError, match="did not print JSON"):
        load_pivot(_pivot([sys.executable, "-c", "print('nope')"]), {})


@pytest.mark.parametrize("output", ["", " \r\n", "not JSON"])
def test_load_pivot_never_substitutes_empty_json_for_invalid_output(output):
    script = (
        f"import sys; sys.stdout.write({output!r}); "
        "sys.stderr.write('provider: unsupported category window\\n')"
    )
    with pytest.raises(
        PivotLoadError,
        match="did not print JSON: provider: unsupported category window",
    ):
        load_pivot(_pivot([sys.executable, "-c", script]), {})


def test_load_pivot_accepts_an_explicit_empty_array():
    payload = load_pivot(_pivot([sys.executable, "-c", "print('[]')"]), {})
    assert payload.rows == () and payload.summary == {}


def test_load_pivot_bounds_invalid_json_diagnostic():
    script = "import sys; sys.stderr.write('earlier line\\n' + 'x' * 300)"
    with pytest.raises(PivotLoadError) as caught:
        load_pivot(_pivot([sys.executable, "-c", script]), {})
    assert str(caught.value) == "list command did not print JSON: " + "x" * 200


def test_load_pivot_rejects_oversized_output():
    with pytest.raises(PivotLoadError, match="output exceeded"):
        load_pivot(
            _pivot([sys.executable, "-c", "print('x' * 100)"]),
            {},
            max_output_bytes=50,
        )
