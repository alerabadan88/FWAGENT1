from pathlib import Path

import pytest

from codegen.generator import generate_firmware
from core.eda_parser import parse_config_file
from core.hardware_model import MCU
from services.build_service import BuildService
from services.flash_service import (
    FlashError,
    FlashResult,
    FlashService,
    SerialPort,
    find_avrdude,
    list_serial_ports,
)
from services.toolchain import AvrToolchain

FIXTURES = Path(__file__).parent / "fixtures"

requires_avr = pytest.mark.skipif(
    not AvrToolchain.is_available(), reason="avr-gcc is not installed on this machine"
)
requires_avrdude = pytest.mark.skipif(
    not FlashService.is_available(), reason="avrdude is not installed on this machine"
)


def make_uno() -> MCU:
    return MCU(
        name="ATmega328P", family="AVR", flash_kb=32, ram_kb=2,
        clock_mhz=16, gpio_pins=20, voltage=5.0,
    )


@pytest.fixture
def built_hex(tmp_path):
    """A real firmware.hex produced by the real toolchain."""
    analysis = parse_config_file(FIXTURES / "arduino_uno_config.json")
    firmware = generate_firmware(analysis)
    build = BuildService().build(firmware, analysis.mcu, tmp_path)
    assert build.ok, build.diagnostics
    hex_path = AvrToolchain().elf_to_hex(build.elf_path, tmp_path / "firmware.hex")
    return hex_path, analysis.mcu, build


# --- No hardware needed ------------------------------------------------------


def test_a_not_run_result_never_claims_success():
    result = FlashResult(status="not_run", port="COM3", diagnostics="nothing to do")

    assert result.ok is False
    assert "not run" in result.summary()


def test_error_line_skips_avrdudes_signoff():
    """avrdude prints 'Avrdude done. Thank you.' even when it failed."""
    result = FlashResult(
        status="failed",
        port="COM99",
        diagnostics=(
            "Error: cannot open port \\\\.\\COM99: not found\n"
            "\n"
            "Avrdude done.  Thank you."
        ),
    )

    assert result.error_line.startswith("Error: cannot open port")
    assert "Thank you" not in result.summary()


def test_programmer_settings_are_per_part():
    assert FlashService.programmer_for(make_uno())["part"] == "m328p"
    assert FlashService.programmer_for(make_uno())["programmer"] == "arduino"


def test_an_unknown_part_has_no_programmer():
    stm32 = make_uno().model_copy(update={"name": "STM32F405RGT6"})

    with pytest.raises(FlashError, match="no avrdude programmer is configured"):
        FlashService.programmer_for(stm32)


def test_listing_ports_never_raises():
    ports = list_serial_ports()

    assert isinstance(ports, list)
    assert all(isinstance(p, SerialPort) for p in ports)


def test_serial_port_renders_without_a_description():
    assert str(SerialPort(name="COM3")) == "COM3"
    assert "Arduino" in str(SerialPort(name="COM3", description="Arduino Uno"))


# --- Real avrdude, no board --------------------------------------------------


@requires_avrdude
def test_avrdude_is_found_and_reports_a_version():
    assert find_avrdude() is not None
    assert "avrdude" in FlashService().version().lower()


@requires_avr
@requires_avrdude
def test_a_missing_hex_never_invokes_avrdude(built_hex, tmp_path):
    _, mcu, _ = built_hex

    result = FlashService().flash(tmp_path / "absent.hex", mcu, port="COM3")

    assert result.status == "not_run"
    assert result.command == []  # avrdude was never run
    assert "does not exist" in result.diagnostics


@requires_avr
@requires_avrdude
def test_an_empty_port_is_refused_rather_than_guessed(built_hex):
    hex_path, mcu, _ = built_hex

    result = FlashService().flash(hex_path, mcu, port="   ")

    assert result.status == "not_run"
    assert "explicitly" in result.diagnostics


@requires_avr
@requires_avrdude
def test_flashing_a_nonexistent_port_fails_cleanly(built_hex):
    """Must fail fast with a real diagnostic, not hang waiting on a device."""
    hex_path, mcu, _ = built_hex

    result = FlashService().flash(hex_path, mcu, port="COM99", timeout=30)

    assert result.status == "failed"
    assert not result.ok
    assert "COM99" in result.error_line
    assert result.command  # the attempt was genuinely made


# --- HEX generation ----------------------------------------------------------


@requires_avr
def test_hex_and_bin_are_produced_from_the_elf(built_hex, tmp_path):
    hex_path, _, build = built_hex

    assert hex_path.is_file()
    assert hex_path.read_text(encoding="utf-8").startswith(":10")  # Intel HEX record

    bin_path = AvrToolchain().elf_to_bin(build.elf_path, tmp_path / "firmware.bin")
    # The raw image must match what avr-size measured, byte for byte.
    assert bin_path.stat().st_size == build.memory.flash_used_bytes


@requires_avr
def test_converting_a_missing_elf_is_an_error(tmp_path):
    from core.exceptions import CompilationError

    with pytest.raises(CompilationError, match="does not exist"):
        AvrToolchain().elf_to_hex(tmp_path / "absent.elf", tmp_path / "out.hex")
