"""Tests for reading connectivity out of a schematic.

The netlist is the only input that knows which pin a sensor is wired to, so
these tests are about that mapping arriving intact — and about the parser
saying so when the schematic does not settle something.
"""

from pathlib import Path

import pytest

from core.device_catalog import DeviceCatalog
from core.exceptions import EDAParseError
from core.netlist_parser import (
    Net,
    Node,
    parse_kicad_netlist,
    parse_kicad_netlist_file,
    role_for,
)
from core.schematic import analyse_netlist, identify_mcu
from services.toolchain import AvrToolchain

FIXTURE = Path(__file__).parent / "fixtures" / "netlists" / "uno_sensors.net"

requires_avr = pytest.mark.skipif(
    not AvrToolchain.is_available(), reason="avr-gcc is not installed on this machine"
)


@pytest.fixture(scope="module")
def netlist():
    return parse_kicad_netlist_file(FIXTURE)


@pytest.fixture(scope="module")
def catalog():
    return DeviceCatalog()


def wrap(components: str, nets: str = "") -> str:
    return f'(export (version "E") (components {components}) (nets {nets}))'


# --- Parsing ------------------------------------------------------------------


def test_components_and_values_are_read(netlist):
    assert set(netlist.components) == {"U1", "U2", "U3", "R1", "C1"}
    assert netlist.components["U1"].value == "ATmega328P-PU"
    assert netlist.components["U2"].footprint == "Sensor:DHT22"


def test_nodes_carry_the_mcu_pin_name(netlist):
    dht = next(n for n in netlist.nets if n.name == "/DHT_DATA")
    mcu_node = next(n for n in dht.nodes if n.ref == "U1")

    assert mcu_node.function == "PD2"  # straight from the schematic
    assert mcu_node.pin == "4"          # physical package pin, not the port bit


def test_power_nets_are_recognised(netlist):
    names = {net.name for net in netlist.nets if net.is_power}

    assert names == {"GND", "VCC"}
    assert [net.name for net in netlist.signal_nets()] == [
        "/DHT_DATA", "/SONAR_TRIG", "/SONAR_ECHO"
    ]


def test_a_net_of_only_power_pins_is_a_rail_whatever_its_name():
    net = Net(name="/BOARD_SUPPLY", nodes=[
        Node(ref="U1", pin="7", function="VCC", pin_type="power_in"),
        Node(ref="U2", pin="1", function="VDD", pin_type="power_in"),
    ])

    assert net.is_power


def test_passive_components_are_identified(netlist):
    assert netlist.components["R1"].is_passive
    assert netlist.components["C1"].is_passive
    assert not netlist.components["U2"].is_passive


@pytest.mark.parametrize(
    "text,match",
    [
        ("", "empty"),
        ("(design (source \"x\"))", "does not look like a KiCad netlist"),
        ('(export (version "E") (components ))', "lists no components"),
        ('(export (components (comp (ref "U1")', "unclosed"),
    ],
)
def test_malformed_netlists_are_rejected(text, match):
    with pytest.raises(EDAParseError, match=match):
        parse_kicad_netlist(text)


def test_a_missing_file_is_an_error(tmp_path):
    with pytest.raises(EDAParseError, match="netlist not found"):
        parse_kicad_netlist_file(tmp_path / "absent.net")


@pytest.mark.parametrize(
    "function,role",
    [
        ("TRIG", "trigger"), ("Trigger", "trigger"),
        ("ECHO", "echo"),
        ("DATA", "pin"), ("SIG", "pin"), ("OUT", "pin"),
        ("SDA", "sda"), ("SCL", "scl"),
        ("SOMETHING_ELSE", "pin"),  # falls back rather than failing
    ],
)
def test_sensor_pin_names_map_to_driver_roles(function, role):
    assert role_for(function) == role


# --- Identifying the MCU ------------------------------------------------------


@requires_avr
def test_the_mcu_is_found_by_asking_the_toolchain(netlist, catalog):
    """U1 being first is not evidence; the value resolving to a real part is."""
    ref, part = identify_mcu(netlist, catalog)

    assert (ref, part) == ("U1", "atmega328p")


@requires_avr
def test_a_schematic_with_no_targetable_part_is_refused(catalog):
    text = wrap('(comp (ref "U1") (value "STM32F405RGT6")) (comp (ref "R1") (value "10k"))')

    with pytest.raises(EDAParseError, match="no component .* can target"):
        identify_mcu(parse_kicad_netlist(text), catalog)


@requires_avr
def test_two_microcontrollers_are_refused_rather_than_picked_between(catalog):
    text = wrap(
        '(comp (ref "U1") (value "ATmega328P")) (comp (ref "U5") (value "ATmega2560"))'
    )

    with pytest.raises(EDAParseError, match="more than one component"):
        identify_mcu(parse_kicad_netlist(text), catalog)


# --- Connectivity -------------------------------------------------------------


