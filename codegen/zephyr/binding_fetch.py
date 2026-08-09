"""Fetch a binding YAML at a pinned ref, and cache it.

Separated from the verifier on purpose: the verifier takes text and produces
evidence, which keeps it offline and testable. This is the part that reaches
the network, and it is the only part that does.

Two invariants:

* The ref is always pinned. A binding fetched from ``main`` supports nothing
  reproducible -- Zephyr's bindings change between releases, and a compatible
  that exists today may be renamed tomorrow.
* A local Zephyr checkout wins over the network. If the workspace is on disk,
  that is the artifact the build will actually use, and reading anything else
  would verify a different file from the one that matters.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from pathlib import Path

REPO = "zephyrproject-rtos/zephyr"
DEFAULT_REF = "v4.4.2"
CACHE = Path(__file__).parent / "data" / "bindings_cache"


class BindingUnavailable(Exception):
    """The binding could not be obtained, with the reason."""


class BindingFetcher:
    """Gets the text of a binding file, preferring a checkout over the network."""

    def __init__(
        self,
        ref: str = DEFAULT_REF,
        zephyr_base: Path | None = None,
        cache: Path | None = None,
        allow_network: bool = True,
    ) -> None:
        if ref in {"main", "master", "HEAD"}:
            raise ValueError(
                f"refusing to pin bindings to '{ref}'. Zephyr's bindings change "
                f"between releases, so a compatible verified against a moving "
                f"branch is not verified against anything reproducible."
            )
        self.ref = ref
        self._cache = cache or CACHE
        self._allow_network = allow_network
        env = os.environ.get("ZEPHYR_BASE")
        self._base = Path(zephyr_base) if zephyr_base else (Path(env) if env else None)

    @property
    def source(self) -> str:
        """The pinned identity to record on any evidence derived from this."""
        if self._base is not None:
            return f"{self._base.name}@{self.ref} (local checkout)"
        return f"{REPO}@{self.ref}"

    def fetch(self, binding_path: str) -> str:
        """The binding's text. Raises rather than returning a guess."""
        if self._base is not None:
            local = self._base / binding_path
            if local.is_file():
                return local.read_text(encoding="utf-8", errors="replace")

        cached = self._cache / self.ref / binding_path.replace("/", "__")
        if cached.is_file():
            return cached.read_text(encoding="utf-8", errors="replace")

        if not self._allow_network:
            raise BindingUnavailable(
                f"{binding_path} is not in the local checkout or the cache, and "
                f"network access is disabled"
            )

        url = f"https://raw.githubusercontent.com/{REPO}/{self.ref}/{binding_path}"
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                text = response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise BindingUnavailable(f"could not fetch {binding_path}: {exc}") from exc

        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(text, encoding="utf-8")
        return text
