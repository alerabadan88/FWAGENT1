"""Authoritative per-part facts, taken from the toolchain rather than a model.

A language model is useful for the *naming* problem ("Arduino Nano" is an
ATmega328P board). It is the wrong source for the *facts* that drive code
generation: a misremembered RAM size or a peripheral the part does not have
produces firmware that compiles and then fails silently on the bench.

So this module asks avr-gcc instead. Every number here comes from avr-libc's
own headers, read through the preprocessor, for whichever part is asked about:

    FLASHEND, RAMEND, RAMSTART, E2END, SPM_PAGESIZE

and peripheral presence is decided by whether the part's header actually
defines the corresponding registers. If avr-gcc does not know a part, it is
rejected -- which is exactly right, because a part the compiler cannot target
is a part we cannot build for.
"""

from __future__ import annotations

import ast

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from core.exceptions import FWAgentError
from services.toolchain import AvrToolchain

# Registers whose presence in the part's header proves a peripheral exists.
# Checking the header beats keeping a hand-written table that silently rots.
#
# UART is deliberately absent here: which USART a part has is not a yes/no
# question. The ATmega328P has USART0 (UDR0); the ATmega32U4 has USART1 (UDR1)
# and no USART0 at all. Matching on the wrong register name reports "no UART"
# for a board that plainly has one, so the index is discovered separately.
_PERIPHERAL_MARKERS = {
    "adc": ("ADCSRA",),
    "i2c": ("TWCR",),
    "spi": ("SPCR", "SPCR0"),
    "timer1": ("TCNT1",),
    "watchdog": ("WDTCSR", "WDTCR"),
}

_UDR_PATTERN = re.compile(r"^UDR(\d*)$")
_PORT_PATTERN = re.compile(r"^PORT([A-L])$")
_PORT_BIT_PATTERN = re.compile(r"^P([A-L])(\d)$")
_ADC_CHANNEL_PATTERN = re.compile(r"^ADC(\d+)D$")

# Development boards whose part is not guessable from the name. Kept small on
# purpose: it is a convenience shortcut, and every entry still gets verified
# against the toolchain before use.
BOARD_PARTS = {
    "ARDUINO UNO": "atmega328p",
    "ARDUINO NANO": "atmega328p",
    "ARDUINO PRO MINI": "atmega328p",
    "ARDUINO MINI": "atmega328p",
    "ARDUINO MEGA": "atmega2560",
    "ARDUINO MEGA 2560": "atmega2560",
    "ARDUINO LEONARDO": "atmega32u4",
    "ARDUINO MICRO": "atmega32u4",
    "DIGISPARK": "attiny85",
}


class DeviceNotFoundError(FWAgentError):
    """Raised when the toolchain does not know the requested part."""


@dataclass(frozen=True)
class DeviceFacts:
    """What the toolchain says about a part. No field here is guessed."""

    part: str
    core: str
    flash_bytes: int
    ram_bytes: int
    eeprom_bytes: int
    flash_page_bytes: int
    peripherals: frozenset[str]
    ports: dict[str, int] = field(default_factory=dict)
    """Width of each I/O port the part actually has, e.g. ``{"B": 8, "C": 7}``.
    The ATtiny85 has only port B and only 6 usable bits; the ATmega2560 has
    A through L. Read from the part's header, so a pin can be checked against
    the silicon instead of assumed."""
    adc_channels: int = 0
    usart_suffix: str | None = None
    """Register suffix of the USART to drive: ``"0"`` for UDR0, ``"1"`` for UDR1,
    ``""`` for parts whose registers are unnumbered (UDR/UCSRA), ``None`` for
    parts with no USART at all."""
    source: str = "avr-gcc + avr-libc headers"

    @property
    def has_uart(self) -> bool:
        return self.usart_suffix is not None

    @property
    def flash_kb(self) -> float:
        return self.flash_bytes / 1024

    @property
    def ram_kb(self) -> float:
        return self.ram_bytes / 1024

    def has(self, peripheral: str) -> bool:
        return peripheral in self.peripherals

    def summary(self) -> str:
        listed = set(self.peripherals)
        if self.usart_suffix is not None:
            listed.add(f"usart{self.usart_suffix or '(unnumbered)'}")
        present = ", ".join(sorted(listed)) or "none detected"
        return (
            f"{self.part} ({self.core}): {self.flash_kb:.0f} KB flash, "
            f"{self.ram_kb:.2f} KB RAM, {self.eeprom_bytes} B EEPROM; {present}"
        )


