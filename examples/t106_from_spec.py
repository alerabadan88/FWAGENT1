"""Run the knowledge base against a real customer specification.

    python -m examples.t106_from_spec "path/to/T106_Proudct specification V0.2-20260508.xlsx"

The point of doing it from the document rather than from a hand-written device
list is that the *gap* becomes measurable. The specification names what the
product contains. It assigns no pin anywhere. Reading it mechanically shows
exactly how far a product-definition sheet gets you, which is further than
nothing and nowhere near enough.

Every device produced here carries the row it came from, and every row that
declares something this script does not model is printed too. A reader that
silently drops rows would be the same silent-failure pattern the rest of the
project exists to avoid: the output would look complete.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from knowledge.base import KnowledgeBase
from knowledge.board import BoardFacts, Device
from knowledge.emit import EmitError, emit
from knowledge.questions import advisory, blocking, board_questions, family_questions

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"

#: Values that mean "this product does not have one".
ABSENT = {"/", "no", "n/a", "none", ""}


@dataclass(frozen=True)
class Row:
    number: int
    cells: tuple[str, ...]

    def cell(self, index: int) -> str:
        return self.cells[index] if index < len(self.cells) else ""

    @property
    def item(self) -> str:
        return f"{self.cell(1)} {self.cell(2)}".strip()

    @property
    def spec(self) -> str:
        return self.cell(3)

    @property
    def remark(self) -> str:
        return self.cell(4)

    @property
    def declares_something(self) -> bool:
        return self.spec.strip().lower() not in ABSENT


# --- reading the workbook ---------------------------------------------------


def read_sheet(path: Path, wanted: str) -> list[Row]:
    """One sheet as rows. Standard library only -- an .xlsx is a zip of XML."""
    with zipfile.ZipFile(path) as archive:
        shared = _shared_strings(archive)
        part = _sheet_part(archive, wanted)
        if part is None:
            raise SystemExit(f"{path.name} has no sheet named {wanted!r}")

        root = ET.fromstring(archive.read(part))
        rows = []
        for element in root.find(f"{NS}sheetData").findall(f"{NS}row"):
            cells: dict[int, str] = {}
            for cell in element.findall(f"{NS}c"):
                text = _cell_text(cell, shared)
                if text:
                    cells[_column(cell.get("r"))] = text
            if cells:
                width = max(cells) + 1
                rows.append(Row(
                    number=int(element.get("r")),
                    cells=tuple(cells.get(i, "") for i in range(width)),
                ))
        return rows


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(t.text or "" for t in si.iter(f"{NS}t")) for si in root.findall(f"{NS}si")]


def _sheet_part(archive: zipfile.ZipFile, wanted: str) -> str | None:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels.findall(f"{PKG}Relationship")}
    for sheet in workbook.find(f"{NS}sheets"):
        if sheet.get("name") == wanted:
            target = targets[sheet.get(f"{REL}id")]
            return target if target.startswith("xl/") else "xl/" + target.lstrip("/")
    return None


def _cell_text(cell, shared: list[str]) -> str:
    kind = cell.get("t")
    value = cell.find(f"{NS}v")
    if kind == "s" and value is not None:
        return shared[int(value.text)].strip()
    if kind == "inlineStr":
        inline = cell.find(f"{NS}is")
        return "".join(t.text or "" for t in inline.iter(f"{NS}t")).strip() if inline is not None else ""
    return (value.text or "").strip() if value is not None else ""


def _column(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for char in letters:
        n = n * 26 + (ord(char) - 64)
    return n - 1


# --- the document to a board ------------------------------------------------
# Matched on the English Item column, which is stable across revisions of this
# template. Each rule states what it produces so an unmatched row is visible.

_RULES = [
    ("Front Touch Keys", "button", lambda row: [
        Device(name="home key", kind="button", interface="gpio",
               role=f"{row.spec} / {row.remark}".strip(" /")),
    ]),
    ("Indicating LED", "tri-colour indicator", lambda row: [
        Device(name="led red", kind="led", interface="gpio", role="power / battery"),
        Device(name="led blue", kind="led", interface="gpio", role="gps fix"),
        Device(name="led green", kind="led", interface="gpio", role="network / server"),
    ]),
    ("G-Sensor", "accelerometer", lambda row: [
        Device(name=row.remark or "g-sensor", kind="imu", interface="i2c",
               role="motion detection"),
    ]),
    ("GPS", "gnss receiver", lambda row: [
        Device(name=(row.remark.split()[0] if row.remark else "gnss"),
               kind="gnss", interface="uart", role="positioning"),
    ]),
]


def devices_from(rows: list[Row]) -> tuple[list[Device], list[tuple[int, str]], list[tuple[int, str]]]:
    """Devices, the rows they came from, and every declaring row not modelled."""
    devices: list[Device] = []
    sourced: list[tuple[int, str]] = []
    matched_rows: set[int] = set()

    for row in rows:
        for needle, what, build in _RULES:
            # Matched on the whole Item cell, not a substring of it. Substring
            # matching made "GPS antenna" (r100) match the "GPS" rule and emit a
            # second, non-existent receiver -- an antenna is not a peripheral the
            # firmware talks to. Loose matching invents hardware.
            if row.cell(1).strip().lower() == needle.lower() and row.declares_something:
                made = build(row)
                devices.extend(made)
                sourced.append((row.number, f"{row.item} = {row.spec} -> {what} ({len(made)})"))
                matched_rows.add(row.number)

    skipped = [
        (row.number, f"{row.item} = {row.spec}")
        for row in rows
        if row.declares_something and row.number not in matched_rows and row.item
    ]
    return devices, sourced, skipped


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    path = Path(argv[1])
    if not path.is_file():
        print(f"not found: {path}")
        return 2

    rows = read_sheet(path, "硬件定义")
    mcu = next((r.spec for r in rows if "Chipset" in r.item), "")
    name = next((r.spec for r in rows if "PCBA name" in r.item), path.stem)
    devices, sourced, skipped = devices_from(rows)

    print("=" * 74)
    print(f"Read {path.name}: sheet 硬件定义, {len(rows)} rows")
    print("=" * 74)
    print(f"MCU  : {mcu or '(not stated)'}")
    print(f"Board: {name}")
    print(f"\nDevices derived, with the row each came from:")
    for number, what in sourced:
        print(f"  r{number:<4} {what}")

    print(f"\n{len(skipped)} further rows declare something this script does not model.")
    print("Listed so nothing is dropped silently:")
    for number, what in skipped[:14]:
        print(f"  r{number:<4} {what}")
    if len(skipped) > 14:
        print(f"  ... and {len(skipped) - 14} more")

    board = BoardFacts(board_name=name, mcu=mcu, devices=devices)

    print()
    print("=" * 74)
    print("What the knowledge base knows about the part")
    print("=" * 74)
    family = KnowledgeBase().resolve(mcu)
    if family is None:
        print(f"{mcu}: no record.")
    else:
        print(family.describe())
        for item in family_questions(family):
            print(f"  ? {item.field}: {item.question}")

    print()
    print("=" * 74)
    print("What the document does NOT say, and the firmware needs")
    print("=" * 74)
    questions = board_questions(board, family)
    hard, soft = blocking(questions), advisory(questions)
    print(f"{len(hard)} blocking (silent failure), {len(soft)} advisory (loud failure)\n")
    for item in hard:
        print(f"  ! {item.field}")
        print(f"      {item.question}")
        print(f"      fails as: {item.failure}")
    for item in soft:
        print(f"  - {item.field}"
              + (f"  [default {item.default}]" if item.default else ""))

    print()
    print("=" * 74)
    print("Generation")
    print("=" * 74)
    try:
        project = emit(board, family)
        print(f"generated {project.count} files")
    except EmitError as exc:
        print(str(exc).splitlines()[0])
        print("\nThat refusal is the answer to \"is this specification enough?\"")
        print("It is enough to identify every device. It is not enough to wire one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
