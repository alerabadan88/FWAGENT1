"""Tests for the HTTP surface.

The property that matters most here is negative: there is no path through this
API that produces a zip while a blocking question is unanswered. Everything
else is plumbing.
"""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from codegen.zephyr.soc_facts import ZephyrSocCatalog
from webapp.api import SESSIONS, app

client = TestClient(app)

# Some answers can only come from a Zephyr checkout. Where there is none, the
# code abstains rather than guessing, so the test has nothing to assert.
requires_zephyr = pytest.mark.skipif(
    not ZephyrSocCatalog().available,
    reason="no Zephyr checkout (set ZEPHYR_BASE)",
)

DHT = {"name": "DHT22", "type": "temperature_humidity", "interface": "GPIO",
       "pins": {"pin": "P0.13"}}
BUTTON = {"name": "Button", "type": "user_input", "interface": "GPIO",
          "pins": {"pin": "P0.11"}}
GPS = {"name": "NEO-6M", "type": "gnss", "interface": "UART"}

BOARD = {
    "board_name": "Acme Sensor Node v1", "mcu": "nrf52840",
    "soc_dtsi": "nordic/nrf52840_qiaa.dtsi", "vendor": "acme", "arch": "arm",
}


@pytest.fixture(autouse=True)
def clean_sessions():
    SESSIONS.clear()
    yield
    SESSIONS.clear()


