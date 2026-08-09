"""Generate board configurations, build them, and keep what the compiler says.

This is the self-play loop, and it is worth being exact about what it can
learn, because the obvious reading of "train on synthetic firmware" does not
survive contact with the problem.

There is exactly one oracle here: the compiler. It answers one question --
*does this build* -- and that answer is not a matter of opinion. So the loop is
legitimate for everything that fails **loudly** (a malformed devicetree, a
missing required property, a peripheral referenced but not enabled) and useless
for everything that fails **silently**. No amount of synthetic data can
establish whether a DHT22 is really on P0.13, because nothing in the loop can
observe the board. Generating synthetic answers to that question would teach a
model the distribution of plausible pin assignments, which is precisely what
this system refuses to assume.

That split is not a limitation bolted on afterwards. It is the same
blocking/advisory line the interview already draws, arriving from the other
direction.

What the loop actually produces
-------------------------------
The passing cases are the less interesting half. The **failures** are the
product: each one is either a bug in the generator or a constraint nobody had
written down. Every defect found in this project so far came from building
something -- the SoC variant symbol, the disabled GPIO controllers, GPIOTE, the
dangling pinctrl label. This automates the search that found them.

So this is closer to property-based testing than to data collection, and it is
named for what it does rather than for what it would be nice to call it.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from codegen.zephyr.binding_fetch import BindingFetcher
from codegen.zephyr.board_port import BoardPortError, SocProfile, ZephyrBoardPort
from core.hardware_model import MCU, InterfaceType, PCBAnalysis, Sensor
from services.zephyr_build import BuildUnavailable, ZephyrBuilder

#: Parts the catalog resolves to a real Zephyr binding, with the interface each
#: one uses. Sampled from, not invented: a part with no binding is refused by
#: the generator anyway, so putting one here would only test the refusal.
CANDIDATE_PARTS = [
    ("DHT22", "temperature_humidity", InterfaceType.GPIO, None),
    ("Button", "user_input", InterfaceType.GPIO, None),
    ("BME280", "pressure", InterfaceType.I2C, "0x76"),
    ("BMP280", "pressure", InterfaceType.I2C, "0x77"),
    ("SHT31", "temperature_humidity", InterfaceType.I2C, "0x44"),
    ("MPU6050", "imu", InterfaceType.I2C, "0x68"),
    ("HC-SR04", "distance", InterfaceType.GPIO, None),
    ("NEO-6M", "gnss", InterfaceType.UART, None),
]

_SOC_INCLUDE = re.compile(r'#include\s*<([^>]+\.dtsi)>')
_SELECT_SOC = re.compile(r"select\s+(SOC_\w+)")


@dataclass
class SocCandidate:
    """A SoC profile read out of a board port that is known to build."""

    vendor: str
    board: str
    dtsi_include: str
    kconfig_soc: str
    soc_name: str


@dataclass
class Trial:
    """One generated configuration and what the compiler made of it."""

    seed: int
    soc: str
    vendor: str
    parts: list[str] = field(default_factory=list)
    pins: dict[str, str] = field(default_factory=dict)
    outcome: str = ""
    """generated | refused | build-failed | built"""
    detail: str = ""
    flash_used: int | None = None
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


def soc_candidates(zephyr_base: Path | str, limit: int | None = None) -> list[SocCandidate]:
    """SoC profiles harvested from board ports Zephyr itself ships.

    Sampling from real ports rather than composing profiles by hand: a
    generator fed impossible SoC descriptions would spend its time rediscovering
    that they are impossible.
    """
    base = Path(zephyr_base)
    found: list[SocCandidate] = []

    for board_yml in sorted(base.glob("boards/*/*/board.yml")):
        directory = board_yml.parent
        dts = sorted(directory.glob("*.dts"))
        kconfigs = sorted(directory.glob("Kconfig.*"))
        if not dts or not kconfigs:
            continue

        include = _SOC_INCLUDE.search(dts[0].read_text(encoding="utf-8", errors="replace"))
        select = _SELECT_SOC.search(
            "\n".join(k.read_text(encoding="utf-8", errors="replace") for k in kconfigs)
        )
        if not include or not select:
            continue

        manifest = board_yml.read_text(encoding="utf-8", errors="replace")
        soc_name = re.search(r"^\s*-\s*name:\s*(\S+)", manifest, re.MULTILINE)

        found.append(SocCandidate(
            vendor=directory.parent.name,
            board=directory.name,
            dtsi_include=include.group(1),
            kconfig_soc=select.group(1),
            soc_name=soc_name.group(1) if soc_name else directory.name,
        ))
        if limit and len(found) >= limit:
            break

    return found


class SyntheticCampaign:
    """Generates configurations, builds them, and records what happened."""

    def __init__(
        self,
        zephyr_base: Path | str,
        vendors: tuple[str, ...] = ("nordic",),
        seed: int = 0,
    ) -> None:
        self._base = Path(zephyr_base)
        self._rng = random.Random(seed)
        self._socs = [
            candidate for candidate in soc_candidates(self._base)
            if candidate.vendor in vendors
        ]
        if not self._socs:
            raise ValueError(
                f"no board ports found for {vendors}. Pin control is written per "
                f"vendor (see codegen/zephyr/pinctrl.py), so a campaign can only "
                f"cover vendors that module knows -- anything else would be "
                f"measuring the refusal, not the generator."
            )
        self._port = ZephyrBoardPort(fetcher=BindingFetcher())
        self._builder = ZephyrBuilder()

    @property
    def soc_count(self) -> int:
        return len(self._socs)

    @staticmethod
    def _pin(rng: random.Random) -> str:
        """A pin, drawn from the *call's* generator, never the campaign's.

        Taking it from a campaign-level generator made compose() depend on how
        many times it had been called, so the same seed produced different
        boards. A failure that cannot be reproduced from its seed is noise, not
        a finding -- which is the whole value of the loop.
        """
        return f"P{rng.randint(0, 1)}.{rng.randint(0, 31)}"

    def compose(self, seed: int) -> tuple[SocProfile, PCBAnalysis, list[str], dict[str, str]]:
        """One plausible board: a real SoC, real parts, pins in range."""
        rng = random.Random(seed)
        candidate = rng.choice(self._socs)

        soc = SocProfile(
            name=candidate.soc_name, arch="arm",
            dtsi_include=candidate.dtsi_include, vendor=candidate.vendor,
            kconfig_soc=candidate.kconfig_soc,
            console_tx=self._pin(rng), console_rx=self._pin(rng),
            i2c_sda=self._pin(rng), i2c_scl=self._pin(rng),
        )

        chosen = rng.sample(CANDIDATE_PARTS, rng.randint(1, 3))
        sensors: list[Sensor] = []
        pins: dict[str, str] = {}

        for name, kind, interface, address in chosen:
            assigned: dict[str, str] = {}
            if interface is InterfaceType.GPIO:
                if name.upper().startswith("HC-SR"):
                    assigned = {"trigger": self._pin(rng), "echo": self._pin(rng)}
                else:
                    assigned = {"pin": self._pin(rng)}
            pins.update({f"{name}.{role}": pin for role, pin in assigned.items()})
            sensors.append(Sensor(
                name=name, type=kind, interface=interface,
                bus="I2C1" if interface is InterfaceType.I2C else None,
                address=address, pins=assigned or None,
            ))

        analysis = PCBAnalysis(
            mcu=MCU(name=candidate.soc_name, family="ARM", flash_kb=1024,
                    ram_kb=256, clock_mhz=64, gpio_pins=48, voltage=3.3),
            sensors=sensors,
        )
        return soc, analysis, [s.name for s in sensors], pins

    def run_one(self, seed: int, build: bool = True) -> Trial:
        soc, analysis, parts, pins = self.compose(seed)
        trial = Trial(seed=seed, soc=soc.dtsi_include, vendor=soc.vendor,
                      parts=parts, pins=pins)

        try:
            files = self._port.generate(analysis, soc, f"synth {seed}")
        except BoardPortError as exc:
            # A refusal is a correct outcome, not a failure: the generator
            # declining something it cannot do is the behaviour under test.
            trial.outcome = "refused"
            trial.detail = str(exc).splitlines()[0][:300]
            return trial

        if not build:
            trial.outcome = "generated"
            return trial

        try:
            result = self._builder.build(files, f"synth_{seed}")
        except BuildUnavailable as exc:
            trial.outcome = "generated"
            trial.detail = f"not built: {exc}".splitlines()[0][:200]
            return trial

        if result.ok:
            trial.outcome = "built"
            trial.flash_used = result.flash_used
        else:
            # The interesting half. Each of these is a generator bug or a
            # constraint nobody wrote down.
            trial.outcome = "build-failed"
            trial.detail = _first_error(result.log)
        return trial

    def run(self, count: int, start: int = 0, build: bool = True) -> list[Trial]:
        return [self.run_one(seed, build=build) for seed in range(start, start + count)]


def _first_error(log: str) -> str:
    """The line a person would look at, not the whole transcript."""
    markers = (
        "devicetree error", "error:", "cmake error", "undefined reference",
        "is marked as required", "static assertion", "no such file",
    )
    for line in log.splitlines():
        lowered = line.lower()
        if any(marker in lowered for marker in markers):
            # `FATAL ERROR: command exited with status 1` is west reporting that
            # something below it failed. It names no cause, so it is skipped in
            # favour of the line that does.
            if "command exited with status" in lowered:
                continue
            return line.strip()[:300]
    return log.strip().splitlines()[-1][:300] if log.strip() else "no output"


def summarise(trials: list[Trial]) -> dict[str, object]:
    from collections import Counter

    outcomes = Counter(t.outcome for t in trials)
    failures = Counter(t.detail for t in trials if t.outcome == "build-failed")
    refusals = Counter(t.detail for t in trials if t.outcome == "refused")

    return {
        "trials": len(trials),
        "outcomes": dict(outcomes),
        "build_failures": failures.most_common(10),
        "refusals": refusals.most_common(10),
        "note": (
            "Failures are the product. Each is a generator defect or a "
            "constraint nobody wrote down. A pass establishes that the "
            "devicetree is consistent and the drivers compile -- it says "
            "nothing about whether any pin here matches a real board, and "
            "nothing in this loop can."
        ),
    }


def write(trials: list[Trial], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as handle:
        for trial in trials:
            handle.write(json.dumps(asdict(trial), sort_keys=True) + "\n")
    return destination
