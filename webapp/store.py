"""Persistence, and the corpus that comes out of it.

Two separate things live here, and conflating them would be a mistake.

**Sessions** are operational state: a board someone is working on, so closing
the tab does not lose an interview. Written as one JSON file per session.

**The corpus** is what the product learns from. It is an append-only log of
what was asked, what came back, and -- crucially -- whether the port built.
That last field is the only supervision signal in the whole system; without it
the log is a pile of chat.

What this corpus can and cannot teach
-------------------------------------
It cannot teach a model which pin your DHT22 is on. That is a property of one
physical board, it appears in no corpus, and a model that predicted it would be
guessing with extra steps -- the exact failure this project is built to avoid.

What it can teach is narrower and genuinely useful:

* **Which defaults are wrong.** A default that gets overridden nine times out
  of ten is not a default, it is a bad guess with a nice interface. This is
  measurable here and nowhere else.
* **Which questions never get answered**, meaning they are badly worded or
  the person asking is not the person who knows.
* **Which parts and SoCs actually recur**, which is what should be verified
  by hand next.
* **Which refusals fire most**, which is the backlog in priority order.
* **Priors for suggestion** -- "boards with this SoC usually run at 64 MHz" is
  a reasonable thing to offer as a pre-filled answer someone confirms. It is
  not a reasonable thing to assume, and the blocking/advisory split keeps that
  distinction enforced no matter what any future model suggests.

Every record carries the schema version so a later reader can tell what it is
looking at, and no record is ever rewritten.
"""

from __future__ import annotations

import json
import os
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = 1

DATA_DIR = Path(os.environ.get("FWAGENT_DATA", Path.home() / ".fw-automation-agent"))
SESSIONS_DIR = DATA_DIR / "sessions"
CORPUS = DATA_DIR / "corpus.jsonl"

_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


# --- Sessions -------------------------------------------------------------------


def save_session(session_id: str, payload: dict[str, Any]) -> Path:
    """Write a session so a closed tab does not lose an interview."""
    _ensure()
    path = SESSIONS_DIR / f"{session_id}.json"
    body = {"schema": SCHEMA, "saved": _now(), **payload}
    # Written via a temporary file: a half-written session that still parses is
    # worse than one that is obviously absent.
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(body, indent=1, default=str), encoding="utf-8")
    temporary.replace(path)
    return path


def load_session(session_id: str) -> dict[str, Any] | None:
    path = SESSIONS_DIR / f"{session_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def list_sessions(limit: int = 50) -> list[dict[str, Any]]:
    _ensure()
    files = sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict[str, Any]] = []
    for path in files[:limit]:
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append({
            "session": path.stem,
            "board_name": body.get("board_name", ""),
            "mcu": body.get("mcu", ""),
            "sensors": len(body.get("sensors", [])),
            "answered": len(body.get("answers", {})),
            "saved": body.get("saved", ""),
            "generated": bool(body.get("generated_files")),
        })
    return out


def delete_session(session_id: str) -> bool:
    path = SESSIONS_DIR / f"{session_id}.json"
    if path.is_file():
        path.unlink()
        return True
    return False


# --- Corpus ---------------------------------------------------------------------


def record(event: str, session_id: str, **fields: Any) -> None:
    """Append one event. Never rewrites, never fails the request it came from.

    A corpus write that breaks a user's build would be a bad trade, so failures
    here are swallowed -- but only here, and only for this.
    """
    line = json.dumps(
        {"schema": SCHEMA, "at": _now(), "event": event, "session": session_id, **fields},
        default=str,
    )
    try:
        _ensure()
        with _LOCK:
            with CORPUS.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except OSError:
        pass


def read_corpus(limit: int | None = None) -> list[dict[str, Any]]:
    if not CORPUS.is_file():
        return []
    records: list[dict[str, Any]] = []
    with CORPUS.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records[-limit:] if limit else records


def corpus_stats() -> dict[str, Any]:
    """What the collected data actually supports saying.

    Deliberately does not report anything that would need a model to interpret.
    These are counts, and counts are what this file can honestly produce.
    """
    records = read_corpus()
    if not records:
        return {
            "records": 0,
            "note": "Nothing collected yet. The corpus fills as boards are worked on.",
        }

    answers = [r for r in records if r["event"] == "answer"]
    builds = [r for r in records if r["event"] == "generated"]

    overridden = Counter()
    accepted = Counter()
    for record_ in answers:
        for field, value in (record_.get("answers") or {}).items():
            default = (record_.get("defaults") or {}).get(field)
            if default is None:
                continue
            (overridden if str(value) != str(default) else accepted)[field] += 1

    # A default overridden more often than accepted is a bad guess, and this
    # is the only place in the system that can notice.
    suspect = sorted(
        (
            {
                "field": field,
                "overridden": count,
                "accepted": accepted.get(field, 0),
                "verdict": "the default is probably wrong",
            }
            for field, count in overridden.items()
            if count > accepted.get(field, 0)
        ),
        key=lambda item: -item["overridden"],
    )

    return {
        "records": len(records),
        "sessions": len({r["session"] for r in records}),
        "boards_generated": len(builds),
        "parts_seen": Counter(
            part for r in records for part in (r.get("parts") or [])
        ).most_common(15),
        "socs_seen": Counter(
            r["soc"] for r in records if r.get("soc")
        ).most_common(10),
        "questions_asked": Counter(
            field for r in records for field in (r.get("asked") or [])
        ).most_common(20),
        "defaults_overridden": suspect,
        "refusals": Counter(
            reason[:120] for r in records for reason in (r.get("refusals") or [])
        ).most_common(10),
        "note": (
            "Counts only. This data cannot teach a model which pin your sensor "
            "is on -- that is a fact about one board and appears in no corpus. "
            "It can show which defaults are wrong, which questions go "
            "unanswered, and which parts to verify by hand next."
        ),
    }
