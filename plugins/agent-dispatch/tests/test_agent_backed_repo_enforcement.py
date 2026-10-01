"""Tests for the opt-in "agent-backed repo" enforcement on task creation.

A repo with no registered ``repo`` pointer has no coordinator/supervisor
watching it, so a task queued against it can sit unclaimed indefinitely.
:func:`agent_backed_enforcement_enabled` is default off (many legitimate
deployments create tasks against ad-hoc/throwaway lanes with no registered
pointer); opt in per-deployment via
:data:`registrar_discovery.ENFORCE_REGISTERED_REPOS_ENV`.

Matching is against a pointer's explicit canonical ``aliases``, never its
free-form ``name`` or a basename-only comparison -- a pointer's ``name`` is
operator-chosen and not a repo identity, so two unrelated repos that merely
share a trailing path segment must not both pass.
"""

from __future__ import annotations

import pytest

from agent_dispatch import registrar_discovery
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_records import TaskError
from tests._helpers import TEST_REPO


@pytest.fixture
def registrar_base(tmp_path, monkeypatch):
    """Isolate the registrar pointer store and arm the enforcement env var."""
    base = tmp_path / "registrar"
    monkeypatch.setenv(registrar_discovery.REGISTRAR_DIR_ENV, str(base))
    return base


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


def test_enforcement_defaults_off(q, monkeypatch, registrar_base):
    """With the env var unset, an unregistered repo lane is unaffected."""
    monkeypatch.delenv(registrar_discovery.ENFORCE_REGISTERED_REPOS_ENV, raising=False)
    task = q.create("do a thing", repo=TEST_REPO)
    assert task.repo == TEST_REPO


def test_enforcement_refuses_unregistered_lane_when_enabled(
    q, monkeypatch, registrar_base
):
    monkeypatch.setenv(registrar_discovery.ENFORCE_REGISTERED_REPOS_ENV, "1")
    with pytest.raises(TaskError, match="not a registered agent-backed repo"):
        q.create("do a thing", repo=TEST_REPO)


def test_enforcement_allows_registered_lane_when_enabled(q, monkeypatch, registrar_base):
    monkeypatch.setenv(registrar_discovery.ENFORCE_REGISTERED_REPOS_ENV, "1")
    registrar_discovery.add_pointer(
        "widget", "/some/path/widget", kind="repo", aliases=[TEST_REPO], base=registrar_base
    )
    task = q.create("do a thing", repo=TEST_REPO)
    assert task.repo == TEST_REPO


def test_enforcement_does_not_admit_an_unrelated_repo_sharing_a_basename(
    q, monkeypatch, registrar_base
):
    """A pointer named 'widget' for one checkout must never make an unrelated
    'widget' repo on a different host pass just because the trailing path
    segment matches."""
    monkeypatch.setenv(registrar_discovery.ENFORCE_REGISTERED_REPOS_ENV, "1")
    registrar_discovery.add_pointer(
        "widget",
        "/some/path/widget",
        kind="repo",
        aliases=["host-a.example/org-a/widget"],
        base=registrar_base,
    )
    with pytest.raises(TaskError, match="not a registered agent-backed repo"):
        q.create("do a thing", repo="host-b.example/org-b/widget")


def test_enforcement_matches_an_explicit_second_host_alias(
    q, monkeypatch, registrar_base
):
    """A repo known under multiple host aliases (e.g. a direct host and a
    reverse-proxy alias for the same forge) passes once every alias it's
    known under is explicitly registered."""
    monkeypatch.setenv(registrar_discovery.ENFORCE_REGISTERED_REPOS_ENV, "1")
    registrar_discovery.add_pointer(
        "widget",
        "/some/path/widget",
        kind="repo",
        aliases=["forge.example/org/widget", "proxy.example/forge/org/widget"],
        base=registrar_base,
    )
    task = q.create("do a thing", repo="proxy.example/forge/org/widget")
    assert task.repo == "proxy.example/forge/org/widget"


def test_enforcement_ignores_dir_kind_pointers(q, monkeypatch, registrar_base):
    """A bare ``dir`` pointer (no repo identity) doesn't count as agent-backed,
    even if someone passed aliases for it."""
    monkeypatch.setenv(registrar_discovery.ENFORCE_REGISTERED_REPOS_ENV, "1")
    registrar_discovery.add_pointer(
        "widget", "/some/path/widget", kind="dir", aliases=[TEST_REPO], base=registrar_base
    )
    with pytest.raises(TaskError, match="not a registered agent-backed repo"):
        q.create("do a thing", repo=TEST_REPO)


