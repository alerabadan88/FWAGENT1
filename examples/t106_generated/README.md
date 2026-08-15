# T106 Pet Locator firmware

Report position over the cellular network and show power, GPS and network state on a tri-colour LED.

Generated 2026-08-15 by fw-automation-agent.

## Layout

```
app/     application logic -- complete, and independent of the vendor SDK
port/    the porting layer -- fourteen functions, the only vendor-specific code
```

## State

- Target: `UWS6121EG` (family `UWS6121E`, partial)
- Files: 13
- Port operations needing a human: 14

## What to do next

1. Read `PROVENANCE.md`. It lists every value that came from an answer
   rather than from an artifact, and those are the ones that fail quietly.
2. Fill in `port/hal_uws6121e.c`.
   It contains no logic; it maps fourteen operations onto SDK calls.
3. Build with your vendor toolchain. This generator did not compile
   anything, so nothing here is known to build.
4. Flash it yourself -- see `FLASHING.md`.

## What this is not

Nothing here has run on hardware. The application logic is written to be
correct and is not proof of anything; the board facts it rests on came
from a person and could not be checked by this tool.
