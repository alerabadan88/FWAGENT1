"""Download, verify, and install pinned driver libraries.

The verification here is deterministic, not advisory: an archive whose SHA-256
does not match the pinned value is deleted and refused. This is the difference
between "we found a driver" and "we know exactly which bytes we are compiling".
"""

from __future__ import annotations

import hashlib
import shutil
import tarfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from core.exceptions import FWAgentError
from services.driver_registry import DriverSpec, Framework, check_compatibility


class DriverFetchError(FWAgentError):
    """Raised when a driver cannot be downloaded, verified, or installed."""


class DriverIntegrityError(DriverFetchError):
    """Raised when a downloaded archive does not match its pinned SHA-256."""


@dataclass
class InstalledDriver:
    """A driver that was fetched, verified, and unpacked on disk."""

    spec: DriverSpec
    root: Path
    source_files: list[Path] = field(default_factory=list)
    include_dirs: list[Path] = field(default_factory=list)


def sha256_of(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path, timeout: int = 60) -> None:
    if not (url.startswith("https://") or url.startswith("file://")):
        raise DriverFetchError(f"refusing to fetch from unsupported URL scheme: {url!r}")

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            destination.write_bytes(response.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise DriverFetchError(f"could not download {url}: {exc}") from exc


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


def _extract(archive: Path, destination: Path) -> None:
    """Unpack an archive, refusing entries that escape the destination."""
    destination.mkdir(parents=True, exist_ok=True)

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                if not _is_within(destination, destination / member):
                    raise DriverFetchError(
                        f"archive entry escapes the destination directory: {member!r}"
                    )
            zf.extractall(destination)
        return

    if tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                if member.issym() or member.islnk():
                    raise DriverFetchError(f"archive contains a link entry: {member.name!r}")
                if not _is_within(destination, destination / member.name):
                    raise DriverFetchError(
                        f"archive entry escapes the destination directory: {member.name!r}"
                    )
            tf.extractall(destination)
        return

    raise DriverFetchError(f"{archive.name} is neither a zip nor a tar archive")


def fetch_driver(
    spec: DriverSpec,
    target_framework: Framework,
    install_root: str | Path,
    timeout: int = 60,
) -> InstalledDriver:
    """Fetch, verify, and unpack one pinned driver.

    Gates, in order — each one must pass before the next runs:

    1. static compatibility (framework, license, declared sources, URL scheme)
    2. SHA-256 of the downloaded bytes matches the pinned value
    3. archive entries stay inside the install directory
    4. every declared source file actually exists after extraction

    Whether the driver *compiles* is deliberately not decided here; only the
    real toolchain can settle that.
    """
    report = check_compatibility(spec, target_framework)
    if not report.ok:
        raise DriverFetchError(f"refusing to fetch {spec.library_name}: {report.summary()}")

    install_root = Path(install_root)
    driver_root = install_root / f"{spec.library_name}-{spec.version}"
    if driver_root.exists():
        shutil.rmtree(driver_root)
    driver_root.mkdir(parents=True)

    archive = install_root / f"{spec.library_name}-{spec.version}.archive"
    try:
        _download(spec.url, archive, timeout=timeout)

        actual = sha256_of(archive)
        if actual.lower() != spec.sha256.lower():
            archive.unlink(missing_ok=True)
            shutil.rmtree(driver_root, ignore_errors=True)
            raise DriverIntegrityError(
                f"{spec.library_name} {spec.version} failed integrity check: "
                f"expected sha256 {spec.sha256}, got {actual}. The archive was discarded."
            )

        _extract(archive, driver_root)
    finally:
        archive.unlink(missing_ok=True)

    source_files = []
    for relative in spec.source_files:
        candidate = driver_root / relative
        if not candidate.is_file():
            shutil.rmtree(driver_root, ignore_errors=True)
            raise DriverFetchError(
                f"{spec.library_name} {spec.version} declares source file "
                f"'{relative}' which is not present in the archive"
            )
        source_files.append(candidate)

    include_dirs = []
    for relative in spec.include_dirs:
        candidate = driver_root / relative
        if not candidate.is_dir():
            shutil.rmtree(driver_root, ignore_errors=True)
            raise DriverFetchError(
                f"{spec.library_name} {spec.version} declares include dir "
                f"'{relative}' which is not present in the archive"
            )
        include_dirs.append(candidate)

    return InstalledDriver(
        spec=spec,
        root=driver_root,
        source_files=source_files,
        include_dirs=include_dirs,
    )
