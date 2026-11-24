# Native-only command evidence consumers

Parent: [Payload Invocation](../README.md).
The [native provider](../../process-telemetry/README.md#native-error-only-support)
owns writing and custody. This adapter owns only publication of its resource
closure for payloads that do not declare an Invocation command or Python runtime.

## Declaration

A plugin may add `native-command-errors.json` beside its `plugin.json`:

```json
{
  "version": 1,
  "consumer": "example-node-cli",
  "outputDir": "scripts/command-errors",
  "causes": [
    {
      "code": "consumer-request-refused",
      "message": "The selected consumer request was refused"
    }
  ]
}
```

`version`, `consumer` and `outputDir` are required; `causes` is optional.
Unknown properties, duplicate keys and unsupported versions refuse publication.
The identity must match both the canonical source and copied plugin manifest.
The declaration's bytes must match its canonical source. Version stamping may
change the plugin version without changing that identity.

`outputDir` is a normalized plugin-owned relative POSIX path, not a path to an
installed plugin, a cache search or a machine configuration directory. Parent
traversal, absolute/drive paths, redirected targets, foreign hard links,
nonordinary files and competing output ownership refuse before writes.

Optional cause entries are **committed producer declarations**, not runtime
message templates. At most 16 may extend the provider catalog, with at most 32
combined rows. They cannot redefine a provider cause. Codes are bounded
lowercase identifiers; messages are bounded ASCII text without quotes,
backslashes or control characters. This preserves producer-safe classifications
without accepting exception values, paths, prompts, arguments or streams.

## Published closure and selection

The existing candidate/root and single-consumer materializers use the trusted
producer, not Python code supplied by the copied consumer. They publish all five
native resources together:

```text
<outputDir>/<bundle>/command-errors.sh
<outputDir>/<bundle>/command-errors.ps1
<outputDir>/<bundle>/private-paths.sh
<outputDir>/<bundle>/private-paths.ps1
<outputDir>/<bundle>/command-causes.tsv
<outputDir>/index.json
```

The catalog includes any declared static extensions. The other resources are
the selected provider's exact bytes. `bundle` is SHA-256 of compact, sorted-key
UTF-8 JSON mapping resource names to their SHA-256 digests.

`index.json` uses contract
`schema: "copilot-extensions.native-command-errors"`, `version: 1`,
the `consumer` identity, `bundle`, and `files` mapping all five names to digests.
All bundle files are verified before the index is atomically replaced.
An interrupted or invalid write leaves an existing selection unchanged.
Old bundles and unrelated files are not removed.

Checksums bind the selected resource closure and detect stale or partial
publication. They are not actor authentication, permission or a new trust system.
The existing source, installation and publication owners retain those gates.

## Consumer behavior and rollout

The consumer binds its own declared payload-relative index through its actual
entry/module location, then resolves the selected absolute helper path.
It validates supported schema, consumer, the five-file closure and digests before
execution. It must not infer capability from a package version, borrow another
plugin's directory, provision an interpreter, wrap application streams or replay
an application effect after a logging failure.

An unavailable or unsupported optional diagnostic binding preserves the primary
command's existing result and protocol and reports evidence availability through
that command's existing diagnostic seam. A native helper's PID describes the
emitter, not a remote control target or another process to terminate.

Expand and release the **trusted materializer first**. Promotion may execute
tooling from protected `main` against newer source data; merely putting a new
declaration and its compiler in the same source PR does not establish that the
executing publisher understands it. A consumer declaration is published only
after the containing materializer release is verified. Required CI, review and
actual installed qualification remain separate gates.
