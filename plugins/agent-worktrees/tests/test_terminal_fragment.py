"""Tests for the Windows Terminal fragment *generation* flow (config -> fragment).

Exercises :mod:`agent_worktrees.terminal_fragment` -- the testable Python source
of truth that the installer's ``Build-TerminalFragment`` mirrors. Covers the
config-driven decisions that determine **which** profiles a machine emits: the
default column for unmanaged projects, the locked ``self.agent`` diagonal (why an
agent-exposed project keeps its launcher even with an empty selection), SSH
shell/agent gating, self-skip, cross-project de-duplication, WSL gating, and the
stable-GUID parity with PowerShell.
"""
from __future__ import annotations

from agent_worktrees import terminal_fragment as tf
from agent_worktrees.terminal_fragment import (
    ProjectInput,
    RosterMachine,
    SshEnvironment,
)


# ---------------------------------------------------------------------------
# Shared roster: Anomalous-Potato (self) + Emancipation-Cube + Mantis-Counter, all SSH-ready.
# ---------------------------------------------------------------------------

def _roster() -> tuple[RosterMachine, ...]:
    return (
        RosterMachine(
            key="anomalous-potato", display_name="Anomalous-Potato", ssh_ready=True,
            environments=(
                SshEnvironment("windows", "anomalous-potato", "pwsh"),
                SshEnvironment("wsl", "anomalous-potato-wsl", "bash"),
            ),
        ),
        RosterMachine(
            key="emancipation-cube", display_name="Emancipation-Cube", ssh_ready=True,
            environments=(
                SshEnvironment("windows", "emancipation-cube", "pwsh"),
                SshEnvironment("wsl", "emancipation-cube-wsl", "bash"),
            ),
        ),
        RosterMachine(
            key="mantis-counter", display_name="Mantis-Counter", ssh_ready=True,
            environments=(SshEnvironment("linux", "mantis-counter", "bash"),),
        ),
    )


def _build(project: ProjectInput, self_machine="anomalous-potato", computer="anomalous-potato"):
    return tf.build_fragment([project], self_machine, computer_name=computer)


def _names(result) -> list[str]:
    return [p.name for p in result.profiles]


def _kinds(result) -> set[str]:
    return {p.kind for p in result.profiles}


# ---------------------------------------------------------------------------
# Stable GUID parity with PowerShell New-StableGuid.
# ---------------------------------------------------------------------------

def test_stable_guid_matches_powershell_reference():
    """Reference values captured from the live install.ps1 ``New-StableGuid``."""
    assert tf.stable_guid("test-chamber-local-windows") == \
        "440d9b37-e5d0-d1f2-d8e7-ab1e0a8d6d3b"
    assert tf.stable_guid("ssh-emancipation-cube-wsl") == \
        "a11d2150-db5f-abf0-77b5-ad47539149c1"
    assert tf.stable_guid("agent-worktrees-launch-anomalous-potato-windows") == \
        "1d312c1f-19aa-dcf0-05c2-a7eba97868bf"


def test_guid_field_is_braced():
    assert tf._guid_field("x") == "{" + tf.stable_guid("x") + "}"


# ---------------------------------------------------------------------------
# Unmanaged project -> default column (minimal per-agent + bare cross-machine).
# ---------------------------------------------------------------------------

def test_unmanaged_emits_default_column():
    proj = ProjectInput(name="test-chamber", display="Test Chamber",
                        selection=None, roster=_roster())
    result = _build(proj)
    names = _names(result)

    # Local self.agent launcher present, named after the project.
    assert "Test Chamber" in names
    # A bare shell for every OTHER ready machine x env.
    assert "Emancipation-Cube" in names
    assert "Emancipation-Cube (WSL)" in names
    assert "Mantis-Counter" in names
    # No remote agent-launch combos, no local shells in the default column.
    assert _kinds(result) == {"local-agent", "ssh-shell"}
    # Self is never an SSH target.
    assert not any(p.commandline == "ssh anomalous-potato" for p in result.profiles)


