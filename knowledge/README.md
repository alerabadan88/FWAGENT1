# The vendor-SDK knowledge base

For parts Zephyr does not support, where the only way to write firmware is
against a vendor SDK.

It is a **separate path**, not a replacement. Nothing here imports from
`codegen/zephyr/` and nothing there imports from here; two tests enforce that
in both directions. Adding a family cannot change or break the Zephyr path.

## Why it exists

The Zephyr path works because facts come from artifacts: peripheral counts are
read out of the SoC `.dtsi`, bindings out of `dts/bindings`, and the compiler
adjudicates the result. For a part Zephyr has never heard of, all three of
those disappear at once.

The tempting response is to generate plausible vendor code anyway. That is the
worst available option, because the failure is silent: firmware written from a
recollection of what a vendor API "usually looks like" either fails to link
(annoying, and honest) or links against a similarly-named function and does the
wrong thing at runtime (a defect nobody finds on the bench).

So this package does something narrower and more useful.

## The split it is built around

Firmware for an unfamiliar part is not uniformly unknowable:

| | Depends on the SDK? | What happens here |
|---|---|---|
| LED patterns, button debounce, NMEA parsing, scheduling | No | Emitted complete, every time |
| Fourteen HAL functions | Yes | Filled in when the SDK is present; stubbed with the question when it is not |

The application is where defects actually live, and it is vendor-independent.
So "we do not have the SDK" costs an afternoon on one file containing no logic,
instead of costing the firmware.

That claim is checked rather than asserted: the test suite compiles the emitted
application with `-Wall -Wextra -Werror` on an ARM cross compiler.

## Flow

```
mcu string
   |
   v
KnowledgeBase.resolve()  ---- no match ----> questions.unknown_family()
   |                                          "who makes it? is the SDK anywhere?"
   | match
   v
HwFamily  (READY | PARTIAL | IDENTIFIED)
   |
   |  PARTIAL and want more?   acquire.search_plan()  -> where to look
   |                           acquire.record_lead()  -> CITED, does not promote
   |                           acquire.ingest(path)   -> AUTHORITATIVE, promotes to READY
   v
questions.board_questions()  -> everything about this PCB nobody has said
   |
   |  blocking answered
   v
emit()  ->  app/  (complete)   +  port/  (filled or stubbed)  +  PROVENANCE.md
```

## The two routes to a fact, and why they are not merged

`ingest` reads headers on this machine. Every symbol carries the file and line
it came from, so it is `AUTHORITATIVE` and anyone can go and look.

`record_lead` stores a download page somebody found. That is `CITED`: real,
dated, re-checkable, and **not** authority. A record holding only leads still
emits stubs.

Collapsing these would be the most damaging shortcut available here, because a
page saying "supports I2C, UART, SPI" reads like knowledge and supports no line
of code.

### NDA-gated SDKs

Extraction is deterministic parsing — no model, no network. The SDK never
leaves the machine; only the derived catalogue of names and signatures is
written down, and that stays in the local base. This is why the design does not
need the SDK to be redistributable.

## What it deliberately does not do

**It does not flash, and does not require a flashing tool to exist.** A
generator cannot confirm which board is on the other end of a cable, and a
successful flash proves only that bytes moved — it says nothing about whether
the pin assignments inside the firmware match the board they landed on. An
engineer flashes it. `FLASHING.md` says what is known about the tool, which is
often nothing, and says that too.

**It does not invent register maps.** Which register holds what is a datasheet
fact. A driver written from memory talks successfully to the right part and
configures the wrong thing.

**It does not guess a pin, an active level, or an I2C address.** Each of those
produces firmware that builds and runs. `emit()` refuses and names the
question.

## Files

| | |
|---|---|
| `family.py` | What is known about one silicon family, and on whose authority |
| `base.py` | Records on disk; part number → family |
| `extract.py` | SDK tree → symbol catalogue (offline, deterministic) |
| `acquire.py` | Search plans, leads (CITED), ingestion (AUTHORITATIVE) |
| `board.py` | What somebody said about one PCB |
| `questions.py` | Everything the emitter needs and nobody has said |
| `hal.py` | The fourteen-function porting contract |
| `emit.py` | The C project |
| `seed.py` | Shipped records, as code so the evidence is reviewable |

## Try it

```bash
python -m knowledge.seed          # write the shipped records
python -m examples.knowledge_demo # refuse, then generate, on a real part
```

The demo prints the refusal first. That refusal is the product working.

## Adding a family

1. Add a builder to `seed.py`. State the part patterns by hand — a die ships
   under many order codes, and the suffix is packaging.
2. Record what is known with the evidence helper that fits: `cited()` for a
   datasheet or a spec sheet, never `derived()` unless you read it out of a
   versioned artifact.
3. If an SDK is available, `acquire.ingest(kb, family_id, path)`.
4. Everything still missing shows up in `family.gaps()` as a question.

## Status

`UWS6121E` (UNISOC, Cortex-A5, LTE Cat.1) ships as a `PARTIAL` record. Its
facts are all `CITED` from a customer product-definition spreadsheet, which
states what the product should contain and is *not* evidence about the die. No
SDK was available, so it emits complete application logic and a stubbed port —
and says exactly that in `PROVENANCE.md`.
