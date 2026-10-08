from __future__ import annotations

from machine_transport.registry import MachineEntry, SSHEnvironment
from machine_transport.transport import (
    TransportPlan,
    default_shell_for_env_name,
    get_machine_transport,
    resolve_ssh_target,
    wrap_remote_command,
)


def _loader(entries):
    return lambda: entries


class TestDefaultShellForEnvName:
    def test_uses_configured_shell_when_set(self):
        assert default_shell_for_env_name("windows", "pwsh-preview") == "pwsh-preview"

    def test_defaults_windows_to_pwsh(self):
        assert default_shell_for_env_name("windows", "") == "pwsh"

    def test_defaults_other_envs_to_bash(self):
        assert default_shell_for_env_name("linux", "") == "bash"
        assert default_shell_for_env_name("wsl", "") == "bash"


class TestResolveSshTarget:
    def test_no_ssh_environments_falls_back_to_key(self):
        entry = MachineEntry(key="borealis", display_name="Borealis")
        assert resolve_ssh_target(entry) == ("borealis", "")

    def test_defaults_shell_from_environment_name(self):
        posix_entry = MachineEntry(
            key="borealis", display_name="Borealis", environment="Ubuntu",
            ssh_environments=[SSHEnvironment(name="linux", alias="borealis")],
        )
        assert resolve_ssh_target(posix_entry) == ("borealis", "bash")

        windows_entry = MachineEntry(
            key="atlas-core", display_name="Atlas", environment="Windows 11",
            ssh_environments=[SSHEnvironment(name="windows", alias="atlas-core")],
        )
        assert resolve_ssh_target(windows_entry) == ("atlas-core", "pwsh")

    def test_prefers_windows_environment_when_entry_is_windows(self):
        entry = MachineEntry(
            key="atlas-core", display_name="Atlas", environment="Windows 11",
            ssh_environments=[
                SSHEnvironment(name="wsl", alias="atlas-core-wsl"),
                SSHEnvironment(name="windows", alias="atlas-core"),
            ],
        )
        assert resolve_ssh_target(entry) == ("atlas-core", "pwsh")

    def test_prefers_first_posix_environment_in_declaration_order(self):
        """The loop matches ``name in ("linux", "wsl")`` and returns the
        FIRST one found in declaration order -- it does not itself prefer
        linux over wsl or vice versa."""
        entry = MachineEntry(
            key="borealis", display_name="Borealis", environment="Ubuntu",
            ssh_environments=[
                SSHEnvironment(name="wsl", alias="borealis-wsl"),
                SSHEnvironment(name="linux", alias="borealis"),
            ],
        )
        assert resolve_ssh_target(entry) == ("borealis-wsl", "bash")

    def test_falls_back_to_first_environment_when_no_preference_matches(self):
        entry = MachineEntry(
            key="box-a", display_name="A", environment="",
            ssh_environments=[SSHEnvironment(name="exotic", alias="box-a-exotic")],
        )
        assert resolve_ssh_target(entry) == ("box-a-exotic", "bash")

    def test_honors_explicit_shell_override(self):
        entry = MachineEntry(
            key="box-a", display_name="A", environment="Windows 11",
            ssh_environments=[
                SSHEnvironment(name="windows", alias="box-a", shell="pwsh-preview"),
            ],
        )
        assert resolve_ssh_target(entry) == ("box-a", "pwsh-preview")


class TestWrapRemoteCommand:
    def test_wraps_posix_shells(self):
        assert wrap_remote_command("bash", "aperture-labs") == "bash -lc aperture-labs"
        assert wrap_remote_command("sh", "aperture-labs") == "sh -lc aperture-labs"
        assert wrap_remote_command("zsh", "aperture-labs") == "zsh -lc aperture-labs"

    def test_quotes_the_inner_command(self):
        wrapped = wrap_remote_command("bash", "aperture-labs list --json")
        assert wrapped == "bash -lc 'aperture-labs list --json'"

    def test_never_wraps_pwsh_or_unrecognized_shell(self):
        assert wrap_remote_command("pwsh", "aperture-labs") == "aperture-labs"
        assert wrap_remote_command("", "aperture-labs") == "aperture-labs"
        assert wrap_remote_command("cmd", "aperture-labs") == "aperture-labs"


