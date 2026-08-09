"""HTTP API: a description goes in, a board port comes out as a zip.

The shape of this API is the product's argument, so it is worth stating.

A firmware generator that takes a description and returns firmware is lying by
omission, because the facts it needs are not in the description. They are
properties of a physical board: which pin, which way round the connector, what
the ADC is referenced against. So the flow here is deliberately not
request/response. It is:

    describe  ->  questions  ->  answers  ->  questions  ->  ...  ->  zip

and the loop does not end while anything *blocking* is unanswered. A blocking
question is one whose wrong answer fails silently -- see
`agents/uncertainty.py`, which decides that in ordinary code rather than
leaving it to a model's judgement.

Two things this API will never do:

* fill a blocking answer with a default, however reasonable, and
* return a zip that claims more than the artifacts support. Everything the
  generator could not establish rides along in the download, in a file the
  recipient will actually open.

The free-text step needs a model; everything after it does not. With no API
key the interview still works -- the questions are enumerated deterministically
-- so the useful part of this product runs offline.
"""

from __future__ import annotations

import io
import os
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agents.normalizer import (
    NormalizationError,
    assumed_defaults,
    blocking_questions,
    required_questions,
)
from codegen.zephyr.checks import contention, soc_facts, unsupported_soc
from agents.schemas import HardwareDraft, PinAssignment, SensorDraft
from codegen.zephyr.binding_fetch import BindingFetcher
from codegen.zephyr.board_port import BoardPortError, SocProfile, ZephyrBoardPort
from core.hardware_model import MCU, InterfaceType, PCBAnalysis, Sensor
from services.zephyr_build import BuildUnavailable, ZephyrBuilder, flashing_instructions
from webapp import store

STATIC = Path(__file__).parent / "static"

app = FastAPI(
    title="fw-automation-agent",
    description="Turn a board description into a Zephyr board port, asking for "
                "everything that cannot be derived.",
    version="0.2.0",
)


# --- Session state ------------------------------------------------------------


@dataclass
class Session:
    """One board, from first description to downloadable port.

    Held in memory on purpose: this is a single-tenant tool today, and a
    database would imply a durability guarantee nothing here provides. Swapping
    this dict for storage is the obvious first change for real deployment, and
    it is isolated to this class so that stays true.
    """

    id: str
    draft: HardwareDraft
    answers: dict[str, str] = field(default_factory=dict)
    board_name: str = "Custom Board"
    soc: SocProfile | None = None
    created: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    generated: dict[str, str] | None = None
    binaries: dict[str, bytes] = field(default_factory=dict)
    """Flashable images, once compiled. Cleared whenever an answer changes:
    an image built from superseded answers is the most dangerous artifact this
    system could hand anybody."""
    build_log: str = ""
    build_ok: bool | None = None


SESSIONS: dict[str, Session] = {}


# --- Wire types ---------------------------------------------------------------


class SensorIn(BaseModel):
    name: str = Field(description="Part number as printed, e.g. DHT22")
    type: str = Field(default="", description="What it measures, e.g. temperature")
    interface: str = Field(description="I2C, SPI, UART, GPIO, ADC or 1-Wire")
    address: str | None = Field(default=None, description="I2C address, e.g. 0x76")
    pins: dict[str, str] = Field(default_factory=dict, description="role -> pin")


class StartRequest(BaseModel):
    board_name: str = Field(default="Custom Board")
    mcu: str = Field(description="Exact part number, e.g. nrf52840")
    soc_dtsi: str = Field(
        default="",
        description="Zephyr SoC include, e.g. nordic/nrf52840_qiaa.dtsi",
    )
    vendor: str = Field(default="custom")
    arch: str = Field(default="arm")
    kconfig_soc: str = Field(
        default="",
        description="Kconfig symbol the board selects -- the SoC *variant*, "
                    "e.g. SOC_NRF52840_QIAA, not the die",
    )
    console_tx: str = Field(default="", description="Console UART TX pad, e.g. P0.6")
    console_rx: str = Field(default="", description="Console UART RX pad, e.g. P0.8")
    sensors: list[SensorIn] = Field(default_factory=list)
    description: str = Field(default="", description="Free text, if a model is available")


