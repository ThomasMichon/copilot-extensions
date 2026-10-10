"""Tests for the ``workers config-section`` CLI glue (Phase 1 of
``agent-dispatch-workers-config-section``): the status line + local
enable/disable toggle a Worktree Manager Picker ``config_sections`` entry
invokes.
"""

from __future__ import annotations

import json

import pytest

from agent_dispatch.__main__ import build_parser
from agent_dispatch.registrar import ProfileDeclaration
from agent_dispatch.registrations import RegistrationKind


def _args(argv):
    return build_parser().parse_args(argv)


@pytest.fixture(autouse=True)
def _isolate_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_DISPATCH_OVERRIDES", str(tmp_path / "overrides.json"))


def _declare(name: str, *, owner: str = "repo:example", concurrency: int = 2) -> ProfileDeclaration:
    return ProfileDeclaration(
        name=name,
        kind=RegistrationKind.SUPERVISED_LANE,
        concurrency=concurrency,
        owner=owner,
    )


def _patch_discover(monkeypatch, decls):
    monkeypatch.setattr("agent_dispatch.registrar_discovery.discover", lambda: decls)


# -- parser shape -------------------------------------------------------------


def test_parser_shapes_namespace():
    args = _args(["workers", "config-section", "my-pool", "--toggle", "disable", "--reason", "noisy"])
    assert args.workers_command == "config-section"
    assert args.name == "my-pool"
    assert args.toggle == "disable"
    assert args.reason == "noisy"


# -- status line ---------------------------------------------------------------


def test_status_line_active_pool(monkeypatch, capsys):
    _patch_discover(monkeypatch, [_declare("my-pool", concurrency=3)])
    args = _args(["workers", "config-section", "my-pool"])
    assert args.func(args) == 0
    out = capsys.readouterr().out.strip()
    assert out == "my-pool: 3 lanes declared -- active"
    assert len(out) <= 200


def test_status_line_singular_lane(monkeypatch, capsys):
    _patch_discover(monkeypatch, [_declare("solo-pool", concurrency=1)])
    args = _args(["workers", "config-section", "solo-pool"])
    assert args.func(args) == 0
    assert capsys.readouterr().out.strip() == "solo-pool: 1 lane declared -- active"


def test_status_line_not_found(monkeypatch, capsys):
    _patch_discover(monkeypatch, [])
    args = _args(["workers", "config-section", "ghost-pool"])
    # not-found is reported, not crashed on -- but the exit code signals it
    assert args.func(args) == 1
    assert capsys.readouterr().out.strip() == "ghost-pool: no declaration found"


def test_status_line_ignores_non_supervised_lane_declarations(monkeypatch, capsys):
    other = ProfileDeclaration(name="my-pool", kind="emitter", owner="repo:example")
    _patch_discover(monkeypatch, [other])
    args = _args(["workers", "config-section", "my-pool"])
    assert args.func(args) == 1
    assert capsys.readouterr().out.strip() == "my-pool: no declaration found"


# -- toggle + status round-trip -------------------------------------------------


def test_toggle_disable_then_status_reflects_override(monkeypatch, capsys):
    _patch_discover(monkeypatch, [_declare("my-pool", owner="repo:example", concurrency=2)])

    args = _args(["workers", "config-section", "my-pool", "--toggle", "disable", "--reason", "flaky"])
    assert args.func(args) == 0
    out = capsys.readouterr().out.strip()
    assert out == "my-pool: 2 lanes declared -- overridden off (flaky)"

    # a later plain status call (no --toggle) reflects the persisted override
    args = _args(["workers", "config-section", "my-pool"])
    assert args.func(args) == 0
    assert capsys.readouterr().out.strip() == "my-pool: 2 lanes declared -- overridden off (flaky)"


def test_toggle_enable_clears_override(monkeypatch, capsys):
    _patch_discover(monkeypatch, [_declare("my-pool", owner="repo:example")])

    args = _args(["workers", "config-section", "my-pool", "--toggle", "disable"])
    args.func(args)
    capsys.readouterr()

    args = _args(["workers", "config-section", "my-pool", "--toggle", "enable"])
    assert args.func(args) == 0
    assert capsys.readouterr().out.strip() == "my-pool: 2 lanes declared -- active"


def test_toggle_on_unknown_pool_still_applies_and_reports_not_found(monkeypatch, capsys):
    """A toggle targets the pool's *logical* override id, independent of a live
    declaration -- so a not-yet-synced pool can still be pre-disabled, and the
    override takes effect the instant its declaration appears."""
    _patch_discover(monkeypatch, [])
    args = _args(["workers", "config-section", "ghost-pool", "--toggle", "disable", "--owner", "repo:example"])
    assert args.func(args) == 1  # still reports "not found" for the declaration itself
    assert capsys.readouterr().out.strip() == "ghost-pool: no declaration found"

    from agent_dispatch import overrides as ov
    from agent_dispatch.config import overrides_path

    assert ov.overridden_off_ids(ov.load_overrides(overrides_path())) == {
        "logical:repo:example:ghost-pool"
    }


# -- JSON output -----------------------------------------------------------------


