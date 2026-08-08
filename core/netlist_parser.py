"""Read connectivity out of a schematic netlist.

This is the input worth having. A datasheet describes a *part*: it says what
PD2 is on an ATmega328P. It cannot say that *your* DHT22 is wired to it --
that fact exists only in your schematic. So the netlist, not a datasheet and
not a model's recollection, is where sensor-to-pin mapping comes from.

KiCad's netlist already names the MCU pin on each connection::

    (net (code "3") (name "/DHT_DATA")
      (node (ref "U1") (pin "4") (pinfunction "PD2") ...)
      (node (ref "U2") (pin "2") (pinfunction "DATA") ...))

so the pin arrives MCU-native and needs no translation. The MCU itself is
identified by asking the toolchain whether a component's value names a part it
can target -- not by pattern-matching the reference designator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from core.exceptions import EDAParseError

# Nets that carry power rather than signal. Filtered so a shared ground does
# not look like every sensor being wired to every other one.
_POWER_NET_NAMES = re.compile(
    r"^/?(gnd|vcc|vdd|vss|\+?[0-9]v[0-9]?|v\+|v-|agnd|dgnd|earth)$", re.IGNORECASE
)
_POWER_PIN_TYPES = {"power_in", "power_out"}

# Reference prefixes for parts that are never a sensor.
_PASSIVE_PREFIXES = ("R", "C", "L", "D", "Y", "X", "FB", "TP", "J", "SW", "F")

# Sensor pin names, mapped to the role the drivers expect.
_ROLE_ALIASES = {
    "trig": "trigger", "trigger": "trigger",
    "echo": "echo",
    "data": "pin", "dat": "pin", "sig": "pin", "signal": "pin", "out": "pin",
    "sda": "sda", "scl": "scl",
    "tx": "tx", "rx": "rx",
}


@dataclass
class Component:
    ref: str
    value: str
    footprint: str = ""

    @property
    def is_passive(self) -> bool:
        prefix = re.match(r"^([A-Za-z]+)", self.ref)
        return bool(prefix) and prefix.group(1).upper() in _PASSIVE_PREFIXES


@dataclass
class Node:
    ref: str
    pin: str
    function: str = ""
    pin_type: str = ""

    @property
    def is_power(self) -> bool:
        return self.pin_type in _POWER_PIN_TYPES


@dataclass
class Net:
    name: str
    nodes: list[Node] = field(default_factory=list)

    @property
    def is_power(self) -> bool:
        stripped = self.name.split("/")[-1]
        if _POWER_NET_NAMES.match(stripped) or _POWER_NET_NAMES.match(self.name):
            return True
        # A net where every node is a power pin is a rail whatever it is called.
        return bool(self.nodes) and all(node.is_power for node in self.nodes)


@dataclass
class Netlist:
    """A parsed schematic: what is on the board and how it is wired."""

    components: dict[str, Component] = field(default_factory=dict)
    nets: list[Net] = field(default_factory=list)
    source: str = ""

    def signal_nets(self) -> list[Net]:
        return [net for net in self.nets if not net.is_power]

    def connections_to(self, ref: str) -> list[tuple[Node, list[Node]]]:
        """For each signal net touching ``ref``: its node, and the others."""
        found = []
        for net in self.signal_nets():
            mine = [node for node in net.nodes if node.ref == ref]
            theirs = [node for node in net.nodes if node.ref != ref]
            if mine and theirs:
                found.append((mine[0], theirs))
        return found


# --- S-expression reading -----------------------------------------------------

_TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+')


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text)


def _parse_sexp(tokens: list[str], position: int = 0):
    """Read one S-expression. Returns the value and the next position."""
    if position >= len(tokens):
        raise EDAParseError("netlist ended in the middle of an expression")

    token = tokens[position]

    if token == "(":
        items = []
        position += 1
        while position < len(tokens) and tokens[position] != ")":
            item, position = _parse_sexp(tokens, position)
            items.append(item)
        if position >= len(tokens):
            raise EDAParseError("netlist has an unclosed '('")
        return items, position + 1

    if token == ")":
        raise EDAParseError("netlist has a ')' with no matching '('")

    if token.startswith('"'):
        return token[1:-1].replace('\\"', '"'), position + 1

    return token, position + 1


def _fields(node: list) -> dict[str, str]:
    """Collect ``(key "value")`` children into a dict."""
    out = {}
    for item in node:
        if isinstance(item, list) and len(item) >= 2 and isinstance(item[0], str):
            if isinstance(item[1], str):
                out[item[0]] = item[1]
    return out


def _find_all(node, tag: str) -> list[list]:
    """Every direct child list whose head is ``tag``."""
    return [
        item for item in node
        if isinstance(item, list) and item and item[0] == tag
    ]


def _find_section(root: list, tag: str) -> list:
    for item in root:
        if isinstance(item, list) and item and item[0] == tag:
            return item
    return []


def parse_kicad_netlist(text: str, source: str = "") -> Netlist:
    """Parse a KiCad ``.net`` export into a :class:`Netlist`."""
    tokens = _tokenize(text)
    if not tokens:
        raise EDAParseError("the netlist is empty")

    tree, _ = _parse_sexp(tokens)
    if not isinstance(tree, list) or not tree or tree[0] != "export":
        raise EDAParseError(
            "this does not look like a KiCad netlist (expected it to start with '(export')"
        )

    netlist = Netlist(source=source)

    for comp in _find_all(_find_section(tree, "components"), "comp"):
        values = _fields(comp)
        ref = values.get("ref")
        if not ref:
            continue
        netlist.components[ref] = Component(
            ref=ref,
            value=values.get("value", ""),
            footprint=values.get("footprint", ""),
        )

    for net in _find_all(_find_section(tree, "nets"), "net"):
        values = _fields(net)
        nodes = []
        for node in _find_all(net, "node"):
            node_values = _fields(node)
            nodes.append(Node(
                ref=node_values.get("ref", ""),
                pin=node_values.get("pin", ""),
                function=node_values.get("pinfunction", ""),
                pin_type=node_values.get("pintype", ""),
            ))
        netlist.nets.append(Net(name=values.get("name", ""), nodes=nodes))

    if not netlist.components:
        raise EDAParseError("the netlist lists no components")

    return netlist


def parse_kicad_netlist_file(path: str | Path) -> Netlist:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise EDAParseError(f"netlist not found: {path}") from exc
    except OSError as exc:
        raise EDAParseError(f"could not read netlist {path}: {exc}") from exc

    return parse_kicad_netlist(text, source=str(path))


def role_for(pin_function: str, fallback: str = "pin") -> str:
    """Map a sensor's own pin name to the role the drivers use."""
    cleaned = re.sub(r"[^a-z]", "", (pin_function or "").lower())
    return _ROLE_ALIASES.get(cleaned, fallback)