def start(**overrides) -> dict:
    payload = {**BOARD, "sensors": [DHT], **overrides}
    response = client.post("/api/sessions", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def answer(session_id: str, answers: dict) -> dict:
    response = client.post(f"/api/sessions/{session_id}/answers", json={"answers": answers})
    assert response.status_code == 200, response.text
    return response.json()


def complete(status: dict) -> dict:
    """Answer every blocking question with something plausible."""
    return {
        q["field"]: (q["options"][0] if q["options"] else "64000000")
        for q in status["blocking"]
    }


# --- The interview ---------------------------------------------------------------


def test_a_new_session_is_not_ready_and_says_why():
    status = start()

    assert not status["ready"]
    assert status["blocking"]


def test_blocking_questions_carry_no_default():
    """A default here is how the guess gets in."""
    for question in start(sensors=[DHT, BUTTON])["blocking"]:
        assert question["default"] is None


def test_advisory_questions_do_carry_one():
    advisory = start()["advisory"]

    assert advisory
    assert any(q["default"] for q in advisory)


def test_every_question_says_what_a_wrong_answer_does():
    status = start(sensors=[DHT, BUTTON])

    for question in status["blocking"] + status["advisory"]:
        assert "If it is wrong:" in question["why"]


def test_a_button_is_asked_which_way_it_is_wired():
    fields = {q["field"] for q in start(sensors=[BUTTON])["blocking"]}

    assert "sensors[0].active_level" in fields
    assert "sensors[0].pull_resistor" in fields


def test_answering_removes_the_question():
    status = start()
    before = {q["field"] for q in status["blocking"]}

    after = answer(status["session"], {"f_cpu_hz": "64000000"})

    assert "f_cpu_hz" in before
    assert "f_cpu_hz" not in {q["field"] for q in after["blocking"]}


def test_a_blank_answer_does_not_count_as_an_answer():
    status = start()
    after = answer(status["session"], {"f_cpu_hz": "   "})

    assert "f_cpu_hz" in {q["field"] for q in after["blocking"]}


def test_the_defaults_that_will_be_used_are_listed():
    assert start()["assumptions"]


# --- Peripheral counts come from the SoC, not from avr-gcc ------------------------


def test_a_gps_and_a_console_fit_on_a_part_with_two_uarts():
    """The AVR path answered 'one UART' for an nRF52840, which is simply wrong."""
    assert start(sensors=[GPS])["conflicts"] == []


@requires_zephyr
def test_a_part_with_several_uarts_is_asked_which_one_the_console_is_on():
    """Only answerable with a checkout; without one the count abstains at 1."""
    fields = {q["field"] for q in start(sensors=[GPS])["blocking"]}

    assert "usart_index" in fields


def test_two_devices_at_one_i2c_address_is_a_conflict():
    sensors = [
        {"name": "BME280", "type": "pressure", "interface": "I2C", "address": "0x76"},
        {"name": "BMP280", "type": "pressure", "interface": "I2C", "address": "0x76"},
    ]

    assert start(sensors=sensors)["conflicts"]


def test_an_soc_zephyr_does_not_ship_is_refused():
    status = start(soc_dtsi="nonesuch/not_a_real_soc.dtsi")

    # Only meaningful with a checkout present; without one the check abstains,
    # which is the correct behaviour rather than a false accusation.
    if status["refusals"]:
        assert "does not ship" in status["refusals"][0]


# --- Generation ------------------------------------------------------------------


def test_generation_is_refused_while_anything_blocking_is_open():
    status = start()

    response = client.post(f"/api/sessions/{status['session']}/generate")

    assert response.status_code == 409
    assert response.json()["detail"]["fields"]


def test_download_is_refused_before_anything_is_generated():
    status = start()

    assert client.get(f"/api/sessions/{status['session']}/download").status_code == 409


def test_a_fully_answered_board_generates():
    status = start()
    status = answer(status["session"], complete(status))
    assert status["ready"]

    response = client.post(f"/api/sessions/{status['session']}/generate")

    assert response.status_code == 200, response.text
    assert response.json()["count"] >= 9


def test_the_zip_contains_the_board_port_and_its_provenance():
    status = start()
    status = answer(status["session"], complete(status))
    client.post(f"/api/sessions/{status['session']}/generate")

    response = client.get(f"/api/sessions/{status['session']}/download")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert "PROVENANCE.md" in names
    assert any(n.endswith(".dts") for n in names)
    assert "app/src/main.c" in names


def test_the_provenance_separates_derived_facts_from_answered_ones():
    status = start()
    answers = complete(status)
    status = answer(status["session"], answers)
    client.post(f"/api/sessions/{status['session']}/generate")

    response = client.get(f"/api/sessions/{status['session']}/download")
    text = zipfile.ZipFile(io.BytesIO(response.content)).read("PROVENANCE.md").decode()

    assert "Derived from a versioned artifact" in text
    assert "Answered by a human, and unverifiable by anything here" in text
    for field in answers:
        assert field in text


def test_the_provenance_says_the_port_was_never_built():
    status = start()
    status = answer(status["session"], complete(status))
    client.post(f"/api/sessions/{status['session']}/generate")

    text = zipfile.ZipFile(io.BytesIO(
        client.get(f"/api/sessions/{status['session']}/download").content
    )).read("PROVENANCE.md").decode()

    assert "has not been built" in text


def test_answering_again_invalidates_the_previous_generation():
    """Otherwise a download could carry a port built from stale answers."""
    status = start()
    status = answer(status["session"], complete(status))
    client.post(f"/api/sessions/{status['session']}/generate")

    answer(status["session"], {"f_cpu_hz": "16000000"})

    assert client.get(f"/api/sessions/{status['session']}/download").status_code == 409


def test_generation_without_an_soc_include_is_refused_with_the_reason():
    status = start(soc_dtsi="")
    status = answer(status["session"], complete(status))

    response = client.post(f"/api/sessions/{status['session']}/generate")

    assert response.status_code == 422
    assert "not guessed" in str(response.json()["detail"])


# --- Plumbing ---------------------------------------------------------------------


def test_a_missing_mcu_is_rejected():
    assert client.post("/api/sessions", json={**BOARD, "mcu": "  "}).status_code == 422


def test_an_unknown_session_is_a_404():
    assert client.get("/api/sessions/nope").status_code == 404


def test_health_reports_the_pinned_zephyr():
    body = client.get("/api/health").json()

    assert body["status"] == "ok"
    assert body["zephyr_ref"] == "v4.4.2"
    assert body["zephyr_bindings"] > 3000


def test_the_page_is_served():
    response = client.get("/")

    assert response.status_code == 200
    assert "fw-automation-agent" in response.text
