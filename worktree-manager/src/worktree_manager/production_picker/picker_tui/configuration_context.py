"""Explicit project/workspace bindings for Configuration provider list queries."""

from .. import context


def list_context(pivot, machine: object) -> dict:
    result = {"machine": "" if machine is None else str(machine)}
    if pivot.placement == "config" and any(
        token in argument
        for argument in pivot.list_cmd
        for token in ("{repo}", "{configuration_workspace}")
    ):
        binding = context.project_bootstrap()
        result.update(
            repo=context.project(),
            configuration_workspace=binding.workspace if binding and binding.workspace else "",
        )
    return result


def run_list(runtime, machine: object, generation: object = None) -> None:
    try:
        ctx = list_context(runtime.pivot, machine)
    except RuntimeError as exc:
        runtime._finish(machine, ("error", [], str(exc)), {})
        return
    if runtime.pivot.stream:
        runtime._run_list_stream(machine, ctx)
        return
    state, rows, error, summary = runtime._exec_list(ctx)
    with runtime._lock:
        runtime._inflight.discard(machine)
        if generation is not None and generation != runtime._gen:
            return
        runtime._cache[machine] = (state, rows, error)
        runtime._summaries[machine] = summary
