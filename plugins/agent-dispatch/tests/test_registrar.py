"""Tests for the task registrar declaration schema (Phase 1, pure)."""

from __future__ import annotations

import json

import pytest

from agent_dispatch.registrar import (
    Body,
    Filters,
    Fleet,
    RegistrarError,
    declaration_from_env,
    load_declaration,
)
from agent_dispatch.registrar_discovery import read_declaration_file

# -- Loading + validation ----------------------------------------------------

def test_minimal_declaration_defaults():
    d = load_declaration({"name": "general"})
    assert d.name == "general"
    assert d.labels == ()
    assert d.repos == "all"
    assert d.concurrency == 1
    assert d.interval == 30.0
    assert d.max_attempts == 3
    assert d.heartbeat is True
    assert d.reactive is False
    assert d.body == Body()  # embody / task-worker
    assert d.fleet == Fleet()


def test_reactive_compatibility_value_is_normalized_off():
    assert load_declaration({"name": "general", "reactive": True}).reactive is False


def test_full_general_pool_declaration():
    d = load_declaration(
        {
            "name": "general",
            "labels": ["general"],
            "concurrency": 2,
            "interval": 30,
            "body": {"type": "headless", "agent": "general-loop-worker"},
            "owner": "test-chamber",
            "description": "General-purpose loop pool",
        }
    )
    assert d.labels == ("general",)
    assert d.concurrency == 2
    assert d.body.type == "headless"
    assert d.body.agent == "general-loop-worker"
    assert d.owner == "test-chamber"


def test_periodic_emitter_declaration():
    d = load_declaration(
        {
            "name": "review-inbox",
            "kind": "emitter",
            "spec": {
                "id": "review-inbox",
                "command": ["review-emitter", "tick"],
                "interval_seconds": 3600,
            },
            "filters": {"permit": {"machine": ["host-a"]}},
        }
    )
    assert d.kind == "emitter"
    assert d.spec["command"] == ["review-emitter", "tick"]
    assert d.filters.permit["machine"] == frozenset({"host-a"})
    args = d.to_supervise_args()
    assert args[:4] == ["supervise", "register", "--kind", "emitter"]
    assert "--spec" in args


def test_plugin_companion_accepts_runtime_generation_override():
    declaration = load_declaration(
        {
            "name": "engine",
            "kind": "plugin-companion",
            "runtime_generation": "engine-v1",
            "transition_group": "engine-runtime",
            "spec": {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "engine",
                            "version": "engine-v1",
                            "profile": "host",
                            "python_env": "ENGINE_PYTHON",
                            "projects": [{"path": ".", "extras": ["engine"]}],
                            "identity_paths": ["src/engine"],
                            "imports": ["example.engine"],
                        }
                    ],
                },
            },
        },
        allow_plugin_companion=True,
    )
    assert declaration.runtime_generation == "engine-v1"
    assert declaration.transition_group == "engine-runtime"


@pytest.mark.parametrize("field", ["runtime_generation", "transition_group"])
def test_plugin_companion_only_fields_are_rejected_outside_plugin_companions(field):
    with pytest.raises(RegistrarError, match=field):
        load_declaration(
            {
                "name": "general",
                "kind": "emitter",
                field: "engine-v1",
                "spec": {
                    "id": "review-inbox",
                    "command": ["review-emitter", "tick"],
                    "interval_seconds": 3600,
                },
            }
        )


def test_non_lane_declaration_rejects_lane_fields():
    with pytest.raises(RegistrarError, match="does not accept lane fields"):
        load_declaration(
            {
                "name": "review-inbox",
                "kind": "emitter",
                "labels": ["review"],
                "spec": {
                    "id": "review-inbox",
                    "command": ["review-emitter", "tick"],
                    "interval_seconds": 3600,
                },
            }
        )


def test_name_is_required():
    with pytest.raises(RegistrarError, match="name"):
        load_declaration({"labels": ["general"]})


