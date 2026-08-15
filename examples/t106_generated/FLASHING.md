# Flashing

**This tool does not flash anything, and does not need the flashing tool
to have been available.** An engineer flashes this image.

## Why it is left to you

A generator cannot confirm which board is on the other end of a cable.
Writing to the wrong one is not recoverable from here, and a successful
flash proves only that bytes were transferred. It replaces whatever was
on the part, and it
says nothing about whether the pins in this firmware match the board.

That last point is the one worth keeping: a device that boots after
flashing has demonstrated nothing about the wiring assumptions inside it.

## What is known about the tool

- Nothing. No flashing tool was recorded for this family, which is fine:
  the vendor's own utility is what you would use in any case.

## Before you flash

1. Confirm the pin assignments in `PROVENANCE.md` against the schematic.
   They came from an interview and nothing has checked them.
2. Confirm this is a `UWS6121EG` and not a variant with different memory.
3. Have a way back. Flashing replaces whatever is on the part.
