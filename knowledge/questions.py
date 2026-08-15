"""Everything the emitter needs and nobody has said yet.

Same rule as `agents.uncertainty`, and the same reason for it: the list is
derived in ordinary code from what the generator actually consumes, not asked
of a model. A model told to "ask when unsure" reports fewer gaps than exist,
because filling them plausibly is its default behaviour.

The split between blocking and advisory is about **how a wrong answer fails**,
never about importance:

* Loud failure -- nothing links, the bus NACKs, the serial port emits visible
  rubbish. A default is fine; somebody will notice within a minute.
* Silent failure -- an inverted LED, a pin that is never read, an address that
  belongs to a different part on the same bus. No default. A human answers.

The second list is short and it is the whole point of the interview.
"""

from __future__ import annotations

from agents.uncertainty import STANDARD_BAUDS, Uncertainty
from knowledge.board import BoardFacts, Device
from knowledge.family import HwFamily

PULLS = ["pull-up", "pull-down", "none (external resistor on the board)"]
LEVELS = ["active high (pin driven high turns it on / pressed reads high)",
          "active low (pin driven low turns it on / pressed reads low)"]


def unknown_family(mcu: str) -> list[Uncertainty]:
    """Nothing in the base matches this part.

    This is not a failure state. It is the start of the conversation that adds
    the part, so the questions are the ones that would let somebody create the
    record honestly.
    """
    return [
        Uncertainty(
            field="family.vendor",
            question=f"Who makes {mcu}? The silicon vendor, not the module maker.",
            why=(
                "It decides where the SDK is looked for. "
                "If it is wrong: the search returns a different vendor's SDK, whose "
                "function names are plausible and whose registers are not."
            ),
            failure="a search that finds documentation for the wrong silicon",
            blocking=True,
            asked_of="whoever chose the part",
        ),
        Uncertainty(
            field="family.sdk_path",
            question=(
                f"Is the {mcu} SDK unpacked anywhere on this machine? If so, give the "
                f"path to its root. Nothing is uploaded -- the headers are read locally."
            ),
            why=(
                "Function names and signatures come out of the SDK's own headers. "
                "If it is wrong: without it the porting layer is emitted as stubs, "
                "which is honest but leaves an afternoon of work for an engineer."
            ),
            failure="a port that cannot be filled in, only stubbed",
            blocking=False,
            asked_of="the firmware engineer",
        ),
        Uncertainty(
            field="family.os_model",
            question=f"Does {mcu} firmware run under an RTOS with tasks, or bare-metal?",
            why=(
                "It decides whether generated code may block. "
                "If it is wrong: a delay that is fine inside a task stalls a "
                "bare-metal loop, and the device stops reporting hours later."
            ),
            failure="a main loop that stops servicing everything else",
            blocking=True,
            options=["RTOS with tasks", "bare-metal single loop"],
            asked_of="the firmware engineer",
        ),
    ]


def family_questions(family: HwFamily) -> list[Uncertainty]:
    """Gaps in what is known about the silicon."""
    out = []
    for gap in family.gaps():
        out.append(Uncertainty(
            field=gap.field,
            question=gap.question,
            why=f"{gap.why} If it is wrong: the porting layer rests on it.",
            failure=(
                "a porting layer that cannot be completed"
                if gap.blocks_port else
                "generated code that is correct but under-commented"
            ),
            blocking=False,
            asked_of="the firmware engineer",
        ))
    return out


def board_questions(board: BoardFacts, family: HwFamily | None = None) -> list[Uncertainty]:
    """Everything about this PCB the emitter needs and does not have."""
    out: list[Uncertainty] = []
    syntax = f" Use this part's notation, e.g. {family.pin_syntax}." if family and family.pin_syntax else ""

    if not board.intent.strip():
        out.append(Uncertainty(
            field="intent",
            question="In one or two sentences, what should this firmware do?",
            why=(
                "It is written into the README and the top of main.c. "
                "If it is wrong: nothing breaks, but the next engineer has to "
                "reconstruct the purpose from the code."
            ),
            failure="firmware nobody can review against an intent",
            blocking=False,
            asked_of="whoever specified the product",
        ))

    if board.loop_ms is None:
        out.append(Uncertainty(
            field="loop_ms",
            question="How often should the main loop run, in milliseconds?",
            why=(
                "It sets both data freshness and power draw. "
                "If it is wrong: on a battery product the current draw is wrong by "
                "the same factor, and nothing in the firmware reports it."
            ),
            failure="a battery life that misses target with no visible symptom",
            blocking=False,
            options=["100", "250", "1000", "5000"],
            default="1000",
            asked_of="whoever set the power budget",
        ))

    for index, device in enumerate(board.devices):
        out += _device_questions(index, device, syntax)

    return out


