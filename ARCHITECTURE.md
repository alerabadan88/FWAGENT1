# The approach

For someone joining the project. This does not describe what the code does —
the docstrings do that — but **why it is shaped this way**, which is the part
you cannot get from reading it.

## The real problem

A firmware generator that takes a description and returns firmware is lying by
omission. The facts it needs are not in the description. They are properties of
a physical object: which pin the sensor is on, which way round the connector
goes, what the analog input is referenced against, what the ADDR pin selects.

And there is an asymmetry that decides the whole architecture:

> A wrong hardware fact produces firmware that **compiles, boots, and reports
> plausible numbers**. No test catches it, because the test is generated from
> the same assumption.

Next to that, a syntax error is a gift. So the entire system is organised
around one question: *where did this value come from?*

## Three categories of fact

| | Source | Automatable |
|---|---|---|
| **Derivable** | compiler headers, Zephyr bindings, the SoC devicetree | yes, completely |
| **About this board** | a netlist, or a person | no: it is in no corpus |
| **Product decisions** | the customer | no: a choice, not a fact |

Category 2 is the hard one, and it is where silent failure lives. No RAG, no
datasheet and no model knows which pin you soldered the DHT22 to.

## Decision 1 — Uncertainty is enumerated in code, not in a prompt

`agents/uncertainty.py`

The obvious design is to tell a model *"ask when you are unsure"*. It does not
work. Filling gaps plausibly is what a language model does by default; asking
it to notice its own gaps reports **fewer** than exist, not more.

So the list of unknowns is derived in ordinary code from what the generator
actually consumes. A model that forgets to wonder about the clock cannot cause
a 16× timing error, because `scan_draft` raises the question anyway.

What is left for the model is what it is good at: phrasing the question and
reading the answer back.

### Blocking versus advisory

The split is **not** about importance. It is about *how a wrong answer fails*:

- Fails **loudly** — build error, NACK, visible garbage on the serial line →
  a default is fine. Someone will notice.
- Fails **silently** — plausible wrong readings, a sensor that is never read,
  timing off by a constant factor → **no default**. It blocks, and the person
  holding the board answers.

Every entry states its failure mode in the `failure` field, so the
classification can be argued with rather than believed.

## Decision 2 — Evidence states, not a "verified" boolean

`core/evidence.py`, `services/verifier.py`

There is no `verified: bool` anyone can set to `True`. "Verified" is not a
property a value has: it is a *relationship* between the value and an artifact
somebody can go and read.

| State | What it lets you conclude |
|---|---|
| `authoritative` | read out of a versioned artifact, with an exact locator |
| `executed` | proven by running something. Only the property tested |
| `cited` | found by looking outside. Real evidence, **not** authority |
| `none` | somebody asserted it |

**There is no "reviewed" state, and that is deliberate.** Re-reading a claim
creates no evidence. If a register address came from a model's recollection, a
second model asked whether it looks right is querying the distribution that
produced it: their errors are correlated. Two passes multiply confidence
without multiplying evidence, and they yield a document saying "verified" with
nothing external behind it — worse than an honest `none`, because it can no
longer be spotted downstream.

Promotion happens only by **finding an artifact**. Never by deliberating harder.

Practical consequences:

- A citation requires a retrieval date (enforced in `__post_init__`): an
  external page changes or vanishes, and an undated citation cannot be
  re-checked.
- A source with no pinned version is refused as an authority. `ameba-rtos-d`
  will not do; `ameba-rtos-d@a1b2c3d` will.
- Evidence cannot be silently weakened: replacing a checked fact with a
  recollection raises.
- A **contradiction** (the artifact says something else) stops the build. It is
  not a warning. It travels as a flag on the exception, not inferred from the
  message text.

## Decision 3 — Zephyr, and why bare metal was set aside

`codegen/zephyr/`

Generating register writes means owning the register map for every part, with
nothing to check it against. It was built that way first — the AVR bare-metal
drivers in `codegen/templates/drivers/` work — but it does not scale: every new
family is new register maps, equally uncheckable.

With Zephyr you **stop generating drivers**. A node saying *"there is an
aosong,dht on this pin"* hands the work to code written by someone with the
datasheet open. ~1500 lines of driver templates become ~40 of devicetree, and
the register-map problem is not managed: it disappears.

The devicetree is also the machine-readable description of *your specific
board* — exactly category 2 — and it works the same for a custom PCBA as for a
development board.

And it fits by itself: the questions the enumerator already asked (which pin,
which pull, which active level) turn out to be the fields of a devicetree node.
`aosong,dht` requires `dio-gpios`; we were already asking.

### Resolving a part: three outcomes, and no fourth

- **`exact`** — a binding named for the part. Still only a *candidate*:
  Zephyr's convention is "filename == compatible", and a convention is not an
  artifact. `ZephyrBindingVerifier` reads the YAML's own `compatible:` field.
