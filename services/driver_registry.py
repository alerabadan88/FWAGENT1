"""Registry of driver libraries that can be fetched and built into firmware.

Every entry is *pinned*: a fixed version, a fixed URL, and the SHA-256 of the
archive at that URL. Nothing is resolved as "latest" — an unpinned dependency
is one whose contents can change under you between builds.

A driver is only usable if it passes every gate in :func:`check_compatibility`.
An agent may *propose* candidates; it does not get to declare them good.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Framework(str, Enum):
    """Which runtime a driver's source expects to be compiled against."""

    BARE_METAL_AVR = "bare-metal-avr"
    ARDUINO = "arduino"


# Licenses we are willing to vendor into generated firmware.
ALLOWED_LICENSES = frozenset({"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "Unlicense", "ISC"})


@dataclass(frozen=True)
class DriverSpec:
    """A pinned, verifiable driver library for one sensor part."""

    part: str
    library_name: str
    version: str
    url: str
    sha256: str
    license: str
    framework: Framework
    source_files: tuple[str, ...] = ()
    include_dirs: tuple[str, ...] = ()
    homepage: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if len(self.sha256) != 64 or not all(c in "0123456789abcdef" for c in self.sha256.lower()):
            raise ValueError(
                f"driver '{self.library_name}' has a malformed sha256: {self.sha256!r}"
            )


@dataclass
class CompatibilityReport:
    """Result of checking a candidate driver against a target build."""

    spec: DriverSpec
    target_framework: Framework
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self) -> str:
        if self.ok:
            return f"{self.spec.library_name} {self.spec.version}: all checks passed"
        return f"{self.spec.library_name} {self.spec.version}: " + "; ".join(self.failed)


def check_compatibility(spec: DriverSpec, target_framework: Framework) -> CompatibilityReport:
    """Run the static gates a driver must clear before it is fetched.

    These are the checks answering "is this the right driver?" that can be
    decided without downloading anything. Integrity (SHA-256) is verified at
    fetch time, and the final gate — does it actually compile — can only be
    settled by the real toolchain.
    """
    report = CompatibilityReport(spec=spec, target_framework=target_framework)

    if spec.framework == target_framework:
        report.passed.append(f"framework matches target ({target_framework.value})")
    else:
        report.failed.append(
            f"framework mismatch: driver targets {spec.framework.value}, "
            f"build targets {target_framework.value}"
        )

    if spec.license in ALLOWED_LICENSES:
        report.passed.append(f"license {spec.license} is allowed")
    else:
        report.failed.append(
            f"license {spec.license!r} is not in the allowlist {sorted(ALLOWED_LICENSES)}"
        )

    if spec.source_files:
        report.passed.append(f"declares {len(spec.source_files)} source file(s)")
    else:
        report.failed.append("declares no source files, so nothing would be compiled")

    if spec.url.startswith("https://") or spec.url.startswith("file://"):
        report.passed.append("uses a supported URL scheme")
    else:
        report.failed.append(f"unsupported or insecure URL scheme: {spec.url!r}")

    return report


class DriverRegistry:
    """Lookup of known driver specs by sensor part name."""

    def __init__(self, specs: list[DriverSpec] | None = None) -> None:
        self._by_part: dict[str, list[DriverSpec]] = {}
        for spec in specs or []:
            self.register(spec)

    def register(self, spec: DriverSpec) -> None:
        self._by_part.setdefault(spec.part.upper(), []).append(spec)

    def candidates_for(self, part: str) -> list[DriverSpec]:
        return list(self._by_part.get(part.upper(), []))

    def resolve(self, part: str, target_framework: Framework) -> DriverSpec | None:
        """Return the first candidate that clears every static gate, else ``None``."""
        for spec in self.candidates_for(part):
            if check_compatibility(spec, target_framework).ok:
                return spec
        return None

    def rejection_reasons(self, part: str, target_framework: Framework) -> list[str]:
        """Explain why no candidate was usable — for diagnostics, not guessing."""
        candidates = self.candidates_for(part)
        if not candidates:
            return [f"no driver is registered for part '{part}'"]
        return [check_compatibility(s, target_framework).summary() for s in candidates]

    def parts(self) -> list[str]:
        return sorted(self._by_part)
