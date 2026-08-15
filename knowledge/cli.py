"""Command line for the vendor-SDK knowledge base.

Deliberately its own entry point rather than subcommands bolted onto `cli.py`,
for the same reason the package is separate: nothing about adding a vendor
family should be able to disturb the Zephyr path.

    python -m knowledge.cli list
    python -m knowledge.cli show UWS6121EG
    python -m knowledge.cli find UWS6121EG          # where to look for the SDK
    python -m knowledge.cli ingest UWS6121E <path>  # read an SDK on this machine
    python -m knowledge.cli ask <board.json>        # what is still unanswered
    python -m knowledge.cli emit <board.json> <out> # write the firmware project
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from knowledge.acquire import ingest, search_plan
from knowledge.base import KnowledgeBase
from knowledge.board import BoardFacts, Device
from knowledge.emit import EmitError, emit
from knowledge.questions import advisory, blocking, board_questions, family_questions, unknown_family


def load_board(path: Path) -> BoardFacts:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    devices = [Device(**d) for d in raw.pop("devices", [])]
    return BoardFacts(devices=devices, **raw)


def cmd_list(args) -> int:
    families = KnowledgeBase(args.base).families()
    if not families:
        print("No families. Run `python -m knowledge.seed` to write the shipped records.")
        return 0
    for family in families:
        print(family.describe())
        print()
    return 0


def cmd_show(args) -> int:
    kb = KnowledgeBase(args.base)
    family = kb.resolve(args.mcu)
    if family is None:
        print(f"{args.mcu}: nothing in this base matches.\n")
        print("That is a question, not a dead end:")
        for item in unknown_family(args.mcu):
            print(f"  - [{item.field}] {item.question}")
        return 1

    print(family.describe())
    if family.notes:
        print(f"\n{family.notes}")
    print("\nFacts:")
    for claim in sorted(family.facts.values(), key=lambda c: c.predicate):
        print(f"  {claim.describe()}")
    questions = family_questions(family)
    if questions:
        print("\nStill needed:")
        for item in questions:
            print(f"  - [{item.field}] {item.question}")
    return 0


def cmd_find(args) -> int:
    kb = KnowledgeBase(args.base)
    family = kb.resolve(args.mcu)
    plan = search_plan(args.mcu, family.vendor if family else args.vendor)
    print(plan.describe())
    print(
        "\nRun these yourself, or have the agent run them. A page you find is a\n"
        "lead (cited), not an SDK -- record it with acquire.record_lead(). Only\n"
        "`ingest` on an unpacked tree produces symbols the port can call."
    )
    return 0


def cmd_ingest(args) -> int:
    kb = KnowledgeBase(args.base)
    try:
        family = ingest(kb, args.family, args.path, name=args.name, version=args.version)
    except (LookupError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(family.describe())
    return 0


def cmd_ask(args) -> int:
    kb = KnowledgeBase(args.base)
    board = load_board(args.board)
    family = kb.resolve(board.mcu)
    questions = board_questions(board, family)

    hard, soft = blocking(questions), advisory(questions)
    print(f"{len(hard)} blocking, {len(soft)} advisory\n")
    for item in hard:
        print(f"! {item.field}")
        print(f"    {item.question}")
        print(f"    fails as: {item.failure}")
    for item in soft:
        default = f" [default {item.default}]" if item.default else ""
        print(f"- {item.field}{default}")
        print(f"    {item.question}")
    return 1 if hard else 0


def cmd_emit(args) -> int:
    kb = KnowledgeBase(args.base)
    board = load_board(args.board)
    family = kb.resolve(board.mcu)

    try:
        project = emit(board, family)
    except EmitError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    out = Path(args.out)
    for relative, content in sorted(project.files.items()):
        path = out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"  {relative}")
    print(f"\n{project.count} files -> {out}")
    print(f"{len(project.review)} porting operations need a human; see PROVENANCE.md.")
    print("Nothing was compiled and nothing was flashed.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="knowledge", description=__doc__)
    parser.add_argument("--base", default=None, help="knowledge base directory")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="every family on record").set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="what is known about a part")
    show.add_argument("mcu")
    show.set_defaults(func=cmd_show)

    find = sub.add_parser("find", help="where to look for the SDK")
    find.add_argument("mcu")
    find.add_argument("--vendor", default="")
    find.set_defaults(func=cmd_find)

    ing = sub.add_parser("ingest", help="read an SDK tree on this machine")
    ing.add_argument("family")
    ing.add_argument("path")
    ing.add_argument("--name", default="")
    ing.add_argument("--version", default="")
    ing.set_defaults(func=cmd_ingest)

    ask = sub.add_parser("ask", help="what is still unanswered for a board")
    ask.add_argument("board", type=Path)
    ask.set_defaults(func=cmd_ask)

    out = sub.add_parser("emit", help="write the firmware project")
    out.add_argument("board", type=Path)
    out.add_argument("out")
    out.set_defaults(func=cmd_emit)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