@requires_avr
def test_sensors_arrive_on_their_real_mcu_pins(netlist, catalog):
    report = analyse_netlist(netlist, catalog)

    by_name = {s.name: s for s in report.analysis.sensors}
    assert by_name["DHT22"].pins == {"pin": "PD2"}
    # Two wires, two roles, taken from the sensor's own pin names.
    assert by_name["HC-SR04"].pins == {"trigger": "PB1", "echo": "PB2"}


@requires_avr
def test_passives_on_a_signal_net_do_not_become_sensors(netlist, catalog):
    """R1 shares the DHT data net as a pull-up; it is not a sensor."""
    report = analyse_netlist(netlist, catalog)

    assert {s.name for s in report.analysis.sensors} == {"DHT22", "HC-SR04"}


@requires_avr
def test_an_unrecognised_part_is_reported_not_dropped(catalog):
    text = wrap(
        '(comp (ref "U1") (value "ATmega328P")) (comp (ref "U9") (value "BME680"))',
        '(net (code "1") (name "/X")'
        ' (node (ref "U1") (pin "4") (pinfunction "PD3") (pintype "bidirectional"))'
        ' (node (ref "U9") (pin "1") (pinfunction "DATA") (pintype "bidirectional")))',
    )

    report = analyse_netlist(parse_kicad_netlist(text), catalog)

    assert any("BME680" in item for item in report.unrecognised_parts)
    assert not report.ok  # nothing generatable, and it says so


@requires_avr
def test_a_connection_with_no_named_mcu_pin_is_reported(catalog):
    text = wrap(
        '(comp (ref "U1") (value "ATmega328P")) (comp (ref "U2") (value "DHT22"))',
        '(net (code "1") (name "/DATA")'
        ' (node (ref "U1") (pin "4") (pintype "bidirectional"))'   # no pinfunction
        ' (node (ref "U2") (pin "2") (pinfunction "DATA") (pintype "bidirectional")))',
    )

    report = analyse_netlist(parse_kicad_netlist(text), catalog)

    assert any("does not name" in item for item in report.unmapped_connections)


@requires_avr
def test_the_netlist_does_not_claim_to_know_the_clock(netlist, catalog):
    """A netlist has no crystal frequency, so that must not look settled."""
    report = analyse_netlist(netlist, catalog)

    assert any("clock frequency" in note for note in report.notes)


# --- All the way to a binary --------------------------------------------------


@requires_avr
def test_a_schematic_becomes_compilable_firmware(netlist, catalog, tmp_path):
    from codegen.generator import generate_firmware
    from services.build_service import BuildService

    report = analyse_netlist(netlist, catalog)
    firmware = generate_firmware(report.analysis)
    build = BuildService().build(firmware, report.analysis.mcu, tmp_path)

    assert build.ok, build.diagnostics
    # The pins in the image are the ones the schematic specified: the netlist
    # said PD2, and the single-wire driver names that role "signal".
    header = firmware.files["config.h"]
    assert "#define DHT22_SIGNAL_PORT   PORTD" in header
    assert "#define DHT22_SIGNAL_BIT    2" in header
    assert "#define HC_SR04_TRIGGER_BIT    1" in header  # PB1
    assert "#define HC_SR04_ECHO_BIT    2" in header     # PB2


@requires_avr
def test_a_pin_wired_only_to_passives_is_reported_not_dropped(catalog):
    """An analog sensor is often a divider, which a netlist cannot distinguish
    from any other passive network. The pin is clearly in use, so say so."""
    text = wrap(
        '(comp (ref "U1") (value "ATmega328P"))'
        ' (comp (ref "U2") (value "DHT22"))'
        ' (comp (ref "R2") (value "LDR 5528"))'
        ' (comp (ref "R3") (value "10k"))',
        '(net (code "1") (name "/DATA")'
        ' (node (ref "U1") (pin "4") (pinfunction "PD2") (pintype "bidirectional"))'
        ' (node (ref "U2") (pin "2") (pinfunction "DATA") (pintype "bidirectional")))'
        ' (net (code "2") (name "/LIGHT")'
        ' (node (ref "U1") (pin "23") (pinfunction "PC0") (pintype "bidirectional"))'
        ' (node (ref "R2") (pin "2") (pinfunction "~") (pintype "passive"))'
        ' (node (ref "R3") (pin "1") (pinfunction "~") (pintype "passive")))',
    )

    report = analyse_netlist(parse_kicad_netlist(text), catalog)

    assert any("PC0" in item for item in report.passive_only_pins)
    assert any("LDR 5528" in item for item in report.passive_only_pins)
    # The rest of the board still comes through.
    assert [s.name for s in report.analysis.sensors] == ["DHT22"]


@requires_avr
def test_the_shipped_example_netlist_is_a_real_kicad_export(catalog):
    """The examples must be readable by the command that documents them."""
    example = Path(__file__).parent.parent / "examples" / "arduino-uno" / "board.kicad.net"

    report = analyse_netlist(parse_kicad_netlist_file(example), catalog)

    assert report.ok
    assert report.mcu_part == "atmega328p"
    assert {s.name for s in report.analysis.sensors} == {"DHT22", "HC-SR04"}
