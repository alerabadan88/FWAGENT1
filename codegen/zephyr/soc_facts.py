"""How many of each peripheral a SoC has, read from Zephyr's own devicetree.

This exists because of a bug worth keeping in mind. The peripheral-contention
check -- "you want a GPS and a console, and this part has one UART" -- asked
`core/device_catalog.py`, which answers by invoking avr-gcc. For an nRF52840
that returns one USART, which is wrong; the part has two UARTE instances. The
check was not merely unhelpful for ARM parts, it was confidently incorrect,
which is worse than declining to answer.

The fix is the same principle the rest of the project runs on: ask the artifact
that actually describes the silicon. For a Zephyr target that is the SoC .dtsi
Zephyr ships, in the checkout the build will use.

Counting is textual rather than a full devicetree parse. That is a real
limitation and it is bounded in the honest direction: an include this does not
follow makes the count *lower*, so the contention check errs toward asking
rather than toward silently allowing an impossible design.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

#: Node names, by peripheral kind, as Zephyr's SoC files label them.
_PATTERNS = {
    "uart": re.compile(r"^\s*(?:uart|usart|serial|uarte)(\d+)\s*:", re.MULTILINE | re.IGNORECASE),
    "i2c": re.compile(r"^\s*(?:i2c|twi|twim)(\d+)\s*:", re.MULTILINE | re.IGNORECASE),
    "spi": re.compile(r"^\s*(?:spi|spim)(\d+)\s*:", re.MULTILINE | re.IGNORECASE),
    "gpio": re.compile(r"^\s*gpio(\d+)\s*:", re.MULTILINE | re.IGNORECASE),
}

_INCLUDE = re.compile(r'^\s*#include\s+[<"]([^>"]+)[>"]', re.MULTILINE)


@dataclass(frozen=True)
class SocFacts:
    """What Zephyr's devicetree says the SoC has."""

    soc: str
    counts: dict[str, int] = field(default_factory=dict)
    source: str = ""
    files_read: tuple[str, ...] = ()

    def count(self, peripheral: str) -> int | None:
        """How many instances, or None when nothing could be read.

        None is a real answer and must not be coerced to zero or one: "I do not
        know how many UARTs this part has" and "this part has one UART" lead to
        different, incompatible decisions.
        """
        return self.counts.get(peripheral)


class ZephyrSocCatalog:
    """Reads peripheral counts out of a Zephyr checkout."""

    def __init__(self, zephyr_base: Path | str | None = None) -> None:
        env = os.environ.get("ZEPHYR_BASE")
        base = zephyr_base or env
        self._base = Path(base) if base else None

    @property
    def available(self) -> bool:
        return self._base is not None and (self._base / "dts").is_dir()

    def facts(self, dtsi_include: str) -> SocFacts:
        """Counts for the SoC named by its Zephyr .dtsi path.

        `dtsi_include` is what goes in the board's `#include`, e.g.
        'nordic/nrf52840_qiaa.dtsi'.
        """
        if not self.available:
            return SocFacts(soc=dtsi_include, source="no Zephyr checkout available")

        text, read = self._read_with_includes(dtsi_include)
        if not read:
            return SocFacts(
                soc=dtsi_include,
                source=f"{dtsi_include} was not found under {self._base}/dts",
            )

        counts = {
            kind: len({m.group(1) for m in pattern.finditer(text)})
            for kind, pattern in _PATTERNS.items()
        }
        return SocFacts(
            soc=dtsi_include,
            counts={k: v for k, v in counts.items() if v},
            source=f"{self._base.name} dts/{dtsi_include}",
            files_read=tuple(read),
        )

    def _read_with_includes(self, relative: str, depth: int = 4) -> tuple[str, list[str]]:
        """The file plus the SoC files it includes, one level of nesting at a time.

        Only devicetree sources are followed. A C header pulled in for macros
        defines no nodes, and chasing it would find nothing while costing time.
        """
        collected: list[str] = []
        texts: list[str] = []
        queue = [(relative, depth)]
        seen: set[str] = set()

        while queue:
            name, remaining = queue.pop(0)
            if name in seen or remaining < 0:
                continue
            seen.add(name)

            path = self._resolve(name)
            if path is None:
                continue

            body = path.read_text(encoding="utf-8", errors="replace")
            texts.append(body)
            collected.append(name)

            for included in _INCLUDE.findall(body):
                if included.endswith((".dtsi", ".dts")):
                    queue.append((included, remaining - 1))

        return "\n".join(texts), collected

    def _resolve(self, name: str) -> Path | None:
        candidates = [
            self._base / "dts" / name,
            self._base / "dts" / "arm" / name,
            self._base / "dts" / "riscv" / name,
            self._base / "dts" / "common" / name,
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate

        # SoC files reference siblings by bare name; search, but only under dts.
        # Filtered to files: rglob returns directories too, and a directory
        # whose name matches would otherwise be opened as a devicetree.
        stem = Path(name).name
        if not stem or stem in {".", ".."}:
            return None
        matches = [m for m in (self._base / "dts").rglob(stem) if m.is_file()]
        return matches[0] if matches else None


@lru_cache(maxsize=64)
def peripheral_count(dtsi_include: str, peripheral: str, zephyr_base: str | None = None) -> int | None:
    """Convenience wrapper, cached because each call walks several files."""
    return ZephyrSocCatalog(zephyr_base).facts(dtsi_include).count(peripheral)
