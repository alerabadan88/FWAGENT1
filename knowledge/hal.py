"""The porting contract: the smallest interface the generated application needs.

Why there is a HAL at all
-------------------------
Because it splits the firmware along the line where knowledge runs out. The
application -- the LED state machine, the button debounce, the NMEA parser, the
report scheduler -- is where the bugs actually live, and none of it depends on
the vendor. It can be written completely and correctly for a part whose SDK
nobody here has ever seen.

What *does* depend on the vendor is fourteen functions. So those fourteen are
isolated in one file, and when the SDK is unknown that file comes out as stubs,
each carrying the exact question an engineer has to answer. The customer gets
the firmware; the porting cost is one afternoon on a file with no logic in it.

Candidate matching, and what it is not
--------------------------------------
When an SDK catalogue *is* present, each operation lists name fragments that a
matching function would plausibly contain. This narrows hundreds of symbols to
a handful. It does not identify the right one.

That distinction is load-bearing and is preserved all the way into the emitted
code: "`sdk_gpio_output_set` exists in this SDK" is AUTHORITATIVE, read from a
header. "`sdk_gpio_output_set` is what `hal_gpio_write` should call" is a
guess. So a single candidate is emitted as a call *and flagged for review*;
several or none become `#error` with the candidates listed. Never a silent pick.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Operation:
    """One function the port must supply."""

    name: str
    signature: str
    purpose: str
    must: tuple[str, ...]
    """Fragments a candidate's name must contain (all of them), lowercased."""
    any_of: tuple[str, ...] = ()
    """At least one of these, when given. Separates 'write' from 'read'."""
    optional: bool = False
    """True when the application still works if this is left unimplemented."""


#: The contract. Ordered as a port would be written, top to bottom.
OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="hal_init",
        signature="void hal_init(void)",
        purpose="Bring up clocks and anything the SDK requires before use.",
        must=("init",),
        any_of=("system", "board", "chip", "hal", "bsp"),
    ),
    Operation(
        name="hal_delay_ms",
        signature="void hal_delay_ms(uint32_t ms)",
        purpose="Block for a number of milliseconds.",
        must=("delay",),
        any_of=("ms", "milli"),
    ),
    Operation(
        name="hal_uptime_ms",
        signature="uint32_t hal_uptime_ms(void)",
        purpose="Milliseconds since boot, free-running. Used for all scheduling.",
        must=(),
        any_of=("tick", "uptime", "millis", "systime", "timestamp"),
    ),
    Operation(
        name="hal_gpio_config_output",
        signature="void hal_gpio_config_output(hal_pin_t pin)",
        purpose="Make a pin a push-pull output.",
        must=("gpio",),
        any_of=("dir", "config", "mode", "output", "init"),
    ),
    Operation(
        name="hal_gpio_config_input",
        signature="void hal_gpio_config_input(hal_pin_t pin, hal_pull_t pull)",
        purpose="Make a pin an input with the given pull resistor.",
        must=("gpio",),
        any_of=("pull", "input", "config", "mode"),
    ),
    Operation(
        name="hal_gpio_write",
        signature="void hal_gpio_write(hal_pin_t pin, bool level)",
        purpose="Drive an output pin high or low.",
        must=("gpio",),
        any_of=("write", "set", "out"),
    ),
    Operation(
        name="hal_gpio_read",
        signature="bool hal_gpio_read(hal_pin_t pin)",
        purpose="Sample an input pin.",
        must=("gpio",),
        any_of=("read", "get", "in"),
    ),
    Operation(
        name="hal_i2c_init",
        signature="int hal_i2c_init(uint8_t bus, uint32_t hz)",
        purpose="Bring up an I2C controller at the given bit rate.",
        must=("init",),
        any_of=("i2c", "twi"),
        optional=True,
    ),
    Operation(
        name="hal_i2c_write",
        signature="int hal_i2c_write(uint8_t bus, uint8_t addr, const uint8_t *data, size_t len)",
        purpose="Write bytes to an I2C slave.",
        must=("write",),
        any_of=("i2c", "twi"),
        optional=True,
    ),
    Operation(
        name="hal_i2c_read",
        signature="int hal_i2c_read(uint8_t bus, uint8_t addr, uint8_t *data, size_t len)",
        purpose="Read bytes from an I2C slave.",
        must=("read",),
        any_of=("i2c", "twi"),
        optional=True,
    ),
    Operation(
        name="hal_i2c_write_read",
        signature=(
            "int hal_i2c_write_read(uint8_t bus, uint8_t addr, const uint8_t *tx, "
            "size_t tx_len, uint8_t *rx, size_t rx_len)"
        ),
        purpose="Register read: write the register index, repeated start, read back.",
        must=(),
        any_of=("i2c", "twi"),
        optional=True,
    ),
    Operation(
        name="hal_uart_init",
        signature="int hal_uart_init(uint8_t port, uint32_t baud)",
        purpose="Bring up a UART at the given baud rate, 8N1.",
        must=("init",),
        any_of=("uart", "usart", "serial"),
        optional=True,
    ),
    Operation(
        name="hal_uart_write",
        signature="int hal_uart_write(uint8_t port, const uint8_t *data, size_t len)",
        purpose="Send bytes.",
        must=("write",),
        any_of=("uart", "usart", "serial"),
        optional=True,
    ),
    Operation(
        name="hal_uart_read",
        signature=(
            "int hal_uart_read(uint8_t port, uint8_t *data, size_t len, uint32_t timeout_ms)"
        ),
        purpose="Receive up to len bytes, returning early on timeout.",
        must=("read",),
        any_of=("uart", "usart", "serial"),
        optional=True,
    ),
)


def candidates(operation: Operation, symbol_names) -> list[str]:
    """SDK functions that could plausibly implement this operation.

    Narrowing, not identification -- see the module docstring. Returns at most
    a handful; an operation matching dozens of symbols is reported as unmatched
    rather than picked from, because a list that long means the heuristic did
    not actually narrow anything.
    """
    hits = []
    for name in symbol_names:
        lowered = name.lower()
        if not all(fragment in lowered for fragment in operation.must):
            continue
        if operation.any_of and not any(fragment in lowered for fragment in operation.any_of):
            continue
        hits.append(name)
    return sorted(hits) if len(hits) <= 8 else []
