"""End to end on a part Zephyr does not support.

Two halves, and the first is the more important one:

1. The T106 as its specification actually stands today -- no pin assignments
   anywhere. Generation refuses and prints the questions. That refusal is the
   product working, not failing.

2. The same board once somebody who has seen the schematic has answered. The
   project is written to `examples/t106_generated/` so the emitted C can be read.

The pin numbers in part 2 are illustrative and are labelled as such wherever
they appear in the output. Nobody has seen a T106 schematic here.

    python -m examples.knowledge_demo
"""

from __future__ import annotations

from pathlib import Path

from knowledge.base import KnowledgeBase
from knowledge.board import BoardFacts, Device
from knowledge.emit import EmitError, emit
from knowledge.questions import advisory, blocking, board_questions, family_questions

OUT = Path(__file__).parent / "t106_generated"


def unanswered() -> BoardFacts:
    """The T106 exactly as the specification defines it: no pins, anywhere."""
    return BoardFacts(
        board_name="T106 Pet Locator",
        mcu="UWS6121EG",
        devices=[
            Device(name="led red", kind="led", interface="gpio", role="power / battery"),
            Device(name="led blue", kind="led", interface="gpio", role="gps fix"),
            Device(name="led green", kind="led", interface="gpio", role="network / server"),
            Device(name="home key", kind="button", interface="gpio"),
            Device(name="ag3335a", kind="gnss", interface="uart", role="positioning"),
            Device(name="da267", kind="imu", interface="i2c", role="motion wake"),
        ],
    )


def answered() -> BoardFacts:
    """The same board after the interview. Pin values here are illustrative."""
    board = unanswered()
    board.intent = (
        "Report position over the cellular network and show power, GPS and "
        "network state on a tri-colour LED."
    )
    board.loop_ms = 1000

    pins = {"led red": "GPIO_12", "led blue": "GPIO_13", "led green": "GPIO_14"}
    for device in board.devices:
        if device.name in pins:
            device.pins = {"out": pins[device.name]}
            device.active_level = "active low"

    home = next(d for d in board.devices if d.kind == "button")
    home.pins = {"in": "GPIO_5"}
    home.active_level = "active low"
    home.pull = "pull-up"

    gnss = next(d for d in board.devices if d.kind == "gnss")
    gnss.bus, gnss.baud = "UART1", 9600

    imu = next(d for d in board.devices if d.kind == "imu")
    imu.bus, imu.address, imu.pins = "I2C0", "0x26", {"int": "GPIO_7"}
    return board


def main() -> None:
    kb = KnowledgeBase()
    family = kb.resolve("UWS6121EG")

    print("=" * 72)
    print("1. What the knowledge base knows about the part")
    print("=" * 72)
    print(family.describe() if family else "UWS6121EG: no record")

    if family:
        print("\nQuestions about the silicon:")
        for item in family_questions(family):
            print(f"  - [{item.field}] {item.question}")

    print()
    print("=" * 72)
    print("2. The T106 as specified today -- generation must refuse")
    print("=" * 72)
    board = unanswered()
    questions = board_questions(board, family)
    print(f"{len(blocking(questions))} blocking, {len(advisory(questions))} advisory\n")
    for item in blocking(questions):
        print(f"  ! {item.field}")
        print(f"      {item.question}")
        print(f"      fails as: {item.failure}")
    try:
        emit(board, family)
        print("\nUNEXPECTED: generation did not refuse")
    except EmitError as exc:
        print(f"\nRefused, correctly:\n{str(exc).splitlines()[0]}")

    print()
    print("=" * 72)
    print("3. The same board, answered -- generation proceeds")
    print("=" * 72)
    project = emit(answered(), family)
    OUT.mkdir(parents=True, exist_ok=True)
    for relative, content in sorted(project.files.items()):
        path = OUT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"  {relative:28} {len(content.splitlines()):4} lines")
    print(f"\n{project.count} files -> {OUT}")
    print(f"{len(project.review)} porting operations need a human.")


if __name__ == "__main__":
    main()