def test_unknown_top_level_key_rejected():
    with pytest.raises(RegistrarError, match="unknown key"):
        load_declaration({"name": "x", "concurency": 2})  # typo


def test_unknown_body_key_rejected():
    with pytest.raises(RegistrarError, match="body: unknown key"):
        load_declaration({"name": "x", "body": {"kind": "headless"}})


def test_bad_body_type_rejected():
    with pytest.raises(RegistrarError, match="body.type"):
        load_declaration({"name": "x", "body": {"type": "sidecar"}})


def test_disposable_cli_label_must_be_watched_and_local():
    with pytest.raises(RegistrarError, match="not in labels"):
        load_declaration(
            {
                "name": "x",
                "labels": ["review"],
                "body": {
                    "type": "embody",
                    "disposable_cli_labels": ["other"],
                },
            }
        )
    declaration = load_declaration(
        {
            "name": "x",
            "labels": ["review"],
            "body": {
                "type": "headless",
                "disposable_cli_labels": ["review"],
            },
        }
    )
    assert declaration.body.disposable_cli_labels == ("review",)
    with pytest.raises(RegistrarError, match="only for local"):
        load_declaration(
            {
                "name": "x",
                "labels": ["review"],
                "fleet": {"pool": ["host-a"]},
                "body": {
                    "type": "embody",
                    "disposable_cli_labels": ["review"],
                },
            }
        )


def test_concurrency_must_be_positive():
    with pytest.raises(RegistrarError, match="concurrency"):
        load_declaration({"name": "x", "concurrency": 0})


def test_max_active_processes_is_clear_concurrency_alias():
    declaration = load_declaration(
        {"name": "reviewers", "max_active_processes": 4}
    )
    assert declaration.concurrency == 4
    assert "--max-concurrent" in declaration.to_supervise_args()


def test_concurrency_aliases_must_agree():
    with pytest.raises(RegistrarError, match="must agree"):
        load_declaration(
            {
                "name": "reviewers",
                "concurrency": 2,
                "max_active_processes": 4,
            }
        )


def test_name_charset_enforced():
    with pytest.raises(RegistrarError, match="name"):
        load_declaration({"name": "bad name!"})


def test_labels_accepts_comma_string():
    d = load_declaration({"name": "x", "labels": "a, b c"})
    assert d.labels == ("a", "b", "c")


def test_headless_label_must_be_watched():
    with pytest.raises(RegistrarError, match="not in labels"):
        load_declaration(
            {"name": "x", "labels": ["a"], "body": {"type": "headless", "headless_labels": ["b"]}}
        )


def test_label_max_attempts_typed():
    d = load_declaration({"name": "x", "label_max_attempts": {"general": 0}})
    assert d.label_max_attempts == {"general": 0}
    with pytest.raises(RegistrarError, match="label_max_attempts"):
        load_declaration({"name": "x", "label_max_attempts": {"general": "lots"}})


# -- to_supervise_args (lossless render) -------------------------------------

def test_supervise_args_general_pool_headless():
    d = load_declaration(
        {
            "name": "general",
            "labels": ["general"],
            "concurrency": 2,
            "interval": 30,
            "body": {"type": "headless", "agent": "general-loop-worker"},
        }
    )
    args = d.to_supervise_args()
    assert args[0] == "supervise"
    assert "--all-repos" in args
    assert args.count("--label") == 1
    assert args[args.index("--label") + 1] == "general"
    assert _flag_val(args, "--max-concurrent") == "2"
    assert _flag_val(args, "--interval") == "30"  # no trailing .0
    # a headless local body is headless by DEFAULT -- no per-label --headless-label,
    # just the agent name (and no --embody-backend, since headless is the default)
    assert "--headless-label" not in args
    assert "--embody-backend" not in args
    assert _flag_val(args, "--headless-agent") == "general-loop-worker"


