"""Tests for the self-play loop.

The property under test is what the loop *claims*. It has one oracle -- the
compiler -- answering one question, and a passing trial must never be recorded
as anything more than "this builds". Whether a pin matches a real board is not
observable from inside the loop, and nothing here may imply otherwise.
"""

import os

import pytest

from codegen.zephyr.soc_facts import ZephyrSocCatalog
from datasets.synthetic import (
    CANDIDATE_PARTS,
    SyntheticCampaign,
    Trial,
    _first_error,
    soc_candidates,
    summarise,
)

requires_zephyr = pytest.mark.skipif(
    not ZephyrSocCatalog().available, reason="no Zephyr checkout (set ZEPHYR_BASE)"
)


@pytest.fixture(scope="module")
def campaign():
    return SyntheticCampaign(os.environ["ZEPHYR_BASE"], vendors=("nordic",))


# --- Sampling from things that are real -------------------------------------------


@requires_zephyr
def test_soc_profiles_come_from_board_ports_that_already_build():
    """Composing SoC descriptions by hand would only rediscover impossible ones."""
    candidates = soc_candidates(os.environ["ZEPHYR_BASE"], limit=40)

    assert candidates
    for candidate in candidates:
        assert candidate.dtsi_include.endswith(".dtsi")
        assert candidate.kconfig_soc.startswith("SOC_")


@requires_zephyr
def test_a_vendor_with_no_pinctrl_dialect_is_refused_up_front():
    """Otherwise the campaign measures the refusal rather than the generator."""
    with pytest.raises(ValueError, match="[Pp]in control is written per vendor"):
        SyntheticCampaign(os.environ["ZEPHYR_BASE"], vendors=("nobody",))


def test_every_candidate_part_names_its_interface():
    for name, kind, interface, address in CANDIDATE_PARTS:
        assert name and kind and interface
        if interface.value == "I2C":
            assert address, f"{name} is on I2C and needs an address"


# --- Composition -------------------------------------------------------------------


@requires_zephyr
def test_the_same_seed_composes_the_same_board(campaign):
    """A failure has to be reproducible from its seed alone, or it is noise."""
    first = campaign.compose(7)
    second = campaign.compose(7)

    assert first[2] == second[2]
    assert first[3] == second[3]


@requires_zephyr
def test_different_seeds_compose_different_boards(campaign):
    boards = {tuple(campaign.compose(seed)[2]) for seed in range(12)}

    assert len(boards) > 1


@requires_zephyr
def test_a_multi_pin_part_gets_all_of_its_pins(campaign):
    """An HC-SR04 needs a trigger and an echo; one would be refused later."""
    for seed in range(40):
        _soc, _analysis, parts, pins = campaign.compose(seed)
        if "HC-SR04" in parts:
            assert "HC-SR04.trigger" in pins
            assert "HC-SR04.echo" in pins
            return
    pytest.skip("no HC-SR04 sampled in this range")


@requires_zephyr
def test_the_console_pads_are_always_supplied(campaign):
    """They are blocking, so a campaign without them measures nothing else."""
    soc, _analysis, _parts, _pins = campaign.compose(3)

    assert soc.console_tx and soc.console_rx


# --- What the loop records ----------------------------------------------------------


def test_a_refusal_is_recorded_as_an_outcome_not_a_failure():
    trials = [Trial(seed=0, soc="s", vendor="v", outcome="refused", detail="no driver")]

    assert summarise(trials)["outcomes"] == {"refused": 1}
    assert summarise(trials)["refusals"]


def test_build_failures_are_reported_as_the_product():
    trials = [
        Trial(seed=i, soc="s", vendor="v", outcome="build-failed", detail="undefined label")
        for i in range(3)
    ]

    summary = summarise(trials)

    assert summary["build_failures"][0] == ("undefined label", 3)
    assert "Failures are the product" in str(summary["note"])


def test_a_pass_is_not_reported_as_more_than_it_is():
    """It builds. It is not known to match any board, and cannot be."""
    note = str(summarise([Trial(seed=0, soc="s", vendor="v", outcome="built")])["note"])

    assert "says nothing about whether any pin here matches a real board" in note


def test_the_first_real_error_is_extracted_rather_than_the_whole_log():
    log = "\n".join([
        "-- Configuring", "-- Found toolchain",
        "devicetree error: /soc/uart@40002000: undefined node label 'uart0_default'",
        "CMake Error at dts.cmake:312", "FATAL ERROR",
    ])

    assert "undefined node label" in _first_error(log)


def test_an_empty_log_does_not_produce_a_confident_diagnosis():
    assert _first_error("") == "no output"
