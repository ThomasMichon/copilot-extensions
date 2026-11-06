# Synchronous and asynchronous imports

Importing `agent_procutil` or its synchronous exports does not import `asyncio`.
The synchronous helpers remain usable by minimal bundles that carry only the
package initializer.

`spawn_in_kill_on_close_job` is a lazy package export backed by `_async.py`.
Requesting that export loads `asyncio`; invoking the helper retains the same
subprocess creation, suspended-child assignment, resume and failure behavior.
Its name, signature, annotations, coroutine identity and public module identity
remain stable. Runtime type-hint resolution uses the public `JobHandle` class.

The package's `asyncio` attribute remains available on demand. Async subprocess
creation reads that attribute and the package's platform/Job helper attributes,
so existing package-level monkeypatches continue to apply.

`__all__` is unchanged and `dir(agent_procutil)` includes lazy exports without
loading them. Star imports and introspection that requests each attribute load
the async implementation; inspecting only the module dictionary does not
materialize lazy exports.

Release materialization copies the complete canonical package, including
`_async.py`, through the normal shared-library tooling. This import boundary
reduces synchronous import work; it does not establish a startup latency bound.
