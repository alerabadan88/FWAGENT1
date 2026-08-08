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


def required_questions(
    draft: HardwareDraft, answers: dict[str, str] | None = None
) -> list[OpenQuestion]:
    """Everything still unanswered that the generator genuinely needs.

    Derived from the pipeline's own requirements, so it cannot drift from what
    the code actually consumes.
    """
    answers = answers or {}
    questions: list[OpenQuestion] = []

    board_key = (draft.board_name or draft.mcu_name or "").upper()
    known = KNOWN_BOARDS.get(board_key)

    if not draft.mcu_name and not known and "mcu_name" not in answers:
        questions.append(_q(
            "mcu_name",
            "Which microcontroller is on the board?",
            "The pin mapping, the compiler target, and the memory budget all depend on it. "
            "There is no safe default.",
            options=["ATmega328P (Arduino Uno)"],
        ))

    for index, sensor in enumerate(draft.sensors):
        interface = (sensor.interface or "").upper()
        needs_pins = interface in {"GPIO", "ADC", "1-WIRE"}
        key = f"sensors[{index}].pins"

        if needs_pins and not sensor.pins and key not in answers:
            hint = (
                "trigger and echo pins" if sensor.name.upper() == "HC-SR04"
                else "the pin it is wired to"
            )
            questions.append(_q(
                key,
                f"Which pin(s) is the {sensor.name} connected to? ({hint})",
                "Firmware toggles a specific port bit; the wrong pin means the sensor is "
                "never read and nothing reports an error.",
            ))

        if interface == "I2C" and not sensor.address and f"sensors[{index}].address" not in answers:
            questions.append(_q(
                f"sensors[{index}].address",
                f"What I2C address does the {sensor.name} use?",
                "Two devices at the same address on one bus collide and both read garbage.",
            ))

        pkey = f"sensors[{index}].sample_period_ms"
        if pkey not in answers:
            floor = MIN_SAMPLE_PERIOD_MS.get(sensor.name.upper())
            why = "How often the firmware reads it drives power draw and data freshness."
            if floor:
                why += f" The {sensor.name} cannot be read faster than every {floor} ms."
            questions.append(_q(
                pkey,
                f"How often should the {sensor.name} be read? (in milliseconds)",
                why,
                default=str(max(floor or 1000, 1000)),
            ))

        ckey = f"sensors[{index}].critical"
        if ckey not in answers:
            questions.append(_q(
                ckey,
                f"Is the {sensor.name} reading critical -- must a failed read be retried "
                f"and flagged rather than just skipped?",
                "Critical readings get retries and an explicit error on the wire; "
                "non-critical ones are logged and the loop moves on.",
                options=["no", "yes"],
                default="no",
            ))

    if "loop_period_ms" not in answers:
        questions.append(_q(
            "loop_period_ms",
            "How often should the whole measurement cycle run? (in milliseconds)",
            "This sets how fresh the data is and how much power the board draws. "
            "It is currently the single biggest thing the tool would otherwise assume.",
            default="2000",
        ))

    if "uart_baud" not in answers:
        questions.append(_q(
            "uart_baud",
            "What baud rate should the serial output use?",
            "Whatever reads the output has to match it exactly, and not every rate is "
            "reachable from this clock without unacceptable error.",
            options=[str(b) for b in STANDARD_BAUDS],
            default="9600",
        ))

    if not draft.f_cpu_hz and "f_cpu_hz" not in answers and not known:
        questions.append(_q(
            "f_cpu_hz",
            "What clock frequency does the board run at? (in Hz)",
            "Every delay and the baud rate divisor are computed from it; a wrong value "
            "makes all timing wrong by the same factor.",
            options=["16000000", "8000000"],
            default="16000000",
        ))

    return questions


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
    name = answers.get("mcu_name") or draft.mcu_name or draft.board_name or ""
    known = KNOWN_BOARDS.get(name.upper()) or KNOWN_BOARDS.get((draft.board_name or "").upper())

    if known is None:
        raise NormalizationError(
            f"no specs are known for '{name or 'this board'}'. "
            f"Known boards: {sorted(KNOWN_BOARDS)}"
        )

    clock_hz = _int_answer(answers, "f_cpu_hz", draft.f_cpu_hz or int(known["clock_mhz"] * 1e6))

    return MCU(
        name=str(known["mcu_name"]),
        family=str(known["mcu_family"]),
        flash_kb=float(known["flash_kb"]),
        ram_kb=float(known["ram_kb"]),
        clock_mhz=clock_hz / 1e6,
        gpio_pins=int(known["gpio_pins"]),
        voltage=draft.supply_voltage or float(known["voltage"]),
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

    outstanding = required_questions(draft, answers)
    if outstanding:
        fields = ", ".join(q.field for q in outstanding)
        raise NormalizationError(f"cannot normalize yet; still unanswered: {fields}")

    if not draft.sensors:
        raise NormalizationError("no sensors were identified, so there is nothing to read")

    mcu = _build_mcu(draft, answers)
    sensors = [_build_sensor(s, i, answers) for i, s in enumerate(draft.sensors)]
    analysis = PCBAnalysis(mcu=mcu, sensors=sensors)  # raises on I2C conflicts etc.

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