def _device_questions(index: int, device: Device, syntax: str) -> list[Uncertainty]:
    prefix = f"devices[{index}]"
    label = device.name or f"device {index}"
    out: list[Uncertainty] = []

    if device.interface == "gpio":
        if not device.pins:
            role = "in" if device.kind == "button" else "out"
            out.append(Uncertainty(
                field=f"{prefix}.pins.{role}",
                question=f"Which pin is {label} wired to?{syntax}",
                why=(
                    "Every access to it names this pin. "
                    "If it is wrong: the firmware drives or samples a different "
                    "pin, which may be unconnected -- so it reads a stable, "
                    "meaningless value and reports it as data."
                ),
                failure="a pin that is never actually touched, silently",
                blocking=True,
            ))
        if not device.active_level:
            out.append(Uncertainty(
                field=f"{prefix}.active_level",
                question=f"Is {label} active high or active low?",
                why=(
                    "It decides which way every drive and every read is inverted. "
                    "If it is wrong: an LED is on when it should be off and a "
                    "button reads pressed when it is released. Both look like "
                    "working firmware."
                ),
                failure="every state inverted, with no error anywhere",
                blocking=True,
                options=LEVELS,
            ))
        if device.kind == "button" and not device.pull:
            out.append(Uncertainty(
                field=f"{prefix}.pull",
                question=f"Does {label} need an internal pull-up, a pull-down, or neither?",
                why=(
                    "An input with no pull floats. "
                    "If it is wrong: the pin reads noise, so the button appears "
                    "to press itself at random intervals."
                ),
                failure="phantom presses that only appear in the field",
                blocking=True,
                options=PULLS,
            ))

    elif device.interface == "i2c":
        if not device.bus:
            out.append(Uncertainty(
                field=f"{prefix}.bus",
                question=f"Which I2C controller is {label} on?",
                why=(
                    "The bus instance is named in every transfer. "
                    "If it is wrong: transfers go to a bus this part is not on "
                    "and every read times out."
                ),
                failure="a device that never answers",
                blocking=True,
            ))
        if not device.address:
            out.append(Uncertainty(
                field=f"{prefix}.address",
                question=(
                    f"What is {label}'s 7-bit I2C address on this board? Check the "
                    f"address-select pin -- the part number does not settle it."
                ),
                why=(
                    "Many parts sit at either of two addresses depending on a "
                    "strap pin the firmware cannot read. "
                    "If it is wrong: a *different device* on the same bus answers, "
                    "and returns numbers in a plausible range."
                ),
                failure="readings from the wrong part, in range and wrong",
                blocking=True,
            ))

    elif device.interface == "uart":
        if not device.bus:
            out.append(Uncertainty(
                field=f"{prefix}.bus",
                question=f"Which UART is {label} on?",
                why=(
                    "Named in every read and write. "
                    "If it is wrong: the port is silent, or worse, it is the "
                    "console and the log becomes the device's input."
                ),
                failure="a silent port, or a console fighting a sensor",
                blocking=True,
            ))
        if device.baud is None:
            out.append(Uncertainty(
                field=f"{prefix}.baud",
                question=f"What baud rate does {label} use?",
                why=(
                    "It must match what the part actually ships configured to. "
                    "If it is wrong: the data arrives as visible rubbish, which "
                    "is at least obvious on a scope or a terminal."
                ),
                failure="unparseable bytes -- loud, and quick to spot",
                blocking=False,
                options=[str(b) for b in STANDARD_BAUDS],
                default="9600" if device.kind == "gnss" else "115200",
            ))

    return out


def blocking(items: list[Uncertainty]) -> list[Uncertainty]:
    return [u for u in items if u.blocking]


def advisory(items: list[Uncertainty]) -> list[Uncertainty]:
    return [u for u in items if not u.blocking]
