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


def test_generates_a_driver_pair_per_sensor(uno_analysis):
    firmware = generate_firmware(uno_analysis)

    assert set(firmware.files) == {
        "main.c",
        "config.h",
        "sensor.h",
        "uart.c",
        "uart.h",
        "dht22.c",
        "dht22.h",
        "hc_sr04.c",
        "hc_sr04.h",
        "ldr.c",
        "ldr.h",
    }
    assert all(content.strip() for content in firmware.files.values())


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


def test_no_driver_is_left_as_a_stub(uno_analysis):
    firmware = generate_firmware(uno_analysis)

    for name, content in firmware.files.items():
        assert "NOT_IMPLEMENTED" not in content, name
        assert "TODO: implement" not in content, name


def test_dht22_driver_implements_the_real_protocol(uno_analysis):
    source = generate_firmware(uno_analysis).files["dht22.c"]

    assert "for (i = 0; i < 40; i++)" in source  # 40-bit frame
    assert "SENSOR_ERR_CHECKSUM" in source
    assert "_delay_ms(2)" in source  # >=1 ms start pulse
    assert "0x8000u" in source  # sign flag on the temperature word


def test_ultrasonic_driver_times_the_echo_with_a_hardware_timer(uno_analysis):
    source = generate_firmware(uno_analysis).files["hc_sr04.c"]

    assert "TCNT1" in source
    assert "_delay_us(10)" in source  # 10 us trigger pulse
    assert "SENSOR_ERR_OUT_OF_RANGE" in source


def test_sensor_names_become_valid_c_identifiers(uno_analysis):
    source = generate_firmware(uno_analysis).files["main.c"]

    assert "hc_sr04_init" in source  # 'HC-SR04' -> 'HC_SR04'
    assert "HC-SR04_" not in source


def test_a_part_with_no_driver_is_rejected():
    analysis = PCBAnalysis(
        mcu=MCU(name="ATmega328P", family="AVR", flash_kb=32, ram_kb=2, clock_mhz=16, gpio_pins=20, voltage=5.0),
        sensors=[Sensor(name="DS18B20", type="temperature", interface=InterfaceType.GPIO, pins={"pin": "D4"})],
    )

    with pytest.raises(CodegenError, match="no driver is implemented"):
        generate_firmware(analysis)


def test_generated_files_are_written_to_disk(uno_analysis, tmp_path):
    written = generate_firmware(uno_analysis).write_to(tmp_path)

    assert (tmp_path / "main.c").read_text(encoding="utf-8").startswith("/* Generated")
    assert (tmp_path / "dht22.c").is_file()
    assert len(written) == 11


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
def test_every_generated_source_survives_wall_wextra_werror(uno_analysis, tmp_path):
    """The strictest gate: no warnings anywhere, in any generated file."""
    firmware = generate_firmware(uno_analysis)
    firmware.write_to(tmp_path)
    toolchain = AvrToolchain()

    sources = sorted(name for name in firmware.files if name.endswith(".c"))
    assert len(sources) == 5  # main + uart + one driver per sensor

    for name in sources:
        result = toolchain.check_syntax(
            tmp_path / name,
            mcu="atmega328p",
            f_cpu_hz=16_000_000,
            include_dirs=(tmp_path,),
            extra_flags=("-Wall", "-Wextra", "-Werror"),
        )
        assert result.ok, f"{name}: {result.diagnostics}"
        assert result.diagnostics == "", name


@requires_avr
def test_generated_firmware_links_into_a_real_elf(uno_analysis, tmp_path):
    firmware = generate_firmware(uno_analysis)
    firmware.write_to(tmp_path)
    elf = tmp_path / "firmware.elf"

    sources = [tmp_path / name for name in sorted(firmware.files) if name.endswith(".c")]
    result = AvrToolchain().compile_to_elf(
        sources,
        elf,
        mcu="atmega328p",
        f_cpu_hz=16_000_000,
        include_dirs=(tmp_path,),
    )

    assert result.ok, result.diagnostics
    assert elf.is_file()
    assert elf.stat().st_size > 0
