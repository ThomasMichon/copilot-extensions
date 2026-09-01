from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_machines.manifest import load_package
from agent_machines.surfaces import apply_surfaces, collect_contributions, settings
from agent_machines.surfaces._common import (
    SurfaceStateError,
    merge_enforce,
    merge_floor,
)

from ._helpers import base_package, write_package


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    # Redirect ~ so backup-before-write lands in the tmp tree, never the real home.
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def _settings(tmp_path: Path) -> Path:
    home = tmp_path / ".copilot"
    home.mkdir(parents=True, exist_ok=True)
    return home


def test_merge_floor_preserves_live_and_adds_missing():
    live = {"enabledPlugins": {"a": True}}
    manifest = {"enabledPlugins": {"a": False, "b": True}}
    out = merge_floor(live, manifest)
    assert out["enabledPlugins"] == {"a": True, "b": True}  # 'a' not clobbered, 'b' added


def test_merge_enforce_overwrites():
    assert merge_enforce({"model": "x"}, {"model": "y"}) == {"model": "y"}


def test_apply_enforce_writes_scalars(tmp_path):
    home = _settings(tmp_path)
    contribs = [("enforce", {"model": "opus", "effortLevel": "high"})]
    result = settings.apply(contribs, home=home, dry_run=False)
    assert result.changed
    data = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert data["model"] == "opus"
    assert data["effortLevel"] == "high"


def test_ensure_present_unions_without_clobber(tmp_path):
    home = _settings(tmp_path)
    (home / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"existing@m": True}}), encoding="utf-8"
    )
    contribs = [("ensure-present", {"enabledPlugins": {"new@m": True}})]
    settings.apply(contribs, home=home, dry_run=False)
    data = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert data["enabledPlugins"] == {"existing@m": True, "new@m": True}


def test_enabled_plugin_false_is_an_authoritative_tombstone(tmp_path):
    home = _settings(tmp_path)
    (home / "settings.json").write_text(
        json.dumps(
            {
                "enabledPlugins": {
                    "legacy@m": True,
                    "operator-disabled@m": False,
                    "operator-extra@m": True,
                    "invalid-null@m": None,
                }
            }
        ),
        encoding="utf-8",
    )
    contribs = [
        (
            "ensure-present",
            {
                "enabledPlugins": {
                    "legacy@m": False,
                    "operator-disabled@m": True,
                    "new@m": True,
                    "invalid-null@m": True,
                }
            },
        )
    ]

    settings.apply(contribs, home=home, dry_run=False)

    data = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert data["enabledPlugins"] == {
        "legacy@m": False,
        "operator-disabled@m": False,
        "operator-extra@m": True,
        "invalid-null@m": True,
        "new@m": True,
    }


@pytest.mark.parametrize("tombstone_first", [False, True])
def test_enabled_plugin_tombstone_wins_across_package_order(
    tmp_path, tombstone_first: bool,
):
    home = _settings(tmp_path)
    contributions = [
        ("ensure-present", {"enabledPlugins": {"legacy@m": True}}),
        ("ensure-present", {"enabledPlugins": {"legacy@m": False}}),
    ]
    if tombstone_first:
        contributions.reverse()

    settings.apply(contributions, home=home, dry_run=False)

    data = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert data["enabledPlugins"]["legacy@m"] is False


def test_dry_run_does_not_write(tmp_path):
    home = _settings(tmp_path)
    result = settings.apply([("enforce", {"model": "opus"})], home=home, dry_run=True)
    assert result.changed  # would change
    assert not (home / "settings.json").exists()


def test_idempotent_second_apply_no_change(tmp_path):
    home = _settings(tmp_path)
    contribs = [("enforce", {"model": "opus"})]
    settings.apply(contribs, home=home, dry_run=False)
    result2 = settings.apply(contribs, home=home, dry_run=False)
    assert not result2.changed


def test_ensure_absent_dry_run_apply_backup_and_idempotency(tmp_path):
    home = _settings(tmp_path)
    path = home / "settings.json"
    original = {
        "enabledPlugins": {
            "remove@m": True,
            "false@m": False,
            "keep@m": True,
        },
        "unrelated": {"keep": True},
    }
    path.write_text(json.dumps(original), encoding="utf-8")
    contributions = [
        (
            "ensure-absent",
            {"enabledPlugins": ["remove@m", "false@m", "missing@m"]},
            "example/activation",
        )
    ]

    preview = settings.apply(contributions, home=home, dry_run=True)
    assert preview.changed
    assert json.loads(path.read_text(encoding="utf-8")) == original
    assert preview.changes == [
        {
            "op": "remove",
            "key": "enabledPlugins",
            "items": ["false@m"],
            "contributors": ["example/activation"],
        },
        {
            "op": "remove",
            "key": "enabledPlugins",
            "items": ["remove@m"],
            "contributors": ["example/activation"],
        },
    ]

    applied = settings.apply(contributions, home=home, dry_run=False)
    assert applied.backup_path is not None
    assert Path(applied.backup_path).exists()
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "enabledPlugins": {"keep@m": True},
        "unrelated": {"keep": True},
    }
    second = settings.apply(contributions, home=home, dry_run=False)
    assert not second.changed
    assert second.backup_path is None


