import json
from pathlib import Path

import pytest

from core.eda_parser import parse_config_dict, parse_config_file, parse_netlist_file
from core.exceptions import EDAParseError, HardwareValidationError
from core.hardware_model import InterfaceType

FIXTURES = Path(__file__).parent / "fixtures"


def test_parses_arduino_uno_config_from_real_file():
    analysis = parse_config_file(FIXTURES / "arduino_uno_config.json")

    assert analysis.mcu.name == "ATmega328P"
    assert analysis.mcu.family == "AVR"
    assert analysis.mcu.flash_kb == 32
    assert analysis.mcu.ram_kb == 2  # config calls this "sram_kb"
    assert analysis.mcu.voltage == 5.0

    assert [s.name for s in analysis.sensors] == ["DHT22", "HC-SR04", "LDR"]

    dht22 = analysis.sensors[0]
    assert dht22.interface == InterfaceType.GPIO
    assert dht22.pins == {"pin": "D2"}  # single "pin" normalized into the dict

    hcsr04 = analysis.sensors[1]
    assert hcsr04.pins == {"trigger": "D9", "echo": "D10"}

    ldr = analysis.sensors[2]
    assert ldr.interface == InterfaceType.ADC
    assert ldr.required is False


def test_parses_esp32_config_from_real_file():
    analysis = parse_config_file(FIXTURES / "esp32_config.json")

    assert analysis.mcu.name == "ESP32-WROOM-32"
    assert analysis.mcu.ram_kb == 520
    assert len(analysis.sensors) == 4

    by_name = {s.name: s for s in analysis.sensors}

    assert by_name["MPU6050"].interface == InterfaceType.I2C
    assert by_name["MPU6050"].bus == "I2C1"
    assert by_name["MPU6050"].address == "0x68"

    # UART sensors declare "port" rather than "bus".
    assert by_name["NEO-6M"].interface == InterfaceType.UART
    assert by_name["NEO-6M"].bus == "UART2"

    assert by_name["DS18B20"].required is False


def test_parsed_esp32_config_builds_a_graph():
    analysis = parse_config_file(FIXTURES / "esp32_config.json")
    graph = analysis.to_graph()

    assert graph.number_of_nodes() == 5  # 1 MCU + 4 sensors
    assert graph.number_of_edges() == 4


def test_missing_file_raises_eda_parse_error():
    with pytest.raises(EDAParseError, match="config file not found"):
        parse_config_file(FIXTURES / "does_not_exist.json")


def test_malformed_json_raises_eda_parse_error(tmp_path):
    bad = tmp_path / "broken.json"
    bad.write_text('{"mcu": "ATmega328P",,}', encoding="utf-8")

    with pytest.raises(EDAParseError, match="not valid JSON"):
        parse_config_file(bad)


def test_missing_mcu_field_raises_eda_parse_error():
    with pytest.raises(EDAParseError, match="missing required field 'mcu'"):
        parse_config_dict({"sensors": []})


def test_missing_mcu_spec_field_raises_eda_parse_error():
    config = {
        "mcu": "ATmega328P",
        "mcu_family": "AVR",
        "power_supply_voltage": 5.0,
        "mcu_specs": {"flash_kb": 32, "clock_mhz": 16, "gpio_pins": 20},
    }

    with pytest.raises(EDAParseError, match="missing required field 'sram_kb'"):
        parse_config_dict(config)


def test_unknown_interface_raises_eda_parse_error():
    config = {
        "mcu": "ATmega328P",
        "mcu_family": "AVR",
        "power_supply_voltage": 5.0,
        "mcu_specs": {"flash_kb": 32, "sram_kb": 2, "clock_mhz": 16, "gpio_pins": 20},
        "sensors": [{"name": "Mystery", "type": "unknown", "interface": "CAN"}],
    }

    with pytest.raises(EDAParseError, match="unknown interface 'CAN'"):
        parse_config_dict(config)


def test_i2c_address_conflict_in_config_raises_hardware_validation_error():
    config = json.loads((FIXTURES / "esp32_config.json").read_text(encoding="utf-8"))
    # Point BMP280 at the address MPU6050 already occupies on the same bus.
    config["sensors"][1]["address"] = "0x68"

    with pytest.raises(HardwareValidationError, match="I2C address conflict"):
        parse_config_dict(config)


def test_a_netlist_yields_connectivity_the_json_config_cannot():
    """The netlist knows which MCU pin each part is on; the JSON config is told."""
    analysis = parse_netlist_file(FIXTURES / "netlists" / "uno_sensors.net")

    assert analysis.mcu.name == "atmega328p"
    by_name = {s.name: s for s in analysis.sensors}
    assert by_name["DHT22"].pins == {"pin": "PD2"}
    assert by_name["HC-SR04"].pins == {"trigger": "PB1", "echo": "PB2"}


def test_feeding_a_json_config_to_the_netlist_parser_is_rejected():
    with pytest.raises(EDAParseError, match="does not look like a KiCad netlist"):
        parse_netlist_file(FIXTURES / "arduino_uno_config.json")
