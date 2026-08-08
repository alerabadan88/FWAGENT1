"""Tests for the I2C bus driver and the BMP280 on top of it.

The compensation arithmetic is checked against an independent reference
implemented here in Python with explicit int32 wrapping, and run on a
simulated ATmega328P. That combination is the point: AVR's `int` is 16 bits
and Bosch's intermediates overflow it repeatedly, so a host-side check would
agree with a transcription that is wrong on the target.
"""

from pathlib import Path

import pytest

from codegen.generator import _resolve_sensor, _twi_settings, generate_firmware
from core.device_catalog import DeviceCatalog
from core.exceptions import CodegenError
from core.hardware_model import MCU, InterfaceType, PCBAnalysis, Sensor
from services.build_service import BuildService
from services.test_service import Check, SimulatorTestService
from services.toolchain import AvrToolchain

requires_avr = pytest.mark.skipif(
    not AvrToolchain.is_available(), reason="avr-gcc is not installed on this machine"
)
requires_sim = pytest.mark.skipif(
    not AvrToolchain.is_available() or not AvrToolchain.simulator_available(),
    reason="avr-gcc/avr-gdb are not installed on this machine",
)


# --- An independent reference for Bosch's routine ----------------------------


def _s32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def _u32(value: int) -> int:
    return value & 0xFFFFFFFF


CAL = dict(
    T1=27504, T2=26435, T3=-1000,
    P1=36477, P2=-10685, P3=3024, P4=2855, P5=140,
    P6=-7, P7=15500, P8=-14600, P9=6000,
)
CAL_C = "{27504, 26435, -1000, 36477, -10685, 3024, 2855, 140, -7, 15500, -14600, 6000}"
ADC_T, ADC_P = 519888, 415148


def reference_temperature(cal, adc_t):
    var1 = _s32((((adc_t >> 3) - (cal["T1"] << 1)) * cal["T2"]) >> 11)
    var2 = _s32(((((adc_t >> 4) - cal["T1"]) * ((adc_t >> 4) - cal["T1"])) >> 12) * cal["T3"] >> 14)
    t_fine = _s32(var1 + var2)
    return _s32((t_fine * 5 + 128) >> 8), t_fine


