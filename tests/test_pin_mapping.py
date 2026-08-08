import pytest

from codegen.pin_mapping import (
    BOARD_PIN_LABELS,
    McuPin,
    adc_channel_for,
    parse_mcu_pin,
    resolve_pin,
    verify_pin,
)
from core.device_catalog import DeviceCatalog
from core.exceptions import CodegenError
from services.toolchain import AvrToolchain

requires_avr = pytest.mark.skipif(
    not AvrToolchain.is_available(), reason="avr-gcc is not installed on this machine"
)


@pytest.fixture(scope="module")
def catalog():
    return DeviceCatalog()


# --- MCU-native pins: the same everywhere, no board needed --------------------


@pytest.mark.parametrize(
    "spec,port,bit",
    [
        ("PD2", "D", 2),
        ("PORTD.2", "D", 2),
        ("PORTD2", "D", 2),
        ("pd2", "D", 2),
        ("  PB5  ", "B", 5),
        ("PF0", "F", 0),
        ("PL7", "L", 7),
    ],
)
def test_mcu_pins_parse_in_any_accepted_spelling(spec, port, bit):
    pin = parse_mcu_pin(spec)

    assert (pin.port, pin.bit) == (port, bit)


def test_registers_are_derived_from_the_port():
    pin = parse_mcu_pin("PD2")

    assert pin.port_register == "PORTD"
    assert pin.ddr_register == "DDRD"
    assert pin.input_register == "PIND"
    assert str(pin) == "PD2"


@pytest.mark.parametrize("spec", ["", "   ", "D2", "GPIO4", "banana", "PZ1", "P"])
def test_non_mcu_pin_names_are_rejected(spec):
    with pytest.raises(CodegenError):
        parse_mcu_pin(spec)


# --- Board labels are board facts, not chip facts ----------------------------


def test_the_same_label_is_a_different_pin_on_a_different_board():
    """This is why a single 'Arduino pin map' would be wrong."""
    assert resolve_pin("D2", board="Arduino Uno") == McuPin("D", 2)
    assert resolve_pin("D2", board="Arduino Mega 2560") == McuPin("E", 4)


@pytest.mark.parametrize(
    "label,expected",
    [
        ("D0", McuPin("D", 0)),
        ("D7", McuPin("D", 7)),
        ("D8", McuPin("B", 0)),  # digital pins roll over to port B at D8
        ("D13", McuPin("B", 5)),
        ("A0", McuPin("C", 0)),
        ("A5", McuPin("C", 5)),
    ],
)
def test_uno_silkscreen_labels_resolve(label, expected):
    assert resolve_pin(label, board="Arduino Uno") == expected


def test_an_mcu_pin_needs_no_board():
    assert resolve_pin("PD2") == McuPin("D", 2)


def test_a_silkscreen_label_without_a_board_is_refused():
    with pytest.raises(CodegenError, match="no board was given"):
        resolve_pin("D2")


def test_a_label_on_an_unmapped_board_is_refused():
    with pytest.raises(CodegenError, match="no pin map is known"):
        resolve_pin("D2", board="Teensy 4.1")


def test_a_label_the_board_does_not_have_is_refused():
    with pytest.raises(CodegenError, match="not a pin label"):
        resolve_pin("D99", board="Arduino Uno")


def test_boards_sharing_a_part_share_a_map():
    """The Nano is the same ATmega328P pinout as the Uno."""
    assert BOARD_PIN_LABELS["ARDUINO NANO"] is BOARD_PIN_LABELS["ARDUINO UNO"]


# --- Verification against the actual silicon ---------------------------------


@requires_avr
def test_a_pin_the_part_has_is_accepted(catalog):
    verify_pin(parse_mcu_pin("PD2"), catalog.facts("atmega328p"))


@requires_avr
def test_a_port_the_part_lacks_is_refused(catalog):
    """The ATtiny85 has only port B."""
    with pytest.raises(CodegenError, match="has no port D"):
        verify_pin(parse_mcu_pin("PD2"), catalog.facts("attiny85"))


@requires_avr
def test_a_bit_beyond_the_ports_width_is_refused(catalog):
    """The ATtiny85's port B is 6 bits, not 8."""
    with pytest.raises(CodegenError, match="6 bits wide"):
        verify_pin(parse_mcu_pin("PB7"), catalog.facts("attiny85"))

    verify_pin(parse_mcu_pin("PB5"), catalog.facts("attiny85"))


@requires_avr
def test_a_port_only_larger_parts_have_is_refused_on_smaller_ones(catalog):
    verify_pin(parse_mcu_pin("PL0"), catalog.facts("atmega2560"))

    with pytest.raises(CodegenError, match="has no port L"):
        verify_pin(parse_mcu_pin("PL0"), catalog.facts("atmega328p"))


# --- ADC channels -------------------------------------------------------------


@requires_avr
@pytest.mark.parametrize("spec,channel", [("PC0", 0), ("PC3", 3), ("PC5", 5)])
def test_analog_pins_map_to_their_channel(catalog, spec, channel):
    assert adc_channel_for(parse_mcu_pin(spec), catalog.facts("atmega328p")) == channel


@requires_avr
def test_the_mega_uses_different_ports_for_analog(catalog):
    mega = catalog.facts("atmega2560")

    assert adc_channel_for(parse_mcu_pin("PF0"), mega) == 0
    assert adc_channel_for(parse_mcu_pin("PK0"), mega) == 8


@requires_avr
def test_a_digital_only_pin_has_no_channel(catalog):
    with pytest.raises(CodegenError, match="not an analog input"):
        adc_channel_for(parse_mcu_pin("PD2"), catalog.facts("atmega328p"))


@requires_avr
def test_an_unrecorded_part_is_asked_about_rather_than_guessed(catalog):
    """Which pin carries which channel is not in the headers, so it is not invented."""
    with pytest.raises(CodegenError, match="is not recorded"):
        adc_channel_for(parse_mcu_pin("PA0"), catalog.facts("atmega644p"))
