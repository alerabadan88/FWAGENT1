"""Confirm a devicetree binding against the binding file itself.

Zephyr's convention is that a binding file is named after the compatible it
declares. A convention is not an artifact: `dts/bindings/sensor/aosong,dht.yaml`
is *expected* to declare ``compatible: "aosong,dht"``, and the only way to know
is to open it. Some files in that tree declare no compatible at all -- they are
includes -- and emitting one of those as a node's compatible produces a build
that fails in a way nobody enjoys reading.

So this reads the YAML, takes the `compatible:` field, and produces
authoritative evidence pinned to the Zephyr tag. The text is supplied rather
than fetched, so the check stays offline and testable; whatever fetches it must
pin the ref.
"""

from __future__ import annotations

import re

from core.evidence import Claim, Evidence, derived
from services.verifier import Unverifiable

_COMPATIBLE = re.compile(r'^compatible:\s*"([^"]+)"', re.MULTILINE)
_PROPERTIES = re.compile(r"^properties:\s*$", re.MULTILINE)
_REQUIRED_PROP = re.compile(
    r"^  (?P<name>[\w-]+):\s*$\n(?:^    .*$\n)*?^    required:\s*true\s*$",
    re.MULTILINE,
)


class ZephyrBindingVerifier:
    """Establishes that a compatible is real, and what it requires."""

    def __init__(self, text: str, source: str, path: str) -> None:
        if "@" not in source:
            raise ValueError(
                f"source {source!r} does not pin a Zephyr ref. Bindings change "
                f"between releases -- a compatible verified against 'main' is "
                f"not verified against anything reproducible."
            )
        self._text = text
        self._source = source
        self._path = path

    def handles(self, claim: Claim) -> bool:
        return claim.predicate in {"dt_compatible", "dt_required_properties"}

    def verify(self, claim: Claim) -> Evidence:
        match = _COMPATIBLE.search(self._text)
        if match is None:
            raise Unverifiable(
                f"{self._path} declares no `compatible:` field. It is an include, "
                f"not a binding, and naming it as a node's compatible will not "
                f"bind any driver."
            )

        declared = match.group(1)
        line = self._text[: match.start()].count("\n") + 1

        if claim.predicate == "dt_compatible":
            if declared != claim.value:
                raise Unverifiable(
                    f"the artifact disagrees: {self._path} declares "
                    f"'{declared}', not '{claim.value}'",
                    contradicted=True,
                )
            return derived(
                source=self._source,
                locator=f"{self._path}:{line}",
                excerpt=match.group(0).strip(),
            )

        required = self.required_properties()
        if set(claim.value) - set(required):
            missing = sorted(set(claim.value) - set(required))
            raise Unverifiable(
                f"the artifact disagrees: {self._path} does not mark "
                f"{missing} as required; it requires {required}",
                contradicted=True,
            )
        return derived(
            source=self._source,
            locator=f"{self._path} (properties)",
            excerpt=f"required: {required}",
        )

    def required_properties(self) -> list[str]:
        """Properties the binding marks `required: true`.

        These are what a generated node must carry. A node missing one does not
        misbehave subtly -- the build fails, which is the good kind of failure,
        but there is no reason to emit it wrong when the binding says so.
        """
        start = _PROPERTIES.search(self._text)
        body = self._text[start.end():] if start else self._text
        return sorted({m.group("name") for m in _REQUIRED_PROP.finditer(body)})
