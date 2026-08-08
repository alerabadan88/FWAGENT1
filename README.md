# fw-automation-agent

Takes a hardware description (JSON config today, EDA netlists later) and generates, builds, and verifies embedded firmware.

## Status

| Module | State |
|---|---|
| `core/` | Done — hardware model, JSON config parser, SQLite parts catalog |
| `codegen/` | Done for AVR — Jinja2 templates producing compilable `main.c`, `config.h`, and real per-sensor drivers |
| `services/` | Toolchain, driver registry/fetcher, build service done; `test_service.py` not started |
| `agents/` | Not started |
| `api/` | Not started |
| `docs/` | Fumadocs site scaffolded; page content still stubs |

97 tests, all passing.

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
# success 3.22 % flash
```

Every number there is measured from the real ELF by `avr-size`.

## Honest scope

- **Only AVR / ATmega328P.** The ESP32 example in `examples/` parses fine but is rejected by codegen — generating Xtensa would need a toolchain this project cannot currently validate against.
- **Drivers are real or the part is rejected.** ADC, DHT22, and HC-SR04 have working implementations; a part with no driver raises `CodegenError` instead of emitting a stub.
- **"Compiles" is not "works."** The acceptance tests prove the generated firmware builds and links; timing-critical protocols can only be validated on physical hardware.
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
