"""Tests for what is known and on whose authority.

The property under test throughout: nothing becomes authoritative by being
asserted confidently, or by being asserted twice. It becomes authoritative by
being read out of a versioned artifact, and the artifact is named.
"""

from datetime import date

import pytest

from core.evidence import (
    Claim,
    Evidence,
    EvidenceKind,
    Ledger,
    asserted,
    cited,
    derived,
    executed,
)
from services.toolchain import AvrToolchain
from services.verifier import (
    AvrLibcVerifier,
    ContradictedClaim,
    HeaderTextVerifier,
    Unverifiable,
    VerificationService,
    unsupported_report,
)

requires_avr = pytest.mark.skipif(
    not AvrToolchain.is_available(), reason="avr-gcc is not installed on this machine"
)

# A fragment in the shape real vendor headers take.
AMEBA_HEADER = """
/* peripheral base addresses */
#define SYSTEM_CTRL_BASE\t\t0x40000000
#define VENDOR_REG_BASE             0x40002800
#define SOME_COUNT                  16
"""


def header_verifier(source="vendor/sdk@abc1234") -> HeaderTextVerifier:
    return HeaderTextVerifier(AMEBA_HEADER, source=source, path="include/hal_platform.h")


# --- The states mean what they say --------------------------------------------


def test_the_default_state_is_unsupported():
    assert Claim("part", "thing", 1).evidence.kind is EvidenceKind.NONE
    assert not Claim("part", "thing", 1).authoritative


def test_a_citation_must_record_when_it_was_retrieved():
    """An external page can change or vanish; a date is what makes it checkable."""
    with pytest.raises(ValueError, match="when it was retrieved"):
        Evidence(kind=EvidenceKind.CITED, source="https://example.com/an", excerpt="x")


def test_authoritative_evidence_must_say_where_in_the_artifact():
    with pytest.raises(ValueError, match="where in the artifact"):
        Evidence(kind=EvidenceKind.AUTHORITATIVE, source="some sdk", locator="")


def test_a_citation_is_not_authority():
    """Found documentation is real evidence and is still not an artifact."""
    claim = Claim("BME280", "address", 0x76, cited(
        source="https://vendor.example/ds.pdf", excerpt="Default address 0x76",
        retrieved=date(2026, 8, 9),
    ))

    assert not claim.authoritative
    assert claim.needs_a_human


def test_execution_proves_only_what_ran():
    claim = Claim("firmware", "compiles", True, executed("avr-gcc -Wall -Werror"))

    assert not claim.authoritative
    assert claim.evidence.kind is EvidenceKind.EXECUTED


def test_evidence_cannot_be_silently_weakened():
    """Replacing a checked fact with a recollection is how verified rots."""
    strong = Claim("atmega328p", "flash_bytes", 32768, derived("avr-libc", "FLASHEND + 1"))

    with pytest.raises(ValueError, match="refusing to replace"):
        strong.with_evidence(asserted("a model said so"))


def test_there_is_no_state_for_having_been_reviewed():
    """Re-reading a claim creates no evidence, so it gets no name here."""
    assert {k.value for k in EvidenceKind} == {
        "authoritative", "executed", "cited", "none"
    }


# --- Reading facts out of the toolchain ----------------------------------------


@requires_avr
def test_a_fact_the_headers_define_is_authoritative_and_cites_its_origin():
    claim = VerificationService().verify(Claim("atmega328p", "flash_bytes", 32768))

    assert claim.authoritative
    assert "FLASHEND + 1" in claim.evidence.locator


@requires_avr
def test_a_wrong_value_is_a_contradiction_and_stops_rather_than_warns():
    with pytest.raises(ContradictedClaim, match="the artifact disagrees"):
        VerificationService().verify(Claim("atmega328p", "flash_bytes", 65536))


@requires_avr
def test_a_peripheral_the_part_lacks_is_a_contradiction():
    """An ATtiny85 has USI, not TWI. The headers settle it."""
    with pytest.raises(ContradictedClaim, match="does not define the registers"):
        VerificationService().verify(Claim("attiny85", "has_peripheral", "i2c"))


@requires_avr
def test_the_toolchain_refuses_to_confirm_a_pin_function():
    """The most important refusal here: avr-libc has registers, not pinouts.

    The SDA pin the generator uses is correct. It just does not come from
    anything this system has read, and the system says so instead of nodding.
    """
    claim = VerificationService().verify(Claim("atmega328p", "twi_sda_pin", "PC4"))

    assert not claim.authoritative
    assert "datasheet pinout" in claim.evidence.note


