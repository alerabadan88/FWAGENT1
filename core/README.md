# core

Core library: hardware data model, EDA/config parsing, and the MCU/sensor catalog. No AI logic — pure data structures, parsing, and validation.

## Status

- `exceptions.py` — done. Project-wide exception hierarchy: `FWAgentError` (base), `HardwareValidationError`, `EDAParseError`.
- `hardware_model.py` — done. Pydantic models `MCU`, `Sensor`, `PCBAnalysis`; `PCBAnalysis` validates I2C bus/address conflicts at construction time and renders to a `networkx.Graph` via `.to_graph()`.
- `eda_parser.py` — JSON config format done (`parse_config_file`, `parse_config_dict`). Netlist parsing (`parse_netlist_file`) raises `NotImplementedError` — not built yet.
- `catalog.py` — not started.

Covered by 15 tests in `tests/`, all passing against the real fixture files in `tests/fixtures/`.

## Input format notes

The JSON config format and the hardware model don't use identical field names; `eda_parser` does the mapping:

| Config file | Model |
|---|---|
| `mcu` (string) | `MCU.name` |
| `mcu_specs.sram_kb` | `MCU.ram_kb` |
| `power_supply_voltage` | `MCU.voltage` |

Sensors declare their connection one of four ways depending on interface — `bus` (I2C), `port` (UART), `pin` (single-pin GPIO/ADC), or `pins` (multi-pin, e.g. HC-SR04's trigger/echo). The parser normalizes a bare `pin` into `pins={"pin": ...}` and treats `port` as the bus identifier.

## Running the tests

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
./.venv/Scripts/python.exe -m pytest -v
```
