"""Tests for registrar discovery -- pointer registry + declaration aggregation (Phase 2)."""

from __future__ import annotations

import json

import pytest

from agent_dispatch.registrar import RegistrarError
from agent_dispatch.registrar_discovery import (
    INREPO_SUBDIR,
    Pointer,
    add_pointer,
    discover,
    discover_repo,
    discover_with_legacy,
    load_pointers,
    read_declaration_file,
    read_legacy_env_profiles,
    read_location,
    remove_pointer,
    repo_pointer,
    save_pointers,
)

# -- Pointer model -----------------------------------------------------------

def test_pointer_roundtrip():
    p = Pointer(name="general", location="/srv/decls", kind="dir", owner="ops")
    assert Pointer.from_dict(p.to_dict()) == p


def test_pointer_to_dict_omits_absent_owner():
    p = Pointer(name="general", location="/srv/decls")
    assert "owner" not in p.to_dict()


def test_pointer_from_dict_requires_name_and_location():
    with pytest.raises(RegistrarError, match="name"):
        Pointer.from_dict({"location": "/x"})
    with pytest.raises(RegistrarError, match="location"):
        Pointer.from_dict({"name": "x"})


def test_pointer_bad_kind_rejected():
    with pytest.raises(RegistrarError, match=r"pointer\.kind"):
        Pointer.from_dict({"name": "x", "location": "/x", "kind": "symlink"})


def test_repo_pointer_resolves_into_inrepo_subdir(tmp_path):
    p = repo_pointer(tmp_path)
    assert p.kind == "repo"
    assert p.resolved_location() == tmp_path / INREPO_SUBDIR


def test_dir_pointer_resolves_to_location(tmp_path):
    p = Pointer(name="d", location=str(tmp_path), kind="dir")
    assert p.resolved_location() == tmp_path


def test_effective_owner_derivation(tmp_path):
    assert Pointer(name="d", location=str(tmp_path)).effective_owner() == "pointer:d"
    assert repo_pointer(tmp_path / "myrepo").effective_owner() == "repo:myrepo"
    assert Pointer(name="d", location="/x", owner="explicit").effective_owner() == "explicit"


# -- pointer-registry persistence --------------------------------------------

def test_pointers_registry_empty_when_absent(tmp_path):
    assert load_pointers(tmp_path) == []


def test_add_load_remove_pointer(tmp_path):
    add_pointer("general", tmp_path / "decls", base=tmp_path)
    pts = load_pointers(tmp_path)
    assert [p.name for p in pts] == ["general"]
    assert remove_pointer("general", tmp_path) is True
    assert load_pointers(tmp_path) == []
    assert remove_pointer("general", tmp_path) is False


def test_add_pointer_replaces_same_name(tmp_path):
    add_pointer("general", tmp_path / "a", base=tmp_path)
    add_pointer("general", tmp_path / "b", base=tmp_path)
    pts = load_pointers(tmp_path)
    assert len(pts) == 1
    assert pts[0].location.endswith("b")


def test_add_pointer_bad_name_rejected(tmp_path):
    with pytest.raises(RegistrarError, match="pointer name"):
        add_pointer("bad name", tmp_path, base=tmp_path)


def test_add_pointer_identical_is_noop(tmp_path):
    add_pointer("general", tmp_path / "decls", base=tmp_path)
    mtime1 = (tmp_path / "pointers.json").stat().st_mtime_ns
    add_pointer("general", tmp_path / "decls", base=tmp_path)  # identical
    mtime2 = (tmp_path / "pointers.json").stat().st_mtime_ns
    assert mtime1 == mtime2  # file not rewritten


def test_corrupt_entry_names_its_index(tmp_path):
    (tmp_path / "pointers.json").write_text(
        json.dumps([{"name": "ok", "location": "/x"}, {"name": "bad"}]), encoding="utf-8"
    )
    with pytest.raises(RegistrarError, match=r"pointers\.json\[1\]"):
        load_pointers(tmp_path)


def test_save_pointers_is_atomic_json_list(tmp_path):
    save_pointers([Pointer(name="x", location="/x")], tmp_path)
    raw = json.loads((tmp_path / "pointers.json").read_text(encoding="utf-8"))
    assert raw == [{"name": "x", "location": "/x", "kind": "dir"}]


