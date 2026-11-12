"""Profile binding retained by the existing master handoff, never its workers."""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import sys

from .supervisor_lane_status import GENERATION_ENV, PROFILE_ENV, profile_arguments, settings_identity

OWNER_ENV = "AGENT_DISPATCH_SUPERVISOR_PROFILE_OWNER"


class MasterProfile:
    def __init__(self, path, generation, arguments=None, *, source_primary=False):
        self.path = Path(path)
        self.generation = generation or hashlib.sha256(self.path.read_bytes()).hexdigest()
        if not self.path.is_absolute() or hashlib.sha256(self.path.read_bytes()).hexdigest() != self.generation:
            raise ValueError("master profile binding is no longer current")
        self.next_generation = self.generation
        self.arguments = list(sys.argv[1:] if arguments is None else arguments)
        self.identity = settings_identity(self.arguments)
        self.source_primary = source_primary

    def environment(self):
        return {
            PROFILE_ENV: str(self.path), GENERATION_ENV: self.next_generation,
            **({OWNER_ENV: "source-primary"} if self.source_primary else {}),
        }

    def preparation(self, *, incoming=False):
        return {
            "path": str(self.path), "generation": self.next_generation if incoming else self.generation,
            **({"owner": "source-primary"} if self.source_primary else {}),
        }

    def configure(self, daemon):
        from .supervisor_registration import _spawn_self_update_successor

        original_target = daemon._self_update_stale_target

        def target(root, running):
            from .supervise_registration_cli import build_profile_parser
            from .registrar_discovery import _parse_env_file
            from .self_update import read_current_version, slot_python
            from .supervisor_registration import registration_scope

            content = self.path.read_bytes()
            generation = hashlib.sha256(content).hexdigest()
            arguments = list(self.arguments)
            if generation != self.generation:
                if self.source_primary:
                    from .source_start import SupervisorStartupPlan, supervisor_plan

                    plan = supervisor_plan(root)
                    if (
                        not isinstance(plan, SupervisorStartupPlan) or plan.profile is None
                        or not plan.profile.samefile(self.path) or plan.profile_generation != generation
                    ):
                        raise ValueError("Source primary profile no longer configures its resident master")
                    arguments, args = list(plan.argv), plan.args
                else:
                    arguments = profile_arguments(_parse_env_file(self.path, strict=True, content=content))
                    try:
                        args = build_profile_parser().parse_args(arguments)
                    except SystemExit as error:
                        raise ValueError("master profile arguments are invalid") from error
                if (
                    arguments[:2] != ["supervise", "serve"]
                    or args.once or args.no_single_instance or args.source_candidate is not None
                    or not math.isfinite(args.interval) or args.interval <= 0
                    or hashlib.sha256(self.path.read_bytes()).hexdigest() != generation
                ):
                    raise ValueError("master profile changed or no longer configures a resident master")
                if registration_scope(args) != (daemon.machine, daemon.env):
                    raise ValueError("live master profile cannot change its machine or environment")
            selected = original_target(root, running)
            if generation != self.generation:
                selected = selected or slot_python(root, read_current_version(root))
            daemon._self_update_argv = arguments
            self.next_generation = generation
            return selected

        def spawn(python, arguments):
            _spawn_self_update_successor(python, arguments, profile_binding=self.environment())

        daemon._self_update_stale_target = target
        daemon._self_update_spawn = spawn
        daemon._master_profile = self


def take_master_profile(arguments=None):
    profile = os.environ.pop(PROFILE_ENV, None)
    generation = os.environ.pop(GENERATION_ENV, None)
    owner = os.environ.pop(OWNER_ENV, None)
    if owner not in (None, "source-primary") or (not profile and (owner is not None or generation is not None)):
        raise ValueError("master profile argument owner is unavailable")
    return MasterProfile(profile, generation, arguments, source_primary=owner is not None) if profile else None


def configure_master_profile(daemon, binding):
    if binding is not None:
        binding.configure(daemon)
