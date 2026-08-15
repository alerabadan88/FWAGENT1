"""What somebody told us about one physical board.

Kept separate from `HwFamily` because the two decay differently. A family fact
("this SDK declares `gpio_set_level`") is true for every board using the part
and stays true until the SDK version changes. A board fact ("the red LED is on
GPIO_12, active low") is true for one PCB revision and is wrong the moment
somebody spins a new one.

Nothing here is inferred. Every field is either something a human said or
empty, and `knowledge.questions` turns every empty one that matters into a
question. There is no default pin, no assumed active level, and no guessed I2C
address, because each of those produces firmware that builds, runs, and is
quietly wrong -- the failure mode a bench test does not catch.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Device kinds the emitter knows how to write code for.
KINDS = ("led", "button", "gnss", "imu", "sensor", "uart_device")

#: Interfaces the porting layer covers.
INTERFACES = ("gpio", "i2c", "uart")


@dataclass
class Device:
    """One thing on the board and how it is wired."""

    name: str
    kind: str
    interface: str

    pins: dict[str, str] = field(default_factory=dict)
    """Role -> pin, in the family's own pin syntax. For a button that is
    {'in': 'GPIO_5'}; for an I2C part with an interrupt, {'int': 'GPIO_7'}."""

    active_level: str = ""
    """'high' or 'low'. For a GPIO device, guessing this inverts the meaning of
    every reading and every drive, and the code still runs."""

    pull: str = ""
    """'up', 'down' or 'none'. Wrong here means a floating input that reads
    whatever the air says."""

    bus: str = ""
    """Instance name as the SDK spells it: 'I2C0', 'UART1'."""

    address: str = ""
    """7-bit I2C address as written, e.g. '0x26'."""

    baud: int | None = None

    role: str = ""
    """What this device is for, in the product's terms: 'gps fix',
    'network status'. Carried into the generated code as the comment on the
    call site, so the firmware reads like the specification."""

    def pin(self, role: str) -> str:
        return self.pins.get(role, "")


@dataclass
class BoardFacts:
    """One board, one firmware."""

    board_name: str
    mcu: str
    devices: list[Device] = field(default_factory=list)

    intent: str = ""
    """What the firmware is supposed to do, in one or two sentences. Written
    into the README and the top of main.c so the next reader knows what they
    are looking at."""

    loop_ms: int | None = None
    """How often the main loop runs. Not defaulted: it sets both data
    freshness and power draw, and on a battery product that is the whole
    design."""

    notes: str = ""

    def of_kind(self, kind: str) -> list[Device]:
        return [d for d in self.devices if d.kind == kind]

    def on_interface(self, interface: str) -> list[Device]:
        return [d for d in self.devices if d.interface == interface]

    @property
    def buses_used(self) -> dict[str, list[str]]:
        """Interface -> the bus instances this board actually uses."""
        used: dict[str, list[str]] = {}
        for device in self.devices:
            if device.bus and device.bus not in used.setdefault(device.interface, []):
                used[device.interface].append(device.bus)
        return used

    def address_conflicts(self) -> list[str]:
        """Two devices answering to one address on one bus.

        Checked here rather than left to the bench, because the symptom is not
        silence: one part answers for both, and the readings look plausible.
        """
        seen: dict[tuple[str, str], str] = {}
        clashes = []
        for device in self.devices:
            if device.interface != "i2c" or not device.address:
                continue
            key = (device.bus or "?", device.address.lower())
            if key in seen:
                clashes.append(
                    f"{seen[key]} and {device.name} are both at {device.address} "
                    f"on {device.bus or 'the same bus'}. Only one can answer; the "
                    f"other is read as if it had, and returns that part's numbers."
                )
            else:
                seen[key] = device.name
        return clashes