class QuestionOut(BaseModel):
    field: str
    question: str
    why: str
    options: list[str]
    default: str | None
    blocking: bool

    @property
    def answerable_by_default(self) -> bool:
        return self.default is not None


class StatusOut(BaseModel):
    session: str
    board_name: str
    ready: bool
    blocking: list[QuestionOut]
    advisory: list[QuestionOut]
    assumptions: list[str]
    refusals: list[str]
    conflicts: list[str]


class AnswerRequest(BaseModel):
    answers: dict[str, str]


# --- Helpers ------------------------------------------------------------------


def _session(session_id: str) -> Session:
    session = SESSIONS.get(session_id)
    if session is None:
        session = _restore(session_id)
    if session is None:
        raise HTTPException(404, f"no session '{session_id}'")
    return session


def _persist(session: Session) -> None:
    store.save_session(session.id, {
        "board_name": session.board_name,
        "mcu": session.draft.mcu_name,
        "soc": session.soc.dtsi_include if session.soc else "",
        "vendor": session.soc.vendor if session.soc else "",
        "arch": session.soc.arch if session.soc else "",
        "kconfig_soc": session.soc.kconfig_soc if session.soc else "",
        "console_tx": session.soc.console_tx if session.soc else "",
        "console_rx": session.soc.console_rx if session.soc else "",
        "sensors": [
            {"name": s.name, "type": s.type, "interface": s.interface,
             "address": s.address, "pins": s.pin_map()}
            for s in session.draft.sensors
        ],
        "answers": session.answers,
        "generated_files": sorted(session.generated) if session.generated else [],
    })


def _restore(session_id: str) -> Session | None:
    """Rebuild a session from disk so a closed tab does not lose an interview.

    The generated files are deliberately not restored: they were produced from
    a particular set of answers, and handing back a zip without re-deriving it
    risks shipping a port built from answers that have since changed.
    """
    body = store.load_session(session_id)
    if body is None:
        return None

    draft = HardwareDraft(
        mcu_name=body.get("mcu"), mcu_family=body.get("arch", "").upper() or None,
        board_name=body.get("board_name"),
        sensors=[
            SensorDraft(
                name=s["name"], type=s.get("type") or "unspecified",
                interface=s["interface"], address=s.get("address"),
                pins=s.get("pins") or None,
                bus="I2C1" if str(s["interface"]).upper() == "I2C" else None,
            )
            for s in body.get("sensors", [])
        ],
    )
    session = Session(
        id=session_id, draft=draft, answers=dict(body.get("answers") or {}),
        board_name=body.get("board_name") or "Custom Board",
        soc=SocProfile(
            name=(body.get("mcu") or "").lower(), arch=body.get("arch") or "arm",
            dtsi_include=body.get("soc") or "", vendor=body.get("vendor") or "custom",
            kconfig_soc=body.get("kconfig_soc") or "",
            console_tx=body.get("console_tx") or "",
            console_rx=body.get("console_rx") or "",
        ),
    )
    SESSIONS[session_id] = session
    return session


def _usart_count(session: Session) -> int:
    """How many UARTs the SoC really has, so the interview asks the right thing.

    Falls back to 1 only when nothing can be read, which makes the interview
    ask *more* rather than assume the part is roomy.
    """
    if not session.soc or not session.soc.dtsi_include:
        return 1
    return soc_facts(session.soc.dtsi_include).count("uart") or 1


def _questions(session: Session) -> tuple[list[QuestionOut], list[QuestionOut]]:
    from agents.uncertainty import scan_draft

    count = _usart_count(session)
    scanned = scan_draft(session.draft, session.answers, usart_count=count)
    blocking_fields = {u.field for u in scanned if u.blocking}
    out = [
        QuestionOut(
            field=u.field, question=u.question,
            why=f"{u.why} If it is wrong: {u.failure}",
            options=list(u.options),
            default=None if u.blocking else u.default,
            blocking=u.blocking,
        )
        for u in scanned
    ]
    return [q for q in out if q.blocking], [q for q in out if not q.blocking]


