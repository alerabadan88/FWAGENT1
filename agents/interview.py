"""The interview loop: ask until the brief is complete, then hand it over.

The loop is deterministic. It asks the questions `normalizer.required_questions`
raises, plus whatever the model flagged as genuinely ambiguous, and stops when
nothing is outstanding. It never fills a required field on the user's behalf
without saying so.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agents.extractor import HardwareExtractor
from agents.normalizer import NormalizationError, check_timing, normalize, required_questions
from agents.schemas import ExtractionResult, FirmwareSpec, OpenQuestion
from core.hardware_model import PCBAnalysis


@dataclass
class InterviewState:
    """Everything gathered so far. Serialisable, so a session can be resumed."""

    extraction: ExtractionResult
    answers: dict[str, str] = field(default_factory=dict)

    def pending(self) -> list[OpenQuestion]:
        """Required questions first — a user who quits early has answered what matters."""
        required = required_questions(self.extraction.hardware, self.answers)
        seen = {q.field for q in required}
        extra = [
            q for q in self.extraction.questions
            if q.field not in seen and q.field not in self.answers
        ]
        return required + extra

    def answer(self, field_name: str, value: str) -> None:
        self.answers[field_name] = value

    @property
    def complete(self) -> bool:
        return not required_questions(self.extraction.hardware, self.answers)


@dataclass
class InterviewOutcome:
    analysis: PCBAnalysis
    spec: FirmwareSpec
    conflicts: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.conflicts


class Interview:
    """Drives one hardware interview from a free-text description."""

    def __init__(self, extractor: HardwareExtractor | None = None) -> None:
        self._extractor = extractor or HardwareExtractor()

    def start(self, description: str) -> InterviewState:
        return InterviewState(extraction=self._extractor.extract(description))

    @staticmethod
    def finish(state: InterviewState) -> InterviewOutcome:
        """Normalize the gathered answers into a brief, or raise."""
        if not state.complete:
            missing = ", ".join(q.field for q in state.pending())
            raise NormalizationError(f"interview is not finished; missing: {missing}")

        analysis, spec = normalize(state.extraction, state.answers)

        return InterviewOutcome(
            analysis=analysis,
            spec=spec,
            conflicts=check_timing(analysis, spec),
            assumptions=list(state.extraction.assumptions),
            unsupported=list(state.extraction.unsupported),
        )
