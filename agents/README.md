# agents

Turns a plain-language description of a board into a validated brief the code generator can consume — asking about anything it would otherwise have to guess.

## The division of labour

A language model is a good reader and a poor authority, so it is used only as a reader:

```
free text ──▶ extractor.py ──▶ ExtractionResult ──▶ normalizer.py ──▶ PCBAnalysis + FirmwareSpec ──▶ codegen
              (the model)       (a draft, untrusted)   (ordinary code)     (validated)
```

| Module | Job | Calls the model? |
|---|---|---|
| `schemas.py` | The contract between the two halves | no |
| `extractor.py` | Reads prose into `ExtractionResult` | **yes** — the only place |
| `normalizer.py` | Decides what must be asked; validates the draft into a brief | no |
| `interview.py` | Runs the ask-until-complete loop | no |

**What must be asked is decided by the pipeline, not the model.** `required_questions()` is derived from what `generate_firmware()` actually consumes, so a model that forgets to ask about the loop period cannot cause a silent default. The model's own questions are appended *after* the required ones.

**The draft is validated, not trusted.** It goes through the same `core/` models as a hand-written config: a hallucinated interface, an unknown board, or two I2C devices at one address raise instead of reaching the compiler.

## What it asks about

These were all hardcoded before — the defaults were chosen by whoever wrote the templates, not by the person whose board it is:

| Question | Why it matters |
|---|---|
| Pin(s) per sensor | The wrong port bit means the sensor is never read and nothing reports an error |
| Sample period per sensor | Drives power draw and data freshness; also bounded by the part's datasheet |
| Is this reading critical? | Critical readings get retries and an explicit error on the wire; others are logged and skipped |
| Loop period | The single biggest thing the tool would otherwise assume (it was 2000 ms, decided by nobody) |
| Baud rate | Whatever reads the output must match exactly, and not every rate is reachable from a given clock |
| MCU / clock | Only when the board name does not already determine them |

Every question carries a `why`. A question a user cannot see the point of is a question they will answer badly.

## Physical limits are checked before code is generated

`check_timing()` compares the requested rates against datasheet minimums:

```
> read the DHT22 every 500 ms
These cannot all be true at once:
  - DHT22 is set to 500 ms but cannot be read faster than every 2000 ms --
    it would return the previous reading without reporting an error.
No firmware generated.
```

That is the failure mode worth catching: polling a DHT22 too fast does not error, it silently repeats the last measurement.

## Unsupported requests are named, not absorbed

If the description asks for something out of scope — a non-AVR part, wireless, MQTT with QoS levels — it comes back in `unsupported` and is printed before anything is built, rather than quietly dropped.

## Running it

```bash
export ANTHROPIC_API_KEY=...     # or: ant auth login
./.venv/Scripts/python.exe cli.py chat -o build/
```

Uses `claude-sonnet-5` with structured outputs (the JSON schema is generated from the Pydantic model, so it cannot drift from what the response is parsed into). Roughly $0.05–0.20 per interview at current rates.

## Testing

`tests/test_agents.py` covers all 24 cases **without calling the API** — the model sits behind an `ExtractorBackend` protocol, so tests inject a canned response. Every deterministic decision (which questions get asked, what validates, which timings conflict) is tested offline and for free.

**Not yet exercised against the live API.** There is no credential on the machine this was built on, so the request shape — model id, structured-output schema, response parsing — is written to the current SDK docs but has not made a real round trip. The first real run is the test that matters.
