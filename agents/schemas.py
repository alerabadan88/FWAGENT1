"""The contract between the language model and the deterministic pipeline.

The model never hands data straight to the code generator. It produces a
*draft* in these shapes; `agents/normalizer.py` then validates that draft
against the real hardware models in `core/` and rejects anything that does not
survive. A model that hallucinates a sensor on a pin the part does not have
produces a `HardwareValidationError`, not a firmware image.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Confidence(str, Enum):
    """How the model came by a value — surfaced so users can audit inferences."""

    STATED = "stated"      # the user said it outright
    INFERRED = "inferred"  # deduced from something the user said
    ASSUMED = "assumed"    # a default the model chose; always worth confirming


class SensorDraft(BaseModel):
    """A sensor as the model understood it. Not yet trusted."""

    name: str = Field(description="Part number as printed on the device, e.g. DHT22")
    type: str = Field(description="What it measures, e.g. temperature_humidity")
    interface: str = Field(description="One of I2C, SPI, UART, GPIO, ADC, 1-Wire")
    pins: dict[str, str] | None = Field(
        default=None,
        description="Pin labels by role, e.g. {'pin': 'D2'} or {'trigger': 'D9', 'echo': 'D10'}",
    )
    bus: str | None = Field(default=None, description="Bus or port name, e.g. I2C1, UART2")
    address: str | None = Field(default=None, description="I2C address, e.g. 0x68")
    required: bool = Field(default=True, description="False if the board works without it")
    confidence: Confidence = Confidence.STATED


class HardwareDraft(BaseModel):
    """The board as the model understood it."""

    mcu_name: str | None = Field(default=None, description="Part number, e.g. ATmega328P")
    mcu_family: str | None = Field(default=None, description="e.g. AVR, ESP32, STM32F4")
    board_name: str | None = Field(default=None, description="e.g. Arduino Uno")
    f_cpu_hz: int | None = Field(default=None, description="Clock in Hz if stated")
    supply_voltage: float | None = None
    sensors: list[SensorDraft] = Field(default_factory=list)


class OpenQuestion(BaseModel):
    """Something the pipeline needs and nobody has said yet.

    ``why`` is required on purpose: a question a user cannot see the point of
    is a question they will answer badly.
    """

    field: str = Field(description="Which spec field the answer fills")
    question: str = Field(description="Asked to the user, in their language")
    why: str = Field(description="What breaks or gets guessed if this goes unanswered")
    options: list[str] = Field(default_factory=list, description="Suggested answers, if any")
    default: str | None = Field(default=None, description="What is assumed if skipped")


class ExtractionResult(BaseModel):
    """What the model returns for one turn of the interview."""

    hardware: HardwareDraft
    questions: list[OpenQuestion] = Field(default_factory=list)
    assumptions: list[str] = Field(
        default_factory=list,
        description="Anything filled in that the user did not state, in plain words",
    )
    unsupported: list[str] = Field(
        default_factory=list,
        description="Parts of the request this system cannot do, said plainly",
    )


class SensorPolicy(BaseModel):
    """Per-sensor runtime behaviour — the decisions currently hardcoded."""

    sample_period_ms: int = Field(gt=0)
    critical: bool = Field(
        default=False,
        description="A failed read on a critical sensor is retried and flagged, not just logged",
    )
    retry_count: int = Field(default=0, ge=0, le=10)


class FirmwareSpec(BaseModel):
    """The validated brief handed to the code generator.

    Everything here was either stated by the user or explicitly confirmed. The
    generator's signature takes exactly these values, so nothing in this class
    is decorative.
    """

    f_cpu_hz: int = Field(gt=0)
    uart_baud: int = Field(gt=0)
    loop_period_ms: int = Field(gt=0)
    policies: dict[str, SensorPolicy] = Field(default_factory=dict)

    def slowest_period_ms(self) -> int:
        if not self.policies:
            return self.loop_period_ms
        return max(p.sample_period_ms for p in self.policies.values())


# --- Parts described by a model rather than hand-written ----------------------


class ConversionKind(str, Enum):
    """How a raw reading becomes an engineering value.

    Deliberately restricted. A linear conversion is auditable at a glance --
    two numbers a reviewer can check against a datasheet line. A polynomial
    compensation like the BMP280's is not: it is twenty shifts and constants
    where a single wrong digit yields readings that look right and are not.
    Those need a hand-written, separately verified driver, so the model is not
    allowed to supply one.
    """

    RAW = "raw"
    LINEAR = "linear"


class Measurement(BaseModel):
    """One value a sensor reports."""

    name: str = Field(description="Short identifier, e.g. temperature")
    unit: str = Field(description="Engineering unit after conversion, e.g. centi_c")
    # Named start_register rather than register: the latter shadows a
    # BaseModel attribute and Pydantic warns about it.
    start_register: int = Field(ge=0, le=0xFF, description="Register the reading starts at")
    length: int = Field(ge=1, le=4, description="Bytes to read")
    big_endian: bool = True
    signed: bool = False
    conversion: ConversionKind = ConversionKind.RAW
    scale_numerator: int = Field(default=1, description="value = raw * num / den + offset")
    scale_denominator: int = Field(default=1, gt=0)
    offset: int = 0


class SensorProfile(BaseModel):
    """A part as a model described it. Not trusted until every field is checked.

    This is the replacement for a hand-maintained parts list: the model
    supplies the shape, `agents/part_lookup.py` validates it, and the generator
    builds a driver from the same verified I2C primitives the BMP280 uses.
    What it cannot do is make the register map true -- see `verified`.
    """

    part: str
    description: str = ""
    interface: str = Field(default="I2C", description="Only I2C is supported here")
    default_address: int = Field(description="7-bit, unshifted")
    alternate_addresses: list[int] = Field(default_factory=list)

    id_register: int | None = Field(default=None, ge=0, le=0xFF)
    id_value: int | None = Field(default=None, ge=0, le=0xFF)

    init_writes: list[tuple[int, int]] = Field(
        default_factory=list,
        description="(register, value) pairs written once at startup",
    )
    startup_delay_ms: int = Field(default=0, ge=0, le=1000)
    measurements: list[Measurement] = Field(default_factory=list)

    datasheet_url: str = ""
    provenance: str = Field(
        default="",
        description="Where this came from, in words a reviewer can act on",
    )

    @property
    def verified(self) -> bool:
        """Whether a human has checked the register map against a datasheet.

        Always False for a model-derived profile. The generated driver says so
        in its own header, because a register map that is merely plausible
        produces firmware that runs and reports wrong numbers.
        """
        return False