class TestGetMachineTransport:
    def test_local_machine_returns_local_plan(self):
        plan = get_machine_transport(
            "aurora-cloud2", config_machine="aurora-cloud2", load_entries=_loader({}),
        )
        assert plan == TransportPlan(local=True, resolved=True, machine_key="aurora-cloud2")

    def test_local_plan_wrap_is_a_no_op(self):
        plan = get_machine_transport(
            "aurora-cloud2", config_machine="aurora-cloud2", load_entries=_loader({}),
        )
        assert plan.wrap("agent-worktrees --version") == "agent-worktrees --version"

    def test_remote_machine_resolves_ssh_alias_and_shell(self):
        entries = {
            "borealis": MachineEntry(
                key="borealis", display_name="Borealis", environment="Ubuntu",
                ssh_environments=[SSHEnvironment(name="linux", alias="borealis")],
            ),
        }
        plan = get_machine_transport(
            "borealis", config_machine="aurora-cloud2", load_entries=_loader(entries),
        )
        assert plan.local is False
        assert plan.resolved is True
        assert plan.machine_key == "borealis"
        assert plan.ssh_alias == "borealis"
        assert plan.shell == "bash"

    def test_remote_plan_wrap_applies_posix_login_shell(self):
        entries = {
            "borealis": MachineEntry(
                key="borealis", display_name="Borealis", environment="Ubuntu",
                ssh_environments=[SSHEnvironment(name="linux", alias="borealis")],
            ),
        }
        plan = get_machine_transport(
            "borealis", config_machine="aurora-cloud2", load_entries=_loader(entries),
        )
        assert plan.wrap("aperture-labs") == "bash -lc aperture-labs"

    def test_remote_windows_plan_wrap_passes_through_untouched(self):
        entries = {
            "atlas-core": MachineEntry(
                key="atlas-core", display_name="Atlas", environment="Windows 11",
                ssh_environments=[SSHEnvironment(name="windows", alias="atlas-core")],
            ),
        }
        plan = get_machine_transport(
            "atlas-core", config_machine="aurora-cloud2", load_entries=_loader(entries),
        )
        assert plan.wrap("aperture-labs") == "aperture-labs"

    def test_unknown_machine_is_unresolved(self):
        plan = get_machine_transport(
            "nonexistent-box", config_machine="aurora-cloud2", load_entries=_loader({}),
        )
        assert plan == TransportPlan(local=False, resolved=False)

    def test_alias_less_remote_environment_resolves_without_an_ssh_alias(self):
        entries = {
            "box-a": MachineEntry(
                key="box-a", display_name="A", environment="Windows 11",
                ssh_environments=[SSHEnvironment(name="windows", alias="")],
            ),
        }
        plan = get_machine_transport(
            "box-a", config_machine="aurora-cloud2", load_entries=_loader(entries),
        )
        assert plan.resolved is True
        assert plan.local is False
        assert plan.ssh_alias is None  # caller must check before dispatching

    def test_remote_entry_with_no_ssh_environments_exposes_neither_alias_nor_shell(self):
        """``resolve_ssh_target``'s own "no SSH environments at all"
        fallback returns ``(entry.key, "")`` -- ``entry.key`` is a
        placeholder identity, never a real SSH alias. A caller that checks
        only ``plan.ssh_alias`` (not also ``plan.shell``) before dispatching
        must not be handed a nonempty-looking alias for an entry with no
        actual remote transport."""
        entries = {
            "box-a": MachineEntry(key="box-a", display_name="A", environment="Windows 11"),
        }
        plan = get_machine_transport(
            "box-a", config_machine="aurora-cloud2", load_entries=_loader(entries),
        )
        assert plan.resolved is True
        assert plan.local is False
        assert plan.ssh_alias is None
        assert plan.shell is None

    def test_registry_load_failure_degrades_to_unresolved(self):
        def _boom():
            raise FileNotFoundError("no registry")

        plan = get_machine_transport(
            "some-box", config_machine="aurora-cloud2", load_entries=_boom,
        )
        assert plan == TransportPlan(local=False, resolved=False)

    def test_direct_match_never_calls_load_entries(self):
        """The fast, common path (``name`` spelled exactly like
        ``config_machine``) must stay registry-free, matching
        ``is_local_machine``'s own degrade-safe ordering."""
        def _boom():
            raise AssertionError("load_entries must not be called")

        plan = get_machine_transport(
            "aurora-cloud2", config_machine="aurora-cloud2", load_entries=_boom,
        )
        assert plan == TransportPlan(local=True, resolved=True, machine_key="aurora-cloud2")

    def test_load_entries_is_called_at_most_once_per_resolution(self):
        """Regression: a naive implementation called ``load_entries`` once
        inside the identity check and again for the entry/alias resolution
        -- two separate snapshots that could disagree (e.g. a live registry
        that changed between reads). Both the identity check and the entry
        resolution must observe the exact same single snapshot."""
        calls = []

        def _counting_loader():
            calls.append(1)
            return {
                "box-a": MachineEntry(
                    key="box-a", display_name="A", alias="configured-alias",
                ),
            }

        # "box-a" (the registry key) vs. "configured-alias" (the configured
        # alias) is NOT a direct string match -- resolving it local requires
        # an actual registry lookup (the alias-vs-key same-entry case).
        plan = get_machine_transport(
            "box-a", config_machine="configured-alias",
            load_entries=_counting_loader,
        )
        assert plan.local is True
        assert len(calls) == 1

    def test_registry_backed_local_match_uses_the_resolved_entry_key(self):
        """A caller passing the registry ALIAS while ``config_machine`` is
        the KEY (or vice versa) must report the matched entry's own
        canonical ``key`` as ``machine_key`` -- not the raw, possibly
        differently-spelled input string."""
        entries = {
            "box-a": MachineEntry(key="box-a", display_name="A", alias="aurora-cloud2"),
        }
        plan = get_machine_transport(
            "box-a", config_machine="aurora-cloud2", load_entries=_loader(entries),
        )
        assert plan.local is True
        assert plan.machine_key == "box-a"

    def test_cached_load_failure_is_observed_consistently_and_only_once(self):
        """A registry load failure must also be cached -- not re-attempted a
        second time for the entry-resolution step -- and both the identity
        check and the entry lookup must observe the same "unavailable"
        outcome."""
        calls = []

        def _boom():
            calls.append(1)
            raise ValueError("malformed machines.yaml")

        plan = get_machine_transport(
            "some-box", config_machine="aurora-cloud2", load_entries=_boom,
        )
        assert plan == TransportPlan(local=False, resolved=False)
        assert len(calls) == 1