def test_supervise_args_embody_body_has_no_headless_label():
    d = load_declaration({"name": "sweeps", "labels": ["review"], "body": {"type": "embody"}, "repos": "all"})
    args = d.to_supervise_args()
    # an explicit CLI (embody) lane pins the backend and emits no headless flags
    assert _flag_val(args, "--embody-backend") == "cli"
    assert "--headless-label" not in args
    assert "--headless-agent" not in args


def test_supervise_args_emit_disposable_cli_label():
    declaration = load_declaration(
        {
            "name": "reviewers",
            "labels": ["review"],
            "body": {
                "type": "embody",
                "disposable_cli_labels": ["review"],
            },
        }
    )
    args = declaration.to_supervise_args()
    assert _flag_val(args, "--disposable-cli-label") == "review"


def test_supervise_args_lane_scoped():
    d = load_declaration({"name": "x", "labels": ["l"], "repos": "test-chamber"})
    args = d.to_supervise_args()
    assert "--all-repos" not in args
    assert _flag_val(args, "--repo") == "test-chamber"


def test_supervise_args_fleet():
    d = load_declaration(
        {
            "name": "fleetpool",
            "labels": ["review"],
            "fleet": {"pool": ["anomalous-potato-wsl"], "origin": "mantis-counter", "headless": True},
            "body": {"type": "headless", "agent": "task-worker"},
        }
    )
    args = d.to_supervise_args()
    assert _flag_val(args, "--pool") == "anomalous-potato-wsl"
    assert _flag_val(args, "--origin") == "mantis-counter"
    assert "--headless" in args
    # fleet mode does not emit --headless-label
    assert "--headless-label" not in args


# -- Env migration (lossless) ------------------------------------------------

def test_env_migration_matches_dib_profile():
    env = {
        "AGENT_DISPATCH_SUPERVISE_LABELS": "document-intake-processing",
        "AGENT_DISPATCH_SUPERVISE_HEADLESS_LABELS": "document-intake-processing",
        "AGENT_DISPATCH_SUPERVISE_HEADLESS_AGENT": "document-intake-processor",
        "AGENT_DISPATCH_SUPERVISE_MAX_CONCURRENT": "1",
        "AGENT_DISPATCH_SUPERVISE_INTERVAL": "30",
    }
    d = declaration_from_env("document-intake", env)
    assert d.name == "document-intake"
    assert d.labels == ("document-intake-processing",)
    assert d.concurrency == 1
    assert d.body.type == "headless"
    assert d.body.agent == "document-intake-processor"
    args = d.to_supervise_args()
    # headless is the default -> the whole lane is headless with no per-label flag
    assert "--headless-label" not in args
    assert "--embody-backend" not in args
    assert _flag_val(args, "--headless-agent") == "document-intake-processor"


def test_env_migration_parses_extra_args_fleet():
    env = {
        "AGENT_DISPATCH_SUPERVISE_LABELS": "review",
        "AGENT_DISPATCH_SUPERVISE_HEADLESS_AGENT": "task-worker",
        "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS": "--pool anomalous-potato-wsl --origin mantis-counter --headless",
    }
    d = declaration_from_env("review", env)
    assert d.fleet.pool == ("anomalous-potato-wsl",)
    assert d.fleet.origin == "mantis-counter"
    assert d.fleet.headless is True
    assert d.body.type == "headless"


def test_env_migration_general_pool_roundtrips_to_expected_args():
    env = {
        "AGENT_DISPATCH_SUPERVISE_LABELS": "general",
        "AGENT_DISPATCH_SUPERVISE_HEADLESS_LABELS": "general",
        "AGENT_DISPATCH_SUPERVISE_HEADLESS_AGENT": "general-loop-worker",
        "AGENT_DISPATCH_SUPERVISE_MAX_CONCURRENT": "2",
        "AGENT_DISPATCH_SUPERVISE_INTERVAL": "30",
    }
    d = declaration_from_env("general", env)
    args = d.to_supervise_args()
    assert _flag_val(args, "--max-concurrent") == "2"
    # headless-by-default: the redundant HEADLESS_LABELS is not re-emitted per-label
    assert "--headless-label" not in args
    assert _flag_val(args, "--headless-agent") == "general-loop-worker"


