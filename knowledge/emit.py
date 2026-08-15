"""Turn board facts plus a family record into a C project an engineer can flash.

The split this module is built around
-------------------------------------
Firmware for an unfamiliar part is not uniformly unknowable. It divides cleanly:

* **The application** -- LED patterns, button debounce, NMEA parsing, the
  scheduler. This is where defects actually live, and none of it depends on the
  vendor. It is emitted complete, every time, for a part whose SDK nobody here
  has seen.

* **The port** -- fourteen functions in one file. This is the only place the
  SDK is needed, and when the SDK is unknown it comes out as stubs, each
  carrying the question an engineer must answer.

So "we do not have the SDK" costs an afternoon on a file containing no logic,
instead of costing the firmware.

What is never done here
-----------------------
Nothing is flashed, and no flashing tool is invoked or required. `FLASHING.md`
records what is known about the tool -- often nothing, which it says -- and the
engineer takes it from there. A build server cannot confirm which board is on
the other end of a cable, so it has no business writing to one.

Provenance is not decoration. Board facts come from a person and nothing here
can check them, so every one is emitted next to a comment saying exactly that.
When there is no compiler and no hardware in the loop, the code saying where
each number came from is the only safeguard left.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from knowledge.board import BoardFacts, Device
from knowledge.family import HwFamily, Support
from knowledge.hal import OPERATIONS, candidates
from knowledge.questions import blocking, board_questions

BANNER = "fw-automation-agent -- generated, not hand-written"


class EmitError(RuntimeError):
    """Generation refused. The message names what is missing and why."""


@dataclass
class Project:
    """The emitted tree: relative path -> file contents."""

    files: dict[str, str] = field(default_factory=dict)
    review: list[str] = field(default_factory=list)
    """Lines a human must check before this is trusted on hardware."""

    def add(self, path: str, content: str) -> None:
        self.files[path] = content

    @property
    def count(self) -> int:
        return len(self.files)


def emit(board: BoardFacts, family: HwFamily | None) -> Project:
    """Generate the project, or refuse and say what is unanswered."""
    open_questions = blocking(board_questions(board, family))
    if open_questions:
        raise EmitError(
            "Not generating: "
            + f"{len(open_questions)} question(s) are unanswered, and each one "
            "fails silently if guessed.\n"
            + "\n".join(f"  - {u.field}: {u.question}" for u in open_questions)
        )

    clashes = board.address_conflicts()
    if clashes:
        raise EmitError("Not generating:\n" + "\n".join(f"  - {c}" for c in clashes))

    _check_pin_syntax(board, family)

    project = Project()
    slug = _slug(family.family_id if family else board.mcu)

    project.add("port/hal.h", _hal_header())
    project.add("port/board_pins.h", _board_pins(board))
    port_source, review = _hal_port(family, slug)
    project.add(f"port/hal_{slug}.c", port_source)
    project.review.extend(review)

    project.add("app/app_config.h", _app_config(board, family))
    project.add("app/main.c", _main(board))

    if board.of_kind("led"):
        project.add("app/led.h", _led_header(board))
        project.add("app/led.c", _led_source(board))
    if board.of_kind("button"):
        project.add("app/button.h", _BUTTON_H)
        project.add("app/button.c", _button_source(board))
    if board.of_kind("gnss"):
        project.add("app/gnss.h", _GNSS_H)
        project.add("app/gnss.c", _GNSS_C)
    if board.of_kind("imu") or board.of_kind("sensor"):
        project.add("app/i2c_devices.h", _i2c_header(board))
        project.add("app/i2c_devices.c", _i2c_source(board))

    project.add("README.md", _readme(board, family, project))
    project.add("PROVENANCE.md", _provenance(board, family, project))
    project.add("FLASHING.md", _flashing(board, family))
    return project


# --- checks -----------------------------------------------------------------


def _check_pin_syntax(board: BoardFacts, family: HwFamily | None) -> None:
    """Catch a pin typed in another family's notation before it reaches C.

    Only runs when the family states its own syntax. Abstaining is correct:
    inventing a pattern would reject valid answers.
    """
    if not family or not family.pin_syntax:
        return
    shape = re.escape(family.pin_syntax)
    shape = re.sub(r"\d+", r"\\d+", shape)
    pattern = re.compile(f"^{shape}$", re.IGNORECASE)

    wrong = []
    for device in board.devices:
        for role, pin in device.pins.items():
            if pin and not pattern.fullmatch(pin):
                wrong.append(f"{device.name}.{role} = {pin!r}")
    if wrong:
        raise EmitError(
            f"These pins are not written the way {family.family_id} names pins "
            f"(example: {family.pin_syntax}):\n"
            + "\n".join(f"  - {w}" for w in wrong)
            + "\nA pin in another part's notation compiles to a different pin, "
              "or to nothing."
        )


# --- the porting layer ------------------------------------------------------


def _hal_header() -> str:
    lines = [
        f"/* {BANNER} */",
        "#ifndef HAL_H",
        "#define HAL_H",
        "",
        "#include <stdbool.h>",
        "#include <stddef.h>",
        "#include <stdint.h>",
        "",
        "/* A pin, as this SoC's SDK identifies one. Widened to 32 bits so a",
        " * port that encodes port+pin in one value still fits. */",
        "typedef uint32_t hal_pin_t;",
        "",
        "typedef enum {",
        "    HAL_PULL_NONE = 0,",
        "    HAL_PULL_UP,",
        "    HAL_PULL_DOWN",
        "} hal_pull_t;",
        "",
    ]
    for op in OPERATIONS:
        lines.append(f"/* {op.purpose} */")
        lines.append(f"{op.signature};")
        lines.append("")
    lines += ["#endif /* HAL_H */", ""]
    return "\n".join(lines)


def _hal_port(family: HwFamily | None, slug: str) -> tuple[str, list[str]]:
    """The one file that depends on the vendor.

    Each operation resolves to one of three outcomes, and they are kept
    visibly distinct because they carry different amounts of evidence:
      * one candidate  -> a call, flagged for review (the symbol is known to
        exist; that it is the *right* symbol is a guess)
      * several/none   -> a stub carrying the question
      * no SDK at all  -> every operation is a stub
    """
    names = [s.name for s in family.symbols] if family else []
    label = family.family_id if family else "unknown part"
    review: list[str] = []

    head = [
        f"/* {BANNER}",
        f" *",
        f" * Porting layer for {label}.",
        f" * This is the only file in the project that depends on the vendor SDK.",
    ]
    if family and family.sdk and family.sdk.present:
        head.append(f" * SDK: {family.sdk.name} {family.sdk.version} ({len(names)} functions catalogued)")
    else:
        head += [
            " *",
            " * No SDK was available when this was generated, so every function",
            " * below is a stub. Filling them in is the whole porting job: there",
            " * is no logic here, only calls into the vendor's API.",
        ]
    head += [
        " */",
        "",
        "#include \"hal.h\"",
        "",
        "/* TODO(port): include the SDK headers this part needs. */",
        "",
        "/* This file is incomplete by construction. Building it as-is is almost",
        " * certainly a mistake, so it refuses. Define HAL_PORT_INCOMPLETE_OK to",
        " * compile the application logic before the port is written -- useful for",
        " * checking the app on a host, never for producing a flashable image. */",
        "#if !defined(HAL_PORT_INCOMPLETE_OK)",
        f"#  error \"port/hal_{slug}.c is not finished: the operations below need SDK calls.\"",
        "#endif",
        "",
    ]

    body: list[str] = []
    for op in OPERATIONS:
        found = candidates(op, names) if names else []
        body.append(f"/* {op.purpose} */")
        body.append(f"{op.signature}")
        body.append("{")

        if len(found) == 1:
            body += [
                f"    /* REVIEW: the SDK declares {found[0]}(), and its name matches",
                f"     * this operation. The argument order has NOT been checked --",
                f"     * confirm it against the header before trusting this call. */",
                f"    /* return {found[0]}(...); */",
            ]
            review.append(f"{op.name} -> {found[0]} (single candidate, argument order unverified)")
        elif found:
            body.append(f"    /* The SDK declares several functions that could be this one:")
            for name in found:
                body.append(f"     *   {name}")
            body.append("     * Pick the right one; none was chosen because guessing between")
            body.append("     * them is exactly the error this generator exists to avoid. */")
            review.append(f"{op.name} -> ambiguous: {', '.join(found)}")
        else:
            body.append("    /* No catalogued SDK function matched this operation.")
            body.append(f"     * Question: which SDK call performs \"{op.purpose}\" */")
            review.append(f"{op.name} -> no candidate; needs an answer")

        # An unimplemented stub leaves every parameter unused, and an engineer
        # who builds with -Wall -Wextra would get fourteen functions' worth of
        # warnings burying anything real.
        body += [f"    (void){arg};" for arg in _parameters(op.signature)]
        body.append(_stub_return(op.signature))
        body += ["}", ""]

    return "\n".join(head + body), review


def _parameters(signature: str) -> list[str]:
    """Parameter names from a declaration, for silencing unused warnings."""
    inner = signature[signature.index("(") + 1: signature.rindex(")")]
    names = []
    for part in inner.split(","):
        part = part.strip()
        if not part or part == "void":
            continue
        token = re.sub(r"\[.*$", "", part.split()[-1].lstrip("*"))
        if token:
            names.append(token)
    return names


def _stub_return(signature: str) -> str:
    returns = signature.split()[0]
    if returns == "void":
        return "    return;"
    if returns == "bool":
        return "    return false;"
    return "    return 0;"


def _board_pins(board: BoardFacts) -> str:
    """Placeholder definitions for pins named in the SDK's own notation.

    A pin answered as `GPIO_12` is a symbol the *SDK* defines. Without the SDK
    nothing declares it, so the application -- which is otherwise entirely
    vendor-independent -- would not compile, and the one part of the project
    that can be checked before porting could not be checked.

    Each definition is therefore guarded with `#ifndef`. Include the SDK header
    ahead of this file and the real constant wins silently; that is the point.
    The placeholder value is the trailing number in the name, which is right
    often enough to be useful and is labelled loudly enough not to be trusted.
    """
    lines = [
        f"/* {BANNER}",
        " *",
        " * Pin names as answered in the interview, in this part's own notation.",
        " *",
        " * Every definition below is a PLACEHOLDER, present only so the",
        " * application logic compiles before the SDK is available. Each is",
        " * guarded: include the SDK's own header before this file and the real",
        " * constant takes precedence with no edit here.",
        " */",
        "#ifndef BOARD_PINS_H",
        "#define BOARD_PINS_H",
        "",
    ]
    emitted: set[str] = set()
    fallback = 0
    for device in board.devices:
        for role, pin in device.pins.items():
            if not pin or pin in emitted or _is_c_number(pin):
                continue
            emitted.add(pin)
            digits = re.search(r"(\d+)", pin)
            if digits:
                value = digits.group(1)
            else:
                value = str(fallback)
                fallback += 1
            lines += [
                f"#ifndef {pin}",
                f"#  define {pin} {value}u  /* PLACEHOLDER for {device.name}.{role};"
                f" confirm against the SDK */",
                "#endif",
                "",
            ]
    if not emitted:
        lines += ["/* Every pin was given numerically; nothing to place-hold. */", ""]
    lines += ["#endif /* BOARD_PINS_H */", ""]
    return "\n".join(lines)


def _is_c_number(text: str) -> bool:
    stripped = text.strip().rstrip("uU")
    if stripped.lower().startswith("0x"):
        return all(c in "0123456789abcdefABCDEF" for c in stripped[2:]) and len(stripped) > 2
    return stripped.isdigit()


# --- application ------------------------------------------------------------


def _app_config(board: BoardFacts, family: HwFamily | None) -> str:
    unverifiable = "answered by a human; nothing here can check it"
    lines = [
        f"/* {BANNER}",
        f" *",
        f" * Board: {board.board_name}",
        f" * MCU:   {board.mcu}" + (f"  (family {family.family_id})" if family else ""),
        " *",
        " * Every value in this file was answered by a person who has seen the",
        " * schematic. None of it is derived from an artifact, so none of it is",
        " * checkable here -- if one is wrong, the firmware still builds and runs.",
        " */",
        "#ifndef APP_CONFIG_H",
        "#define APP_CONFIG_H",
        "",
        "#include \"../port/hal.h\"",
        "/* Pin names in this part's notation. Placeholders until the SDK is",
        " * present; see the header itself. */",
        "#include \"../port/board_pins.h\"",
        "",
        f"#define BOARD_NAME       \"{board.board_name}\"",
        f"#define APP_LOOP_MS      {board.loop_ms if board.loop_ms is not None else 1000}u",
        "",
    ]
    for index, device in enumerate(board.devices):
        stem = _c_ident(device.name)
        lines.append(f"/* {device.name}"
                     + (f" -- {device.role}" if device.role else "")
                     + f"  [{unverifiable}] */")
        for role, pin in device.pins.items():
            lines.append(f"#define {stem}_{role.upper()}_PIN    ((hal_pin_t){pin})")
        if device.active_level:
            high = 1 if device.active_level.startswith("active high") or device.active_level == "high" else 0
            lines.append(f"#define {stem}_ACTIVE_HIGH  {high}")
        if device.pull:
            lines.append(f"#define {stem}_PULL         {_pull_enum(device.pull)}")
        if device.bus:
            lines.append(f"#define {stem}_BUS          {_bus_index(device.bus)}u  /* {device.bus} */")
        if device.address:
            lines.append(f"#define {stem}_ADDR         {device.address}")
        if device.baud is not None:
            lines.append(f"#define {stem}_BAUD         {device.baud}u")
        lines.append("")

    lines += ["#endif /* APP_CONFIG_H */", ""]
    return "\n".join(lines)


def _main(board: BoardFacts) -> str:
    leds = board.of_kind("led")
    buttons = board.of_kind("button")
    gnss = board.of_kind("gnss")
    i2c_parts = board.of_kind("imu") + board.of_kind("sensor")

    lines = [
        f"/* {BANNER}",
        " *",
        f" * {board.intent or 'No intent was stated for this firmware.'}",
        " */",
        "",
        "#include \"app_config.h\"",
        "#include \"../port/hal.h\"",
    ]
    if leds:
        lines.append("#include \"led.h\"")
    if buttons:
        lines.append("#include \"button.h\"")
    if gnss:
        lines.append("#include \"gnss.h\"")
    if i2c_parts:
        lines.append("#include \"i2c_devices.h\"")

    lines += [
        "",
        "int main(void)",
        "{",
        "    hal_init();",
        "",
    ]
    if leds:
        lines.append("    led_init();")
    if buttons:
        lines.append("    button_init();")
    if gnss:
        for device in gnss:
            stem = _c_ident(device.name)
            lines.append(f"    gnss_init({stem}_BUS, {stem}_BAUD);")
    if i2c_parts:
        lines.append("    i2c_devices_init();")

    lines += [
        "",
        "    for (;;) {",
        "        uint32_t now = hal_uptime_ms();",
        "",
    ]
    if leds:
        lines.append("        led_tick(now);")
    if buttons:
        lines += [
            "        switch (button_tick(now)) {",
            "        case BTN_SHORT:",
            "            /* TODO(product): what a short press does was not specified. */",
            "            break;",
            "        case BTN_LONG:",
            "            /* TODO(product): what a long press does was not specified. */",
            "            break;",
            "        default:",
            "            break;",
            "        }",
        ]
    if gnss:
        lines += [
            "        gnss_fix_t fix;",
            "        if (gnss_tick(&fix)) {",
            "            /* A new fix arrived. fix.valid says whether it is usable. */",
            "        }",
        ]
    if i2c_parts:
        lines.append("        i2c_devices_tick(now);")

    lines += [
        "",
        "        hal_delay_ms(APP_LOOP_MS);",
        "    }",
        "}",
        "",
    ]
    return "\n".join(lines)


def _led_header(board: BoardFacts) -> str:
    leds = board.of_kind("led")
    lines = [
        f"/* {BANNER} */",
        "#ifndef LED_H",
        "#define LED_H",
        "",
        "#include <stdint.h>",
        "",
        "typedef enum {",
    ]
    for device in leds:
        lines.append(f"    LED_{_c_ident(device.name)},"
                     + (f"  /* {device.role} */" if device.role else ""))
    lines += [
        "    LED_COUNT",
        "} led_id_t;",
        "",
        "/* The four states a single indicator can be in. Anything the product",
        " * specification describes as steady / slow blink / fast blink / off maps",
        " * onto these without further interpretation. */",
        "typedef enum {",
        "    LED_OFF = 0,",
        "    LED_STEADY,",
        "    LED_SLOW,",
        "    LED_FAST",
        "} led_pattern_t;",
        "",
        "void led_init(void);",
        "void led_set(led_id_t id, led_pattern_t pattern);",
        "void led_tick(uint32_t now_ms);",
        "",
        "#endif /* LED_H */",
        "",
    ]
    return "\n".join(lines)


def _led_source(board: BoardFacts) -> str:
    leds = board.of_kind("led")
    lines = [
        f"/* {BANNER}",
        " *",
        " * Blink timing is derived from now_ms rather than counters, so a missed",
        " * tick shifts phase instead of accumulating drift.",
        " */",
        "",
        "#include \"led.h\"",
        "#include \"app_config.h\"",
        "#include \"../port/hal.h\"",
        "",
        "#define LED_SLOW_PERIOD_MS  1000u",
        "#define LED_FAST_PERIOD_MS   500u",
        "",
        "static const hal_pin_t s_pin[LED_COUNT] = {",
    ]
    for device in leds:
        lines.append(f"    {_c_ident(device.name)}_OUT_PIN,")
    lines += ["};", "", "static const uint8_t s_active_high[LED_COUNT] = {"]
    for device in leds:
        lines.append(f"    {_c_ident(device.name)}_ACTIVE_HIGH,")
    lines += [
        "};",
        "",
        "static led_pattern_t s_pattern[LED_COUNT];",
        "",
        "void led_init(void)",
        "{",
        "    for (unsigned i = 0; i < (unsigned)LED_COUNT; i++) {",
        "        hal_gpio_config_output(s_pin[i]);",
        "        s_pattern[i] = LED_OFF;",
        "    }",
        "}",
        "",
        "void led_set(led_id_t id, led_pattern_t pattern)",
        "{",
        "    if ((unsigned)id < (unsigned)LED_COUNT) {",
        "        s_pattern[id] = pattern;",
        "    }",
        "}",
        "",
        "void led_tick(uint32_t now_ms)",
        "{",
        "    for (unsigned i = 0; i < (unsigned)LED_COUNT; i++) {",
        "        bool on;",
        "        switch (s_pattern[i]) {",
        "        case LED_STEADY: on = true;  break;",
        "        case LED_SLOW:   on = ((now_ms / (LED_SLOW_PERIOD_MS / 2u)) & 1u) != 0u; break;",
        "        case LED_FAST:   on = ((now_ms / (LED_FAST_PERIOD_MS / 2u)) & 1u) != 0u; break;",
        "        case LED_OFF:",
        "        default:         on = false; break;",
        "        }",
        "        /* Active level is applied here and nowhere else, so the rest of",
        "         * the firmware reasons in terms of on and off. */",
        "        hal_gpio_write(s_pin[i], s_active_high[i] ? on : !on);",
        "    }",
        "}",
        "",
    ]
    return "\n".join(lines)


_BUTTON_H = r"""/* fw-automation-agent -- generated, not hand-written */
#ifndef BUTTON_H
#define BUTTON_H

