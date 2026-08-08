# Example inputs

Test fixtures for the pipeline described in `docs/content/docs/agents/discovery-agent.mdx`.
The discovery agent is meant to accept **one** of three input shapes — each board below
has all three so you can test whichever parser you build first:

- `config.json` — structured hardware config (matches `docs/content/api-reference/openapi.json`'s `HardwareConfig` schema, extended with the fields `discovery-agent.mdx` says it extracts: MCU specs, sensors, interfaces, power, clock, special features)
- `description.txt` — free-text hardware description, the loosest input format
- `board.kicad.net` — a real KiCad netlist export (Arduino Uno only). This is the strongest input: it names the MCU pin on each connection, so nothing about the wiring is asked or assumed.

## Boards

- `arduino-uno/` — ATmega328P, 5V, 3 simple sensors over GPIO/analog/1-Wire. Minimal case: no I2C, no RTOS needed.
- `esp32/` — ESP32-WROOM-32. Parses fine and is then **rejected**: there is no toolchain here that can compile for it, so generating code would produce something unverifiable. Kept as the example of an honest refusal.

## Using these

Once `core/`, `codegen/`, and `agents/` are implemented, feed one file at a time to the
discovery agent's entry point (whatever CLI/API command that ends up being) — do not
combine multiple boards or multiple formats for the same board in a single run, since
the real system expects exactly one input.


## Reading the KiCad netlist

```bash
fw-agent schematic examples/arduino-uno/board.kicad.net --build
```

Note what it reports and does not silently absorb: the LDR on `PC0` is a
resistive divider, and a divider is indistinguishable from any other passive
network in a netlist. The pin is clearly in use, so it is reported as needing
its type stated rather than dropped.