def _status(session: Session) -> StatusOut:
    blocking, advisory = _questions(session)
    return StatusOut(
        session=session.id,
        board_name=session.board_name,
        ready=not blocking,
        blocking=blocking,
        advisory=advisory,
        assumptions=assumed_defaults(session.draft, session.answers),
        refusals=unsupported_soc(session.soc.dtsi_include if session.soc else ""),
        conflicts=contention(
            session.draft.sensors, session.soc.dtsi_include if session.soc else ""
        ),
    )


def _effective_answers(session: Session) -> dict[str, str]:
    """What the build will actually use: the answers, over the stated defaults.

    An advisory question carries a default precisely so it can go unanswered,
    and the generator has to see that default rather than treat the field as
    missing. The values are listed in PROVENANCE.md either way, so a default
    that got used is never invisible.
    """
    from agents.uncertainty import scan_draft

    defaults = {
        u.field: u.default
        for u in scan_draft(session.draft, {}, usart_count=_usart_count(session))
        if not u.blocking and u.default is not None
    }
    return {**defaults, **session.answers}


def _analysis(session: Session) -> PCBAnalysis:
    """Build the validated brief from the draft and the answers."""
    draft = session.draft
    answers = _effective_answers(session)

    clock_hz = int(answers.get("f_cpu_hz") or draft.f_cpu_hz or 0)
    voltage = float(answers.get("supply_voltage") or draft.supply_voltage or 0)
    if clock_hz <= 0:
        # Blocking, so it cannot be defaulted; reaching here means the
        # readiness check and this disagree, which is worth failing loudly.
        raise NormalizationError("the clock frequency is still unanswered")
    if voltage <= 0:
        raise NormalizationError("no supply voltage was given and none was defaulted")

    sensors: list[Sensor] = []
    for index, drafted in enumerate(draft.sensors):
        pins = drafted.pin_map()
        answered = answers.get(f"sensors[{index}].pins")
        if answered and not pins:
            parts = [p.strip() for p in answered.replace(",", " ").split() if p.strip()]
            pins = {"pin": parts[0]} if len(parts) == 1 else (
                {"trigger": parts[0], "echo": parts[1]} if len(parts) >= 2 else {}
            )
        sensors.append(Sensor(
            name=drafted.name,
            type=drafted.type or "unspecified",
            interface=InterfaceType(drafted.interface.upper().replace("1-WIRE", "1-Wire")),
            bus=drafted.bus or ("I2C1" if drafted.interface.upper() == "I2C" else None),
            address=answers.get(f"sensors[{index}].address") or drafted.address,
            pins=pins or None,
            required=drafted.required,
        ))

    # Memory and pin count do not affect the board port, so they are not asked.
    # They are placeholders here and nothing downstream reads them.
    return PCBAnalysis(
        mcu=MCU(
            name=draft.mcu_name or "unknown", family=draft.mcu_family or "ARM",
            flash_kb=1024, ram_kb=256, clock_mhz=clock_hz / 1e6,
            gpio_pins=48, voltage=voltage,
        ),
        sensors=sensors,
        board=draft.board_name,
    )


def _board_id(session: Session) -> str:
    return "".join(c if c.isalnum() else "_" for c in session.board_name.lower()).strip("_")


def _last_build(session: Session):
    from services.zephyr_build import BuildResult

    return BuildResult(ok=bool(session.build_ok), log=session.build_log,
                       artifacts=session.binaries, board=_board_id(session))


