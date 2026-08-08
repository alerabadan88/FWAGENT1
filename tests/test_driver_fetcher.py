import zipfile
from pathlib import Path

import pytest

from services.driver_fetcher import (
    DriverFetchError,
    DriverIntegrityError,
    fetch_driver,
    sha256_of,
)
from services.driver_registry import DriverSpec, Framework

FIXTURES = Path(__file__).parent / "fixtures" / "drivers"
ARCHIVE = FIXTURES / "hcsr04_baremetal-1.0.0.zip"


def archive_url() -> str:
    return ARCHIVE.resolve().as_uri()


def make_spec(**overrides) -> DriverSpec:
    defaults = dict(
        part="HC-SR04",
        library_name="hcsr04_baremetal",
        version="1.0.0",
        url=archive_url(),
        sha256=sha256_of(ARCHIVE),
        license="MIT",
        framework=Framework.BARE_METAL_AVR,
        source_files=("hcsr04.c",),
    )
    defaults.update(overrides)
    return DriverSpec(**defaults)


def test_fixture_archive_exists():
    assert ARCHIVE.is_file(), "the driver fixture archive must be committed"


def test_fetch_installs_and_verifies_a_pinned_driver(tmp_path):
    installed = fetch_driver(make_spec(), Framework.BARE_METAL_AVR, tmp_path)

    assert installed.root.is_dir()
    assert [p.name for p in installed.source_files] == ["hcsr04.c"]
    assert (installed.root / "hcsr04.h").is_file()


def test_wrong_checksum_is_refused_and_nothing_is_installed(tmp_path):
    wrong = make_spec(sha256="0" * 64)

    with pytest.raises(DriverIntegrityError, match="failed integrity check"):
        fetch_driver(wrong, Framework.BARE_METAL_AVR, tmp_path)

    # The rejected driver must leave nothing behind to be compiled by accident.
    assert list(tmp_path.iterdir()) == []


def test_incompatible_driver_is_never_downloaded(tmp_path):
    spec = make_spec(framework=Framework.ARDUINO)

    with pytest.raises(DriverFetchError, match="refusing to fetch"):
        fetch_driver(spec, Framework.BARE_METAL_AVR, tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_missing_declared_source_file_is_rejected(tmp_path):
    spec = make_spec(source_files=("hcsr04.c", "does_not_exist.c"))

    with pytest.raises(DriverFetchError, match="not present in the archive"):
        fetch_driver(spec, Framework.BARE_METAL_AVR, tmp_path)


def test_unsupported_url_scheme_is_refused(tmp_path):
    spec = make_spec(url="ftp://example.invalid/x.zip")

    with pytest.raises(DriverFetchError):
        fetch_driver(spec, Framework.BARE_METAL_AVR, tmp_path)


def test_path_traversal_entries_are_refused(tmp_path):
    malicious = tmp_path / "evil.zip"
    with zipfile.ZipFile(malicious, "w") as zf:
        zf.writestr("../escaped.c", "int x;")

    spec = make_spec(url=malicious.resolve().as_uri(), sha256=sha256_of(malicious))

    with pytest.raises(DriverFetchError, match="escapes the destination"):
        fetch_driver(spec, Framework.BARE_METAL_AVR, tmp_path / "install")

    assert not (tmp_path / "escaped.c").exists()


def test_non_archive_payload_is_rejected(tmp_path):
    junk = tmp_path / "not_an_archive.bin"
    junk.write_bytes(b"this is not a zip or tar file")

    spec = make_spec(url=junk.resolve().as_uri(), sha256=sha256_of(junk))

    with pytest.raises(DriverFetchError, match="neither a zip nor a tar"):
        fetch_driver(spec, Framework.BARE_METAL_AVR, tmp_path / "install")


def test_sha256_of_matches_a_known_value():
    assert sha256_of(ARCHIVE) == sha256_of(ARCHIVE)
    assert len(sha256_of(ARCHIVE)) == 64
