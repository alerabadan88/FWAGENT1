"""What is known, and on whose authority.

Every hardware fact this system uses to generate firmware carries the reason it
is believed. There is no boolean called `verified` that anyone can set, because
"verified" is not a property a value has -- it is a relationship between the
value and an artifact somebody can go and read.

The four states, in descending order of what they let you conclude:

``AUTHORITATIVE``
    Derived from a versioned artifact: the compiler's own headers, a pinned
    commit of a vendor SDK, a devicetree binding. Carries a locator precise
    enough to re-check by hand. This is the only state that permits generating
    code that depends on the value without warning anybody.

``EXECUTED``
    Proven by running something -- it compiled, or the simulator computed the
    expected number. Narrow but real: it establishes the property that was
    tested and nothing else. Code that compiles is not code that is correct.

``CITED``
    Found by actively looking, in documentation outside our control: a vendor
    PDF, an application note, a forum answer from the manufacturer. This is
    genuine evidence and it is *not* authoritative, because the source is not
    versioned, may be wrong, and may not describe the revision in hand. It
    carries a URL, the date it was retrieved, and the words it actually said,
    so a human can judge it.

``NONE``
    Somebody -- a person or a model -- asserted it. This is the default, and
    it is not a defect to be in this state; it is a defect to *forget* being in
    it.

Why there is no "reviewed twice" state
--------------------------------------
Re-reading a claim does not create evidence. If a register address came from a
model's recollection, a second model asked whether it looks right is querying
the same distribution that produced it, so their errors are correlated. Two
passes multiply confidence without multiplying evidence, and they yield a
document asserting "verified" that nothing external supports -- a worse outcome
than the honest `NONE`, because it cannot be spotted downstream.

Promotion is therefore only ever by *finding an artifact*: NONE or CITED
becomes AUTHORITATIVE when someone points at a file, at a version. Never by
deliberating harder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class EvidenceKind(str, Enum):
    AUTHORITATIVE = "authoritative"
    EXECUTED = "executed"
    CITED = "cited"
    NONE = "none"


#: Ordered worst to best, for choosing between two accounts of the same fact.
_STRENGTH = {
    EvidenceKind.NONE: 0,
    EvidenceKind.CITED: 1,
    EvidenceKind.EXECUTED: 2,
    EvidenceKind.AUTHORITATIVE: 3,
}


@dataclass(frozen=True)
class Evidence:
    """Why a value is believed, in a form somebody else can re-check."""

    kind: EvidenceKind
    source: str
    """What was consulted: 'avr-libc 2.3.2', 'ameba-rtos-d@a1b2c3d', a URL."""
    locator: str = ""
    """Where in it: a header path and symbol, a file and line, a page number."""
    excerpt: str = ""
    """What it actually said. Present so a reader need not trust the summary."""
    retrieved: date | None = None
    """When. Required for CITED, whose source can change under us."""
    note: str = ""

    def __post_init__(self) -> None:
        if self.kind is EvidenceKind.CITED and self.retrieved is None:
            raise ValueError(
                "cited evidence must record when it was retrieved: an external "
                "page can change or vanish, and a citation without a date "
                "cannot be re-checked"
            )
        if self.kind in (EvidenceKind.AUTHORITATIVE, EvidenceKind.CITED) and not self.source:
            raise ValueError(f"{self.kind.value} evidence must name its source")
        if self.kind is EvidenceKind.AUTHORITATIVE and not self.locator:
            raise ValueError(
                "authoritative evidence must say where in the artifact the value "
                "is, precisely enough to re-check by hand"
            )

    @property
    def strength(self) -> int:
        return _STRENGTH[self.kind]

    def describe(self) -> str:
        parts = [f"{self.kind.value}: {self.source}" if self.source else self.kind.value]
        if self.locator:
            parts.append(self.locator)
        if self.retrieved:
            parts.append(f"retrieved {self.retrieved.isoformat()}")
        return " | ".join(parts)


def asserted(note: str = "") -> Evidence:
    """The honest default: somebody said so, and nothing has checked it."""
    return Evidence(kind=EvidenceKind.NONE, source="", note=note)


def derived(source: str, locator: str, excerpt: str = "") -> Evidence:
    """Read out of a versioned artifact."""
    return Evidence(
        kind=EvidenceKind.AUTHORITATIVE, source=source, locator=locator, excerpt=excerpt
    )


def executed(source: str, locator: str = "", excerpt: str = "") -> Evidence:
    """Established by running something. Proves the property tested, no more."""
    return Evidence(
        kind=EvidenceKind.EXECUTED, source=source, locator=locator, excerpt=excerpt
    )


def cited(source: str, excerpt: str, retrieved: date, locator: str = "") -> Evidence:
    """Found by looking outside. Real evidence; not authority."""
    return Evidence(
        kind=EvidenceKind.CITED, source=source, locator=locator,
        excerpt=excerpt, retrieved=retrieved,
    )


@dataclass(frozen=True)
class Claim:
    """One hardware fact, and the reason it is believed.

    `subject` is what the fact is about (a part, a board, a sensor), and
    `predicate` names the fact ('twi_sda_pin', 'flash_bytes'). Keeping them
    apart is what lets a verifier be asked "can you establish this?" without
    parsing prose.
    """

    subject: str
    predicate: str
    value: object
    evidence: Evidence = field(default_factory=asserted)

    @property
    def authoritative(self) -> bool:
        return self.evidence.kind is EvidenceKind.AUTHORITATIVE

    @property
    def needs_a_human(self) -> bool:
        """True when nothing external supports this and code depends on it."""
        return self.evidence.kind in (EvidenceKind.NONE, EvidenceKind.CITED)

    def describe(self) -> str:
        return f"{self.subject}.{self.predicate} = {self.value!r}  [{self.evidence.describe()}]"

    def with_evidence(self, evidence: Evidence) -> Claim:
        """A copy carrying better evidence.

        Refuses to *weaken* a claim silently: replacing an authoritative value
        with a recollection is how a verified fact quietly becomes a guess.
        """
        if evidence.strength < self.evidence.strength:
            raise ValueError(
                f"refusing to replace {self.evidence.kind.value} evidence for "
                f"{self.subject}.{self.predicate} with weaker "
                f"{evidence.kind.value} evidence"
            )
        return Claim(self.subject, self.predicate, self.value, evidence)


class Ledger:
    """Every claim a build rests on, so the unsupported ones can be listed."""

    def __init__(self) -> None:
        self._claims: dict[tuple[str, str], Claim] = {}

    def record(self, claim: Claim) -> Claim:
        key = (claim.subject, claim.predicate)
        existing = self._claims.get(key)
        # Best evidence wins; a later weaker account does not overwrite it.
        if existing is None or claim.evidence.strength > existing.evidence.strength:
            self._claims[key] = claim
        return self._claims[key]

    def get(self, subject: str, predicate: str) -> Claim | None:
        return self._claims.get((subject, predicate))

    def all(self) -> list[Claim]:
        return sorted(self._claims.values(), key=lambda c: (c.subject, c.predicate))

    def unsupported(self) -> list[Claim]:
        """Claims driving generated code that nothing external backs."""
        return [c for c in self.all() if c.needs_a_human]

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for claim in self.all():
            counts[claim.evidence.kind.value] = counts.get(claim.evidence.kind.value, 0) + 1
        if not counts:
            return "no claims recorded"
        return ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items()))
