import pytest

from services.driver_registry import (
    ALLOWED_LICENSES,
    DriverRegistry,
    DriverSpec,
    Framework,
    check_compatibility,
)

GOOD_SHA = "9e2ac720ca9af39c58e02f725d29be8089545f6c7694ac8acb91eeb71dfb24e0"


def make_spec(**overrides) -> DriverSpec:
    defaults = dict(
        part="HC-SR04",
        library_name="hcsr04_baremetal",
        version="1.0.0",
        url="https://example.invalid/hcsr04-1.0.0.zip",
        sha256=GOOD_SHA,
        license="MIT",
        framework=Framework.BARE_METAL_AVR,
        source_files=("hcsr04.c",),
    )
    defaults.update(overrides)
    return DriverSpec(**defaults)


def test_malformed_sha256_is_rejected_at_construction():
    with pytest.raises(ValueError, match="malformed sha256"):
        make_spec(sha256="not-a-hash")


def test_compatible_driver_passes_every_gate():
    report = check_compatibility(make_spec(), Framework.BARE_METAL_AVR)

    assert report.ok
    assert report.failed == []
    assert len(report.passed) == 4


def test_arduino_driver_is_rejected_for_a_bare_metal_build():
    spec = make_spec(framework=Framework.ARDUINO)

    report = check_compatibility(spec, Framework.BARE_METAL_AVR)

    assert not report.ok
    assert any("framework mismatch" in reason for reason in report.failed)


def test_disallowed_license_is_rejected():
    spec = make_spec(license="GPL-3.0")

    report = check_compatibility(spec, Framework.BARE_METAL_AVR)

    assert not report.ok
    assert any("not in the allowlist" in reason for reason in report.failed)
    assert "GPL-3.0" not in ALLOWED_LICENSES


def test_driver_with_no_source_files_is_rejected():
    report = check_compatibility(make_spec(source_files=()), Framework.BARE_METAL_AVR)

    assert any("no source files" in reason for reason in report.failed)


def test_insecure_url_scheme_is_rejected():
    report = check_compatibility(
        make_spec(url="http://example.invalid/x.zip"), Framework.BARE_METAL_AVR
    )

    assert any("insecure URL scheme" in reason for reason in report.failed)


def test_registry_resolves_the_first_compatible_candidate():
    arduino = make_spec(library_name="DHT_arduino", framework=Framework.ARDUINO)
    bare = make_spec(library_name="dht_baremetal")
    registry = DriverRegistry([arduino, bare])

    resolved = registry.resolve("HC-SR04", Framework.BARE_METAL_AVR)

    assert resolved is not None
    assert resolved.library_name == "dht_baremetal"


def test_registry_returns_none_when_nothing_is_compatible():
    registry = DriverRegistry([make_spec(framework=Framework.ARDUINO)])

    assert registry.resolve("HC-SR04", Framework.BARE_METAL_AVR) is None


def test_rejection_reasons_explain_why_nothing_matched():
    registry = DriverRegistry([make_spec(framework=Framework.ARDUINO)])

    reasons = registry.rejection_reasons("HC-SR04", Framework.BARE_METAL_AVR)

    assert len(reasons) == 1
    assert "framework mismatch" in reasons[0]


def test_rejection_reasons_for_an_unknown_part():
    reasons = DriverRegistry().rejection_reasons("BME680", Framework.BARE_METAL_AVR)

    assert reasons == ["no driver is registered for part 'BME680'"]


def test_part_lookup_is_case_insensitive():
    registry = DriverRegistry([make_spec()])

    assert registry.candidates_for("hc-sr04") == registry.candidates_for("HC-SR04")
    assert registry.parts() == ["HC-SR04"]