def test_json_output_shape(monkeypatch, capsys):
    _patch_discover(monkeypatch, [_declare("my-pool", owner="repo:example", concurrency=4)])
    args = _args(["workers", "config-section", "my-pool", "--json"])
    assert args.func(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "name": "my-pool",
        "owner": "repo:example",
        "found": True,
        "concurrency": 4,
        "overridden_off": False,
        "override_reason": None,
        "override_id": "logical:repo:example:my-pool",
    }


def test_json_output_not_found(monkeypatch, capsys):
    _patch_discover(monkeypatch, [])
    args = _args(["workers", "config-section", "ghost-pool", "--json"])
    assert args.func(args) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["found"] is False
    assert out["concurrency"] is None
    assert out["owner"] == "local"


# -- owner disambiguation ----------------------------------------------------


def test_owner_filters_when_given(monkeypatch, capsys):
    _patch_discover(
        monkeypatch,
        [
            _declare("my-pool", owner="repo:a", concurrency=1),
            _declare("my-pool", owner="repo:b", concurrency=5),
        ],
    )
    args = _args(["workers", "config-section", "my-pool", "--owner", "repo:b"])
    assert args.func(args) == 0
    assert capsys.readouterr().out.strip() == "my-pool: 5 lanes declared -- active"


# -- status-line length budget -----------------------------------------------


def test_status_line_truncates_to_budget(monkeypatch, capsys):
    from agent_dispatch.workers_config_cli import STATUS_LINE_MAX_CHARS, pool_status_line

    long_reason = "x" * 500
    decl = _declare("my-pool", concurrency=1)
    line = pool_status_line(
        "my-pool", declaration=decl, overridden=True, override_reason=long_reason
    )
    assert len(line) == STATUS_LINE_MAX_CHARS
    assert line.endswith("\u2026")


# -- no-name summary mode (Phase 3: the config_sections manifest entry's own
# default invocation, since a static manifest `run` argv cannot know a
# particular consuming repo's own pool name ahead of time) -------------------


def test_parser_name_is_optional():
    args = _args(["workers", "config-section"])
    assert args.name is None


def test_no_name_summarizes_every_declared_pool(monkeypatch, capsys):
    _patch_discover(
        monkeypatch,
        [
            _declare("pool-a", owner="repo:a", concurrency=1),
            _declare("pool-b", owner="repo:b", concurrency=3),
        ],
    )
    args = _args(["workers", "config-section"])
    assert args.func(args) == 0
    out = capsys.readouterr().out.strip()
    assert out == "pool-a: 1 -- active; pool-b: 3 -- active"


def test_no_name_summary_reflects_overrides(monkeypatch, capsys):
    _patch_discover(monkeypatch, [_declare("pool-a", owner="repo:a", concurrency=1)])
    args = _args(["workers", "config-section", "pool-a", "--toggle", "disable", "--reason", "noisy"])
    args.func(args)
    capsys.readouterr()

    args = _args(["workers", "config-section"])
    assert args.func(args) == 0
    assert capsys.readouterr().out.strip() == "pool-a: 1 -- overridden off (noisy)"


def test_no_name_no_pools_declared(monkeypatch, capsys):
    _patch_discover(monkeypatch, [])
    args = _args(["workers", "config-section"])
    assert args.func(args) == 0
    assert capsys.readouterr().out.strip() == "no worker pools declared"


def test_no_name_ignores_non_supervised_lane_declarations(monkeypatch, capsys):
    other = ProfileDeclaration(name="x", kind="emitter", owner="repo:a")
    _patch_discover(monkeypatch, [other])
    args = _args(["workers", "config-section"])
    assert args.func(args) == 0
    assert capsys.readouterr().out.strip() == "no worker pools declared"


def test_no_name_with_toggle_rejected(monkeypatch, capsys):
    _patch_discover(monkeypatch, [])
    args = _args(["workers", "config-section", "--toggle", "disable"])
    assert args.func(args) == 2
    assert "requires a pool name" in capsys.readouterr().err


def test_no_name_json_output_shape(monkeypatch, capsys):
    _patch_discover(
        monkeypatch,
        [
            _declare("pool-a", owner="repo:a", concurrency=1),
            _declare("pool-b", owner="repo:b", concurrency=3),
        ],
    )
    args = _args(["workers", "config-section", "--json"])
    assert args.func(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "pools": [
            {
                "name": "pool-a",
                "owner": "repo:a",
                "concurrency": 1,
                "overridden_off": False,
                "override_reason": None,
            },
            {
                "name": "pool-b",
                "owner": "repo:b",
                "concurrency": 3,
                "overridden_off": False,
                "override_reason": None,
            },
        ],
    }


def test_all_pools_status_line_collapses_overflow_into_more_marker():
    from agent_dispatch.workers_config_cli import STATUS_LINE_MAX_CHARS, all_pools_status_line

    # Enough pools with long-ish names to force overflow past the 200-char budget.
    decls = [_declare(f"pool-with-a-fairly-long-name-{i:03d}", concurrency=1) for i in range(30)]
    line = all_pools_status_line(decls, overrides={})
    assert len(line) <= STATUS_LINE_MAX_CHARS
    assert "more" in line


def test_all_pools_status_line_empty():
    from agent_dispatch.workers_config_cli import all_pools_status_line

    assert all_pools_status_line([], overrides={}) == "no worker pools declared"
