"""Tests for what the pipeline admits it does not know.

The property under test throughout: a value whose wrongness would be *invisible*
is never defaulted. It is asked, and if nobody answers, nothing is generated.
"""

import pytest

from agents.normalizer import (
    assumed_defaults,
    blocking_questions,
    contention,
    required_questions,
    unsupported,
)
from agents.schemas import HardwareDraft, SensorDraft
from agents.uncertainty import Uncertainty, blocking, scan_draft
from agents.uncertainty import contention as contention_for


def draft(**overrides) -> HardwareDraft:
    payload = {"board_name": None, "mcu_name": "ATmega328P", "sensors": []}
    payload.update(overrides)
    return HardwareDraft(**payload)


def fields(items) -> set[str]:
    return {i.field for i in items}


# --- The clock, which is the most damaging thing to assume ---------------------


def test_a_bare_chip_is_asked_what_clocks_it():
    """An ATmega328P with factory fuses runs at 1 MHz, not 16."""
    found = scan_draft(draft())

    assert "f_cpu_source" in fields(found)
    assert "f_cpu_hz" in fields(found)


def test_the_clock_questions_have_no_default():
    for item in scan_draft(draft()):
        if item.field in {"f_cpu_source", "f_cpu_hz"}:
            assert item.blocking
            assert item.to_question().default is None


def test_naming_a_known_board_settles_the_clock():
    """Saying 'Arduino Uno' is a fact about a crystal, not an assumption."""
    asked = fields(required_questions(HardwareDraft(board_name="Arduino Uno")))

    assert "f_cpu_hz" not in asked
    assert "f_cpu_source" not in asked


# --- Serial wiring, the user's own example ------------------------------------


def test_tx_rx_orientation_is_asked_and_cannot_be_defaulted():
    found = {u.field: u for u in scan_draft(draft())}

    assert "uart_wiring" in found
    assert found["uart_wiring"].blocking
    assert "transmits into a transmitter" in found["uart_wiring"].failure


def test_a_part_with_several_usarts_is_asked_which_one_is_wired():
    found = fields(scan_draft(draft(mcu_name="ATmega2560"), usart_count=4))

    assert "usart_index" in found


def test_a_part_with_one_usart_is_not_asked_which_one():
    assert "usart_index" not in fields(scan_draft(draft(), usart_count=1))


def test_the_baud_rate_may_default_because_it_fails_visibly():
    found = {u.field: u for u in scan_draft(draft())}

    assert not found["uart_baud"].blocking
    assert found["uart_baud"].default == "9600"


# --- Analog, the purest silent failure ----------------------------------------


def analog_draft() -> HardwareDraft:
    return draft(sensors=[SensorDraft(
        name="TMP36", type="temperature", interface="ADC", pins={"pin": "PC0"}
    )])


def test_an_analog_sensor_is_asked_what_it_is_referenced_against():
    found = {u.field: u for u in scan_draft(analog_draft())}

    assert found["sensors[0].adc_reference"].blocking
    assert "stable, plausible, and wrong" in found["sensors[0].adc_reference"].failure


def test_an_analog_sensor_is_asked_about_a_divider():
    found = {u.field: u for u in scan_draft(analog_draft())}

    assert found["sensors[0].divider"].blocking


def test_an_analog_sensor_makes_the_supply_voltage_blocking():
    """It sets ADC full scale, so guessing it scales every reading."""
    found = {u.field: u for u in scan_draft(analog_draft())}

    assert found["supply_voltage"].blocking


def test_without_an_analog_sensor_the_supply_voltage_may_default():
    found = {u.field: u for u in scan_draft(draft())}

    assert not found["supply_voltage"].blocking


# --- I2C addressing ------------------------------------------------------------


def i2c_draft(address=None, name="SHT31") -> HardwareDraft:
    return draft(sensors=[SensorDraft(
        name=name, type="temperature", interface="I2C", bus="I2C1", address=address
    )])


def test_a_missing_i2c_address_is_blocking():
    found = {u.field: u for u in scan_draft(i2c_draft())}

    assert found["sensors[0].address"].blocking


def test_the_question_names_the_pin_that_selects_the_address():
    found = {u.field: u for u in scan_draft(i2c_draft())}

    assert "ADDR pin" in found["sensors[0].address"].question
    assert "0x44" in found["sensors[0].address"].question


def test_a_stated_address_on_a_selectable_part_is_still_confirmed():
    """Knowing the part is not knowing the address -- a pin decides it."""
    found = fields(scan_draft(i2c_draft(address="0x44")))

    assert "sensors[0].address_confirmed" in found


def test_a_part_with_a_fixed_address_is_not_second_guessed():
    found = fields(scan_draft(i2c_draft(address="0x40", name="HDC1080")))

    assert "sensors[0].address_confirmed" not in found


def test_pullups_are_asked_but_may_default_because_they_fail_loudly():
    found = {u.field: u for u in scan_draft(i2c_draft(address="0x40", name="HDC1080"))}

    assert not found["sensors[0].pullups"].blocking


# --- Pins ----------------------------------------------------------------------


def test_a_digital_sensor_with_no_pin_is_blocking():
    found = {u.field: u for u in scan_draft(draft(sensors=[
        SensorDraft(name="DHT22", type="temperature", interface="GPIO")
    ]))}

    assert found["sensors[0].pins"].blocking
    assert "floating input" in found["sensors[0].pins"].failure


