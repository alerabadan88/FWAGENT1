"""Tests for the interview agent.

None of these call the Anthropic API. The model's job — reading prose into
structure — is isolated behind a backend, so every deterministic decision is
tested with a canned response, for free and offline.
"""

import json

import pytest

from agents.extractor import ExtractionError, HardwareExtractor
from agents.interview import Interview, InterviewState
from agents.normalizer import (
    MIN_SAMPLE_PERIOD_MS,
    NormalizationError,
    check_timing,
    normalize,
    required_questions,
)
from agents.schemas import ExtractionResult, HardwareDraft, SensorDraft
from core.exceptions import HardwareValidationError


class FakeBackend:
    """Returns a canned response and records what it was asked."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages):
        self.calls.append(messages)
        return self.response


def draft_response(**overrides) -> str:
    payload = {
        "hardware": {
            "mcu_name": None,
            "mcu_family": None,
            "board_name": "Arduino Uno",
            "f_cpu_hz": None,
            "supply_voltage": None,
            "sensors": [
                {
                    "name": "DHT22",
                    "type": "temperature_humidity",
                    "interface": "GPIO",
                    "pins": None,
                    "bus": None,
                    "address": None,
                    "required": True,
                    "confidence": "stated",
                }
            ],
        },
        "questions": [],
        "assumptions": [],
        "unsupported": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


def answered_state(**extra) -> InterviewState:
    state = InterviewState(extraction=HardwareExtractor.parse(draft_response()))
    answers = {
        "sensors[0].pins": "D2",
        "sensors[0].sample_period_ms": "2000",
        "sensors[0].critical": "no",
        "loop_period_ms": "2000",
        "uart_baud": "9600",
    }
    answers.update(extra)
    for key, value in answers.items():
        state.answer(key, value)
    return state


# --- Parsing the model's output ----------------------------------------------


def test_malformed_json_is_rejected():
    with pytest.raises(ExtractionError, match="did not return valid JSON"):
        HardwareExtractor.parse("this is not json")


def test_json_of_the_wrong_shape_is_rejected():
    with pytest.raises(ExtractionError, match="did not match the expected shape"):
        HardwareExtractor.parse('{"hardware": {"sensors": "not a list"}}')


def test_a_fenced_code_block_is_tolerated():
    result = HardwareExtractor.parse(f"```json\n{draft_response()}\n```")

    assert result.hardware.board_name == "Arduino Uno"


def test_an_empty_description_is_refused_without_calling_the_model():
    backend = FakeBackend(draft_response())

    with pytest.raises(ExtractionError, match="empty description"):
        HardwareExtractor(backend).extract("   ")

    assert backend.calls == []


def test_extraction_passes_the_description_to_the_backend():
    backend = FakeBackend(draft_response())

    HardwareExtractor(backend).extract("an Uno with a DHT22")

    assert backend.calls[0][-1]["content"] == "an Uno with a DHT22"


# --- Questions are decided by the code, not the model -------------------------


def test_required_questions_come_from_the_pipeline_not_the_model():
    """The model returned zero questions; the code must still ask what it needs."""
    extraction = HardwareExtractor.parse(draft_response())
    assert extraction.questions == []

    fields = {q.field for q in required_questions(extraction.hardware)}

    assert "loop_period_ms" in fields
    assert "uart_baud" in fields
    assert "sensors[0].pins" in fields
    assert "sensors[0].critical" in fields


def test_a_known_board_is_not_asked_about():
    """'Arduino Uno' already determines the part and clock — don't ask twice."""
    extraction = HardwareExtractor.parse(draft_response())

    fields = {q.field for q in required_questions(extraction.hardware)}

    assert "mcu_name" not in fields
    assert "f_cpu_hz" not in fields


def test_an_unknown_board_is_asked_about():
    draft = HardwareDraft(board_name=None, mcu_name=None, sensors=[])

    fields = {q.field for q in required_questions(draft)}

    assert "mcu_name" in fields


def test_every_question_explains_why_it_matters():
    extraction = HardwareExtractor.parse(draft_response())

    for question in required_questions(extraction.hardware):
        assert question.why.strip(), question.field


def test_answered_questions_stop_being_asked():
    extraction = HardwareExtractor.parse(draft_response())

    before = {q.field for q in required_questions(extraction.hardware)}
    after = {q.field for q in required_questions(extraction.hardware, {"loop_period_ms": "5000"})}

    assert "loop_period_ms" in before
    assert "loop_period_ms" not in after


def test_i2c_sensors_are_asked_for_an_address():
    draft = HardwareDraft(
        board_name="Arduino Uno",
        sensors=[SensorDraft(name="MPU6050", type="imu", interface="I2C", bus="I2C1")],
    )

    fields = {q.field for q in required_questions(draft)}

    assert "sensors[0].address" in fields


# --- Normalization ------------------------------------------------------------


def test_normalize_refuses_while_questions_are_outstanding():
    extraction = HardwareExtractor.parse(draft_response())

    with pytest.raises(NormalizationError, match="still unanswered"):
        normalize(extraction, {})


def test_normalize_produces_a_validated_brief():
    state = answered_state()

    analysis, spec = normalize(state.extraction, state.answers)

    # The canonical part id from the toolchain: this value becomes -mmcu.
    assert analysis.mcu.name.lower() == "atmega328p"
    assert analysis.mcu.flash_kb == 32
    assert analysis.sensors[0].pins == {"pin": "D2"}
    assert spec.f_cpu_hz == 16_000_000
    assert spec.loop_period_ms == 2000


