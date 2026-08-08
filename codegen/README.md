# codegen

Renders firmware C source from a validated `PCBAnalysis` using Jinja2 templates.

## Status

- `pin_mapping.py` — Arduino Uno silkscreen labels (`D2`, `A0`) → AVR port/bit/ADC channel.
- `generator.py` — `generate_firmware(analysis)` returns `main.c`, `config.h`, `sensor.h`, and a `.c`/`.h` driver pair per sensor.
- `templates/drivers/` — `adc`, `dht22`, `hcsr04`.

**Scope: AVR / ATmega328P only.** Other MCU families raise `CodegenError` rather than emitting code that cannot be built — the ESP32 example in `examples/` is deliberately rejected, since generating Xtensa code would need a toolchain this generator has no way to validate against.

## Drivers are real, or the part is rejected

There are no stub drivers. Every supported part has a working implementation; a part with no driver raises `CodegenError` at generation time instead of emitting a placeholder that compiles and silently does nothing.

| Part | Implementation |
|---|---|
| Any ADC sensor | Single-ended conversion against AVcc, /128 prescaler (keeps the ADC clock at 125 kHz, inside the 50–200 kHz window the ATmega328P needs for full 10-bit accuracy). Also exposes `_read_mv()` scaled to the supply rail. |
| DHT22 / AM2302 | Full single-wire protocol: ≥1 ms start pulse, response handshake, 40-bit frame decoded by measuring each bit's high time against a 45 µs threshold, checksum verified, sign-flag handling on the temperature word. Interrupts are masked during the frame. |
| HC-SR04 | 10 µs trigger pulse, echo width timed with **Timer1** (/64 prescaler, 4 µs/tick) rather than a delay loop, so readings don't drift with compiler output. Range-checked against the 4 m datasheet ceiling. |

Failures are typed (`SENSOR_ERR_TIMEOUT`, `SENSOR_ERR_CHECKSUM`, `SENSOR_ERR_OUT_OF_RANGE`, `SENSOR_ERR_ARG`) and a failed read leaves the caller's value untouched — no placeholder data ever reaches the caller.

### Hardware constraints the generated code imposes

- **The HC-SR04 driver owns Timer1.** Don't also use it for PWM or a system tick.
- **The DHT22 driver masks interrupts** for the ~5 ms of a frame, and must not be called from an ISR.
- The DHT22 needs ~2 s between reads; `LOOP_PERIOD_MS` defaults to 2000 for that reason.
- Consecutive sensor reads are separated by `SENSOR_SETTLE_MS` (60 ms) so the ultrasonic burst decays before the next trigger.

## Pin mapping

Real Arduino Uno mapping, verified in `tests/test_pin_mapping.py`:

- `D0`–`D7` → `PORTD` bits 0–7
- `D8`–`D13` → `PORTB` bits 0–5
- `A0`–`A5` → `PORTC` bits 0–5, ADC channels 0–5

Labels are case-insensitive and normalized to uppercase.

## Verification

Generation is not considered correct because it produced text. The acceptance tests in `tests/test_generator.py` run every generated `.c` through the real `avr-gcc`:

- each file must pass `-Wall -Wextra -Werror` with **zero diagnostics**
- the whole set must link into a real `.elf`

Both tests `skipif` avr-gcc is absent rather than faking a pass. See `services/README.md` for toolchain install instructions.

**"Compiles clean" is still not "works on hardware."** The timing-critical paths (DHT22 bit thresholds, HC-SR04 echo timing) are written to datasheet figures and verified only by the compiler; confirming them needs a scope or a physical sensor.