@pytest.mark.parametrize("content", ["{bad", "[]", '{"enabledPlugins": []}'])
def test_ensure_absent_fails_closed_on_malformed_settings(tmp_path, content):
    home = _settings(tmp_path)
    (home / "settings.json").write_text(content, encoding="utf-8")
    with pytest.raises(SurfaceStateError):
        settings.apply(
            [("ensure-absent", {"enabledPlugins": ["optional@m"]})],
            home=home,
            dry_run=True,
        )


def test_ensure_absent_rejects_duplicate_json_keys(tmp_path):
    home = _settings(tmp_path)
    (home / "settings.json").write_text(
        '{"unrelated":1,"unrelated":2,"enabledPlugins":{"optional@m":true}}',
        encoding="utf-8",
    )
    with pytest.raises(SurfaceStateError, match="duplicate JSON key"):
        settings.apply(
            [("ensure-absent", {"enabledPlugins": ["optional@m"]})],
            home=home,
            dry_run=True,
        )


def test_ensure_absent_reports_other_plugin_changes(tmp_path):
    home = _settings(tmp_path)
    (home / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"remove@m": True}}),
        encoding="utf-8",
    )
    result = settings.apply(
        [
            ("ensure-present", {"enabledPlugins": {"add@m": True}}),
            ("ensure-absent", {"enabledPlugins": ["remove@m"]}, "example/remove"),
        ],
        home=home,
        dry_run=True,
    )
    assert {
        "key": "enabledPlugins.add@m",
        "before": None,
        "after": True,
    } in result.changes
    assert {
        "op": "remove",
        "key": "enabledPlugins",
        "items": ["remove@m"],
        "contributors": ["example/remove"],
    } in result.changes


def test_backup_created_on_change(tmp_path):
    home = _settings(tmp_path)
    (home / "settings.json").write_text('{"model": "old"}', encoding="utf-8")
    result = settings.apply([("enforce", {"model": "new"})], home=home, dry_run=False)
    assert result.backup_path is not None
    assert Path(result.backup_path).exists()


