# Plugin projection: settings and launch arguments

These worked examples define the projection acceptance contract, not
instructions to edit a running agent's settings by hand or a claim that every
launch adapter is integrated. Selection, payload availability, registration and
launch are separate steps. Required pre-start payload preparation and local
reflection precede settings publication. Settings precede productive process
startup; native repo-scoped Git acquisition may complete during that startup.
No task is delivered until the effective plugin selection is confirmed ready.
The active effort records which integration and readiness gates remain open.

The self-contained [host/child projection atlas](plugin-projection-atlas.html)
provides a relationship matrix, an authority decision flow and a same-repository
worktree diagram. Open the HTML file locally; it needs no server, script or
external asset.

## Source-preservation rule

The declaration in the controller determines the source kind. An installed
directory is not, by itself, permission to change that declaration.

| Controller reference | Target-local declaration | ACP directory argument |
|---|---|---|
| Local path, same execution world | The same original local source | That original payload directory |
| Local path, remote execution world | Local source pointing at the reflected copy | The reflected payload directory |
| Git source | The same Git origin, revision and plugin location | The successfully installed payload directory in the target world |
| Absolute local path to a target-owned plugin | Local-marketplace source relative to the actual target checkout | The plugin inside that target checkout |

The last row is the deliberate relocation exception. A local checkout's Git
remote does not convert an authored local path into a Git source. Conversely,
a Git-sourced plugin does not become directory-sourced merely because it has
been installed. `--plugin-dir` always names a usable filesystem directory,
including for a plugin whose settings retain a Git source.

Enablement is repository-scoped. These examples do not authorize global
`copilot plugin install` or marketplace-registration commands that would change
other repositories' effective plugins. Native repository-declared auto-install
may populate shared installation storage while keeping the plugin disabled
outside the declaring repository. Preparation must preserve that distinction.

## Which checkout receives the settings?

The destination is the **resolved execution checkout**, not the controller's
checkout, the daemon's working directory, or an assumed repository anchor.

| Launch owner | Settings destination | Automatically supplied plugin directories |
|---|---|---|
| Ordinary static-registry local ACP and ordinary local ACP acquisition | Actual allocated local checkout, or the explicit existing checkout | Every effective prepared plugin |
| Native local CLI acquisition with preparation-capable owner | Actual allocated checkout; compiled receipt bound to the native session | None for identities already registered through settings; owned task waits for native registration |
| Trusted Codespace ACP | Actual resolved checkout inside the Codespace | Every effective prepared plugin |
| Trusted container ACP | Actual resolved checkout inside the container | Every effective prepared plugin |
| Existing headed Codespace/container CLI owner | That same prepared venue checkout | None for identities already registered through settings |
| Static SSH ACP | Actual remote checkout, prepared through the existing SSH owner | Every effective prepared plugin |
| Supported headed SSH CLI | Actual remote anchor, prepared through `agent-ssh copilot` before native launch | Merged repository settings; seed admission uses the complete compiled selection, not duplicate directory flags |
| Windows privileged ACP relay | Actual receiver-owned allocated checkout, using negotiated privileged context | Every effective prepared plugin; live elevation acceptance remains separate |
| Wrapped or receiver-local integration | Separate integration work; do not infer preparation from a successful connection | Not established by the ordinary local adapters |
| Restricted container | Automatic controller projection is refused | No automatic projection |

The file is always `<execution-checkout>/.github/copilot/settings.local.json`.
Copilot merges it over that checkout's `settings.json`. Unrelated settings and
explicit target-local choices remain intact. Cache and receipt paths belong
under the checkout's actual Git metadata, which can be outside the checkout
for a linked worktree.

## Reading the examples

Names and paths below are synthetic. `/example/controller-feature` is the
calling controller worktree and `/example/app` is the callee's checkout.
Windows preparation writes corresponding resolved Windows paths; JSON escapes
their backslashes. Relative authored paths are relative to the settings owner's
repository, not to the controller.