def test_env_migration_bad_number_raises_registrar_error():
    with pytest.raises(RegistrarError, match="MAX_CONCURRENT"):
        declaration_from_env(
            "x", {"AGENT_DISPATCH_SUPERVISE_MAX_CONCURRENT": "two"}
        )
    with pytest.raises(RegistrarError, match="INTERVAL"):
        declaration_from_env("x", {"AGENT_DISPATCH_SUPERVISE_INTERVAL": "soon"})


def test_env_migration_extra_headless_agent_fallback():
    # No dedicated HEADLESS_AGENT var, but --headless-agent in EXTRA_ARGS -> used.
    d = declaration_from_env(
        "x",
        {
            "AGENT_DISPATCH_SUPERVISE_LABELS": "l",
            "AGENT_DISPATCH_SUPERVISE_HEADLESS_LABELS": "l",
            "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS": "--headless-agent custom-worker",
        },
    )
    assert d.body.type == "headless"
    assert d.body.agent == "custom-worker"


def test_with_owner_stamps_only_when_absent():
    d = load_declaration({"name": "x"})
    assert d.with_owner("repo:foo").owner == "repo:foo"
    d2 = load_declaration({"name": "x", "owner": "explicit"})
    assert d2.with_owner("repo:foo").owner == "explicit"


# -- filters block (pools-as-filters) ----------------------------------------

def test_filters_default_empty():
    d = load_declaration({"name": "general"})
    assert d.filters == Filters()
    assert d.filters.is_empty()


def test_filters_explicit_permit_reject_loaded():
    d = load_declaration(
        {
            "name": "review",
            "filters": {
                "permit": {"task-type": ["review"], "role": ["reviewer"]},
                "reject": {"role": ["intern"]},
            },
        }
    )
    assert d.filters.permit["task-type"] == frozenset({"review"})
    assert d.filters.permit["role"] == frozenset({"reviewer"})
    assert d.filters.reject["role"] == frozenset({"intern"})


def test_filters_unknown_side_key_rejected():
    with pytest.raises(RegistrarError, match="filters: unknown key"):
        load_declaration({"name": "x", "filters": {"allow": {"role": ["a"]}}})


def test_filters_unknown_dimension_rejected():
    with pytest.raises(RegistrarError, match="unknown dimension"):
        load_declaration({"name": "x", "filters": {"permit": {"colour": ["blue"]}}})


def test_filters_dimension_underscore_normalized():
    d = load_declaration({"name": "x", "filters": {"permit": {"task_type": ["general"]}}})
    assert d.filters.permit["task-type"] == frozenset({"general"})


def test_permit_membership_scalar():
    d = load_declaration({"name": "review", "filters": {"permit": {"role": ["reviewer"]}}})
    assert d.permits({"role": "reviewer", "task-type": "review"})
    assert not d.permits({"role": "author", "task-type": "review"})


def test_permit_missing_attr_is_wildcard():
    # An untargeted task (no machine declared) binds to a machine-pinned pool.
    d = load_declaration({"name": "review", "filters": {"permit": {"machine": ["host-a"]}}})
    assert d.permits({"task-type": "review"})
    assert d.permits({"task-type": "review", "machine": "host-a"})
    assert not d.permits({"task-type": "review", "machine": "host-b"})


def test_reject_wins_over_permit():
    d = load_declaration(
        {
            "name": "review",
            "filters": {
                "permit": {"task-type": ["review"]},
                "reject": {"repo": ["lane-b"]},
            },
        }
    )
    assert d.permits({"task-type": "review", "repo": "lane-a"})
    assert not d.permits({"task-type": "review", "repo": "lane-b"})