def _provenance(session: Session, files: dict[str, str]) -> str:
    """The file the recipient should read before trusting any of this."""
    blocking, _ = _questions(session)
    answered = "\n".join(f"  {k} = {v}" for k, v in sorted(session.answers.items()))
    effective = _effective_answers(session)
    defaulted = "\n".join(
        f"  {k} = {v}" for k, v in sorted(effective.items()) if k not in session.answers
    )
    return f"""# What this port asserts, and on whose authority

Generated {datetime.now(timezone.utc).isoformat(timespec="seconds")} by
fw-automation-agent for "{session.board_name}".

## Derived from a versioned artifact

Every `compatible` in the devicetree was read out of the binding that declares
it, in a pinned Zephyr. A compatible was never chosen because it resembled a
part number: a driver bound to the wrong device initialises cleanly and reports
numbers that are wrong.

Each node's required properties were read from the same binding, so a node is
never emitted missing something its driver needs.

## Answered by a human, and unverifiable by anything here

{answered or "  (nothing)"}

These are the values to re-check against the board. Not one of them is
discoverable, and every one fails silently when wrong -- an inverted button
reads as permanently held, a wrong pin drives nothing and reports no fault, a
clock off by a factor makes every timing wrong by that same factor.

## Used without being told to

{defaulted or "  (nothing)"}

Each of these has a default because getting it wrong fails *visibly* -- a wrong
baud rate is garbage on a terminal, not a plausible wrong number. They are
listed anyway, because a default that nobody sees is indistinguishable from a
fact.

## Still unanswered

{chr(10).join(f"  {q.field}: {q.question}" for q in blocking) or "  (nothing blocking)"}

## Not established

This port has not been built. A port of the same shape has -- an nRF52840 with
a DHT22 and a button, linking to 34288 B of flash against Zephyr v4.4.2 -- but
that says nothing about this one, and building is not running. Nothing here has
been on hardware. Treat the first `west build` as the real check and the first
bring-up as the only proof.

## Files

{chr(10).join("  " + name for name in sorted(files))}
"""


# --- Endpoints ----------------------------------------------------------------


@app.post("/api/sessions", response_model=StatusOut)
def start(request: StartRequest) -> StatusOut:
    """Open a session. Returns what still has to be answered."""
    if not request.mcu.strip():
        raise HTTPException(422, "a microcontroller part number is required")

    draft = HardwareDraft(
        mcu_name=request.mcu.strip(),
        mcu_family=request.arch.upper(),
        board_name=request.board_name.strip() or None,
        sensors=[
            SensorDraft(
                name=s.name, type=s.type or "unspecified", interface=s.interface,
                address=s.address,
                pins=[PinAssignment(role=r, pin=v) for r, v in (s.pins or {}).items()],
                bus="I2C1" if s.interface.upper() == "I2C" else None,
            )
            for s in request.sensors
        ],
    )

    session = Session(
        id=uuid.uuid4().hex[:12],
        draft=draft,
        board_name=request.board_name.strip() or "Custom Board",
        soc=SocProfile(
            name=request.mcu.strip().lower(),
            arch=request.arch.lower(),
            dtsi_include=request.soc_dtsi.strip(),
            vendor=request.vendor.strip().lower() or "custom",
            kconfig_soc=request.kconfig_soc.strip(),
            console_tx=request.console_tx.strip(),
            console_rx=request.console_rx.strip(),
        ),
    )
    SESSIONS[session.id] = session
    status = _status(session)
    _persist(session)
    store.record(
        "started", session.id,
        board_name=session.board_name, mcu=request.mcu, soc=request.soc_dtsi,
        parts=[s.name for s in request.sensors],
        asked=[q.field for q in status.blocking + status.advisory],
        refusals=status.refusals, conflicts=status.conflicts,
    )
    return status


@app.get("/api/sessions/{session_id}", response_model=StatusOut)
def status(session_id: str) -> StatusOut:
    return _status(_session(session_id))


@app.post("/api/sessions/{session_id}/answers", response_model=StatusOut)
def answer(session_id: str, request: AnswerRequest) -> StatusOut:
    session = _session(session_id)
    # Captured before applying, so the corpus can tell an accepted default from
    # an overridden one -- which is the only learnable signal in this data.
    defaults = {
        q.field: q.default
        for q in _questions(session)[1] if q.default is not None
    }
    accepted = {k: str(v).strip() for k, v in request.answers.items() if str(v).strip()}
    session.answers.update(accepted)
    session.generated = None
    session.binaries = {}
    session.build_ok = None

    status = _status(session)
    _persist(session)
    store.record(
        "answer", session.id, answers=accepted, defaults=defaults,
        remaining=[q.field for q in status.blocking],
    )
    return status


