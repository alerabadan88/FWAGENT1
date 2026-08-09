"""Pin control, which is where devicetree stops being vendor-neutral.

Everything else in a board port is portable: a node with a compatible and a
`reg`, a GPIO phandle, a status. Pin *muxing* is not. Nordic writes
`NRF_PSEL(UART_TX, 0, 6)`, STM32 writes `<&usart1_tx_pa9>` referring to
pre-generated symbols, Espressif writes yet another form. There is no common
spelling and no way to derive one.

So this module knows a small number of dialects, and refuses for the rest. A
refusal here costs the user a few lines of hand-written devicetree. A guess
would produce a board that builds, boots, and prints nothing out of a pin
nobody connected -- which is the failure this project exists to avoid, arriving
by a new route.

The pins themselves are never inferred. Which pin the console sits on is a
property of the PCB, so it is asked.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.exceptions import FWAgentError


class PinctrlUnsupported(FWAgentError):
    """No dialect is known for this vendor, so nothing is emitted."""


@dataclass(frozen=True)
class UartPins:
    tx: str
    rx: str


def _split(pin: str) -> tuple[int, int]:
    """'P0.6' or 'PA9' or '6' -> (port, offset)."""
    text = pin.strip().upper().lstrip("P")
    port, _, offset = text.partition(".")
    if offset:
        return int(port), int(offset)
    if text[:1].isalpha() and text[1:].isdigit():
        return ord(text[0]) - ord("A"), int(text[1:])
    if text.isdigit():
        return 0, int(text)
    raise PinctrlUnsupported(
        f"cannot read '{pin}' as a pin. Give it as 'P0.6', 'PA9' or a plain "
        f"offset -- a guessed pin muxes the peripheral onto the wrong pad, and "
        f"nothing reports it."
    )


def nordic_uart(label: str, pins: UartPins) -> str:
    tx_port, tx_offset = _split(pins.tx)
    rx_port, rx_offset = _split(pins.rx)
    return f"""&pinctrl {{
	{label}_default: {label}_default {{
		group1 {{
			psels = <NRF_PSEL(UART_TX, {tx_port}, {tx_offset})>;
		}};
		group2 {{
			psels = <NRF_PSEL(UART_RX, {rx_port}, {rx_offset})>;
			bias-pull-up;
		}};
	}};

	{label}_sleep: {label}_sleep {{
		group1 {{
			psels = <NRF_PSEL(UART_TX, {tx_port}, {tx_offset})>,
				<NRF_PSEL(UART_RX, {rx_port}, {rx_offset})>;
			low-power-enable;
		}};
	}};
}};
"""


#: Peripherals a vendor needs enabled beyond the obvious ones. On Nordic, GPIO
#: *interrupts* are served by GPIOTE, which is a separate node: without it the
#: build fails on a static assertion rather than on anything that mentions the
#: button. Keyed by vendor because there is no portable rule -- this is the same
#: kind of vendor knowledge as pin muxing, and it is written down for the same
#: reason.
IMPLIED_PERIPHERALS = {
    "nordic": ("gpiote",),
    "acme": ("gpiote",),
}


def implied_peripherals(vendor: str) -> tuple[str, ...]:
    return IMPLIED_PERIPHERALS.get(vendor.lower(), ())


#: Vendors whose pin muxing this can write. Deliberately short.
DIALECTS = {"nordic": nordic_uart, "acme": nordic_uart}


def uart_pinctrl(vendor: str, label: str, pins: UartPins | None) -> str:
    """The pinctrl block for a UART, or a refusal explaining what to add.

    `acme` maps to the Nordic dialect because the demo board is an nRF52840 on
    a custom PCB; a real deployment would key this off the SoC's vendor, not
    the board's. That is a known rough edge, written down rather than hidden.
    """
    if pins is None:
        raise PinctrlUnsupported(
            f"the pins for {label} were not given. Which pad the console comes "
            f"out of is a property of the board -- there is no default, and a "
            f"wrong one produces a board that boots and prints into a pin "
            f"nobody connected."
        )

    dialect = DIALECTS.get(vendor.lower())
    if dialect is None:
        raise PinctrlUnsupported(
            f"no pin-control dialect is known for vendor '{vendor}'. Pin muxing "
            f"is the one part of devicetree with no common spelling: Nordic "
            f"writes NRF_PSEL(...), STM32 refers to pre-generated symbols like "
            f"<&usart1_tx_pa9>, and there is no way to derive either. Write the "
            f"&pinctrl block for {label} by hand -- it is a few lines -- rather "
            f"than have this invent one."
        )

    return dialect(label, pins)