The argument lists show only plugin-directory arguments. Existing ACP flags,
model preferences and other arguments are omitted. Configured or request-owned
arguments are preserved separately; preparation does not silently remove an
explicit `--plugin-dir` supplied by the operator.

Angle-bracketed digest slots explain generated identities and paths; they are
not literal settings or arguments to copy into a launch. The preparation result
supplies those values. Each JSON block shows the plugin-related settings;
unrelated input settings are retained.

### 1. Controller plugin, different local execution checkout

The controller's `control-tools` catalog contains `app-agent`. Its related-repo
selection enables `app-agent@control-tools` for `app`. The callee has no plugin
declarations or overrides.

The selected local payload is:

```text
/example/controller-feature/.ai/app-agent
```

The callee's generated `settings.local.json` is:

```json
{
  "extraKnownMarketplaces": {
    "control-tools": {
      "source": {
        "source": "directory",
        "path": "/example/controller-feature/.ai"
      }
    }
  },
  "enabledPlugins": {
    "app-agent@control-tools": true
  }
}
```

The automatic ACP argument set is:

```json
[
  "--plugin-dir",
  "/example/controller-feature/.ai/app-agent"
]
```

Nothing is copied, and no catalog is written into the controller. The payload
comes from the calling worktree, not an older controller anchor. Repeating
preparation must not turn this reference into a snapshot.

If the controller also has an unselected `app-agent-debug` plugin, it contributes
neither an enabled entry nor a directory argument. A companion declaration does
not independently select it. A harness-only plugin is not made eligible merely
because it shares the catalog.

### 2. The callee explicitly chooses or disables the same identity

The controller still selects `app-agent@control-tools`, but the callee owns
another `control-tools` catalog at `/example/app/.ai`, containing its own
`app-agent`. Its existing **target-local** settings are:

```json
{
  "extraKnownMarketplaces": {
    "control-tools": {
      "source": {
        "source": "directory",
        "path": "./.ai"
      }
    }
  },
  "enabledPlugins": {
    "app-agent@control-tools": true
  },
  "unrelatedPreference": "keep"
}
```

Preparation retains that explicit origin and preference. It does not replace
the origin with the controller's catalog. The effective automatic ACP arguments
are:

```json
[
  "--plugin-dir",
  "/example/app/.ai/app-agent"
]
```

If the target-local enabled value is instead `false`, it stays `false` and the
automatic argument set for this selection is:

```json
[]
```

This does not authorize deleting a separately authored command-line argument.
An explicit operator flag is not an automatic projection entry.

#### Same repository, different worktrees

When a controller worktree drives another worktree of the **same repository**,
the child's own marketplace declaration is still target-native authority.
For example, both worktrees may declare `control-tools` at `./.ai`, while
their `app-agent` payloads contain different revisions. The child resolves
`./.ai` against its own execution checkout and receives
`<child-worktree>/.ai/app-agent`, never the parent's payload just because the
parent initiated the task. The repository anchor is not a substitute either.

The controller can still supply eligible plugins from other marketplaces that
the child does not override. Same-repository delegation does not mean copying
the parent's whole enabled stack. A child's explicit disablement remains
authoritative, and an enabled but missing child-local payload is an error, not
permission to use the parent's revision. A separately authored external
target-local source remains an explicit operator choice, not an automatically
rebased path.

The linked-worktree regression in
[`test_sibling_worktree_plugins.py`](../tests/test_sibling_worktree_plugins.py)
creates different committed parent/child revisions and exercises ordinary ACP,
the CLI acquisition preparation request, copy-mode preparation, and an
independent on-disk settings consumer. Repeat preparation observes child edits
without snapshotting the parent. This is source-consumer evidence, not a new
live Copilot, remote-SSH or container acceptance claim.

### 3. The same controller-local plugin in a remote venue

