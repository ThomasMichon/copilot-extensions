"""Profile binding retained by the existing master handoff, never its workers."""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import sys

from .supervisor_lane_status import GENERATION_ENV, PROFILE_ENV, profile_arguments, settings_identity


class MasterProfile:
    def __init__(self, path, generation, arguments=None):
        self.path = Path(path)
        self.generation = generation or hashlib.sha256(self.path.read_bytes()).hexdigest()
        if not self.path.is_absolute() or hashlib.sha256(self.path.read_bytes()).hexdigest() != self.generation:
            raise ValueError("master profile binding is no longer current")
        self.next_generation = self.generation
        self.arguments = list(sys.argv[1:] if arguments is None else arguments)
        self.identity = settings_identity(self.arguments)

    def environment(self):
        return {PROFILE_ENV: str(self.path), GENERATION_ENV: self.next_generation}

    def configure(self, daemon):
        from .supervisor_registration import _spawn_self_update_successor

        original_target = daemon._self_update_stale_target

        def target(root, running):
            from . import __main__ as cli
            from .registrar_discovery import _parse_env_file
            from .self_update import read_current_version, slot_python
            from .supervisor_registration import registration_scope

            selected = original_target(root, running)
            generation = hashlib.sha256(self.path.read_bytes()).hexdigest()
            arguments = list(self.arguments)
            if generation != self.generation:
                arguments = profile_arguments(_parse_env_file(self.path, strict=True))
                try:
                    args = cli.build_parser().parse_args(arguments)
                except SystemExit as error:
                    raise ValueError("master profile arguments are invalid") from error
                if (
                    arguments[:2] != ["supervise", "serve"]
                    or args.once or args.no_single_instance
                    or not math.isfinite(args.interval) or args.interval <= 0
                    or hashlib.sha256(self.path.read_bytes()).hexdigest() != generation
                ):
                    raise ValueError("master profile changed or no longer configures a resident master")
                if registration_scope(args) != (daemon.machine, daemon.env):
                    raise ValueError("live master profile cannot change its machine or environment")
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
    return MasterProfile(profile, generation, arguments) if profile else None


def configure_master_profile(daemon, binding):
    if binding is not None:
        binding.configure(daemon)
