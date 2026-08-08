"""Security artefacts for a build: an SBOM, a hash manifest, and an honest report.

**This does not make firmware compliant with anything, and does not claim to.**

The EU Cyber Resilience Act (Regulation (EU) 2024/2847) places obligations on a
*manufacturer*, not on a file: a risk assessment, technical documentation, a
coordinated vulnerability disclosure policy, a defined support period, and
conformity assessment leading to CE marking. None of that is a property a code
generator can confer, and a tool that says otherwise is selling a false comfort
that shows up as a failed audit.

What a generator *can* do is implement specific technical measures and produce
evidence of them that a human can check. That is what this module emits:

* a **CycloneDX SBOM** -- Annex I Part II(1) requires a software bill of
  materials in a commonly used, machine-readable format covering at least the
  top-level dependencies, and this is the most literal, checkable requirement
  in the whole regulation;
* a **manifest** of SHA-256 digests, so the image on a device can be matched to
  the build that produced it;
* a **report** naming each measure, the requirement it speaks to, and -- at
  least as importantly -- what it does not cover.

Versions come from the installed toolchain, not from this file, so the SBOM
describes the build that actually happened.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.device_catalog import DeviceFacts
from services.toolchain import AvrToolchain

CYCLONEDX_SPEC_VERSION = "1.5"


@dataclass
class Measure:
    """One technical measure, with what it does and does not achieve."""

    name: str
    implemented: bool
    detail: str
    requirement: str
    limitation: str = ""


@dataclass
class SecurityReport:
    measures: list[Measure] = field(default_factory=list)
    manifest: dict[str, str] = field(default_factory=dict)
    out_of_scope: list[str] = field(default_factory=list)

    @property
    def implemented(self) -> list[Measure]:
        return [m for m in self.measures if m.implemented]

    @property
    def missing(self) -> list[Measure]:
        return [m for m in self.measures if not m.implemented]


def sha256_of(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def toolchain_versions(toolchain: AvrToolchain | None = None) -> dict[str, str]:
    """Read the real versions out of the installed toolchain."""
    toolchain = toolchain or AvrToolchain()
    versions: dict[str, str] = {}

    try:
        gcc = subprocess.run(
            [str(toolchain.gcc_path), "-dumpversion"],
            capture_output=True, text=True, timeout=30,
        )
        if gcc.returncode == 0:
            versions["avr-gcc"] = gcc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.c"
            probe.write_text("", encoding="utf-8")
            libc = subprocess.run(
                [str(toolchain.gcc_path), "-mmcu=atmega328p", "-dM", "-E",
                 "-include", "avr/version.h", str(probe)],
                capture_output=True, text=True, timeout=30,
            )
        match = re.search(r'__AVR_LIBC_VERSION_STRING__\s+"([^"]+)"', libc.stdout)
        if match:
            versions["avr-libc"] = match.group(1)
    except (OSError, subprocess.SubprocessError):
        pass

    return versions


def generate_sbom(
    firmware,
    device: DeviceFacts,
    firmware_version: str,
    build_id: str,
    toolchain: AvrToolchain | None = None,
    artifacts: dict[str, Path] | None = None,
) -> dict:
    """Build a CycloneDX SBOM describing what actually went into the image.

    Both real dependencies are listed: avr-libc is linked into the binary, and
    every generated source file appears with its digest, so a reviewer can tell
    whether the shipped image was built from the sources they were shown.
    """
    versions = toolchain_versions(toolchain)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    components: list[dict] = []

    if "avr-libc" in versions:
        components.append({
            "type": "library",
            "name": "avr-libc",
            "version": versions["avr-libc"],
            "scope": "required",
            "description": "C runtime linked into the firmware image",
            "licenses": [{"license": {"id": "BSD-3-Clause"}}],
        })

    for name, content in sorted(firmware.files.items()):
        components.append({
            "type": "file",
            "name": name,
            "version": firmware_version,
            "scope": "required",
            "hashes": [{"alg": "SHA-256", "content": sha256_of_text(content)}],
            "description": "generated firmware source",
        })

    for label, path in sorted((artifacts or {}).items()):
        path = Path(path)
        if path.is_file():
            components.append({
                "type": "file",
                "name": path.name,
                "version": firmware_version,
                "scope": "required",
                "hashes": [{"alg": "SHA-256", "content": sha256_of(path)}],
                "description": f"build artefact ({label})",
            })

    tools = [
        {"vendor": "GNU", "name": "avr-gcc", "version": versions.get("avr-gcc", "unknown")},
        {"vendor": "fw-automation-agent", "name": "generator", "version": firmware_version},
    ]

    return {
        "bomFormat": "CycloneDX",
        "specVersion": CYCLONEDX_SPEC_VERSION,
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": now,
            "tools": tools,
            "component": {
                "type": "firmware",
                "name": f"{device.part}-firmware",
                "version": firmware_version,
                "description": f"Generated firmware for {device.part}, build {build_id}",
            },
            "properties": [
                {"name": "fwagent:build_id", "value": build_id},
                {"name": "fwagent:target", "value": device.part},
                {"name": "fwagent:core", "value": device.core},
            ],
        },
        "components": components,
    }


def assess(
    firmware,
    device: DeviceFacts,
    build,
    artifacts: dict[str, Path] | None = None,
) -> SecurityReport:
    """Check which technical measures are present in this specific build."""
    sources = "\n".join(firmware.files.values())
    header = firmware.files.get("config.h", "")

    measures = [
        Measure(
            name="Watchdog recovery",
            implemented="watchdog_init" in sources and "wdt_enable" in sources,
            detail=(
                "The watchdog is armed at startup and fed each loop, so a device "
                "whose loop wedges resets instead of going quiet. The post-reset "
                "trap is handled: the WDT is disabled from .init3 before anything "
                "slow runs, which is what stops a watchdog reset becoming a reset "
                "loop."
            ),
            requirement="Annex I Part I(2)(h)/(m): availability, and limiting incident impact",
            limitation=(
                "It recovers a hung loop; it does not detect a loop that keeps "
                "running while producing wrong results. It is also NOT verified "
                "by simulation: GDB's AVR simulator does not implement the WDR "
                "instruction, so compilation is the only evidence for this one "
                "short of real hardware."
            ),
        ),
        Measure(
            name="Serial receiver disabled",
            implemented="1 << TXEN" in sources and "RXEN" not in sources,
            detail=(
                "Only the transmitter is enabled, so the firmware exposes no serial "
                "input to accept commands on."
            ),
            requirement="Annex I Part I(2)(l): limit attack surfaces",
            limitation=(
                "The bootloader still accepts a firmware write over the same pins. "
                "Preventing that is a fuse and lock-bit decision, made when the "
                "device is programmed, not in this firmware."
            ),
        ),
        Measure(
            name="Firmware identity on the wire",
            implemented="FIRMWARE_VERSION" in header and "BUILD_ID" in header,
            detail=(
                "Version and a build id derived from the configuration are printed "
                "at boot, so a unit on a bench can be matched to the build that "
                "produced it -- the prerequisite for knowing whether it carries a fix."
            ),
            requirement="Annex I Part II(1): identify and document components",
            limitation="The identity is reported, not attested; nothing signs it.",
        ),
        Measure(
            name="Unexpected reset is reported",
            implemented="watchdog_caused_last_reset" in sources,
            detail=(
                "A boot after a watchdog reset says so, because a device rebooting "
                "in a loop looks identical to a working one from outside."
            ),
            requirement="Annex I Part I(2)(j): record relevant internal activity",
            limitation=(
                "It is printed on the serial line, not stored. A device nobody is "
                "listening to keeps no record."
            ),
        ),
        Measure(
            name="No dynamic allocation",
            implemented="malloc" not in sources and "calloc" not in sources,
            detail=(
                "No heap is used, so there is no heap to exhaust or corrupt, and "
                "memory use is bounded at link time."
            ),
            requirement="Annex I Part I(2)(e): protect integrity",
            limitation="Stack depth is still unbounded by anything but review.",
        ),
        Measure(
            name="Build fits with headroom",
            implemented=bool(build and build.memory and build.memory.fits),
            detail=(
                f"Measured from the linked image: "
                f"{build.memory.flash_percent} % flash, {build.memory.ram_percent} % RAM."
                if build and build.memory else "Not measured."
            ),
            requirement="Annex I Part I(2)(h): availability under expected load",
            limitation="Static sizes only; it says nothing about runtime stack growth.",
        ),
        Measure(
            name="Secrets absent from the image",
            implemented=not _looks_like_secret(sources),
            detail=(
                "No credential-shaped literal was found in the generated sources. "
                "The firmware transmits only sensor readings and needs no secret."
            ),
            requirement="Annex I Part I(2)(d): protect confidentiality",
            limitation=(
                "A textual scan of generated code. It cannot speak for anything "
                "hand-added afterwards."
            ),
        ),
        Measure(
            name="All drivers verified against a datasheet",
            implemented="UNVERIFIED REGISTER MAP" not in sources,
            detail=(
                "Every driver in this build has a hand-written, separately "
                "verified register map."
                if "UNVERIFIED REGISTER MAP" not in sources else
                "At least one driver was built from a described register map "
                "that nobody has checked against a datasheet. Its own header "
                "lists the addresses to verify. A wrong one does not fail "
                "loudly -- it reports plausible numbers that are wrong."
            ),
            requirement="Annex I Part I(2)(e): integrity of processed data",
            limitation=(
                "A textual check for the marker the generator emits. It cannot "
                "tell whether a hand-written map is itself correct."
            ),
        ),
        Measure(
            name="Secure update mechanism",
            implemented=False,
            detail=(
                "Not implemented. Updates go through the stock serial bootloader, "
                "which authenticates nothing: anyone with physical access to the "
                "port can write different firmware."
            ),
            requirement="Annex I Part I(2)(c)/Part II(8): secure and, where applicable, automatic updates",
            limitation="Closing this needs a signed bootloader, which this generator does not produce.",
        ),
        Measure(
            name="Data-in-transit protection",
            implemented=False,
            detail=(
                "Not implemented. Readings go out over plain UART, readable and "
                "modifiable by anything on those pins."
            ),
            requirement="Annex I Part I(2)(e): protect integrity of transmitted data",
            limitation=(
                "Appropriate for a wired sensor on a closed board; not appropriate "
                "if the link leaves the enclosure."
            ),
        ),
    ]

    manifest: dict[str, str] = {}
    for label, path in sorted((artifacts or {}).items()):
        path = Path(path)
        if path.is_file():
            manifest[path.name] = sha256_of(path)

    return SecurityReport(
        measures=measures,
        manifest=manifest,
        out_of_scope=[
            "Risk assessment for the intended use, and the technical documentation "
            "that must accompany it (Article 13, Annex VII)",
            "A coordinated vulnerability disclosure policy and a contact point for "
            "reports (Annex I Part II(5))",
            "A defined support period, and the process for shipping security updates "
            "during it (Article 13(8), Annex I Part II)",
            "Reporting actively exploited vulnerabilities and severe incidents to "
            "ENISA/CSIRT within the required deadlines (Article 14)",
            "Conformity assessment and CE marking (Articles 32, 30)",
            "Fuse and lock-bit settings, which are applied when the device is "
            "programmed and are outside anything this firmware controls",
        ],
    )


def _looks_like_secret(text: str) -> bool:
    patterns = [
        r'(?i)\b(password|passwd|api[_-]?key|secret|token|private[_-]?key)\s*=\s*"[^"]{6,}"',
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def render_report(
    report: SecurityReport, device: DeviceFacts, firmware_version: str, build_id: str
) -> str:
    """Render the report as Markdown, gaps included."""
    lines = [
        f"# Security measures — {device.part} firmware {firmware_version} ({build_id})",
        "",
        "> **This is evidence, not a compliance claim.** The EU Cyber Resilience Act",
        "> (Regulation (EU) 2024/2847) obliges a *manufacturer* — risk assessment,",
        "> technical documentation, a vulnerability disclosure policy, a support",
        "> period, conformity assessment, CE marking. A generator cannot confer any",
        "> of those. What follows is what this build does and does not do, so a",
        "> human can judge the rest.",
        "",
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}.",
        "",
        "## Implemented",
        "",
    ]

    for measure in report.implemented:
        lines += [
            f"### {measure.name}",
            "",
            measure.detail,
            "",
            f"- *Speaks to:* {measure.requirement}",
            f"- *Does not cover:* {measure.limitation or 'nothing noted'}",
            "",
        ]

    lines += ["## Not implemented", ""]
    for measure in report.missing:
        lines += [
            f"### {measure.name}",
            "",
            measure.detail,
            "",
            f"- *Would speak to:* {measure.requirement}",
            f"- *What closing it needs:* {measure.limitation or 'not analysed'}",
            "",
        ]

    lines += [
        "## Outside anything a generator can do",
        "",
        "These are obligations on the manufacturer. They are listed so they are not",
        "mistaken for handled:",
        "",
    ]
    lines += [f"- {item}" for item in report.out_of_scope]

    if report.manifest:
        lines += ["", "## Artefact digests (SHA-256)", "", "| File | Digest |", "|---|---|"]
        lines += [f"| `{name}` | `{digest}` |" for name, digest in sorted(report.manifest.items())]
        lines += [
            "",
            "Compare these against what is on the device to confirm the shipped image",
            "is the one built from these sources.",
        ]

    return "\n".join(lines) + "\n"


def write_security_artifacts(
    directory: str | Path,
    firmware,
    device: DeviceFacts,
    build,
    firmware_version: str,
    build_id: str,
    artifacts: dict[str, Path] | None = None,
    toolchain: AvrToolchain | None = None,
) -> dict[str, Path]:
    """Write the SBOM, the manifest, and the report next to the build."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    sbom = generate_sbom(
        firmware, device, firmware_version, build_id,
        toolchain=toolchain, artifacts=artifacts,
    )
    report = assess(firmware, device, build, artifacts=artifacts)

    written: dict[str, Path] = {}

    sbom_path = directory / "sbom.cdx.json"
    sbom_path.write_text(json.dumps(sbom, indent=2) + "\n", encoding="utf-8")
    written["sbom"] = sbom_path

    manifest_path = directory / "manifest.sha256"
    manifest_path.write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(report.manifest.items())),
        encoding="utf-8",
    )
    written["manifest"] = manifest_path

    report_path = directory / "SECURITY.md"
    report_path.write_text(
        render_report(report, device, firmware_version, build_id), encoding="utf-8"
    )
    written["report"] = report_path

    return written
