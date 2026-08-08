# codegen

Renders firmware C source from a validated `PCBAnalysis` using Jinja2 templates.

## Status

- `pin_mapping.py` — done. Arduino Uno silkscreen labels (`D2`, `A0`) → AVR port/bit/ADC channel.
- `generator.py` — done for AVR. `generate_firmware(analysis)` returns `main.c` + `config.h`.
- `templates/` — `main.c.j2`, `config.h.j2`.

**Scope: AVR / ATmega328P only.** Other MCU families raise `CodegenError` rather than emitting code that cannot be built — the ESP32 example in `examples/` is deliberately rejected, since generating Xtensa code would need a toolchain this generator has no way to validate against.

## What the generated code actually does

Honest accounting of the emitted firmware, because "it compiles" is not the same as "it works":

| Sensor kind | Init | Read |
|---|---|---|
| ADC (e.g. LDR) | Real — sets pin to high-Z input | **Real** — full ADC conversion (`ADMUX`/`ADCSRA`, busy-wait, returns `ADC`) |
| Ultrasonic (HC-SR04) | Real — trigger as output, echo as input | Stub returning `SENSOR_ERR_NOT_IMPLEMENTED` |
| Single-wire (DHT22) | Real — input with pull-up | Stub returning `SENSOR_ERR_NOT_IMPLEMENTED` |

Unimplemented drivers return an explicit `SENSOR_ERR_NOT_IMPLEMENTED` status rather than a placeholder value, so a caller cannot mistake an unwritten driver for a real reading. The timing-critical protocols (DHT22's 40-bit frame, HC-SR04's echo pulse width) are not written yet.

## Pin mapping

Real Arduino Uno mapping, verified in `tests/test_pin_mapping.py`:

- `D0`–`D7` → `PORTD` bits 0–7
- `D8`–`D13` → `PORTB` bits 0–5
- `A0`–`A5` → `PORTC` bits 0–5, ADC channels 0–5

Labels are case-insensitive and normalized to uppercase.

## Verification

Generation is not considered correct because it produced text. The acceptance tests in `tests/test_generator.py` run the generated `main.c` through the real `avr-gcc`:

- `-fsyntax-only` must pass with **zero diagnostics** (warnings included)
- the source must link into a real `.elf` on disk

Both tests `skipif` avr-gcc is absent rather than faking a pass. See `services/README.md` for toolchain install instructions.