def test_unmanaged_default_reports_in_plan():
    proj = ProjectInput(name="test-chamber", display="Test Chamber",
                        selection=None, roster=_roster())
    plan = _build(proj).plans[0]
    assert plan.unmanaged_default is True
    assert plan.managed is False


# ---------------------------------------------------------------------------
# The locked self.agent diagonal -- the "why a project is/ isn't present" crux.
# ---------------------------------------------------------------------------

def test_managed_empty_but_agent_exposed_keeps_local_launcher():
    """An explicit ``terminal_profiles: []`` on an *agent-exposed* project still
    emits the local launcher (the diagonal is locked) -- so the project never
    vanishes from the Terminal dropdown just because its selection is empty."""
    proj = ProjectInput(name="test-chamber", display="Test Chamber",
                        agent_exposed=True, selection=frozenset(), roster=_roster())
    result = _build(proj)
    assert _names(result) == ["Test Chamber"]
    assert result.profiles[0].kind == "local-agent"


def test_managed_empty_and_no_agent_emits_nothing():
    """A genuine ``--no-agent`` project (agent_exposed False) with an empty
    selection emits no profiles at all."""
    proj = ProjectInput(name="reference-repo", display="Reference Repo",
                        agent_exposed=False, selection=frozenset(), roster=_roster())
    result = _build(proj)
    assert result.profiles == []


def test_local_launcher_shape_matches_powershell():
    proj = ProjectInput(name="test-chamber", display="Test Chamber",
                        agent_exposed=True, selection=frozenset(), roster=_roster())
    p = _build(proj).profiles[0]
    assert p.guid == "{" + tf.stable_guid("test-chamber-local-windows") + "}"
    assert p.name == "Test Chamber"
    assert p.commandline == r'cmd /c "%USERPROFILE%\.local\bin\test-chamber.cmd"'
    wt = p.to_wt()
    assert wt["startingDirectory"] == "%USERPROFILE%"
    assert wt["colorScheme"] == "Aperture Science"
    assert wt["hidden"] is False


# ---------------------------------------------------------------------------
# SSH shell + launch-agent gating.
# ---------------------------------------------------------------------------

def test_managed_selection_emits_ssh_shell_and_launch_agent():
    sel = frozenset({
        "Anomalous-Potato|Win|agent",     # self (locked anyway)
        "Emancipation-Cube|Win|shell",        # plain ssh shell to Emancipation-Cube
        "Emancipation-Cube|Win|agent",        # launch test-chamber on Emancipation-Cube
    })
    proj = ProjectInput(name="test-chamber", display="Test Chamber",
                        selection=sel, roster=_roster())
    result = _build(proj)
    by_name = {p.name: p for p in result.profiles}

    assert by_name["Emancipation-Cube"].commandline == "ssh emancipation-cube"
    assert by_name["Emancipation-Cube"].kind == "ssh-shell"
    # Launch profile: "<project display> (<machine>)", pwsh env -> .cmd binstub.
    assert "Test Chamber (Emancipation-Cube)" in by_name
    launch = by_name["Test Chamber (Emancipation-Cube)"]
    assert launch.commandline == "ssh -t emancipation-cube test-chamber.cmd"
    assert launch.kind == "launch-agent"


def test_launch_agent_uses_bare_binstub_for_bash_env():
    sel = frozenset({"Mantis-Counter|Linux|agent"})
    proj = ProjectInput(name="test-chamber", display="Test Chamber",
                        agent_exposed=False, selection=sel, roster=_roster())
    launch = next(p for p in _build(proj).profiles if p.kind == "launch-agent")
    # bash shell -> no ``.cmd`` suffix.
    assert launch.commandline == "ssh -t mantis-counter test-chamber"
    assert launch.name == "Test Chamber (Mantis-Counter)"


