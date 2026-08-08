"""Tests for the part catalog.

Every fact here is checked against a value from the part's own datasheet, so a
regression in the toolchain query shows up as a wrong number rather than a
silently different one.
"""

import pytest

from core.device_catalog import (
    DeviceCatalog,
    DeviceNotFoundError,
    _eval_c_int,
)
from services.toolchain import AvrToolchain

requires_avr = pytest.mark.skipif(
    not AvrToolchain.is_available(), reason="avr-gcc is not installed on this machine"
)


@pytest.fixture(scope="module")
def catalog():
    return DeviceCatalog()


# --- Expression evaluation (no toolchain needed) ------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("0x7FFF", 0x7FFF),
        ("(0x100)", 0x100),
        ("128", 128),
        ("0x3FFUL", 0x3FF),
        # The ATmega32U4 defines RAMEND symbolically; after expansion it is
        # arithmetic, not a literal.
        ("((0x100) + (0xA00) - 1)", 0xAFF),
        ("(1 << 8)", 256),
    ],
)
def test_preprocessor_expressions_are_evaluated(text, expected):
    assert _eval_c_int(text) == expected


@pytest.mark.parametrize("text", ["", "SOME_UNDEFINED_MACRO", "not an expression", "0x"])
def test_unevaluable_expressions_return_none(text):
    assert _eval_c_int(text) is None


def test_expression_evaluation_does_not_execute_arbitrary_code():
    """The text comes off disk, so it must not reach eval()."""
    assert _eval_c_int("__import__('os').system('echo pwned')") is None
    assert _eval_c_int("open('x','w')") is None


# --- Resolution ---------------------------------------------------------------


@requires_avr
def test_the_compiler_knows_many_parts(catalog):
    parts = catalog.known_parts()

    assert len(parts) > 100
    assert "atmega328p" in parts


@requires_avr
@pytest.mark.parametrize(
    "name,expected",
    [
        ("Arduino Uno", "atmega328p"),
        ("Arduino Nano", "atmega328p"),
        ("Arduino Mega 2560", "atmega2560"),
        ("Arduino Leonardo", "atmega32u4"),
        ("ATmega328P", "atmega328p"),
        ("ATmega328P-PU", "atmega328p"),  # package suffix tolerated
        ("attiny85", "attiny85"),
    ],
)
def test_board_and_part_names_resolve(catalog, name, expected):
    assert catalog.resolve(name) == expected


@requires_avr
@pytest.mark.parametrize("name", ["STM32F405", "ESP32", "", "   ", "not a part"])
def test_parts_the_compiler_cannot_target_do_not_resolve(catalog, name):
    assert catalog.resolve(name) is None


@requires_avr
def test_an_unknown_part_raises_rather_than_guessing(catalog):
    with pytest.raises(DeviceNotFoundError, match="does not know a part"):
        catalog.facts("STM32F405")


# --- Facts, checked against datasheets ----------------------------------------


@requires_avr
@pytest.mark.parametrize(
    "part,flash,ram,eeprom",
    [
        ("atmega328p", 32 * 1024, 2 * 1024, 1024),
        ("atmega2560", 256 * 1024, 8 * 1024, 4096),
        ("attiny85", 8 * 1024, 512, 512),
        ("atmega168", 16 * 1024, 1024, 512),
        # RAMEND is symbolic on this part — 2.5 KB, not 2 KB.
        ("atmega32u4", 32 * 1024, 2560, 1024),
    ],
)
def test_memory_sizes_match_the_datasheet(catalog, part, flash, ram, eeprom):
    facts = catalog.facts(part)

    assert facts.flash_bytes == flash
    assert facts.ram_bytes == ram
    assert facts.eeprom_bytes == eeprom


@requires_avr
@pytest.mark.parametrize(
    "part,suffix",
    [
        ("atmega328p", "0"),
        ("atmega2560", "0"),
        # The ATmega32U4 has USART1 and no USART0 at all — matching on UDR0
        # would report "no UART" for a board that plainly has one.
        ("atmega32u4", "1"),
        # The ATtiny2313's USART registers carry no index.
        ("attiny2313", ""),
        # The ATtiny85 has no USART whatsoever.
        ("attiny85", None),
    ],
)
def test_the_right_usart_is_identified(catalog, part, suffix):
    facts = catalog.facts(part)

    assert facts.usart_suffix == suffix
    assert facts.has_uart is (suffix is not None)


@requires_avr
def test_peripherals_come_from_the_parts_own_header(catalog):
    uno = catalog.facts("atmega328p")
    tiny = catalog.facts("attiny85")

    assert uno.has("i2c") and uno.has("spi") and uno.has("adc")
    # The ATtiny85 has neither TWI nor a hardware SPI peripheral.
    assert not tiny.has("i2c")
    assert not tiny.has("spi")
    assert tiny.has("adc")


@requires_avr
def test_lookups_are_cached(catalog):
    """Each lookup costs two compiler runs, so repeats must not re-run them."""
    first = catalog.facts("atmega328p")
    second = catalog.facts("atmega328p")

    assert first is second