- **`substitute`** — no binding for the part, but a generic driver speaks its
  protocol. A NEO-6M has no binding; `gnss-nmea-generic` does. What you give up
  is written into the generated README: position and time yes, UBX no.
- **`none`** — refused. Picking the closest-looking compatible binds a driver
  for a *different* device, which initialises cleanly and reports wrong numbers.

## Decision 4 — Check against the artifact the build will use

`codegen/zephyr/binding_fetch.py`, `codegen/zephyr/soc_facts.py`

If a local Zephyr checkout exists, it is read in preference to the network:
that is the artifact the build will use, and verifying a different copy
establishes nothing about the build.

This principle fixed one concrete and embarrassing defect. The
peripheral-contention check asked `core/device_catalog.py`, which answers by
invoking avr-gcc. Asked about an nRF52840 it did not abstain: it answered **one
UART**, which is false — the part has two. And `unsupported()` declared it
unsupported. A confidently wrong answer is worse than none, because nothing
downstream treats it as suspect. Counts now come from the SoC's own `.dtsi`:
nRF52840 → 2, STM32F411 → 3, ESP32-S3 → 3.

## Decision 5 — Vendor-specific things are named, not smoothed over

`codegen/zephyr/pinctrl.py`

Pin muxing is where devicetree stops being vendor-neutral. Nordic writes
`NRF_PSEL(UART_TX, 0, 6)`; STM32 refers to pre-generated symbols like
`<&usart1_tx_pa9>`; there is no common spelling and no way to derive one. So
that module knows a couple of dialects and refuses for the rest, with a message
saying what to write by hand.

Same file records *implied* peripherals: on Nordic, GPIO interrupts are served
by GPIOTE, a separate node that nothing in a button's own definition mentions.

A guess in either place would produce a board that builds, boots, and prints
out of a pad nobody connected — the failure this project exists to avoid,
arriving by a new route.

## Decision 6 — What the collected data can and cannot teach

`webapp/store.py`

The corpus is an append-only log of what was asked, what came back, and whether
the port built. It **cannot** teach a model which pin your DHT22 is on: that is
a property of one physical board, appears in no corpus, and a model predicting
it would be guessing with extra steps.

What it can teach:

- **Which defaults are wrong.** A default overridden nine times in ten is not a
  default, it is a bad guess with a nice interface. It caught one on its first
  real data point: `supply_voltage` defaults to 5.0 and an nRF52840 board
  answered 3.3.
- **Which questions go unanswered**, meaning they are badly worded or aimed at
  the wrong person.
- **Which parts and SoCs recur**, which is the verify-by-hand backlog in order.
- **Priors worth *suggesting*** — never assuming. The blocking/advisory split
  keeps that enforced regardless of what a future model proposes.

Whether a port builds is recorded as `"unknown"` rather than omitted, so a
later `west build` can fill it in instead of the corpus quietly implying
success. That field is the only supervision signal in the system.

## Repository map

```
core/
  evidence.py        evidence states and the claim ledger
  device_catalog.py  AVR part facts from avr-libc headers (414 parts)
  hardware_model.py  validated models; raises if the hardware is impossible
  netlist_parser.py  KiCad -> connections (questions you do not have to ask)
agents/
  uncertainty.py     THE DETERMINISTIC ENUMERATOR — start reading here
  normalizer.py      draft + answers -> validated brief, or refusal
  interview.py       the interview loop
  part_lookup.py     model-described parts, always marked unverified
codegen/
  zephyr/            board port: bindings, properties, SoC facts, pinctrl
  templates/zephyr/  the port templates
  templates/drivers/ AVR bare-metal drivers (earlier branch, working)
services/
  verifier.py        fetch the artifact and diff
  zephyr_verifier.py confirm a compatible against its own YAML
  security.py        SBOM and CRA measures, never claiming compliance
webapp/
  api.py             describe -> questions -> answers -> zip
  store.py           session persistence and the corpus
  static/index.html  the front end
```

## What is NOT proven

Stated here and repeated in every generated artifact:

- **A generated board port builds.** nRF52840 with a DHT22 and a button,
  Zephyr v4.4.2, 34288 B of flash, with Zephyr's `dht_api` in the binary.
- **Nothing has ever been on hardware.** No board has been connected at any
  point, so pin numbers, active levels and pulls are only as good as the
  answers they came from.
- AVR timing-critical drivers are simulator-verified only, and the watchdog is
  not even that — GDB's AVR simulator does not implement `WDR`.

If the system ever claims more than this, that is a defect. The project's rule
is that an invented number is worse than a declared gap.

## Where to go next

1. Run `west build` from inside the flow and record the result in the corpus.
   That closes the loop and creates the only supervision signal there is.
2. Netlist → devicetree. Every connection read from the schematic is a question
   nobody has to answer.
3. The active-search agent: when there is no artifact, search, and record what
   turns up as `cited` — never as `authoritative`.
4. Persistent sessions in a real store (they are files today, on purpose).
