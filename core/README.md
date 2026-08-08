# core

Core library: hardware data model, EDA/config parsing, and the MCU/sensor catalog. No AI logic — pure data structures, parsing, and validation.

## Status

- `exceptions.py` — `FWAgentError` (base), `HardwareValidationError`, `EDAParseError`, `CatalogError`.
- `hardware_model.py` — Pydantic models `MCU`, `Sensor`, `PCBAnalysis`; `PCBAnalysis` validates I2C bus/address conflicts at construction time and renders to a `networkx.Graph` via `.to_graph()`.
- `eda_parser.py` — two inputs: JSON config (`parse_config_file`) and KiCad netlist (`parse_netlist_file`, `analyse_netlist_file`).
- `netlist_parser.py` — KiCad S-expression netlists: components, nets, and the MCU pin named on each connection.
- `schematic.py` — turns a netlist into a validated `PCBAnalysis`. Identifies the MCU by asking the toolchain, not by reference designator.
- `device_catalog.py` — per-part facts read from avr-gcc and avr-libc for 414 parts: memory sizes, port widths, ADC channels, which USART.
- `catalog.py` — SQLite catalog of MCU/sensor parts with real CRUD (`Catalog`, `SensorSpec`).

Covered by the suite in `tests/`, against real fixtures — including a KiCad netlist that is parsed all the way to a compiled image.

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
