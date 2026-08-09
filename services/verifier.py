"""Establish a claim by fetching the artifact, not by thinking about it again.

A verifier here answers exactly one question: *can this value be read out of a
versioned artifact I can point at?* If yes, it returns authoritative evidence
with a locator. If no, it says so and says why. It never returns "this looks
correct" -- an opinion about a value is not evidence about a value, however
many times it is repeated.

That constraint is what makes the verifiers useful rather than reassuring. The
AVR one below will happily confirm that ATmega328P has a TWI peripheral and how
big its flash is, and will flatly refuse to confirm which pins SDA and SCL are
on -- because avr-libc's headers define registers and bits, not pin functions.
That refusal is the system working. The pin assignment is real and correct; it
simply comes from a datasheet nobody here has read, and saying so is the whole
point.
"""

from __future__ import annotations

import re
from typing import Protocol

from core.evidence import Claim, Evidence, EvidenceKind, asserted, derived


class Unverifiable(Exception):
    """Why a verifier could not establish a claim.

    `contradicted` separates two outcomes that must never be conflated: the
    artifact was silent (so the claim stays unsupported and the build goes on
    with a warning), or the artifact said something *else* (so the claim is
    false and the build must stop). Carried as a flag rather than inferred
    from the message text, because routing on prose is how the two get mixed
    up the first time somebody rewords an error.
    """

    def __init__(self, message: str, contradicted: bool = False) -> None:
        super().__init__(message)
        self.contradicted = contradicted


class ClaimVerifier(Protocol):
    def handles(self, claim: Claim) -> bool: ...

    def verify(self, claim: Claim) -> Evidence: ...


class AvrLibcVerifier:
    """Reads facts out of the installed avr-libc headers.

    Authoritative for anything the headers define, because they *are* the
    artifact the compiler itself uses. Silent on anything they do not, which
    is more than people expect.
    """

    #: Predicates answerable from the headers, mapped to how to get them.
    SUPPORTED = {
        "flash_bytes", "ram_bytes", "eeprom_bytes", "flash_page_bytes",
        "has_peripheral", "usart_suffixes", "ports", "adc_channels",
        "symbol_defined",
    }

    #: Predicates that sound answerable and are not. Listed explicitly so the
    #: refusal names the reason rather than falling through a default.
    KNOWN_ABSENT = {
        "twi_sda_pin": (
            "avr-libc's headers define registers and bit positions, not pin "
            "functions. Which physical pin carries SDA is in the part's "
            "datasheet pinout, which is not shipped with the toolchain."
        ),
        "twi_scl_pin": (
            "same as twi_sda_pin: pin function assignments live in the "
            "datasheet pinout, not in the headers."
        ),
        "package": (
            "the headers describe one die; the package and its pin numbering "
            "are a datasheet fact."
        ),
        "supply_voltage_range": (
            "electrical characteristics are datasheet facts and appear nowhere "
            "in the headers."
        ),
        "pin_alternate_functions": (
            "alternate function tables are in the datasheet, not the headers."
        ),
    }

    def __init__(self, catalog=None) -> None:
        self._catalog = catalog

    def _facts(self, part: str):
        if self._catalog is None:
            from core.device_catalog import DeviceCatalog

            self._catalog = DeviceCatalog()
        return self._catalog.facts(part)

    def handles(self, claim: Claim) -> bool:
        return claim.predicate in self.SUPPORTED or claim.predicate in self.KNOWN_ABSENT

    def verify(self, claim: Claim) -> Evidence:
        if claim.predicate in self.KNOWN_ABSENT:
            raise Unverifiable(
                f"the toolchain cannot establish '{claim.predicate}': "
                f"{self.KNOWN_ABSENT[claim.predicate]}"
            )

        if claim.predicate not in self.SUPPORTED:
            raise Unverifiable(f"no rule for establishing '{claim.predicate}' from avr-libc")

        facts = self._facts(claim.subject)
        source = f"avr-libc via {facts.source}"
        header = f"<avr/io.h> for {facts.part}"

        actual, locator, excerpt = self._read(facts, claim, header)

        if not self._matches(actual, claim.value):
            raise Unverifiable(
                f"the artifact disagrees: {claim.subject}.{claim.predicate} is "
                f"{actual!r}, not {claim.value!r} ({locator})",
                contradicted=True,
            )

        return derived(source=source, locator=locator, excerpt=excerpt)

    @staticmethod
    def _matches(actual, claimed) -> bool:
        if isinstance(actual, (list, tuple)) and isinstance(claimed, (list, tuple)):
            return list(actual) == list(claimed)
        return actual == claimed

    def _read(self, facts, claim: Claim, header: str):
        predicate = claim.predicate

        if predicate == "has_peripheral":
            name = str(claim.value)
            # The claim's *value* is the peripheral name, so the fact under
            # test is presence, and a mismatch means "not present".
            if not facts.has(name):
                # Absence here is not silence: the headers enumerate every
                # peripheral the die has, so not being listed is a refutation.
                raise Unverifiable(
                    f"the artifact disagrees: {facts.part} does not define the "
                    f"registers for '{name}'; the headers list "
                    f"{sorted(facts.peripherals)}",
                    contradicted=True,
                )
            return name, f"{header}: registers for {name}", f"{name} present"

        if predicate == "symbol_defined":
            symbol = str(claim.value)
            defined = self._symbol_defined(facts.part, symbol)
            if not defined:
                raise Unverifiable(f"{symbol} is not defined for {facts.part}")
            return symbol, f"{header}: #define {symbol}", f"{symbol} is defined"

        value = {
            "flash_bytes": facts.flash_bytes,
            "ram_bytes": facts.ram_bytes,
            "eeprom_bytes": facts.eeprom_bytes,
            "flash_page_bytes": facts.flash_page_bytes,
            "usart_suffixes": list(facts.usart_suffixes),
            "ports": facts.ports,
            "adc_channels": facts.adc_channels,
        }[predicate]

        origin = {
            "flash_bytes": "FLASHEND + 1",
            "ram_bytes": "RAMEND - RAMSTART + 1",
            "eeprom_bytes": "E2END + 1",
            "flash_page_bytes": "SPM_PAGESIZE",
            "usart_suffixes": "UDR* symbols",
            "ports": "PORT* and P<x><n> symbols",
            "adc_channels": "ADC<n>D symbols",
        }[predicate]

        return value, f"{header}: {origin}", f"{origin} = {value!r}"

    def _symbol_defined(self, part: str, symbol: str) -> bool:
        from core.device_catalog import defined_symbols

        return symbol in defined_symbols(part)


