"""Tests for single-install multi-tenant orchestration (agent_logger.tenancy)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_logger import tenancy
from agent_logger.config import load_config

# --------------------------------------------------------------------------- #
# fixtures / helpers                                                            #
# --------------------------------------------------------------------------- #


def _make_session(source: Path, name: str, git_root: str) -> None:
    sess = source / "session-state" / name
    sess.mkdir(parents=True)
    (sess / "events.jsonl").write_text('{"ts": 1}\n', encoding="utf-8")
    (sess / "workspace.yaml").write_text(
        f"id: {name}\ngit_root: {git_root}\n", encoding="utf-8"
    )


def _make_copilot(root: Path) -> Path:
    """A fake ~/.copilot with one session per repo family."""
    src = root / "copilot"
    _make_session(src, "sess-apl", "/work/aperture-labs/wt")
    _make_session(src, "sess-cext", "/work/copilot-extensions/wt")
    _make_session(src, "sess-dot", "/work/dotfiles/wt")
    _make_session(src, "sess-work", "/work/acme-webapp/wt")
    return src


HARNESS = ["aperture-labs", "copilot-extensions", "dotfiles", "acme-webapp"]


def _write_tenant_repo(repo: Path, block: dict) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".agent-logger.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "tenant": block}), encoding="utf-8"
    )


def _write_registry(aw_home: Path, src_parent: Path, names: list[str]) -> None:
    aw_home.mkdir(parents=True, exist_ok=True)
    (aw_home / "projects.yaml").write_text(
        yaml.safe_dump({"projects": {n: {} for n in names}}), encoding="utf-8"
    )
    (aw_home / "repos.yaml").write_text(
        yaml.safe_dump(
            {
                "srcroot": {tenancy.current_platform(): str(src_parent)},
                "repos": {n: {} for n in names},
            }
        ),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# registry reader                                                              #
# --------------------------------------------------------------------------- #


def test_adopted_repo_paths_absent_registry_is_empty(tmp_path):
    assert tenancy.adopted_repo_paths(aw_home=tmp_path / "nope") == []


def test_adopted_repo_paths_resolves_srcroot(tmp_path):
    src = tmp_path / "src"
    (src / "aperture-labs").mkdir(parents=True)
    (src / "dotfiles").mkdir(parents=True)
    _write_registry(tmp_path / ".aw", src, ["aperture-labs", "dotfiles"])
    resolved = dict(tenancy.adopted_repo_paths(aw_home=tmp_path / ".aw"))
    assert resolved["aperture-labs"] == src / "aperture-labs"
    assert resolved["dotfiles"] == src / "dotfiles"


# --------------------------------------------------------------------------- #
# tenant block parsing                                                         #
# --------------------------------------------------------------------------- #


def test_parse_tenant_block_absent_is_none():
    assert tenancy.parse_tenant_block(
        {"schema_version": 1, "log": {}}, source="x", default_id="r"
    ) is None


def test_parse_tenant_block_defaults_id_and_roles():
    block = tenancy.parse_tenant_block(
        {"tenant": {}}, source="x", default_id="aperture-labs"
    )
    assert block["id"] == "aperture-labs"
    assert block["roles"] == ["source"]
    assert block["enabled"] is True


def test_parse_tenant_block_rejects_unknown_field():
    with pytest.raises(tenancy.TenantConfigError):
        tenancy.parse_tenant_block(
            {"tenant": {"bogus": 1}}, source="x", default_id="r"
        )


def test_parse_tenant_block_rejects_unknown_role():
    with pytest.raises(tenancy.TenantConfigError):
        tenancy.parse_tenant_block(
            {"tenant": {"roles": ["source", "wat"]}}, source="x", default_id="r"
        )


def test_parse_tenant_block_future_schema_is_tolerant():
    """A tenant block from a newer schema is read tolerantly: unknown fields,
    unknown per-machine keys, and roles this build can't serve are dropped, not
    fatal."""
    block = tenancy.parse_tenant_block(
        {
            "schema_version": 99,
            "tenant": {
                "id": "aperture-labs",
                "roles": ["source", "index"],  # 'index' is a future role
                "future_field": {"x": 1},
                "machines": {"book2": {"future_key": 1, "sync": {}}},
            },
        },
        source="x",
        default_id="aperture-labs",
    )
    assert block["roles"] == ["source"]  # future role dropped, known kept
    assert block["future_schema"] is True


def test_parse_tenant_block_future_schema_all_roles_unknown_is_inert():
    block = tenancy.parse_tenant_block(
        {"schema_version": 99, "tenant": {"id": "r", "roles": ["index"]}},
        source="x",
        default_id="r",
    )
    assert block["roles"] == []  # inert here; a newer build would serve it


def test_parse_tenant_block_rejects_path_injecting_id():
    for bad in ("../evil", "a/b", "a\\b", ".", ".."):
        with pytest.raises(tenancy.TenantConfigError):
            tenancy.parse_tenant_block(
                {"tenant": {"id": bad}}, source="x", default_id="r"
            )


@pytest.mark.parametrize("bad", [False, "99", 0, -1, 1.5])
def test_parse_tenant_block_rejects_bad_schema_version(bad):
    with pytest.raises(tenancy.TenantConfigError):
        tenancy.parse_tenant_block(
            {"schema_version": bad, "tenant": {"id": "r"}}, source="x", default_id="r"
        )


def test_parse_tenant_block_rejects_non_bool_machine_enabled():
    with pytest.raises(tenancy.TenantConfigError):
        tenancy.parse_tenant_block(
            {"tenant": {"id": "r", "machines": {"book2": {"enabled": "false"}}}},
            source="x",
            default_id="r",
        )


def test_parse_tenant_block_rejects_unknown_machine_role():
    with pytest.raises(tenancy.TenantConfigError):
        tenancy.parse_tenant_block(
            {"tenant": {"id": "r", "machines": {"book2": {"roles": ["wat"]}}}},
            source="x",
            default_id="r",
        )


@pytest.mark.parametrize(
    "machine,key,expected",
    [
        ("book2", "book2", True),
        ("book2-wsl", "book2", True),
        ("book2", "lambda-core", False),
        ("book2extra", "book2", False),  # not a "-" boundary
    ],
)
def test_machine_matches(machine, key, expected):
    assert tenancy.machine_matches(machine, key) is expected


# --------------------------------------------------------------------------- #
# per-machine resolution                                                       #
# --------------------------------------------------------------------------- #


def test_resolve_tenant_applies_machine_override_and_lock_name(tmp_path):
    block = tenancy.parse_tenant_block(
        {
            "tenant": {
                "id": "aperture-labs",
                "roles": ["source"],
                "sync": {"harness_repos": HARNESS},
                "machines": {
                    "book2": {
                        "sync": {
                            "repo_allowlist": ["aperture-labs", "copilot-extensions"],
                            "repo_allowlist_fail_closed": True,
                        }
                    }
                },
            }
        },
        source="x",
        default_id="aperture-labs",
    )
    resolved = tenancy.resolve_tenant(
        block,
        repo_name="aperture-labs",
        repo_path=tmp_path / "repo",
        config_path=tmp_path / "repo" / ".agent-logger.yaml",
        machine="book2",
        home=tmp_path / "home",
    )
    cfg = resolved.config
    assert cfg.sync_repo_allowlist == ["aperture-labs", "copilot-extensions"]
    assert cfg.sync_repo_allowlist_fail_closed is True
    assert cfg.sync_lock_name == "session-sync.lock"  # shared source lock
    # A non-book2 machine takes the unfiltered base scope.
    other = tenancy.resolve_tenant(
        block,
        repo_name="aperture-labs",
        repo_path=tmp_path / "repo",
        config_path=tmp_path / "repo" / ".agent-logger.yaml",
        machine="lambda-core",
        home=tmp_path / "home",
    )
    assert other.config.sync_repo_allowlist == []
    assert other.config.sync_repo_allowlist_fail_closed is False


def test_resolve_tenant_layers_machine_local_supplement(tmp_path):
    home = tmp_path / "home"
    (home / tenancy.TENANT_SUPPLEMENT_DIR).mkdir(parents=True)
    (home / tenancy.TENANT_SUPPLEMENT_DIR / "aperture-labs.yaml").write_text(
        yaml.safe_dump(
            {"sync": {"target": "onedrive", "notify": {"url": "https://secret/hook"}}}
        ),
        encoding="utf-8",
    )
    block = tenancy.parse_tenant_block(
        {"tenant": {"id": "aperture-labs"}}, source="x", default_id="aperture-labs"
    )
    resolved = tenancy.resolve_tenant(
        block,
        repo_name="aperture-labs",
        repo_path=tmp_path / "repo",
        config_path=tmp_path / "repo" / ".agent-logger.yaml",
        machine="book2",
        home=home,
    )
    assert resolved.config.sync_target == "onedrive"
    assert resolved.config.sync_notify["url"] == "https://secret/hook"


# --------------------------------------------------------------------------- #
# discovery                                                                    #
# --------------------------------------------------------------------------- #


def _standing_tenants(tmp_path) -> tuple[Path, Path, Path]:
    """Build the book2 aperture-labs + dotfiles standing tenants + a fake home."""
    src = tmp_path / "src"
    copilot = _make_copilot(tmp_path)
    home = tmp_path / "home"

    _write_tenant_repo(
        src / "aperture-labs",
        {
            "id": "aperture-labs",
            "roles": ["source"],
            "sync": {"harness_repos": HARNESS},
            "machines": {
                "book2": {
                    "sync": {
                        "repo_allowlist": ["aperture-labs", "copilot-extensions"],
                        "repo_allowlist_fail_closed": True,
                    }
                }
            },
        },
    )
    _write_tenant_repo(
        src / "dotfiles",
        {
            "id": "dotfiles",
            "roles": ["source"],
            "sync": {"harness_repos": HARNESS},
            "machines": {
                "book2": {
                    "sync": {
                        "repo_denylist": ["aperture-labs", "copilot-extensions"],
                    }
                }
            },
        },
    )
    # copilot-extensions is adopted but carries NO tenant block -- it is a
    # session origin, not a tenant.
    (src / "copilot-extensions").mkdir(parents=True)
    (src / "copilot-extensions" / ".agent-logger.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "log": {"voice_pack": "none"}}),
        encoding="utf-8",
    )

    _write_registry(
        tmp_path / ".aw", src, ["aperture-labs", "dotfiles", "copilot-extensions"]
    )

    # Machine-local supplements: transport (local target -> distinct dest) + source.
    supp = home / tenancy.TENANT_SUPPLEMENT_DIR
    supp.mkdir(parents=True)
    for tid in ("aperture-labs", "dotfiles"):
        (supp / f"{tid}.yaml").write_text(
            yaml.safe_dump(
                {
                    "sync": {
                        "source": str(copilot),
                        "target": "local",
                        "targets": {"local": {"path": str(home / f"dest-{tid}")}},
                    }
                }
            ),
            encoding="utf-8",
        )
    return copilot, home, tmp_path / ".aw"


def test_discover_only_tenant_blocked_repos(tmp_path):
    _copilot, home, aw = _standing_tenants(tmp_path)
    tenants = tenancy.discover_tenants(machine="book2", home=home, aw_home=aw)
    ids = sorted(t.tenant_id for t in tenants)
    assert ids == ["aperture-labs", "dotfiles"]  # not copilot-extensions


def test_discover_prefers_dedicated_tenant_file(tmp_path):
    """A dedicated .agent-logger.tenant.yaml carries the tenant, and a log-only
    .agent-logger.yaml alongside it does not shadow it."""
    src = tmp_path / "src"
    repo = src / "aperture-labs"
    repo.mkdir(parents=True)
    (repo / ".agent-logger.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "log": {"note_marker": "NOTE:"}}),
        encoding="utf-8",
    )
    (repo / ".agent-logger.tenant.yaml").write_text(
        yaml.safe_dump(
            {"schema_version": 1, "tenant": {"id": "aperture-labs", "roles": ["source"]}}
        ),
        encoding="utf-8",
    )
    _write_registry(tmp_path / ".aw", src, ["aperture-labs"])
    tenants = tenancy.discover_tenants(
        machine="book2", home=tmp_path / "home", aw_home=tmp_path / ".aw"
    )
    assert [t.tenant_id for t in tenants] == ["aperture-labs"]
    assert tenants[0].config_path.name == ".agent-logger.tenant.yaml"


def test_discover_malformed_tenant_is_skipped(tmp_path):
    src = tmp_path / "src"
    (src / "bad").mkdir(parents=True)
    (src / "bad" / ".agent-logger.yaml").write_text(
        yaml.safe_dump({"tenant": {"roles": ["nonsense-role"]}}), encoding="utf-8"
    )
    _write_registry(tmp_path / ".aw", src, ["bad"])
    tenants = tenancy.discover_tenants(
        machine="book2", home=tmp_path / "home", aw_home=tmp_path / ".aw"
    )
    assert tenants == []


def test_discover_future_schema_tenant_carries_advisory(tmp_path):
    """A tenant config from a newer schema is still discovered (tolerant read),
    carrying an advisory that fields were ignored."""
    src = tmp_path / "src"
    repo = src / "aperture-labs"
    repo.mkdir(parents=True)
    (repo / ".agent-logger.tenant.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 99,
                "tenant": {"id": "aperture-labs", "roles": ["source"], "new": 1},
            }
        ),
        encoding="utf-8",
    )
    _write_registry(tmp_path / ".aw", src, ["aperture-labs"])
    tenants = tenancy.discover_tenants(
        machine="book2", home=tmp_path / "home", aw_home=tmp_path / ".aw"
    )
    assert [t.tenant_id for t in tenants] == ["aperture-labs"]
    assert any("newer schema" in a for a in tenants[0].advisories)


def test_discover_dedupes_duplicate_tenant_ids(tmp_path):
    """Two adopted repos declaring the same id -> keep the first, drop the
    collision (they would otherwise share lock/supplement/sink state)."""
    src = tmp_path / "src"
    for repo in ("one", "two"):
        _write_tenant_repo(src / repo, {"id": "shared", "roles": ["source"]})
    _write_registry(tmp_path / ".aw", src, ["one", "two"])
    tenants = tenancy.discover_tenants(
        machine="book2", home=tmp_path / "home", aw_home=tmp_path / ".aw"
    )
    assert [t.tenant_id for t in tenants] == ["shared"]


# --------------------------------------------------------------------------- #
# orchestration -- the complementary no-leak proof                             #
# --------------------------------------------------------------------------- #


def _dest_sessions(home: Path, tid: str, machine: str = "book2") -> set[str]:
    ss = home / f"dest-{tid}" / machine / "session-state"
    if not ss.is_dir():
        return set()
    return {d.name for d in ss.iterdir() if d.is_dir()}


def test_run_all_book2_complementary_no_leak(tmp_path):
    _copilot, home, aw = _standing_tenants(tmp_path)
    tenants = tenancy.discover_tenants(machine="book2", home=home, aw_home=aw)
    result = tenancy.run_all(tenants, roles=("source",), machine="book2")

    assert all(o.status == "ok" for o in result.outcomes), result.as_dict()

    apl = _dest_sessions(home, "aperture-labs")
    dot = _dest_sessions(home, "dotfiles")

    # aperture-labs takes ONLY facility sessions (fail-closed).
    assert apl == {"sess-apl", "sess-cext"}
    # dotfiles takes EVERYTHING ELSE (denylist catch-all).
    assert dot == {"sess-dot", "sess-work"}
    # Exact complements: every session in exactly one tenant, no overlap/gap.
    assert apl.isdisjoint(dot)
    assert apl | dot == {"sess-apl", "sess-cext", "sess-dot", "sess-work"}


def test_run_all_shared_source_lock(tmp_path):
    """Tenants sharing one ~/.copilot source share the default lock so their
    source-mutating steps serialize (corrects an earlier per-tenant-lock race)."""
    _copilot, home, aw = _standing_tenants(tmp_path)
    tenants = tenancy.discover_tenants(machine="book2", home=home, aw_home=aw)
    locks = {t.config.sync_lock_name for t in tenants}
    assert locks == {"session-sync.lock"}


def test_run_all_source_conflict_fails_closed(tmp_path):
    """Two source tenants claiming the same session -> fail closed: a conflict
    is reported and NO source tenant is synced (never double-push a session)."""
    src = tmp_path / "src"
    copilot = _make_copilot(tmp_path)
    home = tmp_path / "home"
    for name in ("alpha", "beta"):
        _write_tenant_repo(
            src / name,
            {
                "id": name,
                "roles": ["source"],
                "sync": {"harness_repos": HARNESS, "repo_allowlist": ["aperture-labs"]},
            },
        )
    _write_registry(tmp_path / ".aw", src, ["alpha", "beta"])
    supp = home / tenancy.TENANT_SUPPLEMENT_DIR
    supp.mkdir(parents=True)
    for name in ("alpha", "beta"):
        (supp / f"{name}.yaml").write_text(
            yaml.safe_dump(
                {
                    "sync": {
                        "source": str(copilot),
                        "target": "local",
                        "targets": {"local": {"path": str(home / f"dest-{name}")}},
                    }
                }
            ),
            encoding="utf-8",
        )
    tenants = tenancy.discover_tenants(machine="book2", home=home, aw_home=tmp_path / ".aw")
    assert tenancy.source_conflicts(tenants)  # both claim sess-apl

    result = tenancy.run_all(tenants, roles=("source",), machine="book2")
    assert any(
        o.status == "failed" and "conflict" in o.detail for o in result.outcomes
    )
    # Fail closed: nothing was pushed to either destination.
    assert not (home / "dest-alpha").exists()
    assert not (home / "dest-beta").exists()


def test_run_all_skips_disabled_tenant(tmp_path):
    src = tmp_path / "src"
    _write_tenant_repo(
        src / "aperture-labs",
        {"id": "aperture-labs", "roles": ["source"], "enabled": False},
    )
    _write_registry(tmp_path / ".aw", src, ["aperture-labs"])
    tenants = tenancy.discover_tenants(
        machine="book2", home=tmp_path / "home", aw_home=tmp_path / ".aw"
    )
    result = tenancy.run_all(tenants, roles=("source",), machine="book2")
    assert [o.status for o in result.outcomes] == ["skipped"]


def test_run_all_dry_run_does_not_write(tmp_path):
    _copilot, home, aw = _standing_tenants(tmp_path)
    tenants = tenancy.discover_tenants(machine="book2", home=home, aw_home=aw)
    tenancy.run_all(tenants, roles=("source",), dry_run=True, machine="book2")
    assert _dest_sessions(home, "aperture-labs") == set()
    assert _dest_sessions(home, "dotfiles") == set()


# --------------------------------------------------------------------------- #
# behavior-preserving: default single-tenant config unchanged                  #
# --------------------------------------------------------------------------- #


def test_tenants_sync_falls_back_to_single_tenant(monkeypatch):
    """`tenants sync` with no adopted tenants falls back to the legacy
    single-home run_sync so a scheduler wired to it keeps syncing."""
    from agent_logger import __main__ as cli

    monkeypatch.setattr(tenancy, "discover_tenants", lambda **k: [])
    called = {}

    def fake_run_sync(cfg, *, dry_run=False, prune=False):
        called["ran"] = True
        return 0

    monkeypatch.setattr("agent_logger.sync.engine.run_sync", fake_run_sync)
    rc = cli.main(["tenants", "sync", "--machine", "book2"])
    assert rc == 0
    assert called.get("ran") is True


def test_default_lock_name_preserved(tmp_path):
    cfg = load_config(home=tmp_path, include_repo=False)
    assert cfg.sync_lock_name == "session-sync.lock"


def test_repo_config_tolerates_tenant_block(tmp_path):
    """A repo-local .agent-logger.yaml carrying a tenant block still loads for
    ambient (log-only) use without raising."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".agent-logger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "log": {"note_marker": "NOTE:"},
                "tenant": {"id": "aperture-labs", "roles": ["source"]},
            }
        ),
        encoding="utf-8",
    )
    cfg = load_config(home=tmp_path / "home", repo_start=repo)
    # tenant block ignored by the ambient loader; log block still honored.
    assert cfg.repo_config_path == repo / ".agent-logger.yaml"


def test_repo_config_tenant_only_no_log(tmp_path):
    """A tenant-only repo config (no log block) is a no-op for ambient load."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".agent-logger.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "tenant": {"id": "r"}}),
        encoding="utf-8",
    )
    cfg = load_config(home=tmp_path / "home", repo_start=repo)
    assert cfg.repo_config_path == repo / ".agent-logger.yaml"
