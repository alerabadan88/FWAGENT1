from pathlib import Path

import pytest

from codegen.generator import generate_firmware
from core.eda_parser import parse_config_file
from services.test_service import Check, SimulationError, SimulatorTestService, SimulationReport
from services.toolchain import AvrToolchain

FIXTURES = Path(__file__).parent / "fixtures"

requires_sim = pytest.mark.skipif(
    not AvrToolchain.is_available() or not AvrToolchain.simulator_available(),
    reason="avr-gcc/avr-gdb are not installed on this machine",
)


@pytest.fixture
def uno_firmware():
    analysis = parse_config_file(FIXTURES / "arduino_uno_config.json")
    return generate_firmware(analysis), analysis.mcu


def test_a_not_run_report_never_claims_success():
    report = SimulationReport(status="not_run", diagnostics="no checks were given")

    assert report.ok is False
    assert report.passed_count == 0
    assert "not run" in report.summary()


def test_parse_results_reads_a_gdb_array():
    values = SimulatorTestService._parse_results("$1 = {686, 0, -101}", expected_count=3)

    assert values == [686, 0, -101]


def test_parse_results_expands_gdb_repeat_runs():
    values = SimulatorTestService._parse_results(
        "$1 = {0 <repeats 4 times>, 7}", expected_count=5
    )

    assert values == [0, 0, 0, 0, 7]


def test_parse_results_rejects_output_with_no_array():
    with pytest.raises(SimulationError, match="could not find the result array"):
        SimulatorTestService._parse_results("Breakpoint 1 at 0x90", expected_count=1)


def test_parse_results_rejects_a_wrong_length_array():
    with pytest.raises(SimulationError, match="expected 3 results"):
        SimulatorTestService._parse_results("$1 = {1, 2}", expected_count=3)


# --- On-target execution -----------------------------------------------------


@requires_sim
def test_driver_arithmetic_runs_correctly_on_the_simulated_mcu(uno_firmware, tmp_path):
    firmware, mcu = uno_firmware
    checks = [
        Check("ticks_to_mm(1000)", "hc_sr04_ticks_to_mm(1000)", 686),
        # 9500 * 686 overflows 16 bits; this proves the 32-bit intermediate works
        # under the target's integer widths, not the host's.
        Check("ticks_to_mm(9500)", "hc_sr04_ticks_to_mm(9500)", 6517),
        Check("ticks_to_mm(0)", "hc_sr04_ticks_to_mm(0)", 0),
    ]

    report = SimulatorTestService().run(firmware, mcu, checks, tmp_path)

    assert report.status == "success", report.diagnostics
    assert report.ok, [f"{r.name}: expected {r.expected}, got {r.actual}" for r in report.failures]
    assert report.passed_count == 3


@requires_sim
def test_dht22_frame_decoding_runs_on_the_simulated_mcu(uno_firmware, tmp_path):
    firmware, mcu = uno_firmware
    checks = [
        Check(
            "valid frame accepted",
            "dht22_decode(good, &t, &rh)",
            0,  # SENSOR_OK
            setup="uint8_t good[5] = {0x02,0x92,0x01,0x0D,0xA2}; int16_t t=0; uint16_t rh=0;",
        ),
        Check("humidity decoded", "rh", 658),
        Check("temperature decoded", "t", 269),
        Check(
            "bad checksum rejected",
            "dht22_decode(bad, 0, 0)",
            2,  # SENSOR_ERR_CHECKSUM
            setup="uint8_t bad[5] = {0x02,0x92,0x01,0x0D,0x00};",
        ),
        Check(
            "sign flag yields a negative temperature",
            "dht22_decode(neg, &t2, 0) == 0 ? t2 : -9999",
            -101,
            setup="uint8_t neg[5]={0x00,0x00,0x80,0x65,0xE5}; int16_t t2=0;",
        ),
    ]

    report = SimulatorTestService().run(firmware, mcu, checks, tmp_path)

    assert report.ok, [f"{r.name}: expected {r.expected}, got {r.actual}" for r in report.failures]


@requires_sim
def test_a_wrong_expectation_is_reported_as_a_failure(uno_firmware, tmp_path):
    """The suite must be able to fail — otherwise passing means nothing."""
    firmware, mcu = uno_firmware
    checks = [
        Check("correct", "hc_sr04_ticks_to_mm(1000)", 686),
        Check("deliberately wrong", "hc_sr04_ticks_to_mm(1000)", 999),
    ]

    report = SimulatorTestService().run(firmware, mcu, checks, tmp_path)

    assert report.status == "success"  # the run itself worked
    assert not report.ok  # but a check did not
    assert report.passed_count == 1
    assert [r.name for r in report.failures] == ["deliberately wrong"]
    assert report.failures[0].actual == 686


@requires_sim
def test_a_harness_that_does_not_compile_reports_failed_not_success(uno_firmware, tmp_path):
    firmware, mcu = uno_firmware
    checks = [Check("nonsense", "this_function_does_not_exist(1)", 0)]

    report = SimulatorTestService().run(firmware, mcu, checks, tmp_path)

    assert report.status == "failed"
    assert not report.ok
    assert report.results == []
    assert "harness did not build" in report.diagnostics
