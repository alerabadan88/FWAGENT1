"""Render firmware C source from a validated :class:`PCBAnalysis`.

Only AVR / ATmega328P (Arduino Uno) is supported so far. Other MCU families
raise :class:`CodegenError` rather than emitting code that cannot be built.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from core.exceptions import CodegenError
from core.hardware_model import InterfaceType, PCBAnalysis, Sensor
from codegen.pin_mapping import AvrPin, map_arduino_uno_pin

TEMPLATE_DIR = Path(__file__).parent / "templates"

SUPPORTED_FAMILIES = {"AVR"}

# Which generated driver shape a sensor gets, keyed by the part name.
_ULTRASONIC_PARTS = {"HC-SR04"}
_SINGLE_WIRE_PARTS = {"DHT22", "AM2302"}

# Template stem backing each driver kind. Every kind has a real implementation;
# a sensor whose part has no driver is rejected rather than stubbed.
_DRIVER_TEMPLATES = {
    "adc": "adc",
    "ultrasonic": "hcsr04",
    "single_wire": "dht22",
}


@dataclass
class RenderedSensor:
    """A sensor with everything the templates need already resolved."""

    name: str
    type: str
    interface: InterfaceType
    symbol: str
    driver_kind: str
    resolved_pins: dict[str, AvrPin]
    required: bool


@dataclass
class GeneratedFirmware:
    """The source files produced for one board."""

    files: dict[str, str]

    def write_to(self, directory: str | Path) -> list[Path]:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        written = []
        for filename, content in self.files.items():
            path = directory / filename
            path.write_text(content, encoding="utf-8")
            written.append(path)
        return written


def _c_symbol(name: str) -> str:
    """Turn a part name like 'HC-SR04' into a C-identifier-safe 'HC_SR04'."""
    symbol = re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_").upper()
    if not symbol:
        raise CodegenError(f"cannot derive a C identifier from sensor name '{name}'")
    if symbol[0].isdigit():
        symbol = f"S_{symbol}"
    return symbol


def _resolve_sensor(sensor: Sensor) -> RenderedSensor:
    if sensor.interface in (InterfaceType.I2C, InterfaceType.SPI, InterfaceType.UART):
        raise CodegenError(
            f"sensor '{sensor.name}' uses {sensor.interface.value}, which the AVR "
            f"generator does not support yet (only GPIO and ADC)"
        )

    if not sensor.pins:
        raise CodegenError(f"sensor '{sensor.name}' has no pins to map")

    if sensor.name in _ULTRASONIC_PARTS:
        driver_kind = "ultrasonic"
        expected_roles = {"trigger", "echo"}
        missing = expected_roles - set(sensor.pins)
        if missing:
            raise CodegenError(
                f"sensor '{sensor.name}' is missing required pin(s): {sorted(missing)}"
            )
        roles = {role: sensor.pins[role] for role in ("trigger", "echo")}
    elif sensor.interface == InterfaceType.ADC:
        driver_kind = "adc"
        roles = {"signal": _single_pin(sensor)}
    elif sensor.name.upper() in _SINGLE_WIRE_PARTS:
        driver_kind = "single_wire"
        roles = {"signal": _single_pin(sensor)}
    else:
        raise CodegenError(
            f"no driver is implemented for part '{sensor.name}' "
            f"(implemented: {sorted(_ULTRASONIC_PARTS | _SINGLE_WIRE_PARTS)} and any ADC sensor)"
        )

    resolved = {role: map_arduino_uno_pin(label) for role, label in roles.items()}

    if driver_kind == "adc" and not resolved["signal"].is_analog_capable:
        raise CodegenError(
            f"sensor '{sensor.name}' is declared as ADC but pin "
            f"'{resolved['signal'].label}' has no ADC channel on this board"
        )

    return RenderedSensor(
        name=sensor.name,
        type=sensor.type,
        interface=sensor.interface,
        symbol=_c_symbol(sensor.name),
        driver_kind=driver_kind,
        resolved_pins=resolved,
        required=sensor.required,
    )


def _single_pin(sensor: Sensor) -> str:
    if len(sensor.pins) != 1:
        raise CodegenError(
            f"sensor '{sensor.name}' declares {len(sensor.pins)} pins; "
            f"expected exactly one"
        )
    return next(iter(sensor.pins.values()))


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=False,
        lstrip_blocks=False,
    )


def generate_firmware(
    analysis: PCBAnalysis,
    f_cpu_hz: int = 16_000_000,
    loop_period_ms: int = 2000,
    sensor_settle_ms: int = 60,
) -> GeneratedFirmware:
    """Render ``main.c`` and ``config.h`` for the given board.

    Raises :class:`CodegenError` for hardware this generator cannot target.
    """
    if analysis.mcu.family.upper() not in SUPPORTED_FAMILIES:
        raise CodegenError(
            f"MCU family '{analysis.mcu.family}' is not supported by the AVR "
            f"generator yet (supported: {sorted(SUPPORTED_FAMILIES)})"
        )

    if not analysis.sensors:
        raise CodegenError("cannot generate firmware for a board with no sensors")

    sensors = [_resolve_sensor(sensor) for sensor in analysis.sensors]
    context = {
        "mcu": analysis.mcu,
        "sensors": sensors,
        "f_cpu_hz": f_cpu_hz,
        "loop_period_ms": loop_period_ms,
        "sensor_settle_ms": sensor_settle_ms,
    }

    env = _environment()
    files = {
        "config.h": env.get_template("config.h.j2").render(**context),
        "sensor.h": env.get_template("sensor.h.j2").render(**context),
        "main.c": env.get_template("main.c.j2").render(**context),
    }

    # One driver pair per sensor, named after the sensor so two parts of the
    # same kind on one board don't collide.
    for sensor in sensors:
        stem = _DRIVER_TEMPLATES[sensor.driver_kind]
        for extension in ("h", "c"):
            template = env.get_template(f"drivers/{stem}.{extension}.j2")
            files[f"{sensor.symbol.lower()}.{extension}"] = template.render(
                sensor=sensor, **context
            )

    return GeneratedFirmware(files=files)
