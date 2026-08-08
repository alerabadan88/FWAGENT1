# Security measures — atmega328p firmware 0.1.0 (765e0d32)

> **This is evidence, not a compliance claim.** The EU Cyber Resilience Act
> (Regulation (EU) 2024/2847) obliges a *manufacturer* — risk assessment,
> technical documentation, a vulnerability disclosure policy, a support
> period, conformity assessment, CE marking. A generator cannot confer any
> of those. What follows is what this build does and does not do, so a
> human can judge the rest.

Generated 2026-08-08T19:26:08+00:00.

## Implemented

### Watchdog recovery

The watchdog is armed at startup and fed each loop, so a device whose loop wedges resets instead of going quiet. The post-reset trap is handled: the WDT is disabled from .init3 before anything slow runs, which is what stops a watchdog reset becoming a reset loop.

- *Speaks to:* Annex I Part I(2)(h)/(m): availability, and limiting incident impact
- *Does not cover:* It recovers a hung loop; it does not detect a loop that keeps running while producing wrong results. It is also NOT verified by simulation: GDB's AVR simulator does not implement the WDR instruction, so compilation is the only evidence for this one short of real hardware.

### Serial receiver disabled

Only the transmitter is enabled, so the firmware exposes no serial input to accept commands on.

- *Speaks to:* Annex I Part I(2)(l): limit attack surfaces
- *Does not cover:* The bootloader still accepts a firmware write over the same pins. Preventing that is a fuse and lock-bit decision, made when the device is programmed, not in this firmware.

### Firmware identity on the wire

Version and a build id derived from the configuration are printed at boot, so a unit on a bench can be matched to the build that produced it -- the prerequisite for knowing whether it carries a fix.

- *Speaks to:* Annex I Part II(1): identify and document components
- *Does not cover:* The identity is reported, not attested; nothing signs it.

### Unexpected reset is reported

A boot after a watchdog reset says so, because a device rebooting in a loop looks identical to a working one from outside.

- *Speaks to:* Annex I Part I(2)(j): record relevant internal activity
- *Does not cover:* It is printed on the serial line, not stored. A device nobody is listening to keeps no record.

### No dynamic allocation

No heap is used, so there is no heap to exhaust or corrupt, and memory use is bounded at link time.

- *Speaks to:* Annex I Part I(2)(e): protect integrity
- *Does not cover:* Stack depth is still unbounded by anything but review.

### Build fits with headroom

Measured from the linked image: 5.98 % flash, 7.37 % RAM.

- *Speaks to:* Annex I Part I(2)(h): availability under expected load
- *Does not cover:* Static sizes only; it says nothing about runtime stack growth.

### Secrets absent from the image

No credential-shaped literal was found in the generated sources. The firmware transmits only sensor readings and needs no secret.

- *Speaks to:* Annex I Part I(2)(d): protect confidentiality
- *Does not cover:* A textual scan of generated code. It cannot speak for anything hand-added afterwards.

## Not implemented

### Secure update mechanism

Not implemented. Updates go through the stock serial bootloader, which authenticates nothing: anyone with physical access to the port can write different firmware.

- *Would speak to:* Annex I Part I(2)(c)/Part II(8): secure and, where applicable, automatic updates
- *What closing it needs:* Closing this needs a signed bootloader, which this generator does not produce.

### Data-in-transit protection

Not implemented. Readings go out over plain UART, readable and modifiable by anything on those pins.

- *Would speak to:* Annex I Part I(2)(e): protect integrity of transmitted data
- *What closing it needs:* Appropriate for a wired sensor on a closed board; not appropriate if the link leaves the enclosure.

## Outside anything a generator can do

These are obligations on the manufacturer. They are listed so they are not
mistaken for handled:

- Risk assessment for the intended use, and the technical documentation that must accompany it (Article 13, Annex VII)
- A coordinated vulnerability disclosure policy and a contact point for reports (Annex I Part II(5))
- A defined support period, and the process for shipping security updates during it (Article 13(8), Annex I Part II)
- Reporting actively exploited vulnerabilities and severe incidents to ENISA/CSIRT within the required deadlines (Article 14)
- Conformity assessment and CE marking (Articles 32, 30)
- Fuse and lock-bit settings, which are applied when the device is programmed and are outside anything this firmware controls

## Artefact digests (SHA-256)

| File | Digest |
|---|---|
| `firmware.elf` | `8526699111ea128a39acdd1c70dfe498f25b12fd1fbbd18477bfbf08bd54d110` |
| `firmware.hex` | `d2c948b54d644d89446de1b4afc1b263122cceb6b35a7c16fb8ab51227526ea2` |

Compare these against what is on the device to confirm the shipped image
is the one built from these sources.