@app.post("/api/sessions/{session_id}/generate")
def generate(session_id: str) -> dict:
    """Generate the port, or say exactly what is stopping it."""
    session = _session(session_id)
    blocking, _ = _questions(session)
    if blocking:
        raise HTTPException(409, {
            "error": "questions with no safe default are still unanswered",
            "fields": [q.field for q in blocking],
        })

    dtsi = session.soc.dtsi_include if session.soc else ""
    refusals = unsupported_soc(dtsi)
    if refusals:
        raise HTTPException(422, {"error": "unsupported", "detail": refusals})

    conflicts = contention(session.draft.sensors, dtsi)
    if conflicts:
        raise HTTPException(422, {"error": "the design does not fit the part",
                                  "detail": conflicts})

    if not session.soc or not session.soc.dtsi_include:
        raise HTTPException(422, {
            "error": "no SoC devicetree include was given",
            "detail": ["Zephyr needs the SoC .dtsi for this part, e.g. "
                       "'nordic/nrf52840_qiaa.dtsi'. It is not guessed: the wrong "
                       "one produces a devicetree describing different silicon."],
        })

    try:
        port = ZephyrBoardPort(fetcher=BindingFetcher())
        files = port.generate(_analysis(session), session.soc, session.board_name)
    except (BoardPortError, NormalizationError) as exc:
        raise HTTPException(422, {"error": "cannot generate", "detail": [str(exc)]}) from exc

    files["PROVENANCE.md"] = _provenance(session, files)
    session.generated = files
    _persist(session)
    store.record(
        "generated", session.id, board_name=session.board_name,
        soc=dtsi, parts=[s.name for s in session.draft.sensors],
        files=sorted(files), answers=dict(session.answers),
        # Whether it builds is not known here: generating is not compiling.
        # Recorded as unknown so a later `west build` can fill it in rather
        # than the corpus quietly implying success.
        build="unknown",
    )
    return {"session": session.id, "files": sorted(files), "count": len(files)}


@app.post("/api/sessions/{session_id}/build")
def build(session_id: str) -> dict:
    """Compile the port into a flashable image, or say what is missing.

    This is the only step here that produces a fact rather than a claim: the
    compiler either accepted it or it did not. That makes it the supervision
    signal the corpus exists to collect.
    """
    session = _session(session_id)
    if session.generated is None:
        raise HTTPException(409, "generate the port before building it")

    builder = ZephyrBuilder()
    board = _board_id(session)

    try:
        result = builder.build(session.generated, board)
    except BuildUnavailable as exc:
        # Not a 500: the port is fine, this host just cannot compile. Told
        # plainly so the user builds locally rather than assuming a defect.
        raise HTTPException(503, {
            "error": "this instance cannot compile firmware",
            "detail": str(exc).splitlines(),
        }) from exc

    session.build_ok = result.ok
    session.build_log = result.log
    session.binaries = dict(result.artifacts) if result.ok else {}

    store.record(
        "built", session.id, board=board, build="ok" if result.ok else "failed",
        soc=session.soc.dtsi_include if session.soc else "",
        parts=[s.name for s in session.draft.sensors],
        flash_used=result.flash_used, ram_used=result.ram_used,
        # The tail is what a failure is diagnosed from; the whole log would
        # bloat the corpus without adding to it.
        error=("" if result.ok else result.log[-2000:]),
    )

    if not result.ok:
        raise HTTPException(422, {
            "error": "the port did not compile",
            "detail": result.log.strip().splitlines()[-40:],
        })

    return {
        "session": session.id,
        "board": board,
        "summary": result.summary,
        "artifacts": sorted(result.artifacts),
        "flash_used": result.flash_used,
        "flash_total": result.flash_total,
        "ram_used": result.ram_used,
        "ram_total": result.ram_total,
    }