def test_enforcement_allows_idempotent_dedup_replay_even_when_unregistered(
    q, monkeypatch, registrar_base
):
    """An exact dedup_key retry on an already-accepted task must return the
    existing row even if its lane's pointer was since removed -- the check
    only gates a genuinely new insert."""
    task = q.create("do a thing", repo=TEST_REPO, dedup_key="dk-1")
    monkeypatch.setenv(registrar_discovery.ENFORCE_REGISTERED_REPOS_ENV, "1")
    replayed = q.create("do a thing", repo=TEST_REPO, dedup_key="dk-1")
    assert replayed.id == task.id


def test_add_pointer_rejects_an_invalid_alias(registrar_base):
    with pytest.raises(registrar_discovery.RegistrarError):
        registrar_discovery.add_pointer(
            "widget", "/some/path/widget", kind="repo", aliases=[""], base=registrar_base
        )


def test_add_pointer_preserves_existing_aliases_on_reregistration(registrar_base):
    """Re-adding the same pointer (e.g. an automated repo-sync reconcile pass)
    without an explicit ``aliases`` argument must not silently discard
    aliases a prior call registered -- only an explicit ``aliases`` replaces
    them."""
    registrar_discovery.add_pointer(
        "widget",
        "/some/path/widget",
        kind="repo",
        aliases=["forge.example/org/widget", "proxy.example/forge/org/widget"],
        base=registrar_base,
    )
    # Re-register with no aliases argument at all (as an unaware caller would).
    registrar_discovery.add_pointer(
        "widget", "/some/path/widget", kind="repo", base=registrar_base
    )
    pointers = registrar_discovery.load_pointers(base=registrar_base)
    (widget,) = [p for p in pointers if p.name == "widget"]
    assert set(widget.aliases) == {
        "forge.example/org/widget",
        "proxy.example/forge/org/widget",
    }


def test_add_pointer_preserves_existing_owner_when_adding_an_alias(registrar_base):
    """Re-registering an unchanged pointer solely to add/replace its aliases
    must not silently clear a previously-declared owner -- only an explicit
    ``owner`` argument changes it."""
    registrar_discovery.add_pointer(
        "widget",
        "/some/path/widget",
        kind="repo",
        owner="repo:widget",
        aliases=["forge.example/org/widget"],
        base=registrar_base,
    )
    registrar_discovery.add_pointer(
        "widget",
        "/some/path/widget",
        kind="repo",
        aliases=["forge.example/org/widget", "proxy.example/forge/org/widget"],
        base=registrar_base,
    )
    pointers = registrar_discovery.load_pointers(base=registrar_base)
    (widget,) = [p for p in pointers if p.name == "widget"]
    assert widget.owner == "repo:widget"
    assert set(widget.aliases) == {
        "forge.example/org/widget",
        "proxy.example/forge/org/widget",
    }


def test_add_pointer_explicit_aliases_do_replace_existing_ones(registrar_base):
    registrar_discovery.add_pointer(
        "widget", "/some/path/widget", kind="repo", aliases=["a.example/x/widget"],
        base=registrar_base,
    )
    registrar_discovery.add_pointer(
        "widget", "/some/path/widget", kind="repo", aliases=["b.example/y/widget"],
        base=registrar_base,
    )
    pointers = registrar_discovery.load_pointers(base=registrar_base)
    (widget,) = [p for p in pointers if p.name == "widget"]
    assert widget.aliases == ("b.example/y/widget",)


def test_add_pointer_resets_aliases_when_location_changes(registrar_base):
    """Retargeting a pointer to a different location must not keep the old
    target's aliases authorized -- that would leave an old repo lane
    wrongly agent-backed while the new target's own identity is never
    derived."""
    registrar_discovery.add_pointer(
        "widget",
        "/some/path/widget-old",
        kind="repo",
        aliases=["forge.example/org/widget-old"],
        base=registrar_base,
    )
    registrar_discovery.add_pointer(
        "widget", "/some/path/widget-new", kind="repo", base=registrar_base
    )
    pointers = registrar_discovery.load_pointers(base=registrar_base)
    (widget,) = [p for p in pointers if p.name == "widget"]
    assert "forge.example/org/widget-old" not in widget.aliases


