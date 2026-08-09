"""The one place a language model is called.

It does exactly one job: read free-form prose about a board into
:class:`ExtractionResult`. It does not decide anything, generate code, or
choose defaults that reach the firmware -- `agents/normalizer.py` owns all of
that, and rejects whatever the model got wrong.

The call goes through :class:`ExtractorBackend`, so tests inject a canned
response and the whole pipeline is exercised without network or spend.
"""

from __future__ import annotations

import json
import os
from typing import Protocol

from pydantic import ValidationError

from agents.schemas import ExtractionResult
from core.exceptions import FWAgentError

MODEL = "claude-sonnet-5"
MAX_TOKENS = 4096

SYSTEM_PROMPT = """\
You read informal descriptions of embedded hardware and turn them into structured data.

Report what the user said. Do not invent parts, pins, addresses, or clock speeds \
that were not stated or that do not follow necessarily from a named board. When you \
do fill something in, mark it `inferred` or `assumed` and name it in `assumptions` \
so the user can see it and correct it.

Ask about anything genuinely ambiguous, and say in `why` what goes wrong if it stays \
unanswered. Do not ask about sample rates, loop periods, baud rates, or clock \
frequency -- those are always asked separately by the caller, so raising them here \
only duplicates the question.

This system generates Zephyr board ports, so any microcontroller Zephyr targets is \
in scope: ARM Cortex-M, RISC-V, Xtensa and the rest. Do not report a part as \
unsupported for being non-AVR -- whether Zephyr ships a driver for it is checked \
against Zephyr's own bindings afterwards, by code, not by you.

Put something in `unsupported` only when this system genuinely cannot do it: \
wireless stacks, MQTT or QoS levels, anything about cloud connectivity. Say it in \
plain words rather than pretending it is covered.

Respond with JSON only, matching the given schema."""


class ExtractionError(FWAgentError):
    """Raised when the model's response cannot be used."""


class ExtractorBackend(Protocol):
    """Anything that can turn a conversation into raw JSON text."""

    def complete(self, messages: list[dict[str, str]]) -> str: ...


class AnthropicBackend:
    """Calls the real API. Costs money; needs a credential."""

    def __init__(self, client=None, model: str = MODEL) -> None:
        if client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - dependency is declared
                raise ExtractionError(
                    "the 'anthropic' package is required: pip install anthropic"
                ) from exc

            if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
                raise ExtractionError(
                    "no Anthropic credential found. Set ANTHROPIC_API_KEY, or run "
                    "`ant auth login` and the SDK will pick up the profile."
                )
            client = anthropic.Anthropic()

        self._client = client
        self._model = model

    def complete(self, messages: list[dict[str, str]]) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=messages,
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": _response_schema(),
                }
            },
        )

        if getattr(response, "stop_reason", None) == "refusal":
            raise ExtractionError("the model declined to answer this request")

        for block in response.content:
            if getattr(block, "type", None) == "text":
                return block.text

        raise ExtractionError("the model returned no text to parse")


def _response_schema() -> dict:
    """JSON Schema the model must satisfy, derived from the Pydantic model.

    Generated rather than hand-written so it cannot drift from the class the
    response is parsed into.
    """
    schema = ExtractionResult.model_json_schema()
    _harden(schema)
    return schema


def _harden(node: object) -> None:
    """Structured outputs reject open objects; close every one, in place."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            node.setdefault("additionalProperties", False)
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["required"] = list(properties)
        for value in node.values():
            _harden(value)
    elif isinstance(node, list):
        for item in node:
            _harden(item)


class HardwareExtractor:
    """Reads prose into a validated :class:`ExtractionResult`."""

    def __init__(self, backend: ExtractorBackend | None = None) -> None:
        self._backend = backend or AnthropicBackend()

    def extract(self, description: str, history: list[dict[str, str]] | None = None) -> ExtractionResult:
        if not description or not description.strip():
            raise ExtractionError("nothing to extract from an empty description")

        messages = list(history or [])
        messages.append({"role": "user", "content": description})

        raw = self._backend.complete(messages)
        return self.parse(raw)

    @staticmethod
    def parse(raw: str) -> ExtractionResult:
        """Parse and validate the model's JSON. Malformed output is an error."""
        text = raw.strip()
        if text.startswith("```"):
            # Strip a fenced block if one slipped through.
            lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ExtractionError(f"the model did not return valid JSON: {exc}") from exc

        try:
            return ExtractionResult.model_validate(payload)
        except ValidationError as exc:
            raise ExtractionError(
                f"the model's JSON did not match the expected shape: {exc}"
            ) from exc