def test_an_unknown_interface_is_asked_before_anything_else_about_that_sensor():
    found = fields(scan_draft(draft(sensors=[
        SensorDraft(name="Mystery", type="unknown", interface="")
    ])))

    assert "sensors[0].interface" in found
    # Nothing downstream of it is asked yet: which pins matter depends on it.
    assert "sensors[0].pins" not in found


# --- Ordering and classification -----------------------------------------------


def test_blocking_questions_come_first():
    found = scan_draft(analog_draft())
    first_advisory = next(i for i, u in enumerate(found) if not u.blocking)

    assert all(u.blocking for u in found[:first_advisory])


def test_every_blocking_item_refuses_to_offer_a_default():
    """Offering a default for something unguessable is how the guess sneaks in."""
    for item in blocking(scan_draft(analog_draft())):
        assert item.to_question().default is None


def test_every_uncertainty_says_how_it_fails():
    for item in scan_draft(analog_draft()):
        assert item.failure.strip(), f"{item.field} does not say what a wrong answer does"
        assert item.why.strip()


def test_the_question_text_carries_the_consequence_to_the_user():
    question = Uncertainty(
        field="x", question="Which pin?", why="Because.",
        failure="the sensor is never read", blocking=True,
    ).to_question()

    assert "the sensor is never read" in question.why


# --- What reaches the rest of the pipeline -------------------------------------


def test_an_answered_board_has_nothing_blocking_left():
    answers = {
        "f_cpu_source": "external crystal", "f_cpu_hz": "16000000",
        "supply_voltage": "5.0", "uart_wiring": "crossed",
        "sensors[0].adc_reference": "AVcc (the supply)",
        "sensors[0].divider": "no divider",
    }

    assert blocking_questions(analog_draft(), answers) == []


def test_defaults_that_will_be_used_are_stated_rather_than_applied_quietly():
    stated = assumed_defaults(draft())

    assert any(s.startswith("uart_baud = 9600") for s in stated)
    assert any(s.startswith("loop_period_ms = 2000") for s in stated)


@pytest.mark.parametrize("field_name", ["f_cpu_source", "f_cpu_hz", "uart_wiring"])
def test_answering_a_question_stops_it_being_asked(field_name):
    answers = {field_name: "whatever the board actually is"}

    assert field_name not in fields(scan_draft(draft(), answers))


# --- Things no answer can fix --------------------------------------------------


def serial_gps_draft(mcu="ATmega328P") -> HardwareDraft:
    return draft(mcu_name=mcu, sensors=[
        SensorDraft(name="NEO-6M", type="gps", interface="UART"),
    ])


def test_a_serial_sensor_on_a_single_usart_part_is_a_conflict_not_a_question():
    """The GPS and the debug output want the same hardware block."""
    problems = contention_for(serial_gps_draft(), usart_count=1)

    assert problems
    assert "2 serial ports are needed" in problems[0]


def test_the_same_design_on_a_part_with_four_usarts_is_fine():
    assert contention_for(serial_gps_draft("ATmega2560"), usart_count=4) == []


def test_two_devices_at_one_address_is_reported_as_unfixable_by_answering():
    problems = contention(draft(sensors=[
        SensorDraft(name="BMP280", type="pressure", interface="I2C", bus="I2C1", address="0x76"),
        SensorDraft(name="BME280", type="pressure", interface="I2C", bus="I2C1", address="0x76"),
    ]))

    assert any("both at 0x76" in p for p in problems)


def test_an_unsupported_mcu_is_reported_before_any_question_is_asked():
    d = draft(mcu_name="RTL8720", sensors=[
        SensorDraft(name="DHT22", type="temperature", interface="GPIO"),
    ])

    assert unsupported(d)
    assert "no backend for other architectures" in unsupported(d)[0]


def test_a_supported_part_is_not_flagged_unsupported():
    assert unsupported(draft(mcu_name="ATmega2560")) == []


# --- Switches ------------------------------------------------------------------


def button_draft(name="Button") -> HardwareDraft:
    return draft(sensors=[SensorDraft(
        name=name, type="user_input", interface="GPIO", pins={"pin": "PD2"}
    )])


def test_a_button_is_asked_which_way_it_is_wired():
    found = {u.field: u for u in scan_draft(button_draft())}

    assert found["sensors[0].active_level"].blocking
    assert "read inverted" in found["sensors[0].active_level"].failure


def test_a_button_is_asked_about_its_pull_resistor():
    found = {u.field: u for u in scan_draft(button_draft())}

    assert found["sensors[0].pull_resistor"].blocking
    assert "phantom presses" in found["sensors[0].pull_resistor"].failure


def test_debounce_may_default_because_it_is_visible_in_behaviour():
    found = {u.field: u for u in scan_draft(button_draft())}

    assert not found["sensors[0].debounce_ms"].blocking
    assert found["sensors[0].debounce_ms"].default == "20"


@pytest.mark.parametrize("name", ["Button", "Pulsador", "Boton", "reed switch", "PIR"])
def test_inputs_are_recognised_in_either_language(name):
    assert "sensors[0].active_level" in fields(scan_draft(button_draft(name)))


def test_a_dht22_is_not_treated_as_a_switch():
    """It is on a GPIO pin too, but it is not a contact."""
    found = fields(scan_draft(draft(sensors=[
        SensorDraft(name="DHT22", type="temperature", interface="GPIO", pins={"pin": "PD2"})
    ])))

    assert "sensors[0].active_level" not in found