def test_add_pointer_resets_aliases_when_kind_changes(registrar_base):
    registrar_discovery.add_pointer(
        "widget",
        "/some/path/widget",
        kind="repo",
        aliases=["forge.example/org/widget"],
        base=registrar_base,
    )
    registrar_discovery.add_pointer("widget", "/some/path/widget", kind="dir", base=registrar_base)
    pointers = registrar_discovery.load_pointers(base=registrar_base)
    (widget,) = [p for p in pointers if p.name == "widget"]
    assert widget.aliases == ()


def test_pointer_from_dict_rejects_malformed_aliases_values():
    """A declared default via ``or []`` would silently coerce a corrupt
    persisted value (``""``, ``None``, ``0``) into an empty alias set instead
    of rejecting it -- the key must be read with an absent-only default."""
    for bad in ("", None, 0, "not-a-list"):
        with pytest.raises(registrar_discovery.RegistrarError):
            registrar_discovery.Pointer.from_dict(
                {"name": "widget", "location": "/x", "kind": "repo", "aliases": bad}
            )


def test_derive_git_remote_alias_refuses_a_non_toplevel_directory(tmp_path):
    """``git -C <dir>`` walks upward to find an enclosing repo; a plain
    subdirectory (or one nested inside an unrelated outer checkout) must
    never have that ancestor's remote silently attributed to it."""
    import subprocess

    outer = tmp_path / "outer"
    outer.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=outer, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.com/org/outer.git"],
        cwd=outer,
        check=True,
    )
    inner = outer / "nested" / "not-a-repo-root"
    inner.mkdir(parents=True)
    assert registrar_discovery._derive_git_remote_alias(inner) is None


def test_derive_git_remote_alias_scrubs_ambient_git_env_contamination(
    tmp_path, monkeypatch
):
    """A leaked ``GIT_DIR``/``GIT_WORK_TREE`` pair in the calling process's own
    environment must never make the probe report an unrelated repository's
    remote as ``location``'s own identity."""
    import subprocess

    real = tmp_path / "real"
    real.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=real, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.com/org/real.git"],
        cwd=real,
        check=True,
    )

    decoy = tmp_path / "decoy"
    decoy.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=decoy, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.com/org/decoy.git"],
        cwd=decoy,
        check=True,
    )

    # Simulate ambient contamination: GIT_DIR points at the decoy repo while
    # GIT_WORK_TREE is set to `real`, so an unscrubbed probe would report
    # `real` as the toplevel (via GIT_WORK_TREE) while reading the decoy's
    # remote (via GIT_DIR).
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(real))

    result = registrar_discovery._derive_git_remote_alias(real)
    assert result == "example.com/org/real"


def test_derive_git_remote_alias_scrubs_git_config_override_contamination(
    tmp_path, monkeypatch
):
    """``GIT_CONFIG_KEY_n``/``GIT_CONFIG_VALUE_n`` can override
    ``remote.origin.url`` directly, with no GIT_DIR/GIT_WORK_TREE redirection
    at all -- the toplevel check alone would not catch this."""
    import subprocess

    real = tmp_path / "real"
    real.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=real, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.com/org/real.git"],
        cwd=real,
        check=True,
    )

    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "remote.origin.url")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "https://attacker.example/org/evil.git")

    result = registrar_discovery._derive_git_remote_alias(real)
    assert result == "example.com/org/real"


def test_derive_git_remote_alias_scrubs_git_config_path_override_contamination(
    tmp_path, monkeypatch
):
    """``GIT_CONFIG_GLOBAL``/``GIT_CONFIG_SYSTEM`` point git at an entirely
    different config *file*, which can itself define ``remote.origin.url`` --
    a different contamination vector than the per-key overrides above."""
    import subprocess

    real = tmp_path / "real"
    real.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=real, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.com/org/real.git"],
        cwd=real,
        check=True,
    )

    evil_config = tmp_path / "evil.gitconfig"
    evil_config.write_text(
        "[remote \"origin\"]\n\turl = https://attacker.example/org/evil.git\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(evil_config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(evil_config))

    result = registrar_discovery._derive_git_remote_alias(real)
    assert result == "example.com/org/real"


def test_known_lane_aliases_reads_only_repo_kind_pointer_aliases(registrar_base):
    registrar_discovery.add_pointer(
        "widget",
        "/home/x/widget",
        kind="repo",
        aliases=["forge.example/org/widget"],
        base=registrar_base,
    )
    registrar_discovery.add_pointer(
        "some-docs",
        "/home/x/docs",
        kind="dir",
        aliases=["forge.example/org/docs"],
        base=registrar_base,
    )
    assert registrar_discovery.known_lane_aliases(base=registrar_base) == frozenset(
        {"forge.example/org/widget"}
    )
