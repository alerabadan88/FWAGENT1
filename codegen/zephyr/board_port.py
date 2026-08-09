"""Turn a schematic plus an interview into a Zephyr board port.

This is the part nobody has automated and everybody does by hand: taking a
custom PCBA and writing the four or five declarative files that let Zephyr
build for it. It is tedious, it is error-prone, and -- crucially -- it is
almost entirely *derivable* from facts the netlist and the interview already
carry.

What is generated is deliberately small. There are no drivers here. A node
saying "there is an aosong,dht on this pin" hands the work to a driver written
by someone with the datasheet open, which is the entire reason for targeting
Zephyr rather than emitting register writes.

What is refused is equally deliberate: a part Zephyr has no binding for does
not get a similar-looking compatible. That would bind a driver for a different
device, which initialises cleanly and reports wrong numbers -- the failure mode
this whole project exists to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from codegen.zephyr.bindings import BindingCatalog, Match, Resolution
from core.exceptions import FWAgentError
from core.hardware_model import InterfaceType, PCBAnalysis

TEMPLATES = Path(__file__).parent.parent / "templates" / "zephyr"

#: Which answer fills which devicetree GPIO property. Every entry is a naming
#: fact -- "the property a binding calls trigger-gpios is the pin the interview
#: calls the trigger" -- and nothing here decides a pin number. A property not
#: listed is one this generator refuses rather than fills, because a phandle
#: pointed at the wrong pin binds cleanly and drives nothing.
GPIO_PROPERTY_PINS = {
    "dio-gpios": ("pin", "dio", "data"),
    "gpios": ("pin", "gpio"),
    "trigger-gpios": ("trigger", "trig"),
    "echo-gpios": ("echo",),
    "int-gpios": ("int", "interrupt", "irq"),
    "reset-gpios": ("reset", "rst"),
}

#: Flags per property. A trigger is driven, an echo is read, and a one-wire
#: data line idles high -- these are protocol facts, not board facts, so they
#: are not asked.
GPIO_PROPERTY_FLAGS = {
    "dio-gpios": "(GPIO_PULL_UP | GPIO_ACTIVE_LOW)",
    "trigger-gpios": "GPIO_ACTIVE_HIGH",
    "echo-gpios": "GPIO_ACTIVE_HIGH",
    "reset-gpios": "GPIO_ACTIVE_LOW",
    "int-gpios": "GPIO_ACTIVE_HIGH",
    "gpios": "(GPIO_ACTIVE_LOW | GPIO_PULL_UP)",
}


class BoardPortError(FWAgentError):
    """Raised when a board port cannot be generated correctly."""


@dataclass(frozen=True)
class SocProfile:
    """What the SoC contributes, which the board file cannot invent.

    Every field here is a fact about the silicon that lives in Zephyr's own
    SoC support, not in the board port. Supplying them wrongly produces a
    devicetree that does not compile, which is the harmless kind of wrong --
    but they are asked for rather than guessed because there is no default
    that is right for two different SoCs.
    """

    name: str
    """The SoC as board.yml names it -- the die, e.g. 'nrf52840'."""
    arch: str
    """'arm', 'riscv', 'xtensa'..."""
    dtsi_include: str
    """The SoC .dtsi Zephyr ships, e.g. 'nordic/nrf52840_qiaa.dtsi'."""
    vendor: str
    """Vendor directory under boards/, e.g. 'nordic'."""
    uart_label: str = "uart0"
    i2c_label: str = "i2c0"
    gpio_label: str = "gpio0"
    runner: str = ""
    """west runner for flashing, e.g. 'nrfjprog'. Empty when unknown."""
    console_tx: str = ""
    """Which pad the console UART transmits on. A board fact, so it is asked."""
    console_rx: str = ""
    i2c_sda: str = ""
    """Which pad SDA comes out on. A board fact, asked like the console pads."""
    i2c_scl: str = ""
    kconfig_soc: str = ""
    """The Kconfig symbol a board selects, which is the *variant*, not the die:
    an nRF52840 board selects SOC_NRF52840_QIAA. The variant decides the memory
    sizes, so the die symbol configures the wrong part."""

    @property
    def soc_symbol(self) -> str:
        """The Kconfig symbol to select, falling back to the die's name."""
        return (self.kconfig_soc or f"SOC_{self.name}").upper()


