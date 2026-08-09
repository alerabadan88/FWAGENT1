# fw-automation-agent

Describe a board. Everything that cannot be derived from an artifact gets
asked. Out comes a Zephyr board port as a downloadable zip.

**Read [ARCHITECTURE.md](ARCHITECTURE.md) first** — it explains why the code is
shaped the way it is, which is the part that does not follow from reading it.

## The one-line version

A wrong hardware fact produces firmware that compiles, boots, and reports
plausible wrong numbers. No test catches it, because the test is generated from
the same assumption. So every value carries where it came from, and anything
that cannot be derived is a question rather than a default.

## Run the web app

```bash
pip install -e ".[web]"
uvicorn webapp.api:app --reload        # http://127.0.0.1:8000
```

Set `ZEPHYR_BASE` to a Zephyr checkout and the peripheral counts, bindings and
required properties are read from it rather than fetched.

## Status

| Module | State |
|---|---|
| `core/` | Hardware model, 414-part AVR catalog from avr-libc, evidence states |
| `agents/` | Deterministic uncertainty enumerator, normalizer, interview |
| `codegen/zephyr/` | Board port generator: bindings, SoC facts, required properties |
| `codegen/templates/drivers/` | AVR bare-metal drivers (earlier branch, working) |
| `services/` | Toolchain, build, simulator, flashing, CRA/SBOM, verifiers |
| `webapp/` | HTTP API and single-page front end, zip download |
| `docs/` | Fumadocs site |

431 tests, all passing.

### What is established

A generated board port **builds**. An nRF52840 with a DHT22 and a button,
against Zephyr v4.4.2 with the Zephyr SDK 1.0.1 ARM toolchain:

```
[178/178] Linking C executable zephyr/zephyr.elf
Memory region         Used Size  Region Size  %age Used
           FLASH:       34288 B         1 MB      3.27%
             RAM:        9120 B       256 KB      3.48%
```

`dht_api` — Zephyr's own DHT driver — is in the binary. Nothing about the
register map was asserted by this project.

### What is not

Building is not running. **Nothing has ever been on hardware**; no board has
been connected at any point, so the pin numbers, the active level and the pull
are only as good as the answers they came from. The AVR timing-critical drivers
are simulator-verified only, and the watchdog is not even that (GDB's AVR
simulator does not implement `WDR`).

## Install

```bash
pip install fw-automation-agent            # gives you the `fw-agent` command
pip install "fw-automation-agent[agent]"   # plus the interview agent
```

The Anthropic SDK is an optional extra on purpose: only the interview agent
talks to a model, and generating or building firmware never needs it.

From a checkout:

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
fw-agent chat                                      # describe a board in prose
fw-agent inspect examples/arduino-uno/config.json
fw-agent build   examples/arduino-uno/config.json
fw-agent verify  examples/arduino-uno/config.json
fw-agent ports                                     # then
fw-agent flash   examples/arduino-uno/config.json --port COM3
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
- **The firmware reports over UART** at 9600 baud 8N1 (configurable through the interview).
- **Flashing is wired up but unproven on hardware.** No board was connected to the machine this was built on; only the failure paths were exercised.
- **The interview agent has not made a live API call.** No credential was available; the deterministic half is fully tested offline.
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
