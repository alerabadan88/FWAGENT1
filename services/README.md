# services

Real invocation of external tools — compilers, test runners. Nothing here simulates a build.

## Status

- `toolchain.py` — done. `AvrToolchain` wraps a real `avr-gcc`: `check_syntax()` (`-fsyntax-only`), `compile_to_elf()`, `version()`. Detection via `find_executable()` searches PATH first, then known install directories.
- `build_service.py` — not started.
- `test_service.py` — not started.

## Toolchain requirement

The AVR toolchain must be installed for anything in this module to do real work:

```bash
# Windows
winget install --id ZakKemble.avr-gcc
# Debian/Ubuntu
sudo apt install gcc-avr avr-libc
# macOS
brew tap osx-cross/avr && brew install avr-gcc
```

If it is absent, `AvrToolchain()` raises `ToolchainNotFoundError` carrying these instructions. It never reports a successful build it did not perform. Use `AvrToolchain.find()` for an optional lookup returning `None`, or `AvrToolchain.is_available()` for a boolean.

### Note on PATH

On Windows, winget adds the toolchain's `bin/` to the *persistent* user PATH, but shells already running keep their old environment — so `avr-gcc` can be correctly installed yet invisible to `shutil.which()` until a new shell is opened. `find_executable()` therefore falls back to globbing the winget package directory. The toolchain tests in `tests/test_toolchain.py` currently pass through exactly this fallback.

## Error model

- Toolchain missing → `ToolchainNotFoundError`
- Toolchain present but the *source* is bad → a normal `CompileResult` with `ok=False` and the compiler's real diagnostics in `.diagnostics`. Rejected code is an expected outcome, not an exception.
- Toolchain present but the *request* is malformed (no sources, missing file, timeout) → `CompilationError`

## Tests

Tests that need the compiler are marked `skipif` on its absence, so the suite stays runnable on machines without it — they skip loudly rather than fabricating a pass.
