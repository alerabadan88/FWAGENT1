"""Turn a model's draft into a validated brief, or refuse.

This layer exists because a language model is a good reader and a poor
authority. It reads messy prose into structure; everything load-bearing is then
decided here, in ordinary code:

* **What must be asked is decided by the pipeline, not the model.** The
  generator's signature says it needs a loop period, a baud rate, and a clock;
  if those are missing, a question is raised whether or not the model thought
  to ask. A model that forgets cannot cause a silent default.
* **Physical limits are checked against datasheets, not vibes.** A DHT22 polled
  every 500 ms returns stale data. That is a conflict surfaced before code is
  generated, not a bug found on a bench.
* **The draft is validated against `core/`.** A hallucinated pin or a duplicate
  I2C address raises rather than reaching the compiler.
"""

from __future__ import annotations

from agents.schemas import (
    ExtractionResult,
    FirmwareSpec,
    HardwareDraft,
    OpenQuestion,
    SensorPolicy,
)
from core.exceptions import FWAgentError
from core.hardware_model import MCU, InterfaceType, PCBAnalysis, Sensor

# Minimum interval between reads, from each part's datasheet. Polling faster
# does not fail loudly -- it silently returns the previous measurement, which
# is the worst kind of bug, so it is refused up front.
MIN_SAMPLE_PERIOD_MS = {
    "DHT22": 2000,
    "AM2302": 2000,
    "HC-SR04": 60,
}

# Boards we can fill in specs for without asking. Anything else must be stated.
KNOWN_BOARDS = {
    "ARDUINO UNO": {
        "mcu_name": "ATmega328P", "mcu_family": "AVR", "flash_kb": 32, "ram_kb": 2,
        "clock_mhz": 16, "gpio_pins": 20, "voltage": 5.0,
    },
    "ATMEGA328P": {
        "mcu_name": "ATmega328P", "mcu_family": "AVR", "flash_kb": 32, "ram_kb": 2,
        "clock_mhz": 16, "gpio_pins": 20, "voltage": 5.0,
    },
}

STANDARD_BAUDS = [9600, 19200, 38400, 57600, 115200]


class NormalizationError(FWAgentError):
    """Raised when a draft cannot become a valid brief."""


def _q(field: str, question: str, why: str, options=None, default=None) -> OpenQuestion:
    return OpenQuestion(
        field=field, question=question, why=why,
        options=options or [], default=default,
    )


def board_facts(draft: HardwareDraft) -> dict[str, str]:
    """What naming a known board actually settles.

    Saying "Arduino Uno" is not an assumption about the clock -- the board has
    a 16 MHz crystal and its fuses are set for it. So a named board answers
    those questions outright, and they are not asked again. An unnamed board,
    or a bare chip, settles nothing: an ATmega328P with factory fuses runs from
    the internal RC divided by 8, at 1 MHz.
    """
    known = KNOWN_BOARDS.get((draft.board_name or draft.mcu_name or "").upper())
    if not known:
        return {}

    return {
        "mcu_name": str(known["mcu_name"]),
        "f_cpu_hz": str(int(known["clock_mhz"] * 1e6)),
        "f_cpu_source": "external crystal on the board",
        "supply_voltage": str(known["voltage"]),
        # A named board has one broken-out serial port, wired to the USB bridge
        # in the orientation the board defines.
        "usart_index": "0",
        "uart_wiring": "handled by the board's USB bridge",
        "uart_peer": "USB-serial adapter",
    }


def usart_count(draft: HardwareDraft, answers: dict[str, str] | None = None) -> int:
    """How many USARTs the part really has, asked of the toolchain.

    Returns 1 when the part is not identified yet -- the question about which
    USART is wired only makes sense once we know there is more than one.
    """
    from core.device_catalog import DeviceCatalog

    name = (answers or {}).get("mcu_name") or draft.mcu_name
    if not name:
        return 1
    try:
        facts = DeviceCatalog().facts(name)
    except Exception:
        return 1
    return max(len(facts.usart_suffixes), 1)


def required_questions(
    draft: HardwareDraft, answers: dict[str, str] | None = None
) -> list[OpenQuestion]:
    """Everything still unanswered that the generator genuinely needs.

    Enumerated by `agents/uncertainty.py`, deterministically, from what the
    generator actually consumes -- so it cannot drift from the code, and a
    model that fails to wonder about something cannot cause a silent default.
    """
    from agents.uncertainty import scan_draft

    return [u.to_question() for u in scan(draft, answers)]


