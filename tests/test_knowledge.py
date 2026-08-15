"""Tests for the vendor-SDK knowledge base.

Two properties matter more than the rest, and both are negative:

* Nothing in this package may reach into the Zephyr path, or be reached from
  it. The base was added so an unsupported part could be handled *without*
  putting the working path at risk, and a stray import would quietly undo that.

* No route through the emitter produces firmware while a silently-failing
  question is unanswered. Pin, active level and I2C address all yield code that
  builds and runs when guessed, so each of them refuses.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from core.evidence import EvidenceKind, cited, derived
from knowledge.acquire import SdkLead, ingest, record_lead, search_plan
from knowledge.base import KnowledgeBase
from knowledge.board import BoardFacts, Device
from knowledge.emit import EmitError, emit
from knowledge.extract import SdkNotFound, extract
from knowledge.family import HwFamily, SdkSource, Support, from_json, to_json
from knowledge.hal import OPERATIONS, candidates
from knowledge.questions import advisory, blocking, board_questions, unknown_family
from knowledge.seed import uws6121e

REPO = Path(__file__).resolve().parent.parent


# --- decoupling -------------------------------------------------------------


def test_the_knowledge_base_never_imports_the_zephyr_path():
    """The whole point of a separate base is that it cannot break the old one."""
    offenders = []
    for path in (REPO / "knowledge").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"^\s*(?:from|import)\s+(\S+)", text, re.MULTILINE):
            module = match.group(1)
            if "zephyr" in module.lower():
                offenders.append(f"{path.name}: {module}")
    assert offenders == []


def test_the_zephyr_path_never_imports_the_knowledge_base():
    """And the coupling must not appear from the other side either."""
    offenders = []
    for folder in ("codegen", "services", "core", "agents", "datasets"):
        for path in (REPO / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if re.search(r"^\s*(?:from|import)\s+knowledge", text, re.MULTILINE):
                offenders.append(str(path.relative_to(REPO)))
    assert offenders == []


# --- identity ---------------------------------------------------------------


def test_a_family_matches_the_order_codes_of_one_die():
    family = uws6121e()

    assert family.matches("UWS6121E")
    assert family.matches("UWS6121EG")
    assert family.matches("uws6121eg")


def test_a_family_does_not_match_a_different_part():
    family = uws6121e()

    assert not family.matches("UWS6122E")
    assert not family.matches("nrf52840")
    assert not family.matches("")


def test_a_record_without_an_sdk_is_partial_not_broken():
    """'I know this part and cannot port it yet' is a useful state."""
    assert uws6121e().support is Support.PARTIAL


def test_a_record_with_an_ingested_sdk_is_ready(tmp_path):
    family = uws6121e()
    family.sdk = SdkSource(name="x", local_path=str(tmp_path))
    family.symbols = extract(_fake_sdk(tmp_path)).symbols

    assert family.support is Support.READY


# --- evidence ---------------------------------------------------------------


def test_the_seeded_facts_are_cited_and_never_authoritative():
    """A product spec says what the product should contain, not what the die is."""
    family = uws6121e()

    assert family.facts
    for claim in family.facts.values():
        assert claim.evidence.kind is EvidenceKind.CITED
        assert claim.evidence.retrieved is not None


def test_a_weaker_account_does_not_overwrite_a_stronger_one():
    family = uws6121e()
    family.record("cpu_clock_hz", 999, derived("SDK", "soc.h:12"))

    family.record("cpu_clock_hz", 1, cited("a forum", "someone said", date.today()))

    assert family.facts["cpu_clock_hz"].value == 999


def test_facts_nothing_backs_are_listed_for_a_human():
    assert uws6121e().unsupported()


def test_a_record_says_what_it_is_missing():
    gaps = {gap.field for gap in uws6121e().gaps()}

    assert "sdk.local_path" in gaps
    assert "pin_syntax" in gaps


def test_the_missing_sdk_is_what_blocks_the_port():
    blockers = [gap.field for gap in uws6121e().gaps() if gap.blocks_port]

    assert blockers == ["sdk.local_path"]


# --- storage ----------------------------------------------------------------


def test_a_record_survives_a_round_trip(tmp_path):
    kb = KnowledgeBase(tmp_path)
    kb.put(uws6121e())

    back = kb.get("UWS6121E")

    assert back is not None
    assert back.cpu == "Cortex-A5"
    assert back.facts["memory"].evidence.kind is EvidenceKind.CITED
    assert back.facts["memory"].evidence.retrieved == date(2026, 8, 15)


def test_json_is_lossless_for_evidence():
    original = uws6121e()

    restored = from_json(to_json(original))

    for predicate, claim in original.facts.items():
        assert restored.facts[predicate].evidence.describe() == claim.evidence.describe()


def test_resolving_an_unknown_part_returns_none_rather_than_a_near_miss(tmp_path):
    """A fuzzy match to the wrong family is the failure this base exists to avoid."""
    kb = KnowledgeBase(tmp_path)
    kb.put(uws6121e())

    assert kb.resolve("UWS6121EG") is not None
    assert kb.resolve("STM32F103") is None


def test_a_half_written_record_does_not_replace_a_good_one(tmp_path):
    kb = KnowledgeBase(tmp_path)
    kb.put(uws6121e())
    before = (tmp_path / "uws6121e.json").read_text(encoding="utf-8")

    with pytest.raises(Exception):
        kb.put(HwFamily(family_id=None))  # type: ignore[arg-type]

    assert (tmp_path / "uws6121e.json").read_text(encoding="utf-8") == before


# --- extraction -------------------------------------------------------------


def _fake_sdk(root: Path) -> Path:
    """A tiny SDK tree: two public headers, plus noise that must be ignored."""
    (root / "inc").mkdir(parents=True, exist_ok=True)
    (root / "inc" / "gpio.h").write_text(
        "/* comment with void fake_decl(void); inside */\n"
        "int sdk_gpio_set_direction(int pin, int dir);\n"
        "void sdk_gpio_output_set(int pin, int level);\n"
        "int sdk_gpio_input_get(int pin);\n"
        "typedef void (*gpio_cb_t)(int pin);\n",
        encoding="utf-8",
    )
    (root / "inc" / "uart.h").write_text(
        "#define UART0 0\n#define UART1 1\n"
        "int sdk_uart_init(int port, unsigned baud);\n"
        "int sdk_uart_write(int port, const char *buf, int len);\n",
        encoding="utf-8",
    )
    (root / "examples").mkdir(exist_ok=True)
    (root / "examples" / "blink.h").write_text(
        "void example_only_function(void);\n", encoding="utf-8"
    )
    return root


def test_extraction_finds_the_declared_functions(tmp_path):
    found = extract(_fake_sdk(tmp_path))

    names = {s.name for s in found.symbols}
    assert "sdk_gpio_output_set" in names
    assert "sdk_uart_init" in names


def test_extraction_ignores_declarations_inside_comments(tmp_path):
    names = {s.name for s in extract(_fake_sdk(tmp_path)).symbols}

    assert "fake_decl" not in names


def test_extraction_ignores_examples(tmp_path):
    """A function in a sample is not SDK API, and cataloguing it invites a call."""
    names = {s.name for s in extract(_fake_sdk(tmp_path)).symbols}

    assert "example_only_function" not in names


def test_every_symbol_carries_the_header_and_line_it_came_from(tmp_path):
    found = extract(_fake_sdk(tmp_path))

    evidence = found.evidence_for("sdk_uart_init", "test SDK")
    assert evidence is not None
    assert evidence.kind is EvidenceKind.AUTHORITATIVE
    assert re.match(r"inc/uart\.h:\d+", evidence.locator)


def test_extraction_counts_peripheral_instances(tmp_path):
    found = extract(_fake_sdk(tmp_path))

    uart = next(b for b in found.peripherals if b.kind == "uart")
    assert uart.count == 2


def test_a_path_that_is_not_an_sdk_says_so(tmp_path):
    empty = tmp_path / "nothing"
    empty.mkdir()

    with pytest.raises(SdkNotFound):
        extract(empty)


def test_ingesting_promotes_a_record_to_ready(tmp_path):
    kb = KnowledgeBase(tmp_path / "kb")
    kb.put(uws6121e())

    family = ingest(kb, "UWS6121E", _fake_sdk(tmp_path / "sdk"), name="Test SDK", version="1.0")

    assert family.support is Support.READY
    assert family.facts["sdk_symbol_count"].authoritative


def test_ingesting_an_unknown_family_is_refused(tmp_path):
    kb = KnowledgeBase(tmp_path / "kb")

    with pytest.raises(LookupError):
        ingest(kb, "NOPE", _fake_sdk(tmp_path / "sdk"))


# --- acquisition ------------------------------------------------------------


def test_a_search_plan_covers_a_broader_stem_than_the_order_code():
    """Vendors document a family and sell order codes with packaging suffixes.

    Searching only the code on the label misses the documentation, so the plan
    carries a stem with the trailing letters removed as well.
    """
    queries = search_plan("UWS6121EG", "UNISOC").queries

    assert any(q.startswith("UWS6121EG ") for q in queries)
    assert any(q.startswith("UWS6121 ") for q in queries)


def test_a_search_plan_names_the_vendor_it_was_given():
    assert any("UNISOC" in q for q in search_plan("UWS6121EG", "UNISOC").queries)


def test_a_lead_is_cited_and_does_not_promote_support(tmp_path):
    """Finding a download page is not the same as having the SDK."""
    kb = KnowledgeBase(tmp_path)
    kb.put(uws6121e())

    family = record_lead(kb, "UWS6121E", SdkLead(
        url="https://example.invalid/sdk",
        title="UWS6121E SDK download",
        why="vendor page listing an SDK archive",
        retrieved=date(2026, 8, 15),
    ))

    assert family.support is Support.PARTIAL
    assert not family.symbols


# --- questions --------------------------------------------------------------


def test_an_unknown_part_becomes_questions_not_a_dead_end():
    fields = {u.field for u in unknown_family("UWS6121EG")}

    assert "family.vendor" in fields
    assert "family.sdk_path" in fields


def test_a_gpio_device_is_asked_its_pin_and_active_level():
    board = BoardFacts(board_name="b", mcu="UWS6121EG",
                       devices=[Device(name="red", kind="led", interface="gpio")])

    fields = {u.field for u in blocking(board_questions(board))}

    assert "devices[0].pins.out" in fields
    assert "devices[0].active_level" in fields


def test_a_button_is_asked_about_its_pull_resistor():
    board = BoardFacts(board_name="b", mcu="x",
                       devices=[Device(name="home", kind="button", interface="gpio")])

    assert "devices[0].pull" in {u.field for u in blocking(board_questions(board))}


def test_an_i2c_device_is_asked_its_address_because_the_part_number_does_not_settle_it():
    board = BoardFacts(board_name="b", mcu="x",
                       devices=[Device(name="da267", kind="imu", interface="i2c")])

    assert "devices[0].address" in {u.field for u in blocking(board_questions(board))}


def test_baud_is_advisory_because_a_wrong_one_fails_loudly():
    board = BoardFacts(board_name="b", mcu="x",
                       devices=[Device(name="gps", kind="gnss", interface="uart", bus="UART1")])

    fields = {u.field for u in advisory(board_questions(board))}
    assert "devices[0].baud" in fields


def test_no_blocking_question_carries_a_default():
    """A default is precisely how the guess gets in."""
    board = BoardFacts(board_name="b", mcu="x", devices=[
        Device(name="red", kind="led", interface="gpio"),
        Device(name="home", kind="button", interface="gpio"),
        Device(name="da267", kind="imu", interface="i2c"),
    ])

    for question in blocking(board_questions(board)):
        assert question.default is None


def test_every_question_says_how_a_wrong_answer_fails():
    board = BoardFacts(board_name="b", mcu="x",
                       devices=[Device(name="red", kind="led", interface="gpio")])

    for question in board_questions(board):
        assert "If it is wrong:" in question.why
        assert question.failure


# --- emission ---------------------------------------------------------------


def _answered_board() -> BoardFacts:
    return BoardFacts(
        board_name="T106 Pet Locator",
        mcu="UWS6121EG",
        intent="Report position over the network and show state on a tri-colour LED.",
        loop_ms=1000,
        devices=[
            Device(name="led red", kind="led", interface="gpio",
                   pins={"out": "GPIO_12"}, active_level="active low", role="power / battery"),
            Device(name="led blue", kind="led", interface="gpio",
                   pins={"out": "GPIO_13"}, active_level="active low", role="gps fix"),
            Device(name="led green", kind="led", interface="gpio",
                   pins={"out": "GPIO_14"}, active_level="active low", role="network"),
            Device(name="home key", kind="button", interface="gpio",
                   pins={"in": "GPIO_5"}, active_level="active low", pull="pull-up"),
            Device(name="ag3335a", kind="gnss", interface="uart",
                   bus="UART1", baud=9600, role="gps fix"),
            Device(name="da267", kind="imu", interface="i2c",
                   bus="I2C0", address="0x26", pins={"int": "GPIO_7"}, role="motion wake"),
        ],
    )


def test_generation_is_refused_while_a_silent_failure_is_unanswered():
    board = _answered_board()
    board.devices[0].active_level = ""

    with pytest.raises(EmitError, match="active_level"):
        emit(board, uws6121e())


def test_two_devices_at_one_address_is_refused():
    board = _answered_board()
    board.devices.append(Device(name="other", kind="sensor", interface="i2c",
                                bus="I2C0", address="0x26"))

    with pytest.raises(EmitError, match="0x26"):
        emit(board, uws6121e())


def test_a_pin_in_another_families_notation_is_refused():
    family = uws6121e()
    family.pin_syntax = "GPIO_12"
    board = _answered_board()
    board.devices[0].pins["out"] = "P0.13"

    with pytest.raises(EmitError, match="P0.13"):
        emit(board, family)


def test_a_fully_answered_board_emits_a_project():
    project = emit(_answered_board(), uws6121e())

    assert "port/hal.h" in project.files
    assert "port/hal_uws6121e.c" in project.files
    assert "app/main.c" in project.files
    assert "app/app_config.h" in project.files


def test_the_hal_declares_every_operation_the_port_must_supply():
    header = emit(_answered_board(), uws6121e()).files["port/hal.h"]

    for operation in OPERATIONS:
        assert operation.signature in header


def test_the_application_is_emitted_even_with_no_sdk():
    """The logic does not depend on the vendor, so it is never withheld."""
    project = emit(_answered_board(), uws6121e())

    assert "led_tick" in project.files["app/led.c"]
    assert "button_tick" in project.files["app/button.c"]
    assert "checksum_ok" in project.files["app/gnss.c"]


def test_the_port_refuses_to_build_until_it_is_finished():
    port = emit(_answered_board(), uws6121e()).files["port/hal_uws6121e.c"]

    assert "#  error" in port
    assert "HAL_PORT_INCOMPLETE_OK" in port


def test_every_unported_operation_is_listed_for_review():
    project = emit(_answered_board(), uws6121e())

    assert len(project.review) == len(OPERATIONS)


def test_active_level_is_applied_in_exactly_one_place():
    """So the rest of the firmware can reason in terms of on and off."""
    led = emit(_answered_board(), uws6121e()).files["app/led.c"]

    assert led.count("s_active_high[i] ?") == 1


def test_the_i2c_address_reaches_the_generated_code():
    config = emit(_answered_board(), uws6121e()).files["app/app_config.h"]

    assert "DA267_ADDR         0x26" in config


def test_the_provenance_separates_answers_from_artifacts():
    text = emit(_answered_board(), uws6121e()).files["PROVENANCE.md"]

    assert "Answered by a human, and unverifiable by anything here" in text
    assert "Derived from a versioned artifact" in text
    assert "GPIO_12" in text


def test_the_provenance_says_it_was_never_compiled_or_run():
    text = emit(_answered_board(), uws6121e()).files["PROVENANCE.md"]

    assert "has not been compiled" in text
    assert "has not been on hardware" in text


def test_a_single_candidate_is_flagged_rather_than_trusted(tmp_path):
    """The symbol existing is evidence. It being the right one is not."""
    kb = KnowledgeBase(tmp_path / "kb")
    kb.put(uws6121e())
    family = ingest(kb, "UWS6121E", _fake_sdk(tmp_path / "sdk"), name="Test SDK")

    port = emit(_answered_board(), family).files["port/hal_uws6121e.c"]

    assert "REVIEW:" in port
    assert "has NOT been checked" in port


def test_candidate_matching_narrows_but_does_not_decide():
    names = ["sdk_gpio_output_set", "sdk_gpio_input_get", "unrelated_thing"]
    write = next(op for op in OPERATIONS if op.name == "hal_gpio_write")

    found = candidates(write, names)

    assert "sdk_gpio_output_set" in found
    assert "unrelated_thing" not in found


# --- flashing ---------------------------------------------------------------


def test_nothing_here_flashes_and_no_flashing_tool_is_required():
    project = emit(_answered_board(), uws6121e())

    text = project.files["FLASHING.md"]
    assert "does not flash anything" in text
    assert "An engineer flashes this image" in text
    for name, content in project.files.items():
        assert "subprocess" not in content, name


def test_the_flashing_notes_say_what_a_successful_flash_does_not_prove():
    text = emit(_answered_board(), uws6121e()).files["FLASHING.md"]

    assert "says nothing about whether the pins in this firmware match" in text


# --- the compiler as the oracle ---------------------------------------------
# The application half of the project is vendor-independent, which is a claim
# worth checking rather than asserting. These tests compile it for real.


def _compiler() -> str | None:
    """A cross compiler, if this machine has one. Any ARM gcc will do."""
    for name in ("arm-zephyr-eabi-gcc", "arm-none-eabi-gcc", "gcc"):
        found = shutil.which(name)
        if found:
            return found

    roots = [os.environ.get("ZEPHYR_SDK_INSTALL_DIR", "")]
    roots += [str(p) for p in Path.home().glob("zephyr-sdk-*")]
    for root in roots:
        if not root:
            continue
        for candidate in Path(root).rglob("arm-zephyr-eabi-gcc*"):
            if candidate.is_file() and candidate.suffix.lower() in ("", ".exe"):
                return str(candidate)
    return None


requires_cc = pytest.mark.skipif(_compiler() is None, reason="no C cross compiler on this machine")


def _write_project(root: Path) -> Path:
    for relative, content in emit(_answered_board(), uws6121e()).files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


@requires_cc
def test_the_emitted_application_compiles_cleanly(tmp_path):
    """Vendor-independent is a claim about code, so compile the code.

    Warnings are errors here on purpose: an engineer building this for real
    will have them on, and fourteen stubs' worth of noise would bury anything
    that mattered.
    """
    root = _write_project(tmp_path)
    sources = sorted(str(p) for p in (root / "app").glob("*.c"))
    sources.append(str(next((root / "port").glob("hal_*.c"))))

    result = subprocess.run(
        [_compiler(), "-c", "-std=c99", "-Wall", "-Wextra", "-Werror",
         "-DHAL_PORT_INCOMPLETE_OK", *sources],
        cwd=root, capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr


@requires_cc
def test_an_unfinished_port_refuses_to_build(tmp_path):
    """Without the opt-in, the stub file must stop the build rather than link."""
    root = _write_project(tmp_path)
    port = next((root / "port").glob("hal_*.c"))

    result = subprocess.run(
        [_compiler(), "-c", "-std=c99", str(port), "-o", os.devnull],
        cwd=root, capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert "is not finished" in result.stderr


@requires_cc
def test_the_sdk_definition_of_a_pin_wins_over_the_placeholder(tmp_path):
    """The placeholders exist only until a real header defines the constant."""
    root = _write_project(tmp_path)
    (root / "sdk_pins.h").write_text("#define GPIO_12 0xAB\n", encoding="utf-8")
    probe = root / "probe.c"
    probe.write_text(
        "#include \"sdk_pins.h\"\n"
        "#include \"app/app_config.h\"\n"
        "/* A cast cannot appear in a #if, so the constant itself is compared. */\n"
        "#if GPIO_12 != 0xAB\n"
        "#  error the placeholder overrode the SDK's own definition\n"
        "#endif\n"
        "int probe(void) { return 0; }\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [_compiler(), "-c", "-std=c99", str(probe), "-o", os.devnull],
        cwd=root, capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
