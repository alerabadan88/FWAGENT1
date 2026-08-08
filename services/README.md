# services

Real invocation of external tools — compilers, downloads, builds. Nothing here simulates a result.

## Status

- `toolchain.py` — done. `AvrToolchain` wraps a real `avr-gcc`: `check_syntax()`, `compile_to_elf()`, `section_sizes()` (via `avr-size`), `version()`.
- `driver_registry.py` — done. Pinned `DriverSpec` entries + `check_compatibility()` static gates.
- `driver_fetcher.py` — done. Download, SHA-256 verify, safe extract, install.
- `build_service.py` — done. Builds firmware (+ fetched drivers) and reports **measured** memory usage.
- `test_service.py` — not started.

## Toolchain requirement

```bash
# Windows
winget install --id ZakKemble.avr-gcc
# Debian/Ubuntu
sudo apt install gcc-avr avr-libc
# macOS
brew tap osx-cross/avr && brew install avr-gcc
```

If absent, `AvrToolchain()` raises `ToolchainNotFoundError` carrying these instructions. It never reports a build it did not perform.

### Note on PATH

On Windows, winget adds the toolchain's `bin/` to the *persistent* user PATH, but already-running shells keep their old environment — so `avr-gcc` can be correctly installed yet invisible to `shutil.which()`. `find_executable()` therefore falls back to globbing the winget package directory.

## How drivers are verified

"Find a driver and make sure it's the right one" is not something an agent can settle by reading code and saying it looks fine. The gates are deterministic and ordered; each must pass before the next runs:

| # | Gate | Where | What it proves |
|---|---|---|---|
| 1 | Framework match | `check_compatibility` | An Arduino library will not be fed to a bare-metal build |
| 2 | License allowlist | `check_compatibility` | We may legally vendor it |
| 3 | Declares source files | `check_compatibility` | There is actually something to compile |
| 4 | `https://` or `file://` only | `check_compatibility` | No plaintext-HTTP supply chain |
| 5 | **SHA-256 matches the pinned value** | `fetch_driver` | The bytes are exactly the reviewed ones |
| 6 | No archive entry escapes the install dir | `fetch_driver` | A malicious zip cannot write outside `vendor/` |
| 7 | Declared sources exist after extraction | `fetch_driver` | The archive matches its own manifest |
| 8 | **It compiles and links** | `BuildService.build` | The only real proof it fits this firmware |

Gates 1–4 run *before* anything is downloaded. On a checksum mismatch the archive is deleted and the install directory removed, so a rejected driver cannot be picked up by a later build.

Everything is **pinned** — a fixed version, URL, and hash. No entry resolves "latest", because an unpinned dependency can change contents between builds.

### The framework problem, concretely

The firmware `codegen/` emits is bare-metal AVR (`avr/io.h`, `util/delay.h`). Most published DHT22/HC-SR04 drivers are Arduino C++ libraries needing `Arduino.h`, `digitalWrite()`, `pulseIn()` — and the installed toolchain has no Arduino core. Those drivers are rejected at gate 1 rather than failing confusingly at link time:

```
DHT_sensor_library 1.4.6: framework mismatch: driver targets arduino,
build targets bare-metal-avr
```

This is a real constraint on what "download the drivers" can mean here — see the open question in the root `README.md`.

## Memory reporting

`MemoryReport` figures come from `avr-size` reading the built ELF:

- `flash_used = .text + .data`, `ram_used = .data + .bss`
- percentages are against the MCU's real capacity from the parsed config
- `fits` is a genuine overflow check

A build that fails reports `status="failed"` with the compiler's real diagnostics and `memory=None`. There is no code path that produces a memory number without an ELF to measure.

## Tests

Driver tests use a committed fixture archive over `file://`, so the suite needs no network. Tests needing the compiler are `skipif`-marked on its absence — they skip loudly rather than fabricating a pass.