def test_corrupt_registry_raises(tmp_path):
    (tmp_path / "pointers.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(RegistrarError, match="invalid pointer registry"):
        load_pointers(tmp_path)


# -- reading declaration documents -------------------------------------------

def test_read_declaration_file_json(tmp_path):
    f = tmp_path / "general.json"
    f.write_text(json.dumps({"name": "general", "labels": ["general"]}), encoding="utf-8")
    decl = read_declaration_file(f)
    assert decl.name == "general"
    assert decl.labels == ("general",)


def test_relative_emitter_cwd_resolves_from_declaration_directory(tmp_path):
    root = tmp_path / "repo"
    registrar = root / ".agent-dispatch" / "registrar"
    registrar.mkdir(parents=True)
    path = registrar / "reviews.json"
    path.write_text(
        json.dumps(
            {
                "name": "reviews",
                "kind": "emitter",
                "spec": {
                    "id": "reviews",
                    "command": ["python", "tools/reviews.py"],
                    "interval_seconds": 60,
                    "cwd": "../..",
                },
            }
        ),
        encoding="utf-8",
    )
    decl = read_declaration_file(path)
    assert decl.spec["cwd"] == str(root.resolve())


@pytest.mark.parametrize("cwd", ["/srv/repo", "C:\\src\\repo"])
def test_cross_platform_absolute_emitter_cwd_is_not_rebased(tmp_path, cwd):
    path = tmp_path / "reviews.json"
    path.write_text(
        json.dumps(
            {
                "name": "reviews",
                "kind": "emitter",
                "spec": {
                    "id": "reviews",
                    "command": ["reviewer"],
                    "interval_seconds": 60,
                    "cwd": cwd,
                },
            }
        ),
        encoding="utf-8",
    )
    assert read_declaration_file(path).spec["cwd"] == cwd


def test_read_declaration_file_yaml(tmp_path):
    f = tmp_path / "general.yaml"
    f.write_text("name: general\nlabels: [general]\nconcurrency: 2\n", encoding="utf-8")
    decl = read_declaration_file(f)
    assert decl.name == "general"
    assert decl.concurrency == 2


def test_read_declaration_file_unknown_suffix(tmp_path):
    f = tmp_path / "general.txt"
    f.write_text("name: general", encoding="utf-8")
    with pytest.raises(RegistrarError, match="unrecognized declaration suffix"):
        read_declaration_file(f)


def test_read_declaration_file_non_mapping(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text(json.dumps(["not", "a", "mapping"]), encoding="utf-8")
    with pytest.raises(RegistrarError, match="must be a mapping"):
        read_declaration_file(f)


def test_read_location_scans_sorted_and_stamps_owner(tmp_path):
    (tmp_path / "b.json").write_text(json.dumps({"name": "b"}), encoding="utf-8")
    (tmp_path / "a.yaml").write_text("name: a\n", encoding="utf-8")
    decls = read_location(tmp_path, owner="repo:demo")
    assert [d.name for d in decls] == ["a", "b"]  # filename-sorted
    assert all(d.owner == "repo:demo" for d in decls)


def test_read_location_missing_dir_is_empty(tmp_path):
    assert read_location(tmp_path / "nope") == []


def test_read_location_explicit_owner_wins_over_stamp(tmp_path):
    (tmp_path / "a.json").write_text(
        json.dumps({"name": "a", "owner": "declared"}), encoding="utf-8"
    )
    (decl,) = read_location(tmp_path, owner="pointer:x")
    assert decl.owner == "declared"


# -- aggregation -------------------------------------------------------------

def test_discover_aggregates_across_pointers(tmp_path):
    d1 = tmp_path / "one"
    d2 = tmp_path / "two"
    d1.mkdir()
    d2.mkdir()
    (d1 / "general.json").write_text(json.dumps({"name": "general"}), encoding="utf-8")
    (d2 / "review.yaml").write_text("name: review\n", encoding="utf-8")
    decls = discover([
        Pointer(name="one", location=str(d1)),
        Pointer(name="two", location=str(d2)),
    ])
    assert [d.name for d in decls] == ["general", "review"]


def test_discover_rejects_duplicate_names(tmp_path):
    d1 = tmp_path / "one"
    d2 = tmp_path / "two"
    d1.mkdir()
    d2.mkdir()
    (d1 / "general.json").write_text(json.dumps({"name": "general"}), encoding="utf-8")
    (d2 / "general.json").write_text(json.dumps({"name": "general"}), encoding="utf-8")
    with pytest.raises(RegistrarError, match="duplicate profile name"):
        discover([Pointer(name="one", location=str(d1)), Pointer(name="two", location=str(d2))])


def test_discover_uses_persisted_registry(tmp_path):
    decls_dir = tmp_path / "decls"
    decls_dir.mkdir()
    (decls_dir / "general.json").write_text(json.dumps({"name": "general"}), encoding="utf-8")
    add_pointer("general", decls_dir, base=tmp_path)
    decls = discover(base=tmp_path)
    assert [d.name for d in decls] == ["general"]


def test_discover_repo_reads_inrepo_dir(tmp_path):
    reg = tmp_path / INREPO_SUBDIR
    reg.mkdir(parents=True)
    (reg / "general.yaml").write_text("name: general\nlabels: [general]\n", encoding="utf-8")
    decls = discover_repo(tmp_path)
    assert [d.name for d in decls] == ["general"]
    assert decls[0].owner == f"repo:{tmp_path.name}"


# -- Legacy env-profile back-compat bridge (Phase 4) -------------------------


def _write_env(path, **vars) -> None:
    path.write_text(
        "\n".join(f"AGENT_DISPATCH_SUPERVISE_{k}={v}" for k, v in vars.items()) + "\n",
        encoding="utf-8",
    )


def test_read_legacy_env_profiles_primary_and_dir(tmp_path):
    base = tmp_path
    _write_env(base / "supervisor.env", LABELS="general", MAX_CONCURRENT="2")
    profiles = base / "supervisors"
    profiles.mkdir()
    _write_env(profiles / "review.env", LABELS="code-review", HEADLESS_AGENT="reviewer")
    decls = read_legacy_env_profiles(
        env_file=base / "supervisor.env", profile_dir=profiles
    )
    by_name = {d.name: d for d in decls}
    assert set(by_name) == {"supervisor", "review"}
    assert by_name["supervisor"].labels == ("general",)
    assert by_name["supervisor"].concurrency == 2
    assert by_name["supervisor"].owner == "legacy-env:supervisor"
    assert by_name["review"].labels == ("code-review",)
    assert by_name["review"].body.agent == "reviewer"


def test_read_legacy_env_profiles_skips_labelless(tmp_path):
    # An empty/label-less primary is inert (label-gated installer) -> skipped.
    (tmp_path / "supervisor.env").write_text(
        "AGENT_DISPATCH_SUPERVISE_LABELS=\nAGENT_DISPATCH_SUPERVISE_INTERVAL=30\n",
        encoding="utf-8",
    )
    assert read_legacy_env_profiles(
        env_file=tmp_path / "supervisor.env", profile_dir=tmp_path / "none"
    ) == []


def test_read_legacy_env_profiles_missing_paths(tmp_path):
    assert read_legacy_env_profiles(
        env_file=tmp_path / "nope.env", profile_dir=tmp_path / "gone"
    ) == []


def test_discover_with_legacy_declaration_wins(tmp_path, monkeypatch):
    # A pointer declares 'general'; a legacy env profile of the SAME name (stem)
    # 'general' plus a distinct 'review' legacy profile. The declaration wins for
    # 'general'; the distinct legacy 'review' is included.
    decls_dir = tmp_path / "decls"
    decls_dir.mkdir()
    (decls_dir / "general.yaml").write_text(
        "name: general\nlabels: [general]\nconcurrency: 5\n", encoding="utf-8"
    )
    reg_base = tmp_path / "reg"
    add_pointer("ops", str(decls_dir), owner="ops", base=reg_base)

    legacy = tmp_path / "install"
    (legacy / "supervisors").mkdir(parents=True)
    # stem 'general' collides with the declaration; stem 'review' is distinct.
    _write_env(legacy / "supervisors" / "general.env", LABELS="general", MAX_CONCURRENT="1")
    _write_env(legacy / "supervisors" / "review.env", LABELS="code-review")

    out = discover_with_legacy(
        base=reg_base,
        env_file=legacy / "none.env",
        profile_dir=legacy / "supervisors",
    )
    by_name = {d.name: d for d in out}
    assert set(by_name) == {"general", "review"}
    # declaration (concurrency 5) wins over the legacy env 'general' (concurrency 1)
    assert by_name["general"].concurrency == 5
    assert by_name["review"].labels == ("code-review",)
