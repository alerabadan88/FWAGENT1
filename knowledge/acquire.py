"""Getting an SDK into the knowledge base.

Two routes in, and they are not equivalent:

``ingest`` -- somebody unpacked the SDK on this machine and gave a path. The
headers are read directly, so every symbol is AUTHORITATIVE and carries the
file and line it came from. This is the only route that produces a port with
real calls in it.

``record_lead`` -- somebody went looking and found a download page, a vendor
portal, a GitHub mirror. That is CITED evidence: real, dated, re-checkable, and
*not* authority. A lead tells you where to get the SDK. It does not tell you
what is in it, and a record holding only leads still emits stubs.

Collapsing those two would be the single most damaging shortcut available here,
because a page saying "supports I2C, UART, SPI" reads like knowledge and
supports no line of code.

`search_plan` is offline on purpose: it produces the queries and the domains
worth trying, and something else does the fetching. That keeps this module
testable, keeps the network an explicit step somebody chose, and keeps the
record of what was searched next to the record of what was found.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from core.evidence import cited, derived
from knowledge.base import KnowledgeBase
from knowledge.extract import extract
from knowledge.family import HwFamily, SdkSource

#: Where vendor SDKs actually turn up, roughly in order of how much the result
#: can be trusted. Vendor first, module makers second, mirrors last.
LIKELY_SOURCES = (
    ("vendor portal", "the silicon vendor's own developer site, usually behind a login or an NDA"),
    ("module maker", "whoever sells the module around the die; they often redistribute the SDK to their customers"),
    ("github mirror", "unofficial, frequently stale, and useful mainly for reading rather than building"),
    ("distributor", "the distributor that sold the part can usually route an SDK request"),
)


@dataclass(frozen=True)
class SdkLead:
    """A place an SDK might be, found by looking."""

    url: str
    title: str
    why: str
    retrieved: date
    kind: str = "unknown"
    """One of the LIKELY_SOURCES keys, when it can be told."""


@dataclass(frozen=True)
class SearchPlan:
    """What to search for, and where. Deterministic; runs no network."""

    mcu: str
    vendor: str
    queries: tuple[str, ...]
    sources: tuple[tuple[str, str], ...]

    def describe(self) -> str:
        lines = [f"Looking for an SDK for {self.mcu}" + (f" ({self.vendor})" if self.vendor else "")]
        lines += [f"  query: {q}" for q in self.queries]
        lines += [f"  where: {name} -- {why}" for name, why in self.sources]
        return "\n".join(lines)


def search_plan(mcu: str, vendor: str = "") -> SearchPlan:
    """The queries worth running for this part.

    Includes the bare family stem as well as the full order code: vendors
    document `UWS6121E` and sell `UWS6121EG`, and searching only the order code
    misses the documentation.
    """
    part = (mcu or "").strip()
    stem = part.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz") or part
    who = vendor.strip()

    queries = []
    for name in dict.fromkeys([part, stem]):
        if not name:
            continue
        queries += [
            f"{name} SDK download",
            f"{name} datasheet pinout",
            f"{who} {name} software development kit".strip(),
            f"{name} GPIO UART I2C example code",
            f"{name} toolchain build firmware",
        ]
    return SearchPlan(
        mcu=part,
        vendor=who,
        queries=tuple(dict.fromkeys(q for q in queries if q.strip())),
        sources=LIKELY_SOURCES,
    )


def record_lead(kb: KnowledgeBase, family_id: str, lead: SdkLead) -> HwFamily:
    """Store where an SDK might be found. CITED -- never promotes support."""
    family = kb.get(family_id)
    if family is None:
        raise LookupError(f"no family record for {family_id!r}; create it before adding leads")

    family.record(
        f"sdk_lead:{lead.url}",
        lead.title,
        cited(source=lead.url, excerpt=lead.why, retrieved=lead.retrieved, locator=lead.kind),
    )
    if family.sdk is None:
        family.sdk = SdkSource(name=lead.title, url=lead.url, retrieved=lead.retrieved)
    kb.put(family)
    return family


def ingest(
    kb: KnowledgeBase,
    family_id: str,
    sdk_path: Path | str,
    *,
    name: str = "",
    version: str = "",
    license_note: str = "",
) -> HwFamily:
    """Read an SDK tree on this machine into the family record.

    Everything the extractor finds becomes AUTHORITATIVE, because it names the
    header and line. Nothing is uploaded; see `knowledge.extract`.
    """
    family = kb.get(family_id)
    if family is None:
        raise LookupError(
            f"no family record for {family_id!r}. Create the record first so the "
            f"part patterns are stated by a human rather than guessed from a path."
        )

    found = extract(sdk_path)
    label = f"{name or family_id} SDK {version}".strip()

    family.sdk = SdkSource(
        name=name or (family.sdk.name if family.sdk else family_id),
        version=version or (family.sdk.version if family.sdk else ""),
        url=family.sdk.url if family.sdk else "",
        local_path=str(Path(sdk_path)),
        retrieved=date.today(),
        license_note=license_note or (family.sdk.license_note if family.sdk else ""),
    )
    family.symbols = found.symbols
    family.peripherals = found.peripherals

    family.record(
        "sdk_symbol_count", len(found.symbols),
        derived(source=label, locator=f"{len(found.headers_read)} headers under {found.root}"),
    )
    for bank in found.peripherals:
        if bank.count:
            family.record(
                f"{bank.kind}_instances", list(bank.instances),
                derived(source=label, locator=f"identifiers in {found.root}"),
            )

    kb.put(family)
    return family