#include <stdint.h>

typedef enum {
    BTN_NONE = 0,
    BTN_SHORT,
    BTN_LONG
} btn_event_t;

void        button_init(void);
btn_event_t button_tick(uint32_t now_ms);

#endif /* BUTTON_H */
"""


def _button_source(board: BoardFacts) -> str:
    device = board.of_kind("button")[0]
    stem = _c_ident(device.name)
    return "\n".join([
        f"/* {BANNER}",
        " *",
        " * Debounce plus press classification. The long-press threshold is a",
        " * product decision, not a technical one; it is defined here so there is",
        " * exactly one place to change it.",
        " */",
        "",
        "#include \"button.h\"",
        "#include \"app_config.h\"",
        "#include \"../port/hal.h\"",
        "",
        "#define BTN_DEBOUNCE_MS    40u",
        "#define BTN_LONG_MS      1200u",
        "",
        "static uint8_t  s_stable;      /* last debounced level */",
        "static uint8_t  s_candidate;",
        "static uint32_t s_changed_ms;",
        "static uint32_t s_pressed_ms;",
        "",
        "void button_init(void)",
        "{",
        f"    hal_gpio_config_input({stem}_IN_PIN, {stem}_PULL);",
        "    s_stable = 0u;",
        "    s_candidate = 0u;",
        "    s_changed_ms = 0u;",
        "    s_pressed_ms = 0u;",
        "}",
        "",
        "btn_event_t button_tick(uint32_t now_ms)",
        "{",
        f"    bool raw = hal_gpio_read({stem}_IN_PIN);",
        f"    uint8_t level = ({stem}_ACTIVE_HIGH ? raw : !raw) ? 1u : 0u;",
        "",
        "    if (level != s_candidate) {",
        "        s_candidate = level;",
        "        s_changed_ms = now_ms;",
        "        return BTN_NONE;",
        "    }",
        "    if (level == s_stable || (now_ms - s_changed_ms) < BTN_DEBOUNCE_MS) {",
        "        return BTN_NONE;",
        "    }",
        "",
        "    s_stable = level;",
        "    if (level) {",
        "        s_pressed_ms = now_ms;",
        "        return BTN_NONE;   /* classified on release */",
        "    }",
        "    return ((now_ms - s_pressed_ms) >= BTN_LONG_MS) ? BTN_LONG : BTN_SHORT;",
        "}",
        "",
    ])


_GNSS_H = r"""/* fw-automation-agent -- generated, not hand-written */
#ifndef GNSS_H
#define GNSS_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    bool    valid;      /* RMC status was 'A' */
    double  latitude;   /* degrees, north positive */
    double  longitude;  /* degrees, east positive */
    double  speed_kn;
    uint32_t utc_hhmmss;
    uint32_t date_ddmmyy;
} gnss_fix_t;

