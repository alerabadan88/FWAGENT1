"""Where family records live, and how a part number finds one.

Records are one JSON file per family, in a directory, written by temp-file
rename so a crash mid-write leaves the previous record intact rather than a
truncated one. The format is plain and hand-editable on purpose: when an
engineer knows something the extractor could not read, correcting the record
should not require running anything.

Resolution is deliberately dumb -- pattern match, first hit wins, no fuzzy
scoring. A near-miss that silently resolves to the wrong family is exactly the
failure this project exists to prevent, so an unmatched part returns None and
becomes a question.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from knowledge.family import HwFamily, from_json, to_json

#: Shipped records live beside the code so a fresh checkout knows what it knows.
DEFAULT_ROOT = Path(__file__).parent / "families"


class KnowledgeBase:
    """A directory of family records."""

    def __init__(self, root: Path | str | None = None) -> None:
        env = os.environ.get("FW_KNOWLEDGE_BASE")
        self.root = Path(root or env or DEFAULT_ROOT)

    # --- reading ------------------------------------------------------------

    def families(self) -> list[HwFamily]:
        if not self.root.is_dir():
            return []
        out = []
        for path in sorted(self.root.glob("*.json")):
            try:
                out.append(from_json(json.loads(path.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, KeyError) as exc:
                # A corrupt record must not take the whole base down, but it
                # must not pass unnoticed either.
                raise KnowledgeBaseError(
                    f"{path.name} is not a usable family record: {exc}. "
                    f"Fix or remove it; records are plain JSON."
                ) from exc
        return out

    def get(self, family_id: str) -> HwFamily | None:
        path = self._path_for(family_id)
        if not path.is_file():
            return None
        return from_json(json.loads(path.read_text(encoding="utf-8")))

    def resolve(self, mcu: str) -> HwFamily | None:
        """The family whose patterns match this part number, or None.

        None is a real answer: it means nobody has taught this base about the
        part, which is a question to ask rather than a gap to fill in.
        """
        for family in self.families():
            if family.matches(mcu):
                return family
        return None

    # --- writing ------------------------------------------------------------

    def put(self, family: HwFamily) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path_for(family.family_id)
        payload = json.dumps(to_json(family), indent=2, ensure_ascii=False, sort_keys=True)

        handle, tmp = tempfile.mkstemp(dir=str(self.root), suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return path

    def _path_for(self, family_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in family_id.lower())
        return self.root / f"{safe}.json"


class KnowledgeBaseError(RuntimeError):
    """A record on disk cannot be read as a family."""