def unsupported(draft: HardwareDraft, answers: dict[str, str] | None = None) -> list[str]:
    """What this tool cannot build for at all, checked before anything is asked.

    Asking someone which pin their sensor is on, and only then telling them the
    microcontroller is not supported, wastes their time and reads as a bug. So
    this is checked first and stops the interview.
    """
    from core.device_catalog import DeviceCatalog

    name = (answers or {}).get("mcu_name") or draft.mcu_name
    if not name:
        return []
    if KNOWN_BOARDS.get(name.upper()) or KNOWN_BOARDS.get((draft.board_name or "").upper()):
        return []

    try:
        DeviceCatalog().facts(name)
    except Exception:  # noqa: BLE001 - the message below is the whole point
        return [
            f"'{name}' is not a part this toolchain can build for. This system "
            f"targets AVR through avr-gcc; it has no backend for other "
            f"architectures, so there is nothing to ask about pins yet. If the "
            f"part number is a typo, correct it and start again."
        ]
    return []


def contention(draft: HardwareDraft, answers: dict[str, str] | None = None) -> list[str]:
    """Demands on the hardware that no answer from the user can satisfy."""
    from agents.uncertainty import contention as _contention

    resolved = {**board_facts(draft), **(answers or {})}
    return _contention(draft, usart_count=usart_count(draft, resolved))


def scan(draft: HardwareDraft, answers: dict[str, str] | None = None):
    """The raw uncertainties, with what a named board already settles applied."""
    from agents.uncertainty import scan_draft

    resolved = {**board_facts(draft), **(answers or {})}
    return scan_draft(draft, resolved, usart_count=usart_count(draft, resolved))


def blocking_questions(
    draft: HardwareDraft, answers: dict[str, str] | None = None
) -> list[OpenQuestion]:
    """Only the ones with no safe default -- where a guess fails silently."""
    return [u.to_question() for u in scan(draft, answers) if u.blocking]


def assumed_defaults(draft: HardwareDraft, answers: dict[str, str] | None = None) -> list[str]:
    """Advisory answers nobody gave, stated plainly so they can be corrected.

    These are the values the build will use without being told to. Every one of
    them fails loudly if wrong, which is why it is allowed -- but it is still
    said out loud rather than applied quietly.
    """
    return [
        f"{u.field} = {u.default} ({u.question.rstrip('?')}?)"
        for u in scan(draft, answers)
        if not u.blocking and u.default is not None
    ]


def _int_answer(answers: dict[str, str], key: str, fallback: int) -> int:
    raw = answers.get(key)
    if raw is None or str(raw).strip() == "":
        return fallback
    try:
        return int(str(raw).strip())
    except ValueError as exc:
        raise NormalizationError(f"answer for '{key}' must be a whole number, got {raw!r}") from exc


def _bool_answer(answers: dict[str, str], key: str, fallback: bool) -> bool:
    raw = answers.get(key)
    if raw is None:
        return fallback
    return str(raw).strip().lower() in {"yes", "y", "true", "1", "si", "sí"}


def _build_mcu(draft: HardwareDraft, answers: dict[str, str]) -> MCU:
    """Build the MCU from the answers, asking the toolchain for the part's facts.

    `KNOWN_BOARDS` only supplies what naming a *board* settles -- the crystal
    and the supply rail. The part's own memory, ports and peripherals come from
    the compiler's headers, so any of the 400-odd supported AVRs works here,
    not just the two that were once hardcoded.
    """
    from core.device_catalog import DeviceCatalog

    name = answers.get("mcu_name") or draft.mcu_name or draft.board_name or ""
    known = KNOWN_BOARDS.get(name.upper()) or KNOWN_BOARDS.get((draft.board_name or "").upper())
    part = str(known["mcu_name"]) if known else name

    if not part:
        raise NormalizationError("no microcontroller was identified, so nothing can be built")

    try:
        facts = DeviceCatalog().facts(part)
    except Exception as exc:  # noqa: BLE001 - reported below, never swallowed
        if known is None:
            raise NormalizationError(
                f"the toolchain does not know a part called '{part}', so its memory "
                f"map and peripherals cannot be established. Check the part number "
                f"against the marking on the package."
            ) from exc
        facts = None

    clock_hz = _int_answer(
        answers, "f_cpu_hz",
        draft.f_cpu_hz or (int(known["clock_mhz"] * 1e6) if known else 0),
    )
    if clock_hz <= 0:
        raise NormalizationError(
            "the clock frequency is still unknown; it cannot be defaulted because "
            "every delay and the baud divisor scale with it"
        )

    voltage = draft.supply_voltage or float(
        answers.get("supply_voltage") or (known["voltage"] if known else 0) or 0
    )
    if voltage <= 0:
        raise NormalizationError("the supply voltage is still unknown")

    if facts is not None:
        return MCU(
            name=facts.part, family="AVR",
            flash_kb=facts.flash_kb, ram_kb=facts.ram_kb,
            clock_mhz=clock_hz / 1e6,
            gpio_pins=sum(facts.ports.values()) or int(known["gpio_pins"]),
            voltage=voltage,
        )

    return MCU(
        name=str(known["mcu_name"]), family=str(known["mcu_family"]),
        flash_kb=float(known["flash_kb"]), ram_kb=float(known["ram_kb"]),
        clock_mhz=clock_hz / 1e6, gpio_pins=int(known["gpio_pins"]), voltage=voltage,
    )


