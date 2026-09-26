"""Focused tests for the shared two-phase lazy-dispatch mechanism, entirely
independent of any one adopting plugin (agent-cli-lazy-dispatch Phase 2's
own Validation Plan requirement)."""
from __future__ import annotations

import argparse
import sys
import types

import pytest

from lazy_cli_dispatch import core_helper, dispatch_lazy, self_override


def _make_fake_module(
    monkeypatch: pytest.MonkeyPatch, name: str, *, register_attr: str = "add_parsers"
):
    """Register a fake importable module ``<pkg>.<name>`` in ``sys.modules``
    exposing ``add_parsers``/``register_cli`` (per ``register_attr``) plus a
    ``cmd_<name>`` handler, and return (module, calls) so a test can assert
    on what the handler received."""
    calls: list[argparse.Namespace] = []

    def handler(args: argparse.Namespace) -> int:
        calls.append(args)
        return 7

    def register(sub) -> None:
        p = sub.add_parser(name, help=f"the {name} command")
        p.add_argument("--flag", action="store_true")

    module = types.SimpleNamespace(**{register_attr: register, f"cmd_{name}": handler})
    monkeypatch.setitem(sys.modules, f"pkg.{name}", module)
    return module, calls


class TestDispatchLazy:
    def test_dispatches_to_the_table_owned_module_and_handler(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _, calls = _make_fake_module(monkeypatch, "widget")
        rc = dispatch_lazy(
            "widget", ["widget", "--flag"],
            dispatch_table={"widget": ("widget", "cmd_widget")},
            package="pkg",
            prog="my-cli",
            ensure_cluster_loaded=lambda: pytest.fail("must not run for a cluster-free module"),
            cluster_free_modules=frozenset({"widget"}),
        )
        assert rc == 7
        assert len(calls) == 1
        assert calls[0].flag is True

    def test_loads_cluster_for_a_not_yet_cluster_free_module(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _make_fake_module(monkeypatch, "gizmo")
        loaded = []
        dispatch_lazy(
            "gizmo", ["gizmo"],
            dispatch_table={"gizmo": ("gizmo", "cmd_gizmo")},
            package="pkg",
            prog="my-cli",
            ensure_cluster_loaded=lambda: loaded.append(1),
            cluster_free_modules=frozenset(),  # not cluster-free
        )
        assert loaded == [1]

    def test_cluster_free_module_never_loads_cluster(self, monkeypatch: pytest.MonkeyPatch):
        _make_fake_module(monkeypatch, "sprocket")
        loaded = []
        dispatch_lazy(
            "sprocket", ["sprocket"],
            dispatch_table={"sprocket": ("sprocket", "cmd_sprocket")},
            package="pkg",
            prog="my-cli",
            ensure_cluster_loaded=lambda: loaded.append(1),
            cluster_free_modules=frozenset({"sprocket"}),
        )
        assert loaded == []

    def test_register_cli_modules_uses_register_cli_instead_of_add_parsers(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _, calls = _make_fake_module(monkeypatch, "pane", register_attr="register_cli")
        rc = dispatch_lazy(
            "pane", ["pane"],
            dispatch_table={"pane": ("pane", "cmd_pane")},
            package="pkg",
            prog="my-cli",
            ensure_cluster_loaded=lambda: None,
            cluster_free_modules=frozenset({"pane"}),
            register_cli_modules=frozenset({"pane"}),
        )
        assert rc == 7
        assert len(calls) == 1

    def test_command_map_override_wins_over_module_handler(self, monkeypatch: pytest.MonkeyPatch):
        _make_fake_module(monkeypatch, "widget")
        override_calls = []

        def override(args: argparse.Namespace) -> int:
            override_calls.append(args)
            return 99

        rc = dispatch_lazy(
            "widget", ["widget"],
            dispatch_table={"widget": ("widget", "cmd_widget")},
            package="pkg",
            prog="my-cli",
            ensure_cluster_loaded=lambda: None,
            cluster_free_modules=frozenset({"widget"}),
            command_map={"widget": override},
        )
        assert rc == 99
        assert len(override_calls) == 1

    def test_scoped_parser_prog_matches_caller(self, monkeypatch: pytest.MonkeyPatch, capsys):
        _make_fake_module(monkeypatch, "widget")
        with pytest.raises(SystemExit):
            dispatch_lazy(
                "widget", ["widget", "--help"],
                dispatch_table={"widget": ("widget", "cmd_widget")},
                package="pkg",
                prog="my-cli",
                ensure_cluster_loaded=lambda: None,
                cluster_free_modules=frozenset({"widget"}),
            )
        out = capsys.readouterr().out
        assert "my-cli" in out

    def test_unknown_command_raises_keyerror_not_silently_ignored(self):
        with pytest.raises(KeyError):
            dispatch_lazy(
                "nope", ["nope"],
                dispatch_table={},
                package="pkg",
                prog="my-cli",
                ensure_cluster_loaded=lambda: None,
            )


class TestSelfOverride:
    def test_prefers_a_monkeypatched_global_over_local(self):
        def real_impl():
            return "real"

        def fake_impl():
            return "fake"

        module_globals = {"some_name": fake_impl}
        resolved = self_override(module_globals, "some_name", real_impl)
        assert resolved is fake_impl

    def test_falls_back_to_local_when_absent(self):
        def real_impl():
            return "real"

        resolved = self_override({}, "some_name", real_impl)
        assert resolved is real_impl

    def test_falls_back_to_local_when_global_is_identical_to_local(self):
        def real_impl():
            return "real"

        module_globals = {"some_name": real_impl}
        resolved = self_override(module_globals, "some_name", real_impl)
        assert resolved is real_impl

    def test_ignores_a_non_callable_global(self):
        def real_impl():
            return "real"

        module_globals = {"some_name": "not-a-function"}
        resolved = self_override(module_globals, "some_name", real_impl)
        assert resolved is real_impl


class TestCoreHelper:
    def test_prefers_a_bound_attribute_on_core_module_over_local(self):
        def real_impl():
            return "real"

        def fake_impl():
            return "fake"

        core = types.SimpleNamespace(some_name=fake_impl)
        resolved = core_helper(core, "some_name", real_impl)
        assert resolved is fake_impl

    def test_falls_back_to_local_when_core_module_lacks_the_attribute(self):
        def real_impl():
            return "real"

        core = types.SimpleNamespace()
        resolved = core_helper(core, "some_name", real_impl)
        assert resolved is real_impl

    def test_falls_back_to_local_when_core_attribute_is_identical_to_local(self):
        def real_impl():
            return "real"

        core = types.SimpleNamespace(some_name=real_impl)
        resolved = core_helper(core, "some_name", real_impl)
        assert resolved is real_impl