@app.get("/api/build-capability")
def build_capability() -> dict:
    """Whether this host can compile, and exactly what it lacks if not."""
    builder = ZephyrBuilder()
    gaps = builder.missing()
    return {"can_build": not gaps, "missing": gaps}


@app.get("/api/sessions/{session_id}/download")
def download(session_id: str) -> StreamingResponse:
    session = _session(session_id)
    if session.generated is None:
        raise HTTPException(409, "nothing generated yet for this session")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(session.generated.items()):
            archive.writestr(name, content)

        # The images go at the top of the archive, because they are what the
        # person opening it is after.
        for name, blob in sorted(session.binaries.items()):
            archive.writestr(f"firmware/{name}", blob)

        if session.binaries:
            archive.writestr(
                "firmware/FLASHING.md",
                flashing_instructions(_board_id(session), _last_build(session)),
            )
        if session.build_log:
            archive.writestr("firmware/build.log", session.build_log)
    buffer.seek(0)

    stem = "".join(c if c.isalnum() else "_" for c in session.board_name).strip("_")
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{stem or "board"}_port.zip"'},
    )


class DescribeRequest(BaseModel):
    text: str = Field(description="Free prose about the board")


@app.post("/api/describe")
def describe(request: DescribeRequest) -> dict:
    """Read prose into a draft, using a model.

    This is the only endpoint that needs a model, and it is doing the one job a
    model is reliably good at here: turning messy prose into structure. Nothing
    it returns is trusted -- the draft goes through the same deterministic
    uncertainty scan as a hand-filled form, so a hallucinated sensor produces
    questions rather than firmware.

    Without a credential this returns 503 rather than degrading quietly. The
    form path needs no model at all and is the supported way to work offline.
    """
    if not request.text.strip():
        raise HTTPException(422, "nothing to read")

    try:
        from agents.extractor import ExtractionError, HardwareExtractor
    except ImportError as exc:
        raise HTTPException(503, {
            "error": "the interview agent is not installed",
            "detail": ['pip install "fw-automation-agent[agent]"'],
        }) from exc

    try:
        extractor = HardwareExtractor()
        result = extractor.extract(request.text)
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim, never swallowed
        raise HTTPException(503, {
            "error": "could not read that with a model",
            "detail": [
                str(exc),
                "The form below needs no model and produces the same questions.",
            ],
        }) from exc

    hardware = result.hardware
    store.record("described", "-", parts=[s.name for s in hardware.sensors],
                 mcu=hardware.mcu_name or "", chars=len(request.text))

    return {
        "mcu": hardware.mcu_name or "",
        "board_name": hardware.board_name or "",
        "sensors": [
            {"name": s.name, "type": s.type, "interface": s.interface,
             "address": s.address, "pins": s.pin_map(),
             "confidence": s.confidence.value}
            for s in hardware.sensors
        ],
        "assumptions": result.assumptions,
        "unsupported": result.unsupported,
        "note": (
            "Read from your text by a model. Nothing here is trusted -- it "
            "pre-fills the form, and every value still goes through the same "
            "questions as if you had typed it."
        ),
    }


@app.get("/api/sessions")
def sessions() -> dict:
    """Every board worked on, so a session can be picked up later."""
    return {"sessions": store.list_sessions()}


@app.get("/api/corpus")
def corpus() -> dict:
    """What the collected interactions support saying. Counts, not inferences."""
    return store.corpus_stats()


@app.get("/api/health")
def health() -> dict:
    from codegen.zephyr.bindings import BindingCatalog

    catalog = BindingCatalog()
    return {
        "status": "ok",
        "zephyr_bindings": len(catalog),
        "zephyr_ref": catalog.ref,
        "sessions_in_memory": len(SESSIONS),
        "sessions_stored": len(store.list_sessions(limit=10_000)),
        "storage": store.storage_status(),
        "zephyr_checkout": bool(os.environ.get("ZEPHYR_BASE")),
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


if STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
