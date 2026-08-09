"""Tests for the corpus extracted from Zephyr's own board ports.

The property that matters: a prior carries its support, and a prior with weak
support is labelled an anecdote rather than offered as a suggestion. That is
not a nicety. Extracting `dio-gpios` flags over the whole tree finds six
samples, none of them from a DHT sensor, and a naive prior would have
suggested their flags for one.
"""

import pytest

from datasets.zephyr_corpus import (
    MIN_SUPPORT,
    BoardRecord,
    ZephyrCorpus,
    confidence,
    priors,
)
from codegen.zephyr.soc_facts import ZephyrSocCatalog

requires_zephyr = pytest.mark.skipif(
    not ZephyrSocCatalog().available, reason="no Zephyr checkout (set ZEPHYR_BASE)"
)


def record(**overrides) -> BoardRecord:
    payload = {"board": "b", "vendor": "v", "source": "zephyr@v4.4.2 boards/v/b"}
    payload.update(overrides)
    return BoardRecord(**payload)


# --- Support is what separates a prior from an anecdote --------------------------


def test_a_pattern_with_thin_support_is_labelled_an_anecdote():
    assert confidence(6, 6) == "anecdote"
    assert confidence(MIN_SUPPORT - 1, 1000) == "anecdote"


def test_a_pattern_almost_everything_does_is_named_as_such():
    assert confidence(800, 810) == "near-universal"


def test_a_common_but_not_universal_pattern_says_so():
    assert confidence(500, 900) == "common"


def test_a_minority_pattern_is_not_dressed_up():
    assert confidence(300, 900) == "one option among several"


def test_thinly_supported_properties_are_left_out_of_the_priors():
    records = [record(gpio_flags={"rare-gpios": ["GPIO_ACTIVE_HIGH"]}) for _ in range(3)]

    assert "rare-gpios" not in priors(records)["gpio_flags"]


def test_a_well_supported_property_is_included_with_its_count():
    records = [record(gpio_flags={"gpios": ["GPIO_ACTIVE_LOW"]}) for _ in range(MIN_SUPPORT)]

    entry = priors(records)["gpio_flags"]["gpios"][0]

    assert entry["boards"] == MIN_SUPPORT
    assert entry["confidence"] == "near-universal"


def test_every_prior_carries_its_support():
    records = [record(console_speed=115200) for _ in range(50)]

    for entry in priors(records)["console_speed"]:
        assert "boards" in entry and "confidence" in entry


def test_the_note_says_a_prior_cannot_answer_a_blocking_question():
    text = str(priors([record(console_speed=115200)])["note"])

    assert "may never satisfy a blocking one" in text


# --- Against the real tree ---------------------------------------------------------


@pytest.fixture(scope="module")
def corpus():
    import os

    return ZephyrCorpus(os.environ["ZEPHYR_BASE"])


@requires_zephyr
def test_the_tree_yields_hundreds_of_aligned_board_ports(corpus):
    """The whole argument for this source over scraped maker projects."""
    records = corpus.extract()

    assert len(records) > 500
    assert all(r.board and r.vendor for r in records)


@requires_zephyr
def test_the_corpus_records_where_it_came_from(corpus):
    assert corpus.source == "zephyrproject-rtos/zephyr@v4.4.2"
    assert "@" in corpus.extract(limit=1)[0].source


@requires_zephyr
def test_real_boards_overwhelmingly_use_115200(corpus):
    """The evidence that changed the uart_baud default from 9600."""
    speeds = priors(corpus.extract())["console_speed"]

    assert speeds[0]["value"] == 115200
    assert speeds[0]["confidence"] == "near-universal"


@requires_zephyr
def test_a_part_absent_from_the_tree_gets_no_prior_from_a_different_part(corpus):
    """No board in Zephyr carries an aosong,dht. That must stay visible."""
    records = corpus.extract()

    assert not any("aosong,dht" in r.compatibles for r in records)
    # The dio-gpios samples that do exist belong to other parts and are too few
    # to be offered for anything.
    assert "dio-gpios" not in priors(records)["gpio_flags"]


@requires_zephyr
def test_extraction_needs_a_checkout_and_says_so_when_absent(tmp_path):
    with pytest.raises(FileNotFoundError, match="needs a Zephyr checkout"):
        ZephyrCorpus(tmp_path)