The selection is the same as example 1, but execution is in a trusted Codespace
or container at `/workspaces/app`. The controller's filesystem is not available
there. For this example, the checkout's Git metadata is `/workspaces/app/.git`.

The generated local marketplace contains the reflected selected payload. The
target settings refer to that marketplace, not to the host path:

```json
{
  "extraKnownMarketplaces": {
    "control-tools": {
      "source": {
        "source": "directory",
        "path": "/workspaces/app/.git/copilot-venue-plugins/marketplaces/control-tools"
      }
    }
  },
  "enabledPlugins": {
    "app-agent@control-tools": true
  }
}
```

The automatic ACP arguments identify the prepared snapshot's actual payload:

```json
[
  "--plugin-dir",
  "/workspaces/app/.git/copilot-venue-plugins/marketplaces/control-tools/snapshots/<payload-sha256>/.ai/app-agent"
]
```

`<payload-sha256>` is an explanatory slot for the digest returned by preparation,
not a literal path component or a variable Copilot expands. An actual launch
contains the full resolved path. A linked worktree uses its real Git metadata
directory instead of assuming `.git` is a directory inside the checkout.

The local source remains local even if the controller worktree has a Git remote.
Bridge does not substitute that remote for the chosen worktree's files.

### 4. A Git-sourced plugin remains Git-sourced

The controller selects `helper@shared-tools` from this declaration:

```json
{
  "extraKnownMarketplaces": {
    "shared-tools": {
      "source": {
        "source": "github",
        "repo": "example/shared-tools"
      }
    }
  }
}
```

The target's corresponding projection retains that source:

```json
{
  "extraKnownMarketplaces": {
    "shared-tools": {
      "source": {
        "source": "github",
        "repo": "example/shared-tools"
      }
    }
  },
  "enabledPlugins": {
    "helper@shared-tools": true
  }
}
```

Any supported revision or plugin-location fields in the effective input remain
part of the declaration and acquisition requirements. Merely writing those
fields into a receipt does not establish that the requested revision was fetched.

For example, an explicit Git URL and non-default branch retain this same
marketplace value in the target settings:

```json
{
  "source": {
    "source": "git",
    "url": "https://example.org/shared-tools.git",
    "ref": "release-tools"
  }
}
```

If that revision's catalog locates `helper` under `./packages/helper`, acquisition
must use that catalog entry, not a same-named plugin at the repository root or
the default branch's entry. The catalog's plugin-relative location is not an
installation-cache path to substitute into `extraKnownMarketplaces`.

Suppose scoped native preparation verifies the executing profile's payload at
`/home/agent/.copilot/installed-plugins/shared-tools/helper`. ACP receives:

```json
[
  "--plugin-dir",
  "/home/agent/.copilot/installed-plugins/shared-tools/helper"
]
```

The installed location is **not** substituted for the Git source in settings.
The CLI argument set for the same settings-driven selection has no automatic
`--plugin-dir` additions.

Preparation must not globally enable `helper@shared-tools` or add a user-global
marketplace as a workaround. Starting with no global enablement, another
repository using this same profile but declaring no such selection must not
load the helper. Sharing installed files is not sharing activation.

Native scoped acquisition may complete during startup, but writing Git
declarations or starting the process is not evidence of readiness. Task delivery
waits for confirmation of the complete selection. A mode supplying explicit
directories must have usable paths when it supplies those arguments.

### 5. Absolute controller reference to a target-owned plugin

The controller has an absolute local reference to the target repository's
catalog at `/example/app-anchor/.ai`. That catalog defines `app-agent@app-tools`.
The ownership binding establishes that this is the same target repository,
whose actual execution checkout is `/workspaces/app`.

The target projection is relative to that checkout:

```json
{
  "extraKnownMarketplaces": {
    "app-tools": {
      "source": {
        "source": "directory",
        "path": "./.ai"
      }
    }
  },
  "enabledPlugins": {
    "app-agent@app-tools": true
  }
}
```

