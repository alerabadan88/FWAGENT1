# fw-automation-agent

Takes a hardware description (JSON config today, EDA netlists later) and generates, builds, and verifies embedded firmware.

## Status

| Module | State |
|---|---|
| `core/` | Done — hardware model, JSON config parser, SQLite parts catalog |
| `codegen/` | Done for AVR — Jinja2 templates producing compilable `main.c` + `config.h` |
| `services/` | Toolchain, driver registry/fetcher, build service done; `test_service.py` not started |
| `agents/` | Not started |
| `api/` | Not started |
| `docs/` | Fumadocs site scaffolded; page content still stubs |

94 tests, all passing.

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

```python
from core.eda_parser import parse_config_file
from codegen.generator import generate_firmware
from services.build_service import BuildService

analysis = parse_config_file("examples/arduino-uno/config.json")
firmware = generate_firmware(analysis)
result = BuildService().build(firmware, analysis.mcu, "build/")

print(result.status, result.memory.flash_percent, "% flash")
# success 0.65 % flash
```

Every number there is measured from the real ELF by `avr-size`.

## Honest scope

- **Only AVR / ATmega328P.** The ESP32 example in `examples/` parses fine but is rejected by codegen — generating Xtensa would need a toolchain this project cannot currently validate against.
- **Only the ADC driver is fully implemented.** DHT22 and HC-SR04 get real init code and a read returning `SENSOR_ERR_NOT_IMPLEMENTED`, so an unwritten driver cannot be mistaken for a real reading.
- **"Compiles" is not "works."** The acceptance tests prove the generated firmware builds and links; timing-critical protocols can only be validated on physical hardware.
- **No netlist parsing yet** — `parse_netlist_file()` raises `NotImplementedError`.

## Open question: where drivers come from

`services/` can fetch, verify, and link external drivers (see `services/README.md` for the eight gates). But the generated firmware is bare-metal AVR, while most published DHT22/HC-SR04 drivers are Arduino C++ libraries — they are rejected at the framework gate. Three ways forward:

1. **Write the bare-metal drivers** into `codegen/templates/` — no supply chain, full control, and the protocols are short. Least code to trust.
2. **Switch codegen to the Arduino framework** (via `arduino-cli`) — unlocks the large Arduino library ecosystem, at the cost of a much bigger dependency and less control over generated code.
3. **Curate a small registry of bare-metal drivers** — pinned and hash-verified, as `services/driver_registry.py` already supports. Needs a real reviewed source for each part.

Nothing is decided; the machinery supports all three.

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