def test_apply_surfaces_dispatch_all_three(tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    data = base_package("a/x", gate=["*"])
    data["manage"] = {
        "copilot.settings": {"disposition": "enforce", "values": {"model": "opus"}},
        "copilot.permissions": {
            "disposition": "ensure-present",
            "by-location-class": [
                {"match": "$REPO(acme)", "tool_approvals": [{"kind": "commands"}]}
            ],
        },
        "copilot.trustedFolders": {
            "disposition": "ensure-present",
            "by-location-class": ["$REPO(acme)"],
        },
    }
    pkg = load_package(write_package(repo, "p.yaml", data), source_repo="acme")
    results = apply_surfaces([pkg], home=_settings(tmp_path), dry_run=True)
    surfaces = {r.surface for r in results}
    assert surfaces == {"copilot.settings", "copilot.permissions", "copilot.trustedFolders"}


def test_only_filter_scopes_surfaces(tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    data = base_package("a/x", gate=["*"])
    data["manage"] = {
        "copilot.settings": {"disposition": "enforce", "values": {"model": "opus"}},
        "copilot.trustedFolders": {"disposition": "ensure-present",
                                   "by-location-class": ["$REPO(acme)"]},
    }
    pkg = load_package(write_package(repo, "p.yaml", data), source_repo="acme")
    results = apply_surfaces([pkg], home=_settings(tmp_path), dry_run=True, only=["settings"])
    assert {r.surface for r in results} == {"copilot.settings"}


def test_trusted_folders_union_preserves_other_config(tmp_path):
    from agent_machines.surfaces import trusted_folders
    repo = tmp_path / "acme"
    repo.mkdir()
    home = _settings(tmp_path)
    (home / "config.json").write_text(
        json.dumps({"trustedFolders": ["/old"], "expAssignmentsCache": {"x": 1}}), encoding="utf-8"
    )
    spec = [{"disposition": "ensure-present", "by-location-class": ["$REPO(acme)"]}]
    trusted_folders.apply(spec, {"acme": repo}, home=home, dry_run=False)
    cfg = json.loads((home / "config.json").read_text())
    assert "/old" in cfg["trustedFolders"]
    assert str(repo) in cfg["trustedFolders"]
    assert cfg["expAssignmentsCache"] == {"x": 1}  # machine-junk untouched (allowlist)


def test_permissions_union_no_duplicate(tmp_path):
    from agent_machines.surfaces import permissions
    repo = tmp_path / "acme"
    repo.mkdir()
    home = _settings(tmp_path)
    approval = {"kind": "commands", "commandIdentifiers": ["git"]}
    (home / "permissions-config.json").write_text(
        json.dumps({"locations": {str(repo): {"tool_approvals": [approval]}}}), encoding="utf-8"
    )
    spec = [{"disposition": "ensure-present",
             "by-location-class": [{"match": "$REPO(acme)",
                                    "tool_approvals": [approval, {"kind": "write"}]}]}]
    result = permissions.apply(spec, {"acme": repo}, home=home, dry_run=False)
    data = json.loads((home / "permissions-config.json").read_text())
    approvals = data["locations"][str(repo)]["tool_approvals"]
    assert approval in approvals and {"kind": "write"} in approvals
    assert len(approvals) == 2  # existing not duplicated
    assert result.changed


def test_settings_diff_records_before_after(tmp_path):
    home = _settings(tmp_path)
    (home / "settings.json").write_text('{"model": "stale"}', encoding="utf-8")
    result = settings.apply([("enforce", {"model": "opus"})], home=home, dry_run=True)
    change = next(c for c in result.changes if c["key"] == "model")
    assert change["before"] == "stale"
    assert change["after"] == "opus"


def test_settings_enforce_preserves_unmanaged_keys(tmp_path):
    # Restore enforces ONLY the declared managed keys; every unmanaged key in
    # settings.json passes through untouched -- the surface merges a small subset,
    # it never rewrites/replaces the whole file. Guards the operator invariant
    # ("enforce a small subset we care about, not put back the whole settings.json")
    # and the subagents over-management regression: a stray subagents.agents.*
    # block is neither added nor removed here (it is simply not a managed key).
    home = _settings(tmp_path)
    (home / "settings.json").write_text(
        json.dumps(
            {
                "model": "stale",
                "logLevel": "all",
                "footer": {"showBranch": True},
                "subagents": {"agents": {"gitea": {"model": "claude-opus-4.8"}}},
            }
        ),
        encoding="utf-8",
    )
    settings.apply([("enforce", {"model": "opus"})], home=home, dry_run=False)
    data = json.loads((home / "settings.json").read_text())
    assert data["model"] == "opus"  # managed key enforced
    assert data["logLevel"] == "all"  # unmanaged scalar preserved
    assert data["footer"] == {"showBranch": True}  # unmanaged object preserved
    # stray subagents block untouched -- not removed, not modified
    assert data["subagents"] == {"agents": {"gitea": {"model": "claude-opus-4.8"}}}
    # nothing added or removed beyond the single managed key
    assert set(data) == {"model", "logLevel", "footer", "subagents"}


def test_collect_contributions_prefix_scoping(tmp_path):
    data = base_package("a/x", gate=["*"])
    data["manage"] = {
        "copilot.settings": {"disposition": "enforce", "values": {"model": "opus"}},
        "copilot.settings.plugins": {
            "disposition": "ensure-present",
            "values": {"enabledPlugins": {}},
        },
        "copilot.permissions": {"disposition": "ensure-present", "values": {"x": 1}},
    }
    pkg = load_package(write_package(tmp_path / "a", "p.yaml", data), source_repo="a")
    got = collect_contributions([pkg], "copilot.settings")
    assert len(got) == 2  # settings + settings.plugins, not permissions


def test_plugin_tombstone_group_preserves_operator_extras(tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    data = base_package(schema_version=1)
    data["manage"]["copilot.settings.plugin-tombstones"] = {
        "disposition": "enforce",
        "values": {"enabledPlugins": {"legacy@m": False}},
    }
    package = load_package(
        write_package(repo, "p.yaml", data),
        source_repo="acme",
    )
    home = _settings(tmp_path)
    (home / "settings.json").write_text(
        json.dumps(
            {
                "enabledPlugins": {
                    "legacy@m": True,
                    "operator-extra@m": True,
                }
            }
        ),
        encoding="utf-8",
    )

    contributions = collect_contributions([package], "copilot.settings")
    settings.apply(contributions, home=home, dry_run=False)

    restored = json.loads(
        (home / "settings.json").read_text(encoding="utf-8")
    )
    assert restored["enabledPlugins"] == {
        "legacy@m": False,
        "operator-extra@m": True,
    }