void gnss_init(uint8_t port, uint32_t baud);

/* Feeds bytes from the UART into the parser. Returns true, and fills *out,
 * when a complete and checksum-valid RMC sentence has been decoded. */
bool gnss_tick(gnss_fix_t *out);

#endif /* GNSS_H */
"""


_GNSS_C = r"""/* fw-automation-agent -- generated, not hand-written
 *
 * NMEA 0183 line assembly and RMC decoding.
 *
 * This is receiver-independent: any module that emits standard NMEA works
 * without change. Vendor-specific configuration sentences (fix rate, which
 * constellations, low-power modes) are NOT here, because they differ per part
 * and inventing them would produce a device that silently keeps its defaults.
 *
 * The checksum is verified before a sentence is used. A corrupt sentence that
 * parses is worse than one that is dropped: it yields a position.
 */

#include "gnss.h"
#include "../port/hal.h"

#include <stdlib.h>
#include <string.h>

#define GNSS_LINE_MAX 96

static uint8_t s_port;
static char    s_line[GNSS_LINE_MAX];
static size_t  s_len;

void gnss_init(uint8_t port, uint32_t baud)
{
    s_port = port;
    s_len = 0u;
    (void)hal_uart_init(port, baud);
}

static int hex_value(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

/* $....*HH -- XOR of everything between '$' and '*'. */
static bool checksum_ok(const char *line, size_t len)
{
    if (len < 4u || line[0] != '$') return false;

    size_t star = 0u;
    for (size_t i = 0u; i < len; i++) {
        if (line[i] == '*') { star = i; }
    }
    if (star == 0u || (star + 2u) >= len) return false;

    int hi = hex_value(line[star + 1u]);
    int lo = hex_value(line[star + 2u]);
    if (hi < 0 || lo < 0) return false;

    uint8_t sum = 0u;
    for (size_t i = 1u; i < star; i++) {
        sum ^= (uint8_t)line[i];
    }
    return sum == (uint8_t)((hi << 4) | lo);
}

/* ddmm.mmmm -> degrees. The degree part is a fixed 2 or 3 digits depending on
 * whether this is latitude or longitude, which is why width is passed in. */
static double to_degrees(const char *field, int degree_digits, char hemisphere)
{
    if (field == NULL || field[0] == '\0') return 0.0;

    char degrees[4] = {0};
    for (int i = 0; i < degree_digits && field[i] != '\0'; i++) {
        degrees[i] = field[i];
    }
    double value = atof(degrees) + (atof(field + degree_digits) / 60.0);
    if (hemisphere == 'S' || hemisphere == 'W') {
        value = -value;
    }
    return value;
}

/* Splits in place. Returns how many fields were found. */
static int split(char *line, char **fields, int max_fields)
{
    int count = 0;
    fields[count++] = line;
    for (char *p = line; *p != '\0' && count < max_fields; p++) {
        if (*p == ',' || *p == '*') {
            *p = '\0';
            fields[count++] = p + 1;
        }
    }
    return count;
}

static bool decode_rmc(char *line, gnss_fix_t *out)
{
    char *f[16];
    int n = split(line, f, 16);
    if (n < 10) return false;

    /* Field 0 is $--RMC; the talker prefix varies by constellation. */
    if (strlen(f[0]) < 6 || strcmp(f[0] + 3, "RMC") != 0) return false;

    memset(out, 0, sizeof(*out));
    out->valid       = (f[2][0] == 'A');
    out->utc_hhmmss  = (uint32_t)atol(f[1]);
    out->latitude    = to_degrees(f[3], 2, f[4][0]);
    out->longitude   = to_degrees(f[5], 3, f[6][0]);
    out->speed_kn    = atof(f[7]);
    out->date_ddmmyy = (uint32_t)atol(f[9]);
    return true;
}

bool gnss_tick(gnss_fix_t *out)
{
    uint8_t byte;

    while (hal_uart_read(s_port, &byte, 1u, 0u) == 1) {
        if (byte == '\r') {
            continue;
        }
        if (byte == '\n') {
            bool decoded = false;
            if (s_len > 0u && s_len < GNSS_LINE_MAX) {
                s_line[s_len] = '\0';
                if (checksum_ok(s_line, s_len)) {
                    decoded = decode_rmc(s_line, out);
                }
            }
            s_len = 0u;
            if (decoded) {
                return true;
            }
            continue;
        }
        if (s_len < (GNSS_LINE_MAX - 1u)) {
            s_line[s_len++] = (char)byte;
        } else {
            /* Overlong sentence: drop it rather than truncate into a parse. */
            s_len = 0u;
        }
    }
    return false;
}
"""


def _i2c_header(board: BoardFacts) -> str:
    parts = board.of_kind("imu") + board.of_kind("sensor")
    lines = [
        f"/* {BANNER} */",
        "#ifndef I2C_DEVICES_H",
        "#define I2C_DEVICES_H",
        "",
        "#include <stdbool.h>",
        "#include <stdint.h>",
        "",
        "void i2c_devices_init(void);",
        "void i2c_devices_tick(uint32_t now_ms);",
        "",
    ]
    for device in parts:
        stem = _c_ident(device.name).lower()
        lines += [
            f"/* {device.name}"
            + (f" -- {device.role}" if device.role else "") + " */",
            f"bool {stem}_read_reg(uint8_t reg, uint8_t *value);",
            f"bool {stem}_write_reg(uint8_t reg, uint8_t value);",
            "",
        ]
    lines += ["#endif /* I2C_DEVICES_H */", ""]
    return "\n".join(lines)


def _i2c_source(board: BoardFacts) -> str:
    parts = board.of_kind("imu") + board.of_kind("sensor")
    lines = [
        f"/* {BANNER}",
        " *",
        " * Register access for the I2C parts on this board. The address and bus",
        " * for each come from the interview and are defined in app_config.h.",
        " *",
        " * Register maps are deliberately absent: which register holds what is a",
        " * datasheet fact, and writing one from memory produces a driver that",
        " * talks successfully to the right part and configures the wrong thing.",
        " */",
        "",
        "#include \"i2c_devices.h\"",
        "#include \"app_config.h\"",
        "#include \"../port/hal.h\"",
        "",
    ]
    for device in parts:
        stem = _c_ident(device.name)
        low = stem.lower()
        lines += [
            f"bool {low}_read_reg(uint8_t reg, uint8_t *value)",
            "{",
            f"    return hal_i2c_write_read({stem}_BUS, {stem}_ADDR,",
            "                              &reg, 1u, value, 1u) == 0;",
            "}",
            "",
            f"bool {low}_write_reg(uint8_t reg, uint8_t value)",
            "{",
            "    uint8_t payload[2];",
            "    payload[0] = reg;",
            "    payload[1] = value;",
            f"    return hal_i2c_write({stem}_BUS, {stem}_ADDR, payload, sizeof(payload)) == 0;",
            "}",
            "",
        ]

    lines += [
        "void i2c_devices_init(void)",
        "{",
    ]
    for bus in sorted({d.bus for d in parts if d.bus}):
        lines.append(f"    (void)hal_i2c_init({_bus_index(bus)}u, 100000u);  /* {bus} */")
    lines += [
        "    /* TODO(driver): each part needs its configuration written here.",
        "     * That requires its datasheet register map, which was not supplied. */",
        "}",
        "",
        "void i2c_devices_tick(uint32_t now_ms)",
        "{",
        "    (void)now_ms;",
        "    /* TODO(driver): periodic sampling goes here. */",
        "}",
        "",
    ]
    return "\n".join(lines)


# --- documents --------------------------------------------------------------


def _readme(board: BoardFacts, family: HwFamily | None, project: Project) -> str:
    support = family.support.value if family else "no family record"
    return "\n".join([
        f"# {board.board_name} firmware",
        "",
        board.intent or "_No intent was stated for this firmware._",
        "",
        f"Generated {date.today().isoformat()} by fw-automation-agent.",
        "",
        "## Layout",
        "",
        "```",
        "app/     application logic -- complete, and independent of the vendor SDK",
        "port/    the porting layer -- fourteen functions, the only vendor-specific code",
        "```",
        "",
        "## State",
        "",
        f"- Target: `{board.mcu}`" + (f" (family `{family.family_id}`, {support})" if family else ""),
        f"- Files: {project.count}",
        f"- Port operations needing a human: {len(project.review)}",
        "",
        "## What to do next",
        "",
        "1. Read `PROVENANCE.md`. It lists every value that came from an answer",
        "   rather than from an artifact, and those are the ones that fail quietly.",
        f"2. Fill in `port/hal_{_slug(family.family_id if family else board.mcu)}.c`.",
        "   It contains no logic; it maps fourteen operations onto SDK calls.",
        "3. Build with your vendor toolchain. This generator did not compile",
        "   anything, so nothing here is known to build.",
        "4. Flash it yourself -- see `FLASHING.md`.",
        "",
        "## What this is not",
        "",
        "Nothing here has run on hardware. The application logic is written to be",
        "correct and is not proof of anything; the board facts it rests on came",
        "from a person and could not be checked by this tool.",
        "",
    ])


def _provenance(board: BoardFacts, family: HwFamily | None, project: Project) -> str:
    lines = [
        "# Provenance",
        "",
        "Where every fact in this firmware came from, so the weak ones are visible.",
        "",
        "## Answered by a human, and unverifiable by anything here",
        "",
        "These drive the generated code. Nothing in this tool can check them, and",
        "each one produces firmware that builds and runs if it is wrong.",
        "",
    ]
    for index, device in enumerate(board.devices):
        lines.append(f"### {device.name} (`devices[{index}]`, {device.interface})")
        for role, pin in device.pins.items():
            lines.append(f"- `pins.{role}` = `{pin}`")
        for label, value in (
            ("active_level", device.active_level), ("pull", device.pull),
            ("bus", device.bus), ("address", device.address),
            ("baud", device.baud),
        ):
            if value not in ("", None):
                lines.append(f"- `{label}` = `{value}`")
        lines.append("")

    lines += ["## Derived from a versioned artifact", ""]
    if family and family.sdk and family.sdk.present:
        lines += [
            f"- SDK `{family.sdk.name} {family.sdk.version}` at `{family.sdk.local_path}`",
            f"- {len(family.symbols)} function declarations, each carrying its header and line",
            "",
        ]
        for claim in sorted(family.facts.values(), key=lambda c: c.predicate):
            if claim.authoritative:
                lines.append(f"- `{claim.predicate}` = `{claim.value}` — {claim.evidence.describe()}")
        lines.append("")
    else:
        lines += [
            "- Nothing. No SDK was present when this was generated, so no symbol",
            "  in the porting layer rests on an artifact.",
            "",
        ]

    if family and family.unsupported():
        lines += ["## Found by looking, but not authoritative", ""]
        for claim in family.unsupported():
            lines.append(f"- `{claim.predicate}` = `{claim.value}` — {claim.evidence.describe()}")
        lines.append("")

    lines += [
        "## The porting layer",
        "",
        "Each line below is an operation whose SDK mapping a human must settle.",
        "A single named candidate means the SDK declares a function whose *name*",
        "fits. It does not mean the arguments match.",
        "",
    ]
    lines += [f"- {item}" for item in project.review] or ["- none"]
    lines += [
        "",
        "## Not established",
        "",
        "- This firmware has not been compiled.",
        "- It has not been run.",
        "- It has not been on hardware.",
        "",
    ]
    return "\n".join(lines)


def _flashing(board: BoardFacts, family: HwFamily | None) -> str:
    tool = ""
    if family:
        claim = family.facts.get("flash_tool")
        if claim:
            tool = str(claim.value)
    return "\n".join([
        "# Flashing",
        "",
        "**This tool does not flash anything, and does not need the flashing tool",
        "to have been available.** An engineer flashes this image.",
        "",
        "## Why it is left to you",
        "",
        "A generator cannot confirm which board is on the other end of a cable.",
        "Writing to the wrong one is not recoverable from here, and a successful",
        "flash proves only that bytes were transferred. It replaces whatever was",
        "on the part, and it",
        "says nothing about whether the pins in this firmware match the board.",
        "",
        "That last point is the one worth keeping: a device that boots after",
        "flashing has demonstrated nothing about the wiring assumptions inside it.",
        "",
        "## What is known about the tool",
        "",
        (f"- {tool}" if tool else
         "- Nothing. No flashing tool was recorded for this family, which is fine:\n"
         "  the vendor's own utility is what you would use in any case."),
        "",
        "## Before you flash",
        "",
        "1. Confirm the pin assignments in `PROVENANCE.md` against the schematic.",
        "   They came from an interview and nothing has checked them.",
        f"2. Confirm this is a `{board.mcu}` and not a variant with different memory.",
        "3. Have a way back. Flashing replaces whatever is on the part.",
        "",
    ])


# --- helpers ----------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "target"


def _c_ident(text: str) -> str:
    ident = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").upper()
    if not ident:
        return "DEV"
    return f"D_{ident}" if ident[0].isdigit() else ident


def _pull_enum(pull: str) -> str:
    lowered = pull.lower()
    if "up" in lowered:
        return "HAL_PULL_UP"
    if "down" in lowered:
        return "HAL_PULL_DOWN"
    return "HAL_PULL_NONE"


def _bus_index(bus: str) -> int:
    """The trailing number of a bus name: 'I2C0' -> 0.

    Falls back to 0 when the name carries no index, which is right for parts
    with exactly one controller and visible in app_config.h either way.
    """
    match = re.search(r"(\d+)\s*$", bus)
    return int(match.group(1)) if match else 0