@dataclass
class DeviceNode:
    """One devicetree node, with the evidence for its compatible."""

    label: str
    resolution: Resolution
    parent: str
    properties: dict[str, str] = field(default_factory=dict)
    comment: str = ""
    required_properties: list[str] = field(default_factory=list)
    """What the binding says a node of this kind must carry. Filled in by
    `plan` from the binding file itself, not from what we happen to emit."""
    unchecked_reason: str = ""
    """Why the binding could not be read, when it could not. Never blank
    silently: an unchecked node must be distinguishable from a checked one."""

    gpio_properties: dict[str, str] = field(default_factory=dict)
    """Devicetree GPIO property -> the pin that fills it, from the interview."""
    pins: dict[str, str] = field(default_factory=dict)
    """Every pin recorded for this part, keyed by the role the user gave."""

    def emitted_properties(self) -> set[str]:
        """Properties this node will actually carry in the devicetree.

        The templates add a couple that are implied by the node's shape rather
        than by its `properties` dict -- a gpio-keys node carries its pin on a
        child, and a one-wire sensor carries dio-gpios. Both are declared here
        so the required-property check compares against what is emitted, not
        against what was passed in.
        """
        return set(self.properties) | set(self.gpio_properties)

    @property
    def compatible(self) -> str:
        if not self.resolution.compatible:
            raise BoardPortError(f"node '{self.label}' has no resolved compatible")
        return self.resolution.compatible


def _label(name: str) -> str:
    """A devicetree label: lowercase, alphanumeric and underscores only."""
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in name.lower())
    cleaned = cleaned.strip("_")
    return cleaned if cleaned and not cleaned[0].isdigit() else f"dev_{cleaned}"


def _gpio_spec(pin: str, soc: SocProfile, flags: str) -> str:
    """A <&gpio N flags> phandle from a pin the user gave us.

    Accepts 'P0.13', 'PD2' and '13'. Refuses anything else rather than picking
    a number, because a wrong pin here is silent: the node binds, the driver
    initialises, and it drives a pin nothing is connected to.
    """
    text = pin.strip().upper().lstrip("P")
    port, _, offset = text.partition(".")

    if offset:
        controller, number = port, offset
    elif text[:1].isalpha() and text[1:].isdigit():
        # AVR-style 'PD2': the letter is the port, the digit the bit.
        controller, number = str(ord(text[0]) - ord("A")), text[1:]
    elif text.isdigit():
        controller, number = "0", text
    else:
        raise BoardPortError(
            f"cannot read '{pin}' as a pin. Give it as 'P0.13' (port.offset), "
            f"'PD2', or a plain offset -- guessing would drive the wrong pin, "
            f"and that failure is silent."
        )

    if not number.isdigit():
        raise BoardPortError(f"cannot read a pin offset out of '{pin}'")

    base = soc.gpio_label.rstrip("0123456789") or "gpio"
    return f"<&{base}{controller} {number} {flags}>"


def _gpio_controllers(nodes: list[DeviceNode], soc: SocProfile) -> list[str]:
    """Every GPIO controller a generated node points at.

    They arrive `status = "disabled"` from the SoC .dtsi and the board is what
    turns them on. A phandle into a disabled controller does not warn -- the
    device object is never emitted and the link fails on an undeclared symbol,
    which is a long way from the cause.
    """
    base = soc.gpio_label.rstrip("0123456789") or "gpio"
    used: set[str] = set()

    for node in nodes:
        for pin in list(node.gpio_properties.values()) or list(node.pins.values()):
            try:
                spec = _gpio_spec(pin, soc, "0")
            except BoardPortError:
                continue
            used.add(spec.split()[0].lstrip("<&"))

    return sorted(used) or [f"{base}0"]


def _cpucluster_of(soc: SocProfile) -> str:
    """Which core a multi-core SoC's .dtsi describes.

    board.yml has to name it, and Zephyr's own file naming carries it --
    `nrf5340_cpuapp_qkaa.dtsi` is the application core. Omitting it makes west
    reject the board as ambiguous, from inside boards.cmake, without saying
    that a qualifier is what is missing.
    """
    from codegen.zephyr.soc_facts import ZephyrSocCatalog

    return ZephyrSocCatalog().facts(soc.dtsi_include).cpucluster if soc.dtsi_include else ""


