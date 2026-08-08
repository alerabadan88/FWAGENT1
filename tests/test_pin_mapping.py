import pytest

from codegen.pin_mapping import map_arduino_uno_pin
from core.exceptions import CodegenError


@pytest.mark.parametrize(
    "label,port,bit",
    [
        ("D0", "D", 0),
        ("D2", "D", 2),
        ("D7", "D", 7),
        ("D8", "B", 0),  # digital pins roll over to PORTB at D8
        ("D9", "B", 1),
        ("D13", "B", 5),
    ],
)
def test_digital_pins_map_to_the_right_port_and_bit(label, port, bit):
    pin = map_arduino_uno_pin(label)

    assert (pin.port, pin.bit) == (port, bit)
    assert pin.adc_channel is None
    assert pin.is_analog_capable is False


@pytest.mark.parametrize("label,bit", [("A0", 0), ("A3", 3), ("A5", 5)])
def test_analog_pins_map_to_portc_with_an_adc_channel(label, bit):
    pin = map_arduino_uno_pin(label)

    assert pin.port == "C"
    assert pin.bit == bit
    assert pin.adc_channel == bit
    assert pin.is_analog_capable is True


def test_register_names_are_derived_from_the_port():
    pin = map_arduino_uno_pin("D2")

    assert pin.port_register == "PORTD"
    assert pin.ddr_register == "DDRD"
    assert pin.input_register == "PIND"


def test_pin_labels_are_case_insensitive():
    assert map_arduino_uno_pin("d9") == map_arduino_uno_pin("D9")


@pytest.mark.parametrize("label", ["D14", "D99", "A6", "A9"])
def test_out_of_range_pins_are_rejected(label):
    with pytest.raises(CodegenError, match="not a valid Arduino Uno"):
        map_arduino_uno_pin(label)


@pytest.mark.parametrize("label", ["GPIO4", "PB5", "", "   ", "X1"])
def test_unrecognized_pin_labels_are_rejected(label):
    with pytest.raises(CodegenError):
        map_arduino_uno_pin(label)
