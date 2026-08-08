"""Tests for looking a part up instead of keeping a list of them.

None of these call the API. The model's job — describing a part — sits behind
a backend protocol, so every deterministic decision is tested offline.

The theme throughout: a described register map is a *claim*, and the tests
check that it is treated as one. A profile that is merely coherent must never
pass for verified.
"""

import json

import pytest

from agents.part_lookup import PartLookup, PartLookupError, validate_profile
from agents.schemas import ConversionKind, Measurement, SensorProfile
from core.device_catalog import DeviceCatalog
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


class FakeBackend:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages):
        self.calls.append(messages)
        return self.response


def profile_json(**overrides) -> str:
    payload = {
        "part": "SHT31",
        "description": "Temperature and humidity sensor",
        "interface": "I2C",
        "default_address": 0x44,
        "alternate_addresses": [0x45],
        "id_register": None,
        "id_value": None,
        "init_writes": [[0x21, 0x30]],
        "startup_delay_ms": 20,
        "measurements": [{
            "name": "temperature", "unit": "centi_c", "start_register": 0xE0,
            "length": 2, "big_endian": True, "signed": False,
            "conversion": "linear", "scale_numerator": 17500,
            "scale_denominator": 65535, "offset": -4500,
        }],
        "datasheet_url": "https://sensirion.com/sht3x",
        "provenance": "Recalled from the SHT3x datasheet; not checked against it.",
    }
    payload.update(overrides)
    return json.dumps(payload)


# --- A described profile is never verified -----------------------------------


def test_a_described_profile_is_never_marked_verified():
    """The single most important property here."""
    profile = PartLookup.parse(profile_json())

    assert profile.verified is False


def test_the_generated_driver_says_it_is_unverified():
    profile = PartLookup.parse(profile_json())
    firmware = _generate_with(profile)

    source = firmware.files["sht31.c"]
    assert "UNVERIFIED REGISTER MAP" in source
    # The specific values a reviewer has to check are listed, not just a warning.
    assert "address        0x44" in source
    assert "startup write  0x21 <- 0x30" in source
    assert "0xE0" in source
    assert "not checked against it" in source  # the stated provenance


def test_the_header_carries_the_warning_too():
    """Anyone reading the API surface must see it, not only the implementation."""
    profile = PartLookup.parse(profile_json())
    firmware = _generate_with(profile)

    assert "has NOT been checked against a" in firmware.files["sht31.h"]


# --- Validation ---------------------------------------------------------------


def test_a_coherent_profile_has_no_shape_problems():
    assert validate_profile(PartLookup.parse(profile_json())) == []


@pytest.mark.parametrize("address", [0x00, 0x07, 0x78, 0x7F])
def test_reserved_addresses_are_rejected(address):
    profile = PartLookup.parse(profile_json(default_address=address))

    assert any("outside the usable 7-bit range" in p for p in validate_profile(profile))


def test_a_non_i2c_part_is_rejected():
    profile = PartLookup.parse(profile_json(interface="SPI"))

    assert any("only I2C" in p for p in validate_profile(profile))


def test_an_id_register_without_its_value_is_rejected():
    profile = PartLookup.parse(profile_json(id_register=0xD0, id_value=None))

    assert any("needs both" in p for p in validate_profile(profile))


def test_a_profile_with_no_measurements_is_rejected():
    """A part needing polynomial compensation comes back this way on purpose."""
    profile = PartLookup.parse(profile_json(
        part="BME280", measurements=[],
        provenance="Needs polynomial compensation; a hand-written driver is required.",
    ))

    assert any("would read nothing" in p for p in validate_profile(profile))


def test_a_profile_with_no_provenance_is_rejected():
    """Without it a reviewer has nothing to check the numbers against."""
    profile = PartLookup.parse(profile_json(provenance="   "))

    assert any("where its values came from" in p for p in validate_profile(profile))


def test_a_scaling_of_zero_is_rejected():
    profile = PartLookup.parse(profile_json(measurements=[{
        "name": "temperature", "unit": "c", "start_register": 0x00, "length": 2,
        "big_endian": True, "signed": False, "conversion": "linear",
        "scale_numerator": 0, "scale_denominator": 1, "offset": 0,
    }]))

    assert any("scales by zero" in p for p in validate_profile(profile))


