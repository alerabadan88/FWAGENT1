"""Turn a parsed netlist into a validated :class:`PCBAnalysis`.

Two decisions here are deliberately not left to pattern-matching:

* **Which component is the MCU** is settled by asking the toolchain whether a
  component's value names a part it can target. ``U1`` being first is not
  evidence; ``ATmega328P-PU`` resolving to ``atmega328p`` is.
* **Which pin a sensor sits on** comes from the net that joins them, with the
  MCU's own ``pinfunction`` giving the pin. Nothing is inferred from a
  reference designator or a part number.

Anything the schematic does not say -- a sensor whose type is not recognised,
a connection with no MCU pin name -- is reported rather than filled in.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.exceptions import EDAParseError
from core.hardware_model import MCU, InterfaceType, PCBAnalysis, Sensor
from core.netlist_parser import Netlist, role_for

# Parts the generator has drivers for, with the interface each one speaks.
# A part that is not here still gets reported, so the caller can be asked
# rather than silently dropped.
KNOWN_SENSOR_PARTS = {
    "DHT22": ("temperature_humidity", InterfaceType.GPIO),
    "AM2302": ("temperature_humidity", InterfaceType.GPIO),
    "HC-SR04": ("ultrasonic_distance", InterfaceType.GPIO),
    "HCSR04": ("ultrasonic_distance", InterfaceType.GPIO),
    "BMP280": ("pressure_temperature", InterfaceType.I2C),
}

# An I2C device's address is set by strapping a pin, not by the netlist, so
# it has to be stated. A default is offered because it is the commoner
# strapping, and it is reported rather than applied silently.
DEFAULT_I2C_ADDRESSES = {
    "BMP280": "0x76",
}


@dataclass
class SchematicReport:
    """What the schematic yielded, and what it left open."""

    analysis: PCBAnalysis | None = None
    mcu_ref: str = ""
    mcu_part: str = ""
    unrecognised_parts: list[str] = field(default_factory=list)
    unmapped_connections: list[str] = field(default_factory=list)
    passive_only_pins: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.analysis is not None


def _normalize_part(value: str) -> str:
    return "".join(ch for ch in value.upper() if ch.isalnum() or ch == "-")


def identify_mcu(netlist: Netlist, catalog) -> tuple[str, str]:
    """Find the component that is a microcontroller the toolchain knows.

    Returns ``(reference, part)``. Raises if there is not exactly one.
    """
    matches: list[tuple[str, str]] = []
    for component in netlist.components.values():
        if component.is_passive or not component.value:
            continue
        resolved = catalog.resolve(component.value)
        if resolved:
            matches.append((component.ref, resolved))

    if not matches:
        values = ", ".join(
            sorted({c.value for c in netlist.components.values() if c.value})
        )
        raise EDAParseError(
            f"no component in this schematic names a part the toolchain can target "
            f"(saw: {values}). Either the MCU is not an AVR, or its value field does "
            f"not carry the part number."
        )

    if len(matches) > 1:
        listed = ", ".join(f"{ref} ({part})" for ref, part in matches)
        raise EDAParseError(
            f"more than one component looks like a microcontroller: {listed}. "
            f"Multi-MCU boards are not supported."
        )

    return matches[0]


def analyse_netlist(netlist: Netlist, catalog, board: str | None = None) -> SchematicReport:
    """Build a :class:`PCBAnalysis` from real connectivity."""
    mcu_ref, mcu_part = identify_mcu(netlist, catalog)
    facts = catalog.facts(mcu_part)

    report = SchematicReport(mcu_ref=mcu_ref, mcu_part=mcu_part)

    # Gather every signal connection from the MCU, grouped by the part at the
    # other end, so a two-wire sensor arrives as one sensor with two roles.
    pins_by_ref: dict[str, dict[str, str]] = {}
    for mcu_node, others in netlist.connections_to(mcu_ref):
        if not mcu_node.function:
            report.unmapped_connections.append(
                f"net '{_net_label(netlist, mcu_ref, mcu_node.pin)}' reaches "
                f"{mcu_ref} pin {mcu_node.pin}, but the schematic does not name "
                f"which MCU pin that is"
            )
            continue

        active = [
            other for other in others
            if (component := netlist.components.get(other.ref)) is not None
            and not component.is_passive
        ]

        if not active:
            # Everything on this net is a passive. That is not nothing: an
            # analog sensor is often a resistive divider, which in a netlist is
            # indistinguishable from any other passive network. Reporting it
            # beats dropping it, because the pin is clearly in use.
            parts = ", ".join(
                f"{o.ref} ({netlist.components[o.ref].value})"
                for o in others
                if o.ref in netlist.components
            )
            report.passive_only_pins.append(
                f"{mcu_node.function} is wired to passives only ({parts}). If that "
                f"is an analog sensor, its type has to be stated -- a divider "
                f"cannot be identified from a netlist."
            )
            continue

        for other in active:
            role = role_for(other.function)
            pins_by_ref.setdefault(other.ref, {})[role] = mcu_node.function

    sensors: list[Sensor] = []
    for ref, pins in sorted(pins_by_ref.items()):
        component = netlist.components[ref]
        known = KNOWN_SENSOR_PARTS.get(_normalize_part(component.value))
        if known is None:
            report.unrecognised_parts.append(
                f"{ref} ({component.value}) on {', '.join(sorted(pins.values()))}"
            )
            continue

        sensor_type, interface = known

        if interface == InterfaceType.I2C:
            # The bus pins are the MCU's, and the address is a strap the
            # netlist does not record. Say which was assumed.
            address = DEFAULT_I2C_ADDRESSES.get(_normalize_part(component.value))
            if address is None:
                report.unrecognised_parts.append(
                    f"{ref} ({component.value}) is on I2C but its address is not "
                    f"known and a netlist does not carry one"
                )
                continue
            report.notes.append(
                f"{component.value} address assumed to be {address}; a netlist "
                f"cannot show it, since it is set by strapping a pin"
            )
            sensors.append(Sensor(
                name=component.value,
                type=sensor_type,
                interface=interface,
                bus="I2C1",
                address=address,
                required=True,
            ))
            continue

        sensors.append(Sensor(
            name=component.value,
            type=sensor_type,
            interface=interface,
            pins=pins,
            required=True,
        ))

    if not sensors:
        report.notes.append(
            "no sensor with a known driver is wired to the MCU in this schematic"
        )
        return report

    mcu = MCU(
        name=facts.part,
        family="AVR",
        flash_kb=facts.flash_kb,
        ram_kb=facts.ram_kb,
        # The schematic does not carry the crystal frequency; it is asked for
        # separately rather than assumed here.
        clock_mhz=16,
        gpio_pins=sum(facts.ports.values()) or 1,
        voltage=5.0,
    )
    report.analysis = PCBAnalysis(mcu=mcu, sensors=sensors, board=board)
    report.notes.append(
        "clock frequency and supply voltage are not in a netlist; the defaults "
        "shown must be confirmed"
    )
    return report


def _net_label(netlist: Netlist, ref: str, pin: str) -> str:
    for net in netlist.nets:
        if any(node.ref == ref and node.pin == pin for node in net.nodes):
            return net.name
    return "?"
