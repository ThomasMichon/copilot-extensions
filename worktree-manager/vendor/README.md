# Temporary dbus-next 0.2.3 wheel

**TODO: REMOVE ON 2026-10-28 OR LATER**, after confirming native UV resolves
`dbus-next==0.2.3` from the affected mirror with the seven-day cutoff intact.
Remove this directory, the `dbus-next` source mapping, and the
`_bundled_dbus_next` predicate and its two callers together.

The unchanged `dbus_next-0.2.3-py3-none-any.whl` was downloaded through the
configured governed package feed, not around it. It is the upstream wheel
published on July 25, 2021, not a rebuild. Its SHA-256 matches both the serving
feed and the canonical PyPI release metadata:

```text
58948f9aff9db08316734c0be2a120f6dc502124d9642f55e90ac82ffb16a18b
```

The affected mirror reports an October 19, 2026 upload time for those same bytes,
causing UV's seven-day filter to reject a five-year-old package. Bundling this
one verified wheel avoids that incorrect timestamp without relaxing the cutoff
for any registry dependency. The declaration audit still checks the canonical
release age. This is a temporary artifact-specific workaround for
[issue #7461](https://github.com/ThomasMichon/copilot-extensions/issues/7461),
not general local-wheel support.

Copyright (c) 2019 Tony Crisci. The upstream MIT license is retained inside
`dbus_next-0.2.3.dist-info/LICENSE` in the wheel.
