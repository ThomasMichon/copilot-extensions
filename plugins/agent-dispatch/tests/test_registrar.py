"""Tests for the task registrar declaration schema (Phase 1, pure)."""

from __future__ import annotations

import pytest

from agent_dispatch.registrar import (
    Body,
    Fleet,
    RegistrarError,
    declaration_from_env,
    load_declaration,
)


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
    assert d.reactive is True
    assert d.body == Body()  # embody / task-worker
    assert d.fleet == Fleet()


def test_full_general_pool_declaration():
    d = load_declaration(
        {
            "name": "general",
            "labels": ["general"],
            "concurrency": 2,
            "interval": 30,
            "body": {"type": "headless", "agent": "general-loop-worker"},
            "owner": "aperture-labs",
            "description": "General-purpose loop pool",
        }
    )
    assert d.labels == ("general",)
    assert d.concurrency == 2
    assert d.body.type == "headless"
    assert d.body.agent == "general-loop-worker"
    assert d.owner == "aperture-labs"


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


def test_concurrency_must_be_positive():
    with pytest.raises(RegistrarError, match="concurrency"):
        load_declaration({"name": "x", "concurrency": 0})


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
    # a headless local body routes its labels headless + names the agent
    assert _flag_val(args, "--headless-label") == "general"
    assert _flag_val(args, "--headless-agent") == "general-loop-worker"


def test_supervise_args_embody_body_has_no_headless_label():
    d = load_declaration({"name": "boards", "labels": ["cab"], "repos": "all"})
    args = d.to_supervise_args()
    assert "--headless-label" not in args
    assert "--headless-agent" not in args


def test_supervise_args_lane_scoped():
    d = load_declaration({"name": "x", "labels": ["l"], "repos": "aperture-labs"})
    args = d.to_supervise_args()
    assert "--all-repos" not in args
    assert _flag_val(args, "--repo") == "aperture-labs"


def test_supervise_args_fleet():
    d = load_declaration(
        {
            "name": "fleetpool",
            "labels": ["cab"],
            "fleet": {"pool": ["lambda-core-wsl"], "origin": "wheatley", "headless": True},
            "body": {"type": "headless", "agent": "task-worker"},
        }
    )
    args = d.to_supervise_args()
    assert _flag_val(args, "--pool") == "lambda-core-wsl"
    assert _flag_val(args, "--origin") == "wheatley"
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
    assert _flag_val(args, "--headless-label") == "document-intake-processing"
    assert _flag_val(args, "--headless-agent") == "document-intake-processor"


def test_env_migration_parses_extra_args_fleet():
    env = {
        "AGENT_DISPATCH_SUPERVISE_LABELS": "cab",
        "AGENT_DISPATCH_SUPERVISE_HEADLESS_AGENT": "task-worker",
        "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS": "--pool lambda-core-wsl --origin wheatley --headless",
    }
    d = declaration_from_env("cab", env)
    assert d.fleet.pool == ("lambda-core-wsl",)
    assert d.fleet.origin == "wheatley"
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
    assert _flag_val(args, "--headless-label") == "general"
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


# -- helpers -----------------------------------------------------------------

def _flag_val(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]