def reference_pressure(cal, adc_p, t_fine):
    var1 = _s32((t_fine >> 1) - 64000)
    var2 = _s32((((var1 >> 2) * (var1 >> 2)) >> 11) * cal["P6"])
    var2 = _s32(var2 + _s32((var1 * cal["P5"]) << 1))
    var2 = _s32((var2 >> 2) + (cal["P4"] << 16))
    var1 = _s32(((cal["P3"] * (((var1 >> 2) * (var1 >> 2)) >> 13)) >> 3)
                + ((cal["P2"] * var1) >> 1) >> 18)
    var1 = _s32(((32768 + var1) * cal["P1"]) >> 15)
    if var1 == 0:
        return 0
    p = _u32(_u32((1048576 - adc_p) - (var2 >> 12)) * 3125)
    p = _u32((p << 1) // var1) if p < 0x80000000 else _u32((p // var1) * 2)
    var1 = _s32((cal["P9"] * _s32(((p >> 3) * (p >> 3)) >> 13)) >> 12)
    var2 = _s32((_s32(p >> 2) * cal["P8"]) >> 13)
    return _u32(_s32(p) + ((var1 + var2 + cal["P7"]) >> 4))


def test_the_reference_produces_physically_plausible_values():
    """If the reference is nonsense, agreeing with it proves nothing."""
    centi_c, t_fine = reference_temperature(CAL, ADC_T)
    pascals = reference_pressure(CAL, ADC_P, t_fine)

    assert 2000 < centi_c < 3000        # 20-30 C
    assert 90_000 < pascals < 110_000   # near sea level


# --- Bus bit rate -------------------------------------------------------------


def test_the_standard_rate_matches_the_datasheet_divisor():
    settings = _twi_settings(16_000_000, 100_000, "atmega328p")

    assert settings["twi_twbr"] == 72
    assert settings["twi_actual_hz"] == 100_000
    assert settings["twi_sda_pin"] == "PC4"
    assert settings["twi_scl_pin"] == "PC5"


def test_a_rate_the_hardware_cannot_reach_is_refused():
    """Master mode needs TWBR >= 10; below that the peripheral misbehaves."""
    # 400 kHz from a 1 MHz clock would need TWBR below the legal minimum.
    with pytest.raises(CodegenError, match="requires at least 10"):
        _twi_settings(1_000_000, 400_000, "atmega328p")


def test_a_part_whose_twi_pins_are_unrecorded_is_refused():
    with pytest.raises(CodegenError, match="not recorded"):
        _twi_settings(16_000_000, 100_000, "atmega644p")


# --- Address handling ---------------------------------------------------------


def _i2c_sensor(**overrides):
    fields = dict(
        name="BMP280", type="pressure_temperature",
        interface=InterfaceType.I2C, bus="I2C1", address="0x76",
    )
    fields.update(overrides)
    return Sensor(**fields)


@requires_avr
def test_an_i2c_sensor_needs_no_pins(catalog_facts):
    resolved = _resolve_sensor(_i2c_sensor(), device=catalog_facts)

    assert resolved.resolved_pins == {}   # SDA and SCL belong to the part
    assert resolved.address == "0x76u"
    assert resolved.driver_kind == "i2c_bmp280"


@requires_avr
@pytest.mark.parametrize("address", ["0x00", "0x07", "0x78", "0x7F"])
def test_reserved_i2c_addresses_are_refused(catalog_facts, address):
    with pytest.raises(CodegenError, match="usable 7-bit range"):
        _resolve_sensor(_i2c_sensor(address=address), device=catalog_facts)


@requires_avr
def test_a_part_with_no_driver_on_i2c_is_refused(catalog_facts):
    with pytest.raises(CodegenError, match="no I2C driver is implemented"):
        _resolve_sensor(_i2c_sensor(name="SHT31", address="0x44"), device=catalog_facts)


@requires_avr
def test_a_part_with_no_twi_peripheral_is_refused():
    tiny = DeviceCatalog().facts("attiny85")

    with pytest.raises(CodegenError, match="no TWI peripheral"):
        _resolve_sensor(_i2c_sensor(), device=tiny)


# --- Generated firmware -------------------------------------------------------


@pytest.fixture(scope="module")
def catalog_facts():
    return DeviceCatalog().facts("atmega328p")


@pytest.fixture(scope="module")
def bmp_board(catalog_facts):
    mcu = MCU(
        name="atmega328p", family="AVR", flash_kb=catalog_facts.flash_kb,
        ram_kb=catalog_facts.ram_kb, clock_mhz=16, gpio_pins=23, voltage=5.0,
    )
    analysis = PCBAnalysis(mcu=mcu, sensors=[_i2c_sensor()])
    return analysis, generate_firmware(analysis, device=catalog_facts)


@requires_avr
def test_the_bus_driver_is_emitted_once(bmp_board):
    _, firmware = bmp_board

    assert "twi.c" in firmware.files and "twi.h" in firmware.files
    assert "bmp280.c" in firmware.files
    assert "#define TWI_TWBR        72" in firmware.files["config.h"]


@requires_avr
def test_the_bus_wait_is_bounded(bmp_board):
    """An unbounded wait on TWINT hangs the firmware if a device holds SDA."""
    _, firmware = bmp_board
    source = firmware.files["twi.c"]

    assert "TWI_WAIT_LIMIT" in source
    assert "SENSOR_ERR_TIMEOUT" in source
    # No bare spin on the flag.
    assert "while (!(TWCR & (uint8_t)(1 << TWINT))) {\n        if" in source


@requires_avr
def test_the_chip_id_is_checked(bmp_board):
    """A BME280 shares the address and answers 0x60, not 0x58."""
    _, firmware = bmp_board

    assert "SENSOR_ERR_WRONG_PART" in firmware.files["bmp280.c"]
    assert "0x58u" in firmware.files["bmp280.c"]


@requires_avr
def test_the_i2c_firmware_builds(bmp_board, tmp_path):
    analysis, firmware = bmp_board

    build = BuildService().build(firmware, analysis.mcu, tmp_path)

    assert build.ok, build.diagnostics
    assert build.memory.fits


@requires_avr
def test_every_i2c_source_survives_wall_wextra_werror(bmp_board, tmp_path):
    analysis, firmware = bmp_board
    firmware.write_to(tmp_path)
    toolchain = AvrToolchain()

    for name in sorted(n for n in firmware.files if n.endswith(".c")):
        result = toolchain.check_syntax(
            tmp_path / name, mcu="atmega328p", f_cpu_hz=16_000_000,
            include_dirs=(tmp_path,), extra_flags=("-Wall", "-Wextra", "-Werror"),
        )
        assert result.ok, f"{name}: {result.diagnostics}"
        assert result.diagnostics == "", name


# --- The arithmetic, on the target --------------------------------------------


@requires_sim
def test_compensation_matches_the_reference_on_the_simulated_mcu(bmp_board, tmp_path):
    analysis, firmware = bmp_board
    expected_t, expected_t_fine = reference_temperature(CAL, ADC_T)
    expected_p = reference_pressure(CAL, ADC_P, expected_t_fine)

    setup = (
        f"bmp280_calibration_t cal = {CAL_C};"
        " int32_t tf = 0; int32_t T = 0; uint32_t P = 0;"
        " uint8_t raw[6] = {0x65, 0x5A, 0xC0, 0x7E, 0xEC, 0x00};"
        " int32_t ap = 0; int32_t at = 0;"
    )
    checks = [
        Check("temperature", f"T = bmp280_compensate_temperature(&cal, {ADC_T}, &tf)",
              expected_t, setup=setup),
        Check("t_fine carried to pressure", "tf", expected_t_fine),
        Check("pressure", f"(int32_t)(P = bmp280_compensate_pressure(&cal, {ADC_P}, tf))",
              expected_p),
        Check("raw pressure unpacked", "bmp280_parse_raw(raw, &ap, &at), ap", 0x655AC),
        Check("raw temperature unpacked", "at", 0x7EEC0),
        Check("null calibration returns zero", "bmp280_compensate_temperature(0, 1, 0)", 0),
    ]

    report = SimulatorTestService().run(firmware, analysis.mcu, checks, tmp_path)

    assert report.status == "success", report.diagnostics
    assert report.ok, [
        f"{r.name}: expected {r.expected}, got {r.actual}" for r in report.failures
    ]