def _verify_against_soc(soc: SocProfile, nodes: list[DeviceNode]) -> None:
    """Refuse to name a node the SoC does not define.

    Found by generating boards across the Nordic family and building them: the
    generator assumed `uart0`, `i2c0`, `gpio0` and `gpio1` exist everywhere.
    An nRF52811 has one GPIO controller, and referencing `gpio1` gives
    `undefined node label 'gpio1'` -- which says where and not why.

    Silent when there is no checkout to consult. Not knowing which labels exist
    is different from knowing they do not.
    """
    from codegen.zephyr.soc_facts import ZephyrSocCatalog

    catalog = ZephyrSocCatalog()
    if not catalog.available or not soc.dtsi_include:
        return

    facts = catalog.facts(soc.dtsi_include)
    if not facts.labels:
        return

    wanted = {soc.uart_label, *_gpio_controllers(nodes, soc)}
    if any(n.parent == "i2c" for n in nodes):
        wanted.add(soc.i2c_label)

    missing = sorted(label for label in wanted if label and label not in facts.labels)
    if not missing:
        return

    present = sorted(
        label for label in facts.labels
        if label.startswith(("gpio", "uart", "i2c")) and not label.endswith(("_default", "_sleep"))
    )
    raise BoardPortError(
        f"{soc.dtsi_include} defines no {missing}. A board port that names a "
        f"node the SoC does not have fails with `undefined node label`, which "
        f"says where and not why.\n"
        f"This part has: {', '.join(present) or 'nothing recognisable'}.\n"
        f"Either a pin was given on a port this package does not bring out, or "
        f"the peripheral labels need setting for this SoC."
    )


def _bus_compatible(soc: SocProfile, bus: str) -> str:
    from codegen.zephyr.pinctrl import bus_compatible

    return bus_compatible(soc.vendor, bus)


def _i2c_pinctrl(soc: SocProfile, needed: bool) -> tuple[str, bool]:
    """The &pinctrl block for the I2C bus, when anything is on it.

    Found by building rather than by reading: a Nordic i2c node ships with no
    compatible and no pin mux, and the build reports only `'pinctrl-0' is
    marked as required`, which names the second problem and hides the first.
    """
    from codegen.zephyr.pinctrl import (
        I2C_DIALECTS, PinctrlUnsupported, UartPins, i2c_pinctrl,
    )

    if not needed:
        return "", False

    has_pads = bool(soc.i2c_sda and soc.i2c_scl)
    if soc.vendor.lower() in I2C_DIALECTS and not has_pads:
        raise BoardPortError(
            f"there are parts on I2C but the SDA and SCL pads were not given. "
            f"On {soc.vendor} the bus is muxed onto specific pins, and which "
            f"pads those are is a property of the board. A wrong pair drives "
            f"the bus into pins nothing is connected to and every transfer "
            f"times out. Set i2c_sda and i2c_scl."
        )

    try:
        return i2c_pinctrl(soc.vendor, soc.i2c_label, UartPins(soc.i2c_sda, soc.i2c_scl)), True
    except PinctrlUnsupported as exc:
        return (
            "/* No pin control was generated for the I2C bus.\n"
            " *\n"
            f" * {exc}\n"
            " */"
        ), False


def _implied(soc: SocProfile) -> list[str]:
    from codegen.zephyr.pinctrl import implied_peripherals

    return list(implied_peripherals(soc.vendor))


def _console_pinctrl(soc: SocProfile) -> tuple[str, bool]:
    """The &pinctrl block for the console UART, and whether the UART can use it.

    Returns the block and a flag, because the two have to move together.
    Emitting `pinctrl-0 = <&uart0_default>` without the block it names gives
    `undefined node label 'uart0_default'` from the devicetree compiler, which
    is a long way from what actually happened: nobody said which pad the
    console is on.
    """
    from codegen.zephyr.pinctrl import (
        DIALECTS,
        PinctrlUnsupported,
        UartPins,
        uart_pinctrl,
    )

    has_pins = bool(soc.console_tx and soc.console_rx)

    if soc.vendor.lower() in DIALECTS and not has_pins:
        raise BoardPortError(
            f"the console UART pads were not given. On {soc.vendor} the UART is "
            f"muxed onto specific pins, and which pads those are is a property "
            f"of the board -- there is no default, and a wrong one gives a "
            f"board that boots and prints into a pin nobody connected. Set "
            f"console_tx and console_rx (for example P0.6 and P0.8)."
        )

    try:
        block = uart_pinctrl(
            soc.vendor, soc.uart_label, UartPins(soc.console_tx, soc.console_rx)
        )
        return block, True
    except PinctrlUnsupported as exc:
        # An unknown vendor: emit neither the block nor a reference to it, and
        # say what has to be written by hand.
        return (
            "/* No pin control was generated for the console UART.\n"
            " *\n"
            f" * {exc}\n"
            " *\n"
            " * Add a &pinctrl block for it, and the matching pinctrl-0 and\n"
            " * pinctrl-1 properties on the UART node below.\n"
            " */"
        ), False


