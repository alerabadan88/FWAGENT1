"""Arduino Uno (ATmega328P) pin label -> AVR port/bit mapping.

The config format uses Arduino silkscreen labels ("D2", "A0"); AVR code needs
the underlying port register and bit. This is the real Uno mapping:

    D0-D7   -> PORTD bits 0-7
    D8-D13  -> PORTB bits 0-5
    A0-A5   -> PORTC bits 0-5, also ADC channels 0-5
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.exceptions import CodegenError

_DIGITAL_PIN = re.compile(r"^D(\d{1,2})$", re.IGNORECASE)
_ANALOG_PIN = re.compile(r"^A(\d)$", re.IGNORECASE)


@dataclass(frozen=True)
class AvrPin:
    """A resolved AVR pin: which port, which bit, and its ADC channel if any."""

    label: str
    port: str
    bit: int
    adc_channel: int | None = None

    @property
    def port_register(self) -> str:
        return f"PORT{self.port}"

    @property
    def ddr_register(self) -> str:
        return f"DDR{self.port}"

    @property
    def input_register(self) -> str:
        return f"PIN{self.port}"

    @property
    def is_analog_capable(self) -> bool:
        return self.adc_channel is not None


def map_arduino_uno_pin(label: str) -> AvrPin:
    """Resolve an Arduino Uno pin label to its AVR port and bit.

    Raises :class:`CodegenError` for labels the Uno does not have.
    """
    if not isinstance(label, str) or not label.strip():
        raise CodegenError("pin label must be a non-empty string")

    # Normalized so that "d9" and "D9" resolve to equal pins, and so generated
    # code uses one consistent spelling regardless of how the config wrote it.
    label = label.strip().upper()

    digital = _DIGITAL_PIN.match(label)
    if digital:
        number = int(digital.group(1))
        if 0 <= number <= 7:
            return AvrPin(label=label, port="D", bit=number)
        if 8 <= number <= 13:
            return AvrPin(label=label, port="B", bit=number - 8)
        raise CodegenError(
            f"'{label}' is not a valid Arduino Uno digital pin (expected D0-D13)"
        )

    analog = _ANALOG_PIN.match(label)
    if analog:
        number = int(analog.group(1))
        if 0 <= number <= 5:
            return AvrPin(label=label, port="C", bit=number, adc_channel=number)
        raise CodegenError(
            f"'{label}' is not a valid Arduino Uno analog pin (expected A0-A5)"
        )

    raise CodegenError(
        f"unrecognized Arduino Uno pin label '{label}' (expected D0-D13 or A0-A5)"
    )
