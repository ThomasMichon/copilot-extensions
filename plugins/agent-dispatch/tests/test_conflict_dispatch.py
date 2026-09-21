"""Tests for the generalized conflict-dispatch primitive."""

from __future__ import annotations

import json

from agent_dispatch import conflict_dispatch as cd


def test_dedup_key_is_label_scoped_to_domain():
    assert cd.dedup_key(label="projection-conflict", domain="my-consumer-repo") == (
        "projection-conflict:my-consumer-repo"
    )


def test_build_conflict_descriptor_shape():
    descriptor = cd.build_conflict_descriptor(
        kind="projection-conflict",
        domain="my-consumer-repo",
        repo="owner/my-consumer-repo",
        pr=42,
        branch="projection-reflect/instructions",
        base="main",
        label="projection-conflict",
    )

    assert descriptor == {
        "schema": cd.SCHEMA,
        "kind": "projection-conflict",
        "domain": "my-consumer-repo",
        "repo": "owner/my-consumer-repo",
        "pr": 42,
        "branch": "projection-reflect/instructions",
        "base": "main",
        "dedup_key": "projection-conflict:my-consumer-repo",
    }


def test_build_conflict_descriptor_carries_optional_extra():
    descriptor = cd.build_conflict_descriptor(
        kind="config-conflict",
        domain="shelly",
        repo="owner/repo",
        pr=7,
        branch="reflect/shelly",
        base="master",
        label="config-conflict",
        extra={"subtree": "config/shelly"},
    )

    assert descriptor["extra"] == {"subtree": "config/shelly"}


def test_descriptor_line_roundtrips_through_parse():
    descriptor = cd.build_conflict_descriptor(
        kind="projection-conflict",
        domain="my-consumer-repo",
        repo="owner/my-consumer-repo",
        pr=42,
        branch="projection-reflect/instructions",
        base="main",
        label="projection-conflict",
    )
    line = f"some log noise {cd.descriptor_line(descriptor)}"

    parsed = cd.parse_descriptor_line(line, kind="projection-conflict")

    assert parsed == descriptor


def test_parse_descriptor_line_rejects_non_marker_lines():
    assert cd.parse_descriptor_line("just an ordinary log line", kind="x") is None


def test_parse_descriptor_line_rejects_wrong_kind():
    descriptor = cd.build_conflict_descriptor(
        kind="config-conflict",
        domain="shelly",
        repo="owner/repo",
        pr=7,
        branch="reflect/shelly",
        base="master",
        label="config-conflict",
    )
    line = cd.descriptor_line(descriptor)

    # A shared log stream may carry another producer's descriptor kind --
    # a caller scanning for its own kind must not pick up someone else's.
    assert cd.parse_descriptor_line(line, kind="projection-conflict") is None


def test_parse_descriptor_line_rejects_malformed_json():
    line = f"{cd.DESCRIPTOR_MARKER} not-json-at-all"
    assert cd.parse_descriptor_line(line, kind="x") is None


def test_build_dispatch_reuses_conflict_resolution_recipe():
    dispatch = cd.build_dispatch(
        kind="projection-conflict",
        domain="my-consumer-repo",
        label="projection-conflict",
        repo="owner/my-consumer-repo",
        pr=42,
        branch="projection-reflect/instructions",
        base="main",
        reconciler_agent="projection-reconciler",
    )

    assert dispatch.descriptor["dedup_key"] == "projection-conflict:my-consumer-repo"
    argv = dispatch.argv
    assert argv[0:2] == ("agent-dispatch", "create")
    assert "--label" in argv and "projection-conflict" in argv
    assert "--dedup-key" in argv
    assert "--repo" in argv and "owner/my-consumer-repo" in argv
    assert "--async" in argv

    prompt_index = argv.index("--prompt") + 1
    prompt = argv[prompt_index]
    # The shared recipe's own safety clauses ride along (proves reuse, not
    # a hand-authored duplicate).
    assert "resolved state" in prompt
    assert "owner/my-consumer-repo#42" in prompt
    # The domain-specific delegation this generic recipe does not know about.
    assert "projection-reconciler" in prompt

    goal_index = argv.index("--goal") + 1
    assert "owner/my-consumer-repo#42" in argv[goal_index]

    done_index = argv.index("--done-criteria") + 1
    assert argv[done_index]  # non-empty, reused from the recipe

    payload_index = argv.index("--payload-inline") + 1
    payload = json.loads(argv[payload_index])
    assert payload == dispatch.descriptor


def test_build_dispatch_includes_target_machine_when_given():
    dispatch = cd.build_dispatch(
        kind="config-conflict",
        domain="shelly",
        label="config-conflict",
        repo="owner/repo",
        pr=7,
        branch="reflect/shelly",
        base="master",
        reconciler_agent="config-reconciler",
        target_machine="wheatley",
    )

    assert dispatch.argv[-2:] == ("--target-machine", "wheatley")


def test_build_dispatch_omits_target_machine_by_default():
    dispatch = cd.build_dispatch(
        kind="config-conflict",
        domain="shelly",
        label="config-conflict",
        repo="owner/repo",
        pr=7,
        branch="reflect/shelly",
        base="master",
        reconciler_agent="config-reconciler",
    )

    assert "--target-machine" not in dispatch.argv


def test_different_domains_same_label_get_distinct_dedup_keys():
    a = cd.build_dispatch(
        kind="projection-conflict",
        domain="repo-a",
        label="projection-conflict",
        repo="owner/repo-a",
        pr=1,
        branch="b",
        base="main",
        reconciler_agent="projection-reconciler",
    )
    b = cd.build_dispatch(
        kind="projection-conflict",
        domain="repo-b",
        label="projection-conflict",
        repo="owner/repo-b",
        pr=2,
        branch="b",
        base="main",
        reconciler_agent="projection-reconciler",
    )

    assert a.descriptor["dedup_key"] != b.descriptor["dedup_key"]
