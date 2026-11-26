# Opt-in Windows native-crypto long-prefix regression

Run the command below from the repository root.

Prepare a disposable numbered Vault SSH runtime through the canonical
Source/profile constructor at a long physical prefix. Set
`COPILOT_EXTENSIONS_TEST_LONG_PREFIX_VAULT_PYTHON` to that fixture's absolute
`.venv\Scripts\python.exe`, then explicitly run
`tools/run-tests.ps1 agent-vault --test-file tests/native/test_native_crypto_long_prefix.py --allow-explicit-tiers --subsuite-timeout 120 --plugin-timeout 180`.
This native file is excluded from default discovery and required unit CI.
An absent selection skips; an unavailable, short-prefix or wrong-origin runtime
fails. All DLL filenames must exceed the ordinary Win32 threshold before both
fresh consumers run through ordinary and extended package-path spellings.
The controls retain byte-preservation checks without copying DLLs, creating key
files or starting a service. The fixture owner retains runtime cleanup; the
runner owns each bounded child. This is native loading evidence, not service
lifetime or the complete installed setup journey.

See the [Vault references](../../README.md#architecture-and-references) and
[repository testing guide](../../../../TESTING.md).