def test_lookup_refuses_an_unusable_profile_rather_than_returning_it():
    backend = FakeBackend(profile_json(default_address=0x00))

    with pytest.raises(PartLookupError, match="cannot be used"):
        PartLookup(backend).describe("SHT31")


def test_malformed_model_output_is_rejected():
    with pytest.raises(PartLookupError, match="did not return valid JSON"):
        PartLookup.parse("not json at all")


def test_output_of_the_wrong_shape_is_rejected():
    with pytest.raises(PartLookupError, match="did not match the expected shape"):
        PartLookup.parse('{"part": "X", "default_address": "not a number"}')


def test_an_empty_part_name_never_reaches_the_model():
    backend = FakeBackend(profile_json())

    with pytest.raises(PartLookupError, match="no part name"):
        PartLookup(backend).describe("  ")

    assert backend.calls == []


def test_schematic_context_is_passed_to_the_model():
    backend = FakeBackend(profile_json())

    PartLookup(backend).describe("SHT31", context="wired to the I2C bus at U4")

    assert "U4" in backend.calls[0][-1]["content"]


# --- The generated driver actually works -------------------------------------


def _generate_with(profile: SensorProfile):
    from codegen.generator import generate_firmware

    facts = DeviceCatalog().facts("atmega328p")
    mcu = MCU(
        name="atmega328p", family="AVR", flash_kb=facts.flash_kb,
        ram_kb=facts.ram_kb, clock_mhz=16, gpio_pins=23, voltage=5.0,
    )
    analysis = PCBAnalysis(mcu=mcu, sensors=[Sensor(
        name=profile.part, type="described", interface=InterfaceType.I2C,
        bus="I2C1", address=f"0x{profile.default_address:02X}",
    )])
    return generate_firmware(
        analysis, device=facts, sensor_profiles={profile.part: profile}
    )


@requires_avr
def test_a_part_in_no_hardcoded_list_produces_a_building_driver(tmp_path):
    profile = PartLookup.parse(profile_json())
    firmware = _generate_with(profile)

    facts = DeviceCatalog().facts("atmega328p")
    mcu = MCU(name="atmega328p", family="AVR", flash_kb=facts.flash_kb,
              ram_kb=facts.ram_kb, clock_mhz=16, gpio_pins=23, voltage=5.0)
    build = BuildService().build(firmware, mcu, tmp_path)

    assert build.ok, build.diagnostics


@requires_avr
def test_the_generated_driver_survives_wall_wextra_werror(tmp_path):
    profile = PartLookup.parse(profile_json())
    firmware = _generate_with(profile)
    firmware.write_to(tmp_path)

    result = AvrToolchain().check_syntax(
        tmp_path / "sht31.c", mcu="atmega328p", f_cpu_hz=16_000_000,
        include_dirs=(tmp_path,), extra_flags=("-Wall", "-Wextra", "-Werror"),
    )

    assert result.ok, result.diagnostics
    assert result.diagnostics == ""


@requires_sim
@pytest.mark.parametrize("raw", [0, 13107, 26214, 65535])
def test_the_described_scaling_computes_correctly_on_the_target(tmp_path, raw):
    """raw * 17500 / 65535 overflows 16 bits, and AVR's int is 16 bits."""
    profile = PartLookup.parse(profile_json())
    firmware = _generate_with(profile)
    facts = DeviceCatalog().facts("atmega328p")
    mcu = MCU(name="atmega328p", family="AVR", flash_kb=facts.flash_kb,
              ram_kb=facts.ram_kb, clock_mhz=16, gpio_pins=23, voltage=5.0)

    expected = (raw * 17500) // 65535 - 4500
    report = SimulatorTestService().run(
        firmware, mcu,
        [Check(f"convert({raw})", f"sht31_convert_temperature({raw})", expected)],
        tmp_path,
    )

    assert report.ok, [
        f"expected {r.expected}, got {r.actual}" for r in report.failures
    ]