def test_a_critical_sensor_gets_retries():
    state = answered_state(**{"sensors[0].critical": "yes"})

    _, spec = normalize(state.extraction, state.answers)

    assert spec.policies["DHT22"].critical is True
    assert spec.policies["DHT22"].retry_count > 0


def test_hcsr04_pin_pair_is_split_into_trigger_and_echo():
    payload = json.loads(draft_response())
    payload["hardware"]["sensors"][0] = {
        "name": "HC-SR04", "type": "distance", "interface": "GPIO",
        "pins": None, "bus": None, "address": None, "required": True,
        "confidence": "stated",
    }
    extraction = HardwareExtractor.parse(json.dumps(payload))
    answers = {
        "sensors[0].pins": "D9 D10",
        "sensors[0].sample_period_ms": "100",
        "sensors[0].critical": "no",
        "loop_period_ms": "1000",
        "uart_baud": "9600",
    }

    analysis, _ = normalize(extraction, answers)

    assert analysis.sensors[0].pins == {"trigger": "D9", "echo": "D10"}


def test_a_non_numeric_answer_is_rejected_rather_than_coerced():
    state = answered_state(loop_period_ms="soon")

    with pytest.raises(NormalizationError, match="must be a whole number"):
        normalize(state.extraction, state.answers)


def test_an_unknown_board_cannot_be_normalized():
    extraction = ExtractionResult(
        hardware=HardwareDraft(
            mcu_name="PIC16F877A",
            sensors=[SensorDraft(name="DHT22", type="t", interface="GPIO", pins={"pin": "D2"})],
        )
    )
    answers = {
        "sensors[0].sample_period_ms": "2000", "sensors[0].critical": "no",
        "loop_period_ms": "2000", "uart_baud": "9600", "f_cpu_hz": "16000000",
        "f_cpu_source": "external crystal", "uart_wiring": "crossed",
        "supply_voltage": "5.0",
    }

    # Every question is answered, so this is not a missing-information refusal:
    # the toolchain simply cannot build for a PIC, and says which fact is absent.
    with pytest.raises(NormalizationError, match="does not know a part called"):
        normalize(extraction, answers)


def test_duplicate_i2c_addresses_are_caught_by_the_hardware_model():
    """A model that hallucinates a bus collision must not reach the generator."""
    extraction = ExtractionResult(
        hardware=HardwareDraft(
            board_name="Arduino Uno",
            sensors=[
                SensorDraft(name="A", type="imu", interface="I2C", bus="I2C1", address="0x68"),
                SensorDraft(name="B", type="imu", interface="I2C", bus="I2C1", address="0x68"),
            ],
        )
    )
    answers = {
        "sensors[0].address": "0x68", "sensors[0].sample_period_ms": "1000",
        "sensors[0].critical": "no",
        "sensors[1].address": "0x68", "sensors[1].sample_period_ms": "1000",
        "sensors[1].critical": "no",
        "loop_period_ms": "1000", "uart_baud": "9600",
    }

    with pytest.raises(HardwareValidationError, match="I2C address conflict"):
        normalize(extraction, answers)


# --- Physical feasibility -----------------------------------------------------


def test_polling_a_dht22_too_fast_is_a_conflict():
    """The datasheet floor is 2 s; faster silently returns the previous reading."""
    state = answered_state(**{"sensors[0].sample_period_ms": "500", "loop_period_ms": "500"})

    outcome = Interview.finish(state)

    assert not outcome.ok
    assert any("2000 ms" in c for c in outcome.conflicts)
    assert MIN_SAMPLE_PERIOD_MS["DHT22"] == 2000


def test_a_loop_faster_than_its_slowest_sensor_is_a_conflict():
    analysis, spec = normalize(*_answered())
    spec.loop_period_ms = 100

    conflicts = check_timing(analysis, spec)

    assert any("slowest sensor" in c for c in conflicts)


def test_a_feasible_brief_has_no_conflicts():
    outcome = Interview.finish(answered_state())

    assert outcome.ok
    assert outcome.conflicts == []


def _answered():
    state = answered_state()
    return state.extraction, state.answers


# --- The loop -----------------------------------------------------------------


def test_finishing_early_is_refused():
    state = InterviewState(extraction=HardwareExtractor.parse(draft_response()))

    with pytest.raises(NormalizationError, match="not finished"):
        Interview.finish(state)


def test_unsupported_requests_are_carried_through_to_the_outcome():
    extraction = HardwareExtractor.parse(
        draft_response(unsupported=["MQTT QoS levels are not supported"])
    )
    state = InterviewState(extraction=extraction)
    for key, value in answered_state().answers.items():
        state.answer(key, value)

    outcome = Interview.finish(state)

    assert outcome.unsupported == ["MQTT QoS levels are not supported"]


def test_the_generator_accepts_the_normalized_spec():
    """The brief must actually drive codegen — otherwise the interview is theatre."""
    from codegen.generator import generate_firmware

    outcome = Interview.finish(answered_state(uart_baud="19200", loop_period_ms="5000"))
    firmware = generate_firmware(
        outcome.analysis,
        f_cpu_hz=outcome.spec.f_cpu_hz,
        loop_period_ms=outcome.spec.loop_period_ms,
        uart_baud=outcome.spec.uart_baud,
    )

    assert "#define UART_BAUD       19200" in firmware.files["config.h"]
    assert "#define LOOP_PERIOD_MS  5000" in firmware.files["config.h"]
