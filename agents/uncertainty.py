"""Everything this pipeline does not know, enumerated deterministically.

The rule this module exists to enforce: **no load-bearing value is ever filled
in silently.** If nobody has said what clocks the board, what an ADC channel is
referenced against, or which way round TX and RX are wired, that is a question
for the person who built the PCB -- not a default.

Why the enumeration is code and not a prompt
--------------------------------------------
The obvious design is to tell a model "ask when you are unsure". It does not
work. Filling gaps plausibly is what a language model does by default; asking
it to notice its own gaps makes it report fewer of them than exist, not more.
So the list of unknowns is derived here, in ordinary code, from what the
generator actually consumes. A model that forgets to wonder about the clock
cannot cause a silent 16x timing error, because `scan_draft` raises the
question whether or not anything wondered.

The model's job is narrower and it is good at it: phrasing the question in the
user's language, and reading the answer back into a value.

Blocking versus advisory
------------------------
The split is not about importance, it is about **how a wrong answer fails**:

* If a wrong value fails *loudly* -- the build errors, the device NACKs, the
  serial output is visible garbage -- a default is fine. Someone will notice.
* If a wrong value fails *silently* -- plausible readings that are wrong, a
  sensor that is simply never read, timing off by a constant factor -- there is
  no default. It blocks, and a human answers.

That is the whole of `Uncertainty.blocking`, and every entry below states its
failure mode in `failure` so the classification can be argued with.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agents.schemas import Confidence, HardwareDraft, OpenQuestion
from core.hardware_model import InterfaceType, PCBAnalysis

# Parts whose I2C address is set by a pin the firmware cannot read. Knowing the
# part is not knowing the address: the same family sits at either, and the
# wrong one means a *different device* answers and returns plausible numbers.
ADDRESS_SELECTABLE = {
    "BMP280": (0x76, 0x77, "SDO"),
    "BME280": (0x76, 0x77, "SDO"),
    "SHT31": (0x44, 0x45, "ADDR"),
    "SHT35": (0x44, 0x45, "ADDR"),
    "MPU6050": (0x68, 0x69, "AD0"),
    "ADS1115": (0x48, 0x49, "ADDR"),
    "SSD1306": (0x3C, 0x3D, "SA0"),
    "TCA9548A": (0x70, 0x77, "A0..A2"),
}

# What an ADC reading is measured against. Every one of these produces a
# perfectly stable, perfectly wrong number if it is guessed.
ADC_REFERENCES = ["AVcc (the supply)", "internal 1.1 V", "external AREF pin"]

STANDARD_BAUDS = [9600, 19200, 38400, 57600, 115200]


@dataclass(frozen=True)
class Uncertainty:
    """One thing nobody has said, and what happens if it is guessed."""

    field: str
    question: str
    why: str
    failure: str
    blocking: bool
    options: list[str] = field(default_factory=list)
    default: str | None = None
    asked_of: str = "the person who wired the board"

    def to_question(self) -> OpenQuestion:
        """The shape the interview loop and the chat interface consume."""
        return OpenQuestion(
            field=self.field,
            question=self.question,
            why=f"{self.why} If it is wrong: {self.failure}",
            options=list(self.options),
            default=None if self.blocking else self.default,
        )


def _u(field_name: str, question: str, why: str, failure: str, *, blocking: bool,
       options=None, default=None, asked_of="the person who wired the board") -> Uncertainty:
    return Uncertainty(
        field=field_name, question=question, why=why, failure=failure,
        blocking=blocking, options=list(options or []), default=default,
        asked_of=asked_of,
    )


# --- Clock and power ---------------------------------------------------------


def clock_uncertainties(draft: HardwareDraft, answers: dict[str, str]) -> list[Uncertainty]:
    """The clock is the single most damaging thing to assume.

    An AVR ships with CKDIV8 set and the internal RC selected, so an ATmega328P
    that nobody has configured runs at 1 MHz, not the 16 MHz its Arduino board
    would give it. Every delay and the baud divisor scale with this.
    """
    out: list[Uncertainty] = []

    if "f_cpu_source" not in answers:
        out.append(_u(
            "f_cpu_source",
            "What clocks the board -- an external crystal or resonator, or the "
            "MCU's internal RC oscillator?",
            "The fuses that select the clock also decide whether the part boots "
            "divided by 8.",
            "every delay and the UART divisor are off by a constant factor, and "
            "nothing reports it -- the firmware runs, just at the wrong speed",
            blocking=True,
            options=["external crystal", "internal RC oscillator", "not sure"],
        ))

    if not draft.f_cpu_hz and "f_cpu_hz" not in answers:
        out.append(_u(
            "f_cpu_hz",
            "What frequency does the MCU actually run at, in Hz?",
            "This is the frequency after any fuse divider, not the crystal's "
            "marking. A 16 MHz crystal with CKDIV8 set gives 2 MHz.",
            "delays and baud rate are wrong by that factor; serial output is "
            "unreadable and timing-critical sensors silently misread",
            blocking=True,
            options=["16000000", "8000000", "1000000"],
        ))

    return out


def power_uncertainties(draft: HardwareDraft, answers: dict[str, str]) -> list[Uncertainty]:
    out: list[Uncertainty] = []

    has_analog = any((s.interface or "").upper() == "ADC" for s in draft.sensors)

    if draft.supply_voltage is None and "supply_voltage" not in answers:
        out.append(_u(
            "supply_voltage",
            "What voltage does the MCU run at?",
            "It sets the ADC full-scale value when AVcc is the reference, and "
            "decides whether a 3.3 V sensor can be driven directly."
            + (" There is an analog sensor here, so this scales its readings." if has_analog else ""),
            "analog readings are scaled wrong by the ratio of the two voltages "
            "-- stable, plausible, and incorrect",
            blocking=has_analog,
            options=["5.0", "3.3"],
            default=None if has_analog else "5.0",
        ))

    return out


# --- Serial ------------------------------------------------------------------


def uart_uncertainties(
    draft: HardwareDraft, answers: dict[str, str], usart_count: int = 1
) -> list[Uncertainty]:
    """Serial is where wiring mistakes are least visible.

    TX into TX is the classic one: the board transmits into another
    transmitter, hears nothing, and reports no error at any layer. Only the
    person holding the board can say which way the pair is crossed.
    """
    out: list[Uncertainty] = []

    if usart_count > 1 and "usart_index" not in answers:
        out.append(_u(
            "usart_index",
            f"This MCU has {usart_count} USARTs. Which one is the output "
            f"actually wired to?",
            "Each USART is a different pair of physical pins.",
            "the firmware transmits correctly on a port nothing is connected "
            "to, and the wired port stays silent -- no error anywhere",
            blocking=True,
            options=[str(i) for i in range(usart_count)],
        ))

    if "uart_wiring" not in answers:
        out.append(_u(
            "uart_wiring",
            "On the connector, is the MCU's TX going to the other side's RX "
            "(crossed), or is the header labelled from the MCU's point of view?",
            "The firmware drives TX and listens on RX; which physical wire that "
            "is depends on how the connector was laid out.",
            "TX meets TX: the board transmits into a transmitter and receives "
            "nothing. No layer reports an error, the link is simply dead",
            blocking=True,
            options=["crossed (MCU TX -> peer RX)", "straight through", "not sure"],
        ))

    if "uart_peer" not in answers:
        out.append(_u(
            "uart_peer",
            "What is on the other end of the serial line?",
            "A USB-serial adapter tolerates anything; a radio module or a second "
            "MCU may need a fixed rate, and a 3.3 V peer must not be driven at 5 V.",
            "a 5 V line into a 3.3 V-only receiver can damage it, and that is "
            "not recoverable in firmware",
            blocking=False,
            options=["USB-serial adapter", "another MCU", "radio module", "nothing yet"],
            default="USB-serial adapter",
        ))

    if "uart_baud" not in answers:
        out.append(_u(
            "uart_baud",
            "What baud rate should the serial output use?",
            "Whatever reads the output has to match exactly, and not every rate "
            "is reachable from this clock within tolerance.",
            "the output is visible garbage, which is at least obvious",
            blocking=False,
            options=[str(b) for b in STANDARD_BAUDS],
            default="9600",
        ))

    return out


# --- Sensors -----------------------------------------------------------------


def sensor_uncertainties(draft: HardwareDraft, answers: dict[str, str]) -> list[Uncertainty]:
    out: list[Uncertainty] = []

    for index, sensor in enumerate(draft.sensors):
        interface = (sensor.interface or "").upper()
        name = sensor.name
        prefix = f"sensors[{index}]"

        if not interface and f"{prefix}.interface" not in answers:
            out.append(_u(
                f"{prefix}.interface",
                f"How is the {name} connected -- I2C, SPI, UART, a plain GPIO "
                f"pin, an analog input, or 1-Wire?",
                "It decides which driver is generated and which peripheral is "
                "brought up.",
                "the wrong peripheral is initialised and the sensor is never "
                "read; the loop still runs and reports zeros",
                blocking=True,
                options=["I2C", "SPI", "UART", "GPIO", "ADC", "1-Wire"],
            ))
            continue

        if interface in {"GPIO", "ADC", "1-WIRE"} and not sensor.pins:
            key = f"{prefix}.pins"
            if key not in answers:
                hint = (
                    "trigger and echo, in that order"
                    if name.upper() in {"HC-SR04", "HCSR04"}
                    else "the MCU pin, e.g. PD2, or the board label, e.g. D2"
                )
                out.append(_u(
                    key,
                    f"Which pin is the {name} wired to? ({hint})",
                    "The firmware toggles one specific port bit; nothing else "
                    "on the chip can tell it which.",
                    "a different pin is driven. The sensor is never read and "
                    "the firmware reports no fault -- it reads a floating input",
                    blocking=True,
                ))

        if interface == "GPIO" and _looks_like_an_input(name, sensor.type):
            out.extend(_digital_input_uncertainties(name, prefix, answers))

        if interface == "ADC":
            out.extend(_adc_uncertainties(name, prefix, answers))

        if interface == "I2C":
            out.extend(_i2c_uncertainties(sensor, prefix, answers))

        if interface == "UART":
            out.extend(_uart_peripheral_uncertainties(name, prefix, answers))

        skey = f"{prefix}.sample_period_ms"
        if skey not in answers:
            out.append(_u(
                skey,
                f"How often should the {name} be read, in milliseconds?",
                "It drives power draw and how fresh the data is.",
                "too fast returns the previous reading on many parts; this is "
                "checked against the datasheet floor before generating",
                blocking=False,
                default="1000",
                asked_of="whoever decides what the product needs",
            ))

        ckey = f"{prefix}.critical"
        if ckey not in answers:
            out.append(_u(
                ckey,
                f"Is the {name} reading critical -- should a failed read be "
                f"retried and flagged rather than skipped?",
                "Critical readings get retries and an explicit error on the "
                "wire; others are logged and the loop moves on.",
                "a failure is silently skipped and the last good value looks "
                "current",
                blocking=False,
                options=["no", "yes"],
                default="no",
                asked_of="whoever decides what the product needs",
            ))

    return out


_INPUT_WORDS = ("button", "switch", "boton", "botón", "pulsador", "interruptor",
                "input", "contact", "reed", "pir", "endstop", "limit")


def _looks_like_an_input(name: str, type_: str) -> bool:
    text = f"{name} {type_}".lower()
    return any(word in text for word in _INPUT_WORDS)


def _digital_input_uncertainties(
    name: str, prefix: str, answers: dict[str, str]
) -> list[Uncertainty]:
    """A switch has two questions the part number cannot answer.

    Which way it is wired decides whether a press reads as 0 or 1, and a
    floating input with no pull resistor reads as noise -- often as a stream of
    phantom presses, which looks like a working button behaving badly rather
    than like a wiring fault.
    """
    out: list[Uncertainty] = []

    if f"{prefix}.active_level" not in answers:
        out.append(_u(
            f"{prefix}.active_level",
            f"When the {name} is pressed, does the pin go to ground (active "
            f"low) or to the supply (active high)?",
            "This is decided by which side of the switch is wired to the pin, "
            "and it inverts the logic in firmware.",
            "the input is read inverted: the firmware thinks the button is held "
            "down whenever it is not, and no error is reported",
            blocking=True,
            options=["active low (switch to ground)", "active high (switch to supply)"],
        ))

    if f"{prefix}.pull_resistor" not in answers:
        out.append(_u(
            f"{prefix}.pull_resistor",
            f"Does the board have a pull-up or pull-down resistor on the {name}, "
            f"or should the MCU's internal pull-up be enabled?",
            "An input pin with nothing holding it reads whatever is induced on "
            "the trace.",
            "the pin floats and reads noise -- typically as phantom presses, "
            "which reads like a flaky button rather than a wiring fault",
            blocking=True,
            options=["board has one", "enable the MCU's internal pull-up", "not sure"],
        ))

    if f"{prefix}.debounce_ms" not in answers:
        out.append(_u(
            f"{prefix}.debounce_ms",
            f"How long should the {name} be debounced for, in milliseconds?",
            "Mechanical contacts bounce for a few milliseconds and register as "
            "several presses.",
            "one press counts as several; visible in behaviour, so 20 ms is a "
            "reasonable starting point",
            blocking=False,
            default="20",
            asked_of="whoever decides what the product needs",
        ))

    return out


def _uart_peripheral_uncertainties(
    name: str, prefix: str, answers: dict[str, str]
) -> list[Uncertainty]:
    """A serial *sensor* competes for the same peripheral as the debug output."""
    out: list[Uncertainty] = []

    if f"{prefix}.usart_index" not in answers:
        out.append(_u(
            f"{prefix}.usart_index",
            f"Which USART is the {name} wired to, and is it the same one used "
            f"for the debug output?",
            "A serial peripheral is a pair of pins and one hardware block. Two "
            "devices on one USART cannot both be listened to.",
            "the two streams interleave and both are corrupted, or the sensor "
            "is simply never heard",
            blocking=True,
        ))

    if f"{prefix}.baud" not in answers:
        out.append(_u(
            f"{prefix}.baud",
            f"What baud rate does the {name} transmit at?",
            "A serial sensor sends at its own configured rate; it does not "
            "negotiate.",
            "the stream decodes as garbage, which is at least visible",
            blocking=False,
            default="9600",
        ))

    return out


def _adc_uncertainties(name: str, prefix: str, answers: dict[str, str]) -> list[Uncertainty]:
    """Analog is the purest silent-failure case: a wrong reference is stable."""
    out: list[Uncertainty] = []

    if f"{prefix}.adc_reference" not in answers:
        out.append(_u(
            f"{prefix}.adc_reference",
            f"What is the {name}'s analog input measured against -- AVcc, the "
            f"internal 1.1 V reference, or an external voltage on AREF?",
            "The ADC returns a fraction of its reference. Converting that to "
            "volts needs the reference, and the chip cannot report it.",
            "every reading is scaled by the ratio of the two references. The "
            "numbers are stable, plausible, and wrong",
            blocking=True,
            options=ADC_REFERENCES,
        ))

    if f"{prefix}.divider" not in answers:
        out.append(_u(
            f"{prefix}.divider",
            f"Is there a resistor divider between the {name} and the MCU pin? "
            f"If so, what is the ratio (e.g. '2:1' or '10k/10k')?",
            "A divider scales the real voltage before the ADC sees it, and "
            "nothing on the chip knows it is there.",
            "readings are off by the divider ratio, consistently, with no "
            "indication anything is wrong",
            blocking=True,
            options=["no divider", "2:1", "other"],
        ))

    return out


def _i2c_uncertainties(sensor, prefix: str, answers: dict[str, str]) -> list[Uncertainty]:
    out: list[Uncertainty] = []
    name = sensor.name
    selectable = ADDRESS_SELECTABLE.get(name.upper())

    if not sensor.address and f"{prefix}.address" not in answers:
        hint = ""
        if selectable:
            low, high, pin = selectable
            hint = (
                f" A {name} sits at 0x{low:02X} or 0x{high:02X} depending on how "
                f"its {pin} pin is tied."
            )
        out.append(_u(
            f"{prefix}.address",
            f"What 7-bit I2C address does the {name} answer at?{hint}",
            "The bus is addressed, not wired per device; the address is set by "
            "a pin on the part.",
            "either nothing answers, or -- worse -- a different device on the "
            "bus answers and returns readings that look like this sensor's",
            blocking=True,
        ))
    elif selectable and f"{prefix}.address_confirmed" not in answers:
        low, high, pin = selectable
        stated = str(sensor.address)
        out.append(_u(
            f"{prefix}.address_confirmed",
            f"The {name} is listed at {stated}. Its {pin} pin selects between "
            f"0x{low:02X} and 0x{high:02X} -- can you confirm which way it is "
            f"tied on this board?",
            "This is set by a pin, so the part number does not determine it. "
            "Only the board does.",
            "the other variant of the same family answers instead and reports "
            "plausible numbers from the wrong sensor",
            blocking=False,
            options=[f"0x{low:02X}", f"0x{high:02X}"],
            default=stated,
        ))

    if f"{prefix}.pullups" not in answers:
        out.append(_u(
            f"{prefix}.pullups",
            f"Does the board have pull-up resistors on SDA and SCL for the "
            f"{name}, or should the MCU's internal ones be enabled?",
            "I2C is open-drain: with no pull-up the bus never rises and every "
            "transfer fails. The MCU's internal pull-ups are weak but usually "
            "work at 100 kHz.",
            "every transfer times out. This one at least fails loudly",
            blocking=False,
            options=["board has pull-ups", "use the MCU's internal pull-ups", "not sure"],
            default="board has pull-ups",
        ))

    return out


# --- System ------------------------------------------------------------------


def system_uncertainties(draft: HardwareDraft, answers: dict[str, str]) -> list[Uncertainty]:
    out: list[Uncertainty] = []

    if not draft.mcu_name and "mcu_name" not in answers:
        out.append(_u(
            "mcu_name",
            "Which microcontroller is on the board? The exact part number, as "
            "printed on the package.",
            "The compiler target, the register map, the pin count and the "
            "memory budget all come from it.",
            "nothing can be generated at all -- this one fails loudly, which "
            "is why it is asked first",
            blocking=True,
        ))

    if "loop_period_ms" not in answers:
        out.append(_u(
            "loop_period_ms",
            "How often should the whole measurement cycle run, in milliseconds?",
            "It sets data freshness and power draw.",
            "the loop runs faster than the slowest sensor allows; that is "
            "checked and refused before generating",
            blocking=False,
            default="2000",
            asked_of="whoever decides what the product needs",
        ))

    return out


# --- Entry points ------------------------------------------------------------


def scan_draft(
    draft: HardwareDraft,
    answers: dict[str, str] | None = None,
    usart_count: int = 1,
) -> list[Uncertainty]:
    """Everything unresolved about a draft, blocking items first.

    Ordered so a user who stops answering part way through has answered the
    things that would otherwise fail silently.
    """
    answers = answers or {}

    found = (
        system_uncertainties(draft, answers)
        + clock_uncertainties(draft, answers)
        + power_uncertainties(draft, answers)
        + sensor_uncertainties(draft, answers)
        + uart_uncertainties(draft, answers, usart_count)
    )

    return sorted(found, key=lambda u: not u.blocking)


def blocking(uncertainties: list[Uncertainty]) -> list[Uncertainty]:
    return [u for u in uncertainties if u.blocking]


def contention(draft: HardwareDraft, usart_count: int = 1) -> list[str]:
    """Demands on the hardware that cannot all be met at once.

    Distinct from an uncertainty: no answer from the user resolves these,
    because the part does not have the peripherals the design needs. Asking
    would be dishonest -- the right response is to say what is short and let
    the person decide what to drop or change.
    """
    problems: list[str] = []

    serial_sensors = [s for s in draft.sensors if (s.interface or "").upper() == "UART"]
    # The debug output is itself a consumer; the firmware always emits one.
    needed = len(serial_sensors) + 1

    if serial_sensors and needed > usart_count:
        names = ", ".join(s.name for s in serial_sensors)
        problems.append(
            f"{needed} serial ports are needed ({names}, plus the debug output) "
            f"but this MCU has {usart_count}. Either give up the debug output, "
            f"move a device to another interface, or use a part with more "
            f"USARTs. Bit-banged serial is not generated here, and a GPS at "
            f"9600 baud on a software UART is not something to rely on."
        )

    i2c = [s for s in draft.sensors if (s.interface or "").upper() == "I2C"]
    by_address: dict[str, list[str]] = {}
    for sensor in i2c:
        if sensor.address:
            by_address.setdefault(str(sensor.address).lower(), []).append(sensor.name)
    for address, names in by_address.items():
        if len(names) > 1:
            problems.append(
                f"{' and '.join(names)} are both at {address} on the same bus. "
                f"One of them has to be moved with its address-select pin, or "
                f"put behind a multiplexer."
            )

    return problems


def scan_analysis(
    analysis: PCBAnalysis, provenance: dict[str, Confidence] | None = None
) -> list[Uncertainty]:
    """Uncertainties in an already-built brief, e.g. one loaded from a file.

    A `PCBAnalysis` has a value for everything -- that is what makes it valid.
    What it does not carry is *who decided* each value, so `provenance` says
    which fields were assumed rather than stated. Anything marked ASSUMED on a
    load-bearing field becomes a question, because a config file cannot tell
    you whether 16 MHz was measured or copied from an example.
    """
    provenance = provenance or {}
    out: list[Uncertainty] = []

    if provenance.get("mcu.clock_mhz") == Confidence.ASSUMED:
        out.append(_u(
            "f_cpu_hz",
            f"The brief says {analysis.mcu.clock_mhz:g} MHz, but nothing "
            f"confirms it. Is that the frequency the part actually runs at?",
            "It was filled in, not stated.",
            "every delay and the baud divisor scale with it",
            blocking=True,
            options=["16000000", "8000000", "1000000"],
        ))

    if provenance.get("mcu.voltage") == Confidence.ASSUMED and any(
        s.interface == InterfaceType.ADC for s in analysis.sensors
    ):
        out.append(_u(
            "supply_voltage",
            f"The brief says {analysis.mcu.voltage:g} V and there is an analog "
            f"sensor. Was that measured?",
            "It sets the ADC full scale.",
            "analog readings are scaled wrong and look stable",
            blocking=True,
            options=["5.0", "3.3"],
        ))

    for index, sensor in enumerate(analysis.sensors):
        if sensor.interface != InterfaceType.I2C:
            continue
        selectable = ADDRESS_SELECTABLE.get(sensor.name.upper())
        if selectable and provenance.get(f"sensors[{index}].address") != Confidence.STATED:
            low, high, pin = selectable
            out.append(_u(
                f"sensors[{index}].address",
                f"{sensor.name} is set to {sensor.address}, but its {pin} pin "
                f"selects between 0x{low:02X} and 0x{high:02X}. Which is it on "
                f"this board?",
                "The part number does not determine the address; the wiring does.",
                "another device answers and returns plausible readings",
                blocking=True,
                options=[f"0x{low:02X}", f"0x{high:02X}"],
            ))

    return out