@requires_avr
def test_an_unestablished_claim_is_reported_not_raised():
    """Being unverified is a state to surface, not an error to swallow."""
    claim = VerificationService().verify(Claim("atmega328p", "package", "TQFP-32"))

    assert claim.needs_a_human
    assert claim.evidence.note


# --- Reading facts out of a vendor header --------------------------------------


def test_a_value_in_a_pinned_header_is_authoritative_with_a_line_number():
    claim = VerificationService([header_verifier()]).verify(
        Claim("SYSTEM_CTRL_BASE", "define", 0x40000000)
    )

    assert claim.authoritative
    assert claim.evidence.locator == "include/hal_platform.h:3"
    assert "0x40000000" in claim.evidence.excerpt


def test_a_wrong_base_address_is_a_contradiction():
    with pytest.raises(ContradictedClaim, match="is 0x40002800"):
        VerificationService([header_verifier()]).verify(
            Claim("VENDOR_REG_BASE", "define", 0x40009000)
        )


def test_a_symbol_absent_from_the_header_stays_unsupported():
    claim = VerificationService([header_verifier()]).verify(
        Claim("UART9_BASE", "define", 0x40004000)
    )

    assert not claim.authoritative
    assert "not defined anywhere" in claim.evidence.note


def test_an_unpinned_source_is_refused_as_an_authority():
    """A file with no version is not an artifact; it is a copy of something."""
    with pytest.raises(ValueError, match="does not pin a version"):
        header_verifier(source="ameba-rtos-d")


def test_the_verifier_reports_the_reason_it_could_not_help():
    with pytest.raises(Unverifiable, match="not defined anywhere"):
        header_verifier().verify(Claim("NOPE", "define", 1))


# --- The ledger ----------------------------------------------------------------


def test_the_ledger_keeps_the_best_account_of_a_fact():
    ledger = Ledger()
    ledger.record(Claim("p", "flash_bytes", 32768))
    ledger.record(Claim("p", "flash_bytes", 32768, derived("avr-libc", "FLASHEND + 1")))

    assert ledger.get("p", "flash_bytes").authoritative


def test_a_weaker_later_account_does_not_overwrite_a_checked_one():
    ledger = Ledger()
    ledger.record(Claim("p", "flash_bytes", 32768, derived("avr-libc", "FLASHEND + 1")))
    ledger.record(Claim("p", "flash_bytes", 999, asserted("guessed")))

    assert ledger.get("p", "flash_bytes").value == 32768


def test_the_ledger_lists_what_nothing_backs():
    ledger = Ledger()
    ledger.record(Claim("p", "flash_bytes", 32768, derived("avr-libc", "FLASHEND + 1")))
    ledger.record(Claim("p", "twi_sda_pin", "PC4"))

    unsupported = ledger.unsupported()

    assert [c.predicate for c in unsupported] == ["twi_sda_pin"]


def test_the_report_names_every_unsupported_value():
    ledger = Ledger()
    ledger.record(Claim("atmega328p", "twi_sda_pin", "PC4", asserted("typed by hand")))

    report = unsupported_report(ledger.all())

    assert "1 of 1 claims are not backed" in report
    assert "twi_sda_pin" in report
    assert "typed by hand" in report


def test_the_report_shows_what_a_citation_actually_said():
    ledger = Ledger()
    ledger.record(Claim("BME280", "address", 0x76, cited(
        source="https://vendor.example/ds.pdf",
        excerpt="The default I2C address is 0x76.",
        retrieved=date(2026, 8, 9),
    )))

    report = unsupported_report(ledger.all())

    assert "The default I2C address is 0x76." in report
    assert "2026-08-09" in report


def test_a_fully_backed_build_says_so_plainly():
    ledger = Ledger()
    ledger.record(Claim("p", "flash_bytes", 32768, derived("avr-libc", "FLASHEND + 1")))

    assert "Every recorded claim" in unsupported_report(ledger.all())


# --- Routing --------------------------------------------------------------------


def test_a_contradiction_is_flagged_rather_than_detected_from_its_wording():
    """Routing on prose breaks the first time somebody rewords an error."""
    silent = Unverifiable("nothing found")
    disagreement = Unverifiable("the artifact disagrees: x is 1", contradicted=True)

    assert not silent.contradicted
    assert disagreement.contradicted


@requires_avr
def test_verifying_many_claims_returns_one_result_each():
    claims = [
        Claim("atmega328p", "flash_bytes", 32768),
        Claim("atmega328p", "twi_sda_pin", "PC4"),
    ]

    results = VerificationService().verify_all(claims)

    assert [c.authoritative for c in results] == [True, False]
