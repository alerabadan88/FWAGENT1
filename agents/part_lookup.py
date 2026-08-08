"""Ask a model about a part instead of keeping a list of them.

This replaces a hand-maintained parts table. It does **not** replace having a
verified driver, and the difference is the whole design:

* **Identifying a part is a knowledge question.** "What is an SHT31?" has a
  right answer a model reliably knows, and a wrong one is recoverable — the
  address gets range-checked, the interface gets cross-checked against the
  schematic, and a part it cannot place is refused rather than guessed at.

* **A register map is not a knowledge question, it is a claim.** A plausible
  but wrong register address produces firmware that compiles, runs, and
  reports numbers that look fine. Nothing downstream can catch that, because
  the only reference for "is 0x2C the right measurement register" would be the
  same model that said so.

So a profile that comes from here is generated into a driver built out of the
already-verified I2C primitives, and is marked unverified everywhere it
surfaces: in the driver's own header, in the CLI, and in the security report.
The claim a human has to check is narrowed to a handful of numbers, and made
impossible to miss.

Polynomial compensation is refused outright — see `ConversionKind`.
"""

from __future__ import annotations

import json
from typing import Protocol

from pydantic import ValidationError

from agents.schemas import ConversionKind, SensorProfile
from core.exceptions import FWAgentError

MODEL = "claude-sonnet-5"
MAX_TOKENS = 4096

SYSTEM_PROMPT = """\
You answer factual questions about I2C sensor parts, as structured data.

Report only what you are confident is in the part's datasheet: its I2C address, \
its identification register and value if it has one, the registers written once \
at startup to begin measuring, and where each measurement is read from.

Accuracy matters far more than coverage here. The output becomes firmware that \
will be trusted to report real measurements, and a register address that is \
merely plausible produces readings that look correct and are not. If you are \
unsure of a specific value, say so in `provenance` rather than supplying your \
best guess.

Conversions must be `raw` or `linear` (value = raw * numerator / denominator + \
offset). If a part needs polynomial compensation from calibration registers -- \
a BMP280, a BME280, an MS5611 -- do not attempt it: return the part with an \
empty `measurements` list and explain in `provenance` that it needs a \
hand-written driver.

Put in `provenance` where the values come from and how sure you are, in words a \
reviewer can act on.

Respond with JSON only, matching the given schema."""


class PartLookupError(FWAgentError):
    """Raised when a part cannot be described usefully."""


class LookupBackend(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str: ...


class AnthropicLookupBackend:
    """Calls the real API. Costs money; needs a credential."""

    def __init__(self, client=None, model: str = MODEL) -> None:
        if client is None:
            import os

            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - declared as an extra
                raise PartLookupError(
                    "the 'anthropic' package is required: "
                    'pip install "fw-automation-agent[agent]"'
                ) from exc

            if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
                raise PartLookupError(
                    "no Anthropic credential found. Set ANTHROPIC_API_KEY, or run "
                    "`ant auth login` and the SDK will pick up the profile."
                )
            client = anthropic.Anthropic()

        self._client = client
        self._model = model

    def complete(self, messages: list[dict[str, str]]) -> str:
        from agents.extractor import _harden

        schema = SensorProfile.model_json_schema()
        _harden(schema)

        response = self._client.messages.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=messages,
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )

        if getattr(response, "stop_reason", None) == "refusal":
            raise PartLookupError("the model declined to answer this request")

        for block in response.content:
            if getattr(block, "type", None) == "text":
                return block.text

        raise PartLookupError("the model returned no text to parse")


def validate_profile(profile: SensorProfile) -> list[str]:
    """Everything checkable about a profile without a datasheet.

    Returns the reasons it is unusable. An empty list means it is *coherent*,
    which is a much weaker claim than correct.
    """
    problems: list[str] = []

    if profile.interface.upper() != "I2C":
        problems.append(
            f"only I2C parts can be generated this way; '{profile.part}' is "
            f"described as {profile.interface}"
        )

    # 0x00-0x07 and 0x78-0x7F are reserved by the I2C specification.
    for address in [profile.default_address, *profile.alternate_addresses]:
        if not 0x08 <= address <= 0x77:
            problems.append(
                f"address 0x{address:02X} is outside the usable 7-bit range "
                f"0x08-0x77, so no device can be there"
            )

    if (profile.id_register is None) != (profile.id_value is None):
        problems.append(
            "an identification register needs both the register and the value "
            "it should return, or neither"
        )

    if not profile.measurements:
        problems.append(
            f"no measurements were described for '{profile.part}', so a driver "
            f"would read nothing"
        )

    for measurement in profile.measurements:
        if measurement.conversion == ConversionKind.LINEAR:
            if measurement.scale_numerator == 0:
                problems.append(
                    f"measurement '{measurement.name}' scales by zero, which "
                    f"reports a constant"
                )
        if measurement.length > 2 and not measurement.signed:
            # Not wrong, but worth flagging: it will not fit a uint16 report.
            pass

    if not profile.provenance.strip():
        problems.append(
            "the profile does not say where its values came from, so a reviewer "
            "has nothing to check against"
        )

    return problems


class PartLookup:
    """Describes a part by asking a model, then checking the answer's shape."""

    def __init__(self, backend: LookupBackend | None = None) -> None:
        self._backend = backend or AnthropicLookupBackend()

    def describe(self, part: str, context: str = "") -> SensorProfile:
        if not part or not part.strip():
            raise PartLookupError("no part name was given")

        question = f"Describe the I2C sensor part {part.strip()}."
        if context.strip():
            question += f"\n\nContext from the schematic: {context.strip()}"

        raw = self._backend.complete([{"role": "user", "content": question}])
        profile = self.parse(raw)

        problems = validate_profile(profile)
        if problems:
            raise PartLookupError(
                f"the description of '{part}' cannot be used: " + "; ".join(problems)
            )

        return profile

    @staticmethod
    def parse(raw: str) -> SensorProfile:
        text = raw.strip()
        if text.startswith("```"):
            text = "\n".join(
                line for line in text.splitlines() if not line.strip().startswith("```")
            ).strip()

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PartLookupError(f"the model did not return valid JSON: {exc}") from exc

        try:
            return SensorProfile.model_validate(payload)
        except ValidationError as exc:
            raise PartLookupError(
                f"the model's JSON did not match the expected shape: {exc}"
            ) from exc
