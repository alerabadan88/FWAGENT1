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
_I2C_PARTS = {"BMP280"}

# Which SDA/SCL pins the TWI peripheral is hard-wired to, per part. These
# are fixed in silicon -- not a choice -- so they are looked up, and a part
# that is not here is refused rather than assumed to match the 328P.
_TWI_PINS = {
    "atmega328p": ("PC4", "PC5"),
    "atmega168": ("PC4", "PC5"),
    "atmega8": ("PC4", "PC5"),
    "atmega2560": ("PD1", "PD0"),
    "atmega32u4": ("PD1", "PD0"),
}

# Template stem backing each driver kind. Every kind has a real implementation;
# a sensor whose part has no driver is rejected rather than stubbed.
_DRIVER_TEMPLATES = {
    "adc": "adc",
    "ultrasonic": "hcsr04",
    "single_wire": "dht22",
    "i2c_bmp280": "bmp280",
    # Built from a model-supplied profile rather than hand-written. Kept
    # distinct so nothing treats it as equally trustworthy.
    "i2c_profile": "generic_i2c",
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
    # I2C parts are addressed rather than pinned; SDA and SCL belong to the MCU.
    address: str | None = None
    # Set when the driver is built from a described register map rather than
    # a hand-verified one. Its presence means 'unverified'.
    profile: object | None = None


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


def _resolve_sensor(
    sensor: Sensor, device=None, board: str | None = None, profiles=None
) -> RenderedSensor:
    profiles = profiles or {}
    if sensor.interface == InterfaceType.I2C:
        return _resolve_i2c_sensor(sensor, device, profile=profiles.get(sensor.name))

    if sensor.interface in (InterfaceType.SPI, InterfaceType.UART):
        raise CodegenError(
            f"sensor '{sensor.name}' uses {sensor.interface.value}, which the AVR "
            f"generator does not support yet (GPIO, ADC and I2C are supported)"
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


def _resolve_i2c_sensor(sensor: Sensor, device, profile=None) -> RenderedSensor:
    """An I2C sensor is identified by address; SDA and SCL belong to the part."""
    part = sensor.name.upper().replace("-", "")

    if profile is not None:
        driver_kind = "i2c_profile"
    elif part in _I2C_PARTS:
        driver_kind = f"i2c_{part.lower()}"
    else:
        raise CodegenError(
            f"no I2C driver is implemented for '{sensor.name}' "
            f"(hand-written: {sorted(_I2C_PARTS)}). Supply a profile to build "
            f"one from a described register map instead."
        )

    if device is not None and not device.has("i2c"):
        raise CodegenError(
            f"{device.part} has no TWI peripheral, so '{sensor.name}' cannot be "
            f"driven over I2C on this part"
        )

    if not sensor.address:
        raise CodegenError(f"I2C sensor '{sensor.name}' has no address")

    try:
        address = int(str(sensor.address), 0)
    except ValueError as exc:
        raise CodegenError(
            f"'{sensor.address}' is not a usable I2C address for '{sensor.name}'"
        ) from exc

    # 7-bit addressing: 0x00-0x07 and 0x78-0x7F are reserved by the spec.
    if not 0x08 <= address <= 0x77:
        raise CodegenError(
            f"I2C address {sensor.address} for '{sensor.name}' is outside the "
            f"usable 7-bit range 0x08-0x77"
        )

    return RenderedSensor(
        name=sensor.name,
        type=sensor.type,
        interface=sensor.interface,
        symbol=_c_symbol(sensor.name),
        driver_kind=driver_kind,
        resolved_pins={},
        adc_channels={},
        address=f"0x{address:02X}u",
        required=sensor.required,
        profile=profile,
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


# Watchdog periods the AVR hardware actually offers, in milliseconds, with the
# avr-libc constant that selects each. Nothing else is selectable.
_WDT_PERIODS = [
    (15, "WDTO_15MS"), (30, "WDTO_30MS"), (60, "WDTO_60MS"), (120, "WDTO_120MS"),
    (250, "WDTO_250MS"), (500, "WDTO_500MS"), (1000, "WDTO_1S"), (2000, "WDTO_2S"),
    (4000, "WDTO_4S"), (8000, "WDTO_8S"),
]


def _watchdog_settings(
    loop_period_ms: int, sensor_settle_ms: int, sensor_count: int
) -> dict[str, object]:
    """Pick the shortest watchdog period that still clears the worst-case loop.

    Too short and the device resets itself mid-normal-operation; too long and a
    wedged loop goes unnoticed. The budget allows for the loop delay, the gaps
    between sensors, and a slow read from each one.
    """
    # A blocking sensor read can take a few hundred ms before its own timeout.
    worst_read_ms = 400
    budget = (
        loop_period_ms
        + sensor_settle_ms * max(sensor_count - 1, 0)
        + worst_read_ms * sensor_count
    )
    # Half again, so ordinary jitter never trips it.
    preferred = int(budget * 1.5)
    longest_ms, longest_constant = _WDT_PERIODS[-1]

    for period, constant in _WDT_PERIODS:
        if period >= preferred:
            return {
                "watchdog_timeout_ms": period,
                "watchdog_timeout_constant": constant,
                "watchdog_budget_ms": budget,
                "watchdog_margin": "comfortable",
            }

    # The preferred margin does not fit, but the loop itself still does. The
    # longest period is a real guard with less headroom -- better than none,
    # and better than refusing to generate over a margin preference.
    if budget < longest_ms:
        return {
            "watchdog_timeout_ms": longest_ms,
            "watchdog_timeout_constant": longest_constant,
            "watchdog_budget_ms": budget,
            "watchdog_margin": "tight",
        }

    raise CodegenError(
        f"a loop of about {budget} ms cannot be guarded: the longest watchdog this "
        f"hardware offers is {longest_ms} ms, so the watchdog would fire during "
        f"normal operation. Shorten the loop period (currently {loop_period_ms} ms) "
        f"or read fewer sensors per cycle."
    )


def _build_id(
    analysis: PCBAnalysis, f_cpu_hz: int, loop_period_ms: int,
    uart_baud: int, firmware_version: str, part: str,
) -> str:
    """A short, deterministic identifier for this exact configuration.

    Derived from the *inputs*, not the emitted files: the id is printed inside
    config.h, so hashing the output would be circular. Same board and settings
    give the same id; change any of them and it changes, which is what makes a
    unit on a bench traceable back to a build.
    """
    import hashlib
    import json

    material = json.dumps(
        {
            "part": part,
            "board": analysis.board,
            "mcu": analysis.mcu.model_dump(),
            "sensors": [s.model_dump() for s in analysis.sensors],
            "f_cpu_hz": f_cpu_hz,
            "loop_period_ms": loop_period_ms,
            "uart_baud": uart_baud,
            "version": firmware_version,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:8]


def _twi_settings(f_cpu_hz: int, scl_hz: int, part: str) -> dict[str, object]:
    """Compute TWBR, and refuse rates the hardware cannot actually produce.

    SCL = F_CPU / (16 + 2 * TWBR * prescaler). With prescaler 1 the datasheet
    requires TWBR >= 10 for master mode; below that the peripheral misbehaves
    rather than merely running fast, so it is an error, not a warning.
    """
    if scl_hz <= 0:
        raise CodegenError(f"I2C clock must be positive, got {scl_hz}")

    twbr = round((f_cpu_hz / scl_hz - 16) / 2)

    if twbr < 10:
        fastest = f_cpu_hz / (16 + 2 * 10)
        raise CodegenError(
            f"an I2C clock of {scl_hz} Hz needs TWBR={twbr}, but master mode "
            f"requires at least 10. The fastest reachable rate at {f_cpu_hz} Hz "
            f"is about {fastest:.0f} Hz."
        )
    if twbr > 255:
        raise CodegenError(
            f"an I2C clock of {scl_hz} Hz needs TWBR={twbr}, which does not fit "
            f"in the 8-bit register. Raise the clock or use a prescaler."
        )

    actual = f_cpu_hz / (16 + 2 * twbr)
    sda, scl = _TWI_PINS.get(part, (None, None))
    if sda is None:
        raise CodegenError(
            f"which pins carry TWI on {part} is not recorded "
            f"(recorded for: {sorted(_TWI_PINS)}). They are fixed in silicon, "
            f"so they have to be stated rather than assumed."
        )

    return {
        "twi_scl_hz": scl_hz,
        "twi_twbr": twbr,
        "twi_actual_hz": int(round(actual)),
        "twi_bit_us": round(1e6 / actual, 1),
        "twi_sda_pin": sda,
        "twi_scl_pin": scl,
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
    i2c_clock_hz: int = 100_000,
    sensor_profiles: dict | None = None,
    device: "DeviceFacts | None" = None,
    firmware_version: str = "0.1.0",
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
        _resolve_sensor(
            sensor, device=device, board=analysis.board, profiles=sensor_profiles
        )
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
        "has_i2c": any(s.driver_kind.startswith("i2c_") for s in sensors),
        "device": device,
        "firmware_version": firmware_version,
        "build_id": _build_id(
            analysis, f_cpu_hz, loop_period_ms, uart_baud,
            firmware_version, device.part,
        ),
        **_watchdog_settings(loop_period_ms, sensor_settle_ms, len(sensors)),
        # Register index of the USART this part actually has -- see DeviceFacts.
        "usart": device.usart_suffix,
        **_uart_settings(f_cpu_hz, uart_baud),
    }

    if context["has_i2c"]:
        context.update(_twi_settings(f_cpu_hz, i2c_clock_hz, device.part))
    # main.c declares a shared uint16_t for every driver that reports one value.
    context["has_simple_value"] = (
        context["has_ultrasonic"] or context["has_adc"] or context["has_i2c"]
    )

    env = _environment()
    files = {
        "config.h": env.get_template("config.h.j2").render(**context),
        "sensor.h": env.get_template("sensor.h.j2").render(**context),
        "uart.h": env.get_template("drivers/uart.h.j2").render(**context),
        "uart.c": env.get_template("drivers/uart.c.j2").render(**context),
        "watchdog.h": env.get_template("drivers/watchdog.h.j2").render(**context),
        "watchdog.c": env.get_template("drivers/watchdog.c.j2").render(**context),
        "main.c": env.get_template("main.c.j2").render(**context),
    }

    # The I2C bus driver is shared by every device on it, so it is emitted
    # once rather than per sensor.
    if context["has_i2c"]:
        files["twi.h"] = env.get_template("drivers/twi.h.j2").render(**context)
        files["twi.c"] = env.get_template("drivers/twi.c.j2").render(**context)

    # One driver pair per sensor, named after the sensor so two parts of the
    # same kind on one board don't collide.
    for sensor in sensors:
        stem = _DRIVER_TEMPLATES[sensor.driver_kind]
        for extension in ("h", "c"):
            template = env.get_template(f"drivers/{stem}.{extension}.j2")
            files[f"{sensor.symbol.lower()}.{extension}"] = template.render(
                sensor=sensor, profile=sensor.profile, **context
            )

    return GeneratedFirmware(files=files)
