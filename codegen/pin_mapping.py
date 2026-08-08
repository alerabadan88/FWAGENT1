"""Pin identity, expressed the way the silicon sees it.

A pin has two possible names and only one of them is a fact about the chip:

* ``PD2`` is an MCU pin. It means port D, bit 2, on any AVR that has a port D,
  and it can be checked against the part's own header.
* ``D2`` is a *silkscreen label* on an Arduino Uno. It maps to PD2 only because
  that board wires it that way; on a Mega, ``D2`` is PE4.

So the MCU pin is the primary form here, and board labels are a convenience
layer that resolves into one. Anything that comes from a schematic or netlist
is already MCU-native, which is why that is the input worth having.

Every resolved pin is verified against :class:`DeviceFacts` before use, so a
port the part does not have, or a bit beyond that port's width, is refused
rather than silently generating code that toggles nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.exceptions import CodegenError

# Accepts PD2, PORTD.2, PORTD2, and pd2.
_MCU_PIN = re.compile(r"^(?:P|PORT)([A-L])\.?(\d{1,2})$", re.IGNORECASE)

# Arduino silkscreen labels. These are board facts, not chip facts, so each map
# is named for one board and is only consulted when a board is stated.
#
# The Uno/Nano share the ATmega328P's DIP pinout; the Mega is a different part
# with an entirely different arrangement, which is exactly why a single
# "Arduino pin map" would be wrong.
_UNO_LABELS: dict[str, tuple[str, int]] = {
    **{f"D{n}": ("D", n) for n in range(8)},
    **{f"D{n}": ("B", n - 8) for n in range(8, 14)},
    **{f"A{n}": ("C", n) for n in range(6)},
}

_MEGA_LABELS: dict[str, tuple[str, int]] = {
    "D0": ("E", 0), "D1": ("E", 1), "D2": ("E", 4), "D3": ("E", 5),
    "D4": ("G", 5), "D5": ("E", 3), "D6": ("H", 3), "D7": ("H", 4),
    "D8": ("H", 5), "D9": ("H", 6), "D10": ("B", 4), "D11": ("B", 5),
    "D12": ("B", 6), "D13": ("B", 7),
    **{f"A{n}": ("F", n) for n in range(8)},
    **{f"A{n}": ("K", n - 8) for n in range(8, 16)},
}

BOARD_PIN_LABELS: dict[str, dict[str, tuple[str, int]]] = {
    "ARDUINO UNO": _UNO_LABELS,
    "ARDUINO NANO": _UNO_LABELS,
    "ARDUINO PRO MINI": _UNO_LABELS,
    "ARDUINO MINI": _UNO_LABELS,
    "ARDUINO MEGA": _MEGA_LABELS,
    "ARDUINO MEGA 2560": _MEGA_LABELS,
}

# Which port pin carries each ADC channel. This is not derivable from the
# headers -- they say ADC3 exists, not that it is on PC3 -- so it is a table,
# and a part that is not in it gets asked about rather than guessed at.
ADC_PIN_CHANNELS: dict[str, dict[tuple[str, int], int]] = {
    "atmega328p": {("C", n): n for n in range(6)},
    "atmega168": {("C", n): n for n in range(6)},
    "atmega8": {("C", n): n for n in range(6)},
    "atmega2560": {
        **{("F", n): n for n in range(8)},
        **{("K", n): n + 8 for n in range(8)},
    },
    "attiny85": {("B", 5): 0, ("B", 2): 1, ("B", 4): 2, ("B", 3): 3},
}


@dataclass(frozen=True)
class McuPin:
    """One I/O pin, named as the chip names it."""

    port: str
    bit: int

    def __str__(self) -> str:
        return f"P{self.port}{self.bit}"

    @property
    def port_register(self) -> str:
        return f"PORT{self.port}"

    @property
    def ddr_register(self) -> str:
        return f"DDR{self.port}"

    @property
    def input_register(self) -> str:
        return f"PIN{self.port}"


def parse_mcu_pin(spec: str) -> McuPin:
    """Read an MCU-native pin name. Raises for anything else."""
    if not isinstance(spec, str) or not spec.strip():
        raise CodegenError("a pin must be a non-empty string")

    match = _MCU_PIN.match(spec.strip())
    if not match:
        raise CodegenError(
            f"'{spec}' is not an MCU pin. Use the chip's own name, e.g. PD2 or PORTD.2. "
            f"Board labels like 'D2' need the board to be stated as well."
        )

    return McuPin(port=match.group(1).upper(), bit=int(match.group(2)))


def resolve_pin(spec: str, board: str | None = None) -> McuPin:
    """Resolve either an MCU pin or a board silkscreen label into an MCU pin."""
    if not isinstance(spec, str) or not spec.strip():
        raise CodegenError("a pin must be a non-empty string")

    cleaned = spec.strip()

    if _MCU_PIN.match(cleaned):
        return parse_mcu_pin(cleaned)

    labels = BOARD_PIN_LABELS.get((board or "").upper()) if board else None
    if labels is None:
        known = ", ".join(sorted(BOARD_PIN_LABELS))
        hint = (
            f"no pin map is known for board '{board}' (known: {known})"
            if board
            else "no board was given, so a silkscreen label cannot be resolved"
        )
        raise CodegenError(
            f"'{cleaned}' is not an MCU pin name, and {hint}. "
            f"Give the chip pin instead, e.g. PD2."
        )

    mapped = labels.get(cleaned.upper())
    if mapped is None:
        raise CodegenError(f"'{cleaned}' is not a pin label on {board}")

    return McuPin(port=mapped[0], bit=mapped[1])


def verify_pin(pin: McuPin, device) -> None:
    """Check a pin exists on the part. Raises :class:`CodegenError` if not."""
    if not device.ports:
        raise CodegenError(
            f"no port information is available for {device.part}, so pin "
            f"{pin} cannot be verified"
        )

    width = device.ports.get(pin.port)
    if width is None:
        available = ", ".join(sorted(device.ports))
        raise CodegenError(
            f"{device.part} has no port {pin.port} (it has: {available}), "
            f"so {pin} does not exist on this part"
        )

    if pin.bit >= width:
        raise CodegenError(
            f"{device.part} port {pin.port} is {width} bits wide, so {pin} is "
            f"out of range"
        )


def adc_channel_for(pin: McuPin, device) -> int:
    """Which ADC channel a pin carries on this part.

    Raises when the part is not in the table: an ADC channel guessed wrong
    reads a different pin and reports plausible numbers from the wrong sensor,
    which is worse than refusing.
    """
    mapping = ADC_PIN_CHANNELS.get(device.part)
    if mapping is None:
        known = ", ".join(sorted(ADC_PIN_CHANNELS))
        raise CodegenError(
            f"which pins carry ADC channels on {device.part} is not recorded "
            f"(recorded for: {known}). It cannot be derived from the headers, "
            f"so it has to be stated rather than guessed."
        )

    channel = mapping.get((pin.port, pin.bit))
    if channel is None:
        usable = ", ".join(str(McuPin(p, b)) for p, b in sorted(mapping))
        raise CodegenError(
            f"{pin} is not an analog input on {device.part} (analog pins: {usable})"
        )

    if channel >= device.adc_channels:
        raise CodegenError(
            f"{pin} maps to ADC channel {channel}, but {device.part} only has "
            f"{device.adc_channels} channels"
        )

    return channel
