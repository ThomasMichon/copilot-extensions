"""Regression tests for the "no sibling plugin writes tracking YAML
directly" structural guard (agent-worktrees-authoritative-daemon effort,
Phase 4). Scope: every `plugins/*` directory except `agent-worktrees`
itself -- not filtered by an `agent-*` name prefix (matching the guard's
own actual scope, see its docstring).

``find_violations``/``_check_file`` accept an explicit repo root, so most
cases are plain unit tests against a throwaway directory tree (no
subprocess, no git). One end-to-end smoke test drives the real script as a
subprocess against the actual repo checkout, confirming it stays clean --
mirrors test_check_no_agent_machines_packages.py's own pattern.

Run:  python -m pytest tools/test_check_no_sibling_tracking_writes.py
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-no-sibling-tracking-writes.py"
REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("check_no_sibling_tracking_writes", SCRIPT)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _write_plugin_file(root: Path, plugin: str, rel: str, content: str) -> Path:
    path = root / "plugins" / plugin / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_clean_tree_has_no_violations(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "def hello():\n    return 1\n",
    )
    assert guard.find_violations(tmp_path) == []


def test_read_only_accessor_is_not_flagged(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def get(wt_id):\n"
        "    return tracking.load_record_by_id(wt_id)\n",
    )
    assert guard.find_violations(tmp_path) == []


def test_flags_direct_call_via_module_import(tmp_path):
    path = _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def write(record, wt_path):\n"
        "    tracking.save_record(record, wt_path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert violations[0].path == path
    assert "save_record" in violations[0].detail


def test_flags_direct_from_import_of_write_function(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import save_record\n"
        "\n"
        "def write(record, wt_path):\n"
        "    save_record(record, wt_path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_flags_import_from_owning_submodule(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees.tracking_claims import add_follow_up\n"
        "\n"
        "def note(record, summary):\n"
        "    add_follow_up(record, summary)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "add_follow_up" in violations[0].detail


def test_owning_plugin_itself_is_never_scanned(tmp_path):
    # agent-worktrees is exempt: it's the module that OWNS these writes.
    _write_plugin_file(
        tmp_path, "agent-worktrees", "src/agent_worktrees/tracking.py",
        "def save_record(record, path):\n    pass\n",
    )
    assert guard.find_violations(tmp_path) == []


def test_aliased_module_import_is_still_caught(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking as wt_tracking\n"
        "\n"
        "def write(record, path):\n"
        "    wt_tracking.stamp_mux_live(\"wt-1\", True)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "stamp_mux_live" in violations[0].detail


def test_package_root_aliased_import_is_caught(tmp_path):
    # `import agent_worktrees as aw; aw.tracking.save_record(...)` --
    # a two-level attribute chain off a package-root alias, not a module
    # alias -- must be caught just like the single-level module-alias form.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import agent_worktrees as aw\n"
        "\n"
        "def write(record, path):\n"
        "    aw.tracking.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_unaliased_package_root_import_is_caught(tmp_path):
    # The unaliased equivalent: `import agent_worktrees;
    # agent_worktrees.tracking.save_record(...)`.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import agent_worktrees\n"
        "\n"
        "def write(record, path):\n"
        "    agent_worktrees.tracking.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_tracking_controller_relations_reexport_is_caught(tmp_path):
    # tracking_controller_relations.py re-exports save_record (a thin
    # `from .tracking import save_record as impl; impl(...)` wrapper) --
    # importing that module's own copy must be caught identically to
    # importing tracking.py's directly.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking_controller_relations\n"
        "\n"
        "def write(record, path):\n"
        "    tracking_controller_relations.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_dotted_import_without_alias_binds_the_package_not_the_submodule(tmp_path):
    # `import agent_worktrees.tracking` (NO `as`) is Python-bound to the
    # top-level name `agent_worktrees`, never a bare `tracking` -- the
    # submodule is only reachable via the two-level
    # `agent_worktrees.tracking...` chain. A prior version of this guard
    # mismodeled this shape as a `tracking` module-alias binding, which
    # both mis-caught an unrelated bare `tracking.save_record(...)` name
    # collision and MISSED this actual valid import shape.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import agent_worktrees.tracking\n"
        "\n"
        "def write(record, path):\n"
        "    agent_worktrees.tracking.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_controller_relation_functions_are_denylisted(tmp_path):
    # backfill_legacy_controller_relations / set_controller_relation /
    # end_controller_relation / remove_controller_relation persist records
    # in tracking_controller_relations.py and are ALSO re-exported by
    # tracking.py itself -- both import paths must be caught.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def write(record, **kw):\n"
        "    tracking.set_controller_relation(record, **kw)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "set_controller_relation" in violations[0].detail


def test_load_or_create_anchor_record_is_denylisted(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking_claims\n"
        "\n"
        "def write(*a, **kw):\n"
        "    tracking_claims.load_or_create_anchor_record(*a, **kw)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "load_or_create_anchor_record" in violations[0].detail


def test_wildcard_import_of_agent_worktrees_is_rejected_outright(tmp_path):
    # `from agent_worktrees import *` then a bare `save_record(...)` call
    # produces an ImportFrom alias literally named "*" -- neither the
    # module-alias nor the direct-write-function branch ever matches it,
    # so the bare call is never inspected. Reject the wildcard import
    # itself outright rather than trying to resolve its exported names.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import *\n"
        "\n"
        "def write(record, path):\n"
        "    save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "wildcard" in violations[0].detail


def test_wildcard_import_of_tracking_submodule_is_rejected_outright(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees.tracking import *\n"
        "\n"
        "def write(record, path):\n"
        "    save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "wildcard" in violations[0].detail


def test_verb_handler_module_direct_call_is_caught(tmp_path):
    # tracking_claim_write.apply_claim_add acquires _RecordLock, mutates
    # the record, and calls tracking.save_record itself -- importing it
    # directly is the SAME bypass as importing tracking.save_record, just
    # skipping the verb-dispatch layer on the way there.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking_claim_write\n"
        "\n"
        "def write(args):\n"
        "    tracking_claim_write.apply_claim_add(args)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "apply_claim_add" in violations[0].detail


def test_verb_handler_direct_from_import_is_caught(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees.tracking_session_registration_write import "
        "apply_session_register\n"
        "\n"
        "def write(args):\n"
        "    apply_session_register(args)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "apply_session_register" in violations[0].detail


def test_private_liveness_writers_are_denylisted(tmp_path):
    # _stamp_liveness (backing stamp_mux_live/stamp_bound_live) and
    # _apply_session_state_stamp (backing stamp_session_state) are private
    # but nothing in Python actually prevents importing them directly --
    # both persist a record and must be caught identically to their public
    # wrappers.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def write(wt_id):\n"
        "    tracking._stamp_liveness(wt_id, True, live_attr=\"mux_live\", "
        "at_attr=\"mux_live_at\", refresh=False, throttle_secs=60.0)\n"
        "    tracking._apply_session_state_stamp(wt_id)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 2
    details = {v.detail for v in violations}
    assert any("_stamp_liveness" in d for d in details)
    assert any("_apply_session_state_stamp" in d for d in details)


def test_tracking_write_direct_execution_apis_are_denylisted(tmp_path):
    # tracking_write.run_direct(verb, args, reason=...) and
    # tracking_write.compute(kind, payload) both load and invoke a
    # registered verb's handler IN-PROCESS -- the same bypass as importing
    # the handler (e.g. apply_claim_add) itself, just without ever naming
    # it. dispatch/write_with_boot are the sanctioned daemon-mediated API
    # and must NOT be flagged.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking_write\n"
        "\n"
        "def write(args):\n"
        "    tracking_write.run_direct(\"claim_add\", args, reason=\"x\")\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "run_direct" in violations[0].detail


def test_tracking_write_dispatch_is_not_flagged(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking_write\n"
        "\n"
        "def write(args):\n"
        "    tracking_write.dispatch(\"claim_add\", args)\n"
        "    tracking_write.write_with_boot(\"claim_add\", args)\n",
    )
    assert guard.find_violations(tmp_path) == []


def test_reassignment_of_a_module_alias_is_still_caught(tmp_path):
    # A trivial rename (`writer_module = tracking`) must not evade the
    # alias tracking above -- the reassignment propagation pass exists
    # exactly to close this.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "writer_module = tracking\n"
        "\n"
        "def write(record, path):\n"
        "    writer_module.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_chained_reassignment_of_a_package_alias_is_still_caught(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import agent_worktrees as aw\n"
        "a = aw\n"
        "b = a\n"
        "\n"
        "def write(record, path):\n"
        "    b.tracking.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_attribute_reassignment_off_a_package_alias_is_still_caught(tmp_path):
    # `tracking_module = aw.tracking` -- a two-level Attribute RHS, not a
    # bare Name -- must resolve to the same module alias a direct
    # `aw.tracking.save_record(...)` call already catches.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import agent_worktrees as aw\n"
        "tracking_module = aw.tracking\n"
        "\n"
        "def write(record, path):\n"
        "    tracking_module.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_annotated_assignment_alias_is_still_caught(tmp_path):
    # `writer_module: object = tracking` -- a type annotation adds no
    # actual indirection, so this AnnAssign form must be treated
    # identically to the plain `writer_module = tracking` form.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "writer_module: object = tracking\n"
        "\n"
        "def write(record, path):\n"
        "    writer_module.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_annotated_attribute_assignment_alias_is_still_caught(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import agent_worktrees as aw\n"
        "tracking_module: object = aw.tracking\n"
        "\n"
        "def write(record, path):\n"
        "    tracking_module.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_getattr_with_a_literal_write_function_name_is_caught(tmp_path):
    # getattr(tracking, "save_record") never produces an ast.Attribute
    # node, so it evades the plain attribute-access check entirely --
    # but the literal string name still resolves statically.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def write(record, path):\n"
        "    getattr(tracking, \"save_record\")(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_getattr_via_package_alias_chain_is_caught(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import agent_worktrees as aw\n"
        "\n"
        "def write(record, path):\n"
        "    getattr(aw.tracking, \"save_record\")(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_getattr_with_a_non_literal_name_is_not_claimed_safe(tmp_path):
    # A non-literal getattr name (e.g. a variable) is genuinely
    # undecidable statically -- this guard does not attempt it, and this
    # test documents that boundary rather than asserting false safety.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def write(record, path, fn_name):\n"
        "    getattr(tracking, fn_name)(record, path)\n",
    )
    assert guard.find_violations(tmp_path) == []


def test_getattr_with_a_constant_string_alias_name_is_caught(tmp_path):
    # fn = "save_record"; getattr(tracking, fn)(...) -- the fetched name
    # is a NAME, but one previously assigned a plain string literal, so
    # it resolves the same as an inline literal would (the same
    # constant-string resolution the dynamic-import checks already use).
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "fn = \"save_record\"\n"
        "\n"
        "def write(record, path):\n"
        "    getattr(tracking, fn)(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_dunder_dict_subscript_with_a_constant_string_alias_key_is_caught(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "key = \"_STAMP_QUEUE\"\n"
        "\n"
        "def read():\n"
        "    return tracking.__dict__[key]\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "_STAMP_QUEUE" in violations[0].detail


def test_dunder_dict_subscript_with_a_non_literal_key_is_not_claimed_safe(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def read(key):\n"
        "    return tracking.__dict__[key]\n",
    )
    assert guard.find_violations(tmp_path) == []


def test_dunder_dict_subscript_with_a_literal_write_function_name_is_caught(
    tmp_path,
):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def write(record, path):\n"
        "    tracking.__dict__[\"save_record\"](record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_getattr_of_an_unrelated_module_is_not_flagged(tmp_path):
    # getattr on something that isn't a tracked module/package alias must
    # never be flagged, regardless of the literal name it fetches.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import os\n"
        "\n"
        "def read():\n"
        "    return getattr(os, \"save_record\")\n",
    )
    assert guard.find_violations(tmp_path) == []


def test_literal_importlib_import_module_is_caught(tmp_path):
    # importlib.import_module("agent_worktrees.tracking") never produces
    # an Import/ImportFrom node, so it evades ordinary alias tracking --
    # but the literal string target still resolves statically.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import importlib\n"
        "\n"
        "def write(record, path):\n"
        "    tracking = importlib.import_module(\"agent_worktrees.tracking\")\n"
        "    tracking.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_literal_dunder_import_of_the_bare_package_is_caught(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "def write(record, path):\n"
        "    aw = __import__(\"agent_worktrees\")\n"
        "    aw.tracking.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_inline_literal_dynamic_import_call_is_caught(tmp_path):
    # No assignment at all -- the call result is used directly.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import importlib\n"
        "\n"
        "def write(record, path):\n"
        "    importlib.import_module(\"agent_worktrees.tracking\")"
        ".save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_dynamic_import_with_a_non_literal_target_is_not_claimed_safe(tmp_path):
    # A non-literal import target (a variable) is genuinely undecidable
    # statically -- documents the boundary rather than asserting false
    # safety, mirroring the getattr non-literal test.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import importlib\n"
        "\n"
        "def write(record, path, module_name):\n"
        "    tracking = importlib.import_module(module_name)\n"
        "    tracking.save_record(record, path)\n",
    )
    assert guard.find_violations(tmp_path) == []


def test_dynamic_import_of_an_unrelated_module_is_not_flagged(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import importlib\n"
        "\n"
        "def read():\n"
        "    os_mod = importlib.import_module(\"os\")\n"
        "    return os_mod.save_record\n",
    )
    assert guard.find_violations(tmp_path) == []


def test_dynamic_import_target_via_a_constant_string_alias_is_caught(tmp_path):
    # mod_name = "agent_worktrees.tracking"; importlib.import_module(mod_name)
    # -- the target is a NAME, but one previously assigned a plain string
    # literal, so it resolves the same as an inline literal would.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import importlib\n"
        "mod_name = \"agent_worktrees.tracking\"\n"
        "\n"
        "def write(record, path):\n"
        "    tracking = importlib.import_module(mod_name)\n"
        "    tracking.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_dunder_import_without_fromlist_resolves_to_the_package(tmp_path):
    # __import__("agent_worktrees.tracking") with NO fromlist returns the
    # TOP-LEVEL agent_worktrees package (CPython's own __import__
    # semantics), not the tracking submodule directly -- the submodule is
    # only reachable via a further `.tracking` access, exactly like the
    # package-alias chain from an ordinary `import agent_worktrees`.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "def write(record, path):\n"
        "    aw = __import__(\"agent_worktrees.tracking\")\n"
        "    aw.tracking.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_dunder_import_without_fromlist_is_not_the_submodule_directly(tmp_path):
    # The WRONG (pre-fix) resolution would have treated `aw` itself as a
    # tracking-module alias, so `aw.save_record(...)` (skipping the
    # `.tracking` hop entirely) must NOT be flagged -- that's not what
    # __import__ without fromlist actually returns.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "def write(record, path):\n"
        "    aw = __import__(\"agent_worktrees.tracking\")\n"
        "    aw.save_record(record, path)\n",
    )
    assert guard.find_violations(tmp_path) == []


def test_dunder_import_with_fromlist_resolves_to_the_submodule(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "def write(record, path):\n"
        "    tracking = __import__(\"agent_worktrees.tracking\", "
        "fromlist=[\"tracking\"])\n"
        "    tracking.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_dunder_import_with_empty_fromlist_list_resolves_to_the_package(tmp_path):
    # fromlist=[] is exactly equivalent to omitting the argument --
    # CPython still returns the top-level package, not the submodule.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "def write(record, path):\n"
        "    aw = __import__(\"agent_worktrees.tracking\", fromlist=[])\n"
        "    aw.save_record(record, path)\n",
    )
    assert guard.find_violations(tmp_path) == []


def test_dunder_import_with_empty_fromlist_still_caught_via_package_chain(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "def write(record, path):\n"
        "    aw = __import__(\"agent_worktrees.tracking\", fromlist=[])\n"
        "    aw.tracking.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_dunder_import_with_none_fromlist_resolves_to_the_package(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "def write(record, path):\n"
        "    aw = __import__(\"agent_worktrees.tracking\", fromlist=None)\n"
        "    aw.save_record(record, path)\n",
    )
    assert guard.find_violations(tmp_path) == []


def test_getattr_of_stamp_queue_attribute_is_caught(tmp_path):
    # getattr(tracking, "_STAMP_QUEUE") reflectively fetches the exact
    # same object the plain `tracking._STAMP_QUEUE` attribute check
    # already flags -- caught the moment the queue is reflectively
    # reached, regardless of what's chained onto the result afterward.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def write():\n"
        "    return getattr(tracking, \"_STAMP_QUEUE\")\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "_STAMP_QUEUE" in violations[0].detail


def test_dunder_dict_subscript_of_stamp_queue_attribute_is_caught(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def write():\n"
        "    return tracking.__dict__[\"_STAMP_QUEUE\"]\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "_STAMP_QUEUE" in violations[0].detail


def test_getattr_of_queue_write_method_is_caught(tmp_path):
    # getattr(tracking._STAMP_QUEUE, "submit")(...) -- reflectively
    # fetching a write-triggering METHOD off an ALREADY-resolved queue
    # expression, not the queue object itself (that's the previous test).
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def write(wt_id):\n"
        "    getattr(tracking._STAMP_QUEUE, \"submit\")(wt_id, turns=1)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "submit" in violations[0].detail


def test_dunder_dict_subscript_of_queue_write_method_is_caught(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def write(wt_id):\n"
        "    tracking._STAMP_QUEUE.__dict__[\"submit\"](wt_id, turns=1)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "submit" in violations[0].detail


def test_queue_write_method_via_a_queue_alias_reflective_access_is_caught(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "queue = tracking._STAMP_QUEUE\n"
        "\n"
        "def write(wt_id):\n"
        "    getattr(queue, \"submit\")(wt_id, turns=1)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "submit" in violations[0].detail


def test_inline_dunder_import_package_chain_is_caught(tmp_path):
    # __import__("agent_worktrees.tracking").tracking.save_record(...)
    # -- __import__ with no fromlist returns the top-level package
    # INLINE (never assigned to a variable), and the `.tracking` hop off
    # that call result reaches the protected module directly.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "def write(record, path):\n"
        "    __import__(\"agent_worktrees.tracking\")"
        ".tracking.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_stamp_write_queue_class_is_denylisted(tmp_path):
    # Importing the _StampWriteQueue CLASS directly and instantiating a
    # fresh queue reaches the exact same YAML-writing helpers as the
    # resident singleton, just via a separate instance.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def write():\n"
        "    return tracking._StampWriteQueue()\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "_StampWriteQueue" in violations[0].detail


def test_tracking_lazy_helper_call_is_caught(tmp_path):
    # from agent_worktrees.tracking_lifecycle import _tracking;
    # _tracking().save_record(...) -- an internal lazy-import helper
    # (used across several agent_worktrees modules to dodge circular
    # imports) that always returns the `tracking` submodule itself.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees.tracking_lifecycle import _tracking\n"
        "\n"
        "def write(record, path):\n"
        "    _tracking().save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_unrelated_similarly_named_helper_call_is_not_flagged(tmp_path):
    # A LOCAL function merely named `_tracking` (never imported from any
    # agent_worktrees module) must never be flagged -- only a `_tracking`
    # name actually imported from a protected module is recognized.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "def _tracking():\n"
        "    return object()\n"
        "\n"
        "def write():\n"
        "    return _tracking().save_record\n",
    )
    assert guard.find_violations(tmp_path) == []


def test_tracking_lazy_helper_result_reassigned_is_still_caught(tmp_path):
    # tracking = _tracking(); tracking.save_record(...) -- the helper's
    # RETURN VALUE reassigned to a new name before use.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees.tracking_lifecycle import _tracking\n"
        "tracking = _tracking()\n"
        "\n"
        "def write(record, path):\n"
        "    tracking.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_getattr_package_to_module_hop_is_caught(tmp_path):
    # import agent_worktrees as aw; getattr(aw, "tracking").save_record(...)
    # -- a single reflective CALL doing the package-to-module hop that
    # the plain `aw.tracking` Attribute chain already catches.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import agent_worktrees as aw\n"
        "\n"
        "def write(record, path):\n"
        "    getattr(aw, \"tracking\").save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_getattr_package_to_module_hop_reassigned_is_still_caught(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import agent_worktrees as aw\n"
        "tracking = getattr(aw, \"tracking\")\n"
        "\n"
        "def write(record, path):\n"
        "    tracking.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_aliased_importlib_module_is_still_caught(tmp_path):
    # `import importlib as il; il.import_module(...)` -- an aliased
    # `importlib` module binding, not the literal identifier.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import importlib as il\n"
        "\n"
        "def write(record, path):\n"
        "    tracking = il.import_module(\"agent_worktrees.tracking\")\n"
        "    tracking.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_bare_import_module_name_is_still_caught(tmp_path):
    # `from importlib import import_module` -- calling it with no
    # `importlib.` prefix at all.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from importlib import import_module\n"
        "\n"
        "def write(record, path):\n"
        "    tracking = import_module(\"agent_worktrees.tracking\")\n"
        "    tracking.save_record(record, path)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "save_record" in violations[0].detail


def test_stamp_queue_submit_is_caught(tmp_path):
    # tracking._STAMP_QUEUE.submit(...) bypasses stamp_session_state's
    # own wrapper while triggering the exact same persisted write.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def write(wt_id):\n"
        "    tracking._STAMP_QUEUE.submit(wt_id, turns=1)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "_STAMP_QUEUE.submit" in violations[0].detail


def test_stamp_queue_submit_mux_via_package_alias_is_caught(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "import agent_worktrees as aw\n"
        "\n"
        "def write(wt_id):\n"
        "    aw.tracking._STAMP_QUEUE.submit_mux(\n"
        "        wt_id, True, refresh=False, throttle_secs=60.0)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "_STAMP_QUEUE.submit_mux" in violations[0].detail


def test_stamp_queue_apply_is_caught(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def write(wt_id):\n"
        "    tracking._STAMP_QUEUE._apply(wt_id)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "_STAMP_QUEUE._apply" in violations[0].detail


def test_stamp_queue_read_only_attribute_is_not_flagged(tmp_path):
    # An unrelated attribute access on the queue object (not one of its
    # write-triggering methods) must never be flagged.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def inspect(wt_id):\n"
        "    return tracking._STAMP_QUEUE._pending\n",
    )
    assert guard.find_violations(tmp_path) == []


def test_stamp_queue_reassigned_to_a_new_name_is_still_caught(tmp_path):
    # `queue = tracking._STAMP_QUEUE; queue.submit(...)` -- the queue
    # OBJECT itself is bound to a new name, not the module, so the
    # inline three-level chain check alone would miss this.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "queue = tracking._STAMP_QUEUE\n"
        "\n"
        "def write(wt_id):\n"
        "    queue.submit(wt_id, turns=1)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "queue.submit" in violations[0].detail


def test_stamp_queue_imported_directly_is_still_caught(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees.tracking import _STAMP_QUEUE\n"
        "\n"
        "def write(wt_id):\n"
        "    _STAMP_QUEUE.submit(wt_id, turns=1)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "_STAMP_QUEUE.submit" in violations[0].detail


def test_atomic_write_is_denylisted(tmp_path):
    # tracking._atomic_write is the low-level function save_record itself
    # calls to actually emit the serialized YAML -- a sibling could use
    # it to write raw content straight to a tracking.yaml path, bypassing
    # every validation/locking layer above it.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def write(path, content):\n"
        "    tracking._atomic_write(path, content)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "_atomic_write" in violations[0].detail


def test_replace_with_retry_is_denylisted(tmp_path):
    # _replace_with_retry is the even-lower-level os.replace(src, dst)
    # helper _atomic_write itself calls -- a sibling could stage a file
    # and replace it straight over a tracking.yaml path directly.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n"
        "\n"
        "def write(src, dst):\n"
        "    tracking._replace_with_retry(src, dst)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "_replace_with_retry" in violations[0].detail


def test_verbs_registry_subscript_is_denylisted(tmp_path):
    # tracking_write._VERBS["claim_add"](args) reaches a mutating handler
    # via the raw registry dict, never naming run_direct, compute, or any
    # apply_* function -- denylisting the `_VERBS` attribute NAME itself
    # catches this the moment it's accessed, regardless of which key is
    # subscripted.
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking_write\n"
        "\n"
        "def write(args):\n"
        "    tracking_write._VERBS[\"claim_add\"](args)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "_VERBS" in violations[0].detail


def test_verbs_registry_bound_to_a_variable_is_still_denylisted(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking_write\n"
        "\n"
        "def write(args):\n"
        "    verbs = tracking_write._VERBS\n"
        "    verbs[\"claim_add\"](args)\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "_VERBS" in violations[0].detail


def test_unreadable_file_fails_closed_not_silently_skipped(tmp_path, monkeypatch):
    # A file the guard cannot even read must be reported, not silently
    # treated as clean -- otherwise an unreadable/undecodable file is a
    # guaranteed way to smuggle a forbidden import past CI.
    path = _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "from agent_worktrees import tracking\n",
    )
    real_read_text = Path.read_text

    def _boom(self, *a, **kw):
        if self == path:
            raise OSError("permission denied (simulated)")
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _boom)
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "could not be read" in violations[0].detail


def test_syntactically_invalid_file_fails_closed_not_silently_skipped(tmp_path):
    _write_plugin_file(
        tmp_path, "agent-example", "src/agent_example/thing.py",
        "def broken(:\n    pass\n",
    )
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert "syntax error" in violations[0].detail


def test_one_ast_walk_preserves_import_then_breadth_first_diagnostics(tmp_path, monkeypatch):
    path = _write_plugin_file(
        tmp_path, "example", "nested.py",
        "from agent_worktrees.tracking import save_record\n"
        "from agent_worktrees import tracking\n"
        "def nested():\n"
        "    tracking.retire_record(record)\n"
        "tracking.save_record(record, path)\n",
    )
    real_walk = guard.ast.walk
    walks = []

    def counted_walk(tree):
        walks.append(tree)
        return real_walk(tree)

    monkeypatch.setattr(guard.ast, "walk", counted_walk)
    violations = guard._check_file(path, repo_root=tmp_path)
    assert len(walks) == 1
    assert [(v.line, v.detail) for v in violations] == [
        (1, "imports write function `save_record` directly "
         "from `agent_worktrees.tracking`"),
        (5, "calls write function `tracking.save_record` directly"),
        (4, "calls write function `tracking.retire_record` directly"),
    ]


def test_reused_assignments_reach_fixed_point_across_reverse_alias_chains(tmp_path):
    path = _write_plugin_file(
        tmp_path, "example", "aliases.py",
        "from importlib import import_module as load\n"
        "queue_copy = queue\n"
        "queue: object = module_copy._STAMP_QUEUE\n"
        "module_copy = module\n"
        "module: object = package_copy.tracking\n"
        "package_copy = package\n"
        "package = load(module_name)\n"
        "module_name: str = 'agent_worktrees'\n"
        "queue_copy.submit(record)\n"
        "module_copy.save_record(record, path)\n"
        "package_copy.tracking.retire_record(record)\n",
    )
    violations = guard._check_file(path, repo_root=tmp_path)
    assert [(v.line, v.detail) for v in violations] == [
        (9, "calls write-triggering queue method `queue_copy.submit` directly"),
        (10, "calls write function `module_copy.save_record` directly"),
        (11, "calls write function `package_copy.tracking.retire_record` directly"),
    ]


def test_invalid_utf8_file_fails_closed(tmp_path):
    path = _write_plugin_file(tmp_path, "example", "invalid.py", "")
    path.write_bytes(b"\xff")
    violations = guard._check_file(path, repo_root=tmp_path)
    assert len(violations) == 1
    assert violations[0].line == 0
    assert "is not valid UTF-8" in violations[0].detail


@pytest.mark.parametrize("source, expected_count", [
    ("import \uff41gent_worktrees as aw\naw.tracking.save_record(record, path)\n", 1),
    ("from \uff41gent_worktrees.tracking import save_record\n", 1),
    ("from agent_worktrees.tracking import _tracking as lazy\n"
     "getattr(lazy(), 'save_record')(record, path)\n", 1),
    ("__import__('agent_\\x77orktrees.tracking', fromlist=['x'])"
     ".save_record(record, path)\n", 1),
    ("__import__('agent_' 'worktrees').tracking.save_record(record, path)\n", 1),
    ("from importlib import import_module as load\n"
     "module = load(target)\ntarget: str = 'agent_\\u0077orktrees.tracking'\n"
     "module.__dict__['save_record'](record, path)\n", 1),
    ("from agent_worktrees.tracking import _STAMP_QUEUE as queue\n"
     "getattr(queue, 'submit')(record)\n", 1),
    ("from agent_worktrees import tracking\n"
     "tracking.__dict__['_STAMP_QUEUE']\n", 1),
    ("from agent_worktrees import *\n", 1),
    ("from agent_worktrees.tracking import *\n", 1),
    ("import unrelated as tracking\ntracking.save_record(record, path)\n", 0),
    ("from agent_worktrees_lookalike import tracking\n"
     "tracking.save_record(record, path)\n", 0),
    ("__import__('agent_' + 'worktrees').tracking.save_record(record, path)\n", 0),
    ("def write(target):\n    __import__(target).tracking.save_record(record, path)\n", 0),
    ("def _tracking():\n    return object()\n_tracking().save_record(record, path)\n", 0),
])
def test_root_admission_matches_unfiltered_analysis(tmp_path, monkeypatch, source, expected_count):
    path = _write_plugin_file(tmp_path, "example", "roots.py", source)
    actual = [(v.line, v.detail) for v in guard._check_file(path, repo_root=tmp_path)]
    monkeypatch.setattr(guard, "_has_tracking_root", lambda *_: True)
    expected = [(v.line, v.detail) for v in guard._check_file(path, repo_root=tmp_path)]
    assert actual == expected
    assert len(actual) == expected_count


def test_rootless_tree_still_reads_and_parses_each_changed_input(tmp_path, monkeypatch):
    path = _write_plugin_file(tmp_path, "example", "changing.py", "value = 1\n")
    read_text, parse = Path.read_text, guard.ast.parse
    reads, parses, admissions = [], [], []
    has_root = guard._has_tracking_root

    def reading(target, *args, **kwargs):
        reads.append(target)
        return read_text(target, *args, **kwargs)

    def parsing(source, *args, **kwargs):
        parses.append(source)
        return parse(source, *args, **kwargs)

    def admitting(nodes, targets):
        result = has_root(nodes, targets)
        admissions.append(result)
        return result

    monkeypatch.setattr(Path, "read_text", reading)
    monkeypatch.setattr(guard.ast, "parse", parsing)
    monkeypatch.setattr(guard, "_has_tracking_root", admitting)
    assert guard._check_file(path, repo_root=tmp_path) == []
    path.write_text("from agent_worktrees import tracking\ntracking.save_record(record, path)\n",
                    encoding="utf-8")
    assert len(guard._check_file(path, repo_root=tmp_path)) == 1
    path.write_text("def broken(:\n", encoding="utf-8")
    violations = guard._check_file(path, repo_root=tmp_path)
    assert len(violations) == 1
    assert "syntax error" in violations[0].detail
    assert reads == [path, path, path]
    assert len(parses) == 3
    assert admissions == [False, True]


def test_rootless_tree_does_not_enter_alias_analysis(tmp_path, monkeypatch):
    path = _write_plugin_file(
        tmp_path, "example", "unrelated.py",
        "module = unrelated\nmodule.save_record(record, path)\n",
    )
    real_isinstance = isinstance
    assignment_checks = []

    def checking(value, types):
        if types in (guard.ast.Assign, guard.ast.AnnAssign):
            assignment_checks.append(value)
        return real_isinstance(value, types)

    monkeypatch.setattr(guard, "isinstance", checking, raising=False)
    assert guard._check_file(path, repo_root=tmp_path) == []
    assert assignment_checks == []
    monkeypatch.setattr(guard, "_has_tracking_root", lambda *_: True)
    assert guard._check_file(path, repo_root=tmp_path) == []
    assert assignment_checks


def test_real_repo_checkout_is_clean():
    """End-to-end smoke test: the real, live repo tree must never trip this
    guard -- confirms the script (not just find_violations()) exits 0
    against the actual checkout, matching every sibling plugin's true
    (all read-only) tracking usage this effort's own survey found."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