def test_capabilities_subset_semantics():
    d = load_declaration({"name": "review", "filters": {"permit": {"capabilities": ["checkout", "gpu"]}}})
    # task requiring a subset of provided capabilities binds
    assert d.permits({"task-type": "review", "capabilities": ["checkout"]})
    assert d.permits({"task-type": "review"})  # requires nothing
    # task requiring a capability the pool doesn't provide is rejected
    assert not d.permits({"task-type": "review", "capabilities": ["checkout", "tpu"]})


def test_capabilities_reject_intersects():
    d = load_declaration(
        {"name": "review", "filters": {"reject": {"capabilities": ["dangerous"]}}}
    )
    assert d.permits({"task-type": "review", "capabilities": ["safe"]})
    assert not d.permits({"task-type": "review", "capabilities": ["safe", "dangerous"]})


def test_shorthand_task_type_from_name_and_labels():
    # No explicit filters: name + labels become the task-type permit.
    d = load_declaration({"name": "general", "labels": ["general", "loop"]})
    ef = d.effective_filters()
    assert ef.permit["task-type"] == frozenset({"general", "loop"})
    assert d.permits({"task-type": "loop"})
    assert not d.permits({"task-type": "review"})


def test_shorthand_repo_from_lane():
    d = load_declaration({"name": "review", "repos": "lane-a"})
    ef = d.effective_filters()
    assert ef.permit["repo"] == frozenset({"lane-a"})
    assert d.permits({"task-type": "review", "repo": "lane-a"})
    assert not d.permits({"task-type": "review", "repo": "lane-b"})


def test_shorthand_all_repos_has_no_repo_permit():
    d = load_declaration({"name": "general", "repos": "all"})
    assert "repo" not in d.effective_filters().permit
    assert d.permits({"task-type": "general", "repo": "any-lane"})


def test_explicit_filters_win_over_shorthand():
    # An explicit permit.task-type overrides the name/labels default.
    d = load_declaration(
        {"name": "general", "labels": ["general"], "filters": {"permit": {"task-type": ["special"]}}}
    )
    ef = d.effective_filters()
    assert ef.permit["task-type"] == frozenset({"special"})
    assert d.permits({"task-type": "special"})
    assert not d.permits({"task-type": "general"})


def test_permits_rejects_bad_attrs_shape():
    d = load_declaration({"name": "x"})
    with pytest.raises(RegistrarError, match="task attributes"):
        d.permits(["not", "a", "mapping"])  # type: ignore[arg-type]


def test_filters_dont_affect_supervise_args():
    # The filter block is a binding concept, not part of the legacy supervise argv.
    d = load_declaration(
        {"name": "general", "labels": ["general"], "filters": {"permit": {"role": ["worker"]}}}
    )
    assert "--role" not in d.to_supervise_args()


def test_plugin_companion_requires_attributed_discovery():
    data = {
        "name": "index-service",
        "kind": "plugin-companion",
        "spec": {
            "command": ["bin/serve"],
            "stop_command": ["bin/stop"],
            "health_probe": ["bin/health"],
        },
    }
    with pytest.raises(RegistrarError, match="attributed plugin discovery"):
        load_declaration(data)
    assert load_declaration(data, allow_plugin_companion=True).kind == "plugin-companion"


def test_trusted_declaration_file_rejects_plugin_companion(tmp_path):
    path = tmp_path / "companion.json"
    path.write_text(
        json.dumps(
            {
                "name": "index-service",
                "kind": "plugin-companion",
                "spec": {
                    "command": ["bin/serve"],
                    "stop_command": ["bin/stop"],
                    "health_probe": ["bin/health"],
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RegistrarError, match="attributed plugin discovery"):
        read_declaration_file(path)


# -- helpers -----------------------------------------------------------------

def _flag_val(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]
