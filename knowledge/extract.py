"""Read an SDK tree and record what it actually declares.

This is the module that makes a family record worth trusting. Everything it
produces is AUTHORITATIVE evidence, because every symbol carries the header and
line it was read from, and anyone can open that file and look.

Deliberately not an LLM, and deliberately offline
-------------------------------------------------
Two reasons, and the second matters more than it looks:

1. A model asked "what is the GPIO function in this SDK?" will answer. It will
   answer for an SDK it has never seen, in the right shape, with the wrong
   name -- and the result compiles only if you are unlucky enough that a
   similarly-named function exists.

2. Vendor SDKs are routinely NDA-gated. Parsing locally means the SDK never
   leaves the machine. Only the derived catalogue -- names and signatures --
   is ever written down, and even that stays in the local knowledge base.

Textual, not a C parser
-----------------------
Declarations are matched with regular expressions over comment-stripped text.
That is a real limitation and it is bounded in the honest direction: a
declaration this misses is simply absent from the catalogue, so generated code
that calls it is *refused* as "not in this SDK" rather than emitted unchecked.
Missing a symbol costs a question. Inventing one costs a silent defect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from core.evidence import Evidence, derived
from knowledge.family import ApiSymbol, PeripheralBank

#: Block and line comments. Stripped before matching so a commented-out
#: declaration never enters the catalogue.
_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)

#: `ret name(args);` -- one declaration, possibly wrapped over lines.
#: `[^;{}()]*` on the argument list keeps it from spanning a function body.
_DECL = re.compile(
    r"(?P<ret>[A-Za-z_][\w\s\*]*?)\s*"
    r"(?P<name>[A-Za-z_]\w*)\s*"
    r"\(\s*(?P<args>[^;{}()]*)\)\s*;",
    re.MULTILINE,
)

#: Peripheral instance identifiers, as SDKs spell them in enums and defines.
_INSTANCE = re.compile(
    r"\b(?P<kind>UART|USART|SERIAL|I2C|TWI|SPI|GPIO|ADC|TIMER|TIM|PWM|DMA)"
    r"_?(?P<index>\d+)\b",
    re.IGNORECASE,
)

#: Words that make a match a type or a statement, not a function declaration.
_NOT_FUNCTIONS = {
    "if", "for", "while", "switch", "return", "sizeof", "typedef", "struct",
    "union", "enum", "else", "do", "case", "defined", "static_assert",
}

#: Header suffixes worth reading. Sources are skipped: a function defined in a
#: .c but not declared in a header is not part of the SDK's interface.
_HEADER_SUFFIXES = {".h", ".hpp"}

#: Directories that hold examples and tests rather than the SDK interface.
#: Symbols from these would be catalogued as available API and are not.
_SKIP_DIRS = {
    "example", "examples", "sample", "samples", "test", "tests", "demo", "demos",
    "doc", "docs", "build", "out", "output", "tools", "tool", "third_party",
    ".git", ".svn", "__pycache__",
}


@dataclass(frozen=True)
class Extraction:
    """What one pass over an SDK tree found."""

    symbols: tuple[ApiSymbol, ...]
    peripherals: tuple[PeripheralBank, ...]
    headers_read: tuple[str, ...]
    root: str

    #: Symbol name -> where it was read, for provenance on generated calls.
    locators: dict[str, str] = None  # type: ignore[assignment]

    def evidence_for(self, symbol_name: str, sdk_label: str) -> Evidence | None:
        """AUTHORITATIVE evidence that this SDK declares this function."""
        where = (self.locators or {}).get(symbol_name)
        if not where:
            return None
        return derived(source=sdk_label, locator=where)

    def describe(self) -> str:
        banks = ", ".join(
            f"{b.kind} x{b.count}" for b in self.peripherals if b.count
        ) or "none identified"
        return (
            f"{len(self.symbols)} functions from {len(self.headers_read)} headers "
            f"under {self.root}; peripherals: {banks}"
        )


class SdkNotFound(RuntimeError):
    """The path given is not a directory, or holds no headers."""


def extract(root: Path | str, max_files: int = 4000) -> Extraction:
    """Catalogue the public interface of an SDK tree.

    `max_files` is a guard, not a tuning knob: a mistyped path pointing at a
    home directory should stop rather than walk it.
    """
    base = Path(root)
    if not base.is_dir():
        raise SdkNotFound(
            f"{base} is not a directory. Give the path to the unpacked SDK "
            f"root -- the directory that contains its headers."
        )

    symbols: dict[str, ApiSymbol] = {}
    locators: dict[str, str] = {}
    instances: dict[str, set[str]] = {}
    headers: list[str] = []

    for path in _headers(base, max_files):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        relative = path.relative_to(base).as_posix()
        headers.append(relative)
        clean = _COMMENTS.sub(" ", text)

        for name, symbol, line in _declarations(clean, relative):
            # First declaration wins. A later header redeclaring the same name
            # does not change what it is, and keeping the first keeps the
            # locator pointing at the canonical header.
            if name not in symbols:
                symbols[name] = symbol
                locators[name] = f"{relative}:{line}"

        for match in _INSTANCE.finditer(clean):
            kind = _normalise_kind(match.group("kind"))
            instances.setdefault(kind, set()).add(match.group(0).upper())

    if not headers:
        raise SdkNotFound(
            f"No .h files under {base}. Either this is not an SDK root, or the "
            f"headers are in a subdirectory -- point at that instead."
        )

    peripherals = tuple(
        PeripheralBank(kind=kind, instances=tuple(sorted(found)))
        for kind, found in sorted(instances.items())
    )
    return Extraction(
        symbols=tuple(symbols[name] for name in sorted(symbols)),
        peripherals=peripherals,
        headers_read=tuple(headers),
        root=str(base),
        locators=locators,
    )


def _headers(base: Path, max_files: int):
    seen = 0
    for path in sorted(base.rglob("*")):
        if seen >= max_files:
            return
        if not path.is_file() or path.suffix.lower() not in _HEADER_SUFFIXES:
            continue
        if any(part.lower() in _SKIP_DIRS for part in path.relative_to(base).parts[:-1]):
            continue
        seen += 1
        yield path


def _declarations(clean: str, relative: str):
    """Every function declaration in comment-stripped header text."""
    for match in _DECL.finditer(clean):
        name = match.group("name")
        ret = " ".join(match.group("ret").split())
        if name in _NOT_FUNCTIONS or not ret:
            continue
        # `typedef ret (*name)(args);` and similar are types, not entry points.
        if "typedef" in ret.split():
            continue
        # A return type that is only punctuation means the regex latched onto
        # the tail of an expression rather than a declaration.
        if not any(ch.isalnum() for ch in ret):
            continue
        args = " ".join(match.group("args").split())
        line = clean.count("\n", 0, match.start()) + 1
        yield name, ApiSymbol(
            name=name,
            header=relative,
            signature=f"{ret} {name}({args})",
            returns=ret,
        ), line


def _normalise_kind(raw: str) -> str:
    lowered = raw.lower()
    if lowered in {"usart", "serial"}:
        return "uart"
    if lowered == "twi":
        return "i2c"
    if lowered == "tim":
        return "timer"
    return lowered
