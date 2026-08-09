"""Resolving a part to a devicetree binding, without inventing one.

The whole reason to target Zephyr is that the driver already exists and was
written by someone with the datasheet. That advantage is lost the moment this
code *guesses* which driver, because a plausible-but-wrong ``compatible``
either fails to build (fine) or binds a driver for a different part in the same
family (not fine -- it initialises, it reads, and it reports wrong numbers).

So resolution has three outcomes and no fourth:

``EXACT``
    A binding whose name matches the part. Still only a *candidate*: Zephyr's
    convention is filename == compatible, and conventions are not artifacts.
    `ZephyrBindingVerifier` confirms it against the YAML's own `compatible:`
    field before anything is generated.

``SUBSTITUTE``
    No binding for this part, but a generic driver covers its protocol -- a
    NEO-6M has no binding, and `gnss-nmea-generic` speaks NMEA at it. Usable,
    and the caller is told exactly what it gives up.

``NONE``
    Nothing matches. This is a question for the user or a driver somebody has
    to write. It is never resolved by picking the closest-looking name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

DATA = Path(__file__).parent / "data" / "zephyr_bindings_v4.4.2.json"


class Match(str, Enum):
    EXACT = "exact"
    SUBSTITUTE = "substitute"
    NONE = "none"


@dataclass(frozen=True)
class Resolution:
    """What driver, if any, Zephyr already has for a part."""

    part: str
    match: Match
    compatible: str | None = None
    binding_path: str | None = None
    caveat: str = ""
    alternatives: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.match is not Match.NONE


#: Parts whose vendor prefix or marketing name differs from the binding name.
#: Every entry is a *naming* fact -- "a DHT22 is what Aosong calls an AM2302,
#: and Zephyr's binding for it is aosong,dht" -- not a claim about registers.
_ALIASES = {
    "DHT22": "aosong,dht",
    "AM2302": "aosong,dht",
    "DHT11": "aosong,dht",
    "BMP280": "bosch,bme280-i2c",
    "BME280": "bosch,bme280-i2c",
    "SHT31": "sensirion,sht3xd",
    "SHT35": "sensirion,sht3xd",
    "HC-SR04": "hc-sr04",
    "HCSR04": "hc-sr04",
    "MPU6050": "invensense,mpu6050",
    "SSD1306": "solomon,ssd1306fb",
    "ADS1115": "ti,ads1115",
    "BUTTON": "gpio-keys",
    "SWITCH": "gpio-keys",
}

#: Generic drivers that speak a protocol rather than knowing a part, and what
#: relying on one actually costs.
_SUBSTITUTES = {
    "gnss": (
        "gnss-nmea-generic",
        "Zephyr has no binding for this exact receiver. The generic NMEA driver "
        "parses the standard sentences any receiver emits, which covers "
        "position, time and fix quality. What it does not give you is the "
        "vendor's binary configuration protocol -- on a u-blox that means UBX, "
        "so you cannot change the update rate, the dynamic model, or the "
        "constellations from firmware. If the module's defaults suit you, this "
        "is enough.",
    ),
}


class BindingCatalog:
    """The bindings a pinned Zephyr actually ships."""

    def __init__(self, data_path: Path | None = None) -> None:
        payload = json.loads((data_path or DATA).read_text(encoding="utf-8"))
        self.ref: str = payload["zephyr_ref"]
        self.source: str = payload["source"]
        self.captured: str = payload["captured"]
        self._candidates: dict[str, list[str]] = payload["candidates"]

    def __len__(self) -> int:
        return len(self._candidates)

    def path_for(self, compatible: str) -> str | None:
        paths = self._candidates.get(compatible)
        return f"dts/bindings/{paths[0]}" if paths else None

    def search(self, term: str, limit: int = 6) -> list[str]:
        """Candidates whose name contains a term, for offering alternatives."""
        needle = term.lower().replace("-", "").replace("_", "")
        hits = [
            name for name in self._candidates
            if needle in name.lower().replace("-", "").replace("_", "")
        ]
        return sorted(hits)[:limit]

    def resolve(self, part: str, kind: str = "") -> Resolution:
        """Find the driver for a part, or say plainly that there is not one."""
        cleaned = part.strip()
        if not cleaned:
            raise ValueError("no part name to resolve")

        alias = _ALIASES.get(cleaned.upper())
        if alias and self.path_for(alias):
            return Resolution(
                part=cleaned, match=Match.EXACT, compatible=alias,
                binding_path=self.path_for(alias),
            )

        # A part number given as a compatible already, or matching one exactly.
        if self.path_for(cleaned.lower()):
            return Resolution(
                part=cleaned, match=Match.EXACT, compatible=cleaned.lower(),
                binding_path=self.path_for(cleaned.lower()),
            )

        substitute = _SUBSTITUTES.get(kind.lower())
        if substitute:
            compatible, caveat = substitute
            if self.path_for(compatible):
                return Resolution(
                    part=cleaned, match=Match.SUBSTITUTE, compatible=compatible,
                    binding_path=self.path_for(compatible), caveat=caveat,
                    alternatives=self.search(cleaned),
                )

        return Resolution(
            part=cleaned, match=Match.NONE,
            caveat=(
                f"Zephyr {self.ref} ships no binding for '{cleaned}' and no "
                f"generic driver covers it. Either the part number is different "
                f"from what Zephyr calls it, or a driver has to be written. "
                f"Guessing a similar-looking compatible would bind a driver for "
                f"a different part, which initialises and reports wrong numbers."
            ),
            alternatives=self.search(cleaned) or self.search(kind),
        )
