# fw-automation-agent

Takes a hardware description (JSON config today, EDA netlists later) and generates, builds, and verifies embedded firmware.

## Status

| Module | State |
|---|---|
| `core/` | Done — hardware model, JSON config parser, SQLite parts catalog |
| `codegen/` | Done for AVR — real per-sensor drivers plus UART reporting |
| `services/` | Toolchain, driver registry/fetcher, build service, and simulator test service — all done |
| `agents/` | Not started |
| `api/` | Not started |
| `docs/` | Fumadocs site scaffolded; page content still stubs |

106 tests, all passing.

## Install

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"    # Windows
# .venv/bin/python -m pip install -e ".[dev]"            # Linux/macOS
```

The AVR toolchain is required for the build/codegen acceptance tests:

```bash
winget install --id ZakKemble.avr-gcc            # Windows
sudo apt install gcc-avr avr-libc                # Debian/Ubuntu
brew tap osx-cross/avr && brew install avr-gcc   # macOS
```

## Run the tests

```bash
./.venv/Scripts/python.exe -m pytest -v
```

Tests requiring `avr-gcc` skip (visibly) when it is absent instead of faking a pass. No test needs network access.

## End-to-end today

```bash
./.venv/Scripts/python.exe cli.py inspect examples/arduino-uno/config.json
./.venv/Scripts/python.exe cli.py build   examples/arduino-uno/config.json
./.venv/Scripts/python.exe cli.py verify  examples/arduino-uno/config.json
```

`verify` builds the firmware and then runs the drivers' arithmetic **on a simulated
ATmega328P** (`avr-gdb`'s instruction-set simulator), so target integer widths and
overflow behave as they will on the device:

```
Build OK   flash 5.54 %, RAM 4.98 %
  PASS  DHT22: valid frame accepted
  PASS  DHT22: humidity decodes to 65.8 %
  PASS  DHT22: temperature decodes to 26.9 C
  PASS  DHT22: corrupt checksum rejected
  PASS  HC-SR04: 1000 ticks is 686 mm
  PASS  HC-SR04: no 16-bit overflow at range
  PASS  HC-SR04: zero ticks is zero mm

7/7 checks passed on a simulated ATmega328P
```

Memory figures are measured from the real ELF by `avr-size`; the checks are real
executions, not assertions about source text.

## Honest scope

- **Only AVR / ATmega328P.** The ESP32 example in `examples/` parses fine but is rejected by codegen — generating Xtensa would need a toolchain this project cannot currently validate against.
- **Drivers are real or the part is rejected.** ADC, DHT22, and HC-SR04 have working implementations; a part with no driver raises `CodegenError` instead of emitting a stub.
- **Verified in simulation, not on hardware.** The drivers' arithmetic runs on a simulated ATmega328P, but the timing-critical paths (DHT22 bit thresholds, HC-SR04 echo timing) come from datasheets and need a scope or a real sensor to confirm.
- **The firmware reports over UART** at 9600 baud 8N1; nothing is flashed automatically yet (`avrdude` is installed but not wired up).
- **No netlist parsing yet** — `parse_netlist_file()` raises `NotImplementedError`.

## Where drivers come from

Decided: **the bare-metal drivers are written in-tree** (`codegen/templates/drivers/`). The generated firmware is bare-metal AVR, and most published DHT22/HC-SR04 libraries are Arduino C++ that would be rejected at the framework gate anyway — writing the protocols directly means no supply chain and full control, and they are short.

`services/` can still fetch, verify, and link external drivers (see `services/README.md` for the eight gates); that path is kept for libraries large enough that writing them would not pay off.

## Layout

```
core/       hardware model, parsing, catalog
codegen/    Jinja2 firmware templates + pin mapping
services/   toolchain, driver fetch/verify, build
agents/     (not started)
examples/   Arduino Uno and ESP32 sample inputs
tests/      pytest suite + fixtures
docs/       Fumadocs documentation site
```