def test_wsl_ssh_shell_labelled_and_iconed():
    sel = frozenset({"Emancipation-Cube|WSL|shell"})
    proj = ProjectInput(name="test-chamber", display="Test Chamber",
                        agent_exposed=False, selection=sel, roster=_roster(),
                        icon="ICON.ico", wsl_icon="WSL.ico")
    p = next(x for x in _build(proj).profiles if x.kind == "ssh-shell")
    assert p.name == "Emancipation-Cube (WSL)"
    assert p.commandline == "ssh emancipation-cube-wsl"
    assert p.icon == "WSL.ico"


# ---------------------------------------------------------------------------
# Self-skip, readiness gating, de-duplication.
# ---------------------------------------------------------------------------

def test_self_machine_never_becomes_ssh_target():
    # Even if the selection names the self machine as a shell target, no ssh
    # profile to self is emitted (the SSH loop skips self).
    sel = frozenset({"Anomalous-Potato|Win|shell", "Anomalous-Potato|WSL|shell"})
    proj = ProjectInput(name="test-chamber", display="Test Chamber",
                        selection=sel, roster=_roster())
    result = _build(proj)
    assert not any(p.commandline.startswith("ssh anomalous-potato") for p in result.profiles)
    # The Anomalous-Potato|Win|shell selection still produces a LOCAL shell (pwsh),
    # not an SSH-to-self.
    local_shells = [p for p in result.profiles if p.kind == "local-shell"]
    assert len(local_shells) == 1
    assert local_shells[0].commandline == "pwsh.exe"


def test_not_ready_machine_excluded():
    roster = (
        RosterMachine(key="anomalous-potato", display_name="Anomalous-Potato", ssh_ready=True,
                      environments=(SshEnvironment("windows", "anomalous-potato", "pwsh"),)),
        RosterMachine(key="book2", display_name="Book2", ssh_ready=False,
                      environments=(SshEnvironment("windows", "book2", "pwsh"),)),
    )
    proj = ProjectInput(name="test-chamber", display="Test Chamber",
                        selection=None, roster=roster)
    result = _build(proj)
    # Book2 is not ready -> no bare shell for it in the default column.
    assert "Book2" not in _names(result)


def test_shell_profiles_deduped_across_projects():
    """Local + remote *shell* GUIDs are project-independent: two projects both
    selecting the Emancipation-Cube shell emit ONE Emancipation-Cube profile."""
    sel = frozenset({"Emancipation-Cube|Win|shell"})
    p1 = ProjectInput(name="test-chamber", display="Test Chamber",
                    selection=sel, roster=_roster())
    p2 = ProjectInput(name="agent-worktrees", display="Agent Worktrees",
                    selection=sel, roster=_roster())
    result = tf.build_fragment([p1, p2], "anomalous-potato", computer_name="anomalous-potato")
    emancipation_cube = [p for p in result.profiles if p.name == "Emancipation-Cube"]
    assert len(emancipation_cube) == 1


# ---------------------------------------------------------------------------
# WSL local profiles.
# ---------------------------------------------------------------------------

def test_local_wsl_agent_requires_recorded_distro():
    sel = frozenset({"Anomalous-Potato|WSL|agent"})
    # No distro/state recorded -> no local WSL agent profile.
    without = ProjectInput(name="test-chamber", display="Test Chamber",
                        agent_exposed=False, selection=sel, roster=_roster())
    assert not any(p.kind == "local-wsl-agent" for p in _build(without).profiles)

    # Distro + state recorded -> the WSL launcher appears.
    with_wsl = ProjectInput(name="test-chamber", display="Test Chamber",
                        agent_exposed=False, selection=sel, roster=_roster(),
                        wsl_distro="Ubuntu", wsl_state="ready")
    p = next(x for x in _build(with_wsl).profiles if x.kind == "local-wsl-agent")
    assert p.name == "Test Chamber (WSL)"
    assert p.commandline == "wsl.exe -d Ubuntu -- bash -lc test-chamber"