_ALLOWED_OPS = (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Div, ast.LShift, ast.RShift)


def _eval_c_int(text: str) -> int | None:
    """Evaluate an integer expression the preprocessor left behind.

    Some parts define their sizes symbolically -- the ATmega32U4 has
    ``RAMEND = (RAMSTART + RAMSIZE - 1)`` -- so after expansion this can be
    real arithmetic, not just a literal. Parsed with `ast` and walked with an
    allow-list rather than handed to `eval`, because this string comes from a
    file on disk.
    """
    cleaned = re.sub(r"([0-9a-fA-FxX])[UuLl]+\b", r"\1", text.strip())
    if not cleaned:
        return None

    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError:
        return None

    def visit(node: ast.AST) -> int | None:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = visit(node.operand)
            if operand is None:
                return None
            return operand if isinstance(node.op, ast.UAdd) else -operand
        if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_OPS):
            left, right = visit(node.left), visit(node.right)
            if left is None or right is None:
                return None
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, (ast.Div, ast.FloorDiv)):
                return left // right if right else None
            if isinstance(node.op, ast.LShift):
                return left << right
            if isinstance(node.op, ast.RShift):
                return left >> right
        return None

    return visit(tree)


class DeviceCatalog:
    """Looks parts up in the installed toolchain."""

    def __init__(self, toolchain: AvrToolchain | None = None) -> None:
        self._toolchain = toolchain or AvrToolchain()

    @property
    def _device_specs_dir(self) -> Path | None:
        root = self._toolchain.gcc_path.parent.parent
        matches = sorted(root.glob("lib/gcc/avr/*/device-specs"))
        return matches[-1] if matches else None

    def known_parts(self) -> list[str]:
        """Every part this compiler can target, from its own device-specs."""
        directory = self._device_specs_dir
        if directory is None:
            return []
        return sorted(p.name[len("specs-"):] for p in directory.glob("specs-*"))

    def resolve(self, name: str) -> str | None:
        """Map a board or part name to a compiler part name, or ``None``.

        Never invents a part: the result is always something the toolchain
        confirmed it can target.
        """
        if not name or not name.strip():
            return None

        candidate = name.strip()
        board = BOARD_PARTS.get(candidate.upper())
        if board:
            candidate = board

        normalized = re.sub(r"[^a-z0-9]", "", candidate.lower())
        parts = self.known_parts()

        for part in parts:
            if re.sub(r"[^a-z0-9]", "", part) == normalized:
                return part

        # Tolerate a trailing package/variant suffix, e.g. ATmega328P-PU.
        trimmed = re.sub(r"(pu|au|mu|an|pa)$", "", normalized)
        for part in parts:
            if re.sub(r"[^a-z0-9]", "", part) == trimmed:
                return part

        return None

    def facts(self, part: str) -> DeviceFacts:
        """Read the part's real numbers out of the toolchain."""
        resolved = self.resolve(part)
        if resolved is None:
            raise DeviceNotFoundError(
                f"avr-gcc does not know a part called '{part}', so nothing could be "
                f"built for it. Run `known_parts()` to see what is available."
            )
        return _facts_for(str(self._toolchain.gcc_path), resolved)