def _build_sensor(draft: "SensorDraft", index: int, answers: dict[str, str]) -> Sensor:  # noqa: F821
    try:
        interface = InterfaceType(draft.interface.upper() if draft.interface.upper() in
                                  {"I2C", "SPI", "UART", "GPIO", "ADC"} else draft.interface)
    except ValueError as exc:
        raise NormalizationError(
            f"sensor '{draft.name}' has an interface this system does not know: "
            f"{draft.interface!r}"
        ) from exc

    pins = draft.pins
    answered = answers.get(f"sensors[{index}].pins")
    if answered:
        parts = [p.strip() for p in str(answered).replace(",", " ").split() if p.strip()]
        if draft.name.upper() == "HC-SR04" and len(parts) >= 2:
            pins = {"trigger": parts[0], "echo": parts[1]}
        elif parts:
            pins = {"pin": parts[0]}

    return Sensor(
        name=draft.name,
        type=draft.type,
        interface=interface,
        bus=draft.bus,
        address=answers.get(f"sensors[{index}].address") or draft.address,
        pins=pins,
        required=draft.required,
    )


def check_timing(analysis: PCBAnalysis, spec: FirmwareSpec) -> list[str]:
    """Conflicts between what was asked for and what the parts can physically do."""
    conflicts: list[str] = []

    for sensor in analysis.sensors:
        floor = MIN_SAMPLE_PERIOD_MS.get(sensor.name.upper())
        policy = spec.policies.get(sensor.name)
        if floor is None or policy is None:
            continue
        if policy.sample_period_ms < floor:
            conflicts.append(
                f"{sensor.name} is set to {policy.sample_period_ms} ms but cannot be read "
                f"faster than every {floor} ms -- it would return the previous reading "
                f"without reporting an error."
            )

    slowest = spec.slowest_period_ms()
    if spec.loop_period_ms < slowest:
        conflicts.append(
            f"the loop runs every {spec.loop_period_ms} ms but the slowest sensor needs "
            f"{slowest} ms between reads"
        )

    return conflicts


def normalize(
    extraction: ExtractionResult, answers: dict[str, str] | None = None
) -> tuple[PCBAnalysis, FirmwareSpec]:
    """Validate a draft plus answers into a brief the generator can consume.

    Raises :class:`NormalizationError` if anything required is still missing,
    and propagates ``HardwareValidationError`` from `core/` if the hardware
    itself is impossible.
    """
    answers = answers or {}
    draft = extraction.hardware

    # Only blocking uncertainties stop this. An advisory one has a default that
    # fails loudly if it is wrong, and it is recorded as an assumption instead
    # -- refusing to build over a baud rate nobody stated would be theatre.
    outstanding = blocking_questions(draft, answers)
    if outstanding:
        fields = ", ".join(q.field for q in outstanding)
        raise NormalizationError(
            f"cannot normalize yet; these have no safe default and are still "
            f"unanswered: {fields}"
        )

    if not draft.sensors:
        raise NormalizationError("no sensors were identified, so there is nothing to read")

    mcu = _build_mcu(draft, answers)
    sensors = [_build_sensor(s, i, answers) for i, s in enumerate(draft.sensors)]
    analysis = PCBAnalysis(
        mcu=mcu,
        sensors=sensors,
        # Carried through so silkscreen labels ('D2') can be resolved to the
        # chip pin they mean on this particular board.
        board=draft.board_name,
    )  # raises on I2C conflicts etc.

    policies = {}
    for index, sensor in enumerate(draft.sensors):
        floor = MIN_SAMPLE_PERIOD_MS.get(sensor.name.upper(), 1)
        policies[sensor.name] = SensorPolicy(
            sample_period_ms=_int_answer(
                answers, f"sensors[{index}].sample_period_ms", max(floor, 1000)
            ),
            critical=_bool_answer(answers, f"sensors[{index}].critical", False),
            retry_count=2 if _bool_answer(answers, f"sensors[{index}].critical", False) else 0,
        )

    spec = FirmwareSpec(
        f_cpu_hz=_int_answer(answers, "f_cpu_hz", int(mcu.clock_mhz * 1e6)),
        uart_baud=_int_answer(answers, "uart_baud", 9600),
        loop_period_ms=_int_answer(answers, "loop_period_ms", 2000),
        policies=policies,
    )

    return analysis, spec