The automatic ACP arguments are:

```json
[
  "--plugin-dir",
  "/workspaces/app/.ai/app-agent"
]
```

There is no reference to `/example/app-anchor` and no second reflected copy.
The same-machine version resolves under the actual local execution worktree.
The target's catalog determines its plugin directory; the controller's old
payload path is not blindly appended to the new checkout.

A matching basename or a similar path is not ownership evidence. If the plugin
instead belongs to an unrelated controller repository, examples 1 or 3 apply.
A Git declaration does not take this local-path exception merely because it
names the target repository's Git remote.

### 6. Same marketplace name, different physical local catalogs

In this same-execution-world example, the callee is `/example/app`. Its existing
local settings register `control-tools` at `./.ai` and
enable `native-helper@control-tools`. That catalog does not contain `app-agent`.
The controller's different `control-tools` catalog supplies the selected
`app-agent`.

The target keeps its original catalog. The controller receives a separate
operation-owned local registration:

```json
{
  "extraKnownMarketplaces": {
    "control-tools": {
      "source": {
        "source": "directory",
        "path": "./.ai"
      }
    },
    "control-tools-venue-<origin-digest>": {
      "source": {
        "source": "directory",
        "path": "/example/controller-feature/.ai",
        "manifestName": "control-tools"
      }
    }
  },
  "enabledPlugins": {
    "native-helper@control-tools": true,
    "app-agent@control-tools-venue-<origin-digest>": true
  }
}
```

The alias digest is computed by preparation, not supplied literally.
`manifestName` is inside `source` and identifies the unchanged source catalog.
The operation's receipt maps logical `app-agent@control-tools` to its projected
identity. That mapping alone does not select or enable another plugin.

The effective automatic ACP directory set is:

```json
[
  "--plugin-dir",
  "/example/controller-feature/.ai/app-agent",
  "--plugin-dir",
  "/example/app/.ai/native-helper"
]
```

The order is not additional selection authority. Both physical origins must
survive preparation; neither source catalog is rewritten. An explicit disable
of the logical controller selection must disable its projection, and an
operator's explicit alias disable or origin change must not be overwritten.

### 7. Existing installed inventory without a repository catalog

A legacy local ACP selection may already have a valid installed payload at
`/home/agent/.copilot/installed-plugins/installed-tools/helper`, while the target
has no marketplace declaration for `installed-tools`.

The target-local enablement is:

```json
{
  "enabledPlugins": {
    "helper@installed-tools": true
  }
}
```

ACP can receive the verified existing payload:

```json
[
  "--plugin-dir",
  "/home/agent/.copilot/installed-plugins/installed-tools/helper"
]
```

This preserves the existing explicit-directory ACP fallback; it is not proof
that a manifest-only directory is a native CLI installation. If a grounded source
declaration exists in the selected execution profile, preparation can preserve
that source through the appropriate earlier example, subject to target-local
authority. It must not invent a Git origin, write a catalog into the installed
payload, or silently use the dispatcher's different profile.

## Combined selections and negative cases

For multiple eligible selections, merge their owned settings entries and emit
the union of their effective ACP payload directories. For example, examples 1
and 4 together retain one directory source and one Git source; their arguments
contain the controller payload directory and the verified native installation
directory. They do not become two directory declarations.

Apply these exclusions before calling the result ready:

| Condition | Required outcome |
|---|---|
| Explicit target-local disable | No automatic directory for that logical selection, including through an alias |
| Explicit target-local origin override | Preserve it and prepare its payload, not the overridden controller payload |
| Unselected catalog sibling or external companion | No new enablement or directory |
| Pre-start preparation failure or malformed settings | No productive child launch |
| Failed or incomplete native Git startup activation | No ready status or task delivery |
| Unrelated repository using the same profile | No activation caused by this repository's projection |
| Configured/request-owned directory flag | Preserve the authored argument; do not misclassify it as an owned projection |
