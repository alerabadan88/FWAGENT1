# Example inputs

Two boards, in the input formats the tool actually reads.

## arduino-uno/

ATmega328P — DHT22, HC-SR04, and an LDR. The case everything else is tested against.

| File | What it is |
|---|---|
| `board.kicad.net` | A real KiCad netlist export. **The strongest input**: it names the MCU pin on each connection, so nothing about the wiring is asked or assumed. |
| `config.json` | The same board stated by hand. Uses Arduino silkscreen labels, which is why it carries `"board": "Arduino Uno"`. |
| `description.txt` | Free text, for the interview agent. |

```bash
fw-agent schematic examples/arduino-uno/board.kicad.net --build
fw-agent build     examples/arduino-uno/config.json
fw-agent verify    examples/arduino-uno/config.json
```

### What the netlist reports rather than absorbs

```
PASSIVE   PC0 is wired to passives only (R2 (LDR 5528), R3 (10k)). If that is
          an analog sensor, its type has to be stated -- a divider cannot be
          identified from a netlist.
NOTE      clock frequency and supply voltage are not in a netlist
```

The LDR is a resistive divider, and in a netlist that is indistinguishable from
any other passive network. `PC0` is plainly in use, so saying nothing would read
as "unused", which is false. It is reported instead.

The same goes for the crystal frequency and supply voltage: a netlist does not
carry them, so they are shown as defaults to confirm rather than facts.

## esp32/

ESP32-WROOM-32. It parses fine and is then **rejected**:

```
error: MCU family 'ESP32' is not supported by the AVR generator yet
```

Kept deliberately. There is no toolchain here that can compile for an ESP32, so
generating code for one would produce something that looks like firmware with
no evidence that it is. This is what an honest refusal looks like.
