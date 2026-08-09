"""Extract a training corpus from Zephyr's own board ports.

The idea this replaces: scrape maker sites for projects with a schematic and
its firmware. The objection is not that it is hard, it is that the population
is wrong. Those projects compile and run and are frequently silently
incorrect -- a DHT22 polled faster than it can answer, a button with no
debounce, an input left floating -- which is exactly the class of defect this
system exists to catch. Training on them teaches a model to reproduce those
defects with model confidence attached.

Zephyr's tree is the same shape of data with the properties that population
lacks:

* **Aligned.** Every board port is a complete (hardware description, firmware
  configuration) pair, not a photo of a breadboard next to an .ino.
* **Reviewed.** Maintainers merged it.
* **Labelled.** It builds in CI. That is the supervision signal you would
  otherwise have to guess at, and it is free here.
* **Licensed.** Apache-2.0, with the ref pinned like every other artifact this
  project reads.

What comes out is not firmware to imitate. It is answers to the questions
`agents/uncertainty.py` already asks -- which pads a console sits on, how a
button is wired on a real board, which compatible goes with which part -- so a
suggestion can be offered and confirmed. A prior may pre-fill an advisory
answer. It may never satisfy a blocking one, because a wrong blocking answer
fails silently and no amount of corpus changes that.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

# `label: node@addr {` -- the node header devicetree uses everywhere.
_NODE = re.compile(r"^\s*(\w+)\s*:\s*[\w@,.-]+\s*\{", re.MULTILINE)
_COMPATIBLE = re.compile(r'compatible\s*=\s*"([^"]+)"')
_GPIO_PHANDLE = re.compile(r"(\w[\w-]*-gpios|gpios)\s*=\s*<&(\w+)\s+(\d+)\s+([^>]*)>")
_PSEL = re.compile(r"NRF_PSEL\((\w+),\s*(\d+),\s*(\d+)\)")
_CURRENT_SPEED = re.compile(r"current-speed\s*=\s*<(\d+)>")
_STATUS_OKAY = re.compile(r"&(\w+)\s*\{[^}]*status\s*=\s*\"okay\"", re.DOTALL)


@dataclass
class BoardRecord:
    """One board port, reduced to the facts that answer a question."""

    board: str
    vendor: str
    socs: list[str] = field(default_factory=list)
    compatibles: list[str] = field(default_factory=list)
    gpio_flags: dict[str, list[str]] = field(default_factory=dict)
    """Devicetree GPIO property -> the flag combinations real boards use."""
    console_speed: int | None = None
    console_pads: dict[str, str] = field(default_factory=dict)
    enabled_peripherals: list[str] = field(default_factory=list)
    source: str = ""


def _flags(raw: str) -> str:
    """Normalise a flags expression to something countable."""
    cleaned = re.sub(r"\s+", " ", raw).strip().strip("()")
    parts = sorted(p.strip() for p in cleaned.split("|") if p.strip())
    return " | ".join(parts) or "0"


class ZephyrCorpus:
    """Reads board ports out of a checkout and reduces them to answers."""

    def __init__(self, zephyr_base: Path | str, ref: str = "v4.4.2") -> None:
        self._base = Path(zephyr_base)
        self.ref = ref
        if not (self._base / "boards").is_dir():
            raise FileNotFoundError(
                f"{self._base}/boards does not exist. This needs a Zephyr "
                f"checkout, which is the artifact the corpus is derived from -- "
                f"there is nothing to fall back on."
            )

    @property
    def source(self) -> str:
        return f"zephyrproject-rtos/zephyr@{self.ref}"

    def board_files(self) -> list[Path]:
        return sorted(self._base.glob("boards/*/*/board.yml"))

    def extract(self, limit: int | None = None) -> list[BoardRecord]:
        records: list[BoardRecord] = []
        for board_yml in self.board_files()[:limit]:
            record = self._read_board(board_yml)
            if record is not None:
                records.append(record)
        return records

    def _read_board(self, board_yml: Path) -> BoardRecord | None:
        directory = board_yml.parent
        try:
            manifest = board_yml.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        name = self._scalar(manifest, "name") or directory.name
        vendor = self._scalar(manifest, "vendor") or directory.parent.name
        socs = re.findall(r"^\s*-\s*name:\s*(\S+)", manifest, re.MULTILINE)

        text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in sorted(directory.glob("*.dts*"))
            if path.is_file()
        )
        if not text.strip():
            return None

        record = BoardRecord(
            board=name, vendor=vendor, socs=socs,
            source=f"{self.source} boards/{directory.parent.name}/{directory.name}",
        )

        record.compatibles = sorted(set(_COMPATIBLE.findall(text)))

        for prop, _controller, _offset, flags in _GPIO_PHANDLE.findall(text):
            record.gpio_flags.setdefault(prop, []).append(_flags(flags))

        speed = _CURRENT_SPEED.search(text)
        if speed:
            record.console_speed = int(speed.group(1))

        for function, port, pin in _PSEL.findall(text):
            # First occurrence wins: the console is defined before secondary
            # ports in every board file in the tree.
            record.console_pads.setdefault(function, f"P{port}.{pin}")

        record.enabled_peripherals = sorted(set(_STATUS_OKAY.findall(text)))
        return record

    @staticmethod
    def _scalar(text: str, key: str) -> str | None:
        match = re.search(rf"^\s*{key}:\s*(\S+)", text, re.MULTILINE)
        return match.group(1) if match else None


#: Below this, a pattern is an anecdote rather than a prior. The number is not
#: arbitrary: extracting `dio-gpios` flags over the whole tree found six
#: samples, none of them from a DHT, and a naive prior would have suggested
#: their flags for one. Support is reported alongside every prior for the same
#: reason -- a caller that cannot see it will treat three boards and four
#: hundred as the same claim.
MIN_SUPPORT = 20


def confidence(count: int, total: int) -> str:
    """A word for how much a prior is worth, so a caller cannot ignore it."""
    if total == 0 or count < MIN_SUPPORT:
        return "anecdote"
    share = count / total
    if share >= 0.9:
        return "near-universal"
    if share >= 0.5:
        return "common"
    return "one option among several"


def priors(records: list[BoardRecord]) -> dict[str, object]:
    """Turn the records into suggestions, with the counts behind each one.

    Counts are reported alongside every prior on purpose. A suggestion whose
    support is three boards and one whose support is four hundred are different
    things, and a caller that cannot tell them apart will treat both as fact.
    """
    gpio_flags: dict[str, Counter] = {}
    for record in records:
        for prop, flags in record.gpio_flags.items():
            gpio_flags.setdefault(prop, Counter()).update(flags)

    speeds = Counter(r.console_speed for r in records if r.console_speed)
    peripherals = Counter(p for r in records for p in r.enabled_peripherals)
    compatibles = Counter(c for r in records for c in r.compatibles)
    vendors = Counter(r.vendor for r in records)

    return {
        "source": records[0].source.split(" ")[0] if records else "",
        "boards": len(records),
        "console_speed": [
            {
                "value": speed, "boards": count,
                "share": round(count / sum(speeds.values()), 3),
                "confidence": confidence(count, sum(speeds.values())),
            }
            for speed, count in speeds.most_common(5)
        ],
        "gpio_flags": {
            prop: [
                {
                    "flags": flags, "boards": count,
                    "share": round(count / sum(counter.values()), 3),
                    "confidence": confidence(count, sum(counter.values())),
                }
                for flags, count in counter.most_common(4)
            ]
            for prop, counter in sorted(gpio_flags.items())
            if sum(counter.values()) >= MIN_SUPPORT
        },
        "peripherals_enabled": peripherals.most_common(15),
        "compatibles": compatibles.most_common(20),
        "vendors": vendors.most_common(10),
        "note": (
            "Priors, with their support. A prior may pre-fill an advisory "
            "answer for a human to confirm. It may never satisfy a blocking "
            "one: a wrong blocking answer fails silently, and how many boards "
            "did something else does not change that. Anything below "
            f"{MIN_SUPPORT} boards is reported as an anecdote and must not be "
            "offered as a suggestion."
        ),
    }


def write(records: list[BoardRecord], destination: Path) -> Path:
    """One JSON line per board, so the corpus appends rather than rewrites."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")
    return destination
