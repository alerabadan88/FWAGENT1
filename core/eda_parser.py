"""Parsers that turn hardware input files into a validated :class:`PCBAnalysis`.

Only the JSON config format is supported so far. Netlist formats (Altium/KiCad)
are not implemented yet — see :func:`parse_netlist_file`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.exceptions import EDAParseError
from core.hardware_model import MCU, InterfaceType, PCBAnalysis, Sensor

# Config files describe RAM as "sram_kb"; the hardware model calls it "ram_kb".
_MCU_SPEC_FIELDS = {
    "flash_kb": ("flash_kb",),
    "ram_kb": ("sram_kb", "ram_kb"),
    "clock_mhz": ("clock_mhz",),
    "gpio_pins": ("gpio_pins",),
}


def _require(data: dict[str, Any], key: str, context: str) -> Any:
    if key not in data:
        raise EDAParseError(f"{context}: missing required field '{key}'")
    return data[key]


def _parse_mcu(data: dict[str, Any]) -> MCU:
    name = _require(data, "mcu", "config")
    if not isinstance(name, str) or not name.strip():
        raise EDAParseError("config: field 'mcu' must be a non-empty string")

    specs = _require(data, "mcu_specs", "config")
    if not isinstance(specs, dict):
        raise EDAParseError("config: field 'mcu_specs' must be an object")

    resolved: dict[str, Any] = {}
    for model_field, accepted_keys in _MCU_SPEC_FIELDS.items():
        for key in accepted_keys:
            if key in specs:
                resolved[model_field] = specs[key]
                break
        else:
            raise EDAParseError(
                f"config.mcu_specs: missing required field "
                f"'{accepted_keys[0]}' (for MCU '{name}')"
            )

    try:
        return MCU(
            name=name,
            family=_require(data, "mcu_family", "config"),
            voltage=_require(data, "power_supply_voltage", "config"),
            **resolved,
        )
    except EDAParseError:
        raise
    except Exception as exc:
        raise EDAParseError(f"config: invalid MCU definition for '{name}': {exc}") from exc


def _parse_sensor(raw: dict[str, Any], index: int) -> Sensor:
    context = f"config.sensors[{index}]"
    if not isinstance(raw, dict):
        raise EDAParseError(f"{context}: each sensor must be an object")

    name = _require(raw, "name", context)
    interface_value = _require(raw, "interface", context)

    try:
        interface = InterfaceType(interface_value)
    except ValueError as exc:
        supported = ", ".join(member.value for member in InterfaceType)
        raise EDAParseError(
            f"{context}: unknown interface '{interface_value}' for sensor "
            f"'{name}' (supported: {supported})"
        ) from exc

    # A sensor names its connection in one of four ways depending on interface.
    pins = raw.get("pins")
    if pins is None and "pin" in raw:
        pins = {"pin": raw["pin"]}

    try:
        return Sensor(
            name=name,
            type=_require(raw, "type", context),
            interface=interface,
            bus=raw.get("bus") or raw.get("port"),
            address=raw.get("address"),
            pins=pins,
            required=raw.get("required", True),
        )
    except EDAParseError:
        raise
    except Exception as exc:
        raise EDAParseError(f"{context}: invalid sensor '{name}': {exc}") from exc


def parse_config_dict(data: dict[str, Any]) -> PCBAnalysis:
    """Build a :class:`PCBAnalysis` from an already-decoded config mapping.

    Raises :class:`EDAParseError` for malformed input and
    :class:`~core.exceptions.HardwareValidationError` for input that parses but
    describes impossible hardware (e.g. two I2C devices at the same address).
    """
    if not isinstance(data, dict):
        raise EDAParseError("config: top level must be a JSON object")

    mcu = _parse_mcu(data)

    raw_sensors = data.get("sensors", [])
    if not isinstance(raw_sensors, list):
        raise EDAParseError("config: field 'sensors' must be an array")

    sensors = [_parse_sensor(raw, i) for i, raw in enumerate(raw_sensors)]
    return PCBAnalysis(mcu=mcu, sensors=sensors)


def parse_config_file(path: str | Path) -> PCBAnalysis:
    """Parse a JSON hardware config file from disk."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise EDAParseError(f"config file not found: {path}") from exc
    except OSError as exc:
        raise EDAParseError(f"could not read config file {path}: {exc}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EDAParseError(f"{path} is not valid JSON: {exc}") from exc

    return parse_config_dict(data)


def parse_netlist_file(path: str | Path) -> PCBAnalysis:
    """Parse an Altium/KiCad netlist. Not implemented yet."""
    raise NotImplementedError(
        "Netlist parsing is not implemented yet; only JSON config input is supported. "
        "Use parse_config_file()."
    )