# Sizes are asked for through a probe rather than read from -dM output,
# because -dM shows a macro's *definition*, which on some parts is symbolic.
# Preprocessing a probe makes the preprocessor expand it for us. The names are
# string literals so they survive expansion themselves.
_SIZE_KEYS = ("FLASHEND", "RAMEND", "RAMSTART", "E2END", "SPM_PAGESIZE")
_PROBE = "#include <avr/io.h>\n" + "".join(
    f'FWQ "{key}" {key}\n' for key in _SIZE_KEYS
)


@lru_cache(maxsize=256)
def _facts_for(gcc_path: str, part: str) -> DeviceFacts:
    """Cached because each lookup costs two compiler invocations."""
    import subprocess
    import tempfile

    def run(args: list[str], source: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.c"
            probe.write_text(source, encoding="utf-8")
            try:
                return subprocess.run(
                    [gcc_path, f"-mmcu={part}", *args, str(probe)],
                    capture_output=True, text=True, timeout=60,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise DeviceNotFoundError(
                    f"could not query avr-gcc about '{part}': {exc}"
                ) from exc

    macros = run(["-dM", "-E", "-include", "avr/io.h"], "")
    if macros.returncode != 0:
        last = macros.stderr.strip().splitlines()
        raise DeviceNotFoundError(
            f"avr-gcc rejected part '{part}': {last[-1] if last else 'unknown error'}"
        )

    defined = {
        match.group(1)
        for line in macros.stdout.splitlines()
        if (match := re.match(r"#define\s+(\w+)", line))
    }

    sizes: dict[str, int] = {}
    expanded = run(["-E", "-P"], _PROBE)
    for line in expanded.stdout.splitlines():
        match = re.match(r'\s*FWQ\s+"(\w+)"\s+(.*)', line)
        if match:
            value = _eval_c_int(match.group(2))
            if value is not None:
                sizes[match.group(1)] = value

    missing = [key for key in ("FLASHEND", "RAMEND", "RAMSTART") if key not in sizes]
    if missing:
        raise DeviceNotFoundError(
            f"avr-libc's headers for '{part}' do not give {', '.join(missing)}, "
            f"so this part cannot be characterised automatically."
        )

    core = "unknown"
    core_match = re.search(r"__AVR_ARCH__\s+(\d+)", macros.stdout)
    if core_match:
        core = f"avr{core_match.group(1)}"

    peripherals = {
        name
        for name, registers in _PERIPHERAL_MARKERS.items()
        if any(register in defined for register in registers)
    }

    # Which ports exist, and how wide each one really is. Both come from the
    # header rather than an assumption that every port is 8 bits: the ATtiny85's
    # port B is 6.
    ports: dict[str, int] = {}
    for name in defined:
        if (match := _PORT_PATTERN.match(name)):
            ports.setdefault(match.group(1), 0)
    for name in defined:
        if (match := _PORT_BIT_PATTERN.match(name)) and match.group(1) in ports:
            letter, bit = match.group(1), int(match.group(2))
            ports[letter] = max(ports[letter], bit + 1)

    adc_channels = 1 + max(
        (int(m.group(1)) for name in defined if (m := _ADC_CHANNEL_PATTERN.match(name))),
        default=-1,
    )

    # Lowest-numbered USART present; "" when the part's registers carry no index.
    suffixes = sorted(
        (match.group(1) for name in defined if (match := _UDR_PATTERN.match(name))),
        key=lambda s: (s == "", s),
    )
    usart_suffix = suffixes[0] if suffixes else None

    return DeviceFacts(
        part=part,
        core=core,
        flash_bytes=sizes["FLASHEND"] + 1,
        ram_bytes=sizes["RAMEND"] - sizes["RAMSTART"] + 1,
        eeprom_bytes=sizes.get("E2END", -1) + 1,
        flash_page_bytes=sizes.get("SPM_PAGESIZE", 0),
        peripherals=frozenset(peripherals),
        ports=ports,
        adc_channels=adc_channels,
        usart_suffix=usart_suffix,
    )
