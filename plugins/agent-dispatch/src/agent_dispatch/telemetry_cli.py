"""Native offline retained-telemetry configuration command."""

from plugin_activation.state import write_json_object_atomic
from process_telemetry.retained_config import execute, register_commands
from single_instance_lease.operation import OperationLock

from .telemetry import SINK_ENV_VAR, _default_config_path


def run(args):
    return execute(
        args, target=_default_config_path, service="agent-dispatch", sink_env=SINK_ENV_VAR,
        lock_factory=OperationLock, write_atomic=write_json_object_atomic,
    )


def register(sub):
    register_commands(sub, run)