class ZephyrBoardPort:
    """Generates the files Zephyr needs to build for a board it has never seen."""

    def __init__(self, catalog: BindingCatalog | None = None, fetcher=None) -> None:
        self._catalog = catalog or BindingCatalog()
        self._fetcher = fetcher

    def plan(self, analysis: PCBAnalysis, answers: dict[str, str] | None = None) -> list[DeviceNode]:
        """Work out the nodes, refusing any part Zephyr cannot drive.

        Runs before generating anything so the refusal arrives before a
        half-written board port does.
        """
        answers = answers or {}
        nodes: list[DeviceNode] = []
        unresolved: list[Resolution] = []

        for index, sensor in enumerate(analysis.sensors):
            resolution = self._catalog.resolve(sensor.name, sensor.type)
            if not resolution.usable:
                unresolved.append(resolution)
                continue
            nodes.append(self._node(sensor, index, resolution, answers))

        if unresolved:
            details = "\n".join(f"  - {r.part}: {r.caveat}" for r in unresolved)
            near = {r.part: r.alternatives for r in unresolved if r.alternatives}
            hint = ""
            if near:
                hint = "\n\nSimilarly named bindings, in case the part is listed under another name:\n"
                hint += "\n".join(f"  {p}: {', '.join(a)}" for p, a in near.items())
            raise BoardPortError(
                f"{len(unresolved)} part(s) have no Zephyr driver:\n{details}{hint}"
            )

        self._check_required_properties(nodes)
        return nodes

    def _check_required_properties(self, nodes: list[DeviceNode]) -> None:
        """Refuse a node missing a property its own binding marks required.

        The alternative -- emitting the node anyway -- produces a devicetree
        error at build time, which is harmless but pointless when the binding
        says outright what is needed. What this must never do is invent a
        value for a property it does not understand: a phandle guessed at
        points somewhere real and wrong.
        """
        if self._fetcher is None:
            return

        from services.zephyr_verifier import ZephyrBindingVerifier

        for node in nodes:
            if not node.resolution.binding_path:
                continue
            try:
                text = self._fetcher.fetch(node.resolution.binding_path)
            except Exception as exc:  # noqa: BLE001 - recorded, never silent
                node.unchecked_reason = str(exc)
                continue

            verifier = ZephyrBindingVerifier(
                text, source=self._fetcher.source, path=node.resolution.binding_path
            )
            required = verifier.required_properties()
            node.required_properties = required
            self._fill_gpio_properties(node, required)

            missing = [p for p in required if p not in node.emitted_properties()]
            if missing:
                known = ", ".join(sorted(node.pins)) or "none"
                raise BoardPortError(
                    f"'{node.resolution.part}' would be emitted as a "
                    f"{node.compatible} node without {missing}, which its "
                    f"binding marks required ({node.resolution.binding_path}).\n"
                    f"Pins recorded for it: {known}.\n"
                    f"Either the interview did not ask for this one, or the "
                    f"property is not a pin at all. Nothing is invented here: a "
                    f"phandle pointed at the wrong pin binds cleanly and drives "
                    f"nothing."
                )

    @staticmethod
    def _fill_gpio_properties(node: DeviceNode, required: list[str]) -> None:
        """Match the pins the interview collected to the properties the binding wants.

        The matching is by *role name*, not by position: a binding's
        `trigger-gpios` is filled from the pin somebody called the trigger. When
        a part needs only one pin and only one is known, that one is used --
        there is no ambiguity to resolve. Anything else is left unfilled, and
        the caller refuses.
        """
        for prop in required:
            if not prop.endswith("-gpios") and prop != "gpios":
                continue
            aliases = GPIO_PROPERTY_PINS.get(prop, ())
            for alias in aliases:
                for role, pin in node.pins.items():
                    if role.lower() == alias:
                        node.gpio_properties[prop] = pin
                        break
                if prop in node.gpio_properties:
                    break

        # A single-pin part whose pin was given without a role name.
        unfilled = [p for p in required if p.endswith("gpios") and p not in node.gpio_properties]
        spare = [pin for role, pin in node.pins.items() if pin not in node.gpio_properties.values()]
        if len(unfilled) == 1 and len(spare) == 1:
            node.gpio_properties[unfilled[0]] = spare[0]

    def _node(self, sensor, index: int, resolution: Resolution, answers: dict) -> DeviceNode:
        label = _label(sensor.name)
        key = f"sensors[{index}]"

        if sensor.interface is InterfaceType.I2C:
            if not sensor.address:
                raise BoardPortError(f"{sensor.name} is on I2C with no address")
            address = int(str(sensor.address), 0)
            return DeviceNode(
                label=label, resolution=resolution, parent="i2c",
                properties={"reg": f"<0x{address:02x}>"},
            )

        if sensor.interface is InterfaceType.UART:
            return DeviceNode(label=label, resolution=resolution, parent="uart")

        # GPIO, 1-Wire and analog parts all reduce to "which pin(s)", and the
        # binding itself names the properties -- aosong,dht wants dio-gpios, an
        # HC-SR04 wants trigger-gpios and echo-gpios. Which pins those are is a
        # board fact, so they come from the schematic or the interview.
        pins = dict(sensor.pins or {})
        answered = answers.get(f"{key}.pins")
        if answered and not pins:
            parts = [p.strip() for p in str(answered).replace(",", " ").split() if p.strip()]
            pins = {"pin": parts[0]} if len(parts) == 1 else {
                "trigger": parts[0], "echo": parts[1]
            } if len(parts) >= 2 else {}

        if not pins:
            raise BoardPortError(
                f"{sensor.name} needs a pin and none was given. This is the "
                f"question the interview exists to ask."
            )
        return DeviceNode(
            label=label, resolution=resolution, parent="root",
            properties={}, pins=pins,
        )

    def generate(
        self,
        analysis: PCBAnalysis,
        soc: SocProfile,
        board_name: str,
        answers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """The complete set of files, keyed by path relative to a west workspace."""
        from jinja2 import Environment, FileSystemLoader, StrictUndefined

        answers = answers or {}
        nodes = self.plan(analysis, answers)
        board = _label(board_name)

        _verify_against_soc(soc, nodes)
        pinctrl_block, has_pinctrl = _console_pinctrl(soc)
        i2c_block, has_i2c_pinctrl = _i2c_pinctrl(
            soc, any(n.parent == "i2c" for n in nodes)
        )

        env = Environment(
            loader=FileSystemLoader(TEMPLATES),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )

        context = {
            "board": board,
            "board_title": board_name,
            "soc": soc,
            "nodes": nodes,
            "i2c_nodes": [n for n in nodes if n.parent == "i2c"],
            "uart_nodes": [n for n in nodes if n.parent == "uart"],
            "root_nodes": [n for n in nodes if n.parent == "root"],
            "buttons": [n for n in nodes if n.compatible == "gpio-keys"],
            "sensors": [n for n in nodes if n.parent in {"i2c", "root"}
                        and n.compatible != "gpio-keys"],
            "zephyr_ref": self._catalog.ref,
            "binding_source": self._catalog.source,
            "gpio_controllers": _gpio_controllers(nodes, soc),
            "implied_peripherals": _implied(soc),
            "console_pinctrl": pinctrl_block,
            "console_has_pinctrl": has_pinctrl,
            "i2c_pinctrl": i2c_block,
            "i2c_has_pinctrl": has_i2c_pinctrl,
            "i2c_compatible": _bus_compatible(soc, "i2c"),
            "cpucluster": _cpucluster_of(soc),
            "gpio_spec": lambda pin, flags: _gpio_spec(pin, soc, flags),
            "gpio_flags": lambda prop: GPIO_PROPERTY_FLAGS.get(prop, "GPIO_ACTIVE_HIGH"),
            "pin_of": lambda node: self._pin_for(node, analysis, answers),
        }

        base = f"boards/{soc.vendor}/{board}"
        files = {
            f"{base}/board.yml": "board.yml.j2",
            f"{base}/{board}.dts": "board.dts.j2",
            f"{base}/{board}_defconfig": "board_defconfig.j2",
            f"{base}/Kconfig.{board}": "Kconfig.board.j2",
            f"{base}/board.cmake": "board.cmake.j2",
            "app/CMakeLists.txt": "CMakeLists.txt.j2",
            "app/prj.conf": "prj.conf.j2",
            "app/src/main.c": "main.c.j2",
            "README.md": "README.md.j2",
        }

        return {path: env.get_template(name).render(**context) for path, name in files.items()}

    @staticmethod
    def _pin_for(node: DeviceNode, analysis: PCBAnalysis, answers: dict) -> str:
        for index, sensor in enumerate(analysis.sensors):
            if _label(sensor.name) != node.label:
                continue
            pin = (sensor.pins or {}).get("pin") or answers.get(f"sensors[{index}].pins")
            if pin:
                return pin
        raise BoardPortError(f"no pin recorded for '{node.label}'")


def write_port(files: dict[str, str], destination: Path) -> list[Path]:
    written: list[Path] = []
    for relative, content in files.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(target)
    return written
