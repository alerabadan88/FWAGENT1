from pathlib import Path

import pytest

from codegen.generator import generate_firmware
from core.eda_parser import parse_config_file
from core.exceptions import CodegenError
from core.hardware_model import MCU, InterfaceType, PCBAnalysis, Sensor
from services.toolchain import AvrToolchain

FIXTURES = Path(__file__).parent / "fixtures"

requires_avr = pytest.mark.skipif(
    not AvrToolchain.is_available(),
    reason="avr-gcc is not installed on this machine",
)


@pytest.fixture
def uno_analysis():
    return parse_config_file(FIXTURES / "arduino_uno_config.json")


def test_generates_both_source_files(uno_analysis):
    firmware = generate_firmware(uno_analysis)

    assert set(firmware.files) == {"main.c", "config.h"}
    assert firmware.files["main.c"].strip()
    assert firmware.files["config.h"].strip()


def test_config_header_carries_the_real_pin_mapping(uno_analysis):
    header = generate_firmware(uno_analysis).files["config.h"]

    # DHT22 on D2 -> PORTD bit 2
    assert "#define DHT22_SIGNAL_PORT   PORTD" in header
    assert "#define DHT22_SIGNAL_BIT    2" in header
    # HC-SR04 trigger D9 -> PORTB bit 1, echo D10 -> PORTB bit 2
    assert "#define HC_SR04_TRIGGER_BIT    1" in header
    assert "#define HC_SR04_ECHO_BIT    2" in header
    # LDR on A0 -> PORTC bit 0, ADC channel 0
    assert "#define LDR_SIGNAL_ADC_CHANNEL 0" in header


def test_optional_sensor_is_marked_not_required(uno_analysis):
    header = generate_firmware(uno_analysis).files["config.h"]

    assert "#define LDR_REQUIRED 0" in header
    assert "#define DHT22_REQUIRED 1" in header


def test_unimplemented_drivers_return_not_implemented_instead_of_fake_data(uno_analysis):
    source = generate_firmware(uno_analysis).files["main.c"]

    assert "SENSOR_ERR_NOT_IMPLEMENTED" in source
    # The ADC driver is genuinely implemented, so it must NOT be a stub.
    assert "return adc_read(LDR_SIGNAL_ADC_CHANNEL, out_value);" in source


def test_sensor_names_become_valid_c_identifiers(uno_analysis):
    source = generate_firmware(uno_analysis).files["main.c"]

    assert "hc_sr04_init" in source  # 'HC-SR04' -> 'HC_SR04'
    assert "HC-SR04_" not in source


def test_generated_files_are_written_to_disk(uno_analysis, tmp_path):
    written = generate_firmware(uno_analysis).write_to(tmp_path)

    assert sorted(p.name for p in written) == ["config.h", "main.c"]
    assert (tmp_path / "main.c").read_text(encoding="utf-8").startswith("/* Generated")


def test_non_avr_mcu_is_rejected():
    analysis = parse_config_file(FIXTURES / "esp32_config.json")

    with pytest.raises(CodegenError, match="family 'ESP32' is not supported"):
        generate_firmware(analysis)


def test_i2c_sensor_is_rejected_by_the_avr_generator():
    analysis = PCBAnalysis(
        mcu=MCU(name="ATmega328P", family="AVR", flash_kb=32, ram_kb=2, clock_mhz=16, gpio_pins=20, voltage=5.0),
        sensors=[
            Sensor(name="MPU6050", type="imu", interface=InterfaceType.I2C, bus="I2C1", address="0x68")
        ],
    )

    with pytest.raises(CodegenError, match="does not support yet"):
        generate_firmware(analysis)


def test_board_with_no_sensors_is_rejected():
    analysis = PCBAnalysis(
        mcu=MCU(name="ATmega328P", family="AVR", flash_kb=32, ram_kb=2, clock_mhz=16, gpio_pins=20, voltage=5.0),
        sensors=[],
    )

    with pytest.raises(CodegenError, match="no sensors"):
        generate_firmware(analysis)


def test_adc_sensor_on_a_digital_only_pin_is_rejected():
    analysis = PCBAnalysis(
        mcu=MCU(name="ATmega328P", family="AVR", flash_kb=32, ram_kb=2, clock_mhz=16, gpio_pins=20, voltage=5.0),
        sensors=[Sensor(name="LDR", type="light_level", interface=InterfaceType.ADC, pins={"pin": "D2"})],
    )

    with pytest.raises(CodegenError, match="no ADC channel"):
        generate_firmware(analysis)


# --- Acceptance: the generated C must survive the real compiler ---------------


@requires_avr
def test_generated_firmware_passes_real_syntax_check(uno_analysis, tmp_path):
    generate_firmware(uno_analysis).write_to(tmp_path)

    result = AvrToolchain().check_syntax(
        tmp_path / "main.c", mcu="atmega328p", f_cpu_hz=16_000_000, include_dirs=(tmp_path,)
    )

    assert result.ok, result.diagnostics
    assert result.diagnostics == ""  # no warnings either


@requires_avr
def test_generated_firmware_links_into_a_real_elf(uno_analysis, tmp_path):
    generate_firmware(uno_analysis).write_to(tmp_path)
    elf = tmp_path / "firmware.elf"

    result = AvrToolchain().compile_to_elf(
        [tmp_path / "main.c"],
        elf,
        mcu="atmega328p",
        f_cpu_hz=16_000_000,
        include_dirs=(tmp_path,),
    )

    assert result.ok, result.diagnostics
    assert elf.is_file()
    assert elf.stat().st_size > 0
