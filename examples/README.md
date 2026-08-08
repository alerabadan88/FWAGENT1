# Example inputs

Test fixtures for the pipeline described in `docs/content/docs/agents/discovery-agent.mdx`.
The discovery agent is meant to accept **one** of three input shapes — each board below
has all three so you can test whichever parser you build first:

- `config.json` — structured hardware config (matches `docs/content/api-reference/openapi.json`'s `HardwareConfig` schema, extended with the fields `discovery-agent.mdx` says it extracts: MCU specs, sensors, interfaces, power, clock, special features)
- `description.txt` — free-text hardware description, the loosest input format
- `netlist.net` — EDA/netlist-style input (`Designator=`/`PartNumber=` pairs), the format closest to real CAD export

## Boards

- `arduino-uno/` — ATmega328P, 5V, 3 simple sensors over GPIO/analog/1-Wire. Minimal case: no I2C, no RTOS needed.
- `esp32/` — ESP32-WROOM-32, 3.3V, mixed I2C/UART/1-Wire sensors, WiFi/BT on-chip. Exercises multi-bus I2C planning and the FreeRTOS-vs-bare-metal decision the design agent has to make.

## Using these

Once `core/`, `codegen/`, and `agents/` are implemented, feed one file at a time to the
discovery agent's entry point (whatever CLI/API command that ends up being) — do not
combine multiple boards or multiple formats for the same board in a single run, since
the real system expects exactly one input.
