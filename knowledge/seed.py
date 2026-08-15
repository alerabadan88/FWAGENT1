"""The records this base ships with.

Kept as code rather than as checked-in JSON so the *evidence* for each fact is
reviewable next to the fact. Anyone can read this file and see that nothing
here was recalled: every value names the document and the row it came from.

Run `python -m knowledge.seed` to write them into the base.

On the UWS6121E record specifically
-----------------------------------
Everything in it is CITED, not AUTHORITATIVE, and the distinction is not
pedantry. The source is a customer product-definition spreadsheet -- a real,
dated, re-checkable document, and *not* the silicon vendor's datasheet. It
states what the product is meant to contain. It is not evidence about the die.

So the record resolves the part, reports PARTIAL, and lists what it needs. That
is the honest position: enough to start the conversation, not enough to write a
porting layer, and it says which of the two it is.
"""

from __future__ import annotations

from datetime import date

from core.evidence import cited
from knowledge.base import KnowledgeBase
from knowledge.family import HwFamily, SdkSource

#: The document the UWS6121E facts come from.
T106_SPEC = "T106_Proudct specification V0.2-20260508.xlsx (V0.2)"
T106_READ = date(2026, 8, 15)


def uws6121e() -> HwFamily:
    family = HwFamily(
        family_id="UWS6121E",
        vendor="UNISOC",
        # Order codes seen so far: UWS6121EG. The stem covers the die; the
        # suffix is packaging and does not change the firmware interface.
        part_patterns=(r"UWS6121E[A-Z0-9\-]*",),
        arch="arm",
        cpu="Cortex-A5",
        os_model="rtos",
        notes=(
            "LTE Cat.1 SoC with an integrated modem. The SDK is NDA-gated and was "
            "not available when this record was written, so no symbol here is "
            "authoritative. Zephyr v4.4.2 does not support this part: a search of "
            "soc/, dts/ and boards/ for unisoc|sprd|spreadtrum|uws61 returns "
            "nothing, which is why it lives in this base rather than in the "
            "Zephyr path."
        ),
        sdk=SdkSource(
            name="UNISOC UWS6121E SDK",
            license_note=(
                "NDA-gated. Read locally only -- knowledge.extract parses headers "
                "on this machine and uploads nothing."
            ),
        ),
    )

    family.record(
        "cpu_clock_hz", 500_000_000,
        cited(source=T106_SPEC, excerpt="CPU主频 | Cortex A5 500MHz",
              retrieved=T106_READ, locator="硬件定义 r25"),
    )
    family.record(
        "os", "RTOS",
        cited(source=T106_SPEC, excerpt="Operating System | 操作系统 | RTOS",
              retrieved=T106_READ, locator="硬件定义 r27"),
    )
    family.record(
        "memory", "SIP 16MB Flash + 16MB RAM",
        cited(source=T106_SPEC, excerpt="Internal | 内存 | SIP 16MB Flash+16MB RAM",
              retrieved=T106_READ, locator="硬件定义 r49"),
    )
    family.record(
        "network", "LTE FDD B1/3/5/8, TDD B34/39/40/41, VoLTE",
        cited(source=T106_SPEC, excerpt="4G Network | 4G网络 | FDDLTE:B1/3/5/8 TDDLTE:B34/B39/40/41(38)",
              retrieved=T106_READ, locator="硬件定义 r30"),
    )
    # Stated as TBD in the source document. Recorded because "the customer has
    # not decided" is itself a fact the firmware budget depends on.
    family.record(
        "application_space", "TBD in the source document",
        cited(source=T106_SPEC, excerpt="End User Space | 用户空间 | TBD",
              retrieved=T106_READ, locator="硬件定义 r51"),
    )
    return family


def install(kb: KnowledgeBase | None = None) -> list[str]:
    base = kb or KnowledgeBase()
    written = []
    for build in (uws6121e,):
        family = build()
        base.put(family)
        written.append(family.family_id)
    return written


if __name__ == "__main__":
    for family_id in install():
        print(f"wrote {family_id}")