def test_local_wsl_shell_honored_without_distro():
    sel = frozenset({"Anomalous-Potato|WSL|shell"})
    proj = ProjectInput(name="test-chamber", display="Test Chamber",
                        agent_exposed=False, selection=sel, roster=_roster())
    p = next(x for x in _build(proj).profiles if x.kind == "local-wsl-shell")
    assert p.commandline == "wsl.exe"
    assert p.name == "Anomalous-Potato (WSL)"


# ---------------------------------------------------------------------------
# Fragment envelope.
# ---------------------------------------------------------------------------

def test_fragment_carries_profiles_and_scheme():
    proj = ProjectInput(name="test-chamber", display="Test Chamber",
                        selection=None, roster=_roster())
    frag = _build(proj).fragment()
    assert "profiles" in frag and "schemes" in frag
    assert frag["schemes"][0]["name"] == "Aperture Science"
    # Every profile object is fully formed.
    for p in frag["profiles"]:
        assert set(p) >= {"guid", "name", "commandline", "icon",
                          "startingDirectory", "colorScheme", "hidden"}


def test_multi_project_no_guid_collisions():
    p1 = ProjectInput(name="test-chamber", display="Test Chamber",
                    selection=None, roster=_roster())
    p2 = ProjectInput(name="agent-worktrees", display="Agent Worktrees",
                    selection=None, roster=_roster())
    result = tf.build_fragment([p1, p2], "anomalous-potato", computer_name="anomalous-potato")
    guids = [p.guid for p in result.profiles]
    assert len(guids) == len(set(guids))


# ---------------------------------------------------------------------------
# Env-label vocabulary helpers.
# ---------------------------------------------------------------------------

def test_env_label_helpers():
    assert tf.sel_env_label("windows") == "Win"
    assert tf.sel_env_label("wsl") == "WSL"
    assert tf.sel_env_label("linux") == "Linux"
    assert tf.ssh_env_label("windows") == "Windows"
    assert tf.ssh_env_label("wsl") == "WSL"


# ---------------------------------------------------------------------------
# Disk collection wiring (managed-empty detection through config.yaml).
# ---------------------------------------------------------------------------

def test_collect_local_projects_reads_managed_empty(tmp_path, monkeypatch):
    """A ``terminal_profiles: []`` config.yaml yields a managed (empty) selection,
    while an absent key yields ``None`` (unmanaged)."""
    from agent_worktrees import config as _cfg
    from agent_worktrees import installer as _inst
    from agent_worktrees import repos as _repos

    # Two projects: one with an empty managed selection, one unmanaged.
    managed_dir = tmp_path / ".test-chamber"
    managed_dir.mkdir()
    (managed_dir / "config.yaml").write_text("terminal_profiles: []\n", encoding="utf-8")
    unmanaged_dir = tmp_path / ".other-proj"
    unmanaged_dir.mkdir()
    (unmanaged_dir / "config.yaml").write_text("machine: anomalous-potato\n", encoding="utf-8")

    monkeypatch.setattr(_cfg, "project_dir",
                        lambda name=None: tmp_path / f".{name}")
    monkeypatch.setattr(
        _inst, "read_projects_registry",
        lambda: {"projects": {"test-chamber": {"display_name": "Test Chamber"},
                              "other-proj": {}}})
    monkeypatch.setattr(_repos, "read_registry",
                        lambda: _repos.ReposRegistry(repos={}))

    projects = tf.collect_local_projects(current_project="test-chamber")
    by_name = {p.name: p for p in projects}

    # Current project is placed first.
    assert projects[0].name == "test-chamber"
    # Managed empty -> empty frozenset (NOT None).
    assert by_name["test-chamber"].selection == frozenset()
    assert by_name["test-chamber"].display == "Test Chamber"
    # Absent key -> None (unmanaged, default column at build time).
    assert by_name["other-proj"].selection is None
    # Title-cased slug fallback for the display name.
    assert by_name["other-proj"].display == "Other Proj"


