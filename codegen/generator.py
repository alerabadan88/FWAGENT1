"""Render firmware C source from a validated :class:`PCBAnalysis`.

Only AVR / ATmega328P (Arduino Uno) is supported so far. Other MCU families
raise :class:`CodegenError` rather than emitting code that cannot be built.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from core.exceptions import CodegenError, FWAgentError
from core.hardware_model import InterfaceType, PCBAnalysis, Sensor

if TYPE_CHECKING:  # avoid importing the toolchain layer at module load
    from core.device_catalog import DeviceFacts
from codegen.pin_mapping import McuPin, adc_channel_for, resolve_pin, verify_pin

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
    resolved_pins: dict[str, McuPin]
    adc_channels: dict[str, int]
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


def _resolve_sensor(sensor: Sensor, device=None, board: str | None = None) -> RenderedSensor:
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

    resolved = {role: resolve_pin(label, board=board) for role, label in roles.items()}

    adc_channels: dict[str, int] = {}
    if device is not None:
        for role, pin in resolved.items():
            verify_pin(pin, device)
        if driver_kind == "adc":
            adc_channels["signal"] = adc_channel_for(resolved["signal"], device)

    return RenderedSensor(
        name=sensor.name,
        type=sensor.type,
        interface=sensor.interface,
        symbol=_c_symbol(sensor.name),
        driver_kind=driver_kind,
        resolved_pins=resolved,
        adc_channels=adc_channels,
        required=sensor.required,
    )


def _single_pin(sensor: Sensor) -> str:
    if len(sensor.pins) != 1:
        raise CodegenError(
            f"sensor '{sensor.name}' declares {len(sensor.pins)} pins; "
            f"expected exactly one"
        )
    return next(iter(sensor.pins.values()))


def _uart_settings(f_cpu_hz: int, baud: int) -> dict[str, object]:
    """Compute UBRR and report the baud error the divisor actually produces.

    The divisor is an integer, so the achieved rate is rarely exactly the
    requested one. Receivers tolerate roughly 2%; beyond that the link is
    unreliable, so it is a generation-time error rather than a surprise on
    the bench.
    """
    if baud <= 0:
        raise CodegenError(f"UART baud rate must be positive, got {baud}")

    ubrr = round(f_cpu_hz / (16 * baud)) - 1
    if ubrr < 0 or ubrr > 4095:
        raise CodegenError(
            f"baud rate {baud} is not reachable at {f_cpu_hz} Hz "
            f"(UBRR would be {ubrr}, hardware allows 0..4095)"
        )

    actual = f_cpu_hz / (16 * (ubrr + 1))
    error_percent = (actual - baud) / baud * 100.0

    if abs(error_percent) > 2.0:
        raise CodegenError(
            f"baud rate {baud} at {f_cpu_hz} Hz has {error_percent:.2f}% error "
            f"(UBRR={ubrr}, actual={actual:.0f}); most receivers need under 2%"
        )

    return {
        "uart_baud": baud,
        "uart_ubrr": ubrr,
        "uart_actual_baud": int(round(actual)),
        "uart_error_percent": f"{error_percent:.2f}",
    }


def _lookup_device(mcu_name: str) -> "DeviceFacts":
    """Get the part's real facts from the toolchain.

    Kept as a late import so `codegen` does not drag a toolchain dependency
    into callers that supply `device` themselves.
    """
    from core.device_catalog import DeviceCatalog, DeviceNotFoundError

    try:
        return DeviceCatalog().facts(mcu_name)
    except DeviceNotFoundError as exc:
        raise CodegenError(str(exc)) from exc
    except FWAgentError as exc:
        raise CodegenError(
            f"could not read the facts for '{mcu_name}' from the toolchain: {exc}. "
            f"Install avr-gcc, or pass `device=` explicitly."
        ) from exc


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
    uart_baud: int = 9600,
    device: "DeviceFacts | None" = None,
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

    device = device or _lookup_device(analysis.mcu.name)
    if not device.has_uart:
        raise CodegenError(
            f"{device.part} has no USART, so it cannot report readings over serial. "
            f"This generator has no other transport."
        )

    sensors = [
        _resolve_sensor(sensor, device=device, board=analysis.board)
        for sensor in analysis.sensors
    ]
    context = {
        "mcu": analysis.mcu,
        "sensors": sensors,
        "f_cpu_hz": f_cpu_hz,
        "loop_period_ms": loop_period_ms,
        "sensor_settle_ms": sensor_settle_ms,
        "has_single_wire": any(s.driver_kind == "single_wire" for s in sensors),
        "has_ultrasonic": any(s.driver_kind == "ultrasonic" for s in sensors),
        "has_adc": any(s.driver_kind == "adc" for s in sensors),
        "device": device,
        # Register index of the USART this part actually has -- see DeviceFacts.
        "usart": device.usart_suffix,
        **_uart_settings(f_cpu_hz, uart_baud),
    }
    # main.c declares a shared uint16_t for every driver that reports one value.
    context["has_simple_value"] = context["has_ultrasonic"] or context["has_adc"]

    env = _environment()
    files = {
        "config.h": env.get_template("config.h.j2").render(**context),
        "sensor.h": env.get_template("sensor.h.j2").render(**context),
        "uart.h": env.get_template("drivers/uart.h.j2").render(**context),
        "uart.c": env.get_template("drivers/uart.c.j2").render(**context),
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