class HeaderTextVerifier:
    """Establishes a claim by finding it in a C header fetched from a repo.

    Written for vendor SDKs that ship no machine description -- Realtek's Ameba
    being the case in hand: no CMSIS-SVD exists, but `hal_platform.h` in a
    pinned commit of ameba-rtos-d does define the peripheral base addresses.
    A pinned commit is a versioned artifact, so a value read out of one is
    authoritative in exactly the way a forum post is not.

    The text is supplied rather than fetched here, so this stays offline and
    testable; whatever fetches it must record the commit it pinned.
    """

    _DEFINE = re.compile(
        r"^\s*#\s*define\s+(?P<name>\w+)\s+\(?\s*(?P<value>0x[0-9A-Fa-f]+|\d+)",
        re.MULTILINE,
    )

    def __init__(self, text: str, source: str, path: str) -> None:
        if "@" not in source:
            raise ValueError(
                f"source {source!r} does not pin a version. An unpinned file is "
                f"not a versioned artifact and cannot support an authoritative "
                f"claim -- use 'repo@commit' or 'sdk@release'."
            )
        self._text = text
        self._source = source
        self._path = path

    def handles(self, claim: Claim) -> bool:
        return claim.predicate == "define"

    def verify(self, claim: Claim) -> Evidence:
        symbol = str(claim.subject)
        for match in self._DEFINE.finditer(self._text):
            if match.group("name") != symbol:
                continue
            found = int(match.group("value"), 0)
            expected = claim.value
            if isinstance(expected, str):
                expected = int(expected, 0)
            if found != expected:
                raise Unverifiable(
                    f"the artifact disagrees: {symbol} is 0x{found:X} in "
                    f"{self._path}, not 0x{int(expected):X}",
                    contradicted=True,
                )
            line = self._text[: match.start()].count("\n") + 1
            return derived(
                source=self._source,
                locator=f"{self._path}:{line}",
                excerpt=match.group(0).strip(),
            )

        raise Unverifiable(f"{symbol} is not defined anywhere in {self._path}")


class VerificationService:
    """Routes a claim to whatever can establish it, and records the outcome."""

    def __init__(self, verifiers: list[ClaimVerifier] | None = None) -> None:
        self._verifiers = list(verifiers) if verifiers is not None else [AvrLibcVerifier()]

    def verify(self, claim: Claim) -> Claim:
        """Return the claim with the best evidence anything could produce.

        Never raises for an unestablished claim: being unverified is a state to
        be reported, not an error. It raises only when an artifact actively
        *contradicts* the claim, which is a different thing entirely and must
        not be swallowed.
        """
        reasons: list[str] = []

        for verifier in self._verifiers:
            if not verifier.handles(claim):
                continue
            try:
                return claim.with_evidence(verifier.verify(claim))
            except Unverifiable as exc:
                if exc.contradicted:
                    raise ContradictedClaim(str(exc)) from exc
                reasons.append(str(exc))

        if not reasons:
            reasons.append("nothing available can establish this predicate")

        return claim.with_evidence(asserted(note="; ".join(reasons)))

    def verify_all(self, claims: list[Claim]) -> list[Claim]:
        return [self.verify(c) for c in claims]


class ContradictedClaim(Exception):
    """An artifact says something different. Never a warning; always a stop."""


def unsupported_report(claims: list[Claim]) -> str:
    """Plain text listing what a build rests on that nothing external backs."""
    weak = [c for c in claims if c.needs_a_human]
    if not weak:
        return "Every recorded claim is backed by a versioned artifact."

    lines = [
        f"{len(weak)} of {len(claims)} claims are not backed by a versioned "
        f"artifact. Each is a value somebody asserted:",
        "",
    ]
    for claim in weak:
        lines.append(f"  {claim.subject}.{claim.predicate} = {claim.value!r}")
        if claim.evidence.kind is EvidenceKind.CITED:
            lines.append(
                f"    cited: {claim.evidence.source} "
                f"(retrieved {claim.evidence.retrieved})"
            )
            if claim.evidence.excerpt:
                lines.append(f"    said: {claim.evidence.excerpt}")
        elif claim.evidence.note:
            lines.append(f"    {claim.evidence.note}")
    return "\n".join(lines)