def test_collect_skips_reserved_runtime_name(tmp_path, monkeypatch):
    """The runtime's own name never becomes a launchable project, from ANY
    source: a stale projects.yaml/repos.yaml entry, or ``current_project``
    (running the ``agent-worktrees`` binstub resolves the active project to
    ``agent-worktrees``). Otherwise the generator emits a bogus "Agent
    Worktrees" launcher backed by no repo."""
    from agent_worktrees import config as _cfg
    from agent_worktrees import installer as _inst
    from agent_worktrees import repos as _repos

    monkeypatch.setattr(_cfg, "project_dir", lambda name=None: tmp_path / f".{name}")
    # Both registries still list agent-worktrees (the real-world drift).
    monkeypatch.setattr(
        _inst, "read_projects_registry",
        lambda: {"projects": {"agent-worktrees": {}, "dotfiles": {}}})
    monkeypatch.setattr(_repos, "read_registry",
                        lambda: _repos.ReposRegistry(repos={}))

    # Even with current_project explicitly the reserved name, it is filtered.
    names = [p.name for p in tf.collect_local_projects(
        current_project="agent-worktrees")]
    assert "agent-worktrees" not in names
    assert "dotfiles" in names


# ---------------------------------------------------------------------------
# state.json / generatedProfiles reconciliation -- the convergent-sync fix.
# ---------------------------------------------------------------------------

def test_reconcile_heals_hidden_fragment_profile():
    """A live fragment GUID missing from settings.json (WT is hiding it) is
    pruned from generatedProfiles so WT re-discovers it. This is the exact
    Test Chamber failure the delta-based sync could never heal."""
    frag = ["{aaaa}", "{bbbb}"]
    settings = ["{aaaa}"]                 # bbbb materialized nowhere -> hidden
    generated = ["{aaaa}", "{bbbb}"]
    plan = tf.reconcile_generated_profiles(frag, settings, generated)
    assert plan.remove == ["{bbbb}"]
    assert plan.keep == ["{aaaa}"]
    assert plan.changed is True


def test_reconcile_keeps_materialized_profiles_untouched():
    """Fragment GUIDs already in settings.json stay in generatedProfiles, so
    WT preserves user customizations (no churn on steady state)."""
    frag = ["{aaaa}", "{bbbb}"]
    settings = ["{aaaa}", "{bbbb}"]
    generated = ["{aaaa}", "{bbbb}"]
    plan = tf.reconcile_generated_profiles(frag, settings, generated)
    assert plan.remove == []
    assert plan.changed is False


def test_reconcile_is_idempotent():
    """Re-running after WT materializes the healed profile is a no-op -- the
    invariant converges rather than oscillating."""
    frag = ["{aaaa}", "{bbbb}"]
    # First pass: bbbb hidden -> removed.
    first = tf.reconcile_generated_profiles(frag, ["{aaaa}"], ["{aaaa}", "{bbbb}"])
    assert first.remove == ["{bbbb}"]
    # WT then re-discovers bbbb into settings + generatedProfiles.
    second = tf.reconcile_generated_profiles(
        frag, ["{aaaa}", "{bbbb}"], ["{aaaa}", "{bbbb}"])
    assert second.remove == []


def test_reconcile_removes_stale_and_changed():
    frag = ["{aaaa}"]
    settings = ["{aaaa}", "{old}"]
    generated = ["{aaaa}", "{old}", "{chg}"]
    plan = tf.reconcile_generated_profiles(
        frag, settings, generated, stale_guids=["{old}"], changed_guids=["{chg}"])
    assert set(plan.remove) == {"{old}", "{chg}"}
    assert plan.keep == ["{aaaa}"]


