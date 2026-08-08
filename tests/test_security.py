"""Tests for the security artefacts.

The point of these is that the report tells the truth in both directions: it
must not mark a measure present when the firmware does not implement it, and
it must not quietly drop the obligations a generator cannot discharge.
"""

import json
from pathlib import Path

import pytest

from codegen.generator import generate_firmware
from core.device_catalog import DeviceCatalog
from core.eda_parser import parse_config_file
from services.build_service import BuildService
from services.security import (
    Measure,
    SecurityReport,
    _looks_like_secret,
    assess,
    generate_sbom,
    render_report,
    sha256_of,
    toolchain_versions,
    write_security_artifacts,
)
from services.toolchain import AvrToolchain

FIXTURES = Path(__file__).parent / "fixtures"

requires_avr = pytest.mark.skipif(
    not AvrToolchain.is_available(), reason="avr-gcc is not installed on this machine"
)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """A real build, so the report describes something that exists."""
    directory = tmp_path_factory.mktemp("secure")
    analysis = parse_config_file(FIXTURES / "arduino_uno_config.json")
    firmware = generate_firmware(analysis)
    build = BuildService().build(firmware, analysis.mcu, directory)
    assert build.ok, build.diagnostics
    hex_path = AvrToolchain().elf_to_hex(build.elf_path, directory / "firmware.hex")
    device = DeviceCatalog().facts(analysis.mcu.name)
    build_id = firmware.files["config.h"].split("BUILD_ID")[1].split('"')[1]
    return firmware, device, build, hex_path, build_id, directory


# --- Secret detection (no toolchain needed) -----------------------------------


@pytest.mark.parametrize(
    "text",
    [
        'const char *k = "hello";',
        "uart_write_string(\"DHT22 T=\");",
        "#define UART_BAUD 9600",
    ],
)
def test_ordinary_firmware_text_is_not_flagged(text):
    assert not _looks_like_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        'static const char *api_key = "sk-abcdefghijkl";',
        'char password = "hunter2xyz";',
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_credential_shaped_literals_are_flagged(text):
    assert _looks_like_secret(text)


# --- The report must be able to say "no" --------------------------------------


def test_a_measure_is_not_marked_present_without_the_code_for_it():
    """A report that always says yes is worth nothing."""

    class Bare:
        files = {"main.c": "int main(void){for(;;){}}", "config.h": ""}

    report = assess(Bare(), device=_fake_device(), build=None)

    absent = {m.name for m in report.missing}
    assert "Watchdog recovery" in absent
    assert "Firmware identity on the wire" in absent
    assert "Unexpected reset is reported" in absent


def test_manufacturer_obligations_are_always_listed():
    report = assess(_bare(), device=_fake_device(), build=None)

    joined = " ".join(report.out_of_scope).lower()
    assert "risk assessment" in joined
    assert "vulnerability disclosure" in joined
    assert "support period" in joined
    assert "ce marking" in joined


def test_every_measure_states_what_it_does_not_cover():
    report = assess(_bare(), device=_fake_device(), build=None)

    for measure in report.measures:
        assert measure.limitation.strip(), measure.name
        assert measure.requirement.strip(), measure.name


def test_the_rendered_report_refuses_to_claim_compliance():
    report = SecurityReport(measures=[
        Measure("X", True, "detail", "Annex I Part I(2)(h)", "limit"),
    ])

    text = render_report(report, _fake_device(), "1.0.0", "abcd1234")

    assert "not a compliance claim" in text
    assert "cannot confer" in text
    # It must never assert the firmware *is* compliant.
    assert "is compliant" not in text.lower()
    assert "fully compliant" not in text.lower()


def test_unimplemented_measures_appear_in_the_rendered_report():
    report = SecurityReport(measures=[
        Measure("Signed updates", False, "Not implemented.", "Annex I Part II(8)", "needs a bootloader"),
    ])

    text = render_report(report, _fake_device(), "1.0.0", "abcd1234")

    assert "## Not implemented" in text
    assert "Signed updates" in text


# --- Against a real build -----------------------------------------------------


