from pathlib import Path

import pytest

from codegen.generator import generate_firmware
from core.eda_parser import parse_config_file
from services.build_service import BuildService, MemoryReport
from services.driver_fetcher import fetch_driver, sha256_of
from services.driver_registry import DriverSpec, Framework
from services.toolchain import AvrToolchain

FIXTURES = Path(__file__).parent / "fixtures"
ARCHIVE = FIXTURES / "drivers" / "hcsr04_baremetal-1.0.0.zip"

requires_avr = pytest.mark.skipif(
    not AvrToolchain.is_available(),
    reason="avr-gcc is not installed on this machine",
)


@pytest.fixture
def uno_analysis():
    return parse_config_file(FIXTURES / "arduino_uno_config.json")


def test_memory_report_computes_real_percentages():
    report = MemoryReport(
        text_bytes=200,
        data_bytes=24,
        bss_bytes=100,
        flash_capacity_bytes=32 * 1024,
        ram_capacity_bytes=2 * 1024,
    )

    assert report.flash_used_bytes == 224
    assert report.ram_used_bytes == 124
    assert report.flash_percent == 0.68
    assert report.ram_percent == 6.05
    assert report.fits


def test_memory_report_detects_an_overflowing_build():
    report = MemoryReport(
        text_bytes=40_000,
        data_bytes=0,
        bss_bytes=0,
        flash_capacity_bytes=32 * 1024,
        ram_capacity_bytes=2 * 1024,
    )

    assert not report.fits
    assert report.flash_percent > 100


def test_unknown_mcu_has_no_compiler_target(uno_analysis):
    mcu = uno_analysis.mcu.model_copy(update={"name": "STM32F405RGT6"})

    with pytest.raises(ValueError, match="no avr-gcc -mmcu target"):
        BuildService.mcu_target_for(mcu)


@requires_avr
def test_build_produces_an_elf_and_a_measured_memory_report(uno_analysis, tmp_path):
    firmware = generate_firmware(uno_analysis)

    result = BuildService().build(firmware, uno_analysis.mcu, tmp_path)

    assert result.ok, result.diagnostics
    assert result.mcu_target == "atmega328p"
    assert result.elf_path.is_file()

    # Measured from the real ELF, not estimated.
    assert result.memory.text_bytes > 0
    assert result.memory.flash_capacity_bytes == 32 * 1024
    assert result.memory.fits
    assert 0 < result.memory.flash_percent < 100


@requires_avr
def test_build_failure_reports_real_compiler_diagnostics(uno_analysis, tmp_path):
    firmware = generate_firmware(uno_analysis)
    firmware.files["main.c"] += "\nthis is not valid c;\n"

    result = BuildService().build(firmware, uno_analysis.mcu, tmp_path)

    assert result.status == "failed"
    assert not result.ok
    assert result.elf_path is None
    assert result.memory is None
    assert "error" in result.diagnostics.lower()


@requires_avr
def test_build_links_a_fetched_driver_into_the_firmware(uno_analysis, tmp_path):
    spec = DriverSpec(
        part="HC-SR04",
        library_name="hcsr04_baremetal",
        version="1.0.0",
        url=ARCHIVE.resolve().as_uri(),
        sha256=sha256_of(ARCHIVE),
        license="MIT",
        framework=Framework.BARE_METAL_AVR,
        source_files=("hcsr04.c",),
    )
    installed = fetch_driver(spec, Framework.BARE_METAL_AVR, tmp_path / "vendor")

    firmware = generate_firmware(uno_analysis)
    build_dir = tmp_path / "build"
    baseline = BuildService().build(firmware, uno_analysis.mcu, build_dir)

    with_driver = BuildService().build(
        firmware, uno_analysis.mcu, build_dir, drivers=[installed]
    )

    assert with_driver.ok, with_driver.diagnostics
    assert with_driver.drivers == ["hcsr04_baremetal@1.0.0"]
    # The driver's code is really in the binary, so the image must grow.
    assert with_driver.memory.text_bytes > baseline.memory.text_bytes
