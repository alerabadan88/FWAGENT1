"""What is known about one hardware family, and on whose authority.

A family record answers three questions, and keeps them apart because they have
different consequences:

1. **Which parts is this?**  Matching is by pattern, because vendors ship a
   dozen order codes over one die: `UWS6121EG`, `UWS6121E-A`, and plain
   `UWS6121E` are the same silicon to a firmware author.

2. **What does its SDK provide?**  Function names, the headers that declare
   them, peripheral instances. These are only ever filled in by *reading an SDK
   tree that is present on this machine* (`knowledge.extract`). Nothing here is
   populated from recollection, because a function name that does not exist
   produces code that looks right and does not link -- and, worse, a function
   that exists with different argument order produces code that links and does
   the wrong thing.

3. **What is still missing?**  Gaps are first-class. A family with no SDK is a
   perfectly valid record: it resolves, it reports `PARTIAL`, and it says which
   questions would promote it. That is the difference between "I cannot help"
   and "here is what I need".

Support levels
--------------
``READY``       An SDK tree was ingested here; symbols are AUTHORITATIVE. The
                emitted porting layer can call real functions.
``PARTIAL``     The family is identified and some facts are CITED, but no SDK
                is present. Application logic can still be emitted in full; the
                porting layer comes out as stubs with the questions inline.
``IDENTIFIED``  Little more than the name and vendor.

There is no ``VERIFIED``. Consistent with `core.evidence`, re-reading a record
does not improve it; only pointing at an artifact does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from core.evidence import Claim, Evidence, EvidenceKind, asserted


class Support(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    IDENTIFIED = "identified"


@dataclass(frozen=True)
class SdkSource:
    """Where the SDK is, or where it would have to come from.

    `local_path` being empty is the normal state for an NDA-gated SDK and is
    not an error. It is the single fact that separates READY from PARTIAL.
    """

    name: str
    version: str = ""
    url: str = ""
    local_path: str = ""
    retrieved: date | None = None
    license_note: str = ""
    """Anything that constrains redistribution. NDA-gated SDKs are read locally
    and never uploaded; recording that here keeps the constraint next to the
    thing it constrains."""

    @property
    def present(self) -> bool:
        return bool(self.local_path)


@dataclass(frozen=True)
class ApiSymbol:
    """One function the SDK declares, as its own header spells it."""

    name: str
    header: str
    signature: str = ""
    returns: str = ""

    def describe(self) -> str:
        return f"{self.signature or self.name}  [{self.header}]"


@dataclass(frozen=True)
class PeripheralBank:
    """How many of a peripheral the part has, and what the SDK calls them."""

    kind: str
    """'uart', 'i2c', 'spi', 'gpio', 'adc', 'timer'."""
    instances: tuple[str, ...] = ()
    """As named by the SDK: ('UART0', 'UART1'). Empty means not established."""

    @property
    def count(self) -> int | None:
        """None, not zero, when unknown -- they lead to different decisions."""
        return len(self.instances) or None


@dataclass(frozen=True)
class Gap:
    """Something the record lacks, and the question that would fill it."""

    field: str
    question: str
    why: str
    blocks_port: bool
    """True when the porting layer cannot be filled in without it. False means
    the application still compiles; only a comment is missing."""


@dataclass
class HwFamily:
    """One silicon family and everything established about it."""

    family_id: str
    vendor: str
    part_patterns: tuple[str, ...] = ()
    """Regexes, matched case-insensitively against the MCU string a user types."""

    arch: str = ""
    cpu: str = ""
    os_model: str = ""
    """'rtos', 'bare-metal', 'linux'. Decides whether emitted code may block."""

    sdk: SdkSource | None = None
    symbols: tuple[ApiSymbol, ...] = ()
    peripherals: tuple[PeripheralBank, ...] = ()
    pin_syntax: str = ""
    """How this family names a pin, e.g. 'GPIO_12' or 'P0.13'. Used to check an
    answer before it reaches a template, not to invent one."""

    facts: dict[str, Claim] = field(default_factory=dict)
    """Keyed by predicate. Subject is always `family_id`."""

    notes: str = ""

    # --- identity -----------------------------------------------------------

    def matches(self, mcu: str) -> bool:
        probe = (mcu or "").strip()
        if not probe:
            return False
        if probe.lower() == self.family_id.lower():
            return True
        return any(re.fullmatch(p, probe, re.IGNORECASE) for p in self.part_patterns)

    # --- what can be done with it -------------------------------------------

    @property
    def support(self) -> Support:
        if self.sdk and self.sdk.present and self.symbols:
            return Support.READY
        if self.facts or self.peripherals or self.sdk:
            return Support.PARTIAL
        return Support.IDENTIFIED

    def symbol(self, name: str) -> ApiSymbol | None:
        for sym in self.symbols:
            if sym.name == name:
                return sym
        return None

    def bank(self, kind: str) -> PeripheralBank | None:
        for bank in self.peripherals:
            if bank.kind == kind:
                return bank
        return None

    def record(self, predicate: str, value: object, evidence: Evidence | None = None) -> Claim:
        """Add a fact. Better evidence wins; a weaker later account does not."""
        claim = Claim(self.family_id, predicate, value, evidence or asserted())
        existing = self.facts.get(predicate)
        if existing is None or claim.evidence.strength > existing.evidence.strength:
            self.facts[predicate] = claim
        return self.facts[predicate]

    def unsupported(self) -> list[Claim]:
        """Facts that nothing external backs, which a human must confirm."""
        return [c for c in self.facts.values() if c.needs_a_human]

    def gaps(self) -> list[Gap]:
        """What is missing, worst first.

        This is the method that turns "I do not support this part" into a list
        somebody can act on.
        """
        found: list[Gap] = []
        if not (self.sdk and self.sdk.present):
            found.append(Gap(
                field="sdk.local_path",
                question=(
                    f"Where is the {self.vendor} SDK for {self.family_id} unpacked "
                    f"on this machine? A path is enough; nothing is uploaded."
                ),
                why=(
                    "Function names and signatures are read out of the SDK headers. "
                    "Without them the porting layer can only be emitted as stubs, "
                    "because writing a call from memory produces code that either "
                    "does not link or -- worse -- links with the arguments in the "
                    "wrong order."
                ),
                blocks_port=True,
            ))
        if not self.symbols and self.sdk and self.sdk.present:
            found.append(Gap(
                field="symbols",
                question=(
                    f"The SDK path for {self.family_id} is set but no symbols were "
                    f"extracted. Which subdirectory holds the public headers?"
                ),
                why="Extraction found no function declarations under the given root.",
                blocks_port=True,
            ))
        if not self.os_model:
            found.append(Gap(
                field="os_model",
                question=(
                    f"Does {self.family_id} firmware run under an RTOS with tasks, "
                    f"or bare-metal in a single loop?"
                ),
                why=(
                    "It decides whether emitted code may block. A delay that is "
                    "fine inside a task hangs a bare-metal main loop, and the "
                    "symptom is a device that stops reporting hours later."
                ),
                blocks_port=False,
            ))
        if not self.pin_syntax:
            found.append(Gap(
                field="pin_syntax",
                question=f"How does the {self.family_id} SDK name a pin? Give one example.",
                why=(
                    "Answers from the interview are checked against this before "
                    "they reach a template, so a pin typed in another family's "
                    "notation is caught rather than emitted."
                ),
                blocks_port=False,
            ))
        return found

    # --- reporting ----------------------------------------------------------

    def describe(self) -> str:
        lines = [
            f"{self.family_id} ({self.vendor}) -- {self.support.value}",
            f"  arch: {self.arch or '?'}   cpu: {self.cpu or '?'}   os: {self.os_model or '?'}",
        ]
        if self.sdk:
            where = self.sdk.local_path or "not present on this machine"
            lines.append(f"  sdk: {self.sdk.name} {self.sdk.version} -- {where}")
        if self.symbols:
            lines.append(f"  symbols: {len(self.symbols)} from {len({s.header for s in self.symbols})} headers")
        for bank in self.peripherals:
            lines.append(f"  {bank.kind}: {', '.join(bank.instances) or 'count unknown'}")
        for gap in self.gaps():
            mark = "!" if gap.blocks_port else "-"
            lines.append(f"  {mark} missing {gap.field}")
        return "\n".join(lines)


# --- serialisation ----------------------------------------------------------
# Written by hand rather than with a library so the on-disk shape stays legible
# and reviewable: a family record is something a person should be able to open
# and correct.


def _evidence_to_json(ev: Evidence) -> dict:
    return {
        "kind": ev.kind.value,
        "source": ev.source,
        "locator": ev.locator,
        "excerpt": ev.excerpt,
        "retrieved": ev.retrieved.isoformat() if ev.retrieved else None,
        "note": ev.note,
    }


def _evidence_from_json(raw: dict) -> Evidence:
    return Evidence(
        kind=EvidenceKind(raw["kind"]),
        source=raw.get("source", ""),
        locator=raw.get("locator", ""),
        excerpt=raw.get("excerpt", ""),
        retrieved=date.fromisoformat(raw["retrieved"]) if raw.get("retrieved") else None,
        note=raw.get("note", ""),
    )


def to_json(family: HwFamily) -> dict:
    return {
        "family_id": family.family_id,
        "vendor": family.vendor,
        "part_patterns": list(family.part_patterns),
        "arch": family.arch,
        "cpu": family.cpu,
        "os_model": family.os_model,
        "pin_syntax": family.pin_syntax,
        "notes": family.notes,
        "sdk": None if family.sdk is None else {
            "name": family.sdk.name,
            "version": family.sdk.version,
            "url": family.sdk.url,
            "local_path": family.sdk.local_path,
            "retrieved": family.sdk.retrieved.isoformat() if family.sdk.retrieved else None,
            "license_note": family.sdk.license_note,
        },
        "symbols": [
            {"name": s.name, "header": s.header, "signature": s.signature, "returns": s.returns}
            for s in family.symbols
        ],
        "peripherals": [
            {"kind": b.kind, "instances": list(b.instances)} for b in family.peripherals
        ],
        "facts": {
            predicate: {"value": claim.value, "evidence": _evidence_to_json(claim.evidence)}
            for predicate, claim in family.facts.items()
        },
    }


def from_json(raw: dict) -> HwFamily:
    sdk_raw = raw.get("sdk")
    family = HwFamily(
        family_id=raw["family_id"],
        vendor=raw.get("vendor", ""),
        part_patterns=tuple(raw.get("part_patterns", ())),
        arch=raw.get("arch", ""),
        cpu=raw.get("cpu", ""),
        os_model=raw.get("os_model", ""),
        pin_syntax=raw.get("pin_syntax", ""),
        notes=raw.get("notes", ""),
        sdk=None if not sdk_raw else SdkSource(
            name=sdk_raw.get("name", ""),
            version=sdk_raw.get("version", ""),
            url=sdk_raw.get("url", ""),
            local_path=sdk_raw.get("local_path", ""),
            retrieved=date.fromisoformat(sdk_raw["retrieved"]) if sdk_raw.get("retrieved") else None,
            license_note=sdk_raw.get("license_note", ""),
        ),
        symbols=tuple(
            ApiSymbol(
                name=s["name"], header=s.get("header", ""),
                signature=s.get("signature", ""), returns=s.get("returns", ""),
            )
            for s in raw.get("symbols", [])
        ),
        peripherals=tuple(
            PeripheralBank(kind=b["kind"], instances=tuple(b.get("instances", ())))
            for b in raw.get("peripherals", [])
        ),
    )
    for predicate, entry in raw.get("facts", {}).items():
        family.facts[predicate] = Claim(
            subject=family.family_id,
            predicate=predicate,
            value=entry["value"],
            evidence=_evidence_from_json(entry["evidence"]),
        )
    return family