@requires_avr
def test_the_generated_firmware_implements_the_measures_it_claims(built):
    firmware, device, build, hex_path, _, _ = built

    report = assess(firmware, device, build, artifacts={"hex": hex_path})
    present = {m.name for m in report.implemented}

    assert "Watchdog recovery" in present
    assert "Serial receiver disabled" in present
    assert "Firmware identity on the wire" in present
    assert "No dynamic allocation" in present


@requires_avr
def test_measures_the_firmware_lacks_are_reported_as_missing(built):
    """Update signing and transport protection genuinely are not there."""
    firmware, device, build, hex_path, _, _ = built

    report = assess(firmware, device, build, artifacts={"hex": hex_path})
    missing = {m.name for m in report.missing}

    assert "Secure update mechanism" in missing
    assert "Data-in-transit protection" in missing


@requires_avr
def test_the_watchdog_reaches_the_generated_sources(built):
    firmware, *_ = built

    assert "wdt_enable" in firmware.files["watchdog.c"]
    # The reset-loop trap: the WDT must be disabled before anything slow runs.
    assert '.init3' in firmware.files["watchdog.c"]
    assert "MCUSR" in firmware.files["watchdog.c"]
    assert "watchdog_kick" in firmware.files["main.c"]


# --- SBOM ---------------------------------------------------------------------


@requires_avr
def test_the_sbom_is_valid_cyclonedx(built):
    firmware, device, build, hex_path, build_id, _ = built

    sbom = generate_sbom(firmware, device, "0.1.0", build_id, artifacts={"hex": hex_path})

    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.5"
    assert sbom["serialNumber"].startswith("urn:uuid:")
    assert sbom["metadata"]["component"]["type"] == "firmware"


@requires_avr
def test_the_sbom_records_the_real_toolchain_version(built):
    """Versions must come from the installed tools, not from a literal here."""
    firmware, device, build, hex_path, build_id, _ = built
    versions = toolchain_versions()

    sbom = generate_sbom(firmware, device, "0.1.0", build_id)

    libc = next(c for c in sbom["components"] if c["name"] == "avr-libc")
    assert libc["version"] == versions["avr-libc"]
    gcc = next(t for t in sbom["metadata"]["tools"] if t["name"] == "avr-gcc")
    assert gcc["version"] == versions["avr-gcc"]


@requires_avr
def test_every_source_file_is_in_the_sbom_with_a_digest(built):
    firmware, device, build, hex_path, build_id, _ = built

    sbom = generate_sbom(firmware, device, "0.1.0", build_id)

    named = {c["name"] for c in sbom["components"]}
    assert firmware.files.keys() <= named
    for component in sbom["components"]:
        if component["type"] == "file":
            assert component["hashes"][0]["alg"] == "SHA-256"
            assert len(component["hashes"][0]["content"]) == 64


# --- Artefacts on disk --------------------------------------------------------


@requires_avr
def test_the_artefacts_are_written_and_the_digests_are_real(built, tmp_path):
    firmware, device, build, hex_path, build_id, _ = built

    written = write_security_artifacts(
        tmp_path, firmware, device, build, "0.1.0", build_id,
        artifacts={"hex": hex_path, "elf": build.elf_path},
    )

    assert set(written) == {"sbom", "manifest", "report"}
    json.loads(written["sbom"].read_text(encoding="utf-8"))  # parses

    manifest = written["manifest"].read_text(encoding="utf-8")
    # The digest recorded must be the digest of the file that exists.
    assert sha256_of(hex_path) in manifest
    assert "firmware.hex" in manifest

    assert "not a compliance claim" in written["report"].read_text(encoding="utf-8")


def _fake_device():
    from core.device_catalog import DeviceFacts

    return DeviceFacts(
        part="atmega328p", core="avr5", flash_bytes=32768, ram_bytes=2048,
        eeprom_bytes=1024, flash_page_bytes=128, peripherals=frozenset({"adc"}),
    )


def _bare():
    class Bare:
        files = {"main.c": "", "config.h": ""}

    return Bare()
