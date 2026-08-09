"""Zephyr-target equivalents of the checks that were written for AVR.

Both checks in `agents/normalizer.py` -- "can this toolchain build for the
part" and "does the design need more peripherals than the part has" -- answer
by invoking avr-gcc. Asked about an nRF52840 they do not decline, they answer
*wrongly*: unsupported, and one UART. A confidently wrong answer is worse than
no answer, because nothing downstream treats it as suspect.

So a Zephyr target gets its answers from Zephyr: whether the SoC's devicetree
exists in the checkout, and how many of each peripheral it declares.
"""

from __future__ import annotations

from codegen.zephyr.soc_facts import SocFacts, ZephyrSocCatalog


def soc_facts(dtsi_include: str, zephyr_base=None) -> SocFacts:
    return ZephyrSocCatalog(zephyr_base).facts(dtsi_include)


def unsupported_soc(dtsi_include: str, zephyr_base=None) -> list[str]:
    """Whether Zephyr can describe this SoC at all.

    Silence when there is no checkout to consult: not knowing is not the same
    as knowing it is unsupported, and pretending otherwise would block a board
    that builds perfectly well on a machine that has the SDK.
    """
    catalog = ZephyrSocCatalog(zephyr_base)
    if not catalog.available:
        return []

    if not dtsi_include.strip():
        return [
            "No SoC devicetree include was given. Zephyr needs to know which "
            "silicon this is -- the include is not guessed, because the wrong "
            "one produces a devicetree describing a different part."
        ]

    facts = catalog.facts(dtsi_include)
    if not facts.files_read:
        return [
            f"Zephyr does not ship '{dtsi_include}'. Either the path is wrong, "
            f"or this SoC has no Zephyr support in this release. Look under "
            f"dts/ in the Zephyr tree for the vendor's directory."
        ]
    return []


def contention(sensors, dtsi_include: str, zephyr_base=None) -> list[str]:
    """Demands the part cannot meet, counted from its own devicetree.

    Reports nothing when the count is unknown. A contention check that guesses
    is the thing this module exists to remove.
    """
    problems: list[str] = []
    facts = soc_facts(dtsi_include, zephyr_base)

    serial = [s for s in sensors if str(getattr(s, "interface", "")).upper().endswith("UART")]
    available = facts.count("uart")

    if serial and available is not None:
        # The console is a consumer too: the generated application logs to it.
        needed = len(serial) + 1
        if needed > available:
            names = ", ".join(s.name for s in serial)
            problems.append(
                f"{needed} serial ports are needed ({names}, plus the console) "
                f"but {facts.soc} declares {available}. Zephyr can move the "
                f"console to RTT or USB CDC, or a device can go on another "
                f"interface -- but two devices cannot share one UART, and "
                f"nothing here will pretend otherwise."
            )

    by_address: dict[str, list[str]] = {}
    for sensor in sensors:
        if str(getattr(sensor, "interface", "")).upper().endswith("I2C") and sensor.address:
            by_address.setdefault(str(sensor.address).lower(), []).append(sensor.name)
    for address, names in by_address.items():
        if len(names) > 1:
            problems.append(
                f"{' and '.join(names)} are both at {address} on the same bus. "
                f"Move one with its address-select pin, put it on the second "
                f"I2C controller, or use a multiplexer."
            )

    return problems