def test_reconcile_leaves_foreign_orphans_untouched():
    """A v4/v5 orphan (WT built-in / random profile the user deleted) is kept --
    reclamation must never resurrect a foreign dynamic profile."""
    frag = ["{aaaa}"]
    settings = ["{aaaa}"]
    wsl_v5 = "{7edcd332-66b5-51da-b9b9-c9feed3a9fd2}"   # version nibble 5
    random_v4 = "{12345678-1234-4abc-8def-1234567890ab}"  # version nibble 4
    generated = ["{aaaa}", wsl_v5, random_v4]
    plan = tf.reconcile_generated_profiles(frag, settings, generated)
    assert plan.remove == []
    assert wsl_v5 in plan.keep and random_v4 in plan.keep


def test_reconcile_reclaims_our_orphans():
    """A non-v4/v5 orphan (our raw-hash generator's leftover, in no fragment,
    not materialized) is reclaimed -- this is what lets plain `update` finally
    drain the accumulated generatedProfiles cruft."""
    frag = ["{aaaa}"]
    settings = ["{aaaa}"]
    ours = "{593ace19-f6f2-a0bd-237c-bd163f6708f3}"   # version nibble a -> ours
    plan = tf.reconcile_generated_profiles(frag, settings, ["{aaaa}", ours])
    assert plan.remove == [ours]
    assert ours in plan.reclaimed
    assert "{aaaa}" in plan.keep


def test_reconcile_orphan_in_foreign_fragment_is_kept():
    """A non-v4/v5 orphan that a foreign fragment still emits is kept: pass the
    union of all installed fragments so we don't prune another extension's."""
    ours_frag = ["{aaaa}"]
    foreign = "{593ace19-f6f2-a0bd-237c-bd163f6708f3}"  # non-v4/v5 but foreign-owned
    plan = tf.reconcile_generated_profiles(
        ours_frag, ["{aaaa}"], ["{aaaa}", foreign],
        all_fragment_guids=["{aaaa}", foreign])
    assert plan.remove == []
    assert foreign in plan.keep


def test_reconcile_orphan_reclaim_can_be_disabled():
    ours = "{593ace19-f6f2-a0bd-237c-bd163f6708f3}"
    plan = tf.reconcile_generated_profiles(
        ["{aaaa}"], ["{aaaa}"], ["{aaaa}", ours], reclaim_orphans=False)
    assert plan.remove == []
    assert ours in plan.keep


def test_reconcile_heal_and_reclaim_reported_separately():
    frag = ["{aaaa}", "{bbbb}"]
    settings = ["{aaaa}"]                                  # bbbb hidden
    ours_orphan = "{593ace19-f6f2-a0bd-237c-bd163f6708f3}"
    plan = tf.reconcile_generated_profiles(
        frag, settings, ["{aaaa}", "{bbbb}", ours_orphan])
    assert plan.healed == ["{bbbb}"]
    assert plan.reclaimed == [ours_orphan]
    assert set(plan.remove) == {"{bbbb}", ours_orphan}


def test_is_rfc_v4_or_v5_guid():
    assert tf.is_rfc_v4_or_v5_guid("{7edcd332-66b5-51da-b9b9-c9feed3a9fd2}")   # v5
    assert tf.is_rfc_v4_or_v5_guid("12345678-1234-4abc-8def-1234567890ab")     # v4
    # Our raw-hash GUIDs: version nibble not 4/5.
    assert not tf.is_rfc_v4_or_v5_guid("{593ace19-f6f2-a0bd-237c-bd163f6708f3}")
    assert not tf.is_rfc_v4_or_v5_guid("{440d9b37-e5d0-d1f2-d8e7-ab1e0a8d6d3b}")
    # Malformed / short tokens are not treated as v4/v5.
    assert not tf.is_rfc_v4_or_v5_guid("{foobar}")
    assert not tf.is_rfc_v4_or_v5_guid("")


def test_reconcile_case_insensitive_preserves_original():
    frag = ["{AAAA}"]
    settings = []                          # AAAA hidden
    generated = ["{aaaa}"]                 # different casing than fragment
    plan = tf.reconcile_generated_profiles(frag, settings, generated)
    # Matched case-insensitively, but the original entry casing is preserved.
    assert plan.remove == ["{aaaa}"]

